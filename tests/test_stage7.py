"""Stage 7 behaviour tests: correcting keys typed one key off.

    python tests/test_stage7.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from rime_harness import open_rime  # noqa: E402
from zhuyin import reading_keys  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
NO_LEARN = "patch:\n  asus_learn/enabled: false\n  translator/enable_user_dict: false\n"


def keys(text: str) -> str:
    return "".join(reading_keys(text)[0])


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    r = open_rime("stage7", extra={"asus_zhuyin.custom.yaml": NO_LEARN})
    r.set_option("prediction", False)

    def preview(k: str) -> str:
        r.clear(); r.type(k); s = r.snapshot(); r.clear()
        return s.preview

    sent = "我今天晚上要去吃飯"
    good = keys(sent)
    check("precondition: typed correctly it converts", preview(good) == sent, preview(good))
    # ㄢ (0) of 晚 typed as its neighbour ㄞ (9): ㄨㄞˇ is a syllable too.
    typo = good.replace("j03", "j93", 1)
    check("a key one off is corrected (still a valid syllable)", preview(typo) == sent, preview(typo))
    r.clear(); r.type(typo); r.key("Down"); c = [x for x, _ in r.snapshot().candidates]; r.clear()
    check("what was typed stays second in the list", len(c) > 1 and c[0] == sent and c[1] != sent, f"{c[:3]}")
    # ㄨ (j) typed as ㄍ... no: k is ㄜ, and ㄜㄢˇ is no syllable.
    bad = good.replace("j03", "k03", 1)
    check("a key that breaks the syllable is repaired", preview(bad) == sent, f"{bad!r} -> {preview(bad)!r}")
    # An extra key pressed with the right one (ㄨ + ㄜ).
    extra = good.replace("j03", "jk03", 1)
    check("an extra key inside a syllable is dropped", preview(extra) == sent, f"{extra!r} -> {preview(extra)!r}")
    # Correct input is never changed.
    for text in ("謝謝你的幫忙", "我想要自訂快捷鍵", "請在下週之前交報告", "我用python跑實驗"):
        k = "".join(reading_keys(text)[0]) if "python" not in text else keys("我用") + "python" + keys("跑實驗")
        check(f"correct input stays: {text}", preview(k) == text, preview(k))
    r.close()

    off = open_rime("stage7-off", extra={"asus_zhuyin.custom.yaml": NO_LEARN + "  asus_typo/enabled: false\n"})
    off.set_option("prediction", False)
    off.clear(); off.type(typo); p = off.snapshot().preview; off.close()
    check("asus_typo/enabled: false turns it off", p != sent, p)

    lua_errors = [line for rr in (r, off) for f in rr.log_dir.glob("*.log*")
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
