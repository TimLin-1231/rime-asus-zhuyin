"""Download large model files into rime/ (they are not committed to git).

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
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    for name, url, digest, size in MODELS:
        dst = ROOT / "rime" / name
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
