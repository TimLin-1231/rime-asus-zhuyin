"""Stage 9 stability tests: nothing may freeze the input method or a test run.

  * the segmentor's rewind guard: with an endless-loop bug put back into
    asus_lang.lua, the key still returns and the next input works
  * the harness watchdog: with the guard removed as well, the process ends
    with KEY_TIMEOUT_EXIT instead of running (and logging) for days

    python tests/test_stage9.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from rime_harness import KEY_TIMEOUT_EXIT, SHIFT_MASK, open_rime  # noqa: E402
from zhuyin import reading_keys  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
NO_LEARN = "patch:\n  asus_learn/enabled: false\n  translator/enable_user_dict: false\n"
# The stage 6b bug: the list at the cursor replaced the whole plan, so an
# English word kept before the cursor never matched it (API的用法 + Down).
FIX = "    spans = before\n"
BUG = "    spans = { before[#before] }\n"
GUARD = "    if guard_tripped(env, input) then return true end\n"


def keys(text: str) -> str:
    return "".join(reading_keys(text)[0])


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


# ...and nothing translated the text to lock before the cursor.
EDIT_FIX = "  if fs and tonumber(fs) == seg.start then\n"
EDIT_BUG = "  if false then\n"


def broken(with_guard: bool) -> dict[str, str]:
    """Extra files that put the bug back (and optionally remove the guard)."""
    lang = (ROOT / "rime" / "lua" / "asus_lang.lua").read_text(encoding="utf-8")
    edit = (ROOT / "rime" / "lua" / "asus_edit.lua").read_text(encoding="utf-8")
    assert lang.count(FIX) == 1 and lang.count(GUARD) == 1 and edit.count(EDIT_FIX) == 1, \
        "asus_lang.lua / asus_edit.lua changed: update this test"
    lang = lang.replace(FIX, BUG)
    if not with_guard:
        lang = lang.replace(GUARD, "")
    return {"asus_zhuyin.custom.yaml": NO_LEARN, "lua/asus_lang.lua": lang,
            "lua/asus_edit.lua": edit.replace(EDIT_FIX, EDIT_BUG)}


def loop_keys(r) -> None:
    for ch in "API" + keys("的用法"):
        r.key(ch, SHIFT_MASK if ch.isupper() else 0)
    r.key("Down")


def child() -> None:
    """No guard: the watchdog has to end this process."""
    r = open_rime("stage9-hang", extra=broken(with_guard=False))
    loop_keys(r)
    sys.exit(0)  # not reached while the bug loops


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if "--child" in sys.argv:
        child()

    r = open_rime("stage9", extra=broken(with_guard=True))
    r.set_option("prediction", False)
    t0 = time.perf_counter()
    loop_keys(r)
    dt = time.perf_counter() - t0
    s = r.snapshot()
    check("an endless segmentation loop is cut short (under 2 s)", dt < 2.0, f"{dt:.1f} s")
    check("the input is still there afterwards", s.composing and s.input.startswith("API"), f"{s.input!r}")
    r.clear()
    r.type(keys("今天天氣很好")); s = r.snapshot(); r.clear()
    check("the next input converts as usual", s.preview == "今天天氣很好", f"{s.preview!r}")
    r.close()
    log = "\n".join(f.read_text(encoding="utf-8", errors="replace") for f in r.log_dir.glob("*.log*"))
    check("the guard reports itself in the log", "did not settle" in log)

    # The current code must not need the guard for the inputs that used to loop.
    proc = subprocess.run([sys.executable, __file__, "--clean"], capture_output=True, timeout=300)
    check("the real asus_lang.lua never trips the guard on those inputs", proc.returncode == 0,
          proc.stdout.decode("utf-8", "replace")[-300:] + proc.stderr.decode("utf-8", "replace")[-300:])

    t0 = time.perf_counter()
    proc = subprocess.run([sys.executable, __file__, "--child"], capture_output=True, timeout=120,
                          env={**os.environ, "RIME_KEY_TIMEOUT": "4"})
    dt = time.perf_counter() - t0
    err = proc.stderr.decode("utf-8", "replace")
    check("without the guard the harness watchdog ends the process",
          proc.returncode == KEY_TIMEOUT_EXIT and "librime is stuck" in err, f"rc={proc.returncode} {err[-200:]!r}")
    check("...within seconds, not days", dt < 60, f"{dt:.0f} s")

    # A loop slower than 300 rewinds a second never filled the one-second
    # window; rewinds are counted per key event as well.
    t0 = time.perf_counter()
    proc = subprocess.run([sys.executable, __file__, "--slow"], capture_output=True, timeout=120,
                          env={**os.environ, "RIME_KEY_TIMEOUT": "20"})
    dt = time.perf_counter() - t0
    check("a slow endless loop (5 ms a round) is cut short too", proc.returncode == 0,
          f"rc={proc.returncode} after {dt:.0f} s {proc.stderr.decode('utf-8', 'replace')[-200:]!r}")

    width = max(len(n) for n, _, _ in RESULTS)
    failed = 0
    for name, ok, detail in RESULTS:
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {'' if ok else detail}")
    print(f"{len(RESULTS) - failed}/{len(RESULTS)} passed")
    return 1 if failed else 0


def slow() -> int:
    """The bug again, each round of the loop taking 5 ms (under 300 a second)."""
    extra = broken(with_guard=True)
    wait = "    do local t0 = os.clock(); while os.clock() - t0 < 0.005 do end end\n"
    extra["lua/asus_lang.lua"] = extra["lua/asus_lang.lua"].replace(GUARD, wait + GUARD)
    r = open_rime("stage9-slow", extra=extra)
    loop_keys(r)
    r.clear()
    r.type(keys("今天天氣很好")); ok = r.snapshot().preview == "今天天氣很好"
    r.close()
    log = "\n".join(f.read_text(encoding="utf-8", errors="replace") for f in r.log_dir.glob("*.log*"))
    return 0 if ok and "did not settle" in log else 1


def clean() -> int:
    """The shipped code on the inputs that looped before their fixes."""
    r = open_rime("stage9-clean", extra={"asus_zhuyin.custom.yaml": NO_LEARN})
    loop_keys(r)
    r.clear()
    for ch in "I'm fine":
        r.key("space" if ch == " " else ch, SHIFT_MASK if ch.isupper() else 0)
    r.close()
    log = "\n".join(f.read_text(encoding="utf-8", errors="replace") for f in r.log_dir.glob("*.log*"))
    return 1 if "did not settle" in log else 0


if __name__ == "__main__":
    sys.exit(clean() if "--clean" in sys.argv else slow() if "--slow" in sys.argv else main())
