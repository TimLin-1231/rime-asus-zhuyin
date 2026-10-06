"""Stage 2 behaviour tests: symbol keyboard, Ctrl+punctuation, shortcuts, lexicon.

    python tests/test_stage2.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from rime_harness import CONTROL_MASK, SHIFT_MASK, open_rime  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []

SHORTCUTS = "ㄉㄓ\t測試地址一號\nㄒㄏ\t此致\\n敬禮\n# comment\tignored\n"


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    r = open_rime("stage2", extra={"shortcuts.txt": SHORTCUTS})

    # Symbol keyboard ------------------------------------------------------
    r.clear(); r.key("`"); s = r.snapshot()
    check("` opens the symbol keyboard: full page, common punctuation first",
          s.input == "`" and len(s.candidates) == 9 and s.candidates[0][0] == "，"
          and s.candidates[0][1].startswith(",") and "【一般】" in s.candidates[0][1],
          f"input={s.input!r} n={len(s.candidates)} first={s.candidates[:2]}")

    r.key("q"); s = r.snapshot()
    check("` then Q commits 《 (ASUS 一般 layout)",
          s.committed == "《" and not s.composing, f"committed={s.committed!r}")

    r.clear(); r.key("`"); r.key("1"); s = r.snapshot()
    check("` then 1 commits full-width １ (digit is a layout key, not a select key)",
          s.committed == "１", f"committed={s.committed!r}")

    r.clear(); r.key("`"); r.key("Tab"); r.key("m"); s = r.snapshot()
    check("Tab switches to 數學: m -> μ", s.committed == "μ", f"committed={s.committed!r}")

    r.clear(); r.key("`"); r.key("Tab"); r.key("D", SHIFT_MASK); s = r.snapshot()
    check("數學: Shift+D -> Δ", s.committed == "Δ", f"committed={s.committed!r}")

    r.clear(); r.key("`"); r.key("Tab"); r.key("Tab"); r.key("d"); s = r.snapshot()
    check("Tab twice -> 特殊: d -> →", s.committed == "→", f"committed={s.committed!r}")

    r.clear(); r.key("`"); r.key("Tab"); r.key("Tab"); r.key("Tab"); r.key("q"); s = r.snapshot()
    check("Tab wraps around to 一般", s.committed == "《", f"committed={s.committed!r}")

    r.clear(); r.key("`"); r.key("Right"); r.key("space"); s = r.snapshot()
    check("Right + Space commits the highlighted symbol",
          s.committed == "。", f"committed={s.committed!r}")

    r.clear(); r.key("`"); r.key("Down"); s = r.snapshot()
    check("Down shows the next page of symbols",
          s.composing and s.candidates and s.candidates[0][0] != "，",
          f"first={s.candidates[:1]}")

    r.clear(); r.key("`"); r.key("Escape"); s = r.snapshot()
    check("Esc closes the symbol keyboard", not s.composing and s.committed == "",
          f"composing={s.composing} committed={s.committed!r}")

    r.clear(); r.key("`"); r.key("`"); s = r.snapshot()
    check("` twice commits ｀", s.committed == "｀", f"committed={s.committed!r}")

    r.clear(); r.type("su3"); r.key("`"); s = r.snapshot()
    check("` while composing Zhuyin does not open the keyboard mid-word",
          s.input.startswith("su3"), f"input={s.input!r} committed={s.committed!r}")

    # Ctrl+punctuation ----------------------------------------------------
    cases = [(",", 0, "，"), (".", 0, "。"), ("'", 0, "、"), (";", 0, "；"),
             (":", SHIFT_MASK, "："), ("?", SHIFT_MASK, "？"), ("!", SHIFT_MASK, "！"),
             ("{", SHIFT_MASK, "『"), ("}", SHIFT_MASK, "』"),
             ("(", SHIFT_MASK, "（"), (")", SHIFT_MASK, "）")]
    bad = []
    for key, mask, want in cases:
        r.clear(); r.key(key, CONTROL_MASK | mask)
        if r.snapshot().committed != want:
            bad.append((key, r.snapshot().committed))
    check("Ctrl(+Shift)+punctuation commits full-width punctuation", not bad, f"{bad}")

    r.clear(); r.key(";", CONTROL_MASK | SHIFT_MASK); s = r.snapshot()
    check("Ctrl+Shift+; in unshifted form also gives ：", s.committed == "：", f"{s.committed!r}")

    r.clear(); r.type("su3cl3"); r.key(",", CONTROL_MASK); s = r.snapshot()
    check("Ctrl+, while composing commits text then ，", s.committed == "你好，", f"{s.committed!r}")

    passed_through = []
    for key in ("/", "[", "]"):
        r.clear()
        if r.key(key, CONTROL_MASK) or r.snapshot().committed:
            passed_through.append(key)
    check("Ctrl+/ and Ctrl+[ ] are left to applications", not passed_through,
          f"intercepted={passed_through}")

    r.set_option("ascii_mode", True)
    r.clear(); handled = r.key(",", CONTROL_MASK); s = r.snapshot()
    check("ascii mode: Ctrl+, passes through", not handled and s.committed == "",
          f"handled={handled} committed={s.committed!r}")
    r.set_option("ascii_mode", False)

    r.clear(); r.key("(", SHIFT_MASK); r.key(")", SHIFT_MASK); s = r.snapshot()
    check("half-width mode: ( ) are ASCII parentheses", s.committed == "()",
          f"committed={s.committed!r}")
    r.clear(); r.type("su3cl3"); r.key("(", SHIFT_MASK); s = r.snapshot()
    check("( while composing commits text then (", s.committed == "你好(",
          f"committed={s.committed!r}")

    # Shortcuts -----------------------------------------------------------
    r.clear(); r.type("25"); s = r.snapshot()
    check("shortcut ㄉㄓ expands as the first candidate",
          s.top == "測試地址一號" and s.preview == "測試地址一號", f"top={s.top!r}")
    r.key("Return"); s = r.snapshot()
    check("Enter commits the expansion", s.committed == "測試地址一號", f"{s.committed!r}")

    r.clear(); r.type("vc"); s = r.snapshot()
    check("\\n in an expansion becomes a line break", s.top == "此致\n敬禮", f"top={s.top!r}")

    r.clear(); r.type("25"); r.key("Down"); s = r.snapshot()
    check("normal conversion stays available below the shortcut",
          len(s.candidates) > 1, f"n={len(s.candidates)}")

    # Lexicon (Taiwan readings / spellings) --------------------------------
    for keys, want, label in [
        ("j03g;4", "晚上", "晚上 with Taiwan reading ㄕㄤˋ"),
        ("u zj6", "衣服", "衣服 with Taiwan reading ㄈㄨˊ"),
        ("u/6aj4", "螢幕", "螢幕 preferred over 熒幕"),
        ("vm0 1j4", "宣布", "宣布 preferred over 宣佈"),
    ]:
        r.clear(); r.type(keys); s = r.snapshot()
        check(f"lexicon: {label}", s.top == want, f"top={s.top!r}")

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
