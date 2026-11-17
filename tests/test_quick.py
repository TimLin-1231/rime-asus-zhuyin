"""Dates and number forms (rime/lua/asus_quick.lua).

    python tests/test_quick.py

The clock is fixed at 2026-12-31 09:00 (a Thursday) through the schema
option asus_quick/now, so the end of the year and the morning are covered.
Noon (12:30) runs in a child process (--noon), since librime loads its
config once per process.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from rime_harness import error_lines, open_rime  # noqa: E402
from zhuyin import reading_keys  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def keys(text: str) -> str:
    return "".join(reading_keys(text)[0])


NOON = "NOON "
NOON_ERR = "NOONERR "


def noon_child() -> int:
    """--noon: the clock at 2026-12-31 12:30; prints the list of 現在 + Down."""
    sys.stdout.reconfigure(encoding="utf-8")
    r = open_rime("quick_noon", extra={"asus_zhuyin.custom.yaml": (
        "patch:\n  translator/enable_user_dict: false\n"
        '  asus_quick/now: "2026-12-31 12:30"\n')})
    r.set_option("prediction", False)
    r.clear(); r.type(keys("現在")); r.key("Down")
    print(NOON + json.dumps([c for c, _ in r.snapshot().candidates], ensure_ascii=False), flush=True)
    r.close()
    # Every ERROR/FATAL line of this process's librime logs, for main().
    for line in error_lines():
        print(NOON_ERR + line[:300], flush=True)
    return 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    # Learning stays on: picking a date must not be learned as a correction.
    r = open_rime("quick", extra={"asus_zhuyin.custom.yaml": (
        "patch:\n  translator/enable_user_dict: false\n"
        '  asus_quick/now: "2026-12-31 09:00"\n')})
    r.set_option("prediction", False)

    def listed(*seq: str) -> list[str]:
        r.clear()
        for k in seq:
            if k in ("Down", "Tab"):
                r.key(k)
            else:
                r.type(k)
        out = [c for c, _ in r.snapshot().candidates]
        return out

    # Dates ------------------------------------------------------------------
    got = listed(keys("今天"), "Down")
    check("今天 + Down: the dates follow it",
          got[:6] == ["今天", "2026-12-31", "2026/12/31", "2026年12月31日", "民國115年12月31日", "12月31日（四）"],
          f"{got[:6]}")
    r.clear(); r.type(keys("今天")); s = r.snapshot()
    check("closed list: still just 今天", s.preview == "今天" and "2026-12-31" not in [c for c, _ in s.candidates],
          f"{s.preview!r} {s.candidates[:3]}")
    for word, day in (("明天", "2027-01-01"), ("昨天", "2026-12-30"), ("後天", "2027-01-02"), ("前天", "2026-12-29")):
        got = listed(keys(word), "Down")
        check(f"{word}: {day}", day in got[:3], f"{got[:3]}")
    got = listed(keys("現在"), "Down")
    check("現在: the time", got[:4] == ["現在", "09:00", "上午9:00", "2026-12-31 09:00"], f"{got[:4]}")
    got = listed(keys("我今天"), "Down")
    check("我今天 + Down (the last word's list): the dates", "2026-12-31" in got, f"{got[:4]}")

    r.clear(); r.type(keys("今天")); r.key("Down"); r.key("5"); s = r.snapshot()
    check("今天 + Down + 5 commits 民國115年12月31日", s.committed == "民國115年12月31日", f"{s.committed!r}")
    r.clear(); r.type(keys("今天")); s = r.snapshot()
    check("picking a date is not learned: 今天 again", s.preview == "今天", f"{s.preview!r}")

    # Numbers ------------------------------------------------------------------
    got = listed("1234", "Tab")
    check("1234 + Tab: its forms", got[:7] == ["1234", "1,234", "壹仟貳佰參拾肆", "新臺幣壹仟貳佰參拾肆元整",
                                               "一千二百三十四", "一二三四", "１２３４"], f"{got[:7]}")
    r.clear(); r.type("1234"); r.key("Tab"); r.key("3"); s = r.snapshot()
    check("1234 + Tab + 3 commits 壹仟貳佰參拾肆", s.committed == "壹仟貳佰參拾肆", f"{s.committed!r}")
    got = listed("3.14", "Tab")
    check("3.14: decimals read digit by digit", got[:4] == ["3.14", "參點壹肆", "三點一四", "３．１４"], f"{got[:4]}")
    for k, want in (("20304", ["貳萬零參佰零肆", "二萬零三百零四"]), ("100001000", ["壹億零壹仟", "一億零一千"]),
                    ("10001000", ["壹仟萬壹仟", "一千萬一千"]), ("100000", ["壹拾萬", "十萬"]),
                    ("15", ["壹拾伍", "十五"]), ("0", ["零"]), ("1000000000000", ["壹兆", "一兆"])):
        got = listed(k, "Tab")
        check(f"{k}: {' / '.join(want)}", all(w in got for w in want), f"{got[:7]}")
    got = listed("0", "Tab")
    check("0: no 新臺幣零元整", not any(c.startswith("新臺幣") for c in got), f"{got[:5]}")
    got = listed("204", "Tab")
    check("204 (shown as 但) + Tab: the number first", got[:2] == ["204", "貳佰零肆"], f"{got[:3]}")
    got = listed(keys("我要") + "1234", "Tab")
    check("a number after Chinese: its forms", got[:2] == ["1234", "1,234"], f"{got[:3]}")
    r.clear(); r.type(keys("我要") + "1234"); r.key("Tab"); r.key("2"); s = r.snapshot()
    check("...picking one keeps the Chinese", s.committed == "我要1,234", f"{s.committed!r}")
    got = listed("12345678901234567", "Tab")
    check("17 digits: no forms (beyond 兆)", "1,234" not in "".join(got), f"{got[:3]}")

    # What Tab and Down did before is unchanged.
    got = listed("2026", "Down")
    check("2026 + Down: unchanged (number, full width)", got[:2] == ["2026", "２０２６"], f"{got[:3]}")
    r.clear(); r.type(keys("你")); handled = r.key("Tab"); s = r.snapshot()
    check("Tab on Chinese is not a number", s.preview == "你" and "壹" not in "".join(c for c, _ in s.candidates),
          f"handled={handled} {s.preview!r}")

    r.close()
    # Every log dir of this process (rime_harness.error_lines()); the noon
    # child reports its own below. Nothing here provokes an error line.
    errors = error_lines()
    check("no error lines in the librime log (any E or F line)", not errors, errors[0] if errors else "")

    # Noon: 12:xx is 下午 (12-hour clock), in its own librime process.
    try:
        p = subprocess.run([sys.executable, __file__, "--noon"], capture_output=True, timeout=240)
        out, rc = p.stdout.decode("utf-8", errors="replace"), p.returncode
    except subprocess.TimeoutExpired:
        out, rc = "", "timeout"
    line = next((ln[len(NOON):] for ln in out.splitlines() if ln.startswith(NOON)), "")
    try:
        got = json.loads(line) if line else []
    except ValueError:
        got = []
    check("現在 at 12:30: 下午12:30 (noon is 下午)",
          got[:4] == ["現在", "12:30", "下午12:30", "2026-12-31 12:30"], f"rc={rc} {got[:4]} raw={line[:80]!r}")
    errors = [ln[len(NOON_ERR):] for ln in out.splitlines() if ln.startswith(NOON_ERR)]
    check("the noon process ends cleanly and logs no error", rc == 0 and not errors,
          f"rc={rc} {errors[:3]}")

    width = max(len(n) for n, _, _ in RESULTS)
    failed = 0
    for name, ok, detail in RESULTS:
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {'' if ok else detail}")
    print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(noon_child() if "--noon" in sys.argv[1:] else main())
