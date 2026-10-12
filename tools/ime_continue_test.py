"""Real-window test of the continuation after a pause (stage 16): the input
method in a real Notepad, the writing helper with its model beside it.

It starts the helper, opens a Notepad, switches the input method to English
mode there, types a few words and waits; then Tab must type the phrase the
model offered. It takes the keyboard focus for about 40 seconds (the model
loads during the first wait): do not touch keyboard or mouse while it runs.
Keys are only sent while its own Notepad is in the foreground.

    python tools/ime_continue_test.py

Needs: 小狼毫 as the current input method, redeployed with this version of
rime/; the helper not running already; the model fetched.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from assist import llm, selection  # noqa: E402
from assist.app import TITLE  # noqa: E402
from tsf_test import VK_RETURN, VK_SHIFT, Notepad, Stop, Typist, user32  # noqa: E402
import ctypes.wintypes as wt  # noqa: E402

VK_TAB = 0x09
RESULTS: list[tuple[str, bool, str]] = []
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.FindWindowW.restype = wt.HWND


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    back = user32.GetForegroundWindow()
    saved = selection.clipboard_text()
    np = helper = None
    switched = False
    try:
        if not llm.available():
            raise Stop("the model is not installed: python tools/fetch_assist.py")
        log = ROOT / ".harness" / "assist-test.log"
        log.parent.mkdir(exist_ok=True)
        helper = subprocess.Popen([sys.executable, "-m", "assist"], cwd=ROOT, stdout=log.open("wb"),
                                  stderr=subprocess.STDOUT)
        time.sleep(3.0)
        if helper.poll() is not None or user32.FindWindowW("#32770", TITLE):
            raise Stop("the helper did not start (already running, or its hotkey is taken)")

        np = Notepad()
        t = Typist([np])
        np.activate(); t.clear()

        # Which mode is the input method in? su3cl3 is 你好 in Chinese mode.
        t.keys("su3cl3"); t.vk(VK_RETURN); time.sleep(0.4)
        got = np.text()
        if got == "你好":
            t.vk(VK_SHIFT); t.vk(VK_SHIFT); time.sleep(0.5)  # double tap: English mode
            switched = True
        elif got.strip() != "su3cl3":
            raise Stop(f"typing su3cl3 + Enter gave {got!r}: 小狼毫 is not the active input method here")
        t.clear()

        # English mode composes and corrects: this is the deployed stage 14+ code.
        t.keys("recieve it "); time.sleep(0.4)
        got = np.text()
        if got != "receive it ":
            raise Stop(f"typing 'recieve it ' gave {got!r}: English mode with automatic correction is not "
                       "active (redeploy 小狼毫 with this version of rime/)")
        check("English mode with automatic correction is active", True)

        # 1. The first continuation (the model loads during this wait).
        t.clear()
        t.keys("thank you very "); time.sleep(10.0)
        t.vk(VK_TAB); time.sleep(0.6)
        got = np.text()
        check("after a pause, Tab types the model's phrase", got.lower().startswith("thank you very much"), repr(got))

        # 2. With the model loaded it is there within a second and a half.
        t.clear()
        t.keys("i look forward to hearing from "); time.sleep(1.5)
        t.vk(VK_TAB); time.sleep(0.6)
        got = np.text()
        check("the next one arrives within 1.5 s", got.lower().startswith("i look forward to hearing from you"), repr(got))

        # 3. Typing on without a pause: nothing but what was typed.
        t.clear()
        typed = "thank you very much for all the help"
        t.keys(typed); time.sleep(1.5)
        t.vk(VK_RETURN); time.sleep(0.4)
        got = np.text()
        check("typing without a pause leaves only what was typed", got.strip() == typed, repr(got))
    except Stop as e:
        check("the test could run", False, str(e))
    finally:
        if np is not None:
            try:
                if switched and user32.GetForegroundWindow() == np.hwnd:
                    Typist([np]).vk(VK_SHIFT); Typist([np]).vk(VK_SHIFT); time.sleep(0.3)  # back to Chinese mode
            except Stop:
                pass
            np.close()
        if helper is not None and helper.poll() is None:
            subprocess.run(["taskkill", "/PID", str(helper.pid), "/T", "/F"], capture_output=True)
        selection.set_clipboard_text(saved)
        if back:
            user32.SetForegroundWindow(back)
    width = max(len(n) for n, _, _ in RESULTS)
    failed = 0
    for name, ok, detail in RESULTS:
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {'' if ok else detail}")
    print(f"{len(RESULTS) - failed}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
