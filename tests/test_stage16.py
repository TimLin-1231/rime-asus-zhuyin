"""Stage 16 behaviour tests: after a pause, English mode offers a phrase from
the writing helper's model (assist/bridge.py), Tab types it.

The real bridge runs inside this process on pipes of its own name, with a
stand-in for the model; the F24 key it would send is pressed by the test.

    python tests/test_stage16.py
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
from rime_harness import SHIFT_MASK, open_rime  # noqa: E402
from assist.bridge import Bridge  # noqa: E402
from assist.llm import _phrase  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
PIPE = "asus_zhuyin_test16"
PATCH = ("patch:\n  asus_learn/enabled: false\n  translator/enable_user_dict: false\n"
         f"  asus_english/pipe: {PIPE}\n"
         "  asus_english/compose: true\n")
NAMED = ("Tab", "BackSpace", "Return", "Escape", "Down", "Left", "F24")
PAUSE = 0.15          # the bridge's pause (seconds) in this test
TYPE_ON_PAUSE = 2.0   # ...for the "typing on within the pause" check
WAIT = 5.0            # longest wait for something the bridge does


def wait_for(cond, timeout: float = WAIT) -> bool:
    """Poll cond until it is true or timeout seconds have passed."""
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


class FakeModel:
    """Answers like the model would, and remembers what it was asked."""

    def __init__(self) -> None:
        self.seen: list[str] = []
        self.answers = {"thank you": "very much for your help.", "i look forward to": "hearing from you",
                        "very much for your help.": "", "see you": "tomorrow"}

    def next_words(self, text: str) -> str:
        self.seen.append(text)
        return self.answers.get(text.strip().lower(), "indeed")


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    model, notified = FakeModel(), []
    bridge = Bridge(model, threading.Lock(), pause=PAUSE, name=PIPE, notify=notified.append)
    r = open_rime("stage16", extra={"asus_zhuyin.custom.yaml": PATCH})

    def run(*seq: str):
        """English mode from a clean state; "~" waits for the bridge's pause
        and answer. Returns (committed, snapshot)."""
        r.set_option("ascii_mode", False)
        r.clear(); r.key(">", SHIFT_MASK); r.clear()   # a key in Chinese mode: the typed text is forgotten
        r.set_option("ascii_mode", True)
        for item in seq:
            if item == "~":
                # (A generous deadline: on a busy machine the answer comes late,
                # and the loop ends as soon as it is there.)
                before, deadline = bridge.answered, time.monotonic() + WAIT
                while bridge.answered == before and time.monotonic() < deadline:
                    time.sleep(0.01)
                time.sleep(0.02)
            elif item in NAMED:
                r.key(item)
            else:
                r.type(item)
        return "".join(r.committed), r.snapshot()

    def offered(snap) -> list[str]:
        return [comment for text, comment in snap.candidates if text == ""]

    # -- the request ---------------------------------------------------------------
    got, snap = run("thank you ", "~")
    check("after a pause the model is asked with the text typed so far", model.seen[-1:] == ["thank you "], f"{model.seen}")
    check("the helper is told which window to notify, once", len(notified) == 1, f"{notified}")
    check("until F24 the bubble shows the single words", offered(snap)[:1] != ["very much for your help."] and len(offered(snap)) > 0,
          f"{snap.candidates}")
    # -- the answer ----------------------------------------------------------------
    r.key("F24")
    snap = r.snapshot()
    check("F24: the phrase is offered first", offered(snap)[:1] == ["very much for your help."], f"{snap.candidates}")
    check("...the single words stay behind it", len(offered(snap)) > 1, f"{snap.candidates}")
    check("...nothing is typed or shown inline", "".join(r.committed) == "thank you " and snap.preview == "", f"{r.committed} {snap.preview!r}")
    r.key("Tab")
    got = "".join(r.committed)
    check("Tab types the phrase and a space", got == "thank you very much for your help. ", repr(got))

    got, snap = run("I look forward to ", "~", "F24", "Tab")
    check("a phrase in mid-sentence", got == "I look forward to hearing from you ", repr(got))
    check("the whole text so far was sent", model.seen[-2:-1] == ["I look forward to "] or "I look forward to " in model.seen, f"{model.seen[-3:]}")
    wait_for(lambda: model.seen[-1] == "I look forward to hearing from you ")
    check("after taking a phrase the next one is asked for with it included",
          model.seen[-1] == "I look forward to hearing from you ", f"{model.seen[-2:]}")

    # -- typing on -----------------------------------------------------------------
    # The bridge's pause is wall-clock time: on a busy machine the key typed
    # after the request could come later than the 0.15 s pause, and then the
    # model is asked rightly (test_stage16 failed 3 times in 11 that way). For
    # this check the pause is TYPE_ON_PAUSE, and "v" is typed only once the
    # request has reached the bridge (its pipes are served by several threads,
    # so two quick messages could otherwise arrive the other way round). Then
    # the test waits past the pause: a bridge that asked the model anyway
    # (no debounce) is seen.
    asked, requests = len(model.seen), bridge.asked
    bridge.pause = TYPE_ON_PAUSE
    try:
        run("thank you ")
        arrived = wait_for(lambda: bridge.asked > requests)
        r.type("v")
        cancelled = wait_for(lambda: bridge.request is None)  # the "typed on" message
        time.sleep(TYPE_ON_PAUSE + 0.5)
    finally:
        bridge.pause = PAUSE
    check("typing on within the pause: the model is not asked",
          arrived and cancelled and len(model.seen) == asked,
          f"request arrived={arrived} cancelled={cancelled} asked={model.seen[asked:]}")
    r.key("F24")
    snap = r.snapshot()
    check("...and a late F24 changes nothing", snap.input == "v" and "".join(r.committed) == "thank you ", f"{snap.input!r} {r.committed}")

    got, snap = run("thank you ", "~", "very ", "F24")
    check("an answer to an older request is not shown", "very much for your help." not in offered(snap), f"{snap.candidates}")

    got, snap = run("thank you ", "~", "F24", "s")
    check("a letter dismisses the phrase and starts a word", got == "thank you " and snap.input == "s", f"{got!r} {snap.input!r}")

    # -- what counts as the text typed so far ------------------------------------------
    run("hello there", "Return", "see you ", "~")
    check("Enter starts the text anew", model.seen[-1] == "see you ", f"{model.seen[-1]!r}")
    run("hello there ", "Left", "see you ", "~")
    check("an arrow key too (the caret moved)", model.seen[-1] == "see you ", f"{model.seen[-1]!r}")
    run("see yoo", "BackSpace", "u ", "~")
    check("a deleted letter is not sent", model.seen[-1] == "see you ", f"{model.seen[-1]!r}")
    run("well, see you ", "~")
    check("punctuation typed between words is part of the text", model.seen[-1] == "well, see you ", f"{model.seen[-1]!r}")
    run("see ", "BackSpace", " you ", "~")
    check("BackSpace in the program takes a character off", model.seen[-1] == "see you ", f"{model.seen[-1]!r}")
    run("recieve it ", "~")
    check("a corrected word is sent as corrected", model.seen[-1] == "receive it ", f"{model.seen[-1]!r}")
    asked = len(model.seen)
    run("hello ")
    time.sleep(0.4)
    check("one word alone is not sent", len(model.seen) == asked, f"{model.seen[asked:]}")
    r.set_option("asus_en_noauto", True)
    run("thank you ")
    time.sleep(0.4)
    check("asus_en_noauto: nothing is sent", len(model.seen) == asked, f"{model.seen[asked:]}")
    r.set_option("asus_en_noauto", False)

    # -- F24 is never for the program ---------------------------------------------------
    r.set_option("ascii_mode", False)
    r.clear()
    handled = r.key("F24")
    check("Chinese mode: F24 is swallowed too", handled and "".join(r.committed) == "", f"{handled} {r.committed}")
    r.set_option("ascii_mode", True)
    r.clear()
    check("English mode with nothing waiting: F24 is swallowed", r.key("F24") and not r.snapshot().composing)

    # -- the phrase the model's text is cut down to ---------------------------------------
    check("a phrase ends with the sentence", _phrase(" hearing from you. I will also") == "hearing from you.")
    check("...or after six words", _phrase(" one two three four five six seven") == "one two three four five six")
    check("...and is one line", _phrase(" first line\nsecond") == "first line")
    check("nothing gives nothing", _phrase("  \n") == "")

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
