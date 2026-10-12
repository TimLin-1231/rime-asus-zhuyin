"""Real-window test of the writing helper (assist/): does it read the selected
text of another program, and put the corrected text in its place?

It opens a Notepad, selects a faulty sentence there, and
  1. copies and replaces the selection with assist/selection.py;
  2. starts the helper, presses its hotkey, waits for the correction and
     presses Enter, then reads Notepad's text back.
It takes the keyboard focus for about 20 seconds (most of it waiting for the
model to load): do not touch keyboard or mouse while it runs. Keys are only
sent while its own Notepad, or the helper's window, is in the foreground.

    python tools/assist_test.py

Needs: the helper not running already; Harper and the model fetched
(tools/fetch_assist.py). Covers classic Notepad; other programs by hand.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from assist import llm, selection  # noqa: E402
from assist.app import DEFAULTS, READY_TITLE, TITLE, load_config, parse_hotkey  # noqa: E402
from tsf_test import Notepad, Stop, user32  # noqa: E402

WM_SETTEXT, EM_SETSEL = 0x000C, 0x00B1
VK_RETURN, VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN = 0x0D, 0x11, 0x12, 0x10, 0x5B
FAULTY = "I look forward to hear from you."
CORRECT = "I look forward to hearing from you."
RESULTS: list[tuple[str, bool, str]] = []

user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.FindWindowW.restype = wt.HWND


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def prepare(np: Notepad, text: str, select: bool) -> None:
    user32.SendMessageW(np.edit, WM_SETTEXT, 0, ctypes.addressof(ctypes.create_unicode_buffer(text)))
    np.activate()
    user32.SendMessageW(np.edit, EM_SETSEL, 0 if select else len(text), len(text))
    time.sleep(0.2)
    if user32.GetForegroundWindow() != np.hwnd:
        raise Stop("another window came to the front: stopped")


def press_hotkey(spec: str) -> None:
    mods, vk = parse_hotkey(spec)
    held = [v for bit, v in ((0x2, VK_CONTROL), (0x1, VK_MENU), (0x4, VK_SHIFT), (0x8, VK_LWIN)) if mods & bit]
    selection._chord(*held, vk)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    cfg = load_config()
    marker = "clipboard before the test"
    saved = selection.clipboard_text()  # the user's own, put back at the end
    np, helper = Notepad(), None
    try:
        # -- 1. copy and replace ---------------------------------------------------
        selection.set_clipboard_text(marker)
        prepare(np, FAULTY, select=True)
        text, before = selection.grab(line_if_empty=False)
        check("the selected text is read", text == FAULTY, repr(text))
        check("the earlier clipboard text is remembered", before == marker, repr(before))
        ok = selection.replace(np.hwnd, CORRECT, before)
        time.sleep(0.2)
        check("the selection is replaced", ok and np.text() == CORRECT, repr(np.text()))
        check("the clipboard is put back", selection.clipboard_text() == marker, repr(selection.clipboard_text()))

        prepare(np, FAULTY, select=False)
        text, before = selection.grab(line_if_empty=True)
        check("with nothing selected, the line up to the caret is taken", text == FAULTY, repr(text))
        selection.restore(before)

        # -- 2. the helper itself --------------------------------------------------
        if not llm.available():
            raise Stop("the model is not installed: python tools/fetch_assist.py")
        helper = subprocess.Popen([sys.executable, "-m", "assist"], cwd=ROOT)
        time.sleep(3.0)
        if helper.poll() is not None:
            raise Stop("the helper did not start (already running, or its hotkey is taken)")
        prepare(np, FAULTY, select=True)
        press_hotkey(cfg.get("hotkey", DEFAULTS["hotkey"]))
        popup, deadline = None, time.monotonic() + 90
        while time.monotonic() < deadline and not popup:
            popup = user32.FindWindowW(None, READY_TITLE)
            time.sleep(0.2)
        check("the hotkey opens the window and a correction arrives",
              bool(popup), "no window titled " + READY_TITLE + (" (it opened, no result)" if user32.FindWindowW(None, TITLE) else ""))
        if popup:
            time.sleep(0.3)
            in_front = user32.GetForegroundWindow() == popup
            check("the window has the keyboard", in_front)
            if in_front:
                selection._chord(VK_RETURN)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and np.text() != CORRECT:
                    time.sleep(0.1)
                check("Enter puts the corrected sentence into Notepad", np.text() == CORRECT, repr(np.text()))
                check("Notepad is in front again", user32.GetForegroundWindow() == np.hwnd)
                time.sleep(0.6)
                check("the clipboard is put back again", selection.clipboard_text() == marker,
                      repr(selection.clipboard_text()))
    except Stop as e:
        check("the test could run", False, str(e))
    finally:
        if helper is not None and helper.poll() is None:
            subprocess.run(["taskkill", "/PID", str(helper.pid), "/T", "/F"], capture_output=True)
        np.close()
        selection.set_clipboard_text(saved)
    width = max(len(n) for n, _, _ in RESULTS)
    failed = 0
    for name, ok, detail in RESULTS:
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {'' if ok else detail}")
    print(f"{len(RESULTS) - failed}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
