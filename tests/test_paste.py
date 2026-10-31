"""Paste while composing (rime/lua/asus_keys.lua).

    python tests/test_paste.py

Ctrl+V (also Ctrl+Shift+V, Shift+Insert) while composing commits what is
shown and is not handled, so the application gets the key and pastes after
the committed text. Rime's emacs bindings used to take Ctrl+V as Page_Down:
nothing happened.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from rime_harness import CONTROL_MASK, SHIFT_MASK, open_rime  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    r = open_rime("paste", extra={"asus_zhuyin.custom.yaml": (
        "patch:\n  asus_learn/enabled: false\n  translator/enable_user_dict: false\n")})

    def paste_after(keys: str, key: str = "v", mask: int = CONTROL_MASK, prep=None):
        """Type keys (after prep), press the paste key; (shown, handled, snapshot)."""
        r.clear()
        if prep:
            prep()
        r.type(keys)
        shown = r.snapshot().preview
        handled = r.key(key, mask)
        return shown, handled, r.snapshot()

    for label, key, mask in (("Ctrl+V", "v", CONTROL_MASK), ("Ctrl+Shift+V", "V", CONTROL_MASK | SHIFT_MASK),
                             ("Shift+Insert", "Insert", SHIFT_MASK)):
        shown, handled, s = paste_after("su3dk3u3", key, mask)  # 你可以
        check(f"{label} while composing: what is shown is committed", "".join(r.committed) == shown == "你可以",
              f"{shown!r} {r.committed!r}")
        check(f"{label}: the key goes on to the application", not handled)
        check(f"{label}: nothing left, no next-word prediction", not s.composing and not s.candidates,
              f"{s.input!r} {s.candidates[:2]}")

    r.clear()
    check("Ctrl+V with nothing typed: not handled, nothing committed",
          not r.key("v", CONTROL_MASK) and not r.committed, repr(r.committed))

    r.clear()
    r.type("su3dk3u3")
    r.key("Return")
    had_prediction = bool(r.snapshot().candidates)
    r.committed.clear()
    handled = r.key("v", CONTROL_MASK)
    s = r.snapshot()
    check("Ctrl+V while a prediction shows: it goes, nothing is committed",
          had_prediction and not handled and not r.committed and not s.candidates,
          f"{had_prediction} {handled} {r.committed!r} {s.candidates[:2]}")

    r.clear()
    r.type("su3dk3u3")
    r.key("Down")  # the full list
    expanded = len(r.snapshot().candidates) > 1
    shown = r.snapshot().preview
    handled = r.key("v", CONTROL_MASK)
    check("Ctrl+V with the list open: commits what is shown", expanded and not handled
          and "".join(r.committed) == shown, f"{expanded} {shown!r} {r.committed!r}")
    r.type("su3")
    s = r.snapshot()
    check("and the next input starts compact", s.composing and len(s.candidates) <= 2, f"{s.candidates[:3]}")

    r.clear()
    r.key("space", CONTROL_MASK)  # English mode: letters go straight to the application
    r.type("hello")
    composing = r.snapshot().composing
    handled = r.key("v", CONTROL_MASK)
    check("English mode: Ctrl+V goes on to the application", not composing and not handled and not r.committed,
          f"{composing} {handled} {r.committed!r}")
    r.clear()
    r.key("space", CONTROL_MASK)  # back to Chinese

    shown, handled, s = paste_after("su3dk3u3", "a", CONTROL_MASK)
    check("Ctrl+A still moves to the start (Rime's emacs binding)", handled and not r.committed and s.composing,
          f"{handled} {r.committed!r}")

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
