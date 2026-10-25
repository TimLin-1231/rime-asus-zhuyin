"""Stage 13 behaviour tests: English suggestions ordered by better word
frequencies, and by the previous English word.

    python tests/test_stage13.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from rime_harness import SHIFT_MASK, open_rime  # noqa: E402
from zhuyin import reading_keys  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
NO_LEARN = "patch:\n  asus_learn/enabled: false\n  translator/enable_user_dict: false\n"


def keys(text: str) -> str:
    return "".join(reading_keys(text)[0])


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    r = open_rime("stage13", extra={"asus_zhuyin.custom.yaml": NO_LEARN})
    r.set_option("prediction", False)

    def guesses(seq: str) -> list[str]:
        """Type seq in Chinese mode after a sentence end (Space commits an
        English word); the guesses for what is still being typed."""
        r.clear(); r.key(">", SHIFT_MASK); r.clear()
        r.type(seq)
        typed = seq.split(" ")[-1]
        g = [c for c, _ in r.snapshot().candidates if c != typed]
        r.clear()
        return g

    # Word frequencies.
    g = guesses("neur")
    check("neur: neural before neurone", g[:1] == ["neural"], f"{g}")
    g = guesses("optimi")
    check("optimi: optimization is offered, before optimise",
          "optimization" in g[:3] and ("optimise" not in g or g.index("optimization") < g.index("optimise")), f"{g}")
    g = guesses("occured")
    check("occured: occurred is the correction", g[:1] == ["occurred"], f"{g}")
    g = guesses("untill")
    check("untill: until is the first guess", g[:1] == ["until"], f"{g}")
    g = guesses("datase")
    check("a word only the old list has is still offered (dataset)", g[:1] == ["dataset"], f"{g}")
    g = guesses("pyth")
    check("pyth: python as before", g[:1] == ["python"], f"{g}")

    # The previous English word.
    for prev, typed, want in (("machine", "lea", "learning"), ("new", "yor", "york"), ("next", "wee", "week"),
                              ("open", "sou", "source"), ("high", "sch", "school"), ("looking", "for", "forward")):
        g = [x.lower() for x in guesses(f"{prev} {typed}")]
        check(f"after {prev}: {typed} -> {want}", g[:1] == [want], f"{g[:4]}")
    g = guesses("lea")
    check("without a previous word lea is not learning first", g[:1] != ["learning"], f"{g[:3]}")
    r.clear(); r.key(">", SHIFT_MASK); r.clear()
    r.type("machine lea"); r.key("Tab"); r.key("Tab")
    got = "".join(r.committed); r.clear()
    check("double Tab takes the guess that follows the previous word", got == "machine learning", got)
    # English mode.
    r.clear(); r.set_option("ascii_mode", True)
    r.type("machine lea"); r.key("Tab")
    got = "".join(r.committed); r.clear(); r.set_option("ascii_mode", False)
    check("English mode: the same", got == "machine learning", got)
    # After Chinese the previous word is forgotten.
    r.clear(); r.key(">", SHIFT_MASK); r.clear()
    r.type("machine "); r.type(keys("很好")); r.key("Return"); r.type("lea")
    g = [c for c, _ in r.snapshot().candidates if c != "lea"]; r.clear()
    check("after Chinese text the previous English word no longer counts", g[:1] != ["learning"], f"{g[:3]}")

    # Recognition is unchanged: these go by the old ranks.
    for seq, want in ((keys("幫我") + "check" + keys("一下這個數字"), "幫我check一下這個數字"),
                      (keys("我用") + "python" + keys("跑實驗"), "我用python跑實驗"),
                      (keys("今天天氣很好"), "今天天氣很好")):
        r.clear(); r.key(">", SHIFT_MASK); r.clear()
        r.type(seq); r.key("Return")
        got = "".join(r.committed); r.clear()
        check(f"still converts: {want}", got == want, got)

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
