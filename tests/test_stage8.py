"""Stage 8 behaviour tests: English that is more than letters (I'm, e-mail,
node.js, C++), email addresses, and English mode with suggestions.

    python tests/test_stage8.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from rime_harness import CONTROL_MASK, RELEASE_MASK, SHIFT_MASK, XK, open_rime  # noqa: E402
from zhuyin import reading_keys  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
XK["Delete"] = 0xFFFF
SHIFTED = set('~!@#$%^&*()_+{}|:"<>?') | set("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
EMAILS = "# my addresses\ntim.lin@example.com\ns108abc@test.org\n"


def keys(text: str) -> str:
    return "".join(reading_keys(text)[0])


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    r = open_rime("stage8", extra={"emails.txt": EMAILS})
    r.set_option("prediction", False)

    def press(name: str, mask: int = 0) -> bool:
        handled = r.key(name, mask)
        r.key(name, mask | RELEASE_MASK)
        return handled

    def type_text(s: str) -> None:
        for ch in s:
            press("space" if ch == " " else ch, SHIFT_MASK if ch in SHIFTED else 0)

    def chinese(seq: str) -> tuple[str, str]:
        """Type seq in Chinese mode, then Enter: (committed before Enter, all)."""
        r.clear(); r.set_option("ascii_mode", False)
        type_text(seq)
        before = "".join(r.committed)
        press("Return")
        out = "".join(r.committed)
        r.clear()
        return before, out

    def app(seq: str, direct: bool = False) -> str:
        """English mode: the text the program ends up with ({Tab} etc. are keys)."""
        r.clear(); r.set_option("ascii_mode", True); r.set_option("asus_en_direct", direct)
        out: list[str] = []
        for st in re.findall(r"\{[^}]+\}|.", seq):
            name = st[1:-1] if st.startswith("{") else ("space" if st == " " else st)
            mask = SHIFT_MASK if (len(name) == 1 and name in SHIFTED) else 0
            if name.startswith("C-"):
                name, mask = name[2:], CONTROL_MASK
            n = len(r.committed)
            handled = press(name, mask)
            out.extend(r.committed[n:])
            del r.committed[:]
            if not handled and not mask & CONTROL_MASK:
                if name == "BackSpace":
                    out = ["".join(out)[:-1]]
                else:
                    out.append({"space": " ", "Return": "\n", "Tab": "\t"}.get(name, name if len(name) == 1 else ""))
        r.set_option("asus_en_direct", False); r.set_option("ascii_mode", False)
        return "".join(out)

    # -- English inside Chinese mode ------------------------------------------
    # An apostrophe after a word used to send librime into an endless loop.
    for seq, want in (("I'm fine ", "I'm fine "), ("don't ", "don't "), ("it's ", "it's "),
                      ("e-mail ", "e-mail "), ("node.js ", "node.js "), ("C++ ", "C++ "),
                      ("Hello, world ", "Hello, world "), ("www.google.com", "www.google.com"),
                      ("test@gmail.com", "test@gmail.com"), ("iPhone 15 Pro ", "iPhone 15 Pro ")):
        before, out = chinese(seq)
        check(f"Chinese mode: {seq.strip()!r} stays English", out == want, f"{out!r}")
    r.clear(); type_text("dont"); c = [x for x, _ in r.snapshot().candidates]; r.clear()
    check("dont offers don't as the Tab guess", "don't" in c, f"{c}")
    sent = "我用python跑實驗"
    k = keys("我用") + "python" + keys("跑實驗")
    _, out = chinese(k)
    check("mixed Chinese/English still converts", out == sent, f"{out!r}")
    _, out = chinese(keys("今天天氣很好"))
    check("plain Chinese still converts", out == "今天天氣很好", f"{out!r}")
    # Capitals then a first-tone syllable: CPU + ㄨㄣ (jp, then Space) is not
    # one English word "CPUjp".
    _, out = chinese("CPU" + keys("溫度太高"))
    check("an acronym followed by Chinese: CPU溫度太高", out == "CPU溫度太高", f"{out!r}")
    _, out = chinese("GPUs ")
    check("a plural acronym stays English", out == "GPUs ", f"{out!r}")
    # Down at the end after an acronym used to loop forever (stage 6b).
    r.clear(); type_text("API" + keys("的用法")); press("Down")
    c = [x for x, _ in r.snapshot().candidates]; r.clear()
    check("Down after an acronym + Chinese lists the last word", bool(c) and c[0].endswith("用法"), f"{c[:3]}")

    # -- Enter in the expanded list picks the highlighted candidate -------------
    for word in ("你", "時間"):
        r.clear(); type_text(keys(word)); press("Down"); press("Down")
        want = r.snapshot().candidates[1][0]; press("Return")
        out = "".join(r.committed); r.clear()
        check(f"Down, Down, Enter commits the highlighted word ({want})", out == want, f"{out!r}")
    sent = "我今天很忙"
    r.clear(); type_text(keys(sent)); press("Home"); press("Right"); press("Down")
    c = [x for x, _ in r.snapshot().candidates]
    alt = next((x for x in c[1:] if x and x[0] != "今"), None)
    if alt:
        for _ in range(c.index(alt)):
            press("Down")
        press("Return")
    out = "".join(r.committed); s = r.snapshot(); r.clear()
    check("Enter in the list at the cursor picks the highlighted word and commits",
          alt is not None and out.startswith("我" + alt) and not s.composing, f"{alt!r} -> {out!r}")

    # -- Email addresses --------------------------------------------------------
    r.clear(); type_text("s108"); s = r.snapshot()
    check("first keys of a listed address: shown in the bubble",
          s.candidates and "⇥s108abc@test.org" in s.candidates[0][1], f"{s.candidates[:2]}")
    press("Tab"); s = r.snapshot()
    check("Tab types the whole address", "".join(r.committed) == "s108abc@test.org" and not s.composing,
          f"{r.committed} {s.input!r}")
    r.clear(); type_text("tim"); c = [x for x, _ in r.snapshot().candidates]; r.clear()
    check("an English-looking start lists the address as a guess", "tim.lin@example.com" in c, f"{c}")
    r.clear(); type_text("ab"); s = r.snapshot(); r.clear()
    check("two keys are not enough", not any("@" in x + y for x, y in s.candidates), f"{s.candidates[:2]}")
    # An address typed once is remembered.
    chinese("new.friend@mail.tw")
    r.clear(); type_text("new."); s = r.snapshot(); r.clear()
    check("an address committed once is offered next time",
          any("new.friend@mail.tw" in x + y for x, y in s.candidates), f"{s.candidates[:3]}")
    # Shift+Delete forgets it.
    r.clear(); type_text("new.friend@"); press("Down")
    c = [x for x, _ in r.snapshot().candidates]
    if "new.friend@mail.tw" in c:
        for _ in range(c.index("new.friend@mail.tw")):
            press("Down")
        press("Delete", SHIFT_MASK)
    r.clear(); type_text("new.f"); s = r.snapshot(); r.clear()
    check("Shift+Delete forgets an address",
          not any("new.friend@mail.tw" in x + y for x, y in s.candidates), f"{s.candidates[:3]}")

    # -- English mode -----------------------------------------------------------
    for seq, want, name in (
        ("hello world ", "hello world ", "words are typed as they are"),
        ("pyth{Tab}", "python", "Tab completes"),
        ("recieve{Tab}", "receive", "Tab fixes the spelling"),
        ("dont{Tab}", "don't", "Tab adds the apostrophe"),
        ("tim{Tab}", "tim.lin@example.com", "Tab types a remembered address"),
        ("I'm fine{Return}", "I'm fine\n", "Enter commits and still reaches the program"),
        ("x{Tab}", "x\t", "no guess: Tab reaches the program"),
        ("Hello, world.", "Hello, ", "punctuation commits and is typed"),
        ("abc{BackSpace}d ", "abd ", "BackSpace deletes the last typed letter"),
        ("{BackSpace}", "", "BackSpace with nothing typed reaches the program"),
        ("abc{C-a}", "abc", "a shortcut commits first"),
        ("{Tab}", "\t", "Tab alone reaches the program"),
    ):
        got = app(seq)
        check(f"English mode: {name}", got == want, f"{seq!r} -> {got!r}")
    r.clear(); r.set_option("ascii_mode", True); type_text("pyth"); press("Down"); press("2")
    out = "".join(r.committed); r.clear(); r.set_option("ascii_mode", False)
    check("English mode: Down then 2 picks the second word", out == "python", f"{out!r}")
    got = app("hello wor", direct=True)
    check("asus_en_direct: keys go straight to the program", got == "hello wor", f"{got!r}")
    r.clear(); r.set_option("ascii_mode", True); type_text("hello")
    r.tap("Shift_L"); r.tap("Shift_L"); s = r.snapshot()
    check("double Shift commits the English word, then Chinese mode",
          "".join(r.committed) == "hello" and not s.ascii_mode, f"{r.committed} ascii={s.ascii_mode}")
    r.clear(); r.set_option("ascii_mode", False)

    r.close()
    # A Lua runtime error makes a processor a silent no-op, so any fails the run.
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
