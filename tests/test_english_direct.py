"""English mode types directly by default (rime/lua/asus_english.lua).

    python tests/test_english_direct.py

Fields of one character each (verification codes, PINs) only move on when a
character arrives, so English mode must not hold keys in a composition
unless composing is switched on (asus_english/compose, or the app option
asus_en_compose for one program).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from rime_harness import open_rime  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    r = open_rime("english_direct", extra={"asus_zhuyin.custom.yaml": (
        "patch:\n  asus_learn/enabled: false\n  translator/enable_user_dict: false\n")})

    r.clear(); r.set_option("ascii_mode", True)
    handled = [r.key(k) for k in "a1B2"]
    s = r.snapshot()
    check("default: every key goes straight to the program", not any(handled) and not s.composing,
          f"handled={handled} composing={s.composing} preedit={s.preedit!r}")
    handled = r.key("Tab")
    check("default: Tab goes to the program too", not handled, f"handled={handled}")

    r.clear(); r.set_option("asus_en_compose", True)
    r.type("pyth"); s = r.snapshot()
    check("app option asus_en_compose: the word is composed with guesses",
          s.composing and s.preedit == "pyth" and any(c == "python" for c, _ in s.candidates),
          f"composing={s.composing} preedit={s.preedit!r} {s.candidates[:3]}")
    r.clear(); r.set_option("asus_en_compose", False)

    r.set_option("ascii_mode", False)
    r.clear(); r.type("su3cl3"); s = r.snapshot()
    check("Chinese mode is unchanged", s.preview == "你好", repr(s.preview))

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
