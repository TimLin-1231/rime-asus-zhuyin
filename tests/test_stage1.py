"""Stage 1 behaviour tests for the asus_zhuyin schema (run against a staged copy).

    python tests/test_stage1.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from rime_harness import CONTROL_MASK, SHIFT_MASK, open_rime  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    r = open_rime("stage1")

    # Compact bubble ------------------------------------------------------
    r.clear(); r.type("su3cl3"); s = r.snapshot()
    check("compact: one candidate, inline preview",
          len(s.candidates) == 1 and s.top == "你好" and s.preview == "你好",
          f"{s.candidates} preview={s.preview}")

    r.clear(); r.type("cj8"); s = r.snapshot()
    check("compact: pending syllable reading as comment",
          s.candidates and s.candidates[0][1] == "ㄏㄨㄚ", f"{s.candidates}")

    r.clear(); r.type("su3cl3"); s = r.snapshot()
    check("compact: no reading once syllable has a tone",
          s.candidates and s.candidates[0][1] == "", f"{s.candidates}")

    r.clear(); r.type("2k7"); s = r.snapshot()
    check("digits stay Zhuyin keys while composing",
          s.input == "2k7" and s.top == "的", f"input={s.input} top={s.top}")

    # Expanded menu -------------------------------------------------------
    r.clear(); r.type("ru04"); r.key("Down"); s = r.snapshot()
    check("Down expands to a full page with labels 1-9",
          len(s.candidates) == 9 and s.labels == [str(i) for i in range(1, 10)]
          and r.get_option("_asus_expanded"), f"n={len(s.candidates)} labels={s.labels}")
    expanded = [c for c, _ in s.candidates]

    r.key("3"); s = r.snapshot()
    check("expanded: digit selects on page and collapses",
          s.preview == expanded[2] and not r.get_option("_asus_expanded")
          and "3" not in s.input.replace("ru04", ""),
          f"preview={s.preview} want={expanded[2]} input={s.input}")

    r.clear(); r.type("ru04"); r.key("Down"); r.key("Down"); r.key("space"); s = r.snapshot()
    check("expanded: Space confirms highlighted candidate",
          s.preview == expanded[1] and not r.get_option("_asus_expanded"),
          f"preview={s.preview} want={expanded[1]}")

    r.clear(); r.type("ru04"); r.key("Down"); r.key("Escape"); s = r.snapshot()
    check("expanded: Esc collapses but keeps composition",
          s.composing and len(s.candidates) == 1 and not r.get_option("_asus_expanded"),
          f"composing={s.composing} n={len(s.candidates)}")

    r.clear(); r.type("ru04"); r.key("Down"); r.type("su3"); s = r.snapshot()
    check("expanded: typing resumes compact mode",
          len(s.candidates) == 1 and not r.get_option("_asus_expanded"), f"n={len(s.candidates)}")

    r.clear(); r.type("ru04"); r.key("Down"); r.clear(); r.type("w96"); s = r.snapshot()
    check("cleared while expanded: next input starts compact (digits stay Zhuyin)",
          s.input == "w96" and len(s.candidates) == 1 and not r.get_option("_asus_expanded"),
          f"input={s.input} n={len(s.candidates)} top={s.top}")

    r.clear(); r.type("ru04"); r.key("Down")
    for _ in range(4):
        r.key("BackSpace")
    r.type("w96"); s = r.snapshot()
    check("BackSpace to empty while expanded: next input starts compact",
          s.input == "w96" and len(s.candidates) == 1, f"input={s.input} n={len(s.candidates)}")

    # Accidental Shift ----------------------------------------------------
    r.clear(); r.type("su3"); r.key("C", SHIFT_MASK); r.type("l3"); s = r.snapshot()
    check("Shift+letter while composing acts as the plain key",
          s.input == "su3cl3" and s.top == "你好", f"input={s.input} top={s.top}")

    r.clear(); r.type("su3cl3"); r.tap("Shift_L"); s = r.snapshot()
    check("single Shift tap: no mode switch, nothing committed",
          not s.ascii_mode and s.composing and s.committed == "",
          f"ascii={s.ascii_mode} composing={s.composing} committed={s.committed!r}")

    time.sleep(0.6)
    r.clear(); r.type("su3cl3"); r.tap("Shift_L"); time.sleep(0.1); r.tap("Shift_R"); s = r.snapshot()
    check("double Shift tap: commits Chinese, switches to ascii",
          s.ascii_mode and s.committed == "你好" and not s.composing,
          f"ascii={s.ascii_mode} committed={s.committed!r}")
    time.sleep(0.6)
    r.clear(); r.tap("Shift_L"); time.sleep(0.1); r.tap("Shift_L"); s = r.snapshot()
    check("double Shift tap again: back to Chinese", not s.ascii_mode, f"ascii={s.ascii_mode}")

    time.sleep(0.6)
    r.clear(); r.tap("Shift_L"); time.sleep(0.7); r.tap("Shift_L"); s = r.snapshot()
    check("slow double tap (700 ms gap) is ignored", not s.ascii_mode, f"ascii={s.ascii_mode}")

    time.sleep(0.6)
    r.clear(); r.tap("Shift_L", hold_ms=450); time.sleep(0.05); r.tap("Shift_L", hold_ms=450)
    s = r.snapshot()
    check("held Shift (450 ms) is not a tap", not s.ascii_mode, f"ascii={s.ascii_mode}")

    time.sleep(0.6)
    r.clear(); r.tap("Shift_L"); r.type("a"); r.key("BackSpace"); r.tap("Shift_L"); s = r.snapshot()
    check("Shift, key, Shift is not a double tap", not s.ascii_mode, f"ascii={s.ascii_mode}")

    r.set_option("ascii_mode", False)
    r.clear(); r.type("su3cl3"); r.key("space", CONTROL_MASK); s = r.snapshot()
    check("Ctrl+Space: commits Chinese, switches to ascii",
          s.ascii_mode and s.committed == "你好", f"ascii={s.ascii_mode} committed={s.committed!r}")
    r.clear(); r.key("space", CONTROL_MASK); s = r.snapshot()
    check("Ctrl+Space again: back to Chinese", not s.ascii_mode, f"ascii={s.ascii_mode}")

    # Punctuation still works -------------------------------------------
    r.set_option("ascii_mode", False)
    r.clear(); r.type("su3cl3"); r.key("<", SHIFT_MASK); s = r.snapshot()
    check("Shift+, commits text and outputs full-width comma",
          s.committed + s.preview in ("你好，", "你好，，") or s.committed.startswith("你好，")
          or (s.committed == "你好" and s.top == "，"),
          f"committed={s.committed!r} preview={s.preview!r} top={s.top!r}")

    r.close()
    # A Lua runtime error makes the processor a silent no-op, which could let
    # negative tests pass vacuously, so any Lua error fails the run.
    lua_errors = [line for f in r.log_dir.glob("*.log*")
                  for line in f.read_text(encoding="utf-8", errors="replace").splitlines()
                  if line.startswith("E") and "lua" in line.lower()]
    check("no Lua runtime errors in librime log", not lua_errors,
          lua_errors[0] if lua_errors else "")
    width = max(len(n) for n, _, _ in RESULTS)
    failed = 0
    for name, ok, detail in RESULTS:
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {'' if ok else detail}")
    print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
