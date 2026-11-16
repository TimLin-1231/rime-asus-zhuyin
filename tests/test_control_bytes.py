"""No tracked script or config file holds a stray control character.

    python tools/run_tests.py tests/test_control_bytes.py

A shell heredoc once turned the "\\a" of "C:\\Users\\User\\anaconda3" in
start_assist.bat into a BEL byte (0x07), and the launcher pointed at a path that
cannot exist for a month while every other test passed. This checks every file
that `git ls-files` lists with the extension .bat, .cmd, .ps1, .psm1, .yaml,
.yml, .lua or .py for the bytes 0x00-0x08, 0x0B, 0x0C and 0x0E-0x1F (tab, line
feed and carriage return are fine). A file with a UTF-16 byte-order mark is
decoded first and its characters are checked; a UTF-8 byte-order mark is
skipped. Each finding names the file, the line and the code point.

No librime; runs in well under a second.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS = {".bat", ".cmd", ".ps1", ".psm1", ".yaml", ".yml", ".lua", ".py"}
BAD = set(range(0x00, 0x09)) | {0x0B, 0x0C} | set(range(0x0E, 0x20))


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"],
                         capture_output=True, check=True, timeout=60).stdout
    return [n.decode("utf-8") for n in out.split(b"\0") if n]


def text_of(data: bytes) -> str:
    """The file's characters: UTF-16 by its byte-order mark, else UTF-8 with
    any byte-order mark removed (undecodable bytes are kept as U+FFFD, which is
    not a control character, so only real control bytes are reported)."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data.decode("utf-8", errors="replace")


def findings(name: str, data: bytes) -> list[str]:
    out = []
    for line_no, line in enumerate(text_of(data).split("\n"), 1):
        for col, ch in enumerate(line, 1):
            if ord(ch) in BAD:
                out.append(f"{name}:{line_no}:{col}: U+{ord(ch):04X}")
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    results: list[tuple[str, bool, str]] = []

    names = [n for n in tracked_files() if Path(n).suffix.lower() in EXTENSIONS]
    results.append(("git ls-files lists scripts and configs", len(names) > 0, f"{len(names)} files"))
    bad: list[str] = []
    for name in names:
        path = ROOT / name
        if not path.is_file():  # deleted in the working tree, not yet committed
            continue
        bad += findings(name, path.read_bytes())
    results.append((f"no control bytes in {len(names)} tracked files", not bad,
                    "; ".join(bad[:10]) + (f" ...and {len(bad) - 10} more" if len(bad) > 10 else "")))

    # The checker itself must see what it guards against.
    samples = [
        ("BEL in a path", b'start "" "C:\\Users\\User\x07naconda3\\pythonw.exe"', 1),
        ("UTF-16 file with a form feed", "\ufeffa\x0cb".encode("utf-16-le"), 1),
        ("UTF-8 BOM, tabs and CRLF are fine", b"\xef\xbb\xbfa\tb\r\nc\n", 0),
        ("Chinese text is fine", "島輝\t注音\r\n".encode("utf-8"), 0),
    ]
    for label, data, want in samples:
        got = len(findings("sample", data))
        results.append((f"checker: {label}", got == want, f"found {got}, expected {want}"))

    width = max(len(n) for n, _, _ in results)
    failed = 0
    for name, ok, detail in results:
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {'' if ok else detail}")
    print(f"\n{len(results) - failed}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
