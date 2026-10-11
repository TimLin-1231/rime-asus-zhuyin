"""Stage 11 behaviour tests (fixes after a review):

  * the word after a number is chosen by context, not forced (第2張照片,
    第2級警戒), and what the user picked there wins next time
  * English mode: capitals typed with separate Shift presses do not switch
    to Chinese mode
  * a first-tone syllable (ending in Space) can be inserted at the cursor

    python tests/test_stage11.py
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from rime_harness import RELEASE_MASK, SHIFT_MASK, open_rime  # noqa: E402
from zhuyin import reading_keys  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
# asus_learn stays on (the last checks need it); Rime's own learning is off.
PATCH = "patch:\n  translator/enable_user_dict: false\n"


def keys(text: str) -> str:
    return "".join(reading_keys(text)[0])


def mixed(line: str) -> str:
    """Keys for Chinese with [digits] typed as they are."""
    return "".join(num if num else keys(zh) for zh, num in re.findall(r"([^\[\]]+)|\[([^\]]+)\]", line))


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    r = open_rime("stage11", extra={"asus_zhuyin.custom.yaml": PATCH})
    r.set_option("prediction", False)

    def out(k: str) -> str:
        r.clear(); r.key(">", SHIFT_MASK); r.clear()
        for ch in k:
            r.key(ch, SHIFT_MASK if ch.isupper() else 0)
        r.key("Return")
        text = "".join(r.committed)
        r.clear()
        return text

    # -- the word after a number -------------------------------------------------
    for line in ("第[2]張照片", "第[1]張圖", "第[2]章寫完了", "第[2]級警戒", "震度[4]級", "看了[3]集",
                 "考[3]科", "[3]顆蘋果", "住[3]晚", "走[5]步", "有[3]部車", "[2]隻狗",
                 "這個要[100]元", "我[18]歲的時候", "[2]杯咖啡", "共[12]人參加", "[3]比[2]", "第[3]課"):
        want = line.replace("[", "").replace("]", "")
        got = out(mixed(line))
        check(f"after a number, by context: {want}", got == want, got)
    r.clear(); r.key(">", SHIFT_MASK); r.clear()
    r.type(mixed("第[3]章")); r.key("Down")
    c = [x for x, _ in r.snapshot().candidates]; r.clear()
    check("librime's own conversion stays in the list (張)", "張" in c[:4], f"{c[:5]}")

    # -- Shift in English mode -----------------------------------------------------
    def shift_letter(ch: str) -> None:
        r.key("Shift_L")
        time.sleep(0.02)
        r.key(ch, SHIFT_MASK)
        r.key(ch, SHIFT_MASK | RELEASE_MASK)
        r.key("Shift_L", SHIFT_MASK | RELEASE_MASK)

    r.clear(); r.set_option("ascii_mode", True)
    shift_letter("O"); time.sleep(0.03); shift_letter("K"); r.key("space")
    s = r.snapshot()
    check("English mode: O, K with separate Shift presses stays English",
          s.ascii_mode and "".join(r.committed) == "OK ", f"ascii={s.ascii_mode} {r.committed}")
    r.clear(); r.set_option("ascii_mode", True)
    r.type("ab"); r.tap("Shift_L"); r.key("c"); r.tap("Shift_L"); s = r.snapshot()
    check("English mode: Shift, a letter, Shift is not a double tap", s.ascii_mode, f"ascii={s.ascii_mode}")
    r.clear(); r.set_option("ascii_mode", True)
    r.tap("Shift_L"); r.tap("Shift_L"); s = r.snapshot()
    check("English mode: a real double tap still switches to Chinese", not s.ascii_mode)
    r.clear(); r.set_option("ascii_mode", False)
    r.tap("Shift_R"); r.tap("Shift_R"); s = r.snapshot()
    check("Chinese mode: a double tap switches to English", s.ascii_mode)
    r.clear(); r.set_option("ascii_mode", False)
    shift_letter("O"); time.sleep(0.03); shift_letter("K"); s = r.snapshot()
    check("Chinese mode: O, K with separate Shift presses stays Chinese mode",
          not s.ascii_mode and s.input == "OK", f"ascii={s.ascii_mode} {s.input!r}")
    r.clear()

    # -- inserting at the cursor ---------------------------------------------------
    for ins, name in (("吃", "first tone (Space)"), ("買", "third tone"), ("喝", "first tone (Space)")):
        r.clear(); r.key(">", SHIFT_MASK); r.clear()
        r.type(keys("我今天晚上要去飯"))
        r.key("Left")  # the cursor before 飯
        r.type(keys(ins)); s = r.snapshot(); r.key("Return")
        got = "".join(r.committed); r.clear()
        check(f"insert {ins} at the cursor, {name}", got == f"我今天晚上要去{ins}飯", f"{s.preview!r} -> {got!r}")
    r.clear(); r.key(">", SHIFT_MASK); r.clear()
    r.type(keys("我今天晚上要去飯")); r.key("Left"); r.type(keys("吃") + keys("早")); r.key("Return")
    got = "".join(r.committed); r.clear()
    check("two syllables inserted, the first with Space", got == "我今天晚上要去吃早飯", got)

    # -- what the user picked after a number wins (keep these last: they teach) --
    k = mixed("第[3]集")
    first = out(k)
    r.clear(); r.key(">", SHIFT_MASK); r.clear()
    r.type(k); r.key("Down")
    c = [x for x, _ in r.snapshot().candidates]
    if "集" in c:
        r.key(str(c.index("集") + 1))
        if r.snapshot().composing:
            r.key("Return")
    picked = "".join(r.committed); r.clear()
    check("precondition: 集 can be picked after 第3", picked == "第3集", f"first={first!r} list={c[:6]} -> {picked!r}")
    again = out(k)
    check("...and is what comes out the next time", again == "第3集", again)

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
