"""Tests for tools/backup_learn.py in a sandbox folder (the live input method
and its data are not touched): copying, the manifest, restoring, which old
backups are kept, read-only folders, every server stopped, and what a failed
backup leaves behind. (The servers are stand-ins: nothing real is stopped.)

    python tests/test_backup.py
"""

from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import backup_learn as bl  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
TOOL = str(ROOT / "tools" / "backup_learn.py")


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def run(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, TOOL, "--root", str(root), "--no-service", *args],
                          capture_output=True, text=True, encoding="utf-8", timeout=300)


# Holds a file open with no sharing, as a running server holds its database.
HOLD = r'''
import ctypes, sys, time
k = ctypes.WinDLL("kernel32")
k.CreateFileW.restype = ctypes.c_void_p
h = k.CreateFileW(sys.argv[1], 0x80000000, 0, None, 3, 0x80, None)
print("held" if h and h != ctypes.c_void_p(-1).value else "failed", flush=True)
time.sleep(120)
'''


class Held:
    def __init__(self, path: Path) -> None:
        self.proc = subprocess.Popen([sys.executable, "-c", HOLD, str(path)], stdout=subprocess.PIPE, text=True)
        self.ok = self.proc.stdout.readline().strip() == "held"
        self.pid = self.proc.pid

    def release(self) -> None:
        self.proc.kill()
        self.proc.wait()


def make_live(root: Path) -> None:
    for name in ("asus_learn", "terra_pinyin"):
        db = root / "rime" / f"{name}.userdb"
        db.mkdir(parents=True)
        (db / "000005.ldb").write_bytes(name.encode() * 50)
        (db / "000003.log").write_bytes(b"log " + name.encode())
        (db / "MANIFEST-000004").write_bytes(b"manifest")
        (db / "CURRENT").write_text("MANIFEST-000004\n")
    (root / "rime" / "emails.txt").write_text("a@b.co\n", encoding="utf-8")


def read_only(top: Path, on: bool = True) -> None:
    """Folders under D:\\Document carry the read-only attribute (the Drive client sets it)."""
    for d in [top] + [x for x in top.rglob("*") if x.is_dir()]:
        subprocess.run(["attrib", "+R" if on else "-R", str(d)], check=True, capture_output=True)


def tree_state(root: Path) -> dict[str, bytes]:
    return {f.relative_to(root).as_posix(): f.read_bytes() for f in (root / "rime").rglob("*") if f.is_file()}


def readonly_tests(base: Path) -> None:
    """prune() and restore() on read-only folders; restore() without checksums."""
    root = base / "ro"
    make_live(root)
    dest = root / "backups" / "learn"
    p = run(root, "--no-verify")
    name = bl.backups(dest)[-1][0]
    read_only(dest)
    read_only(root / "rime")
    before = tree_state(root)
    p = run(root, "--restore", "latest", "--yes", "--no-verify", "--fail-after", "1")
    left = [x.name for x in (root / "rime").iterdir() if ".restore-" in x.name]
    check("read-only backup: a failed restore leaves no staged copies behind",
          p.returncode == 1 and tree_state(root) == before and not left
          and not [x for x in dest.iterdir() if x.name.startswith("_before-restore-")], f"{left} {p.stdout}")

    # A manifest without checksums (older format, or edited by hand) and a cut table.
    manifest_path = dest / name / "backup.json"
    m = json.loads(manifest_path.read_text(encoding="utf-8"))
    m.pop("sha256")
    manifest_path.write_text(json.dumps(m), encoding="utf-8")
    table = dest / name / "rime" / "asus_learn.userdb" / "000005.ldb"
    table.write_bytes(table.read_bytes()[:10])
    p = run(root, "--restore", "latest", "--yes", "--no-verify")
    check("a backup whose manifest has no sha256 is not restored",
          p.returncode == 1 and tree_state(root) == before and "no checksums" in p.stdout, p.stdout + p.stderr)

    # Old backups to prune, all read-only: two full ones, a shell of empty
    # folders (what earlier prunes left), and one with a file held open.
    src = dest / name
    olds = ["20250101-100000", "20250101-110000", "20250101-120000"]
    for old in olds:
        subprocess.run(["robocopy", str(src), str(dest / old), "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP"],
                       capture_output=True)
    shell = dest / "20250102-090000" / "rime" / "asus_learn.userdb"
    shell.mkdir(parents=True)
    read_only(dest)
    held = Held(dest / "20250101-110000" / "rime" / "emails.txt")
    try:
        removed, stuck = bl.prune(dest, dt.date.today())
        gone = [n for n in ("20250101-100000", "20250102-090000") if not (dest / n).exists()]
        check("prune() removes read-only folders, empty shells too",
              held.ok and len(gone) == 2 and set(gone) <= set(removed), f"removed={removed} gone={gone}")
        check("prune() counts only folders that are really gone",
              "20250101-110000" in stuck and "20250101-110000" not in removed
              and (dest / "20250101-110000").exists(), f"removed={removed} stuck={stuck}")
    finally:
        held.release()
    read_only(root, False)


def server_tests(base: Path) -> None:
    """Every running server is stopped; after a failure the scheduled run does
    not stop them again the same day; the failure leaves a marker."""
    root = base / "srv"
    make_live(root)
    dest = root / "backups" / "learn"
    calls: list[tuple[str, str]] = []
    # Stand-ins below the server functions: both servers "run" (tasklist and
    # the registry are not asked), stopping and starting is only recorded.
    fake_bin = base / "bin"
    fake_bin.mkdir()
    for name, _ in bl.SERVERS:
        (fake_bin / name).write_bytes(b"")
    running = {"yes": True}
    stuck: set[str] = set()
    saved = {k: getattr(bl, k) for k in ("_pids", "_root", "stop_server", "start_server", "idle_seconds", "copy_db")}
    real_copy = bl.copy_db

    def copy_db(src: Path, dst: Path) -> None:
        calls.append(("copy", src.name))
        real_copy(src, dst)

    def stop_server(exe: Path) -> bool:
        calls.append(("stop", exe.name))
        return exe.name not in stuck

    bl._pids = lambda name: [4242] if running["yes"] else []
    bl._root = lambda key: fake_bin
    bl.stop_server = stop_server
    bl.start_server = lambda exe: calls.append(("start", exe.name)) or True
    bl.idle_seconds = lambda: 1e9
    bl.copy_db = copy_db
    stops = lambda: sum(1 for c in calls if c[0] == "stop")  # noqa: E731
    day1, day2 = dt.datetime(2026, 12, 1, 0, 30), dt.datetime(2026, 12, 2, 0, 30)
    held = Held(root / "rime" / "terra_pinyin.userdb" / "000003.log")
    try:
        rc = bl.scheduled(root, dest, check=False, now=day1)
        first_copy = next((i for i, c in enumerate(calls) if c[0] == "copy"), len(calls))
        last_copy = max((i for i, c in enumerate(calls) if c[0] == "copy"), default=0)
        check("every running server is stopped before the copy and started after it",
              calls[:first_copy] == [("stop", "DaoHuiServer.exe"), ("stop", "WeaselServer.exe")]
              and calls[last_copy + 1:] == [("start", "DaoHuiServer.exe"), ("start", "WeaselServer.exe")],
              f"{calls}")
        m = bl.backups(dest)[-1][1]
        check("a database that cannot be copied names the process holding it",
              rc == 1 and held.ok and any(f"held open by python.exe (pid {held.pid})" in e for e in m["errors"]),
              f"rc={rc} {m.get('errors')}")
        marker = dest / bl.MARKER
        text = marker.read_text(encoding="utf-8") if marker.exists() else ""
        check("a failed backup leaves LAST_BACKUP_FAILED.txt saying what, when and what to do",
              "terra_pinyin.userdb" in text and "2026-12-01 00:30:00" in text and "怎麼辦" in text
              and bl.marker_day(dest) == day1.date(), text[:300])
        p = run(root, "--list")
        check("--list points at the failure marker", bl.MARKER in p.stdout, p.stdout)

        n = stops()
        rc = bl.scheduled(root, dest, check=False, now=day1.replace(hour=2))
        log_text = (dest / "backup.log").read_text(encoding="utf-8")
        check("after a failure the scheduled run does not stop the servers again that day",
              rc == 0 and stops() == n and "skipped: today's backup failed" in log_text, f"{calls[n:]}")

        rc = bl.scheduled(root, dest, check=False, now=day2)
        check("...but it does again the next day", rc == 1 and stops() == n + 2, f"{calls}")

        held.release()
        running["yes"] = False
        n = stops()
        rc = bl.scheduled(root, dest, check=False, now=day2.replace(hour=2))
        check("with no server running it retries the same day, without stopping anything",
              rc == 0 and stops() == n and bl.backups(dest)[-1][1].get("ok"), f"rc={rc} {calls[n:]}")
        check("a good backup removes the failure marker", not marker.exists())

        # restore(): every server stopped and started again; if one will
        # not quit, nothing is changed and the others are started again.
        running["yes"] = True
        del calls[:]
        rc = bl.restore(root, dest, "latest", True, check=False)
        check("restore stops every running server and starts them again",
              rc == 0 and [c for c in calls if c[0] != "copy"] == [
                  ("stop", "DaoHuiServer.exe"), ("stop", "WeaselServer.exe"),
                  ("start", "DaoHuiServer.exe"), ("start", "WeaselServer.exe")], f"rc={rc} {calls}")
        stuck.add("WeaselServer.exe")
        del calls[:]
        before = tree_state(root)
        rc = bl.restore(root, dest, "latest", True, check=False)
        check("restore changes nothing if one server will not quit, and starts the others again",
              rc == 1 and tree_state(root) == before
              and calls == [("stop", "DaoHuiServer.exe"), ("stop", "WeaselServer.exe"), ("start", "DaoHuiServer.exe")],
              f"rc={rc} {calls}")
    finally:
        held.release()
        for k, v in saved.items():
            setattr(bl, k, v)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "rime" / "asus_learn.userdb"
        db.mkdir(parents=True)
        (db / "000005.ldb").write_bytes(b"table" * 100)
        (db / "000007.log").write_bytes(b"log v1")
        (db / "MANIFEST-000004").write_bytes(b"manifest")
        (db / "CURRENT").write_text("MANIFEST-000004\n")
        (db / "LOCK").write_bytes(b"")
        (root / "rime" / "emails.txt").write_text("a@b.co\n", encoding="utf-8")
        (root / "rime" / "asus_zhuyin.custom.yaml").write_text("patch: {}\n", encoding="utf-8")
        (root / "rime" / "asus_zhuyin.schema.yaml").write_text("not personal\n", encoding="utf-8")
        (root / "lexicon").mkdir()
        (root / "lexicon" / "personal_words.txt").write_text("詞\n", encoding="utf-8")
        dest = root / "backups" / "learn"

        p = run(root, "--no-verify")
        items = bl.backups(dest)
        check("a backup is made", p.returncode == 0 and len(items) == 1 and items[0][1].get("ok"),
              p.stdout + p.stderr)
        name = items[0][0] if items else ""
        b = dest / name
        check("the database is copied, without LOCK",
              (b / "rime/asus_learn.userdb/000007.log").read_bytes() == b"log v1"
              and (b / "rime/asus_learn.userdb/MANIFEST-000004").exists()
              and not (b / "rime/asus_learn.userdb/LOCK").exists())
        check("personal files are copied, the schema is not",
              (b / "rime/emails.txt").exists() and (b / "lexicon/personal_words.txt").exists()
              and (b / "rime/asus_zhuyin.custom.yaml").exists()
              and not (b / "rime/asus_zhuyin.schema.yaml").exists())
        check("the run is logged", "ok " + name in (dest / "backup.log").read_text(encoding="utf-8"))
        p = run(root, "--list")
        check("--list shows it", name in p.stdout and "ok" in p.stdout, p.stdout)

        # Damage, then restore. (--no-verify: the database here is a dummy.)
        (db / "000007.log").write_bytes(b"damaged")
        (root / "rime" / "emails.txt").unlink()
        p = run(root, "--restore", "latest", "--no-verify")
        check("--restore without --yes changes nothing",
              p.returncode == 0 and (db / "000007.log").read_bytes() == b"damaged", p.stdout)

        def state() -> dict[str, bytes]:
            return {f.relative_to(root).as_posix(): f.read_bytes()
                    for top in (root / "rime", root / "lexicon") for f in top.rglob("*") if f.is_file()}

        before = state()
        # A failure in the middle of the swap: everything is put back.
        p = run(root, "--restore", "latest", "--yes", "--no-verify", "--fail-after", "2")
        check("a restore that fails half-way leaves everything as it was",
              p.returncode == 1 and state() == before and "put back" in p.stdout, p.stdout + p.stderr)
        check("...and no half-made folders behind",
              not [x for x in dest.iterdir() if x.name.startswith("_before-restore-")]
              and not [x for x in (root / "rime").iterdir() if ".restore-" in x.name],
              f"{[x.name for x in dest.iterdir()]} {[x.name for x in (root / 'rime').iterdir()]}")
        # A backup that was changed after it was made is refused.
        victim = b / "rime/asus_learn.userdb/000005.ldb"
        good_bytes = victim.read_bytes()
        victim.write_bytes(b"tampered")
        p = run(root, "--restore", "latest", "--yes", "--no-verify")
        check("a backup changed after it was made is not restored",
              p.returncode == 1 and state() == before and "changed since" in p.stdout, p.stdout + p.stderr)
        victim.write_bytes(good_bytes)
        moved = b / "lexicon/personal_words.txt"
        moved.rename(moved.with_suffix(".gone"))
        p = run(root, "--restore", "latest", "--yes", "--no-verify")
        check("an incomplete backup is not restored",
              p.returncode == 1 and state() == before and "incomplete" in p.stdout, p.stdout + p.stderr)
        moved.with_suffix(".gone").rename(moved)

        p = run(root, "--restore", "latest", "--yes", "--no-verify")
        check("--restore --yes puts the data back",
              p.returncode == 0 and (db / "000007.log").read_bytes() == b"log v1"
              and (root / "rime" / "emails.txt").read_text(encoding="utf-8") == "a@b.co\n", p.stdout + p.stderr)
        aside = [d for d in dest.iterdir() if d.name.startswith("_before-restore-")]
        check("what was there before the restore is kept aside",
              len(aside) == 1 and (aside[0] / "rime/asus_learn.userdb/000007.log").read_bytes() == b"damaged")
        check("files that are not in the backup are left alone",
              (root / "rime" / "asus_zhuyin.schema.yaml").exists())

        # A backup that failed its check is never restored.
        bad = dest / "20990101-000000"
        bad.mkdir()
        (bad / "backup.json").write_text(json.dumps({"ok": False, "errors": ["x"], "files": {}}))
        p = run(root, "--restore", "latest", "--no-verify")
        check("latest skips a backup that failed its check", name in p.stdout and "20990101" not in p.stdout, p.stdout)
        p = run(root, "--restore", "20990101-000000", "--yes", "--no-verify")
        check("a failed backup cannot be restored by name", p.returncode == 1, p.stdout)

    with tempfile.TemporaryDirectory() as tmp:
        readonly_tests(Path(tmp))
        with contextlib.redirect_stdout(io.StringIO()):  # restore() and log() print
            server_tests(Path(tmp))

    # Which backups are kept: the newest of each of the last 14 days that
    # have one, and the newest of each of the last 12 months.
    ok, bad = {"ok": True}, {"ok": False}
    today = dt.date(2026, 10, 10)
    items = []
    for d in range(40):  # one a day for 40 days, two on the last day
        day = today - dt.timedelta(days=39 - d)
        items.append((f"{day:%Y%m%d}-120000", ok))
    items.append((f"{today:%Y%m%d}-180000", ok))
    items += [("20250115-120000", ok), ("20250120-120000", ok), ("20240101-120000", ok),
              ("20261009-090000", bad), ("20260901-090000", bad)]
    items.sort()
    keep = bl.to_keep(items, today)
    days = sorted({n[:8] for n in keep if n >= "20260927"})
    check("the last 14 days with a backup are kept", len(days) == 14 and "20260927" in days, f"{days}")
    check("only the newest of a day is kept",
          "20261010-180000" in keep and "20261010-120000" not in keep)
    check("older ones: the newest of each month", "20260926-120000" not in keep and "20260831-120000" not in keep
          and "20260930-120000" in keep and "20250120-120000" in keep and "20250115-120000" not in keep, f"{sorted(keep)}")
    check("months beyond the last 12 with a backup are dropped... but not when fewer exist",
          "20240101-120000" in keep, f"{sorted(keep)[:3]}")
    check("a failed backup is kept for a week only",
          "20261009-090000" in keep and "20260901-090000" not in keep)
    check("nothing is kept twice or invented", keep <= {n for n, _ in items})

    # A process started from a background task (as the server is after a
    # backup) inherits low priorities; set_normal() undoes all three.
    probe = r'''
import subprocess, sys
sys.path.insert(0, sys.argv[1])
import procprio as pp
import ctypes as C
k, nt = C.WinDLL("kernel32"), C.WinDLL("ntdll")
k.GetCurrentProcess.restype = C.c_void_p
k.SetPriorityClass.argtypes = [C.c_void_p, C.c_ulong]
nt.NtSetInformationProcess.argtypes = [C.c_void_p, C.c_int, C.c_void_p, C.c_ulong]
h = k.GetCurrentProcess()
k.SetPriorityClass(h, 0x4000)  # what Task Scheduler priority 7 gives
for cls, v in ((33, 1), (39, 2)):
    nt.NtSetInformationProcess(h, cls, C.byref(C.c_ulong(v)), 4)
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
try:
    before = pp.state(child.pid)
    failed = pp.set_normal(child.pid)
    print(before, failed, pp.state(child.pid), sep="|")
finally:
    child.kill()
'''
    p = subprocess.run([sys.executable, "-c", probe, str(ROOT / "tools")], capture_output=True, text=True, timeout=60)
    parts = p.stdout.strip().split("|")
    check("a child of a background task starts with low priorities",
          len(parts) == 3 and "BELOW_NORMAL" in parts[0] and "'Low'" in parts[0], p.stdout + p.stderr)
    check("set_normal() gives it normal CPU, I/O and memory priority",
          len(parts) == 3 and parts[1] == "[]" and parts[2] == "{'cpu': 'NORMAL', 'io': 'Normal', 'page': 5}",
          p.stdout + p.stderr)
    check("the scheduled task runs at a normal (interactive) priority",
          "<Priority>4</Priority>" in bl.TASK_XML)

    width = max(len(n) for n, _, _ in RESULTS)
    failed = 0
    for name, ok_, detail in RESULTS:
        failed += not ok_
        print(f"{'PASS' if ok_ else 'FAIL'}  {name:<{width}}  {'' if ok_ else detail}")
    print(f"{len(RESULTS) - failed}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
