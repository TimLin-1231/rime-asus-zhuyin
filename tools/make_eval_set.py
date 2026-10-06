"""Build evaluation sets (keys -> expected text).

  tests/eval/sentences.txt  -> tests/eval/sentences.tsv
  UserDictionary.txt (ASUS export, personal) -> tests/eval/private/asus_words.tsv

TSV columns: keys, zhuyin (for human review), expected text, category.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from zhuyin import (KEY_TO_SYMBOL, pinyin_to_keys, reading,  # noqa: E402
                    reading_keys, symbols_to_keys)

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "tests" / "eval"


def keys_to_display(keys: str) -> str:
    out, syl = [], ""
    for k in keys:
        syl += KEY_TO_SYMBOL[k]
        if k in " 6347":
            out.append(syl)
            syl = ""
    return " ".join(out + ([syl] if syl else []))


def build_sentences() -> int:
    """Keys use Taiwan MOE readings; rows where terra_pinyin disagrees are logged."""
    rows, category, diffs = [], "misc", []
    for line in (EVAL / "sentences.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("# ---"):
            category = line.strip("# -").split("/")[0].strip()
            continue
        if line.startswith("#"):
            continue
        text, _, override = line.partition("\t")
        keys = (symbols_to_keys(override) if override
                else "".join(reading_keys(text)[0]))
        terra = "".join(pinyin_to_keys(p) for p in reading(text))
        if keys != terra:
            diffs.append(f"{text}\n  moe:   {keys_to_display(keys)}\n  terra: {keys_to_display(terra)}")
        rows.append(f"{keys}\t{keys_to_display(keys)}\t{text}\t{category}")
    (EVAL / "sentences.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (EVAL / "reading_diffs.txt").write_text("\n".join(diffs) + "\n", encoding="utf-8")
    print(f"MOE vs terra_pinyin reading differs in {len(diffs)}/{len(rows)} sentences")
    return len(rows)


def build_asus_words() -> int:
    src = ROOT / "UserDictionary.txt"
    if not src.exists():
        return 0
    rows = []
    for line in src.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        word, _, zhuyin = line.partition("  ")
        keys = symbols_to_keys(zhuyin.strip())
        rows.append(f"{keys}\t{keys_to_display(keys)}\t{word.strip()}\tasus_user_dict")
    out = EVAL / "private" / "asus_words.tsv"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return len(rows)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print("sentences:", build_sentences())
    print("asus words (private):", build_asus_words())
