"""Evaluate the writing helper's grammar correction (assist/).

    python tools/eval_assist.py                      # harper, llm, both; all sets
    python tools/eval_assist.py --backends harper --sets grammar
    python tools/eval_assist.py --note "what changed"

Sets (tests/eval/grammar*.txt): sentence<TAB>correct sentence( | another ...);
a line with both sides the same is a correct sentence.
Backends
  harper   every first suggestion of Harper applied
  llm      the local model's corrected text
  both     Harper first, then the model on its result (what the helper shows)
Metrics
  Fixed    faulty sentences that came out as one of the correct ones
  Other    faulty sentences changed into something else (it may still be
           right: only the listed corrections count)
  Missed   faulty sentences that came back unchanged
  Harmed   correct sentences that were changed (lower is better)
  s/sent   mean seconds per sentence, p90 in parentheses
Results are appended to reports/assist_history.jsonl; failures of the dev set
go to reports/assist_failures.tsv, the held-out set stays blind.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from assist import harper, llm  # noqa: E402

EVAL = ROOT / "tests" / "eval"
HISTORY = ROOT / "reports" / "assist_history.jsonl"


def load(stem: str) -> list[tuple[str, list[str]]]:
    rows = []
    for line in (EVAL / f"{stem}.txt").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            src, want = line.split("\t")
            rows.append((src, [w.strip() for w in want.split(" | ")]))
    return rows


def norm(s: str) -> str:
    return " ".join(s.replace("’", "'").split())


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backends", nargs="+", default=["harper", "llm", "both"], choices=["harper", "llm", "both"])
    ap.add_argument("--sets", nargs="+", default=["grammar", "grammar_test"])
    ap.add_argument("--note", default="")
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()

    model = llm.Model() if any(b != "harper" for b in args.backends) else None
    if model:
        model.start()
        model.fix("Warm up.")  # the first request loads the prompt cache

    def run(backend: str, text: str) -> str:
        if backend in ("harper", "both"):
            text = harper.apply(text, harper.lint(text))
        if backend in ("llm", "both"):
            text = model.fix(text)
        return text

    stamp = dt.datetime.now().isoformat(timespec="seconds")
    rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                         text=True).stdout.strip()
    records, failures = [], []
    for stem in args.sets:
        rows = load(stem)
        print(f"### {stem} (n={len(rows)})")
        print("| backend | Fixed | Other | Missed | Harmed | s/sent |")
        print("|---|---:|---:|---:|---:|---:|")
        for backend in args.backends:
            fixed = other = missed = harmed = nf = nc = 0
            times = []
            for src, wants in rows:
                t0 = time.perf_counter()
                got = norm(run(backend, src))
                times.append(time.perf_counter() - t0)
                if wants == [src]:
                    nc += 1
                    if got != norm(src):
                        harmed += 1
                        failures.append((stem, backend, "harmed", src, got))
                else:
                    nf += 1
                    if got in map(norm, wants):
                        fixed += 1
                    elif got == norm(src):
                        missed += 1
                        failures.append((stem, backend, "missed", src, got))
                    else:
                        other += 1
                        failures.append((stem, backend, "other", src, got))
            p90 = sorted(times)[int(len(times) * 0.9)]
            m = {"n": len(rows), "Fixed": fixed / max(nf, 1), "Other": other / max(nf, 1),
                 "Missed": missed / max(nf, 1), "Harmed": harmed / max(nc, 1),
                 "s/sent": statistics.mean(times), "p90": p90}
            records.append({"time": stamp, "rev": rev, "note": args.note, "set": stem,
                            "backend": backend, "metrics": m})
            print(f"| {backend} | {m['Fixed']:.1%} | {m['Other']:.1%} | {m['Missed']:.1%} | "
                  f"{m['Harmed']:.1%} | {m['s/sent']:.2f} ({p90:.2f}) |")
        print()
    if model:
        model.stop()
    if not args.no_save:
        with HISTORY.open("a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        (ROOT / "reports" / "assist_failures.tsv").write_bytes(("set\tbackend\twhat\tsentence\tgot\n" + "\n".join(
            "\t".join(x) for x in failures if "test" not in x[0]) + "\n").encode("utf-8"))
        print("(saved to reports/assist_history.jsonl; dev-set failures in reports/assist_failures.tsv)")


if __name__ == "__main__":
    main()
