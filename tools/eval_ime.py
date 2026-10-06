"""Quantitative evaluation of conversion quality (and keystroke latency).

Each variant runs in its own subprocess (librime initialises once per process)
against a staged copy of the config, with no learned user data, so runs are
reproducible. Results are appended to reports/eval_history.jsonl and printed
as a Markdown table, side by side with the previous run of each variant.

    python tools/eval_ime.py                       # all variants, all sets
    python tools/eval_ime.py --variants asus stock
    python tools/eval_ime.py --note "after stage 1"

Metrics
  SentAcc   whole input converts exactly to the expected text (top candidate)
  CharAcc   1 - CER(top candidate, expected), CER by Levenshtein distance
  Top1/Top9 word sets: expected word first / anywhere on the first page
  ms/key    mean librime process_key latency
Sets
  dev       tests/eval/sentences.tsv (Taiwan MOE readings); tune against this
  dev_typo  dev with one adjacent-key substitution per sentence
            (seeded; never on tone keys)
  test      tests/eval/sentences_test.tsv, held out: aggregate scores only
  words     tests/eval/private/asus_words.tsv (personal ASUS dictionary), if present
  moe_words tests/eval/private/moe_words.tsv: 400 common MOE words (essay >= 1000,
            seed 42) typed with Taiwan readings; held out from lexicon tuning
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "tests" / "eval"
REPORTS = ROOT / "reports"
HISTORY = REPORTS / "eval_history.jsonl"

WITH_CORRECTION = "patch:\n  translator/enable_correction: true\n"


def _empty_dict(name: str) -> str:
    return (f"---\nname: {name}\nversion: \"0\"\nsort: by_weight\n"
            "use_preset_vocabulary: false\n...\n")


NO_TW = {"terra_pinyin.tw.dict.yaml": _empty_dict("terra_pinyin.tw")}
NO_PERSONAL = {"terra_pinyin.personal.dict.yaml": _empty_dict("terra_pinyin.personal")}
VARIANTS = {
    # name: (config, schema, excluded files, extra files)
    "stock": ("stock", "bopomofo_tw", (), {}),                # stock Weasel Zhuyin
    "asus-gram": ("project", "asus_zhuyin", ("grammar.yaml",), {}),  # ablation: no LM
    "asus+corr": ("project", "asus_zhuyin", (),                # ablation: librime corrector
                  {"asus_zhuyin.custom.yaml": WITH_CORRECTION}),
    "asus-lex": ("project", "asus_zhuyin", (), {**NO_TW, **NO_PERSONAL}),  # = stage 1
    "asus-tw": ("project", "asus_zhuyin", (), NO_TW),          # ablation: no MOE supplement
    "asus-personal": ("project", "asus_zhuyin", (), NO_PERSONAL),  # ablation: no personal words
    "asus": ("project", "asus_zhuyin", (), {}),                # current project config
}
DEFAULT_VARIANTS = ["stock", "asus-lex", "asus-tw", "asus-personal", "asus"]

# QWERTY neighbours for typo injection (letters/digits/punct used by Dachen).
ROWS = ["1234567890-", "qwertyuiop", "asdfghjkl;", "zxcvbnm,./"]
TONE_KEYS = set(" 6347")


def _neighbours() -> dict[str, list[str]]:
    pos = {c: (r, i) for r, row in enumerate(ROWS) for i, c in enumerate(row)}
    out: dict[str, list[str]] = {}
    for c, (r, i) in pos.items():
        out[c] = [d for d, (r2, i2) in pos.items()
                  if d != c and abs(r2 - r) <= 1 and abs(i2 - i) <= 1
                  and d not in TONE_KEYS]
    return out


def inject_typo(keys: str, rng: random.Random) -> str:
    nb = _neighbours()
    idx = [i for i, k in enumerate(keys) if k not in TONE_KEYS and nb.get(k)]
    i = rng.choice(idx)
    return keys[:i] + rng.choice(nb[keys[i]]) + keys[i + 1:]


def load_set(path: Path) -> list[tuple[str, str]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            keys, _, text, *_ = line.split("\t")
            rows.append((keys, text))
    return rows


def build_sets() -> dict[str, list[tuple[str, str]]]:
    dev = load_set(EVAL / "sentences.tsv")
    rng = random.Random(20261006)
    sets = {"dev": dev,
            "dev_typo": [(inject_typo(k, rng), t) for k, t in dev],
            "test": load_set(EVAL / "sentences_test.tsv")}
    words = EVAL / "private" / "asus_words.tsv"
    if words.exists():
        sets["words"] = load_set(words)
    moe = EVAL / "private" / "moe_words.tsv"
    if moe.exists():
        sets["moe_words"] = load_set(moe)
    return sets


# -- worker (runs inside a subprocess) -------------------------------------
def worker(variant: str, sets_json: str) -> None:
    from rime_harness import open_rime
    config, schema, exclude, extra = VARIANTS[variant]
    rime = open_rime(f"eval-{variant}", config, schema, exclude=exclude, extra=extra)
    sets = json.loads(sets_json)
    out: dict[str, list] = {}
    for name, cases in sets.items():
        res = []
        for keys, _ in cases:
            rime.clear()
            t0 = time.perf_counter()
            rime.type(keys)
            dt_ms = (time.perf_counter() - t0) * 1000 / max(len(keys), 1)
            preview = rime.snapshot().preview
            if schema == "asus_zhuyin":
                rime.key("Down")  # expand the compact bubble to a full page
            page = [c for c, _ in rime.snapshot().candidates]
            res.append({"preview": preview, "page": page, "ms_per_key": dt_ms})
        out[name] = res
    rime.close()
    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))


# -- metrics -----------------------------------------------------------------
def levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def score(name: str, cases: list[tuple[str, str]], res: list[dict]) -> dict:
    n = len(cases)
    exact = sum(r["preview"] == t for (_, t), r in zip(cases, res))
    errs = sum(levenshtein(r["preview"], t) for (_, t), r in zip(cases, res))
    total = sum(len(t) for _, t in cases)
    m = {"n": n, "SentAcc": exact / n, "CharAcc": 1 - errs / total,
         "ms/key": statistics.mean(r["ms_per_key"] for r in res)}
    if name in ("words", "moe_words"):
        m = {"n": n, "Top1": exact / n,
             "Top9": sum(t in r["page"] for (_, t), r in zip(cases, res)) / n,
             "ms/key": m["ms/key"]}
    return m


def git_rev() -> str:
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "rime", "tools"],
                               cwd=ROOT, capture_output=True, text=True).stdout.strip()
        return rev + ("+dirty" if dirty else "")
    except OSError:
        return "?"


def sets_hash(sets: dict) -> str:
    return hashlib.sha256(json.dumps(sets, ensure_ascii=False, sort_keys=True)
                          .encode("utf-8")).hexdigest()[:12]


def previous_runs(set_hash: str) -> dict[str, dict]:
    """Last record per variant, only from runs on the identical eval sets."""
    last: dict[str, dict] = {}
    if HISTORY.exists():
        for line in HISTORY.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if rec.get("sets") == set_hash:
                last[rec["variant"]] = rec
    return last


def fmt(v: float, key: str) -> str:
    return f"{v:.2f}" if key == "ms/key" else f"{v * 100:.1f}%"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variants", nargs="+", default=DEFAULT_VARIANTS, choices=list(VARIANTS))
    ap.add_argument("--sets", nargs="+", default=None,
                    help="subset of eval sets (default: all); use 'dev' while tuning")
    ap.add_argument("--note", default="")
    ap.add_argument("--worker", help=argparse.SUPPRESS)
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()

    sets = build_sets()
    if args.sets:
        sets = {k: v for k, v in sets.items() if k in args.sets}
    if args.worker:
        worker(args.worker, sys.stdin.read())
        return

    set_hash = sets_hash(sets)
    prev = previous_runs(set_hash)
    stamp = dt.datetime.now().isoformat(timespec="seconds")
    rev = git_rev()
    records, failures = [], []
    for variant in args.variants:
        proc = subprocess.run([sys.executable, __file__, "--worker", variant],
                              input=json.dumps(sets, ensure_ascii=False).encode("utf-8"),
                              capture_output=True)
        if proc.returncode:
            raise SystemExit(f"{variant} failed:\n{proc.stderr.decode('utf-8', 'replace')}")
        res = json.loads(proc.stdout.decode("utf-8"))
        metrics = {name: score(name, sets[name], res[name]) for name in sets}
        records.append({"time": stamp, "rev": rev, "note": args.note, "sets": set_hash,
                        "variant": variant, "metrics": metrics})
        # Failures are reported for the dev set only; the test set stays blind.
        for (keys, text), r in zip(sets.get("dev", []), res.get("dev", [])):
            if r["preview"] != text:
                failures.append((variant, text, r["preview"]))

    # Markdown report: one table per set, previous run in parentheses.
    lines = [f"## Eval {stamp} @ {rev} sets={set_hash} {args.note}".rstrip(),
             "(previous run on the same eval sets in parentheses)", ""]
    for name in sets:
        keys = list(records[0]["metrics"][name].keys() - {"n"})
        keys.sort(key=["SentAcc", "CharAcc", "Top1", "Top9", "ms/key"].index)
        lines.append(f"### {name} (n={records[0]['metrics'][name]['n']})")
        lines.append("| variant | " + " | ".join(keys) + " |")
        lines.append("|---|" + "---:|" * len(keys))
        for rec in records:
            cells = []
            for k in keys:
                cur = rec["metrics"][name][k]
                old = prev.get(rec["variant"], {}).get("metrics", {}).get(name, {}).get(k)
                cells.append(fmt(cur, k) + (f" ({fmt(old, k)})" if old is not None else ""))
            lines.append(f"| {rec['variant']} | " + " | ".join(cells) + " |")
        lines.append("")
    report = "\n".join(lines)
    print(report)

    if not args.no_save:
        REPORTS.mkdir(exist_ok=True)
        with HISTORY.open("a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        detail = REPORTS / "last_failures.tsv"
        detail.write_text("variant\texpected\tgot\n" + "\n".join(
            "\t".join(x) for x in failures) + "\n", encoding="utf-8")
        print(f"(saved to {HISTORY.relative_to(ROOT)}; dev-set failures in "
              f"{detail.relative_to(ROOT)}; test-set failures are never shown)")


if __name__ == "__main__":
    main()
