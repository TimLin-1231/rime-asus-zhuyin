"""Number behaviour tests: digits typed on the top row in Chinese mode.

    python tests/test_numbers.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from rime_harness import open_rime  # noqa: E402
from zhuyin import reading_keys  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def keys(marked: str) -> str:
    """Chinese with [digits] typed as-is."""
    out, buf, i = [], "", 0
    while i < len(marked):
        if marked[i] == "[":
            if buf:
                out.append("".join(reading_keys(buf)[0])); buf = ""
            j = marked.index("]", i)
            out.append(marked[i + 1:j]); i = j + 1
        else:
            buf += marked[i]; i += 1
    if buf:
        out.append("".join(reading_keys(buf)[0]))
    return "".join(out)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    r = open_rime("numbers", extra={"asus_zhuyin.custom.yaml": "patch:\n  asus_learn/enabled: false\n"})
    r.set_option("prediction", False)

    def preview(k: str) -> str:
        r.clear(); r.type(k); s = r.snapshot(); r.clear()
        return s.preview

    for k in ("2026", "100", "3", "12", "3.14", "35000"):
        check(f"{k} alone is a number", preview(k) == k, preview(k))
    for marked in ("我[2026]年畢業", "我們[3]點見", "這個要[100]元", "我買了[2]杯咖啡",
                   "我[18]歲的時候", "第[2]章寫完了", "體重少了[2.5]公斤", "價格打[8]折"):
        want = marked.replace("[", "").replace("]", "")
        check(f"mixed: {want}", preview(keys(marked)) == want, preview(keys(marked)))

    # Digit keys that spell complete Zhuyin stay Chinese; the digits are second.
    for k, zh in (("204", "但"), ("283", "打")):
        r.clear(); r.type(k); s1 = r.snapshot(); r.key("Down"); s2 = r.snapshot(); r.clear()
        cands = [c for c, _ in s2.candidates]
        check(f"{k} is {zh}; the digits are the second candidate",
              s1.preview == zh and cands[:2] == [zh, k], f"{s1.preview!r} {cands[:3]}")
    r.clear(); r.type("2026"); r.key("Down"); s = r.snapshot(); r.key("2"); s2 = r.snapshot(); r.clear()
    check("a number offers full-width digits second, then the Chinese reading",
          [c for c, _ in s.candidates][:2] == ["2026", "２０２６"] and s2.committed == "２０２６",
          f"{s.candidates[:3]} committed={s2.committed!r}")

    # Chinese is not disturbed while typing it.
    for text in ("謝謝你的幫忙", "等一下我再打電話給你", "我今天晚上要去吃飯"):
        k = "".join(reading_keys(text)[0])
        leaks = []
        for i in range(1, len(k) + 1):
            p = preview(k[:i])
            if any(c.isdigit() for c in p):
                leaks.append(f"{k[:i]}->{p}")
        check(f"no digits while typing {text}", not leaks, ", ".join(leaks[:3]))
        check(f"{text} converts as before", preview(k) == text, preview(k))

    handled = r.key("Return")  # nothing composing
    r.clear()
    handled_kp = bool(r.api.process_key(r.session, 0xFFB2, 0))  # KP_2
    check("numeric keypad digits go straight to the app", not handled_kp, f"handled={handled_kp}")

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
