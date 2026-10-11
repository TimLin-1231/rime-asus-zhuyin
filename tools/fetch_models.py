"""Download large model and data files (they are not committed to git):
language models into rime/, English word frequencies into third_party/symspell/.

    python tools/fetch_models.py
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MODELS = [
    # (file name, url, sha256, size)
    ("zh-hant-t-essay-bgw.gram",
     "https://github.com/lotem/rime-octagram-data/raw/hant/zh-hant-t-essay-bgw.gram",
     "0488ebd6688f900a39200f2b794f2f99bcbf1e8fc27280ae4a2324b08b1559c1",
     41415724),
    # Current language model (rime/grammar.yaml); CC-BY-4.0, amzxyz/RIME-LMDG.
    ("wanxiang-lts-zh-hant.gram",
     "https://github.com/amzxyz/RIME-LMDG/releases/download/LTS/wanxiang-lts-zh-hant.gram",
     "53b678ce17f1d80c75dd51976b697a5d1fc2cd6a13670652069b227b40157c98",
     410679340),
    # English word and word-pair frequencies for the suggestions
    # (tools/build_lang_data.py); MIT, wolfgarbe/SymSpell (Google Books
    # Ngram x SCOWL).
    ("third_party/symspell/frequency_dictionary_en_82_765.txt",
     "https://raw.githubusercontent.com/wolfgarbe/SymSpell/master/SymSpell/frequency_dictionary_en_82_765.txt",
     "c604e1121e398ae7c7fbf777f11e0a0f2fa66eda932cb9fba1321466cf3acd7b",
     1332881),
    ("third_party/symspell/frequency_bigramdictionary_en_243_342.txt",
     "https://raw.githubusercontent.com/wolfgarbe/SymSpell/master/SymSpell/frequency_bigramdictionary_en_243_342.txt",
     "fd892a160184101dd7ae807ac5a302d01fcea1c47304181a8ed7ed9c94545bcd",
     5137213),
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    for name, url, digest, size in MODELS:
        # a bare file name goes into rime/, a path is relative to the project
        dst = ROOT / name if "/" in name else ROOT / "rime" / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and dst.stat().st_size == size and sha256(dst) == digest:
            print(f"ok       {name}")
            continue
        print(f"fetching {name} ({size / 1e6:.1f} MB) from {url}")
        tmp = dst.with_suffix(dst.suffix + ".part")
        urllib.request.urlretrieve(url, tmp)
        if sha256(tmp) != digest:
            tmp.unlink()
            print(f"checksum mismatch for {name}", file=sys.stderr)
            return 1
        tmp.replace(dst)
        print(f"ok       {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
