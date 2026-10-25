"""Shift+BackSpace forgets what was learned for the word left of the editing
cursor (or at the end of the input) and converts it again without it
(lua/asus_edit.lua, lua/asus_learn.lua).

    python tests/test_forget.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from eval_learn import Typist  # noqa: E402
from rime_harness import CONTROL_MASK, SHIFT_MASK, open_rime  # noqa: E402
from zhuyin import reading_keys  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def keys(text: str) -> str:
    return "".join(reading_keys(text)[0])


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    r = open_rime("forget")  # learning is on in the project config
    t = Typist(r, SHIFT_MASK, CONTROL_MASK)
    r.set_option("prediction", False)

    # At the end of the input (no cursor) ------------------------------------
    app, teach = "請重新啟動應用程式", "我把程式碼上傳到伺服器了"
    before = t.preview(keys(app))
    check("precondition: 應用程式 converts wrongly before learning", before != app, before)
    got, picks = t.enter(keys(teach), teach)
    check("teaching: the corrected sentence is committed", got == teach and picks > 0,
          f"got={got!r} picks={picks}")
    check("the learned word is applied in a new sentence", t.preview(keys(app)) == app,
          t.preview(keys(app)))
    t.reset_context(); t.type(keys(app))
    handled = r.key("BackSpace", SHIFT_MASK); s = r.snapshot(); r.clear()
    check("Shift+BackSpace at the end converts the last word again without the learning",
          handled and s.composing and s.input == keys(app) and s.preview == before,
          f"handled={handled} input={s.input!r} preview={s.preview!r} want={before!r}")
    check("...and a later fresh input no longer applies it", t.preview(keys(app)) == before,
          t.preview(keys(app)))
    other = "我把程式碼寄給你"
    check("(other corrections of the taught sentence are kept: 程式碼)",
          t.preview(keys(other)) == other, t.preview(keys(other)))

    # Left of the editing cursor ------------------------------------------------
    mask, probe = "疫情期間請配戴口罩", "騎車要配戴安全帽"
    before2 = t.preview(keys(probe))
    check("precondition: 配戴 converts wrongly before learning", before2 != probe, before2)
    t.enter(keys(mask), mask)
    check("the learned word is applied in a new sentence (配戴)", t.preview(keys(probe)) == probe,
          t.preview(keys(probe)))
    t.reset_context(); t.type(keys(probe))
    for _ in range(3):
        r.key("Left")  # cursor after 戴
    handled = r.key("BackSpace", SHIFT_MASK); s = r.snapshot()
    comment = s.candidates[0][1] if s.candidates else ""
    check("Shift+BackSpace at the cursor converts the word left of it again",
          handled and s.input == keys(probe) and s.preview == before2,
          f"handled={handled} preview={s.preview!r} want={before2!r}")
    check("...the cursor stays where it was", "｜安全帽" in comment, comment)
    r.key("End"); s = r.snapshot(); r.clear()
    check("...and End keeps the new conversion", s.preview == before2, s.preview)
    check("...and a later fresh input no longer applies it", t.preview(keys(probe)) == before2,
          t.preview(keys(probe)))

    # A name corrected in one character (自豪 -> 志豪), forgotten from its end ---
    name, call = "我打給志豪", "打電話給志豪"
    before3, alone = t.preview(keys(call)), t.preview(keys("志豪"))
    check("precondition: the name converts wrongly before learning",
          before3 != call and alone != "志豪", f"{before3} {alone}")
    t.enter(keys(name), name)
    check("the name is learned", t.preview(keys(call)) == call, t.preview(keys(call)))
    check("(typed alone it comes from Rime's own user phrase)", t.preview(keys("志豪")) == "志豪",
          t.preview(keys("志豪")))
    t.reset_context(); t.type(keys(call))
    handled = r.key("BackSpace", SHIFT_MASK); s = r.snapshot(); r.clear()
    check("Shift+BackSpace after the name forgets its corrected character",
          handled and s.preview == before3, f"handled={handled} preview={s.preview!r} want={before3!r}")
    check("...and Rime's own user phrase for it: typed alone it is as before",
          t.preview(keys("志豪")) == alone, t.preview(keys("志豪")))

    # Nothing learned there ----------------------------------------------------------
    plain = "今天天氣很好"
    t.reset_context(); t.type(keys(plain)); s0 = r.snapshot()
    handled = r.key("BackSpace", SHIFT_MASK); s = r.snapshot()
    check("nothing learned: Shift+BackSpace is swallowed and changes nothing",
          handled and s.input == s0.input and s.preview == s0.preview,
          f"handled={handled} {s0.preview!r} -> {s.preview!r} input {s.input!r}")
    r.key("Left"); s0 = r.snapshot()
    handled = r.key("BackSpace", SHIFT_MASK); s = r.snapshot(); r.clear()
    check("...also at the cursor",
          handled and s.input == s0.input and s.preview == s0.preview
          and s.candidates[:1] == s0.candidates[:1],
          f"handled={handled} {s0.candidates[:1]} -> {s.candidates[:1]}")

    # Plain BackSpace and outside a composition -------------------------------------
    syl = reading_keys(app)[0]
    t.reset_context(); t.type(keys(app))
    r.key("BackSpace"); s = r.snapshot(); r.clear()
    check("plain BackSpace still deletes the last character", s.input == "".join(syl[:-1]),
          f"input={s.input!r}")
    r.clear()
    handled = r.key("BackSpace", SHIFT_MASK)
    check("Shift+BackSpace with nothing typed goes to the app", not handled, f"handled={handled}")

    r.close()
    lua_errors = [line for f in r.log_dir.glob("*.log*")
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
