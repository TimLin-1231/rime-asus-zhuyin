"""Self-learning evaluation (stage 5).

Replays tests/eval/learn*.tsv as one user's typing stream with learning on,
against a fresh staged config (no learned data from the live IME):

  baseline  a run that only measures (nothing is taught)
  learned   groups are replayed in order: teach lines are typed and corrected
            the way a user would (expand the menu, pick the longest candidate
            that matches, page down if needed), then the group's probes are
            measured right away; at the end every probe is measured again.

    python tools/eval_learn.py                     # dev stream, variants asus + rime
    python tools/eval_learn.py --set test          # held out: aggregates only
    python tools/eval_learn.py --variants rime nolearn
    python tools/eval_learn.py --set hist        # with typing history (H lines)
    python tools/eval_learn.py --set ctx --variants asus noctx  # with the previous commit

In learn_ctx*.tsv a "+" splits a T/P/C line: the text before it is committed
first (like an H line), the rest is typed as a new composition right after.

Metrics (fractions; picks are counts)
  Probe     P lines right (same word, new sentence)       base -> now -> end
  Contrast  C lines right (the rival word must stay)      base -> now -> end
  English   P lines containing English                    base -> now -> end
  Pred@1/3  Q lines: expected word first / in the top 3 predictions
  General   sentences*.tsv right after the whole stream (collateral damage)
  Picks     candidates picked while teaching; Repeat = picks for a teach line
            the stream has already taught once (accident groups)
  ms/key    mean process_key latency while measuring, with the learned state
Variants
  asus      project config (Rime learning + lua/asus_learn.lua)
  asus2     same, but a correction applies everywhere only after 2 corrections
  ctx       asus with the last committed character as the left context of a
            new composition (asus_learn/committed_context: true, the default)
  noctx     asus without it (committed_context: false, the rule before)
  rime      Rime's own learning only (asus_learn disabled)
  nolearn   no learning at all (enable_user_dict off, asus_learn disabled)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "tests" / "eval"
REPORTS = ROOT / "reports"
HISTORY = REPORTS / "learn_history.jsonl"

SCHEMA = "asus_zhuyin"
VARIANTS = {
    "asus": "",
    "asus2": "  asus_learn/global_after: 2\n",
    "ctx": "  asus_learn/committed_context: true\n",
    "noctx": "  asus_learn/committed_context: false\n",
    "rime": "  asus_learn/enabled: false\n",
    "nolearn": "  asus_learn/enabled: false\n  translator/enable_user_dict: false\n",
}
MAX_PAGES = 12


STEMS = {"dev": "learn", "test": "learn_test", "hist": "learn_hist", "hist_test": "learn_hist_test",
         "ctx": "learn_ctx", "ctx_test": "learn_ctx_test"}


def load(set_name: str) -> tuple[list[dict], list[tuple[str, str]]]:
    stem = STEMS[set_name]
    lines = []
    for row in (EVAL / f"{stem}.tsv").read_text(encoding="utf-8").splitlines():
        kind, keys, _, text, group = row.split("\t")
        # split: the text with its "+" (committed first | new composition).
        lines.append({"kind": kind, "keys": keys, "text": text.replace("+", ""), "split": text,
                      "group": group})
    general = "sentences_test.tsv" if set_name.endswith("test") else "sentences.tsv"
    sents = []
    for row in (EVAL / general).read_text(encoding="utf-8").splitlines():
        cols = row.split("\t")
        sents.append((cols[0], cols[2]))
    return lines, sents


# -- worker (runs inside a subprocess) -------------------------------------
class Typist:
    def __init__(self, rime, shift_mask, ctrl_mask):
        self.r = rime
        self.shift = shift_mask
        self.ctrl = ctrl_mask
        self.key_ms: list[float] = []

    def reset_context(self):
        """Commit 。 so every measurement starts at a sentence boundary."""
        self.r.clear()
        self.r.key(">", self.shift)
        self.r.clear()

    def type(self, keys: str, timed: bool = False):
        t0 = time.perf_counter()
        for ch in keys:
            self.r.key(ch, self.shift if ch.isupper() else 0)
        if timed:
            self.key_ms.append((time.perf_counter() - t0) * 1000 / max(len(keys), 1))

    def preview(self, keys: str, split: str = "") -> str:
        """Top conversion of keys, without committing (nothing is learned).

        keys "a+b" (split: the text "A+B"): A is committed first, then the
        conversion of b right after it is measured (A + conversion).
        """
        before = ""
        if "+" in keys:
            first, keys = keys.split("+", 1)
            before, _ = self.enter(first, split.split("+", 1)[0])
        else:
            self.reset_context()
        self.type(keys, timed=True)
        snap = self.r.snapshot()
        self.r.clear()
        return before + snap.committed + snap.preview

    def enter(self, keys: str, target: str, keep: bool = False, reset: bool = True) -> tuple[str, int]:
        """Type keys and commit `target`, picking candidates when needed.

        keep: leave the session as it is after the commit (prediction bubble).
        reset: start at a sentence boundary (else right after the last commit).
        keys "a+b", target "A+B": commit A first (its picks are not counted),
        then type b as a new composition and commit B.
        """
        if "+" in keys:
            first, keys = keys.split("+", 1)
            head, target = target.split("+", 1)
            before, _ = self.enter(first, head)
            got, picks = self.enter(keys, target, keep, reset=False)
            return before + got, picks
        if reset:
            self.reset_context()
        self.type(keys)
        if self.r.snapshot().preview == target:
            self.r.key("Return")  # Space is the first-tone key
            out = "".join(self.r.committed)
            if not keep:
                self.r.clear()
            return out, 0
        done, picks = "", 0
        # From the start: Home, then Down lists the words at the cursor
        # (Down alone lists the last word since stage 6b).
        self.r.key("Home")
        for _ in range(40):
            if done == target or not self.r.snapshot().input:
                break
            # (After a pick the list has moved on and is open already; Down
            # would move its highlight, and the highlighted row is shown with
            # the text after it.)
            if not self.r.get_option("_asus_expanded"):
                self.r.key("Down")
            rest = target[len(done):]
            pick = None
            for _page in range(MAX_PAGES):
                snap = self.r.snapshot()
                for i, (text, _) in enumerate(snap.candidates):
                    if text and rest.startswith(text) and (pick is None or len(text) > len(pick[1])):
                        pick = (i, text)
                if pick or snap.candidates == [] or len(snap.candidates) < 9:
                    break
                self.r.key("Page_Down")
            if not pick:
                break
            self.r.key(str(pick[0] + 1))
            done += pick[1]
            picks += 1
        if self.r.snapshot().input:  # could not reach the target: commit as is
            if self.r.get_option("_asus_expanded"):
                self.r.key("Escape")
            self.r.key("Return")
        out = "".join(self.r.committed)
        if not keep:
            self.r.clear()
        return out, picks

    def english(self, word: str) -> str:
        """Type an English word; if it does not come out as English, commit
        the letters as typed with Ctrl+Enter (the way to teach a word)."""
        self.reset_context()
        self.type(word)
        if self.r.snapshot().preview == word:
            self.r.key("Return")
        else:
            self.r.key("Return", self.ctrl)
        out = "".join(self.r.committed)
        self.r.clear()
        return out

    def predictions(self, keys: str, context: str) -> list[str]:
        """Commit `context`, then read the next-word prediction bubble."""
        got, _ = self.enter(keys, context, keep=True)
        if got != context:
            self.r.clear()
            return []
        words = [c for _, c in self.r.snapshot().candidates]
        self.r.clear()
        return words


def worker(variant: str, mode: str, set_name: str) -> None:
    from rime_harness import CONTROL_MASK, SHIFT_MASK, open_rime
    lines, sents = load(set_name)
    extra = {f"{SCHEMA}.custom.yaml": "patch:\n" + VARIANTS[variant]}
    rime = open_rime(f"learn-{variant}-{mode}", "project", SCHEMA, extra=extra)
    t = Typist(rime, SHIFT_MASK, CONTROL_MASK)
    out: dict = {"lines": [None] * len(lines), "end": [None] * len(lines), "general": []}

    def measure(i: int, slot: str):
        ln = lines[i]
        if ln["kind"] in "PC":
            out[slot][i] = t.preview(ln["keys"], ln["split"])
        elif ln["kind"] == "Q":
            out[slot][i] = t.predictions(ln["keys"], ln["text"].split("|")[0])

    if mode == "baseline":
        for i, ln in enumerate(lines):
            if ln["kind"] in "PC":
                measure(i, "lines")
        for i, ln in enumerate(lines):  # Q commits text: after the P/C lines
            if ln["kind"] == "Q":
                measure(i, "lines")
    else:
        taught: set[str] = set()
        i = 0
        while i < len(lines):
            group = lines[i]["group"]
            j = i
            while j < len(lines) and lines[j]["group"] == group:
                j += 1
            for k in range(i, j):  # teaching lines first, then the probes
                ln = lines[k]
                if ln["kind"] == "H":  # earlier typing: committed like any sentence
                    got, _ = t.enter(ln["keys"], ln["split"])
                    out["lines"][k] = {"got": got, "picks": 0, "repeat": False}
                elif ln["kind"] == "T":
                    got, picks = t.enter(ln["keys"], ln["split"])
                    out["lines"][k] = {"got": got, "picks": picks, "repeat": ln["text"] in taught}
                    taught.add(ln["text"])
                elif ln["kind"] == "A":
                    right, wrong = ln["text"].split("|")
                    got, picks = t.enter(ln["keys"], wrong)
                    out["lines"][k] = {"got": got, "picks": picks}
                    taught.add(right)
                elif ln["kind"] == "E":
                    out["lines"][k] = {"got": t.english(ln["keys"]), "picks": 0}
            for k in range(i, j):
                measure(k, "lines")
            i = j
        for k, ln in enumerate(lines):
            if ln["kind"] in "PC":
                measure(k, "end")
    out["general"] = [t.preview(keys) for keys, _ in sents]
    out["ms_per_key"] = sum(t.key_ms) / max(len(t.key_ms), 1)
    rime.close()
    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))


# -- scoring -----------------------------------------------------------------
def frac(xs: list[bool]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def score(lines, sents, base, learned) -> dict:
    m: dict = {}

    def right(i, res):
        return res[i] == lines[i]["text"]

    groups = {
        "Probe": [i for i, ln in enumerate(lines) if ln["kind"] == "P"
                  and not any(c.isascii() and c.isalpha() for c in ln["text"])],
        "Contrast": [i for i, ln in enumerate(lines) if ln["kind"] == "C"],
        "English": [i for i, ln in enumerate(lines) if ln["kind"] == "P"
                    and any(c.isascii() and c.isalpha() for c in ln["text"])],
    }
    for name, idx in groups.items():
        m[name] = {"n": len(idx),
                   "base": frac([right(i, base["lines"]) for i in idx]),
                   "now": frac([right(i, learned["lines"]) for i in idx]),
                   "end": frac([right(i, learned["end"]) for i in idx])}
    q = [i for i, ln in enumerate(lines) if ln["kind"] == "Q"]
    for res, tag in ((base, "base"), (learned, "now")):
        top1 = [bool(res["lines"][i]) and res["lines"][i][0] == lines[i]["text"].split("|")[1] for i in q]
        top3 = [lines[i]["text"].split("|")[1] in (res["lines"][i] or [])[:3] for i in q]
        m.setdefault("Pred@1", {"n": len(q)})[tag] = frac(top1)
        m.setdefault("Pred@3", {"n": len(q)})[tag] = frac(top3)
    m["General"] = {"n": len(sents),
                    "base": frac([g == s[1] for g, s in zip(base["general"], sents)]),
                    "now": frac([g == s[1] for g, s in zip(learned["general"], sents)])}
    teach = [learned["lines"][i] for i, ln in enumerate(lines) if ln["kind"] == "T"]
    m["Picks"] = {"n": len(teach), "now": sum(x["picks"] for x in teach if not x["repeat"])}
    rep = [x["picks"] for x in teach if x["repeat"]]
    m["Repeat"] = {"n": len(rep), "now": sum(rep)}
    taught_ok = [x["got"] == lines[i]["text"] for i, x in
                 ((i, learned["lines"][i]) for i, ln in enumerate(lines) if ln["kind"] in "TE")]
    m["Taught"] = {"n": len(taught_ok), "now": frac(taught_ok)}
    m["ms/key"] = {"base": base["ms_per_key"], "now": learned["ms_per_key"]}
    return m


def run(variant: str, mode: str, set_name: str) -> dict:
    proc = subprocess.run([sys.executable, __file__, "--worker", variant, mode, set_name],
                          capture_output=True)
    if proc.returncode:
        raise SystemExit(f"{variant}/{mode} failed:\n{proc.stderr.decode('utf-8', 'replace')}")
    for line in proc.stderr.decode("utf-8", "replace").splitlines():
        if line.startswith("rime_harness:"):  # e.g. the segmentation guard tripped
            print(line, file=sys.stderr)
    return json.loads(proc.stdout.decode("utf-8"))


def git_rev() -> str:
    try:
        rev = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
        dirty = subprocess.run(["git", "diff", "--quiet"], cwd=ROOT).returncode
        return rev + ("+dirty" if dirty else "")
    except Exception:
        return "?"


def fmt(v) -> str:
    if isinstance(v, float):
        return "—" if v != v else (f"{v:.1f}" if v > 1.5 else f"{v * 100:.1f}%")
    return str(v)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variants", nargs="+", default=["asus", "rime"], choices=list(VARIANTS))
    ap.add_argument("--set", default="dev", choices=list(STEMS))
    ap.add_argument("--note", default="")
    ap.add_argument("--worker", nargs=3, help=argparse.SUPPRESS)
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()
    if args.worker:
        worker(*args.worker)
        return

    lines, sents = load(args.set)
    base = run("nolearn", "baseline", args.set)  # nothing is taught in a baseline
    stamp = dt.datetime.now().isoformat(timespec="seconds")
    rev = git_rev()
    report = [f"## Learning eval {stamp} @ {rev} set={args.set} {args.note}".rstrip(), ""]
    records, failures = [], []
    for variant in args.variants:
        learned = run(variant, "learned", args.set)
        m = score(lines, sents, base, learned)
        records.append({"time": stamp, "rev": rev, "note": args.note, "set": args.set,
                        "variant": variant, "metrics": m})
        report.append(f"### {variant}")
        report.append("| metric | n | base | now | end |")
        report.append("|---|---:|---:|---:|---:|")
        for name, v in m.items():
            report.append(f"| {name} | {v.get('n', '')} | {fmt(v.get('base', ''))} | "
                          f"{fmt(v.get('now', ''))} | {fmt(v.get('end', ''))} |")
        report.append("")
        if not args.set.endswith("test"):  # held-out results are never shown line by line
            for i, ln in enumerate(lines):
                got = learned["lines"][i]
                if ln["kind"] in "PC" and got != ln["text"]:
                    failures.append((variant, ln["group"], ln["kind"], ln["text"], got,
                                     learned["end"][i] or ""))
                elif ln["kind"] in "TAE" and got["got"] != ln["text"].split("|")[-1 if ln["kind"] == "A" else 0]:
                    failures.append((variant, ln["group"], ln["kind"], ln["text"], got["got"], ""))
                elif ln["kind"] == "Q":
                    want = ln["text"].split("|")[1]
                    if not got or got[0] != want:
                        failures.append((variant, ln["group"], "Q", ln["text"], " ".join(got or []), ""))
            for (keys, text), g in zip(sents, learned["general"]):
                if g != text:
                    failures.append((variant, "general", "S", text, g, ""))
    print("\n".join(report))
    if not args.no_save:
        REPORTS.mkdir(exist_ok=True)
        with HISTORY.open("a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if not args.set.endswith("test"):
            detail = REPORTS / "learn_failures.tsv"
            detail.write_text("variant\tgroup\tkind\texpected\tgot\tend\n" + "\n".join(
                "\t".join(map(str, x)) for x in failures) + "\n", encoding="utf-8")
            print(f"(dev failures in {detail.relative_to(ROOT)})")


if __name__ == "__main__":
    main()
