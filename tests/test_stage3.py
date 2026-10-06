"""Stage 3 behaviour tests: mixed Chinese/English, stray keys, BackSpace.

    python tests/test_stage3.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from rime_harness import SHIFT_MASK, open_rime  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    r = open_rime("stage3")

    def typed(keys: str):
        r.clear()
        for ch in keys:
            r.key(ch, SHIFT_MASK if ch.isupper() else 0)
        s = r.snapshot()
        return s, s.committed + s.preview

    # Mixed input --------------------------------------------------------
    for keys, want in [
        ("python", "python"),
        ("ji3m/4pythonql3", "我用python跑"),
        ("ji3m/4pythonql3model", "我用python跑model"),
        ("because", "because"),  # "bec" is a word too: kept segments must be re-planned
        ("ji3because", "我because"),
        ("Python", "Python"),
        ("GPU2k7", "GPU的"),
        ("su3cl3", "你好"),
        ("2u04sl3", "電腦"),
    ]:
        s, out = typed(keys)
        check(f"mixed: {keys!r} -> {want}", out == want, f"got {out!r}")

    s, out = typed("5j/ u/ cjp283")
    check("toneless ㄏㄨㄣ never yields CJK Extension B+ characters",
          all(ord(c) < 0x20000 for c in out), f"got {out!r}")

    s, out = typed("python")
    check("English bubble shows no Zhuyin reading comment",
          s.candidates and s.candidates[0][1] == "", f"{s.candidates[:1]}")

    r.clear(); r.type("python"); r.key("Down"); s = r.snapshot()
    check("Chinese reading of the same keys stays available below English",
          len(s.candidates) > 1, f"n={len(s.candidates)}")

    r.clear(); r.type("ji3m/4python"); r.key("space"); s = r.snapshot()
    check("Space after English commits everything plus the space",
          s.committed == "我用python " and not s.composing, f"committed={s.committed!r}")

    # An English sentence typed word by word (reported: "Ihave貓pencil").
    r.clear()
    for word in ["I", "have", "a", "pencil"]:
        for ch in word:
            r.key(ch, SHIFT_MASK if ch.isupper() else 0)
        r.key("space")
    s = r.snapshot()
    check("English sentence: I have a pencil", s.committed == "I have a pencil ",
          f"committed={s.committed!r}")

    # After English, Chinese with tones still converts normally.
    r.clear(); r.type("hello"); r.key("space"); r.type("su3cl3"); s = r.snapshot()
    check("Chinese with tones right after English still converts",
          s.committed == "hello " and s.preview == "你好", f"{s.committed!r} {s.preview!r}")

    # After Chinese, a lone "a" is still Zhuyin.
    r.clear(); r.type("su3cl3"); r.key("Return"); r.clear(); r.type("a"); s = r.snapshot()
    check("after Chinese, lone 'a' stays Zhuyin (ㄇ)", s.preview != "a",
          f"preview={s.preview!r}")

    r.clear(); r.type("su3cl3"); r.key("space"); s = r.snapshot()
    check("Space after Chinese is still the first-tone key (no commit)",
          s.committed == "", f"committed={s.committed!r}")

    # Stray keys ------------------------------------------------------------
    s, out = typed("su3xcl3")
    check("stray key x in 你好 is dropped", out == "你好", f"got {out!r}")

    # BackSpace -------------------------------------------------------------
    r.clear(); r.type("su3cl3"); r.key("BackSpace"); s = r.snapshot()
    check("BackSpace removes the whole last character",
          s.input == "su3" and s.preview == "你", f"input={s.input!r} preview={s.preview!r}")

    r.clear(); r.type("ru0"); r.key("BackSpace"); s = r.snapshot()
    check("BackSpace in an unfinished syllable removes one key",
          s.input == "ru", f"input={s.input!r}")

    r.clear(); r.type("ru04"); r.key("BackSpace"); s = r.snapshot()
    check("BackSpace on a single character empties the input",
          not s.composing, f"input={s.input!r}")

    r.clear(); r.type("ji3python"); r.key("BackSpace"); s = r.snapshot()
    check("BackSpace in an English word removes one letter",
          s.input == "ji3pytho", f"input={s.input!r}")

    r.clear(); r.type("u "); r.key("BackSpace"); s = r.snapshot()
    check("BackSpace removes a first-tone syllable (ends with space)",
          not s.composing, f"input={s.input!r}")

    r.close()
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
