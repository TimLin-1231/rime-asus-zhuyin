"""Tab switches the last word between English and Chinese (rime/lua/asus_tab.lua).

    python tests/test_tab.py

Raw keys shown inline (asus_lang/raw_keys, the live setting) unless noted.
A "slow" second Tab waits past asus_tab/double_ms; a quick one is a double
Tab, which completes as Tab alone did before.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from rime_harness import open_rime  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
PAUSE = 0.5  # past double_ms (400 ms)


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    r = open_rime("tab", extra={"asus_zhuyin.custom.yaml": (
        "patch:\n  asus_learn/enabled: false\n  translator/enable_user_dict: false\n"
        "  asus_lang/raw_keys: true\n")})

    def run(*seq: str):
        """Type; "T" = Tab, "_" = a pause past the double-Tab window."""
        time.sleep(PAUSE)
        r.clear()
        for k in seq:
            if k == "T":
                r.key("Tab")
            elif k == "_":
                time.sleep(PAUSE)
            elif k in ("Return", "Escape", "BackSpace"):
                r.key(k)
            else:
                r.type(k)
        return r.snapshot()

    s = run("ai")
    check("ai alone shows the keys", s.preview == "ai", repr(s.preview))
    s = run("ai", "T")
    check("ai + Tab -> 摸 (first tone added)", s.preview == "摸" and s.input == "ai ", f"{s.preview!r} {s.input!r}")
    s = run("ai", "T", "_", "T")
    check("...Tab again -> ai", s.preview == "ai" and s.input == "ai", f"{s.preview!r} {s.input!r}")
    s = run("ai", "T", "_", "T", "_", "T")
    check("...and again -> 摸", s.preview == "摸", repr(s.preview))
    s = run("ai", "T", "Return")
    check("ai + Tab + Enter commits 摸", s.committed == "摸", repr(s.committed))
    s = run("su3ai", "T")
    check("你ai + Tab -> 你摸", s.preview == "你摸", repr(s.preview))
    s = run("su3ai", "T", "_", "T")
    check("...Tab again -> 你ai", s.preview == "你ai", repr(s.preview))

    s = run("pyth", "T")
    check("an English word + Tab -> its keys as Chinese", s.preview != "pyth" and all(
        0x3400 <= ord(c) <= 0x9fff for c in s.preview), repr(s.preview))
    s = run("pyth", "T", "_", "T")
    check("...Tab again -> the English word", s.preview == "pyth" and s.input == "pyth", f"{s.preview!r} {s.input!r}")
    s = run("su3wifi", "T", "_", "T")
    check("你wifi: Tab twice (slowly) -> 你wifi", s.preview == "你wifi", repr(s.preview))
    s = run("su3wifi", "T")
    check("你wifi + Tab: only the last word changes", s.preview.startswith("你") and "wifi" not in s.preview,
          repr(s.preview))

    s = run("pyth", "T", "T")
    check("double Tab completes: pyth -> python", s.committed == "python", repr(s.committed))
    s = run("pyth", "T", "_", "T", "T")
    check("double Tab after a switch back still completes", s.committed == "python", repr(s.committed))
    s = run("ji3cl3", "T", "T")
    check("double Tab with nothing to complete: nothing moves", s.preview == "我好" and s.committed == "",
          f"{s.preview!r} {s.committed!r}")
    s = run("ru04", "T")
    check("Chinese with tone keys: Tab changes nothing", s.preview == "見" and s.input == "ru04",
          f"{s.preview!r} {s.input!r}")

    s = run("pyth", "T", "Escape")
    s2 = run("pyth")
    check("Esc drops the switch: pyth is English again", s2.preview == "pyth", repr(s2.preview))
    s = run("1234", "T")
    check("a number: Tab still opens its forms (asus_quick)", [c for c, _ in s.candidates][:2] == ["1234", "1,234"],
          f"{s.candidates[:3]}")
    r.clear(); r.type("ji3cl3"); r.key("Return"); time.sleep(PAUSE)
    s = r.snapshot()
    had = bool(s.candidates)
    handled = r.key("Tab"); s = r.snapshot()
    check("after a commit Tab still accepts a prediction", not had or (handled and len(s.committed) > 2),
          f"had={had} {s.committed!r}")

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
