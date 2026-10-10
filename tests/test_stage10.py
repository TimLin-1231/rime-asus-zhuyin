"""Stage 10 behaviour tests: slips of the finger beyond a neighbouring key
(a key not pressed, two keys in the wrong order, an unusual tone), and the
things they must not break (numbers, English).

    python tests/test_stage10.py
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
    r = open_rime("stage10", extra={"asus_zhuyin.custom.yaml": NO_LEARN})
    r.set_option("prediction", False)

    def out(k: str) -> str:
        """Type k after a sentence end, then Enter: everything committed."""
        r.clear(); r.key(">", SHIFT_MASK); r.clear()
        for ch in k:
            r.key(ch, SHIFT_MASK if ch.isupper() else 0)
        r.key("Return")
        text = "".join(r.committed)
        r.clear()
        return text

    def slip(sent: str, old: str, new: str, name: str) -> None:
        good = keys(sent)
        assert old in good, (sent, old, good)
        bad = good.replace(old, new, 1)
        check(f"precondition: {sent}", out(good) == sent, out(good))
        check(f"{name}: {sent}", out(bad) == sent, f"{bad!r} -> {out(bad)!r}")

    # A key not pressed.
    slip("我有點感冒想早點睡", "vu;3", "v;3", "a key left out, no syllable left (ㄒㄤˇ)")
    slip("我今天晚上要去吃飯", "rup ", "ru ", "a key left out, still a syllable (ㄐㄧ)")
    slip("這部電影真的很感人", "2k7", "k7", "the first key left out (ㄜ˙)")
    # Two keys in the wrong order.
    slip("不好意思我遲到了", "2l4xk7", "2lx4k7", "the next syllable's first key before the tone key")
    slip("我們約在捷運站見面", "au04", "au40", "the tone key before the last key, at the end")
    slip("這部電影真的很感人", "cp3", "c3p", "the tone key before the last key, inside")
    slip("請先閱讀相關的研究", "m,4", ",m4", "two keys of one syllable swapped")
    # A neighbouring key where the syllable keeps a valid body but no such tone.
    slip("我正在排隊等公車", "y94", "y84", "a neighbouring key, unusual tone left (ㄗㄚˋ)")
    slip("我現在在公司加班", "n ", "b ", "a neighbouring key, lone initial left (ㄖ + first tone)")
    # First-tone syllables one key off look like English words until Space.
    slip("老師說期末考會比較難", "gji ", "gui ", "Space after gui is the first tone, not English")
    slip("請在下週之前交報告", "rul ", "ril ", "Space after ril is the first tone, not English")

    # What must stay.
    for seq, want in (("ej/413bp6", "共13人"), ("u.316ek7", "有16個"),
                      ("w8 g48m,4", "他是8月"), ("2026su0610m,410b4", "2026年10月10日"),
                      ("ji3u.33ek7", "我有3個"), ("5k4g42u412u03", "這是第1點"),
                      ("w8 g49", "他是9"), ("ji3u.38", "我有8")):
        got = out(seq)
        check(f"a number is never taken for a slip: {want}", got == want, f"{seq!r} -> {got!r}")
    for seq, want in (("GUI ", "GUI "), ("the ", "the "), ("python ", "python "),
                      (keys("我用") + "python" + keys("跑實驗"), "我用python跑實驗")):
        got = out(seq)
        check(f"English stays English: {want.strip()}", got == want, f"{got!r}")
    r.clear(); r.key(">", SHIFT_MASK); r.clear()
    r.type("hello "); r.type("gui "); got = "".join(r.committed); r.clear()
    check("after an English word, gui + Space stays English", got == "hello GUI ", f"{got!r}")
    for sent in ("今天天氣很好適合出去走走", "我把檔案寄給你了", "這個問題我想了很久"):
        got = out(keys(sent))
        check(f"correct input stays: {sent}", got == sent, got)

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
