"""Stage 4 behaviour tests: next-word prediction and autocomplete.

    python tests/test_stage4.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from rime_harness import open_rime  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
JIQI = "ru fu4"  # ㄐㄧ ㄑㄧˋ -> 機器


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    r = open_rime("stage4")

    r.clear(); r.type(JIQI); s = r.snapshot()
    check("precondition: ㄐㄧ ㄑㄧˋ converts to 機器", s.preview == "機器", f"{s.preview!r}")

    # Autocomplete while composing --------------------------------------
    check("autocomplete hint shown in the bubble (⇥ continuation)",
          s.candidates and "⇥" in s.candidates[0][1], f"{s.candidates[:1]}")
    hint = s.candidates[0][1].split("⇥")[-1].strip() if s.candidates else ""
    r.key("Tab"); s = r.snapshot()
    check("Tab accepts conversion + continuation", s.committed == "機器" + hint and hint,
          f"committed={s.committed!r} hint={hint!r}")

    # Prediction after a commit ------------------------------------------
    r.clear(); r.type(JIQI); r.key("Return"); s = r.snapshot()
    preds = [c for _, c in s.candidates]
    check("after committing 機器, next words are predicted",
          s.committed == "機器" and len(preds) >= 2 and all(t == "" for t, _ in s.candidates),
          f"committed={s.committed!r} cands={s.candidates}")
    check("prediction leaves the inline preview empty", s.preview == "", f"{s.preview!r}")

    r.key("Tab"); s = r.snapshot()
    check("Tab accepts the first prediction", s.committed == "機器" + preds[0],
          f"committed={s.committed!r}")
    check("predictions chain after an accepted word", s.composing and s.candidates,
          f"composing={s.composing} cands={s.candidates[:2]}")

    r.clear(); r.type(JIQI); r.key("Return"); r.key("Down"); r.key("Right"); s = r.snapshot()
    check("Down moves to the next prediction; Right accepts it",
          s.committed == "機器" + preds[1], f"committed={s.committed!r}")

    r.clear(); r.type(JIQI); r.key("Return"); r.type("su3"); s = r.snapshot()
    check("typing dismisses the prediction and starts a new composition",
          s.input == "su3" and s.committed == "機器", f"input={s.input!r} committed={s.committed!r}")

    for key in ("Return", "BackSpace", "Escape"):
        r.clear(); r.type(JIQI); r.key("Return")
        handled = r.key(key); s = r.snapshot()
        check(f"{key} dismisses the prediction and goes to the app",
              not handled and not s.composing, f"handled={handled} composing={s.composing}")

    r.clear(); r.type("python"); r.key("space"); s = r.snapshot()
    check("no prediction after English", not s.composing, f"cands={s.candidates}")

    r.set_option("prediction", False)
    r.clear(); r.type(JIQI); r.key("Return"); s = r.snapshot()
    check("prediction switch off: nothing is predicted", not s.composing, f"cands={s.candidates}")
    r.set_option("prediction", True)

    r.close()
    lua_errors = [line for f in r.log_dir.glob("*.log*")
                  for line in f.read_text(encoding="utf-8", errors="replace").splitlines()
                  if line.startswith("E") and "lua" in line.lower()]
    check("no Lua runtime errors in librime log", not lua_errors,
          lua_errors[0] if lua_errors else "")

    width = max(len(n) for n, _, _ in RESULTS)
    failed = 0
    for name, ok, detail in RESULTS:
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {'' if ok else detail}")
    print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
