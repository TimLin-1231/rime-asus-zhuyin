"""Quantitative evaluation of conversion quality (and keystroke latency).

Each variant runs in its own subprocess (librime initialises once per process)
against a staged copy of the config, with no learned user data, so runs are
reproducible. Results are appended to reports/eval_history.jsonl and printed
as a Markdown table, side by side with the previous run of each variant.

    python tools/eval_ime.py                       # default variants, set dev only
    python tools/eval_ime.py --sets dev-all        # every development set
    python tools/eval_ime.py --variants asus stock --sets dev mixed
    python tools/eval_ime.py --note "after stage 1"
    python tools/eval_ime.py --sets test --heldout --note "why this look is needed"

Every figure is printed with its numerator and denominator (97.0% [97/100]).
Against the previous run of the same variant on the same sets, and against
the first variant of this run, it prints how many cases were fixed and how
many broken (paired, case by case); a net change under 6 cases cannot be told
apart from noise.

Held-out sets (name contains "test", and moe_words) are only run with
--heldout and a --note saying why; every such run is logged to
reports/heldout_log.jsonl (also with --no-save) and the run says how many
times each held-out set was evaluated before. A set looked at dozens of times
is no longer held out.

librime errors: each worker reports the ERROR/FATAL lines librime logged and
whether the language model failed to load. If there were any, the tool prints
how many and the first ones on stderr, does not append to the history, and
exits 3 (--allow-errors: save anyway, with the error count in the record).
A broken Lua module does not crash librime; it only turns every set into
raw letters or, after the last candidate, changes nothing visible at all.

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
  autofix_zh / autofix_zh_test  the same words typed in Chinese mode, then Space
  mixed_fix / mixed_fix_test  Chinese with a misspelt English word in it
            (tests/eval/mixed_fix*.txt), and with words that must stay
  en_case / en_case_test  an English word typed in lowercase inside Chinese
            (tests/eval/en_case*.txt): Lower = ordinary words and names that
            come out as typed, Caps = acronyms that come out in capitals
  en_short / en_short_test  3-4 letter English words typed in lowercase inside
            Chinese (tests/eval/en_short*.txt); the dev set also has first tones
            typed without Space that must stay Chinese
  en_edge / en_edge_test  an English word whose last letter, or the key after
            it, could belong to either side (tim + 改 / time + 矮, tests/eval/en_edge*.txt)
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
HELDOUT_LOG = REPORTS / "heldout_log.jsonl"
DEFAULT_SETS = ["dev"]
NOISE_CASES = 6  # a net change smaller than this many cases is not a measurable difference
EXIT_LIBRIME_ERRORS = 3

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
    "asus-raw": ("project", "asus_zhuyin", (), {"asus_zhuyin.custom.yaml": "patch:\n  asus_lang/raw_keys: true\n"}),
}
# asus-raw: the product default (DaoHui ships raw_keys on, and so does the
# developer's own IME); the repository schema keeps it off.
DEFAULT_VARIANTS = ["stock", "stage3", "asus-rules", "asus", "asus-raw"]

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
                       ("mixed_fix", "mixed_fix"), ("mixed_fix_test", "mixed_fix_test"),
                       ("en_case", "en_case"), ("en_case_test", "en_case_test"),
                       ("en_short", "en_short"), ("en_short_test", "en_short_test"),
                       ("en_edge", "en_edge"), ("en_edge_test", "en_edge_test"),
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
    # The same words in Chinese mode: a word, then Space.
    for stem in ("autofix", "autofix_test"):
        if stem in sets:
            sets[stem.replace("autofix", "autofix_zh")] = sets[stem]
    words = EVAL / "private" / "asus_words.tsv"
    if words.exists():
        sets["words"] = load_set(words)
    moe = EVAL / "private" / "moe_words.tsv"
    if moe.exists():
        sets["moe_words"] = load_set(moe)
    return sets


def is_heldout(name: str) -> bool:
    """Held-out sets: never tuned against, only aggregates are shown."""
    return "test" in name or name == "moe_words"


# -- librime health and what was measured (shared by the eval_*.py tools) -----
HEALTH_TAG = "eval_health: "
# A language model that does not load leaves these lines (W/E level); the
# conversion then silently runs without it.
GRAMMAR_FAIL = re.compile(r"^[WEF]\d{8} .*(failed to load grammar|error opening gram db)", re.I)
_BIG = 16 << 20


def content_hash(user: Path) -> str:
    """sha256 over the relative paths and contents of a staged user dir: the
    configuration and data that were measured, including the git-ignored
    generated files a commit id cannot identify. Hashes of files over 16 MB
    (the language models) are cached by name, size and mtime."""
    cache_file = user.parents[1] / "_cache" / "content_hash.json"
    try:
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    h = hashlib.sha256()
    changed = False
    for p in sorted((q for q in user.rglob("*") if q.is_file()), key=lambda q: q.relative_to(user).as_posix()):
        st = p.stat()
        key = f"{p.name}|{st.st_size}|{st.st_mtime_ns}"
        digest = cache.get(key) if st.st_size > _BIG else None
        if digest is None:
            fh = hashlib.sha256()
            with p.open("rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    fh.update(chunk)
            digest = fh.hexdigest()
            if st.st_size > _BIG:
                cache[key] = digest
                changed = True
        h.update(f"{p.relative_to(user).as_posix()}\0{digest}\n".encode("utf-8"))
    if changed:
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(cache), encoding="utf-8")
        except OSError:
            pass
    return h.hexdigest()[:16]


def open_measured(name: str, config: str = "project", schema: str = "asus_zhuyin",
                  exclude: tuple[str, ...] = (), extra: dict[str, str] | None = None):
    """rime_harness.open_rime, plus the content hash of the staged user dir
    (taken before librime writes its build/ into it). Returns (rime, hash)."""
    from rime_harness import Rime, stage_user_dir
    user = stage_user_dir(name, config, False, exclude, extra)
    content = content_hash(user)
    rime = Rime(user, log_name=name)
    if not rime.select_schema(schema) or rime.current_schema() != schema:
        raise RuntimeError(f"schema {schema!r} not available (got {rime.current_schema()!r}); "
                           f"see {rime.log_dir}")
    return rime, content


def complete_error_lines() -> list[str]:
    """rime_harness.error_lines() without the half lines: inside the process
    glog's INFO file is still buffered, so it can end in the first part of an
    error line that the (flushed) ERROR file holds in full."""
    import rime_harness as rh
    lines = rh.error_lines()
    srt = sorted(set(lines))
    partial = {a for a, b in zip(srt, srt[1:]) if b.startswith(a)}
    return [line for line in lines if line not in partial]


def report_health(content: str = "") -> None:
    """In a worker, after rime.close(): one tagged stderr line with the
    librime ERROR/FATAL lines of this process (rime_harness.error_lines()),
    the lines saying the language model did not load, and the content hash."""
    import rime_harness as rh
    errors = complete_error_lines()
    grammar: list[str] = []
    for d in rh.LOG_DIRS:
        for f in sorted(Path(d).glob("*")) if Path(d).is_dir() else []:
            if not f.is_file() or f.name == rh.UNREAD_MARK:
                continue
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                if GRAMMAR_FAIL.match(line) and line not in grammar:
                    grammar.append(line)
    sys.stderr.write(HEALTH_TAG + json.dumps(
        {"errors": len(errors), "first": errors[:5], "grammar": len(grammar), "grammar_first": grammar[:3],
         "logs": [str(d) for d in rh.LOG_DIRS], "content": content}, ensure_ascii=False) + "\n")
    sys.stderr.flush()


def read_health(label: str, stderr_text: str) -> dict:
    """The worker's health report; a worker that gave none counts as broken."""
    for line in stderr_text.splitlines():
        if line.startswith(HEALTH_TAG):
            h = json.loads(line[len(HEALTH_TAG):])
            h["label"] = label
            return h
    return {"label": label, "errors": 1, "grammar": 0, "logs": [], "content": "",
            "first": [f"{label}: the worker printed no librime health report"], "grammar_first": []}


def forward_harness_lines(stderr_text: str) -> None:
    for line in stderr_text.splitlines():
        if line.startswith("rime_harness:"):  # e.g. the segmentation guard tripped
            print(line, file=sys.stderr)


def health_problems(healths: list[dict], tool: str) -> int:
    """Print the librime errors of all workers to stderr; the number of
    workers with errors or a language model that did not load."""
    bad = 0
    for h in healths:
        if not h["errors"] and not h["grammar"]:
            continue
        bad += 1
        if h["errors"]:
            print(f"{tool}: {h['label']}: librime logged {h['errors']} error line(s); first:", file=sys.stderr)
            for line in h["first"][:3]:
                print(f"    {line[:300]}", file=sys.stderr)
        if h["grammar"]:
            print(f"{tool}: {h['label']}: the language model failed to load:", file=sys.stderr)
            for line in h["grammar_first"][:2]:
                print(f"    {line[:300]}", file=sys.stderr)
        if h["logs"]:
            print(f"{tool}: {h['label']}: logs in {', '.join(h['logs'])}", file=sys.stderr)
    return bad


def refuse_to_save(tool: str, what: str) -> None:
    print(f"{tool}: the numbers above were measured on a broken IME: not saved to {what}; "
          f"exit {EXIT_LIBRIME_ERRORS} (--allow-errors saves them anyway)", file=sys.stderr)


# -- worker (runs inside a subprocess) -------------------------------------
def worker(variant: str, sets_json: str) -> None:
    from rime_harness import SHIFT_MASK
    config, schema, exclude, extra = VARIANTS[variant]
    # No learning during evaluation: a commit (e.g. Space after an English
    # word) would otherwise change the ranking for every later case, making
    # scores depend on set order. Learning is evaluated separately (stage 5).
    extra = dict(extra)
    custom = f"{schema}.custom.yaml"
    # English mode types directly by default; the English sets (autofix,
    # english_next) measure its composing mode, so that is switched on.
    extra[custom] = ((extra.get(custom) or "patch:\n") + "  translator/enable_user_dict: false\n"
                     + "  asus_learn/enabled: false\n" + "  asus_english/compose: true\n")
    # Tuning: EVAL_PATCH="asus_lang/costs/fix=1.4;asus_typo/margin=25" (never saved).
    for item in filter(None, os.environ.get("EVAL_PATCH", "").split(";")):
        key, _, value = item.partition("=")
        extra[custom] += f'  {key}: "{value}"\n'
    rime, content = open_measured(f"eval-{variant}", config, schema, exclude=exclude, extra=extra)
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
            word_and_space = name.startswith(("autofix", "english_next"))
            english_mode = word_and_space and not name.startswith("autofix_zh")
            if english_mode:
                rime.set_option("ascii_mode", True)
            if word_and_space:
                keys += " "
            t0 = time.perf_counter()
            for ch in keys:  # capitals are typed with Shift
                rime.key(ch, SHIFT_MASK if ch.isupper() else 0)
            dt_ms = (time.perf_counter() - t0) * 1000 / max(len(keys), 1)
            snap = rime.snapshot()
            preview = snap.committed + snap.preview
            page, final = [], preview
            if word_and_space:
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
    report_health(content)
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


def score(name: str, cases: list[tuple[str, str]], res: list[dict], counts: dict | None = None) -> dict:
    """The metrics of one set. counts, if given, receives {metric: [numerator,
    denominator]} for every fraction (CharAcc: characters right / characters)."""
    c: dict[str, list[int]] = {}
    n = len(cases)
    exact = sum(r["final"] == t for (_, t), r in zip(cases, res))
    errs = sum(levenshtein(r["final"], t) for (_, t), r in zip(cases, res))
    total = sum(len(t) for _, t in cases)
    m = {"n": n, "SentAcc": exact / n, "CharAcc": 1 - errs / max(total, 1),
         "ms/key": statistics.mean(r["ms_per_key"] for r in res)}
    c.update(SentAcc=[exact, n], CharAcc=[total - errs, total])
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
        c["PartAcc"] = [hit, tot]
    if name.startswith("en_case"):
        # Lower: words typed in lowercase that come out as typed; Caps:
        # acronyms typed in lowercase that come out in capitals.
        caps = [bool(re.search(r"[A-Z]", t)) for _, t in cases]
        ok = [r["final"] == t for (_, t), r in zip(cases, res)]
        m = {"n": n, "Lower": sum(o for o, c in zip(ok, caps) if not c) / max(caps.count(False), 1),
             "Caps": sum(o for o, c in zip(ok, caps) if c) / max(caps.count(True), 1),
             "ms/key": m["ms/key"]}
        c = {"Lower": [sum(o for o, k in zip(ok, caps) if not k), caps.count(False)],
             "Caps": [sum(o for o, k in zip(ok, caps) if k), caps.count(True)]}
    if name == "dev_toneless":
        # ASCII = share of outputs with Latin letters (English hallucinated
        # into toneless Chinese); lower is better.
        ascii_n = sum(bool(re.search(r"[A-Za-z]", r["preview"])) for r in res)
        m = {"n": n, "ASCII": ascii_n / n, "ms/key": m["ms/key"]}
        c = {"ASCII": [ascii_n, n]}
    if name == "dev_partial":
        # Digits = share of partial inputs showing a digit; lower is better.
        digits_n = sum(bool(re.search(r"[0-9]", r["preview"])) for r in res)
        m = {"n": n, "Digits": digits_n / n, "ms/key": m["ms/key"]}
        c = {"Digits": [digits_n, n]}
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
        c = {"Fix": [fix, nt], "Wrong": [wrong, nt], "FalseFix": [false, nk]}
    elif name.startswith("english_next"):
        pages = [[w.lower() for w in r["page"]] for r in res]
        n1 = sum(p[:1] == [t] for (_, t), p in zip(cases, pages))
        n3 = sum(t in p[:3] for (_, t), p in zip(cases, pages))
        m = {"n": n, "Next1": n1 / n, "Next3": n3 / n, "ms/key": m["ms/key"]}
        c = {"Next1": [n1, n], "Next3": [n3, n]}
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
        c = {"Guess1": [g1, ng], "Guess3": [g3, ng], "Keep": [keep, nk]}
    if name in ("words", "moe_words"):
        top9 = sum(t in r["page"] for (_, t), r in zip(cases, res))
        m = {"n": n, "Top1": exact / n, "Top9": top9 / n, "ms/key": m["ms/key"]}
        c = {"Top1": [exact, n], "Top9": [top9, n]}
    if counts is not None:
        counts.update({k: [int(a), int(b)] for k, (a, b) in c.items()})
    return m


def case_flags(name: str, cases: list[tuple[str, str]], res: list[dict]) -> str:
    """One character per case, "1" when the case is right by the set's first
    metric (SentAcc, Fix, Next1, Guess1/Keep, Top1; no ASCII / no digit for
    the guard sets): two runs can then be compared case by case."""
    out = []
    for (k, t), r in zip(cases, res):
        if name == "dev_toneless":
            ok = not re.search(r"[A-Za-z]", r["preview"])
        elif name == "dev_partial":
            ok = not re.search(r"[0-9]", r["preview"])
        elif name.startswith("english_next"):
            ok = [w.lower() for w in r["page"]][:1] == [t]
        elif name.startswith("english") and not name.startswith("english_next"):
            k = k.split(" ")[-1]
            if k == t:
                ok = r["preview"] == t
            else:
                guesses = [g for g in r["page"] if g != k and g.isascii() and g.strip()]
                if name.startswith("english_ctx"):
                    guesses = [g.lower() for g in guesses]
                ok = guesses[:1] == [t]
        else:
            ok = r["final"] == t
        out.append("1" if ok else "0")
    return "".join(out)


def paired(old: str | None, new: str) -> tuple[int, int] | None:
    """(fixed, broken) between two case_flags strings of the same set."""
    if not old or len(old) != len(new):
        return None
    return (sum(a == "0" and b == "1" for a, b in zip(old, new)),
            sum(a == "1" and b == "0" for a, b in zip(old, new)))


def paired_text(fb: tuple[int, int]) -> str:
    fixed, broken = fb
    net = fixed - broken
    noise = "；淨變化少於 6 句，量不出差異" if abs(net) < NOISE_CASES else ""
    return f"修好 {fixed} 句、弄壞 {broken} 句（淨 {net:+d}{noise}）"


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


def heldout_history() -> dict[str, set[str]]:
    """Per held-out set, the start times of every earlier run that evaluated
    it: records of the history and of the held-out log (--no-save runs)."""
    seen: dict[str, set[str]] = {}
    for path, field in ((HISTORY, "metrics"), (HELDOUT_LOG, "sets")):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            for name in rec.get(field) or ():
                if is_heldout(name):
                    seen.setdefault(name, set()).add(rec.get("time", ""))
    return seen


def select_sets(all_sets: dict, wanted: list[str]) -> dict:
    """--sets: set names, 'dev-all' (every development set) or 'all'."""
    names: list[str] = []
    for w in wanted:
        if w == "all":
            names += list(all_sets)
        elif w == "dev-all":
            names += [n for n in all_sets if not is_heldout(n)]
        elif w in all_sets:
            names.append(w)
        else:
            raise SystemExit(f"eval_ime: unknown set {w!r}; sets: {', '.join(all_sets)}, dev-all, all")
    return {n: all_sets[n] for n in all_sets if n in names}  # build_sets() order


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variants", nargs="+", default=DEFAULT_VARIANTS, choices=list(VARIANTS))
    ap.add_argument("--sets", nargs="+", default=DEFAULT_SETS,
                    help="eval sets (default: dev); 'dev-all' = every development set, 'all' = every set")
    ap.add_argument("--heldout", action="store_true",
                    help="allow held-out sets (needs --note); logged to reports/heldout_log.jsonl")
    ap.add_argument("--note", default="")
    ap.add_argument("--worker", help=argparse.SUPPRESS)
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--allow-errors", action="store_true",
                    help="save to the history even if librime logged errors (exit code stays 3)")
    args = ap.parse_args()

    if args.worker:  # the parent sends exactly the sets to run
        worker(args.worker, sys.stdin.read())
        return
    sets = select_sets(build_sets(), args.sets)
    heldout = [n for n in sets if is_heldout(n)]
    if heldout and not args.heldout:
        raise SystemExit(f"eval_ime: {', '.join(heldout)} held out: add --heldout and a --note saying why "
                         f"(every look is logged to {HELDOUT_LOG.relative_to(ROOT)})")
    if args.heldout and not args.note.strip():
        raise SystemExit("eval_ime: --heldout needs --note: why this held-out run is needed")

    set_hash = sets_hash(sets)
    prev = previous_runs(set_hash)
    stamp = dt.datetime.now().isoformat(timespec="seconds")
    rev = git_rev()
    earlier = heldout_history()
    for name in heldout:
        n = len(earlier.get(name, set()) - {stamp})
        print(f"held-out set {name}: 這是第 {n + 1} 次評這一組（之前 {n} 次）")
    records, failures, healths, flags = [], [], [], {}
    for variant in args.variants:
        proc = subprocess.run([sys.executable, __file__, "--worker", variant],
                              input=json.dumps(sets, ensure_ascii=False).encode("utf-8"),
                              capture_output=True)
        err = proc.stderr.decode("utf-8", "replace")
        if proc.returncode:
            raise SystemExit(f"{variant} failed:\n{err}")
        forward_harness_lines(err)
        health = read_health(variant, err)
        healths.append(health)
        res = json.loads(proc.stdout.decode("utf-8"))
        counts: dict[str, dict] = {}
        metrics = {name: score(name, sets[name], res[name], counts.setdefault(name, {})) for name in sets}
        flags[variant] = {name: case_flags(name, sets[name], res[name]) for name in sets}
        records.append({"time": stamp, "rev": rev, "note": args.note, "sets": set_hash,
                        "variant": variant, "metrics": metrics, "counts": counts,
                        # Per-case flags only for development sets: the history is
                        # public, and with the held-out sentences next to it the
                        # flags would show exactly which held-out cases fail.
                        "cases": {n: f for n, f in flags[variant].items() if not is_heldout(n)},
                        "content": health.get("content", ""),
                        "errors": health["errors"] + health["grammar"]})
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
             "(numerator/denominator in brackets; previous run on the same eval sets in parentheses)",
             "content: " + ", ".join(f"{r['variant']}={r['content'] or '?'}" for r in records), ""]
    base = records[0]["variant"]
    for name in sets:
        keys = list(records[0]["metrics"][name].keys() - {"n"})
        keys.sort(key=["SentAcc", "CharAcc", "PartAcc", "Lower", "Caps", "ASCII", "Digits", "Guess1", "Guess3", "Keep", "Fix", "Wrong", "FalseFix", "Next1", "Next3", "Top1", "Top9", "ms/key"].index)
        lines.append(f"### {name} (n={records[0]['metrics'][name]['n']})")
        lines.append("| variant | " + " | ".join(keys) + " |")
        lines.append("|---|" + "---:|" * len(keys))
        notes = []
        for rec in records:
            cells = []
            for k in keys:
                cur = rec["metrics"][name][k]
                old = prev.get(rec["variant"], {}).get("metrics", {}).get(name, {}).get(k)
                frac = rec["counts"][name].get(k)
                cells.append(fmt(cur, k) + (f" [{frac[0]}/{frac[1]}]" if frac else "")
                             + (f" ({fmt(old, k)})" if old is not None else ""))
            lines.append(f"| {rec['variant']} | " + " | ".join(cells) + " |")
            old_flags = (prev.get(rec["variant"], {}).get("cases") or {}).get(name)
            fb = paired(old_flags, flags[rec["variant"]][name])
            if fb:
                notes.append(f"- {rec['variant']} vs its previous run: {paired_text(fb)}")
            if rec["variant"] != base:
                fb = paired(flags[base][name], flags[rec["variant"]][name])
                notes.append(f"- {rec['variant']} vs {base} (this run): {paired_text(fb)}")
        lines += notes
        lines.append("")
    report = "\n".join(lines)
    print(report)

    bad = health_problems(healths, "eval_ime")
    if heldout:
        HELDOUT_LOG.parent.mkdir(exist_ok=True)
        with HELDOUT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"time": stamp, "rev": rev, "note": args.note, "sets": heldout,
                                "variants": args.variants,
                                "earlier": {n: len(earlier.get(n, set()) - {stamp}) for n in heldout},
                                "saved": not args.no_save and not os.environ.get("EVAL_PATCH")
                                and (not bad or args.allow_errors),
                                "errors": sum(r["errors"] for r in records)}, ensure_ascii=False) + "\n")
    if bad and not args.allow_errors:
        if not args.no_save and not os.environ.get("EVAL_PATCH"):
            refuse_to_save("eval_ime", HISTORY.relative_to(ROOT).as_posix())
        raise SystemExit(EXIT_LIBRIME_ERRORS)

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
    if bad:  # --allow-errors: saved, but the run still failed
        raise SystemExit(EXIT_LIBRIME_ERRORS)


if __name__ == "__main__":
    main()
