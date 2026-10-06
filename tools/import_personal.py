"""Merge personal vocabulary exports into lexicon/personal_words.txt.

Sources (all optional, read-only):
  UserDictionary.txt                       ASUS Smart Input export (UTF-8)
  D:\\Document\\新注音詞彙\\1.txt            Microsoft Bopomofo export (UTF-16)
  D:\\Document\\小狼豪輸入法\\custom_phrase.txt  old Weasel custom phrases

Output format (one entry per line, "#" comments allowed):
  word<TAB>zhuyin<TAB>source
Existing lines in personal_words.txt are kept (user edits win); only new
(word, reading) pairs are appended. English entries from the old Weasel file
go to lexicon/english_words.txt for stage 3; symbol entries (e.g. 正負 -> ±)
are kept as low-priority personal entries with source "symbol".
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from zhuyin import split_syllables  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LEXICON = ROOT / "lexicon"
PERSONAL = LEXICON / "personal_words.txt"
ENGLISH = LEXICON / "english_words.txt"

ASUS = ROOT / "UserDictionary.txt"
MS = Path(r"D:\Document\新注音詞彙\1.txt")
OLD_WEASEL = Path(r"D:\Document\小狼豪輸入法\custom_phrase.txt")

ZHUYIN_RE = re.compile(r"^[ㄅ-ㄩˉˊˇˋ˙ ]+$")
HAN_RE = re.compile(r"^[\u3400-\u9fff\uf900-\ufaff]+$")


def norm_zhuyin(z: str) -> str:
    """Normalise to space-separated syllables, tone mark last (˙ moved)."""
    z = z.replace("\u3000", "")
    sylls = z.split() if " " in z.strip() else split_syllables(z)
    out = []
    for s in sylls:
        if s.startswith("˙"):
            s = s[1:] + "˙"
        out.append(s)
    return " ".join(out)


def read_asus() -> list[tuple[str, str, str]]:
    if not ASUS.exists():
        return []
    rows = []
    for line in ASUS.read_text(encoding="utf-8-sig").splitlines():
        word, _, z = line.partition("  ")
        if word.strip() and z.strip():
            rows.append((word.strip(), norm_zhuyin(z.strip()), "asus"))
    return rows


def read_ms() -> list[tuple[str, str, str]]:
    if not MS.exists():
        return []
    rows = []
    for line in MS.read_text(encoding="utf-16").splitlines():
        word, _, z = line.partition("  ")
        if word.strip() and z.strip():
            rows.append((word.strip(), norm_zhuyin(z.strip()), "ms_bopomofo"))
    return rows


def read_old_weasel() -> tuple[list[tuple[str, str, str]], list[str]]:
    if not OLD_WEASEL.exists():
        return [], []
    rows, english = [], []
    for line in OLD_WEASEL.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        text, code = parts[0].strip(), parts[1].strip()
        if ZHUYIN_RE.match(code) and HAN_RE.match(text):
            rows.append((text, norm_zhuyin(code), "old_weasel"))
        elif re.fullmatch(r"[A-Za-z][A-Za-z0-9\-_.]*", text):
            english.append(text)
        elif HAN_RE.match(text) and not HAN_RE.match(code):
            # Old file stored these reversed: "正負<TAB>±" meant 正負 -> ±.
            rows.append((code, "@" + text, "symbol"))
    return rows, english


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    LEXICON.mkdir(exist_ok=True)
    existing: list[str] = []
    seen: set[tuple[str, str]] = set()
    if PERSONAL.exists():
        existing = PERSONAL.read_text(encoding="utf-8").splitlines()
        for line in existing:
            if line and not line.startswith("#"):
                w, z, *_ = line.split("\t") + [""]
                seen.add((w, z))
    else:
        existing = [
            "# Personal vocabulary (local only, not in git).",
            "# word<TAB>zhuyin<TAB>source ; zhuyin may be empty (looked up automatically).",
            "# Symbol entries use @reading-word, e.g.  ±<TAB>@正負<TAB>symbol",
        ]
    old_rows, english = read_old_weasel()
    added = 0
    for word, z, src in read_asus() + read_ms() + old_rows:
        if (word, z) in seen:
            continue
        seen.add((word, z))
        existing.append(f"{word}\t{z}\t{src}")
        added += 1
    PERSONAL.write_text("\n".join(existing) + "\n", encoding="utf-8")
    if english:
        prev = set(ENGLISH.read_text(encoding="utf-8").split()) if ENGLISH.exists() else set()
        ENGLISH.write_text("\n".join(sorted(prev | set(english))) + "\n", encoding="utf-8")
    print(f"personal: +{added} entries -> {PERSONAL.relative_to(ROOT)} "
          f"({len(seen)} total); english words: {len(english)}")


if __name__ == "__main__":
    main()
