"""Real-window test of the Weasel TSF patch (patches/weasel): is an unfinished
composition kept, once, when the focus moves away?

The harness drives rime.dll directly and never passes through TSF, so this
script types into a real Notepad with SendInput and reads the text back. It
takes the keyboard focus for about a minute: do not touch keyboard or mouse
while it runs. Every key is only sent while one of its own Notepad windows
is in the foreground; otherwise it stops.

    python tools/tsf_test.py

Needs: 小狼毫 as the current input method, in Chinese mode. Covers classic
Notepad only (an IMM32 application bridged to TSF); Word, browsers and VS
Code talk to TSF differently and still have to be tried by hand.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_GETTEXT, WM_GETTEXTLENGTH, WM_SETTEXT = 0x000D, 0x000E, 0x000C
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
INPUT_KEYBOARD, KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE, KEYEVENTF_EXTENDED = 1, 0x2, 0x8, 0x1
INPUT_MOUSE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0, 0x2, 0x4
VK_CONTROL, VK_RETURN, VK_DELETE, VK_SHIFT = 0x11, 0x0D, 0x2E, 0x10
ULONG_PTR = ctypes.c_size_t


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _U(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _U)]


user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.FindWindowExW.argtypes = [wt.HWND, wt.HWND, wt.LPCWSTR, wt.LPCWSTR]
user32.FindWindowExW.restype = wt.HWND
user32.GetForegroundWindow.restype = wt.HWND
user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.MapVirtualKeyW.argtypes = [wt.UINT, wt.UINT]
user32.VkKeyScanW.argtypes = [wt.WCHAR]
user32.VkKeyScanW.restype = ctypes.c_short
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD


class Stop(Exception):
    pass


def window_of(pid: int) -> int:
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def each(hwnd, _):
        p = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    for _ in range(50):
        user32.EnumWindows(each, 0)
        if found:
            return found[0]
        time.sleep(0.1)
    raise Stop("Notepad did not open")


class Notepad:
    def __init__(self) -> None:
        self.proc = subprocess.Popen(["notepad.exe"])
        self.hwnd = window_of(self.proc.pid)
        self.edit = user32.FindWindowExW(self.hwnd, None, "Edit", None)
        if not self.edit:
            raise Stop("this Notepad has no classic Edit control (Windows 11 Notepad is not supported)")

    def text(self) -> str:
        n = user32.SendMessageW(self.edit, WM_GETTEXTLENGTH, 0, 0)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.SendMessageW(self.edit, WM_GETTEXT, n + 1, ctypes.addressof(buf))
        return buf.value

    def activate(self) -> None:
        fg = user32.GetForegroundWindow()
        me = kernel32.GetCurrentThreadId()
        other = user32.GetWindowThreadProcessId(fg, None)
        user32.AttachThreadInput(me, other, True)
        user32.BringWindowToTop(self.hwnd)
        user32.SetForegroundWindow(self.hwnd)
        user32.AttachThreadInput(me, other, False)
        for _ in range(20):
            if user32.GetForegroundWindow() == self.hwnd:
                time.sleep(0.25)
                return
            time.sleep(0.05)
        raise Stop("could not bring Notepad to the front")

    def click_start(self) -> None:
        """A real mouse click at the start of the text (a posted WM_LBUTTONDOWN
        moves the caret without the input method being told). The pointer is
        put back where it was."""
        if user32.GetForegroundWindow() != self.hwnd:
            raise Stop("another window came to the front: stopped")
        pt, old = wt.POINT(4, 4), wt.POINT()
        user32.ClientToScreen(self.edit, ctypes.byref(pt))
        user32.GetCursorPos(ctypes.byref(old))
        user32.SetCursorPos(pt.x, pt.y)
        time.sleep(0.05)
        for flag in (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP):
            inp = INPUT(type=INPUT_MOUSE)
            inp.u.mi = MOUSEINPUT(0, 0, 0, flag, 0, 0)
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
            time.sleep(0.03)
        user32.SetCursorPos(old.x, old.y)
        time.sleep(0.3)

    def close(self) -> None:
        self.proc.kill()


def _send(scan: int, up: bool, extended: bool = False) -> None:
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDED if extended else 0)
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.u.ki = KEYBDINPUT(0, scan, flags, 0, 0)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


class Typist:
    def __init__(self, windows: list[Notepad]) -> None:
        self.windows = windows

    def _check(self) -> None:
        if user32.GetForegroundWindow() not in [w.hwnd for w in self.windows]:
            raise Stop("another window came to the front: stopped (nothing more was typed)")

    def vk(self, vk: int, extended: bool = False, ctrl: bool = False, shift: bool = False) -> None:
        self._check()
        scan = user32.MapVirtualKeyW(vk, 0)
        mods = [m for m, on in ((VK_CONTROL, ctrl), (VK_SHIFT, shift)) if on]
        for m in mods:
            _send(user32.MapVirtualKeyW(m, 0), False)
        _send(scan, False, extended)
        time.sleep(0.02)
        _send(scan, True, extended)
        for m in reversed(mods):
            _send(user32.MapVirtualKeyW(m, 0), True)
        time.sleep(0.05)

    def keys(self, text: str) -> None:
        for ch in text:
            code = user32.VkKeyScanW(ch)  # low byte: key, bit 8: Shift
            self.vk(code & 0xFF, shift=bool(code & 0x100))
        time.sleep(0.3)

    def clear(self) -> None:
        self.vk(0x1B)  # Esc: drop a composition, if any
        self.vk(ord("A"), ctrl=True)
        self.vk(VK_DELETE, extended=True)
        time.sleep(0.2)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    back = user32.GetForegroundWindow()
    results: list[tuple[str, bool, str]] = []
    a = b = None
    try:
        a, b = Notepad(), Notepad()
        t = Typist([a, b])
        HELLO, KEYS = "你好", "su3cl3"  # ㄋㄧˇ ㄏㄠˇ

        a.activate(); t.clear()
        t.keys(KEYS); t.vk(VK_RETURN); time.sleep(0.3)
        got = a.text()
        if got != HELLO:
            raise Stop(f"typing {KEYS} + Enter gave {got!r}, not {HELLO!r}: 小狼毫 is not the active input "
                       f"method in Chinese mode here, so nothing can be tested")
        results.append(("the input method works in Notepad", True, got))

        # 1. Focus moves to another window with the composition unfinished.
        t.clear(); t.keys(KEYS)
        b.activate(); time.sleep(0.4)
        got = a.text()
        results.append(("focus to another window: the text is kept", got == HELLO, repr(got)))
        a.activate(); t.keys("j3"); t.vk(VK_RETURN); time.sleep(0.3)
        got = a.text()
        results.append(("...and typing on afterwards adds to it once", got == HELLO + "五", repr(got)))

        # 2. Away and back several times, quickly.
        t.clear(); t.keys(KEYS)
        for _ in range(3):
            b.activate(); a.activate()
        b.activate(); time.sleep(0.4)
        got = a.text()
        results.append(("switching back and forth quickly: kept once", got == HELLO, repr(got)))

        # 3. A mouse click elsewhere in the same window: text typed before,
        # then a composition, then a click at the very start.
        a.activate(); t.clear()
        t.keys("j3"); t.vk(VK_RETURN); time.sleep(0.3)  # 五
        t.keys(KEYS)
        a.click_start(); time.sleep(0.3)
        # (Classic Notepad keeps composing after a click: the composition is
        # not part of its text yet and simply moves with the caret. Other
        # applications end it there. Either way nothing may be lost or doubled.)
        t.keys("j3"); t.vk(VK_RETURN); time.sleep(0.3)
        got = a.text()
        results.append(("a click elsewhere, then typing on: nothing lost or doubled",
                        sorted(got) == sorted("五五" + HELLO) and HELLO in got, repr(got)))

        # 4. A shortcut while composing (Ctrl+A).
        t.clear(); t.keys(KEYS)
        t.vk(ord("A"), ctrl=True); time.sleep(0.3)
        got = a.text()
        results.append(("Ctrl+A while composing: nothing lost or doubled", got in (HELLO, ""), repr(got)))
    except Stop as e:
        print("STOPPED:", e)
    finally:
        for w in (a, b):
            if w:
                w.close()
        if back:
            user32.SetForegroundWindow(back)
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {'' if ok else detail}")
    return 0 if results and all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
