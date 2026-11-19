"""Read and set a process's CPU, I/O and memory priority (Windows).

A process started by a background scheduled task (Task Scheduler priority 7,
the default) gets below-normal CPU, low I/O and memory priority 2, and passes
all three on to the processes it starts. The input method's server must not
run like that: under load its keys wait for seconds and the front end gives
them to the application as plain letters. NORMAL_PRIORITY_CLASS at creation
fixes only the CPU part, so set_normal() sets all three after the start.

    python tools/procprio.py DaoHuiServer.exe     show the three priorities
"""

from __future__ import annotations

import ctypes as C
import ctypes.wintypes as W
import subprocess
import sys

PROCESS_SET_INFORMATION = 0x0200
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
NORMAL_PRIORITY_CLASS = 0x20
CLASS_NAMES = {0x40: "IDLE", 0x4000: "BELOW_NORMAL", 0x20: "NORMAL", 0x8000: "ABOVE_NORMAL",
               0x80: "HIGH", 0x100: "REALTIME"}
IO_NAMES = {0: "VeryLow", 1: "Low", 2: "Normal", 3: "High", 4: "Critical"}
# NtQuery/SetInformationProcess classes
PROCESS_IO_PRIORITY = 33
PROCESS_PAGE_PRIORITY = 39
IO_NORMAL = 2
PAGE_NORMAL = 5

_k32 = C.WinDLL("kernel32", use_last_error=True)
_nt = C.WinDLL("ntdll")
_k32.OpenProcess.restype = W.HANDLE
_k32.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
_k32.CloseHandle.argtypes = [W.HANDLE]
_k32.GetPriorityClass.argtypes = [W.HANDLE]
_k32.GetPriorityClass.restype = W.DWORD
_k32.SetPriorityClass.argtypes = [W.HANDLE, W.DWORD]
_nt.NtQueryInformationProcess.argtypes = [W.HANDLE, C.c_int, C.c_void_p, C.c_ulong, C.c_void_p]
_nt.NtQueryInformationProcess.restype = C.c_long
_nt.NtSetInformationProcess.argtypes = [W.HANDLE, C.c_int, C.c_void_p, C.c_ulong]
_nt.NtSetInformationProcess.restype = C.c_long


def _open(pid: int, access: int) -> int:
    h = _k32.OpenProcess(access, False, pid)
    if not h:
        raise OSError(C.get_last_error(), f"cannot open process {pid}")
    return h


def state(pid: int) -> dict:
    """{'cpu': 'NORMAL', 'io': 'Normal', 'page': 5}"""
    h = _open(pid, PROCESS_QUERY_LIMITED_INFORMATION)
    try:
        io, page = C.c_ulong(99), C.c_ulong(99)
        _nt.NtQueryInformationProcess(h, PROCESS_IO_PRIORITY, C.byref(io), 4, None)
        _nt.NtQueryInformationProcess(h, PROCESS_PAGE_PRIORITY, C.byref(page), 4, None)
        cls = _k32.GetPriorityClass(h)
        return {"cpu": CLASS_NAMES.get(cls, hex(cls)), "io": IO_NAMES.get(io.value, str(io.value)),
                "page": page.value}
    finally:
        _k32.CloseHandle(h)


def is_normal(s: dict) -> bool:
    return s["cpu"] in ("NORMAL", "ABOVE_NORMAL", "HIGH") and s["io"] in ("Normal", "High") and s["page"] >= PAGE_NORMAL


def set_normal(pid: int) -> list[str]:
    """Normal CPU, I/O and memory priority for pid; returns what failed."""
    h = _open(pid, PROCESS_SET_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION)
    failed = []
    try:
        if not _k32.SetPriorityClass(h, NORMAL_PRIORITY_CLASS):
            failed.append(f"cpu (error {C.get_last_error()})")
        for name, cls, value in (("io", PROCESS_IO_PRIORITY, IO_NORMAL), ("page", PROCESS_PAGE_PRIORITY, PAGE_NORMAL)):
            v = C.c_ulong(value)
            status = _nt.NtSetInformationProcess(h, cls, C.byref(v), 4)
            if status != 0:
                failed.append(f"{name} (status {status & 0xFFFFFFFF:#x})")
    finally:
        _k32.CloseHandle(h)
    return failed


_adv = C.WinDLL("advapi32", use_last_error=True)
_adv.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]
_adv.GetTokenInformation.argtypes = [W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)]
TOKEN_QUERY = 0x0008
TOKEN_ELEVATION = 20  # TOKEN_INFORMATION_CLASS


def elevated(pid: int) -> bool | None:
    """Whether pid runs with an administrator (elevated) token; None if that
    cannot be read. (An elevated installer used to start the server
    elevated: it then wrote the user's learning data as an administrator.)"""
    try:
        h = _open(pid, PROCESS_QUERY_LIMITED_INFORMATION)
    except OSError:
        return None
    tok = W.HANDLE()
    try:
        if not _adv.OpenProcessToken(h, TOKEN_QUERY, C.byref(tok)):
            return None
        try:
            value, size = W.DWORD(), W.DWORD()
            if not _adv.GetTokenInformation(tok, TOKEN_ELEVATION, C.byref(value), 4, C.byref(size)):
                return None
            return bool(value.value)
        finally:
            _k32.CloseHandle(tok)
    finally:
        _k32.CloseHandle(h)


def pids(image: str) -> list[int]:
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {image}", "/NH", "/FO", "CSV"],
                         capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    found = []
    for line in out.decode("mbcs", "replace").splitlines():
        cols = [c.strip('"') for c in line.split('","')]
        if len(cols) > 1 and cols[0].lower() == image.lower() and cols[1].isdigit():
            found.append(int(cols[1]))
    return found


def main() -> int:
    images = sys.argv[1:] or ["DaoHuiServer.exe"]
    bad = 0
    for image in images:
        for pid in pids(image) or [None]:
            if pid is None:
                print(f"{image}: not running")
                continue
            s = state(pid)
            ok = is_normal(s)
            bad += not ok
            print(f"{image} pid={pid} cpu={s['cpu']} io={s['io']} page={s['page']} {'ok' if ok else 'LOW'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
