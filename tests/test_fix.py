"""AI correction of the composition, Ctrl+R (rime/lua/asus_fix.lua).

    python tests/test_fix.py

The front end's part (asking the model) is played here: the test reads the
request property, answers through asus_fix_result and presses F23, as
DaoHui for macOS does. No model is needed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from rime_harness import CONTROL_MASK, open_rime  # noqa: E402
from zhuyin import reading_keys  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def keys_for(text: str) -> str:
    return "".join(reading_keys(text)[0])


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    r = open_rime("fix", extra={"asus_zhuyin.custom.yaml": (
        "patch:\n  asus_learn/enabled: false\n  translator/enable_user_dict: false\n")})

    def start(text: str, support: bool = True):
        """Type text's keys, press Ctrl+R; (handled, id, body, shown)."""
        r.clear()
        r.set_option("_asus_fix", support)
        r.type(keys_for(text))
        shown = r.snapshot().preview
        handled = r.key("r", CONTROL_MASK)
        req = r.get_property("asus_fix_request")
        rid, _, body = req.partition("\t")
        return handled, rid, body, shown

    def answer(rid: str, text: str):
        r.set_property("asus_fix_request", "")  # taken by the front end
        r.set_property("asus_fix_result", f"{rid}\t{text}")
        r.key("F23")
        return r.snapshot()

    # The IME shows 應用城市 for these keys (a dev sentence).
    want = "請重新啟動應用程式"
    handled, rid, body, shown = start(want)
    check("the IME's own text has the wrong homophone", shown != want, repr(shown))
    check("Ctrl+R is taken while composing", handled)
    check("a request with an id", rid != "" and body != "", repr(rid))
    try:
        req = json.loads(body)
        last = req["messages"][-1]["content"]
        ok = (req["messages"][0]["role"] == "system" and len(req["messages"]) == 12
              and last.startswith("注音：ㄑㄧㄥˇ ㄔㄨㄥˊ") and last.endswith("輸入法：" + shown))
        check("the body is a chat request with the Zhuyin and the IME's text", ok, last)
    except (ValueError, KeyError) as e:
        check("the body is a chat request with the Zhuyin and the IME's text", False, f"{e}: {body[:200]}")
    s = r.snapshot()
    check("the bubble says AI 修正中…", s.candidates and "AI 修正中" in s.candidates[0][1], f"{s.candidates[:1]}")
    check("a second Ctrl+R while waiting asks nothing new",
          r.key("r", CONTROL_MASK) and r.get_property("asus_fix_request").startswith(rid))
    s = answer(rid, want)
    check("the correction is shown inline", s.preview == want, repr(s.preview))
    check("first row: the correction, with what changed", s.candidates and s.candidates[0][0] == want
          and "城市→程式" in s.candidates[0][1], f"{s.candidates[:2]}")
    check("second row: the original", len(s.candidates) > 1 and s.candidates[1][0] == shown, f"{s.candidates[:2]}")
    r.key("Return")
    check("Enter commits the correction", "".join(r.committed) == want, repr(r.committed))
    check("and the input is empty (a prediction may follow)", r.snapshot().input == "")

    _, rid, _, shown = start(want)
    answer(rid, want)
    r.key("Escape")
    s = r.snapshot()
    check("Esc goes back to the original, still composing", s.preview == shown and s.composing, repr(s.preview))
    r.key("Return")
    check("then Enter commits the original", "".join(r.committed) == shown, repr(r.committed))

    _, rid, _, shown = start(want)
    answer(rid, want)
    before = r.snapshot().input
    r.key("BackSpace")
    s = r.snapshot()
    check("BackSpace leaves the correction and deletes from the original",
          len(s.input) < len(before) and want not in s.preview, f"{s.input!r} {s.preview!r}")

    _, rid, _, shown = start(want)
    r.type("u4")  # typed on before the answer came
    s = answer(rid, want)
    check("an answer for an input typed on since is dropped", s.preview != want, repr(s.preview))

    # The guard (as measured in tools/eval_fix.py).
    for text, wrong, why in (("他已經出門了", "她已經出門了", "他 -> 她"),
                             ("資料前處理花了很多時間", "資料前處理花了很長時間", "a rewording that reads differently"),
                             ("我晚一點再回覆你", "我晚一點回覆你", "a dropped word")):
        _, rid, _, shown = start(text)
        s = answer(rid, wrong)
        check(f"rejected: {why}", s.preview == shown and any("沒有要改的" in c for _, c in s.candidates[:1]),
              f"{s.preview!r} {s.candidates[:1]}")
    r.clear()
    r.set_option("_asus_fix", True)
    r.type("rup wu0 y94ru0 " + "10" + "2u03")  # 今天在家 10點
    shown = r.snapshot().preview
    r.key("r", CONTROL_MASK)
    rid = r.get_property("asus_fix_request").partition("\t")[0]
    s = answer(rid, shown.replace("10", "十"))
    check("rejected: a number turned into 十", s.preview == shown, f"{shown!r} -> {s.preview!r}")

    # A stray key (n in ㄔㄨㄙㄥˊ): asus_typo answers for the segment too.
    r.clear()
    r.set_option("_asus_fix", True)
    r.type("fu/3tjn/6vup fu32j/4u/4m/4t/6g4")
    r.key("r", CONTROL_MASK)
    rid = r.get_property("asus_fix_request").partition("\t")[0]
    s = answer(rid, want)
    check("with a slipped key: the correction still comes first", s.preview == want and s.candidates
          and s.candidates[0][0] == want, f"{s.preview!r} {s.candidates[:2]}")

    # The IME writes 預期得 here; asus_grammar must not turn the correction back.
    long_want = "我覺得這個實驗的結果跟我們預期的不太一樣所以要重新設計"
    _, rid, _, shown = start(long_want)
    s = answer(rid, long_want)
    check("a 的 the 的/得 rules would undo is shown as answered", shown != long_want and s.preview == long_want,
          f"{shown!r} -> {s.preview!r}")
    r.key("Return")
    check("and Enter commits it as shown", "".join(r.committed) == long_want, repr(r.committed))

    _, rid, _, shown = start("我們比較了三種不同的方法")
    s = answer(rid, "我們比較了三种不同的方法")
    check("rejected: a simplified character (種 -> 种)", s.preview == shown, repr(s.preview))

    _, rid, _, shown = start(want)
    s = answer(rid, "!需要先下載 AI 模型")
    check("a front-end message is shown", s.preview == shown and "需要先下載" in s.candidates[0][1],
          f"{s.candidates[:1]}")
    r.type("u")
    s = r.snapshot()
    check("and gone with the next key", not any("需要先下載" in c for _, c in s.candidates), f"{s.candidates[:1]}")

    handled, rid, body, shown = start(want, support=False)
    check("without a front end that can ask, Ctrl+R is not taken", not handled and rid == "", repr(rid))
    r.clear()
    r.set_option("_asus_fix", True)
    check("Ctrl+R with nothing typed is not taken", not r.key("r", CONTROL_MASK))

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
    print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
