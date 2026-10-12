"""The input method's line to the model: continuations after a pause.

rime/lua/asus_english.lua cannot wait, has no timer and no network; it can
open a named pipe as a file. So, in English mode:

  1. after a word and Space it writes "<n>\\t<the text typed so far>" to the
     pipe  \\\\.\\pipe\\<name>_in  (and "0" when the user types on);
  2. when no newer request came for `pause` seconds, the model is asked for
     the next few words (assist/llm.py next_words);
  3. the answer "<n>\\t<phrase>" is kept ready on  \\\\.\\pipe\\<name>_out
     and the F24 key is sent to the program in front: the input method takes
     that key, reads the pipe and shows the phrase in its bubble (Tab types
     it).

The text only exists in memory, on this machine: pipes, not files, and they
refuse remote clients. Without this program running the pipe is not there
and the input method simply offers no continuation.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import threading
import time

from . import selection

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateNamedPipeW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD,
                                      wt.DWORD, wt.LPVOID]
kernel32.CreateNamedPipeW.restype = wt.HANDLE
kernel32.ConnectNamedPipe.argtypes = [wt.HANDLE, wt.LPVOID]
kernel32.DisconnectNamedPipe.argtypes = [wt.HANDLE]
kernel32.ReadFile.argtypes = [wt.HANDLE, wt.LPVOID, wt.DWORD, ctypes.POINTER(wt.DWORD), wt.LPVOID]
kernel32.WriteFile.argtypes = [wt.HANDLE, wt.LPCVOID, wt.DWORD, ctypes.POINTER(wt.DWORD), wt.LPVOID]
kernel32.FlushFileBuffers.argtypes = [wt.HANDLE]
kernel32.CloseHandle.argtypes = [wt.HANDLE]

PIPE_ACCESS_INBOUND, PIPE_ACCESS_DUPLEX, FIRST_INSTANCE = 0x1, 0x3, 0x00080000
INSTANCES = 4  # of each pipe: one is listening while another is being served
PIPE_REJECT_REMOTE_CLIENTS = 0x8
ERROR_PIPE_CONNECTED = 535
INVALID_HANDLE = wt.HANDLE(-1).value
VK_F24 = 0x87
NAME = "asus_zhuyin_assist"
MAX_REQUEST = 4096


def _pipe(name: str, access: int, first: bool) -> int:
    handle = kernel32.CreateNamedPipeW(f"\\\\.\\pipe\\{name}", access | (FIRST_INSTANCE if first else 0),
                                       PIPE_REJECT_REMOTE_CLIENTS, INSTANCES, MAX_REQUEST, MAX_REQUEST, 0, None)
    if handle == INVALID_HANDLE or not handle:
        raise OSError(f"cannot create the pipe {name} (error {ctypes.get_last_error()}): "
                      "is another writing helper running?")
    return handle


def _connect(handle: int) -> bool:
    return bool(kernel32.ConnectNamedPipe(handle, None)) or ctypes.get_last_error() == ERROR_PIPE_CONNECTED


def press_f24(hwnd: int) -> None:
    """Tell the input method an answer is ready - only while the program the
    request came from is still in front."""
    if hwnd and selection.foreground() == hwnd:
        selection._chord(VK_F24)


class Bridge:
    def __init__(self, model, lock: threading.Lock, pause: float = 0.6, name: str = NAME, notify=press_f24):
        self.model, self.lock, self.pause, self.notify = model, lock, pause, notify
        # (The out pipe is duplex although nothing is read from it: the C
        # runtime behind Lua's io.open cannot open a write-only pipe to read.)
        pipes = [(self._serve_in, _pipe(name + "_in", PIPE_ACCESS_INBOUND, i == 0)) for i in range(INSTANCES)]
        pipes += [(self._serve_out, _pipe(name + "_out", PIPE_ACCESS_DUPLEX, i == 0)) for i in range(INSTANCES)]
        self.answer = b""            # "<n>\t<phrase>", what the out pipe hands over
        self.request: tuple[int, str, float, int] | None = None  # n, text, when, window
        self.wake = threading.Event()
        self.asked = self.answered = 0  # counters, for tests and curiosity
        for target, handle in pipes:
            threading.Thread(target=target, args=(handle,), daemon=True).start()
        threading.Thread(target=self._work, daemon=True).start()

    # -- the two pipes -----------------------------------------------------------
    def _serve_in(self, pipe: int) -> None:
        buf = ctypes.create_string_buffer(MAX_REQUEST)
        got = wt.DWORD()
        while True:
            if not _connect(pipe):
                time.sleep(0.05)
                continue
            data = b""
            while len(data) < MAX_REQUEST and kernel32.ReadFile(pipe, buf, MAX_REQUEST, ctypes.byref(got), None):
                if not got.value:
                    break
                data += buf.raw[:got.value]
            kernel32.DisconnectNamedPipe(pipe)
            self._received(data)

    def _received(self, data: bytes) -> None:
        number, _, text = data.decode("utf-8", "replace").partition("\t")
        if not number.isdigit() or int(number) == 0 or not text.strip():
            self.request = None  # "0": the user typed on
            return
        self.request = (int(number), text, time.monotonic(), selection.foreground())
        self.asked += 1
        self.wake.set()

    def _serve_out(self, pipe: int) -> None:
        done = wt.DWORD()
        while True:
            if not _connect(pipe):
                time.sleep(0.05)
                continue
            # One line: Lua's read fails when it runs into the closed end of
            # the pipe, so it must find its newline first.
            data = self.answer + b"\n"
            kernel32.WriteFile(pipe, data, len(data), ctypes.byref(done), None)
            kernel32.FlushFileBuffers(pipe)
            kernel32.DisconnectNamedPipe(pipe)

    # -- pause, then ask the model ------------------------------------------------
    def _work(self) -> None:
        while True:
            self.wake.wait()
            self.wake.clear()
            request = self.request
            while request is not None:
                wait = request[2] + self.pause - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                if self.request is request:
                    break
                request = self.request  # a newer one, or None: the user typed on
            if request is None:
                continue
            number, text, _, hwnd = request
            try:
                with self.lock:
                    phrase = self.model.next_words(text)
            except Exception as e:  # noqa: BLE001 - the model failing must not end the thread
                print(f"bridge: {e}", file=sys.stderr)
                continue
            if phrase and self.request is request:
                self.answer = f"{number}\t{phrase}".encode("utf-8")
                self.answered += 1
                self.notify(hwnd)
