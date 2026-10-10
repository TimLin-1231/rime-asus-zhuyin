"""Stage 6 behaviour tests: editing inside the composition with a cursor.

    python tests/test_stage6.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from rime_harness import open_rime  # noqa: E402
from zhuyin import reading_keys  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
NO_LEARN = {"asus_zhuyin.custom.yaml": "patch:\n  asus_learn/enabled: false\n"
                                      "  translator/enable_user_dict: false\n"}


def keys(text: str) -> str:
    return "".join(reading_keys(text)[0])


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    r = open_rime("stage6", extra=NO_LEARN)
    r.set_option("prediction", False)

    def start(k: str) -> str:
        r.clear(); r.type(k)
        return r.snapshot().preview

    def cands() -> list[str]:
        return [c for c, _ in r.snapshot().candidates]

    sent = "但感覺沒有那麼著急或許要"
    first = start(keys(sent))
    check("precondition: sentence converts", first == sent, first)

    # Cursor movement and the bubble.
    r.key("Left"); s = r.snapshot()
    check("Left keeps the text and the input intact", s.preview == sent and s.input == keys(sent),
          f"{s.preview!r} {s.input!r}")
    check("the bubble marks the cursor and the reading after it",
          s.candidates and "｜要" in s.candidates[0][1] and "ㄧㄠˋ" in s.candidates[0][1],
          f"{s.candidates[:1]}")
    for _ in range(2):
        r.key("Left")
    s = r.snapshot()
    check("the cursor moves one character at a time", "急｜或許要" in (s.candidates[0][1] if s.candidates else ""),
          f"{s.candidates[:1]}")

    # Down at the cursor: words starting there; picking changes only them.
    r.key("Down"); c = cands()
    check("Down lists the current text first, then words starting at the cursor",
          c[:1] == ["或許要"] and "或許" in c and "貨" in c, f"{c[:8]}")
    if "貨" in c:
        r.key(str(c.index("貨") + 1))
    s = r.snapshot()
    check("picking replaces only that part; the rest stays", s.preview == "但感覺沒有那麼著急貨許要",
          s.preview)
    check("nothing is committed by a pick in the middle", not s.committed, f"{s.committed!r}")
    r.key("Return"); s = r.snapshot()
    check("Enter commits the edited sentence", s.committed == "但感覺沒有那麼著急貨許要", f"{s.committed!r}")

    # Down at the end of a long input: the list for the last word.
    start(keys(sent))
    r.key("Down"); c = cands()
    check("Down at the end lists the last word, not the start", c[:1] == ["要"] and "藥" in c, f"{c[:6]}")
    r.key("Escape"); s = r.snapshot()
    check("Esc after Down at the end puts everything back", s.preview == sent and s.composing,
          f"{s.preview!r}")
    r.key("Down"); c = cands()
    if "藥" in c:
        r.key(str(c.index("藥") + 1))
    s = r.snapshot()
    check("picking from it replaces only the last word", s.preview == sent[:-1] + "藥" and not s.committed,
          f"{s.preview!r} committed={s.committed!r}")
    r.clear()
    short = "你好"
    start(keys(short)); r.key("Down"); c = cands(); r.clear()
    check("a single word: Down shows its whole list as before", c[:2] == ["你好", "妳好"], f"{c[:4]}")

    # Home, BackSpace in the middle, insertion, Esc.
    start(keys(sent))
    r.key("Home"); r.key("Right"); r.key("Right"); r.key("BackSpace"); s = r.snapshot()
    check("BackSpace deletes the character before the cursor, keeping the rest",
          s.preview == "但覺沒有那麼著急或許要", s.preview)
    for ch in keys("感"):
        r.key(ch)
    s = r.snapshot()
    check("typing inserts at the cursor", s.preview == "但感覺沒有那麼著急或許要", s.preview)
    r.key("Escape"); s = r.snapshot()
    check("Esc leaves the cursor, the text stays", s.preview == "但感覺沒有那麼著急或許要" and s.composing,
          f"{s.preview!r} composing={s.composing}")
    for ch in keys("了"):
        r.key(ch)
    s = r.snapshot()
    check("typing at the end afterwards appends", s.preview == "但感覺沒有那麼著急或許要了", s.preview)
    r.clear()

    # Numbers and English are one cursor step; Down offers their forms.
    start(keys("我") + "2026" + keys("年畢業"))
    for _ in range(4):
        r.key("Left")
    r.key("Down"); c = cands()
    check("Down on a number: the number, full-width digits, Chinese readings",
          c[:2] == ["2026年畢業", "2026"] and "２０２６" in c, f"{c[:6]}")
    if "２０２６" in c:
        r.key(str(c.index("２０２６") + 1))
    s = r.snapshot()
    check("picking the full-width number keeps the rest", s.preview == "我２０２６年畢業", s.preview)
    r.clear()
    start(keys("我用") + "python" + keys("寫的"))
    r.key("Left"); r.key("Left"); r.key("Left"); r.key("Down"); c = cands()
    check("Down on an English word lists the word (and the Chinese readings)",
          "python" in c[:3], f"{c[:6]}")
    r.key("Escape"); s = r.snapshot()
    check("Esc closes the list without changes", s.preview == "我用python寫的", s.preview)
    r.clear()

    # Ordinary typing is unaffected.
    for text in ("我今天晚上要去吃飯", "謝謝你的幫忙"):
        check(f"no cursor: {text} as before", start(keys(text)) == text, start(keys(text)))
    r.close()

    # Learning sees a cursor pick like any other correction.
    r2 = open_rime("stage6-learn")
    r2.set_option("prediction", False)
    r2.clear(); r2.type(keys("請重新啟動應用程式")); before = r2.snapshot().preview
    r2.key("Left"); r2.key("Left"); r2.key("Down")
    c = [x for x, _ in r2.snapshot().candidates]
    ok = "程式" in c
    if ok:
        r2.key(str(c.index("程式") + 1)); r2.key("Return")
    r2.clear(); r2.type(keys("這個程式跑很慢")); after = r2.snapshot().preview; r2.clear()
    check("a cursor pick is learned (程式 in a new sentence)",
          before == "請重新啟動應用城市" and ok and after == "這個程式跑很慢", f"{before!r} {c[:5]} {after!r}")
    r2.close()

    lua_errors = [line for rr in (r, r2) for f in rr.log_dir.glob("*.log*")
                  for line in f.read_text(encoding="utf-8", errors="replace").splitlines()
                  if line.startswith("E") and "lua" in line.lower()]
    check("no Lua runtime errors in librime log", not lua_errors, lua_errors[0] if lua_errors else "")

    width = max(len(n) for n, _, _ in RESULTS)
    failed = 0
    for name, ok, detail in RESULTS:
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {'' if ok else detail}")
    print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
