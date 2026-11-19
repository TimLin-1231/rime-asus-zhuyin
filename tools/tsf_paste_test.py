"""Real-window test of paste while composing (DaoHui for Windows 0.17.4.16).

Ctrl+V, Shift+Insert and Ctrl+Shift+V while composing must commit what is
shown and paste after it. Rime does its part in asus_keys.lua (tested by
tests/test_paste.py); the front end must hold the key back until the commit
is in the document, then send it again (WeaselTSF/KeyEventSink.cpp). Only a
real window shows that, so this script types into a classic Notepad with
SendInput and reads the text back. It takes the keyboard focus for about
half a minute: do not touch keyboard or mouse while it runs. Every key is
only sent while its own Notepad is in the foreground; otherwise it stops.

    python tools/tsf_paste_test.py

The clipboard is set to a test text and put back afterwards (text only).
Needs: 島輝 as the current input method, in Chinese mode. Browsers and Word
still have to be tried by hand.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tsf_test import Notepad, Stop, Typist, VK_RETURN, user32, kernel32  # noqa: E402

CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
VK_INSERT, VK_ESCAPE = 0x2D, 0x1B
user32.OpenClipboard.argtypes = [wt.HWND]
user32.GetClipboardData.restype = wt.HANDLE
user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
user32.SetClipboardData.restype = wt.HANDLE
kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wt.HGLOBAL
kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]


def _open_clipboard() -> None:
    for _ in range(20):
        if user32.OpenClipboard(None):
            return
        time.sleep(0.05)
    raise Stop("could not open the clipboard")


def get_clipboard() -> str | None:
    _open_clipboard()
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = kernel32.GlobalLock(h)
        try:
            return ctypes.wstring_at(p)
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def set_clipboard(text: str) -> None:
    data = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(data)
    h = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
    p = kernel32.GlobalLock(h)
    ctypes.memmove(p, data, size)
    kernel32.GlobalUnlock(h)
    _open_clipboard()
    try:
        user32.EmptyClipboard()
        user32.SetClipboardData(CF_UNICODETEXT, h)
    finally:
        user32.CloseClipboard()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    back = user32.GetForegroundWindow()
    saved = get_clipboard()
    results: list[tuple[str, bool, str]] = []
    a = None
    CLIP, HELLO, KEYS = "ABC", "你好", "su3cl3"  # ㄋㄧˇ ㄏㄠˇ
    try:
        set_clipboard(CLIP)
        a = Notepad()
        t = Typist([a])
        a.activate(); t.clear()
        t.keys(KEYS); t.vk(VK_RETURN); time.sleep(0.3)
        got = a.text()
        if got != HELLO:
            raise Stop(f"typing {KEYS} + Enter gave {got!r}, not {HELLO!r}: 島輝 is not the active input "
                       f"method in Chinese mode here, so nothing can be tested")
        results.append(("the input method works in Notepad", True, got))

        def case(name: str, want, steps) -> None:
            t.clear()
            steps()
            time.sleep(0.6)
            got = a.text()
            if callable(want):
                ok = want(got)
            else:
                ok = got in want if isinstance(want, tuple) else got == want
            results.append((name, ok, repr(got)))

        case("Ctrl+V while composing: committed, then pasted", HELLO + CLIP,
             lambda: (t.keys(KEYS), t.vk(ord("V"), ctrl=True)))
        case("Shift+Insert while composing: committed, then pasted", HELLO + CLIP,
             lambda: (t.keys(KEYS), t.vk(VK_INSERT, extended=True, shift=True)))
        # (Notepad's Edit control does not paste on Ctrl+Shift+V.)
        case("Ctrl+Shift+V while composing: committed, nothing lost or doubled", (HELLO, HELLO + CLIP),
             lambda: (t.keys(KEYS), t.vk(ord("V"), ctrl=True, shift=True)))
        case("Ctrl+V with nothing typed: pasted", CLIP,
             lambda: t.vk(ord("V"), ctrl=True))
        case("Ctrl+V twice while composing", HELLO + CLIP + CLIP,
             lambda: (t.keys(KEYS), t.vk(ord("V"), ctrl=True), time.sleep(0.4), t.vk(ord("V"), ctrl=True)))
        # (ㄨˇ after the paste: 五, 午… depending on what was learned.)
        case("Ctrl+V while composing, then typing on: one more character after the paste",
             lambda got: got.startswith(HELLO + CLIP) and len(got) == len(HELLO + CLIP) + 1,
             lambda: (t.keys(KEYS), t.vk(ord("V"), ctrl=True), time.sleep(0.4),
                      t.keys("j3"), t.vk(VK_RETURN)))
        # The symbol keyboard (`) keeps composing: the key must go on once,
        # not be held back again and again.
        case("Ctrl+V on the symbol keyboard: no loop, pasted at most once", ("", CLIP),
             lambda: (t.keys("`"), t.vk(ord("V"), ctrl=True), time.sleep(0.8), t.vk(VK_ESCAPE)))
    except Stop as e:
        print("STOPPED:", e)
    finally:
        if a:
            a.close()
        if saved is not None:
            try:
                set_clipboard(saved)
            except Stop:
                print("note: the clipboard could not be put back")
        if back:
            user32.SetForegroundWindow(back)
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {'' if ok else detail}")
    return 0 if results and all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
