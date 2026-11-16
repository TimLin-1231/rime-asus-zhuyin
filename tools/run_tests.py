"""Run the test suite: every git-tracked tests/test_*.py, one file at a time.

    python tools/run_tests.py                    # default mode
    python tools/run_tests.py --both             # and again with RIME_TEST_RAW_KEYS=1
    python tools/run_tests.py tests/test_tab.py  # only the files named

Each file runs in its own process tree inside a Windows job object, with a
timeout (--timeout, seconds) after which the whole tree is killed (TIMEOUT).
Each run gets a private TEMP, so librime's logs (TEMP/rime.harness/...) of one
file never mix with another's; the distinct glog ERROR/FATAL lines are
counted and reported (tests that inject a bug on purpose log some).

--both runs every file a second time with RIME_TEST_RAW_KEYS=1:
tools/rime_harness.py then stages asus_lang/raw_keys: true (the product
default) unless the test sets raw_keys itself, and the summary lists what
fails only in that mode.

One line per file, then a summary. The results go to
.harness/test_runs/<UTC time>-<commit>.json (with each file's output under
.harness/test_runs/<that name>/). Exit code 1 if any file failed or timed out.
Untracked tests/test_*.py files are listed loudly and NOT run: track them
(git add) or delete them.
"""

from __future__ import annotations

import argparse
import ctypes as C
import ctypes.wintypes as W
import datetime as dt
import hashlib
import json
import msvcrt
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / ".harness" / "test_runs"
RAW_KEYS_ENV = "RIME_TEST_RAW_KEYS"
MODES = {"default": None, "raw_keys": "1"}

# -- process tree control (job object, kill on close) ---------------------------
k32 = C.WinDLL("kernel32", use_last_error=True)


class _BasicLimits(C.Structure):
    _fields_ = [("PerProcessUserTimeLimit", C.c_int64), ("PerJobUserTimeLimit", C.c_int64),
                ("LimitFlags", W.DWORD), ("MinimumWorkingSetSize", C.c_size_t),
                ("MaximumWorkingSetSize", C.c_size_t), ("ActiveProcessLimit", W.DWORD),
                ("Affinity", C.c_size_t), ("PriorityClass", W.DWORD), ("SchedulingClass", W.DWORD)]


class _IoCounters(C.Structure):
    _fields_ = [(n, C.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]


class _ExtendedLimits(C.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", C.c_size_t), ("JobMemoryLimit", C.c_size_t),
                ("PeakProcessMemoryUsed", C.c_size_t), ("PeakJobMemoryUsed", C.c_size_t)]


k32.CreateJobObjectW.restype = W.HANDLE
k32.CreateJobObjectW.argtypes = [C.c_void_p, W.LPCWSTR]
k32.SetInformationJobObject.argtypes = [W.HANDLE, C.c_int, C.c_void_p, W.DWORD]
k32.AssignProcessToJobObject.argtypes = [W.HANDLE, W.HANDLE]
k32.TerminateJobObject.argtypes = [W.HANDLE, C.c_uint]
k32.CloseHandle.argtypes = [W.HANDLE]
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JobObjectExtendedLimitInformation = 9


def make_job() -> int:
    job = k32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(C.get_last_error(), "CreateJobObjectW failed")
    info = _ExtendedLimits()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(job, JobObjectExtendedLimitInformation, C.byref(info), C.sizeof(info)):
        raise OSError(C.get_last_error(), "SetInformationJobObject failed")
    return job


# -- what to run ------------------------------------------------------------------
def git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(repo), "-c", "core.quotepath=off", *args],
                       capture_output=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise SystemExit(f"run_tests: git {' '.join(args)} failed in {repo}: {p.stderr.strip()}")
    return p.stdout


def tracked(repo: Path, pattern: str) -> list[str]:
    return sorted(line for line in git(repo, "ls-files", "--", pattern).splitlines() if line)


def tree_hash(repo: Path) -> str:
    """SHA-256 over the tracked tests/, tools/ and rime/ files as they are in
    ROOT now (what actually ran, committed or not)."""
    h = hashlib.sha256()
    for rel in tracked(repo, "tests") + tracked(repo, "tools") + tracked(repo, "rime"):
        p = ROOT / rel
        h.update(rel.encode("utf-8") + b"\0")
        h.update(hashlib.sha256(p.read_bytes()).digest() if p.is_file() else b"missing")
    return h.hexdigest()[:16]


def ascii_dir(preferred: Path, fallback_name: str) -> Path:
    """preferred if its real path is ASCII (glog cannot open other paths),
    else a folder under the system TEMP."""
    real = preferred.resolve()
    if str(real).isascii():
        return real
    return Path(os.environ.get("TEMP", r"C:\Windows\Temp")) / "daohui-test-runs" / fallback_name


# -- one file ---------------------------------------------------------------------
CHECK_RE = re.compile(r"^\s*\[?\s*(PASS|FAIL|OK|ERROR|SKIP)\b", re.I)
SUMMARY_RE = re.compile(r"(\d+)\s*/\s*(\d+)\s+(?:checks\s+)?passed", re.I)
FAIL_RE = re.compile(r"^\s*\[?\s*FAIL\b", re.I)
ERROR_LINE = re.compile(r"^[EF]\d{8} ")


def log_errors(tmp: Path) -> list[str]:
    """Distinct glog ERROR/FATAL lines of every librime log under tmp."""
    seen: dict[str, None] = {}
    base = tmp / "rime.harness"
    if base.is_dir():
        for f in sorted(base.rglob("*")):
            if f.is_file():
                for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                    if ERROR_LINE.match(line):
                        seen.setdefault(line)
    return list(seen)


def run_one(test: Path, mode: str, timeout: float, run_dir: Path, tmp_root: Path) -> dict:
    stem = test.stem
    log = run_dir / mode / f"{test.name}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    tmp = tmp_root / mode / stem
    tmp.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1",
               TEMP=str(tmp), TMP=str(tmp))
    env.pop(RAW_KEYS_ENV, None)
    if MODES[mode] is not None:
        env[RAW_KEYS_ENV] = MODES[mode]
    job = make_job()
    timed_out = False
    t0 = time.perf_counter()
    started = dt.datetime.now().isoformat(timespec="seconds")
    try:
        with open(log, "wb") as f:
            p = subprocess.Popen([sys.executable, "-B", str(test)], cwd=str(ROOT), env=env,
                                 stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                 creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
            in_job = bool(k32.AssignProcessToJobObject(job, int(p._handle)))
            if not in_job:
                print(f"run_tests: warning: {test.name} is not in a job object (error {C.get_last_error()}); "
                      "on a timeout only taskkill /T can end its children")
            try:
                rc = p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                k32.TerminateJobObject(job, 1)
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
                try:
                    rc = p.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    rc = None
    finally:
        k32.TerminateJobObject(job, 1)  # stragglers the test left behind
        k32.CloseHandle(job)
    wall = time.perf_counter() - t0
    lines = log.read_bytes().decode("utf-8", "replace").splitlines()
    counts: dict[str, int] = {}
    for line in lines:
        m = CHECK_RE.match(line)
        if m:
            counts[m.group(1).upper()] = counts.get(m.group(1).upper(), 0) + 1
    summaries = [m.group(0) for line in lines for m in [SUMMARY_RE.search(line)] if m]
    errors = log_errors(tmp)
    status = "TIMEOUT" if timed_out else ("PASS" if rc == 0 else "FAIL")
    # A pass needs evidence: exit 0 but a summary short of its total, or no
    # check line at all, is not a pass.
    last = SUMMARY_RE.search(summaries[-1]) if summaries else None
    if status == "PASS" and last and last.group(1) != last.group(2):
        status = "FAIL"
    if status == "PASS" and not counts and not summaries:
        status = "FAIL"
    return {"file": test.relative_to(ROOT).as_posix(), "mode": mode, "status": status, "exit": rc,
            "in_job": in_job,
            "wall_s": round(wall, 1), "started": started, "checks": counts,
            "summary": summaries[-1] if summaries else "",
            "fail_lines": [line.strip() for line in lines if FAIL_RE.match(line)][:50],
            "log_error_lines": len(errors), "log_errors_first": errors[:5],
            "output": str(log), "temp": str(tmp), "tail": lines[-30:]}


def check_key(line: str) -> str:
    """A FAIL line without its details (they often hold timings or values)."""
    return " ".join(re.split(r"\s{2,}", line.strip())[:2])


# -- the run ------------------------------------------------------------------------
def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", help="only these test files (default: every tracked tests/test_*.py)")
    ap.add_argument("--both", action="store_true", help=f"also run every file with {RAW_KEYS_ENV}=1")
    ap.add_argument("--timeout", type=float, default=600, help="seconds per file (default 600)")
    ap.add_argument("--list-from", type=Path, default=ROOT,
                    help="git checkout whose tracked files define the suite (default: this one); "
                         "for a copy of the repo that has no .git")
    ap.add_argument("--out", type=Path, default=OUT_DIR, help=f"results folder (default {OUT_DIR})")
    args = ap.parse_args()
    repo = args.list_from.resolve()

    all_tracked = [t for t in tracked(repo, "tests/test_*.py")]
    present = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "tests").glob("test_*.py"))
    untracked = [t for t in present if t not in all_tracked]
    if args.files:
        names = []
        for f in args.files:
            # Relative to this checkout, never to the shell's folder: a test
            # imports the rime_harness next to it, so a file of another
            # checkout would stage and log there.
            p = Path(f) if Path(f).is_absolute() else ROOT / f
            p = p.resolve()
            if not p.is_file():
                raise SystemExit(f"run_tests: no such test file: {p}")
            if not p.is_relative_to(ROOT):
                raise SystemExit(f"run_tests: {p} is not in this checkout ({ROOT})")
            names.append(p)
        tests = names
        named = {p.relative_to(ROOT).as_posix() for p in names}
        untracked = [t for t in untracked if t not in named]
    else:
        missing = [t for t in all_tracked if not (ROOT / t).is_file()]
        if missing:
            print(f"run_tests: tracked but missing here (counted as FAIL): {', '.join(missing)}")
        tests = [ROOT / t for t in all_tracked]

    modes = ["default", "raw_keys"] if args.both else ["default"]
    commit = git(repo, "rev-parse", "--short", "HEAD").strip()
    dirty = bool(git(repo, "status", "--porcelain", "--untracked-files=no").strip())
    utc = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{utc}-{commit}{'-dirty' if dirty else ''}"
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    report = out / f"{run_id}.json"
    run_dir = out / run_id
    tmp_root = ascii_dir(run_dir, run_id) / "tmp"

    # One suite run at a time per results folder (= per checkout): librime runs
    # of one checkout share .harness/<name>/user.
    lock_file = open(out / ".lock", "a+b")
    try:
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        raise SystemExit(f"run_tests: another run_tests.py is running for {out}")

    def loud_untracked() -> None:
        if untracked:
            print(f"\n!!! {len(untracked)} UNTRACKED test file(s) were NOT run "
                  f"(git add them or delete them):")
            for t in untracked:
                print(f"!!!   {t}")

    loud_untracked()
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        import rime_harness  # noqa: E402  (no librime is loaded by the import)
        rime_root = str(rime_harness.weasel_root())
        dll_sha = hashlib.sha256((Path(rime_root) / "rime.dll").read_bytes()).hexdigest()[:16]
    except Exception as e:  # report it; the tests themselves will fail loudly
        rime_root, dll_sha = f"unavailable: {e}", ""
    meta = {"run_id": run_id, "root": str(ROOT), "list_from": str(repo), "commit": commit, "dirty": dirty,
            "tree_hash": tree_hash(repo), "python": sys.executable, "python_version": platform.python_version(),
            "rime_root": rime_root, "rime_dll_sha256": dll_sha, "modes": modes, "timeout_s": args.timeout,
            "started_utc": utc, "untracked_not_run": untracked, "files": len(tests)}
    print(f"run_tests: {len(tests)} file(s) x {len(modes)} mode(s), timeout {args.timeout:g} s each; "
          f"commit {commit}{' (dirty)' if dirty else ''}; librime from {rime_root}")
    results: list[dict] = []

    def save(finished: bool) -> None:
        doc = {**meta, "finished": finished, "results": results}
        report.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    t_all = time.perf_counter()
    width = max((len(t.name) for t in tests), default=10)
    for mode in modes:
        for test in tests:
            if not test.is_file():
                r = {"file": test.relative_to(ROOT).as_posix(), "mode": mode, "status": "FAIL", "exit": None,
                     "wall_s": 0, "summary": "file missing", "fail_lines": [], "log_error_lines": 0}
            else:
                r = run_one(test, mode, args.timeout, run_dir, tmp_root)
            results.append(r)
            save(False)
            print(f"{r['status']:<7} {mode:<8} {test.name:<{width}}  {r['summary'] or '-':<16} "
                  f"{r['wall_s']:>6.1f} s  log errors {r['log_error_lines']}")
    meta["wall_s"] = round(time.perf_counter() - t_all, 1)

    bad = [r for r in results if r["status"] != "PASS"]
    print()
    for mode in modes:
        rs = [r for r in results if r["mode"] == mode]
        n = {s: sum(r["status"] == s for r in rs) for s in ("PASS", "FAIL", "TIMEOUT")}
        print(f"{mode}: {len(rs)} file(s): {n['PASS']} passed, {n['FAIL']} failed, {n['TIMEOUT']} timed out")
        for r in rs:
            if r["status"] != "PASS":
                print(f"    {r['status']} {r['file']}  {r['summary']}")
    if args.both:
        by = {(r["file"], r["mode"]): r for r in results}
        only_raw = []
        for test in tests:
            f = test.relative_to(ROOT).as_posix()
            d, k = by[(f, "default")], by[(f, "raw_keys")]
            if k["status"] == "PASS":
                continue
            new = [line for line in k["fail_lines"]
                   if check_key(line) not in {check_key(x) for x in d["fail_lines"]}]
            if d["status"] == "PASS" or new:
                only_raw.append({"file": f, "default": d["status"], "raw_keys": k["status"], "checks": new})
        meta["fail_only_in_raw_keys"] = only_raw
        print(f"\nfailing only with {RAW_KEYS_ENV}=1: {len(only_raw)} file(s)")
        for o in only_raw:
            print(f"    {o['file']} (default {o['default']}, raw_keys {o['raw_keys']})")
            for line in o["checks"][:20]:
                print(f"        {line}")
    save(True)
    loud_untracked()
    print(f"\nresult file: {report}")
    print(f"{'FAILED' if bad else 'ALL PASSED'}: {len(results) - len(bad)}/{len(results)} runs passed "
          f"in {meta['wall_s']:.0f} s")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
