"""Stage 14 behaviour tests: English mode corrects a misspelt word on Space
(BackSpace takes the correction back) and offers the next word.

    python tests/test_stage14.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from rime_harness import SHIFT_MASK, open_rime  # noqa: E402
from zhuyin import reading_keys  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
NO_LEARN = "patch:\n  asus_learn/enabled: false\n  translator/enable_user_dict: false\n  asus_english/compose: true\n"
NAMED = ("Tab", "BackSpace", "Return", "Escape", "Down")


def keys(text: str) -> str:
    return "".join(reading_keys(text)[0])


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    r = open_rime("stage14", extra={"asus_zhuyin.custom.yaml": NO_LEARN})

    def run(*seq: str):
        """English mode, from a clean state: each item is a named key or
        text to type (capitals and ? ! : ) " with Shift). Returns (committed,
        snapshot, whether the last key was handled)."""
        r.set_option("ascii_mode", False)
        r.clear(); r.key(">", SHIFT_MASK); r.clear()
        r.set_option("ascii_mode", True)
        handled = True
        for item in seq:
            if item in NAMED:
                handled = r.key(item)
            else:
                for ch in item:
                    handled = r.key(ch, SHIFT_MASK if ch.isupper() or ch in '?!:)"' else 0)
        snap = r.snapshot()
        return "".join(r.committed), snap, handled

    def offered(snap) -> list[str]:
        return [comment for text, comment in snap.candidates if text == ""]

    # -- Automatic correction ------------------------------------------------
    got, snap, _ = run("recieve ")
    check("recieve + Space: receive is shown, not committed yet",
          got == "" and snap.preview == "receive " and snap.composing, f"{got!r} {snap.preview!r}")
    check("the bubble says what BackSpace brings back",
          snap.candidates[:1] == [("receive ", "⌫recieve")], f"{snap.candidates}")
    got, snap, _ = run("recieve ", "the")
    check("the next letter commits the correction", got == "receive " and snap.input == "the", f"{got!r} {snap.input!r}")
    got, snap, handled = run("recieve ", "Return")
    check("so does Enter, which then goes to the program", got == "receive " and not handled, f"{got!r} {handled}")
    got, snap, _ = run("becuase ", " ")
    check("a second Space commits it and is typed too", got == "because " and not snap.composing, f"{got!r}")
    got, snap, _ = run("seperate ", "BackSpace")
    check("BackSpace takes the correction back",
          got == "" and snap.input == "seperate" and snap.preview == "seperate", f"{got!r} {snap.input!r} {snap.preview!r}")
    check("...and the guesses are back", ("separate", "⇥") in snap.candidates, f"{snap.candidates}")
    got, snap, _ = run("seperate ")
    check("a word taken back once is left alone from then on", got == "seperate ", f"{got!r} {snap.preview!r}")
    got, snap, _ = run("goverment ", "Escape", " ")
    check("Esc takes it back too", got == "goverment ", f"{got!r}")
    got, snap, handled = run("definately", "Return")
    check("Enter commits what was typed, uncorrected", got == "definately" and not handled, f"{got!r}")
    got, snap, _ = run("teh,")
    check("a comma corrects as well (the,)", got == "" and snap.preview == "the,", f"{got!r} {snap.preview!r}")
    got, snap, handled = run("teh,", " ")
    check("...and the Space after it goes to the program", got == "the," and not handled, f"{got!r} {handled}")
    got, snap, _ = run("waht?", " ")
    check("a question mark too", got == "what?", f"{got!r}")
    got, snap, _ = run("untill. ", "a")
    check("a full stop typed after the word is kept (until. )", got == "until. ", f"{got!r}")
    for typed, want in (("dont", "don't"), ("im", "I'm"), ("i", "I"), ("Becuase", "Because"), ("Dont", "Don't"),
                        ("thier", "their"), ("tommorow", "tomorrow"), ("occured", "occurred"),
                        ("happend", "happened")):
        got, snap, _ = run(typed + " ", "a")
        check(f"{typed} -> {want}", got == want + " ", f"{got!r}")
    for word in ("the", "form", "numpy", "config", "github", "uint", "systemd", "repos", "dtype", "asus",
                 "colour", "gonna", "GPU", "iPhone", "node.js", "e-mail", "x86"):
        got, snap, _ = run(word + " ")
        check(f"{word} stays as typed", got == word + " ", f"{got!r} {snap.preview!r}")
    r.set_option("asus_en_noauto", True)
    got, snap, _ = run("acheive ")
    check("asus_en_noauto: nothing is corrected, nothing offered",
          got == "acheive " and not snap.composing, f"{got!r} {snap.candidates}")
    r.set_option("asus_en_noauto", False)

    # -- Next words ----------------------------------------------------------
    got, snap, _ = run("thank ")
    check("thank + Space: you is offered first", got == "thank " and offered(snap)[:1] == ["you"], f"{got!r} {snap.candidates}")
    check("nothing is shown inline", snap.preview == "", f"{snap.preview!r}")
    got, snap, _ = run("thank ", "Tab")
    check("Tab types it and a space, and offers again", got == "thank you " and len(offered(snap)) > 0, f"{got!r} {snap.candidates}")
    got, snap, _ = run("thank ", "y")
    check("a letter dismisses the offer and starts the next word",
          got == "thank " and snap.input == "y" and not offered(snap), f"{got!r} {snap.input!r}")
    got, snap, _ = run("thank ", ",")
    check("punctuation dismisses it and is typed", got == "thank ," and not snap.composing, f"{got!r}")
    got, snap, handled = run("thank ", "Return")
    check("Enter dismisses it and goes to the program", got == "thank " and not handled and not snap.composing, f"{got!r} {handled}")
    got, snap, handled = run("thank ", "BackSpace")
    check("BackSpace too", got == "thank " and not handled and not snap.composing, f"{got!r} {handled}")
    got, snap, _ = run("done. ")
    check("nothing is offered after a full stop", got == "done. " and not snap.composing, f"{got!r} {snap.candidates}")
    got, snap, _ = run("machine ", "lea", "Tab")
    check("the previous word still orders the guesses", got == "machine learning", f"{got!r}")
    r.set_option("prediction", False)
    got, snap, _ = run("thank ")
    check("the prediction switch turns the offer off", got == "thank " and not snap.composing, f"{snap.candidates}")
    r.set_option("prediction", True)

    # -- Chinese mode is as before -------------------------------------------
    r.set_option("ascii_mode", False)
    for seq, want in ((keys("今天天氣很好"), "今天天氣很好"),
                      (keys("幫我") + "check" + keys("一下這個數字"), "幫我check一下這個數字")):
        r.clear(); r.key(">", SHIFT_MASK); r.clear()
        r.type(seq); r.key("Return")
        got = "".join(r.committed); r.clear()
        check(f"still converts: {want}", got == want, got)
    r.clear(); r.key(">", SHIFT_MASK); r.clear()
    r.type("recieve ")
    got = "".join(r.committed); snap = r.snapshot(); r.clear()
    check("Chinese mode: Space corrects a misspelt word too (stage 17)",
          got == "" and snap.preview == "receive ", f"{got!r} {snap.preview!r}")

    # -- Typing does not get slower -------------------------------------------
    r.set_option("ascii_mode", True)
    words = [a + b + c for a in ("pre", "con", "dis", "un", "re", "in") for b in ("tra", "ver", "mon", "pla", "sti")
             for c in ("tion", "ment", "able", "ing", "ous", "er", "ly", "ness", "al", "ed")]
    times = []
    for w in words:
        t0 = time.perf_counter()
        r.type(w + " ")
        r.key("a"); r.clear()
        times.append((time.perf_counter() - t0) / (len(w) + 2))
    first, last = sum(times[:50]) / 50 * 1000, sum(times[-50:]) / 50 * 1000
    check("a key takes as long after 300 words as at the start", last < first * 2 + 1, f"{first:.2f} ms -> {last:.2f} ms")
    r.set_option("ascii_mode", False)

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
    print(f"{len(RESULTS) - failed}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
