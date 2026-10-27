"""Back up what the input method has learned (local only).

Copies the learned data into backups/learn/<YYYYMMDD-HHMMSS>/:
  rime/*.userdb          asus_learn (corrections, next words, English words,
                         email addresses) and Rime's own user dictionaries
  rime/emails.txt, rime/shortcuts.txt, rime/*.custom.yaml,
  rime/installation.yaml, rime/user.yaml, lexicon/*_words.txt
then checks that the copy can really be opened (a staged librime reads it
back and counts the entries), writes backup.json, and removes old backups:
the newest of each of the last 14 days that have one, and the newest of each
of the last 12 months, are kept. backups/ is never versioned.

    python tools/backup_learn.py                 back up now
    python tools/backup_learn.py --list          what is there
    python tools/backup_learn.py --install       once a day (Task Scheduler,
                                                 current user, no admin rights)
    python tools/backup_learn.py --uninstall
    python tools/backup_learn.py --restore latest          show what would happen
    python tools/backup_learn.py --restore latest --yes    do it

WeaselServer keeps the newest part of each database (LevelDB's log and
MANIFEST) locked against all reading, so a faithful copy needs the server
stopped: it is asked to quit, the files are copied (well under a second) and
it is started again, about 2-3 seconds without the input method in all. The
scheduled run (--scheduled) therefore waits until nobody has touched the
keyboard or mouse for IDLE_SECONDS, tries again every two hours until a
backup of the day exists, and does nothing once it does.
"""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASK_NAME = "RimeLearnBackup"
NAME_RE = re.compile(r"^(\d{8})-(\d{6})$")
KEEP_DAYS, KEEP_MONTHS, KEEP_BAD_DAYS = 14, 12, 7
IDLE_SECONDS = 120      # --scheduled: keyboard and mouse untouched this long
IDLE_WAIT = 25 * 60     # ...and how long to wait for that before giving up
# The input method's server and the registry key whose WeaselRoot is its
# folder: 島輝 (DaoHui) first, then stock 小狼毫 (Weasel). Whichever runs is
# stopped for the copy; both lock the databases the same way.
SERVERS = [("DaoHuiServer.exe", r"SOFTWARE\WOW6432Node\DaoHui"),
           ("WeaselServer.exe", r"SOFTWARE\WOW6432Node\Rime\Weasel")]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Staged librime reads the copied asus_learn.userdb back and counts its
# entries by kind (the first character of the key).
CHECK_LUA = r'''
local function init(env)
  local out = io.open(rime_api.get_user_data_dir() .. "/asus_learn.check.txt", "w")
  if not out then return end
  local ok, db = pcall(UserDb, "asus_learn", "userdb")
  if not (ok and db and db:open()) then
    out:write("error cannot open\n")
    out:close()
    return
  end
  local counts = {}
  local q = db:query("")
  if q then
    for k, _ in q:iter() do
      local kind = k:sub(1, 1)
      if k:sub(2, 2) == "\t" then counts[kind] = (counts[kind] or 0) + 1 end
    end
  end
  q = nil
  collectgarbage()  -- release the LevelDB iterator before closing
  for kind, n in pairs(counts) do out:write(kind .. " " .. n .. "\n") end
  out:write("done\n")
  out:close()
end
return { init = init, func = function() return 2 end }
'''
CHECK_PATCH = ('patch:\n  asus_learn/enabled: false\n'
               '  "engine/processors/@before 0": lua_processor@*asus_backup_check\n')
KINDS = {"c": "corrections", "r": "chosen words", "x": "undone corrections", "w": "own words",
         "n": "next words", "e": "English words", "m": "email addresses", "b": "banned predictions"}


def log(msg: str, dest: Path | None = None) -> None:
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    if sys.stdout:  # None under pythonw (the scheduled task)
        print(msg)
    if dest:
        dest.mkdir(parents=True, exist_ok=True)
        with (dest / "backup.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def sources(root: Path) -> tuple[list[Path], list[Path]]:
    """(database folders, single files) that exist, as paths under root."""
    rime = root / "rime"
    dbs = sorted(p for p in rime.glob("*.userdb") if p.is_dir())
    files = [rime / "emails.txt", rime / "shortcuts.txt", rime / "installation.yaml", rime / "user.yaml",
             root / "lexicon" / "personal_words.txt", root / "lexicon" / "english_words.txt"]
    files += sorted(rime.glob("*.custom.yaml"))
    return dbs, [f for f in files if f.is_file()]


def db_state(db: Path) -> list[tuple[str, int]]:
    """LevelDB's immutable files: these must not change while copying."""
    out = []
    for f in sorted(db.iterdir()):
        if f.suffix == ".ldb" or f.name.startswith("MANIFEST") or f.name == "CURRENT":
            try:
                out.append((f.name, f.stat().st_size))
            except OSError:
                out.append((f.name, -1))
    return out


def copy_db(src: Path, dst: Path) -> None:
    for attempt in range(4):
        before = db_state(src)
        if dst.exists():
            shutil.rmtree(dst)
        rc = subprocess.run(["robocopy", str(src), str(dst), "/E", "/R:2", "/W:1", "/XF", "LOCK",
                             "/NFL", "/NDL", "/NJH", "/NJS", "/NP"],
                            capture_output=True, creationflags=NO_WINDOW).returncode
        if rc >= 8:  # robocopy: < 8 is success
            raise RuntimeError(f"robocopy {src.name} failed with code {rc}")
        if db_state(src) == before:
            return
        time.sleep(0.5 * (attempt + 1))  # LevelDB was compacting: copy again
    raise RuntimeError(f"{src.name} kept changing while it was copied")


def tree_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def hashes(base: Path, rels: list[str]) -> dict[str, str]:
    """sha256 of every file under base/<rel> (a file or a folder), by path relative to base."""
    out: dict[str, str] = {}
    for rel in rels:
        top = base / rel
        for f in ([top] if top.is_file() else sorted(x for x in top.rglob("*") if x.is_file())):
            out[f.relative_to(base).as_posix()] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def verify(backup: Path) -> dict:
    """Open the copied databases with a staged librime (in a subprocess)."""
    try:
        proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--verify-worker", str(backup)],
                              capture_output=True, timeout=180, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        return {"ok": False, "errors": ["verification timed out"]}
    try:
        return json.loads(proc.stdout.decode("utf-8").strip().splitlines()[-1])
    except (ValueError, IndexError):
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-3:]
        return {"ok": False, "errors": [f"verification failed (exit {proc.returncode})"] + tail}


def verify_worker(backup: Path) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from rime_harness import Rime, stage_user_dir
    user = stage_user_dir("backup-verify", "project", False, (),
                          {"asus_zhuyin.custom.yaml": CHECK_PATCH, "lua/asus_backup_check.lua": CHECK_LUA})
    dbs = sorted(p for p in (backup / "rime").glob("*.userdb") if p.is_dir())
    for db in dbs:
        shutil.copytree(db, user / db.name)
    rime = Rime(user, log_name="backup-verify")
    errors: list[str] = []
    if not rime.select_schema("asus_zhuyin"):
        errors.append("the schema did not load")
    rime.type("su3cl3")  # opens Rime's own user dictionary
    if not rime.snapshot().preview:
        errors.append("typing produced nothing")
    rime.clear()
    rime.close()
    counts: dict[str, int] = {}
    check = user / "asus_learn.check.txt"
    lines = check.read_text(encoding="utf-8").split("\n") if check.exists() else []
    if "done" not in lines:
        if any(d.name == "asus_learn.userdb" for d in dbs):
            errors.append("asus_learn.userdb could not be read back")
    for line in lines:
        m = re.match(r"^(\S) (\d+)$", line)
        if m:
            counts[m.group(1)] = int(m.group(2))
    for f in rime.log_dir.glob("*ERROR*"):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            low = line.lower()
            if line.startswith("E") and ("db" in low or "lua" in low):
                errors.append(line[:200])
    sys.stdout.write("\n" + json.dumps({"ok": not errors, "errors": errors[:5], "asus_learn": counts}) + "\n")
    return 0


# -- the input method's server ------------------------------------------------
def _running(name: str) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH", "/FO", "CSV"],
                         capture_output=True, creationflags=NO_WINDOW).stdout
    return name.lower().encode() in out.lower()


def _root(key: str) -> Path | None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
            return Path(winreg.QueryValueEx(k, "WeaselRoot")[0])
    except OSError:
        return None


def weasel_server() -> Path:
    """The running server (島輝 first), else an installed one."""
    for name, key in SERVERS:
        if _running(name) and _root(key):
            return _root(key) / name
    for name, key in SERVERS:
        root = _root(key)
        if root and (root / name).exists():
            return root / name
    raise FileNotFoundError("no DaoHui or Weasel server is installed")


def server_running() -> bool:
    return any(_running(name) for name, _ in SERVERS)


def wait_for(cond, seconds: float, step: float = 0.2) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return bool(cond())


def stop_server(exe: Path) -> bool:
    subprocess.run([str(exe), "/q"], cwd=str(exe.parent), creationflags=NO_WINDOW)
    return wait_for(lambda: not _running(exe.name), 15)


def start_server(exe: Path) -> bool:
    for _ in range(3):  # without the server there is no input method at all
        subprocess.Popen([str(exe)], cwd=str(exe.parent), creationflags=NO_WINDOW)
        if wait_for(lambda: _running(exe.name), 10):
            return True
    return False


def idle_seconds() -> float:
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0


def backups(dest: Path) -> list[tuple[str, dict]]:
    """(name, manifest) of every backup, oldest first."""
    out = []
    if dest.is_dir():
        for d in sorted(dest.iterdir()):
            if d.is_dir() and NAME_RE.match(d.name):
                try:
                    manifest = json.loads((d / "backup.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    manifest = {"ok": False, "errors": ["no backup.json (unfinished)"]}
                out.append((d.name, manifest))
    return out


def to_keep(items: list[tuple[str, dict]], today: dt.date) -> set[str]:
    good = [n for n, m in items if m.get("ok")]
    keep: set[str] = set()
    by_day: dict[str, str] = {}
    by_month: dict[str, str] = {}
    for name in good:  # oldest first: later ones overwrite
        by_day[name[:8]] = name
        by_month[name[:6]] = name
    keep.update(by_day[d] for d in sorted(by_day)[-KEEP_DAYS:])
    keep.update(by_month[m] for m in sorted(by_month)[-KEEP_MONTHS:])
    # A backup that failed its check is kept for a while to be looked at.
    for name, m in items:
        if not m.get("ok"):
            day = dt.datetime.strptime(name[:8], "%Y%m%d").date()
            if (today - day).days <= KEEP_BAD_DAYS:
                keep.add(name)
    return keep


def prune(dest: Path, today: dt.date) -> list[str]:
    items = backups(dest)
    keep = to_keep(items, today)
    removed = []
    for name, _ in items:
        if name not in keep:
            shutil.rmtree(dest / name, ignore_errors=True)
            removed.append(name)
    return removed


def total(manifest: dict) -> int:
    return sum((manifest.get("asus_learn") or {}).values())


def backup(root: Path, dest: Path, check: bool = True, now: dt.datetime | None = None,
           service: bool = True) -> tuple[Path, dict]:
    now = now or dt.datetime.now()
    previous = [m for _, m in backups(dest) if m.get("ok")]
    target = dest / now.strftime("%Y%m%d-%H%M%S")
    target.mkdir(parents=True)
    dbs, files = sources(root)
    manifest: dict = {"time": now.isoformat(timespec="seconds"), "ok": True, "errors": [], "files": {}}
    exe = weasel_server() if service and dbs and server_running() else None
    t0 = time.monotonic()
    try:
        if exe and not stop_server(exe):
            manifest["ok"] = False
            manifest["errors"].append(f"{exe.name} did not quit; its databases are locked")
        for db in dbs:
            rel = db.relative_to(root)
            try:
                copy_db(db, target / rel)
                manifest["files"][rel.as_posix()] = tree_size(target / rel)
            except (RuntimeError, OSError) as e:
                manifest["ok"] = False
                manifest["errors"].append(str(e))
    finally:
        if exe:
            if not start_server(exe):
                manifest["ok"] = False
                manifest["errors"].append(f"{exe.name} did not start again: start it from the Start menu "
                                          "(島輝 / 小狼毫算法服務)")
            manifest["server_down_seconds"] = round(time.monotonic() - t0, 1)
    for f in files:
        rel = f.relative_to(root)
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target / rel)
        manifest["files"][rel.as_posix()] = f.stat().st_size
    if not dbs:
        manifest["ok"] = False
        manifest["errors"].append("no *.userdb found: nothing has been learned yet?")
    if check and manifest["ok"]:
        res = verify(target)
        manifest["verified"] = True
        manifest["asus_learn"] = res.get("asus_learn", {})
        if not res.get("ok"):
            manifest["ok"] = False
            manifest["errors"] += res.get("errors", [])
        # Much less than last time is suspicious (a damaged or emptied
        # database): keep everything older until someone has looked.
        if previous and total(previous[-1]) >= 20 and total(manifest) < total(previous[-1]) / 2:
            manifest["shrunk"] = True
    # For --restore: the backup must still be what was written here.
    manifest["sha256"] = hashes(target, sorted(manifest["files"]))
    (target / "backup.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return target, manifest


def describe(manifest: dict) -> str:
    counts = manifest.get("asus_learn") or {}
    parts = [f"{n} {KINDS.get(k, k)}" for k, n in sorted(counts.items(), key=lambda kv: -kv[1])]
    size = sum(manifest.get("files", {}).values())
    return f"{size / 1024:.0f} KB" + (", " + ", ".join(parts) if parts else "")


# -- restore -------------------------------------------------------------------
def restore(root: Path, dest: Path, name: str, yes: bool, service: bool = True, check: bool = True,
            fail_after: int | None = None) -> int:
    """All or nothing: the backup is checked and a full copy is staged next
    to the live files before anything is touched; then the live files are
    moved aside and the staged ones moved in, and if any step fails every
    step already made is undone. (fail_after: a failure for the tests.)"""
    items = backups(dest)
    good = [n for n, m in items if m.get("ok")]
    if name == "latest":
        if not good:
            print("There is no good backup to restore.")
            return 1
        name = good[-1]
    src = dest / name
    if not (src / "backup.json").exists():
        print(f"No such backup: {name}")
        return 1
    manifest = dict(items)[name]
    if not manifest.get("ok"):
        print(f"{name} did not pass its check: {manifest.get('errors')}; not restoring it.")
        return 1
    rels = sorted(manifest.get("files", {}))
    print(f"Restore {name} ({describe(manifest)}):")
    for rel in rels:
        print(f"  {rel}")
    if not yes:
        print("Nothing was changed. Add --yes to restore; what is there now is first moved to "
              "backups/learn/_before-restore-<time>/.")
        return 0

    # 1. Is the backup still what was written? Can its databases be read?
    missing = [rel for rel in rels if not (src / rel).exists()]
    if missing:
        print(f"The backup is incomplete (missing {missing}); nothing was changed.")
        return 1
    want = manifest.get("sha256")
    have = hashes(src, rels)
    if want is not None and have != want:
        bad = sorted(k for k in set(want) | set(have) if want.get(k) != have.get(k))
        print(f"The backup has changed since it was made ({bad[:3]}...); nothing was changed.")
        return 1
    if check and not manifest.get("verified"):
        res = verify(src)
        if not res.get("ok"):
            print(f"The backup cannot be read back ({res.get('errors')}); nothing was changed.")
            return 1

    # 2. Stage a complete copy beside the live files (same disk: moving it in
    #    later is a rename) and compare it with the backup.
    stamp = f"{dt.datetime.now():%Y%m%d-%H%M%S}"
    staged = {rel: (root / rel).with_name((root / rel).name + f".restore-{stamp}") for rel in rels}
    aside = dest / f"_before-restore-{stamp}"
    done: list[tuple[str, str]] = []  # ("aside" | "in", rel), in the order made
    server = None
    try:
        try:
            for rel in rels:
                staged[rel].parent.mkdir(parents=True, exist_ok=True)
                if (src / rel).is_dir():
                    shutil.copytree(src / rel, staged[rel])
                else:
                    shutil.copy2(src / rel, staged[rel])
            for rel in rels:
                top = staged[rel]
                files = [top] if top.is_file() else sorted(x for x in top.rglob("*") if x.is_file())
                for f in files:
                    key = rel if top.is_file() else f"{rel}/{f.relative_to(top).as_posix()}"
                    if hashlib.sha256(f.read_bytes()).hexdigest() != have[key]:
                        raise OSError(f"the staged copy of {key} differs from the backup")
        except OSError as e:
            print(f"Could not prepare the restore ({e}); nothing was changed.")
            return 1

        # 3. Swap, with the server stopped; undo everything if a step fails.
        server = weasel_server() if service and server_running() else None
        if server and not stop_server(server):
            print(f"{server.name} did not quit; nothing was changed.")
            server = None
            return 1
        try:
            for n, rel in enumerate(rels):
                if fail_after is not None and n >= fail_after:
                    raise OSError("failure requested by the test")
                cur = root / rel
                if cur.exists():
                    (aside / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(cur), str(aside / rel))
                    done.append(("aside", rel))
                shutil.move(str(staged[rel]), str(cur))
                done.append(("in", rel))
        except OSError as e:
            for step, rel in reversed(done):
                cur = root / rel
                if step == "in":
                    shutil.move(str(cur), str(staged[rel]))
                else:
                    shutil.move(str(aside / rel), str(cur))
            print(f"The restore failed ({e}); everything was put back as it was.")
            return 1
    finally:
        for path in staged.values():  # whatever was not moved in
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            elif path.exists():
                path.unlink()
        if aside.is_dir() and not any(f.is_file() for f in aside.rglob("*")):
            shutil.rmtree(aside, ignore_errors=True)
        if server and not start_server(server):
            print(f"{server.name} did not start again: start it from the Start menu (島輝 / 小狼毫算法服務).")
    print("Restored." + (f" The previous files are in {aside}." if aside.is_dir() else ""))
    return 0


# -- Task Scheduler --------------------------------------------------------------
TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Daily local backup of what the Rime input method has learned (tools/backup_learn.py).</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <Repetition>
        <Interval>PT2H</Interval>
        <Duration>P1D</Duration>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>2026-01-01T00:30:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT40M</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>{arguments}</Arguments>
      <WorkingDirectory>{cwd}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def install() -> int:
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")  # no console window
    command = pythonw if pythonw.exists() else exe
    xml = TASK_XML.format(command=xml_escape(str(command)),
                          arguments=xml_escape(f'"{Path(__file__).resolve()}" --scheduled'),
                          cwd=xml_escape(str(ROOT)))
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "task.xml"
        path.write_text(xml, encoding="utf-16")
        proc = subprocess.run(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(path), "/F"],
                              capture_output=True)
    print((proc.stdout + proc.stderr).decode("mbcs", "replace").strip())
    if proc.returncode == 0:
        print(f"Task {TASK_NAME}: one backup a day, made when the PC has been idle for "
              f"{IDLE_SECONDS // 60} minutes (checked every 2 hours).")
    return proc.returncode


def uninstall() -> int:
    proc = subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], capture_output=True)
    print((proc.stdout + proc.stderr).decode("mbcs", "replace").strip())
    return proc.returncode


def main() -> int:
    if sys.stdout:
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--restore", metavar="NAME", help="a backup name from --list, or 'latest'")
    ap.add_argument("--yes", action="store_true", help="with --restore: really do it")
    ap.add_argument("--no-verify", action="store_true", help="skip reading the copy back")
    ap.add_argument("--scheduled", action="store_true",
                    help="what the daily task runs: once a day, only while the PC is idle")
    ap.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)  # tests
    ap.add_argument("--no-service", action="store_true", help=argparse.SUPPRESS)  # tests
    ap.add_argument("--verify-worker", type=Path, help=argparse.SUPPRESS)
    ap.add_argument("--fail-after", type=int, help=argparse.SUPPRESS)  # tests
    args = ap.parse_args()
    if args.verify_worker:
        return verify_worker(args.verify_worker)
    dest = args.root / "backups" / "learn"
    if args.install:
        return install()
    if args.uninstall:
        return uninstall()
    if args.list:
        for name, m in backups(dest):
            state = "ok " if m.get("ok") else "BAD"
            print(f"{name}  {state} {describe(m)}" + (f"  {m.get('errors')}" if not m.get("ok") else ""))
        return 0
    if args.restore:
        return restore(args.root, dest, args.restore, args.yes, service=not args.no_service,
                       check=not args.no_verify, fail_after=args.fail_after)
    if args.scheduled:
        today = dt.date.today().strftime("%Y%m%d")
        if any(n.startswith(today) and m.get("ok") for n, m in backups(dest)):
            return 0  # today's backup exists
        if not wait_for(lambda: idle_seconds() >= IDLE_SECONDS, IDLE_WAIT, step=5):
            log("skipped: the PC was in use; trying again in two hours", dest)
            return 0
    elif not args.no_service and server_running() and sys.stdout:
        print("The input method stops for 2-3 seconds while its databases are copied...")
    try:
        target, manifest = backup(args.root, dest, check=not args.no_verify, service=not args.no_service)
    except Exception as e:  # the scheduled task has no console: leave a trace
        log(f"FAILED {type(e).__name__}: {e}", dest)
        return 1
    if manifest["ok"] and not manifest.get("shrunk"):
        removed = prune(dest, dt.date.today())
        log(f"ok {target.name}: {describe(manifest)}" + (f"; removed {len(removed)} old" if removed else ""), dest)
        return 0
    if manifest["ok"]:
        log(f"WARNING {target.name}: far fewer entries than the previous backup ({describe(manifest)}); "
            f"nothing old was removed", dest)
        return 0
    log(f"BAD {target.name}: {manifest['errors']}", dest)
    return 1


if __name__ == "__main__":
    sys.exit(main())
