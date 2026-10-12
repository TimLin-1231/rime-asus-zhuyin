"""Download what the writing helper (assist/) runs on. Nothing here is
committed to git; every file is checked against the publisher's SHA-256.

    python tools/fetch_assist.py            # everything (about 3.4 GB)
    python tools/fetch_assist.py harper     # only the grammar checker (12 MB)

  harper   third_party/harper/      Harper grammar checker (Apache-2.0),
                                    github.com/Automattic/harper v2.12.0
  llama    third_party/llama.cpp/   llama.cpp server, CUDA 12.4 build and
                                    its runtime DLLs (MIT), ggml-org/llama.cpp
  model    models/                  Qwen3.5-4B, 4-bit GGUF (Apache-2.0;
                                    converted by unsloth from Qwen/Qwen3.5-4B)
"""

from __future__ import annotations

import hashlib
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FILES = [
    # (group, destination, url, sha256, size, unzip into)
    ("harper", "third_party/harper/harper-cli-x86_64-pc-windows-msvc.zip",
     "https://github.com/Automattic/harper/releases/download/v2.12.0/harper-cli-x86_64-pc-windows-msvc.zip",
     "d5493b2a1fd3b1cdedbae4c2a842e9005e3179f0713edaf1aeee74c4c464484d", 12398555, "third_party/harper"),
    ("llama", "third_party/llama.cpp/llama-b11559-bin-win-cuda-12.4-x64.zip",
     "https://github.com/ggml-org/llama.cpp/releases/download/b11559/llama-b11559-bin-win-cuda-12.4-x64.zip",
     "4a885b80b26a213e86b774a580ac14fb210eba6220579051339c71b2bda1ecd7", 265207128, "third_party/llama.cpp"),
    ("llama", "third_party/llama.cpp/cudart-llama-bin-win-cuda-12.4-x64.zip",
     "https://github.com/ggml-org/llama.cpp/releases/download/b11559/cudart-llama-bin-win-cuda-12.4-x64.zip",
     "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6", 391443627, "third_party/llama.cpp"),
    ("model", "models/Qwen3.5-4B-Q4_K_M.gguf",
     "https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/e87f176479d0855a907a41277aca2f8ee7a09523/"
     "Qwen3.5-4B-Q4_K_M.gguf",
     "00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4", 2740937888, None),
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, tmp: Path, size: int) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "fetch_assist"})
    done, last = 0, time.monotonic()
    with urllib.request.urlopen(req, timeout=60) as resp, tmp.open("wb") as out:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if time.monotonic() - last > 15:
                last = time.monotonic()
                print(f"  {done / 1e6:8.0f} / {size / 1e6:.0f} MB", flush=True)


def main() -> int:
    groups = set(sys.argv[1:]) or {g for g, *_ in FILES}
    for group, name, url, digest, size, unzip in FILES:
        if group not in groups:
            continue
        dst = ROOT / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and dst.stat().st_size == size and sha256(dst) == digest:
            print(f"ok       {name}", flush=True)
        else:
            print(f"fetching {name} ({size / 1e6:.1f} MB) from {url}", flush=True)
            tmp = dst.with_suffix(dst.suffix + ".part")
            download(url, tmp, size)
            if sha256(tmp) != digest:
                tmp.unlink()
                print(f"checksum mismatch for {name}", file=sys.stderr)
                return 1
            tmp.replace(dst)
            print(f"ok       {name}", flush=True)
        if unzip:
            with zipfile.ZipFile(dst) as z:
                z.extractall(ROOT / unzip)
    return 0


if __name__ == "__main__":
    sys.exit(main())
