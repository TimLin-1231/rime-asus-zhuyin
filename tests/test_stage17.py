"""Stage 17 behaviour tests: misspelt English is corrected in Chinese mode too
- inside a sentence as soon as something is typed after it, and on Space
after the word that ends the input.

    python tests/test_stage17.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from rime_harness import SHIFT_MASK, open_rime  # noqa: E402
from zhuyin import reading_keys  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
NO_LEARN = "patch:\n  asus_learn/enabled: false\n  translator/enable_user_dict: false\n  asus_english/compose: true\n"
NAMED = ("Tab", "BackSpace", "Return", "Escape", "Down", "Left", "Right", "Home", "space")


def keys(text: str) -> str:
    return "".join(reading_keys(text)[0])


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    r = open_rime("stage17", extra={"asus_zhuyin.custom.yaml": NO_LEARN})

    def run(*seq: str):
        """Chinese mode from a clean state: named keys, or text typed as is."""
        r.set_option("ascii_mode", False)
        r.clear(); r.key(">", SHIFT_MASK); r.clear()
        handled = True
        for item in seq:
            if item in NAMED:
                handled = r.key(item)
            else:
                r.type(item)
        return "".join(r.committed), r.snapshot(), handled

    # -- inside a sentence -------------------------------------------------------
    got, snap, _ = run(keys("這個"), "enviroment", keys("有問題"))
    check("a misspelt word followed by Chinese is corrected inline",
          snap.preview == "這個environment有問題", snap.preview)
    r.key("Return")
    check("...and committed so", "".join(r.committed) == "這個environment有問題", "".join(r.committed))
    got, snap, _ = run(keys("我會"), "recieve")
    check("the last word of the input is not touched while it may still grow",
          snap.preview == "我會recieve" and ("receive", "⇥") in snap.candidates, f"{snap.preview} {snap.candidates}")
    r.key("Return")
    check("Enter commits it as typed", "".join(r.committed) == "我會recieve", "".join(r.committed))
    got, snap, _ = run(keys("我會"), "recieve", keys("你"))
    check("one character after it is enough", snap.preview == "我會receive你", snap.preview)
    r.key("BackSpace")
    shown = r.snapshot().preview
    r.key("Return")
    check("after deleting that character, what is shown is what Enter commits",
          shown in ("我會receive", "我會recieve") and "".join(r.committed) == shown, f"{shown} {r.committed}")
    got, snap, _ = run("becuase", keys("他沒來"))
    check("at the start of the sentence too", snap.preview == "because他沒來", snap.preview)
    got, snap, _ = run(keys("把"), "seperate", keys("跟"), "definately", keys("改掉"))
    check("two of them in one sentence", snap.preview == "把separate跟definitely改掉", snap.preview)
    for sentence, word in (("把{}改一下", "repo"), ("開{}模式", "async"), ("這個{}太小", "datasets"),
                           ("用{}裝套件", "conda"), ("我用{}跑實驗", "python"), ("我的{}筆電", "asus")):
        a, b = sentence.split("{}")
        got, snap, _ = run(keys(a), word, keys(b))
        check(f"{word} stays in {sentence.format(word)}", word in snap.preview, snap.preview)

    # -- taking it back ------------------------------------------------------------
    got, snap, _ = run(keys("這個"), "mesage", keys("要回嗎"), "Home", "Right", "Right", "Down")
    check("at the word, Down lists the text as it is, the correction, then what was typed",
          snap.candidates[1:3] == [("message", "更正"), ("mesage", "")], f"{snap.candidates[:3]}")
    r.key("3"); r.key("Escape"); r.key("Return")  # (the list moves on after a pick: close it)
    got = "".join(r.committed)
    check("picking what was typed keeps it", got == "這個mesage要回嗎", got)
    got, snap, _ = run(keys("這個"), "mesage", keys("要回嗎"))
    check("...and that word is left alone from then on", snap.preview == "這個mesage要回嗎", snap.preview)

    # -- Space after the word that ends the input ----------------------------------------
    got, snap, _ = run("adress ")
    check("a word alone, then Space: shown corrected, not committed yet",
          got == "" and snap.preview == "address " and snap.candidates[:1] == [("address ", "⌫adress")],
          f"{got!r} {snap.preview!r} {snap.candidates[:1]}")
    got, snap, _ = run("adress ", keys("是")[:1])
    check("the next key commits it", got == "address ", repr(got))
    got, snap, handled = run("adress ", "Return")
    check("Enter commits it and goes to the program", got == "address " and not handled, f"{got!r} {handled}")
    got, snap, _ = run("goverment ", "BackSpace")
    check("BackSpace takes it back", got == "" and snap.preview == "goverment", f"{got!r} {snap.preview!r}")
    r.key("space")
    check("...then Space commits what was typed", "".join(r.committed) == "goverment ", "".join(r.committed))
    got, snap, _ = run("goverment ")
    check("...and the word is left alone from then on", got == "goverment ", f"{got!r} {snap.preview!r}")
    got, snap, _ = run(keys("我用"), "wierd ")
    check("after Chinese in the same input: everything is shown, the word corrected",
          got == "" and snap.preview == "我用weird ", f"{got!r} {snap.preview!r}")
    r.key("Return")
    check("...and committed together", "".join(r.committed) == "我用weird ", "".join(r.committed))
    for word in ("python", "hello", "numpy", "docker", "form"):
        got, snap, _ = run(word + " ")
        check(f"{word} + Space is committed as typed", got == word + " ", f"{got!r} {snap.preview!r}")
    got, snap, _ = run(keys("你好"))
    check("Space as the first tone is untouched", snap.preview == "你好", snap.preview)

    # -- switched off ------------------------------------------------------------------
    r.set_option("asus_en_noauto", True)
    got, snap, _ = run(keys("這個"), "questoin", keys("很難"))
    check("asus_en_noauto: nothing is corrected inline", snap.preview == "這個questoin很難", snap.preview)
    got, snap, _ = run("questoin ")
    check("...nor on Space", got == "questoin ", repr(got))
    r.set_option("asus_en_noauto", False)

    # -- the rest is as before ------------------------------------------------------------
    for seq, want in ((keys("今天天氣很好"), "今天天氣很好"),
                      (keys("幫我") + "check" + keys("一下這個數字"), "幫我check一下這個數字"),
                      (keys("我用") + "python" + keys("跑") + "model", "我用python跑model")):
        got, snap, _ = run(seq, "Return")
        check(f"still converts: {want}", got == want, got)
    r.set_option("ascii_mode", True)
    r.clear()
    r.type("recieve ")
    snap = r.snapshot()
    check("English mode still corrects on Space", snap.preview == "receive ", snap.preview)
    r.key("BackSpace"); r.type(" ")
    check("...and takes it back", "".join(r.committed) == "recieve ", "".join(r.committed))
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
