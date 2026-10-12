"""Short English words typed in lowercase inside Chinese (app, doc, how) are
English, not a toneless syllable and a slip (lua/asus_lang.lua, C_AGAIN).

    python tests/test_en_short.py
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
    r = open_rime("en-short", extra={"asus_zhuyin.custom.yaml": NO_LEARN})
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

    # -- English ---------------------------------------------------------------
    for text, want in (("這個[app]很好用", "這個app很好用"), ("把[doc]傳給我", "把doc傳給我"),
                       ("我不知道[how]做", "我不知道how做"), ("這個[api]壞了", "這個API壞了"),
                       ("這個[job]跑了很久", "這個job跑了很久"), ("我家的[dog]很可愛", "我家的dog很可愛")):
        got = commit(text)
        check(f"in a sentence: {want}", got == want, got)
    got = commit("[cpu]太熱了")
    check("at the start: CPU太熱了", got == "CPU太熱了", got)
    for text, want in (("我想下載這個[app]", "我想下載這個app"), ("我也不知道[how]", "我也不知道how")):
        got = commit(text)
        check(f"at the end: {want}", got == want, got)
    for text, want in (("記憶體[full]了", "記憶體full了"), ("我們的[goal]是什麼", "我們的goal是什麼")):
        got = commit(text)
        check(f"two toneless syllables: {want}", got == want, got)
    snap = start(keys("這個") + "ap")
    check("while typing, two letters are still an unfinished syllable",
          not re.search("[a-z]", snap.preview), snap.preview)
    snap = start(keys("這個") + "app")
    check("...and the third letter shows the word", snap.preview == "這個app", snap.preview)

    # -- Chinese stays Chinese ---------------------------------------------------
    # A first tone typed without Space; the keys start an English word.
    for text, want in (("有[ru]會", "有機會"), ("我的[ru]車", "我的機車"), ("我[ek]哥", "我哥哥"),
                       ("[co]色的", "黑色的")):
        got = commit(text)
        check(f"one missing tone key: {want}", got == want, got)
    # 您 passes through "sup": one unfinished syllable is not a slip.
    snap = start(keys("謝謝") + "sup")
    check("an unfinished syllable that is also a word stays Zhuyin",
          not re.search("[a-z]", snap.preview), snap.preview)
    # No tone keys at all is a way of typing: nothing changes there.
    toneless = re.sub("[ 6347]", "", keys("今天天氣很好我們去公園"))
    snap = start(toneless)
    check("a sentence typed without any tone key has no English in it",
          not re.search("[A-Za-z]", snap.preview), snap.preview)

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
