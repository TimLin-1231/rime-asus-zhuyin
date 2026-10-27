"""Half-width punctuation after letters and digits (idea from BlendKey, MIT).

Punctuation typed right after a letter or a digit stays ASCII (file.txt,
v2.3, I'll, 10:30, 10,000, snake_case), also when the word was already
committed; after Chinese the full-width ，。、「」 stay. Rules: header of
rime/lua/asus_symbols.lua.

    python tests/test_punct_halfwidth.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from rime_harness import CONTROL_MASK, SHIFT_MASK, open_rime  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
SHIFTED = set('~!@#$%^&*()_+{}|:"<>?')


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    r = open_rime("punct_halfwidth", extra={"asus_zhuyin.custom.yaml": "patch:\n  asus_english/compose: true\n"})

    def reset() -> None:
        # Same left context for every case: a Chinese full stop committed.
        r.clear()
        r.key(">", SHIFT_MASK)
        r.clear()

    def typ(*steps: str) -> None:
        """Strings are typed key by key (capitals and shifted symbols with
        Shift); <Name> is a named key."""
        for st in steps:
            if len(st) > 2 and st.startswith("<") and st.endswith(">"):
                r.key(st[1:-1])
                continue
            for ch in st:
                r.key(ch, SHIFT_MASK if (ch.isupper() or ch in SHIFTED) else 0)

    def committed() -> str:
        return "".join(r.committed)

    # -- after a committed word or number ------------------------------------
    after_commit = [
        (("file", "<Return>", ".", "txt", "<Return>"), "file.txt", "file, Enter, . txt"),
        (("2026", "<Return>", ".", "5", "<Return>"), "2026.5", "2026, Enter, . 5 (number after the dot)"),
        (("2026", "<Return>", ",", "ji3", "<Return>"), "2026,我", "2026, Enter, , 我"),
        (("2026", "<Return>", ":", "30", "<Return>"), "2026:30", "2026, Enter, : 30"),
        (("I", "<Return>", "'", "ll", "<Return>"), "I'll", "I, Enter, ' ll"),
        (("snake", "<Return>", "_", "case", "<Return>"), "snake_case", "snake, Enter, _ case"),
        (("Hello", "<Return>", ","), "Hello,", "Hello, Enter, ,"),
        (("C", "<Return>", "#"), "C#", "C, Enter, #"),
        (("US", "<Return>", "$"), "US$", "US, Enter, $ (not ￥)"),
        (("file", "<Return>", "="), "file=", "file, Enter, ="),
    ]
    for steps, want, label in after_commit:
        reset(); typ(*steps); s = r.snapshot()
        # (A prediction bubble may be showing: composing with no input.)
        check(f"after a commit: {label} -> {want}", committed() == want and s.input == "",
              f"committed={committed()!r} input={s.input!r}")

    # -- inside the composition ------------------------------------------------
    in_composition = [
        ("file.txt", "file.txt"), ("v2.3", "v2.3"), ("I'll", "I'll"), ("a@b.com", "a@b.com"),
        ("snake_case", "snake_case"), ("Hello,world", "Hello,world"), ("10:30", "10:30"),
        ("10,000", "10,000"), ("3.5", "3.5"), ("ji310:30", "我10:30"), ("ji32026,ji3", "我2026,我"),
        ("ji3v2.3ji3", "我v2.3我"), ("ji3py3.11", "我py3.11"), ("ji33,000", "我3,000"),
        ("ji32026.5", "我2026.5"), ("ji33.5", "我3.5"),
    ]
    for keys, want in in_composition:
        reset(); typ(keys, "<Return>")
        check(f"in the composition: {keys} -> {want}", committed() == want, f"committed={committed()!r}")

    reset(); typ("ji32026,"); s = r.snapshot()
    check("我2026, while typing shows the comma, not ㄝ", s.preedit == "我2026,", f"preedit={s.preedit!r}")
    reset(); typ("ji3file", "$"); s = r.snapshot()
    check("$ after an English word in the composition: 我file$", committed() == "我file$" and not s.composing,
          f"committed={committed()!r} input={s.input!r}")
    reset(); typ("ji350%")
    check("% after a number in the composition: 我50%", committed() == "我50%", f"committed={committed()!r}")
    reset(); typ("file", "="); s = r.snapshot()
    check("= after an English word types = (was Page_Down)", committed() == "file=", f"committed={committed()!r}")
    reset(); typ("ji310<"); s = r.snapshot()
    check("digits before ， are a number: 我10，", committed() == "我10，", f"committed={committed()!r}")

    # -- negative: after Chinese, Zhuyin keys, escapes --------------------------
    reset(); typ("su3cl3", "<Return>", "<")
    check("after Chinese, < is ，", committed() == "你好，", f"committed={committed()!r}")
    reset(); typ("su3cl3", "<Return>", ">")
    check("after Chinese, > is 。", committed() == "你好。", f"committed={committed()!r}")
    reset(); typ("su3cl3", "<Return>", "["); s = r.snapshot()
    check("after Chinese, [ is 「", s.preedit == "「", f"preedit={s.preedit!r}")
    reset(); typ("su3cl3", "<Return>", "."); s = r.snapshot()
    check("after Chinese, . is ㄡ (a syllable starts)", s.input == "." and s.composing and committed() == "你好",
          f"input={s.input!r} committed={committed()!r}")
    reset(); typ("ji3", "$"); s = r.snapshot()
    check("$ after Chinese in the composition stays ￥", s.preedit == "我￥", f"preedit={s.preedit!r}")
    reset(); typ(".3"); s = r.snapshot()
    check("ㄡˇ still types 偶", s.top == "偶", f"top={s.top!r}")
    reset(); typ("u, "); s = r.snapshot()
    check("ㄧㄝ still types 耶", s.top == "耶", f"top={s.top!r}")
    reset(); typ("OK", "<Return>", "u, "); s = r.snapshot()
    check("after a committed English word, ㄧㄝ (u,) still types 耶", s.top == "耶" and committed() == "OK",
          f"top={s.top!r} committed={committed()!r}")
    reset(); typ("file ", "."); s = r.snapshot()
    check("after a space, . is ㄡ again", s.input == "." and s.composing, f"input={s.input!r}")
    reset(); typ("file", "<Return>", ".", "<BackSpace>", "."); s = r.snapshot()
    check("BackSpace after the half-width . and . again gives ㄡ", s.input == "." and s.composing,
          f"input={s.input!r}")
    reset(); typ("file", "<Return>", "<")
    check("< after English stays ， (OK，我...)", committed() == "file，", f"committed={committed()!r}")
    reset(); typ("OK<ji3", "<Return>")
    check("OK< in the composition: OK，我", committed() == "OK，我", f"committed={committed()!r}")
    reset(); typ("file", "<Return>", "\\"); s = r.snapshot()
    check("\\ after English stays 、 (Python、Java)", s.preedit == "、", f"preedit={s.preedit!r}")
    reset(); typ("file", "<Return>", "]"); s = r.snapshot()
    check("] after English stays 」 (「OK」)", s.preedit == "」", f"preedit={s.preedit!r}")
    reset(); typ("file", "<Return>"); r.key(",", CONTROL_MASK)
    check("Ctrl+, after English is still ，", committed() == "file，", f"committed={committed()!r}")
    reset(); typ("file", "<Return>", "`"); s = r.snapshot()
    check("` after English opens the symbol keyboard", s.input == "`" and len(s.candidates) == 9,
          f"input={s.input!r} n={len(s.candidates)}")
    r.key("q")
    check("symbol keyboard unchanged: ` q -> 《", committed() == "file《", f"committed={committed()!r}")
    reset(); typ("2.ji3", "<Return>")
    check("2.ji3 (都 without its tone) stays Chinese", "." not in committed(), f"committed={committed()!r}")

    r.set_option("ascii_mode", True)
    reset(); typ("file", "."); s = r.snapshot()
    check("English mode: . is ASCII (as before)", "." in committed() + s.preedit and "ㄡ" not in s.preedit,
          f"committed={committed()!r} preedit={s.preedit!r}")
    r.set_option("ascii_mode", False)

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
