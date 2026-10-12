"""Tests of the writing helper (assist/) that need no window and no keyboard:
the pieces, Harper on real sentences, and (with --llm) the local model.

    python tests/test_assist.py          # about 5 s
    python tests/test_assist.py --llm    # also loads the model (about 15 s)

The parts that act on other programs (hotkey, copy, paste) are tried by
tools/assist_test.py in a real Notepad.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from assist import harper, llm  # noqa: E402
from assist.app import DEFAULTS, changed_spans, parse_hotkey  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    # -- small pieces ----------------------------------------------------------
    check("hotkey ctrl+alt+g", parse_hotkey("ctrl+alt+g") == (0x3, ord("G")), f"{parse_hotkey('ctrl+alt+g')}")
    check("hotkey Ctrl + Shift + F9", parse_hotkey("Ctrl + Shift + F9") == (0x6, 0x78), f"{parse_hotkey('Ctrl + Shift + F9')}")
    for bad in ("g", "ctrl+alt", "ctrl+banana"):
        try:
            parse_hotkey(bad)
            check(f"hotkey {bad!r} is refused", False, "accepted")
        except ValueError:
            check(f"hotkey {bad!r} is refused", True)
    check("the default hotkey parses", parse_hotkey(DEFAULTS["hotkey"])[1] != 0)

    old, new = "I look forward to hear from you.", "I look forward to hearing from you."
    spans = changed_spans(old, new)
    check("changed words are marked", [new[a:b] for a, b in spans] == ["hearing"], f"{spans}")
    check("nothing is marked in the same text", changed_spans(old, old) == [])
    new = "She told me that she was busy."
    marked = [new[a:b] for a, b in changed_spans("She said me that she was busy.", new)]
    check("a replaced word is marked alone", marked == ["told"], f"{marked}")
    marked = ["我明天會 receive 它"[a:b] for a, b in changed_spans("我明天會 recieve 它", "我明天會 receive 它")]
    check("Chinese around a change is not marked", marked == ["receive"], f"{marked}")

    F = harper.Finding
    text = "He is a honest man and and he go ."
    found = [F(6, 7, "a", "", "AnA", "", ["an"]), F(19, 26, "and and", "", "Rep", "", ["and"]),
             F(22, 26, " and", "", "Overlap", "", [""]), F(32, 33, " ", "", "Comma", "", [""]),
             F(27, 29, "he", "", "NoFix", "", [])]
    got = harper.apply(text, sorted(found, key=lambda f: (f.start, f.end)))
    check("suggestions are applied, overlapping ones once", got == "He is an honest man and he go.", got)
    check("Replace with", harper._replacement("Replace with: “an”", "a") == "an")
    check("Remove error", harper._replacement("Remove error", "x") == "")
    check("Insert", harper._replacement("Insert “,”", "x") == "x,")
    check("an unknown suggestion is skipped", harper._replacement("Do something", "x") is None)

    check("answer keeps the outer white space", llm._shaped("  hello wrold \n", "hello world") == "  hello world \n")
    check("added quotation marks are dropped", llm._shaped("hello wrold", '"hello world"') == "hello world")
    check("quotation marks of the original are kept", llm._shaped('"hello wrold"', '"hello world"') == '"hello world"')
    check("an empty answer leaves the text", llm._shaped("hello", "  ") == "hello")
    check("a runaway answer leaves the text", llm._shaped("hello", "Sure! " * 40) == "hello")
    check("longer text gets more tokens", llm._budget("a" * 1000) > llm._budget("a" * 10) >= 48)

    # -- Harper ------------------------------------------------------------------
    if harper.available():
        text = "I will recieve an reply tomorrow."
        found = harper.lint(text)
        check("Harper finds the misspelling and the article",
              {f.text for f in found} >= {"recieve", "an"}, f"{[(f.text, f.replacements[:1]) for f in found]}")
        check("offsets point at the text", all(text[f.start:f.end] == f.text for f in found))
        check("applying them corrects the sentence", harper.apply(text, found) == "I will receive a reply tomorrow.",
              harper.apply(text, found))
        text = "我明天會 recieve 那個 package，then I will forward it to you。"
        found = harper.lint(text)
        check("offsets are right after Chinese text", [f.text for f in found] == ["recieve"], f"{[f.text for f in found]}")
        check("a correct sentence has no findings", harper.lint("Could you send me the updated slides by Friday?") == [])
        check("empty text is not sent", harper.lint("   ") == [])
    else:
        print("(Harper is not installed: python tools/fetch_assist.py harper)")

    # -- the model ---------------------------------------------------------------
    if "--llm" in sys.argv and llm.available():
        model = llm.Model(port=8729)
        try:
            got = model.fix("I look forward to hear from you.")
            check("the model corrects a sentence", got == "I look forward to hearing from you.", got)
            got = model.fix("Thanks for your quick reply.")
            check("...and leaves a correct one", got == "Thanks for your quick reply.", got)
            got = model.fix("  我明天會 recieve 那個 package。\n")
            check("Chinese and outer white space survive", got == "  我明天會 receive 那個 package。\n", repr(got))
            got = model.rewrite("Can you send me the file?", "formal")
            check("a rewrite is English text of similar length", 10 < len(got) < 120 and got.isascii(), got)
            got = model.continue_text("Thank you for your email. I will")
            check("a continuation is short and does not repeat the text",
                  0 < len(got) < 200 and "Thank you for your email" not in got, got)
            check("the server listens on this machine only", model.url.startswith("http://127.0.0.1:"))
        finally:
            model.stop()
        check("the server is stopped", not model.running())

    width = max(len(n) for n, _, _ in RESULTS)
    failed = 0
    for name, ok, detail in RESULTS:
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {'' if ok else detail}")
    print(f"{len(RESULTS) - failed}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
