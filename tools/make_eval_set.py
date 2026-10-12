"""Build evaluation sets (keys -> expected text).

  tests/eval/sentences.txt  -> tests/eval/sentences.tsv       (dev set)
  tests/eval/sentences_test.txt -> sentences_test.tsv           (held-out test set)
  UserDictionary.txt (ASUS export, personal) -> tests/eval/private/asus_words.tsv

TSV columns: keys, zhuyin (for human review), expected text, category.
"""

from __future__ import annotations

import re
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
        syl += KEY_TO_SYMBOL.get(k, k)
        if k in " 6347":
            out.append(syl)
            syl = ""
    return " ".join(out + ([syl] if syl else []))


def build_sentences(stem: str = "sentences") -> int:
    """Keys use Taiwan MOE readings; rows where terra_pinyin disagrees are logged."""
    rows, category, diffs = [], "misc", []
    for line in (EVAL / f"{stem}.txt").read_text(encoding="utf-8").splitlines():
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
    (EVAL / f"{stem}.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    if stem == "sentences":  # never write per-sentence details for the held-out set
        (EVAL / "reading_diffs.txt").write_text("\n".join(diffs) + "\n", encoding="utf-8")
    print(f"{stem}: MOE vs terra_pinyin reading differs in {len(diffs)}/{len(rows)} sentences")
    return len(rows)


def build_mixed(stem: str) -> int:
    """Chinese with [English] words: Chinese -> MOE-reading keys, English typed as-is."""
    rows = []
    for line in (EVAL / f"{stem}.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        keys, shown = [], []
        for zh, en in re.findall(r"([^\[\]]+)|\[([^\]]+)\]", line):
            if en:
                en = en.split("=>")[0]  # [typed=>meant]: the misspelling is typed
                keys.append(en)
                shown.append(f"[{en}]")
            else:
                k = "".join(reading_keys(zh)[0])
                keys.append(k)
                shown.append(keys_to_display(k))
        text = re.sub(r"\[[^\]]*=>", "", line).replace("[", "").replace("]", "")  # ...the word meant is expected
        rows.append(f"{''.join(keys)}\t{' '.join(shown)}\t{text}\tmixed")
    (EVAL / f"{stem}.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return len(rows)


def build_particles(stem: str) -> int:
    """Sentences where {X} marks a character typed as ㄉㄜ˙ (2k7)."""
    rows = []
    for line in (EVAL / f"{stem}.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        text = line.replace("{", "").replace("}", "")
        forced = set()
        pos = 0
        for m in re.finditer(r"\{(.)\}|.", line):
            if m.group(1):
                forced.add(pos)
            pos += 1
        keys = reading_keys(text)[0]
        if len(keys) != len(text):
            raise ValueError(f"syllable/character mismatch in {line!r}")
        keys = ["2k7" if i in forced else k for i, k in enumerate(keys)]
        k = "".join(keys)
        rows.append(f"{k}\t{keys_to_display(k)}\t{text}\tparticles")
    (EVAL / f"{stem}.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return len(rows)


def _mixed_keys(line: str) -> tuple[str, str]:
    """Keys and display for text where [english] is typed as-is."""
    keys, shown = [], []
    for zh, en in re.findall(r"([^\[\]]+)|\[([^\]]+)\]", line):
        if en:
            keys.append(en)
            shown.append(f"[{en}]")
        else:
            k = "".join(reading_keys(zh)[0])
            keys.append(k)
            shown.append(keys_to_display(k))
    return "".join(keys), " ".join(shown)


def build_learn(stem: str) -> int:
    """Self-learning stream: TSV kind, keys, display, text, group.

    Keys are those of the (first) text: T/P/C text, A right text, Q context,
    E the English word itself.
    """
    rows, group = [], ""
    for line in (EVAL / f"{stem}.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("# group "):
            group = line[len("# group "):]
            continue
        if not line or line.startswith("#"):
            continue
        kind, _, body = line.partition(" ")
        if kind not in "TAEPCQH" or len(kind) != 1:
            raise ValueError(f"bad line {line!r}")
        first = body.split("|")[0]
        if kind == "E":
            keys, shown = first, f"[{first}]"
        else:
            keys, shown = _mixed_keys(first)
        rows.append(f"{kind}\t{keys}\t{shown}\t{body.replace('[', '').replace(']', '')}\t{group}")
    (EVAL / f"{stem}.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
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
    print("dev sentences:", build_sentences("sentences"))
    print("test sentences (held out):", build_sentences("sentences_test"))
    print("mixed dev:", build_mixed("mixed"))
    print("mixed test (held out):", build_mixed("mixed_test"))
    print("misspelt English in Chinese dev / test:", build_mixed("mixed_fix"), build_mixed("mixed_fix_test"))
    print("capitals of English words dev / test:", build_mixed("en_case"), build_mixed("en_case_test"))
    print("particles dev:", build_particles("particles"))
    print("particles test (held out):", build_particles("particles_test"))
    print("numbers dev:", build_mixed("numbers"))
    print("numbers dev 2:", build_mixed("numbers_more"))
    print("ambiguous numbers dev / test:", build_mixed("numbers_amb"), build_mixed("numbers_amb_test"))
    print("numbers test (held out):", build_mixed("numbers_test"))
    print("edit dev:", build_mixed("edit"))
    print("edit test (held out):", build_mixed("edit_test"))
    print("learning dev:", build_learn("learn"))
    print("learning test (held out):", build_learn("learn_test"))
    print("learning with history dev:", build_learn("learn_hist"))
    print("learning with history test (held out):", build_learn("learn_hist_test"))
    print("asus words (private):", build_asus_words())
