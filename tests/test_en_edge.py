"""Where an English word inside Chinese ends, when one key could belong to
either side: tim + 改 or time + 矮, your + 有 or you + 酒 (lua/asus_lang.lua,
edge_shift: the language model reads the Chinese after it both ways).

    python tests/test_en_edge.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from rime_harness import SHIFT_MASK, open_rime  # noqa: E402
from zhuyin import reading_keys  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
NO_LEARN = "patch:\n  asus_learn/enabled: false\n  translator/enable_user_dict: false\n"


def keys(text: str) -> str:
    """Keys of a sentence; [x] is typed as it is."""
    return "".join(en or "".join(reading_keys(zh)[0]) for zh, en in re.findall(r"([^\[\]]+)|\[([^\]]+)\]", text))


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    r = open_rime("en-edge", extra={"asus_zhuyin.custom.yaml": NO_LEARN})
    r.set_option("prediction", False)

    def start(seq: str):
        r.clear(); r.key(">", SHIFT_MASK); r.clear()
        r.type(seq)
        return r.snapshot()

    def commit(text: str) -> str:
        start(keys(text))
        r.key("Return")
        got = "".join(r.committed); r.clear()
        return got

    def plain(text: str) -> str:
        return text.replace("[", "").replace("]", "")

    # The shorter word, though the longer one is more common.
    for text in ("叫[tim]改一下", "[tim]跟我說過", "這個[mod]改過了", "這個[pin]改掉了"):
        got = commit(text)
        check(f"the key after the word starts the next syllable: {plain(text)}", got == plain(text), got)
    # The longer word, though the shorter one is more common.
    for text in ("他說[your]有問題", "這些[files]有問題", "今天的[news]有看嗎", "我們的[server]一直當機",
                 "有些[users]要換密碼"):
        got = commit(text)
        check(f"the last letter belongs to the word: {plain(text)}", got == plain(text), got)
    # The other reading exists, but what the costs choose is right.
    for text in ("把[plan]給我看", "你[use]就知道了", "這個[new]你看過嗎", "一杯[tea]就夠了", "看[you]就知道",
                 "這個[file]你看一下"):
        got = commit(text)
        check(f"left alone: {plain(text)}", got == plain(text), got)
    # No second reading: nothing to decide.
    for text in ("我用[python]跑實驗", "幫我[check]一下這個數字", "這個[app]很好用"):
        got = commit(text)
        check(f"as before: {plain(text)}", got == plain(text), got)

    # Space after the word ends it there, whatever comes next.
    start(keys("他說") + "you ")
    r.type(keys("酒很好喝")); r.key("Return")
    got = "".join(r.committed); r.clear()
    check("Space after the word ends it there", got.replace(" ", "") == "他說you酒很好喝", got)
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
    raise SystemExit(main())
