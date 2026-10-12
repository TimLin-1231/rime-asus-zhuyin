"""Evaluate the continuation the input method offers after a pause.

    python tools/eval_continue.py
    python tools/eval_continue.py --sets continue --backends raw --note "..."

Sets (tests/eval/continue*.txt): text typed so far<TAB>acceptable next words.
Backends
  ngram   the word that most often follows the last word (stage 14b's table)
  raw     the local model continuing the plain text
  chat    the local model asked, as a chat, for the next few words
Metrics
  First1  the first word offered is one of the acceptable ones
  First3  (ngram only) one of the three words offered is
  words   mean length of the phrase offered
  s       mean seconds per request, p90 in parentheses
Results are appended to reports/assist_history.jsonl; misses of the dev set
are printed, the held-out set stays blind.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from assist import llm  # noqa: E402

EVAL = ROOT / "tests" / "eval"
HISTORY = ROOT / "reports" / "assist_history.jsonl"
FOLLOW = ROOT / "rime" / "opencc" / "asus_en_follow.txt"


def load(stem: str) -> list[tuple[str, set[str]]]:
    rows = []
    for line in (EVAL / f"{stem}.txt").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            text, want = line.split("\t")
            rows.append((text, {w.strip().lower() for w in want.split("|")}))
    return rows


def first_word(phrase: str) -> str:
    m = re.match(r"[A-Za-z0-9']+", phrase.strip())
    return m.group(0).lower() if m else ""


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backends", nargs="+", default=["ngram", "raw", "chat"], choices=["ngram", "raw", "chat"])
    ap.add_argument("--sets", nargs="+", default=["continue", "continue_test"])
    ap.add_argument("--note", default="")
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()

    follow = {}
    for line in FOLLOW.read_text(encoding="utf-8").splitlines():
        k, _, v = line.partition("\t")
        follow[k] = v.split(" ")
    model = llm.Model(port=8749) if set(args.backends) - {"ngram"} else None
    if model:
        model.start()
        model.next_words("Warm up")

    stamp = dt.datetime.now().isoformat(timespec="seconds")
    rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                         text=True).stdout.strip()
    records = []
    for stem in args.sets:
        rows = load(stem)
        print(f"### {stem} (n={len(rows)})")
        print("| backend | First1 | First3 | words | s |")
        print("|---|---:|---:|---:|---:|")
        for backend in args.backends:
            hit1 = hit3 = 0
            lengths, times, misses = [], [], []
            for text, wants in rows:
                t0 = time.perf_counter()
                if backend == "ngram":
                    last = re.findall(r"[A-Za-z']+", text)[-1].lower()
                    offered = [w.lower() for w in follow.get(last, [])[:3]]
                    phrase = offered[0] if offered else ""
                    hit3 += bool(wants & set(offered))
                else:
                    phrase = model.next_words(text + " ", backend)
                times.append(time.perf_counter() - t0)
                lengths.append(len(phrase.split()))
                ok = first_word(phrase) in wants
                hit1 += ok
                if not ok:
                    misses.append(f"    {text} -> {phrase!r}")
            n = len(rows)
            p90 = sorted(times)[int(n * 0.9)]
            m = {"n": n, "First1": hit1 / n, "words": statistics.mean(lengths),
                 "s": statistics.mean(times), "p90": p90}
            if backend == "ngram":
                m["First3"] = hit3 / n
            records.append({"time": stamp, "rev": rev, "note": args.note, "set": stem,
                            "backend": "continue-" + backend, "metrics": m})
            first3 = f"{m['First3']:.1%}" if "First3" in m else "-"
            print(f"| {backend} | {m['First1']:.1%} | {first3} | {m['words']:.1f} | {m['s']:.2f} ({p90:.2f}) |")
            if "test" not in stem:
                print("\n".join(misses), file=sys.stderr)
        print()
    if model:
        model.stop()
    if not args.no_save:
        with HISTORY.open("a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print("(saved to reports/assist_history.jsonl)")


if __name__ == "__main__":
    main()
