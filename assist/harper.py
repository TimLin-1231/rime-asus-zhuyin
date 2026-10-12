"""Grammar and spelling findings from Harper (third_party/harper/harper-cli.exe,
fetched by tools/fetch_assist.py). Rule based, local, about half a second a
call; it explains what it finds but only knows the mistakes it has rules for.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARPER = ROOT / "third_party" / "harper" / "harper-cli.exe"
USER_DICT = ROOT / "lexicon" / "english_words.txt"  # the user's own English words
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW

# Rules that are about taste, not mistakes, in chat and email.
IGNORED = ("LongSentences", "SpelledNumbers", "BoringWords", "UseTitleCase")


@dataclass
class Finding:
    start: int            # character offsets in the text
    end: int
    text: str             # what is there now
    message: str
    rule: str
    kind: str
    replacements: list[str]   # best first; "" removes the text


def available() -> bool:
    return HARPER.exists()


def _replacement(suggestion: str, matched: str) -> str | None:
    m = re.fullmatch(r"Replace with: “(.*)”", suggestion, re.S)
    if m:
        return m.group(1)
    if suggestion == "Remove error":
        return ""
    m = re.fullmatch(r"Insert “(.*)”", suggestion, re.S)
    if m:
        return matched + m.group(1)
    return None


def lint(text: str, timeout: float = 10.0) -> list[Finding]:
    """Findings for text, in reading order. [] when Harper is missing."""
    if not available() or not text.strip():
        return []
    cmd = [str(HARPER), "lint", "--format", "json", "--no-color", "--ignore", ",".join(IGNORED)]
    if USER_DICT.exists():
        cmd += ["--user-dict-path", str(USER_DICT)]
    proc = subprocess.run(cmd, input=text.encode("utf-8"), capture_output=True, timeout=timeout,
                          creationflags=NO_WINDOW)
    try:
        files = json.loads(proc.stdout.decode("utf-8"))
    except ValueError:
        return []
    out = []
    for item in files[0]["lints"] if files else []:
        start, end = item["span"]["char_start"], item["span"]["char_end"]
        matched = text[start:end]
        if not matched.isascii():
            continue  # Chinese punctuation in mixed text is not an English mistake
        repl = [r for r in (_replacement(s, matched) for s in item.get("suggestions", [])) if r is not None]
        out.append(Finding(start, end, matched, item["message"], item["rule"], item["kind"], repl))
    out.sort(key=lambda f: (f.start, f.end))
    return out


def apply(text: str, findings: list[Finding]) -> str:
    """Text with the first suggestion of every finding applied (overlapping
    findings: the earlier one)."""
    out, pos = [], 0
    for f in findings:
        if not f.replacements or f.start < pos:
            continue
        out.append(text[pos:f.start])
        out.append(f.replacements[0])
        pos = f.end
    out.append(text[pos:])
    return "".join(out)
