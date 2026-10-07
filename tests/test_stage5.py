"""Stage 5 behaviour tests: learning from corrections, English words and next
words; forgetting with Shift/Ctrl+Delete; decay; persistence.

    python tests/test_stage5.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from eval_learn import Typist  # noqa: E402
from rime_harness import CONTROL_MASK, SHIFT_MASK, XK, open_rime  # noqa: E402
from zhuyin import reading_keys  # noqa: E402

XK["Delete"] = 0xFFFF
RESULTS: list[tuple[str, bool, str]] = []


def keys(text: str) -> str:
    return "".join(reading_keys(text)[0])


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def decay_case() -> None:
    """Run in a subprocess: weights halve every 40 seconds here."""
    extra = {"asus_zhuyin.custom.yaml": "patch:\n  asus_learn/half_life_days: 0.000463\n"}
    r = open_rime("stage5-decay", extra=extra)
    t = Typist(r, SHIFT_MASK, CONTROL_MASK)
    t.enter(keys("我把程式碼上傳到伺服器了"), "我把程式碼上傳到伺服器了")
    fresh = t.preview(keys("請重新啟動應用程式"))
    time.sleep(40)  # one half-life: below the 0.9 a correction needs to apply everywhere
    old = t.preview(keys("請重新啟動應用程式"))
    r.close()
    print(f"{fresh}\t{old}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if sys.argv[1:] == ["--decay"]:
        decay_case()
        return 0
    r = open_rime("stage5")
    t = Typist(r, SHIFT_MASK, CONTROL_MASK)
    r.set_option("prediction", False)

    # Corrections ----------------------------------------------------------
    app, teach = "請重新啟動應用程式", "我把程式碼上傳到伺服器了"
    before = t.preview(keys(app))
    check("precondition: 應用程式 converts wrongly before learning", before != app, before)
    got, picks = t.enter(keys(teach), teach)
    check("teaching: corrected sentence is committed", got == teach and picks > 0,
          f"got={got!r} picks={picks}")
    after = t.preview(keys(app))
    check("one correction generalises to a new sentence", after == app, after)

    t.reset_context(); t.type(keys(app)); r.key("Down"); s = r.snapshot(); r.clear()
    check("the unrewritten conversion stays one pick away",
          len(s.candidates) > 1 and s.candidates[1][0] == before, f"{s.candidates[:2]}")

    # Undo: pick the old conversion back once.
    city = "這個城市很漂亮"
    shown = t.preview(keys(city))
    check("(known cost) the rival word is rewritten until corrected once", shown != city, shown)
    t.enter(keys(city), city)
    check("after one correction back, the rival sentence is right",
          t.preview(keys(city)) == city, t.preview(keys(city)))
    check("the taught context still gets the learned word",
          t.preview(keys("我把程式碼寄給你")) == "我把程式碼寄給你", t.preview(keys("我把程式碼寄給你")))

    # 在/再: context only.
    t.enter(keys("我在改程式"), "我在改程式")
    for text in ("等一下我再說", "我等一下再改"):
        check(f"在 learned in one context does not leak: {text}",
              t.preview(keys(text)) == text, t.preview(keys(text)))

    # Forgetting ------------------------------------------------------------
    name = "我打給志豪"
    t.enter(keys(name), name)
    probe = "志豪今天沒來上課"
    check("a name picked character by character is learned", t.preview(keys(probe)) == probe,
          t.preview(keys(probe)))
    # (Rime itself learned 志豪 from the two picks; the rewrite below is ours.)
    axis = "請把圖表的座標軸標清楚"
    t.enter(keys(axis), axis)
    probe = "這個點的座標是多少"
    check("a corrected word is used in a new sentence (座標)", t.preview(keys(probe)) == probe,
          t.preview(keys(probe)))
    t.reset_context(); t.type(keys(probe))
    handled = r.key("Delete", SHIFT_MASK); s = r.snapshot()
    check("Shift+Delete on a rewritten conversion undoes the rewrite",
          handled and s.preview != probe and s.composing, f"handled={handled} preview={s.preview!r}")
    r.clear()
    check("...and it stays forgotten", t.preview(keys(probe)) != probe, t.preview(keys(probe)))

    r.clear()
    handled = r.key("Delete", SHIFT_MASK)
    check("Shift+Delete with nothing typed goes to the app", not handled, f"handled={handled}")

    # English -----------------------------------------------------------------
    before = t.preview("kubectl")
    check("precondition: kubectl is not recognised as English", before != "kubectl", before)
    check("Ctrl+Enter commits the letters as typed", t.english("kubectl") == "kubectl")
    check("the word is English from then on", t.preview("kubectl") == "kubectl", t.preview("kubectl"))
    mixed = keys("我用") + "kubectl" + keys("部署")
    check("...also between Chinese", t.preview(mixed) == "我用kubectl部署", t.preview(mixed))
    t.reset_context(); t.type("kube"); r.key("Tab"); s = r.snapshot(); r.clear()
    check("Tab completes a learned word", s.committed == "kubectl", f"committed={s.committed!r}")
    t.reset_context(); t.type("kubectl"); r.key("Down")
    handled = r.key("Delete", CONTROL_MASK); r.clear()
    check("Ctrl+Delete on the English candidate forgets it",
          handled and t.preview("kubectl") != "kubectl", t.preview("kubectl"))

    # Next words ----------------------------------------------------------------
    r.set_option("prediction", True)
    meeting = "下午開會"
    for text in ("我們下午開會討論專案進度", "明天早上開會討論預算"):
        t.enter(keys(text), text)
    t.enter(keys(meeting), meeting, keep=True); s = r.snapshot()
    preds = [c for _, c in s.candidates]
    check("a next word seen twice is predicted first", preds[:1] == ["討論"], f"{preds}")
    handled = r.key("Delete", SHIFT_MASK); r.clear()
    t.enter(keys(meeting), meeting, keep=True); s = r.snapshot(); r.clear()
    preds = [c for _, c in s.candidates]
    check("Shift+Delete on a prediction stops predicting it", handled and "討論" not in preds,
          f"handled={handled} {preds}")
    r.set_option("prediction", False)

    # Persistence: a new session sees what an earlier one learned.
    r.api.destroy_session(r.session)
    r.session = r.api.create_session()
    r.select_schema("asus_zhuyin")
    r.set_option("prediction", False)
    t.reset_context()
    code = "我把程式碼寄給你"
    check("learned data survives a new session", t.preview(keys(code)) == code, t.preview(keys(code)))

    r.close()
    lua_errors = [line for f in r.log_dir.glob("*.log*")
                  for line in f.read_text(encoding="utf-8", errors="replace").splitlines()
                  if line.startswith("E") and "lua" in line.lower()]
    check("no Lua runtime errors in librime log", not lua_errors, lua_errors[0] if lua_errors else "")

    # Decay (own process: librime initialises once per process).
    out = subprocess.run([sys.executable, __file__, "--decay"], capture_output=True)
    lines = out.stdout.decode("utf-8", "replace").strip().splitlines()
    fresh, old = (lines[-1].split("\t") + ["", ""])[:2] if lines else ("", "")
    check("decay: a fresh correction applies", fresh == app, f"{fresh!r} {out.stderr[-300:]!r}")
    check("decay: an old, faded correction no longer applies", old and old != app, f"{old!r}")

    width = max(len(n) for n, _, _ in RESULTS)
    failed = 0
    for name, ok, detail in RESULTS:
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {'' if ok else detail}")
    print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
