"""Read the text selected in another program, and put text in its place.

Both go through the clipboard (Ctrl+C / Ctrl+V sent with SendInput), which
works in nearly every program. The text that was on the clipboard before is
put back afterwards; other clipboard content (a picture, files) is not.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
INPUT_KEYBOARD, KEYEVENTF_KEYUP, KEYEVENTF_EXTENDEDKEY = 1, 0x2, 0x1
VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN = 0x10, 0x11, 0x12, 0x5B, 0x5C
VK_C, VK_V, VK_HOME = 0x43, 0x56, 0x24
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


user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.GetForegroundWindow.restype = wt.HWND
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.IsWindow.argtypes = [wt.HWND]
user32.OpenClipboard.argtypes = [wt.HWND]
user32.GetClipboardData.argtypes = [wt.UINT]
user32.GetClipboardData.restype = wt.HANDLE
user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
user32.SetClipboardData.restype = wt.HANDLE
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wt.HGLOBAL
kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
kernel32.GlobalFree.argtypes = [wt.HGLOBAL]
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]


def foreground() -> int:
    return user32.GetForegroundWindow() or 0


def process_name(hwnd: int) -> str:
    """Lower-case .exe name of the program that owns the window ("" if unknown)."""
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(wt.HWND(hwnd), ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(len(buf))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return buf.value.replace("/", "\\").rsplit("\\", 1)[-1].lower()
    finally:
        kernel32.CloseHandle(handle)


def _open_clipboard() -> bool:
    for _ in range(20):  # another program may hold it for a moment
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def clipboard_text() -> str | None:
    """Text on the clipboard; None when there is none (or it cannot be read)."""
    if not _open_clipboard():
        return None
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def set_clipboard_text(text: str | None) -> bool:
    """Put text on the clipboard (None: leave it empty)."""
    if not _open_clipboard():
        return False
    try:
        user32.EmptyClipboard()
        if text is None:
            return True
        data = ctypes.create_unicode_buffer(text)
        size = ctypes.sizeof(data)
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not handle:
            return False
        ptr = kernel32.GlobalLock(handle)
        ctypes.memmove(ptr, data, size)
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            return False
        return True
    finally:
        user32.CloseClipboard()


def _key(vk: int, up: bool = False) -> INPUT:
    # Home is an "extended" key: sent without the flag it counts as the
    # numeric pad's, and Windows then drops the Shift held with it.
    flags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if vk == VK_HOME else 0)
    return INPUT(INPUT_KEYBOARD, _U(ki=KEYBDINPUT(vk, 0, flags, 0, 0)))


def _send(*events: INPUT) -> None:
    arr = (INPUT * len(events))(*events)
    user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))


def _chord(*vks: int) -> None:
    """Press the keys in order and release them in reverse (Ctrl+C)."""
    _send(*[_key(vk) for vk in vks], *[_key(vk, True) for vk in reversed(vks)])


def wait_modifiers_up(timeout: float = 1.5) -> bool:
    """Wait until the user has let go of Ctrl/Alt/Shift/Win (the hotkey):
    a Ctrl+C sent while Alt is still down would be Ctrl+Alt+C."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN)):
            return True
        time.sleep(0.01)
    return False


def _copy(timeout: float) -> str | None:
    """Ctrl+C in the foreground program; the copied text, or None when the
    clipboard did not change (nothing was selected)."""
    seq = user32.GetClipboardSequenceNumber()
    _chord(VK_CONTROL, VK_C)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.02)
        if user32.GetClipboardSequenceNumber() != seq:
            time.sleep(0.03)  # let the program finish writing every format
            return clipboard_text()
    return None


def grab(line_if_empty: bool = True, timeout: float = 0.6) -> tuple[str, str | None]:
    """The selected text of the foreground program, and the text that was on
    the clipboard before (to give to restore()). With nothing selected and
    line_if_empty, the line up to the caret is selected (Shift+Home) and
    taken. The text is "" when there is nothing to take."""
    wait_modifiers_up()
    before = clipboard_text()
    text = _copy(timeout)
    if not text and line_if_empty:
        _chord(VK_SHIFT, VK_HOME)
        time.sleep(0.05)
        text = _copy(timeout)
    return text or "", before


def restore(before: str | None) -> None:
    set_clipboard_text(before)


def replace(hwnd: int, text: str, before: str | None) -> bool:
    """Type text over the selection of window hwnd (paste), then put the
    earlier clipboard text back. False when the window is gone or would not
    come to the front."""
    if not user32.IsWindow(hwnd):
        return False
    user32.SetForegroundWindow(hwnd)
    deadline = time.monotonic() + 1.0
    while foreground() != hwnd and time.monotonic() < deadline:
        time.sleep(0.02)
    if foreground() != hwnd or not set_clipboard_text(text):
        return False
    wait_modifiers_up()
    time.sleep(0.05)
    _chord(VK_CONTROL, VK_V)
    time.sleep(0.4)  # the program reads the clipboard when it handles the key
    restore(before)
    return True
