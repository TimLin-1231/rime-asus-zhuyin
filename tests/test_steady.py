"""The inline text stays put while the next syllable is being typed
(lua/asus_lang.lua, filter steady; measured by tools/eval_flicker.py).

    python tests/test_steady.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from rime_harness import SHIFT_MASK, open_rime  # noqa: E402
from zhuyin import reading_keys  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
NO_LEARN = "patch:\n  asus_learn/enabled: false\n  translator/enable_user_dict: false\n"
TONES = " 6347"


def keys(text: str) -> str:
    return "".join(reading_keys(text)[0])


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    r = open_rime("steady", extra={"asus_zhuyin.custom.yaml": NO_LEARN})
    r.set_option("prediction", False)

    def typed(seq: str) -> list[tuple[str, str]]:
        """(key, inline text after it) for every key of seq."""
        r.clear(); r.key(">", SHIFT_MASK); r.clear()
        out = []
        for ch in seq:
            r.key(ch)
            out.append((ch, r.snapshot().preview))
        return out

    def jumps(steps: list[tuple[str, str]], only_mid: bool) -> list[str]:
        """Keys after which a character other than the last one shown changed."""
        out, prev = [], ""
        for ch, cur in steps:
            n = 0
            while n < min(len(prev), len(cur)) and prev[n] == cur[n]:
                n += 1
            if len(prev) - n >= 2 and not (only_mid and ch in TONES):
                out.append(f"{prev}->{cur}")
            prev = cur
        return out

    # The sentence that showed 外面最愛血液 for one key.
    steps = typed(keys("外面在下雨記得帶傘"))
    shown = [p for _, p in steps]
    check("外面在下雨: no key of 雨 rewrites 外面在下", "外面最愛血液" not in shown and "外面最愛下雨" not in shown,
          f"{[p for p in shown if '最愛' in p][:2]}")
    check("...and no key that finishes no syllable changes what is shown before the last character",
          not jumps(steps, True), f"{jumps(steps, True)[:3]}")
    check("the sentence is right in the end", shown[-1] == "外面在下雨記得帶傘", shown[-1])
    r.key("Return")
    check("...and committed so", "".join(r.committed) == "外面在下雨記得帶傘", "".join(r.committed))

    # The character just finished may still change with the next one.
    steps = typed(keys("你明天"))
    shown = [p for _, p in steps]
    check("你 + 明 + 天: 名 may become 明 when 天 is begun, and then stays",
          shown[-1] == "你明天" and not jumps(steps, True), f"{shown[-6:]}")

    # Finishing a syllable converts everything afresh.
    steps = typed(keys("我們週末一起去看電影"))
    check("the whole sentence is still converted again when a syllable is finished",
          steps[-1][1] == "我們週末一起去看電影", steps[-1][1])

    # Left alone: English, numbers, a list that is open, the editing cursor.
    steps = typed(keys("我用") + "python" + keys("跑實驗"))
    check("mixed input is right in the end", steps[-1][1] == "我用python跑實驗", steps[-1][1])
    steps = typed(keys("我") + "2026" + keys("年要出國"))
    check("numbers are right in the end", steps[-1][1] == "我2026年要出國", steps[-1][1])
    typed(keys("今天天氣很好") + "j")  # ㄨ of the next syllable
    r.key("BackSpace")
    check("BackSpace of the unfinished key brings back the text as it was", r.snapshot().preview == "今天天氣很好",
          r.snapshot().preview)
    r.key("Left"); s = r.snapshot()
    check("the editing cursor works on the same text", s.preview == "今天天氣很好" and "｜" in (s.candidates[0][1] if s.candidates else ""),
          f"{s.candidates[:1]}")
    r.clear()
    typed(keys("時間") + "j"); r.key("Down")
    check("Down still opens the full list", len(r.snapshot().candidates) > 1, f"{r.snapshot().candidates[:3]}")

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
