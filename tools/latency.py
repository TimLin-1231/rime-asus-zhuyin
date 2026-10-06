"""Per-keystroke latency profile (tail latency, cold start) of the project config.

Types the dev / mixed sets in a fresh process and times every process_key
call, so the occasional slow key shows up (the mean hides it).

    python tools/latency.py [--repeat 2]
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_ime import build_sets  # noqa: E402
from rime_harness import SHIFT_MASK, open_rime  # noqa: E402


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p / 100 * len(xs)))]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--name", default="latency")
    args = ap.parse_args()

    sets = build_sets()
    cases = sets["dev"] + sets["mixed"]
    t0 = time.perf_counter()
    rime = open_rime(args.name)
    print(f"startup (deploy + session): {time.perf_counter() - t0:.2f} s")

    samples: list[tuple[float, int, str, int]] = []  # (ms, round, keys, index)
    first_keys: list[float] = []
    for rnd in range(args.repeat):
        for keys, _ in cases:
            rime.clear()
            for i, ch in enumerate(keys):
                t = time.perf_counter()
                rime.key(ch, SHIFT_MASK if ch.isupper() else 0)
                ms = (time.perf_counter() - t) * 1000
                samples.append((ms, rnd, keys, i))
                if len(first_keys) < 10:
                    first_keys.append(ms)
    rime.close()

    for rnd in range(args.repeat):
        xs = [s[0] for s in samples if s[1] == rnd]
        print(f"round {rnd + 1}: n={len(xs)} mean={statistics.mean(xs):.2f} "
              f"p50={pct(xs, 50):.2f} p95={pct(xs, 95):.2f} p99={pct(xs, 99):.2f} "
              f"max={max(xs):.1f} ms")
    print("first 10 keys of the session (ms):", " ".join(f"{x:.1f}" for x in first_keys))
    print("slowest keystrokes:")
    for ms, rnd, keys, i in sorted(samples, reverse=True)[:12]:
        print(f"  {ms:8.1f} ms  round {rnd + 1}  key {keys[i]!r} at {i} of {keys!r}")


if __name__ == "__main__":
    main()
