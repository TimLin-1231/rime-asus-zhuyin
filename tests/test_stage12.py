"""Stage 12 behaviour tests: a digit key that is also a Zhuyin initial
(5 + ㄨㄢˋ: 5萬 or 賺?), decided by the language model, with the other reading
one pick away; candidates in Taiwan forms get the same treatment as others.

    python tests/test_stage12.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from rime_harness import SHIFT_MASK, open_rime  # noqa: E402
from zhuyin import reading_keys  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
# asus_learn stays on: it holds the email addresses.
PATCH = ("patch:\n  translator/enable_user_dict: false\n"
         '  asus_email/min_prefix: "4"\n')
EMAILS = "tim.lin@example.com\n"


def keys(text: str) -> str:
    return "".join(reading_keys(text)[0])


def mixed(line: str) -> str:
    return "".join(num if num else keys(zh) for zh, num in re.findall(r"([^\[\]]+)|\[([^\]]+)\]", line))


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    r = open_rime("stage12", extra={"asus_zhuyin.custom.yaml": PATCH, "emails.txt": EMAILS})
    r.set_option("prediction", False)

    def out(k: str) -> str:
        r.clear(); r.key(">", SHIFT_MASK); r.clear()
        for ch in k:
            r.key(ch, SHIFT_MASK if ch.isupper() else 0)
        r.key("Return")
        text = "".join(r.committed)
        r.clear()
        return text

    # The digit is a number.
    for line in ("他存了[2]萬元", "這輛車要[5]萬", "月薪[5]萬", "我們有[2]位客人", "今天來了[5]位同學",
                 "我吃了[2]碗飯", "預算是[1]億", "房租[15]萬", "共[12]位", "價格是[52]萬", "[5]萬元"):
        want = line.replace("[", "").replace("]", "")
        got = out(mixed(line))
        check(f"a number before a measure word: {want}", got == want, got)
    # The same keys are Chinese.
    for sent in ("特徵工程對結果影響很大", "他轉身離開","這一段寫得很好", "時間太短了", "我對這件事沒有意見",
                 "早餐店的蛋餅很好吃", "他的地位很高", "請轉告他"):
        got = out(keys(sent))
        check(f"Chinese stays Chinese: {sent}", got == sent, got)
    got = out(mixed("一年賺[25]萬"))
    check("賺 right before a number is not 5萬", got == "一年賺25萬", got)
    # While typing a Chinese sentence no digit shows up.
    sent = "早餐店的蛋餅很好吃"
    r.clear(); r.key(">", SHIFT_MASK); r.clear()
    leaks = []
    for ch in keys(sent):
        r.key(ch)
        pv = r.snapshot().preview
        if re.search(r"[0-9]", pv):
            leaks.append(pv)
    r.clear()
    check("no digit shows while typing 早餐店的蛋餅很好吃", not leaks, ", ".join(leaks[:3]))
    # The other reading is one pick away, marked.
    r.clear(); r.key(">", SHIFT_MASK); r.clear()
    r.type(mixed("月薪[5]萬")); r.key("Home"); r.key("Down")
    c = r.snapshot().candidates
    check("the number reading is marked 數字 and the Chinese one is second",
          len(c) > 1 and c[0][0].startswith("月薪5萬") and "5" not in c[1][0], f"{c[:3]}")
    r.key("2")
    if r.snapshot().composing:
        r.key("Return")
    got = "".join(r.committed); r.clear()
    check("picking the second gives the Chinese reading back", "5" not in got and got.startswith("月"), got)

    # Candidates in Taiwan forms (OpenCC: 爲 -> 為) are treated like the rest.
    got = out(mixed("我有[3]位客人"))
    check("the measure word after a number, where librime offers 為", got == "我有3位客人", got)
    sent = "因為我今天晚上要去吃飯"
    good = keys(sent)
    check(f"precondition: {sent}", out(good) == sent, out(good))
    bad = good.replace("rup ", "ru ", 1)  # 今 without ㄣ, in a sentence with 為
    check("a slip is corrected in a sentence with 為 too", out(bad) == sent, f"{out(bad)!r}")
    check("...and the correction is in Taiwan forms", "爲" not in out(bad), out(bad))

    # One shortest prefix for email suggestions (asus_email/min_prefix: 4 here).
    r.clear(); r.type("tim"); c3 = [x for x, _ in r.snapshot().candidates]; r.clear()
    r.type("tim."); c4 = [x for x, _ in r.snapshot().candidates] + [y for _, y in r.snapshot().candidates]; r.clear()
    check("email: not offered below min_prefix in the guesses either",
          "tim.lin@example.com" not in c3, f"{c3}")
    check("email: offered from min_prefix on", any("tim.lin@example.com" in x for x in c4), f"{c4}")

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
