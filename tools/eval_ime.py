"""Quantitative evaluation of conversion quality (and keystroke latency).

Each variant runs in its own subprocess (librime initialises once per process)
against a staged copy of the config, with no learned user data, so runs are
reproducible. Results are appended to reports/eval_history.jsonl and printed
as a Markdown table, side by side with the previous run of each variant.

    python tools/eval_ime.py                       # all variants, all sets
    python tools/eval_ime.py --variants asus stock
    python tools/eval_ime.py --note "after stage 1"

Metrics
  SentAcc   whole input converts exactly to the expected text (top candidate,
            as committed with Enter since stage 10; before, as previewed)
  CharAcc   1 - CER(top candidate, expected), CER by Levenshtein distance
  Top1/Top9 word sets: expected word first / anywhere on the first page
  ms/key    mean librime process_key latency
Sets
  dev       tests/eval/sentences.tsv (Taiwan MOE readings); tune against this
  dev_toneless  dev typed without tone keys; ASCII = share of outputs with
            Latin letters (English leaking into Chinese), lower is better
  dev_typo  dev with one adjacent-key substitution per sentence
            (seeded; never on tone keys)
  test_typo test with one adjacent-key substitution per sentence (held out)
  dev_typo_end / test_typo_end  the substitution is in the last syllable
  test      tests/eval/sentences_test.tsv, held out: aggregate scores only
  dev_stray / test_stray  one extra neighbouring key inserted per sentence (seed 3)
  dev_omit / test_omit    one key left out per sentence (any key but the last)
  dev_swap / test_swap    two neighbouring keys typed in the wrong order
  mixed / mixed_test      Chinese with English words (tests/eval/mixed*.txt)
  particles / particles_test  的/得/地 (typed ㄉㄜ˙) and 在/再; PartAcc = share
            of those positions converted correctly
  numbers / numbers_test  Chinese with numbers typed on the top-row digit keys
  english_ctx / english_ctx_test  a previous English word, then a prefix: is the
            word that usually follows the first guess (tests/eval/english_ctx*.txt)
  english / english_test  English prefixes and misspellings (tests/eval/english*.txt);
            Guess1 / Guess3 = expected word first / in the first three English
            guesses of the menu, Keep = correct words stay as typed
  autofix / autofix_more / autofix_test  English mode, a word and Space
            (tests/eval/autofix*.txt): Fix = misspellings corrected, Wrong =
            misspellings turned into another word, FalseFix = words that
            should stay as typed and were changed (lower is better)
  english_next / english_next_test  English mode, a word and Space: Next1 /
            Next3 = the expected next word is offered first / among the three
  dev_partial  partial inputs of dev sentences ending on a digit key; Digits =
            share whose preview shows a digit (a number leaking into Chinese)
  words     tests/eval/private/asus_words.tsv (personal ASUS dictionary), if present
  moe_words tests/eval/private/moe_words.tsv: 400 common MOE words (essay >= 1000,
            seed 42) typed with Taiwan readings; held out from lexicon tuning
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import random
import re
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


NO_LANG = {"asus_zhuyin.custom.yaml": (
    "patch:\n  engine/segmentors: [ascii_segmentor, matcher, abc_segmentor, "
    "punct_segmentor, fallback_segmentor]\n")}
NO_TW = {"terra_pinyin.tw.dict.yaml": _empty_dict("terra_pinyin.tw")}
NO_MCB = {"terra_pinyin.mcb.dict.yaml": _empty_dict("terra_pinyin.mcb")}
NO_EN_PREFIX = {"opencc/asus_en_prefix.txt": "zzzzzz\t0:zzzzzz\n"}  # English autocomplete off
# Wanxiang zh-hant model (amzxyz/RIME-LMDG, CC-BY-4.0) with its recommended
# settings, and with the current octagram settings (only the model swapped).
WX_RECOMMENDED = {"grammar.yaml": (
    "hant:\n  grammar:\n    language: wanxiang-lts-zh-hant\n"
    "    collocation_max_length: 6\n    collocation_min_length: 3\n"
    "    collocation_penalty: -14\n    non_collocation_penalty: -6\n"
    "    weak_collocation_penalty: -100\n    rear_penalty: -20\n"
    "  translator/contextual_suggestions: false\n  translator/max_homophones: 8\n")}
ESSAY_LM = {"grammar.yaml": (
    "hant:\n  grammar:\n    language: zh-hant-t-essay-bgw\n"
    "  translator/contextual_suggestions: true\n"
    "  translator/max_homophones: 7\n  translator/max_homographs: 7\n")}
WX_OURS = {"grammar.yaml": (
    "hant:\n  grammar:\n    language: wanxiang-lts-zh-hant\n"
    "  translator/contextual_suggestions: true\n"
    "  translator/max_homophones: 7\n  translator/max_homographs: 7\n")}
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
    "asus-lang": ("project", "asus_zhuyin", (), NO_LANG),     # ablation: no mixed input (= stage 2)
    "asus-mcb": ("project", "asus_zhuyin", (), NO_MCB),       # ablation: no Taiwan frequencies
    "stage3": ("project", "asus_zhuyin", (), {**NO_MCB, **ESSAY_LM}),  # = stage 3 config
    "asus-wx": ("project", "asus_zhuyin", (), ESSAY_LM),      # ablation: previous LM
    "asus-enpre": ("project", "asus_zhuyin", (), NO_EN_PREFIX),  # ablation: no English prefixes
    "asus-rules": ("project", "asus_zhuyin", (), {"asus_zhuyin.custom.yaml": (  # ablation: no 的得地/在再 rules
        "patch:\n  engine/filters: [simplifier@zh_hans, simplifier@zh_hant_tw, uniquifier, "
        "lua_filter@*asus_compact]\n")}),
    "asus+wx": ("project", "asus_zhuyin", (), WX_RECOMMENDED),  # Wanxiang LM, its settings
    "asus+wx-ours": ("project", "asus_zhuyin", (), WX_OURS),  # Wanxiang LM, our settings
    "asus": ("project", "asus_zhuyin", (), {}),                # current project config
}
DEFAULT_VARIANTS = ["stock", "stage3", "asus-rules", "asus"]

# QWERTY neighbours for typo injection (letters/digits/punct used by Dachen).
ROWS = ["1234567890-", "qwertyuiop", "asdfghjkl;", "zxcvbnm,./"]
PARTICLES = set("的得地在再")
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


def inject_typo_end(keys: str, rng: random.Random) -> str:
    """One adjacent-key substitution in the last syllable (no right context)."""
    nb = _neighbours()
    tail = len(keys) - 1
    while tail > 0 and keys[tail - 1] not in TONE_KEYS:
        tail -= 1  # start of the last syllable
    idx = [i for i in range(tail, len(keys)) if keys[i] not in TONE_KEYS and nb.get(keys[i])]
    if not idx:
        return inject_typo(keys, rng)
    i = rng.choice(idx)
    return keys[:i] + rng.choice(nb[keys[i]]) + keys[i + 1:]


def inject_stray(keys: str, rng: random.Random) -> str:
    """Insert one extra key next to a neighbouring key (fat finger), never at the ends."""
    nb = _neighbours()
    idx = [i for i in range(1, len(keys)) if nb.get(keys[i - 1])]
    i = rng.choice(idx)
    return keys[:i] + rng.choice(nb[keys[i - 1]]) + keys[i:]


def inject_omit(keys: str, rng: random.Random) -> str:
    """One key not pressed (any key, tone keys included; never the last one,
    which would just be an unfinished input)."""
    i = rng.randrange(len(keys) - 1)
    return keys[:i] + keys[i + 1:]


def inject_swap(keys: str, rng: random.Random) -> str:
    """Two neighbouring keys pressed in the wrong order."""
    idx = [i for i in range(len(keys) - 1) if keys[i] != keys[i + 1]]
    i = rng.choice(idx)
    return keys[:i] + keys[i + 1] + keys[i] + keys[i + 2:]


def load_set(path: Path) -> list[tuple[str, str]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            keys, _, text, *_ = line.split("\t")
            rows.append((keys, text))
    return rows


def build_sets() -> dict[str, list[tuple[str, str]]]:
    dev = load_set(EVAL / "sentences.tsv")
    test = load_set(EVAL / "sentences_test.tsv")
    rng = random.Random(20261006)
    sets = {"dev": dev,
            "dev_typo": [(inject_typo(k, rng), t) for k, t in dev],
            "test": test}
    # Held-out typos (stage 7): same injection, its own seed.
    sets["test_typo"] = [(inject_typo(k, random.Random(20261007 + i)), t)
                         for i, (k, t) in enumerate(test)]
    # Typos in the last syllable, where only left context exists.
    sets["dev_typo_end"] = [(inject_typo_end(k, random.Random(1007 + i)), t) for i, (k, t) in enumerate(dev)]
    sets["test_typo_end"] = [(inject_typo_end(k, random.Random(2007 + i)), t) for i, (k, t) in enumerate(test)]
    # Guard: dev typed with no tone keys at all; English must not leak in.
    sets["dev_toneless"] = [(re.sub(r"[ 6347]", "", k), t) for k, t in dev]
    stray_rng = random.Random(3)
    sets["dev_stray"] = [(inject_stray(k, stray_rng), t) for k, t in dev]
    sets["test_stray"] = [(inject_stray(k, stray_rng), t) for k, t in test]
    # A key left out, and two keys in the wrong order (stage 10).
    sets["dev_omit"] = [(inject_omit(k, random.Random(4007 + i)), t) for i, (k, t) in enumerate(dev)]
    sets["test_omit"] = [(inject_omit(k, random.Random(5007 + i)), t) for i, (k, t) in enumerate(test)]
    sets["dev_swap"] = [(inject_swap(k, random.Random(6007 + i)), t) for i, (k, t) in enumerate(dev)]
    sets["test_swap"] = [(inject_swap(k, random.Random(7007 + i)), t) for i, (k, t) in enumerate(test)]
    for stem, name in (("mixed", "mixed"), ("mixed_test", "mixed_test"),
                       ("particles", "particles"), ("particles_test", "particles_test"),
                       ("numbers", "numbers"), ("numbers_test", "numbers_test"),
                       ("numbers_more", "numbers_more"),
                       ("numbers_amb", "numbers_amb"), ("numbers_amb_test", "numbers_amb_test")):
        if (EVAL / f"{stem}.tsv").exists():
            sets[name] = load_set(EVAL / f"{stem}.tsv")
    # Guard: every partial input of pure-Chinese dev sentences that ends on a
    # digit key; a digit in the preview is a number leaking into Chinese.
    sets["dev_partial"] = [(k[:i], "") for k, _ in dev[:50]
                           for i in range(1, len(k) + 1) if k[i - 1].isdigit()]
    for stem in ("english", "english_test"):
        path = EVAL / f"{stem}.txt"
        if path.exists():
            sets[stem] = [tuple(line.split("\t")[:2]) for line in
                          path.read_text(encoding="utf-8").splitlines()
                          if line.strip() and not line.startswith("#")]
    # English after a previous English word: "prev typed" -> expected.
    for stem in ("english_ctx", "english_ctx_test"):
        path = EVAL / f"{stem}.txt"
        if path.exists():
            rows = [line.split("	") for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.startswith("#")]
            sets[stem] = [(f"{prev} {typed}", want) for prev, typed, want in rows]
    # English mode: a word and Space (automatic correction, next words).
    for stem in ("autofix", "autofix_more", "autofix_test", "english_next", "english_next_test"):
        path = EVAL / f"{stem}.txt"
        if path.exists():
            sets[stem] = [tuple(line.split("\t")[:2]) for line in
                          path.read_text(encoding="utf-8").splitlines()
                          if line.strip() and not line.startswith("#")]
    words = EVAL / "private" / "asus_words.tsv"
    if words.exists():
        sets["words"] = load_set(words)
    moe = EVAL / "private" / "moe_words.tsv"
    if moe.exists():
        sets["moe_words"] = load_set(moe)
    return sets


# -- worker (runs inside a subprocess) -------------------------------------
def worker(variant: str, sets_json: str) -> None:
    from rime_harness import SHIFT_MASK, open_rime
    config, schema, exclude, extra = VARIANTS[variant]
    # No learning during evaluation: a commit (e.g. Space after an English
    # word) would otherwise change the ranking for every later case, making
    # scores depend on set order. Learning is evaluated separately (stage 5).
    extra = dict(extra)
    custom = f"{schema}.custom.yaml"
    extra[custom] = ((extra.get(custom) or "patch:\n") + "  translator/enable_user_dict: false\n"
                     + "  asus_learn/enabled: false\n")
    # Tuning: EVAL_PATCH="asus_lang/costs/fix=1.4;asus_typo/margin=25" (never saved).
    for item in filter(None, os.environ.get("EVAL_PATCH", "").split(";")):
        key, _, value = item.partition("=")
        extra[custom] += f'  {key}: "{value}"\n'
    rime = open_rime(f"eval-{variant}", config, schema, exclude=exclude, extra=extra)
    sets = json.loads(sets_json)
    out: dict[str, list] = {}
    for name, cases in sets.items():
        res = []
        for keys, _ in cases:
            # Same left context for every case: contextual_suggestions ranks by
            # the last committed text, so commit "。" (sentence start) first.
            rime.clear()
            rime.key(">", SHIFT_MASK)
            rime.clear()
            english_mode = name.startswith(("autofix", "english_next"))
            if english_mode:
                rime.set_option("ascii_mode", True)
                keys += " "
            t0 = time.perf_counter()
            for ch in keys:  # capitals are typed with Shift
                rime.key(ch, SHIFT_MASK if ch.isupper() else 0)
            dt_ms = (time.perf_counter() - t0) * 1000 / max(len(keys), 1)
            snap = rime.snapshot()
            preview = snap.committed + snap.preview
            page, final = [], preview
            if english_mode:
                # What is in the document after Space (a correction is shown
                # there, to be committed by the next key), and the words on offer.
                final = preview.strip()
                page = [comment for text, comment in snap.candidates if text == ""]
                rime.clear()
                rime.set_option("ascii_mode", False)
            elif name.startswith("english") or name in ("words", "moe_words"):
                if schema == "asus_zhuyin":
                    rime.key("Down")  # expand the compact bubble to a full page
                page = [c for c, _ in rime.snapshot().candidates]
            elif name not in ("dev_partial", "dev_toneless"):
                # What the user gets: Enter commits (and decides what digit
                # keys at the very end were, asus_lang.finalize).
                rime.key("Return")
                final = "".join(rime.committed)
            res.append({"preview": preview, "final": final, "page": page, "ms_per_key": dt_ms})
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
    exact = sum(r["final"] == t for (_, t), r in zip(cases, res))
    errs = sum(levenshtein(r["final"], t) for (_, t), r in zip(cases, res))
    total = sum(len(t) for _, t in cases)
    m = {"n": n, "SentAcc": exact / n, "CharAcc": 1 - errs / max(total, 1),
         "ms/key": statistics.mean(r["ms_per_key"] for r in res)}
    if name.startswith("particles"):
        # PartAcc: share of 的/得/地/在/再 positions that come out right
        # (a sentence whose length differs counts all its targets wrong).
        hit = tot = 0
        for (_, t), r in zip(cases, res):
            for i, ch in enumerate(t):
                if ch in PARTICLES:
                    tot += 1
                    hit += len(r["final"]) == len(t) and r["final"][i] == ch
        m["PartAcc"] = hit / tot if tot else 0.0
    if name == "dev_toneless":
        # ASCII = share of outputs with Latin letters (English hallucinated
        # into toneless Chinese); lower is better.
        m = {"n": n, "ASCII": sum(bool(re.search(r"[A-Za-z]", r["preview"])) for r in res) / n,
             "ms/key": m["ms/key"]}
    if name == "dev_partial":
        # Digits = share of partial inputs showing a digit; lower is better.
        m = {"n": n, "Digits": sum(bool(re.search(r"[0-9]", r["preview"])) for r in res) / n,
             "ms/key": m["ms/key"]}
    if name.startswith("autofix"):
        fix = wrong = nt = false = nk = 0
        for (k, t), r in zip(cases, res):
            if k == t:
                nk += 1
                false += r["final"] != t
            else:
                nt += 1
                fix += r["final"] == t
                wrong += r["final"] not in (t, k)
        m = {"n": n, "Fix": fix / max(nt, 1), "Wrong": wrong / max(nt, 1),
             "FalseFix": false / max(nk, 1), "ms/key": m["ms/key"]}
    elif name.startswith("english_next"):
        pages = [[w.lower() for w in r["page"]] for r in res]
        m = {"n": n, "Next1": sum(p[:1] == [t] for (_, t), p in zip(cases, pages)) / n,
             "Next3": sum(t in p[:3] for (_, t), p in zip(cases, pages)) / n, "ms/key": m["ms/key"]}
    elif name.startswith("english"):
        # Guesses = English candidates other than the typed letters, in menu
        # order. Guess1/Guess3: expected word first / in the first three (for
        # prefixes and typos); Keep: a correct word stays as typed inline.
        g1 = g3 = ng = keep = nk = 0
        for (k, t), r in zip(cases, res):
            k = k.split(" ")[-1]  # english_ctx: the previous word is committed
            if k == t:
                nk += 1
                keep += r["preview"] == t
                continue
            ng += 1
            guesses = [c for c in r["page"] if c != k and c.isascii() and c.strip()]
            if name.startswith("english_ctx"):  # united + sta: States is right too
                guesses = [c.lower() for c in guesses]
            g1 += guesses[:1] == [t]
            g3 += t in guesses[:3]
        m = {"n": n, "Guess1": g1 / max(ng, 1), "Guess3": g3 / max(ng, 1),
             "Keep": keep / max(nk, 1), "ms/key": m["ms/key"]}
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
        for line in proc.stderr.decode("utf-8", "replace").splitlines():
            if line.startswith("rime_harness:"):  # e.g. the segmentation guard tripped
                print(line, file=sys.stderr)
        res = json.loads(proc.stdout.decode("utf-8"))
        metrics = {name: score(name, sets[name], res[name]) for name in sets}
        records.append({"time": stamp, "rev": rev, "note": args.note, "sets": set_hash,
                        "variant": variant, "metrics": metrics})
        # Failures are reported for dev sets only; test sets stay blind.
        for name in sets:
            if "test" in name or name in ("words", "moe_words", "dev_toneless", "dev_partial"):
                continue
            for (keys, text), r in zip(sets[name], res[name]):
                got = r["final"]
                if name.startswith("english_next"):  # the words offered after `keys`
                    offered = [w.lower() for w in r["page"][:3]]
                    got = text if text in offered else f"{keys} + [{' '.join(offered)}]"
                if got != text:
                    failures.append((variant, name, text, got))

    # Markdown report: one table per set, previous run in parentheses.
    lines = [f"## Eval {stamp} @ {rev} sets={set_hash} {args.note}".rstrip(),
             "(previous run on the same eval sets in parentheses)", ""]
    for name in sets:
        keys = list(records[0]["metrics"][name].keys() - {"n"})
        keys.sort(key=["SentAcc", "CharAcc", "PartAcc", "ASCII", "Digits", "Guess1", "Guess3", "Keep", "Fix", "Wrong", "FalseFix", "Next1", "Next3", "Top1", "Top9", "ms/key"].index)
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

    if not args.no_save and not os.environ.get("EVAL_PATCH"):
        REPORTS.mkdir(exist_ok=True)
        with HISTORY.open("a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        detail = REPORTS / "last_failures.tsv"
        detail.write_text("variant\tset\texpected\tgot\n" + "\n".join(
            "\t".join(x) for x in failures) + "\n", encoding="utf-8")
        print(f"(saved to {HISTORY.relative_to(ROOT)}; dev-set failures in "
              f"{detail.relative_to(ROOT)}; test-set failures are never shown)")


if __name__ == "__main__":
    main()
