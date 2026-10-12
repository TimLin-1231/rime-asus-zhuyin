"""In-composition editing evaluation (stage 6).

Each line of tests/eval/edit*.tsv is the text the user wants. Its reading is
typed in one go; when the conversion is wrong, a simulated user fixes it
before committing, in two ways (before stage 6b Down listed from the start
of the input; that method, "old", is in reports/edit_history.jsonl):

  down    Down at the end lists the last word (stage 6b): pick there when the
          first wrong character is in that word, otherwise Esc and use the
          cursor as below
  cursor  move the cursor with Left/Right to the first wrong character
          (a number or English word is one step), or Home then Right when
          that is fewer keys; Down, pick the longest candidate matching the
          wanted text from there; repeat

    python tools/eval_edit.py                 # dev set
    python tools/eval_edit.py --set test      # held out: aggregates only

Metrics (over the lines whose conversion was wrong)
  Fixed     the committed text is the wanted text
  Keys      keys pressed to fix (arrows, Down, Page_Down, digits), mean
  Wrong     share of lines converted wrongly in the first place
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "tests" / "eval"
REPORTS = ROOT / "reports"
HISTORY = REPORTS / "edit_history.jsonl"
MAX_PAGES = 12
UNIT = re.compile(r"[A-Za-z][A-Za-z0-9\-']*|\d+(?:\.\d+)?|.")


def units(text: str) -> list[str]:
    """Cursor steps: one per character, a number or English word is one."""
    return UNIT.findall(text)


def load(set_name: str) -> list[tuple[str, str]]:
    stem = "edit" if set_name == "dev" else "edit_test"
    rows = []
    for line in (EVAL / f"{stem}.tsv").read_text(encoding="utf-8").splitlines():
        keys, _, text, _ = line.split("\t")
        rows.append((keys, text))
    return rows


class User:
    def __init__(self, rime, shift):
        self.r = rime
        self.shift = shift
        self.keys = 0

    def press(self, name: str) -> None:
        self.keys += 1
        self.r.key(name)

    def start(self, keys: str) -> str:
        self.r.clear()
        self.r.key(">", self.shift)  # sentence start: commit 。
        self.r.clear()
        for ch in keys:
            self.r.key(ch, self.shift if ch.isupper() else 0)
        self.keys = 0
        return self.r.snapshot().preview

    def pick(self, rest: str) -> str | None:
        """In the expanded list, pick the longest candidate that starts `rest`."""
        for _ in range(MAX_PAGES):
            snap = self.r.snapshot()
            best = None
            for i, (text, _) in enumerate(snap.candidates):
                if text and rest.startswith(text) and (best is None or len(text) > len(best[1])):
                    best = (i, text)
            if best:
                self.press(str(best[0] % 9 + 1))
                return best[1]
            if len(snap.candidates) < 9:
                return None
            self.press("Page_Down")
        return None

    def listing(self) -> bool:
        return bool(self.r.get_option("_asus_expanded"))

    def finish(self) -> str:
        # After a pick the list has moved on to the next character with its
        # first row, the text as it is, highlighted: Enter there commits.
        if self.r.snapshot().input:
            self.r.key("Return")
        if self.r.snapshot().input and self.listing():  # (the first row was something else)
            self.press("Escape")
        if self.r.snapshot().input:
            self.r.key("Return")
        out = "".join(self.r.committed)
        self.r.clear()
        return out

    def fix_down(self, target: str) -> str:
        want = units(target)
        shown = units(self.r.snapshot().preview)
        d = next((i for i, (a, b) in enumerate(zip(shown, want)) if a != b), min(len(shown), len(want)))
        self.press("Down")
        cands = self.r.snapshot().candidates
        start = len(shown) - len(units(cands[0][0])) if cands else len(shown)
        if start == d:  # the list starts at the wrong character
            got = self.pick("".join(want[d:]))
            if got is not None and units(self.r.snapshot().preview) == want:
                return self.finish()
            return self.fix_cursor(target, caret=None if got is None else d + len(units(got)))
        self.press("Escape")
        return self.fix_cursor(target)

    def fix_cursor(self, target: str, caret: int | None = None) -> str:
        want = units(target)
        for _ in range(20):
            shown = units(self.r.snapshot().preview)
            if shown == want or not self.r.snapshot().input:
                break
            d = next((i for i, (a, b) in enumerate(zip(shown, want)) if a != b),
                     min(len(shown), len(want)))
            here = len(shown) if caret is None else caret
            listing = self.listing()  # open after a pick: Left / Right move it
            if not listing and 1 + d < abs(here - d):
                self.press("Home")
                here = 0
            for _ in range(here - d):
                self.press("Left")
            for _ in range(d - here):
                self.press("Right")
            if not listing:
                self.press("Down")
            got = self.pick("".join(want[d:]))
            if got is None:
                break
            caret = d + len(units(got))
        return self.finish()


def worker(set_name: str) -> None:
    from rime_harness import SHIFT_MASK, open_rime
    rows = load(set_name)
    extra = {"asus_zhuyin.custom.yaml": "patch:\n  asus_learn/enabled: false\n"
                                        "  translator/enable_user_dict: false\n"}
    r = open_rime("edit-eval", "project", extra=extra)
    r.set_option("prediction", False)
    u = User(r, SHIFT_MASK)
    out = []
    for keys, text in rows:
        first = u.start(keys)
        res = {"first": first}
        if first != text:
            for method in ("down", "cursor"):
                u.start(keys)
                got = getattr(u, f"fix_{method}")(text)
                res[method] = {"got": got, "keys": u.keys}
        else:
            u.finish()
        out.append(res)
    r.close()
    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", default="dev", choices=["dev", "test"])
    ap.add_argument("--note", default="")
    ap.add_argument("--worker", help=argparse.SUPPRESS)
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()
    if args.worker:
        worker(args.worker)
        return
    rows = load(args.set)
    proc = subprocess.run([sys.executable, __file__, "--worker", args.set], capture_output=True)
    if proc.returncode:
        raise SystemExit(proc.stderr.decode("utf-8", "replace"))
    for line in proc.stderr.decode("utf-8", "replace").splitlines():
        if line.startswith("rime_harness:"):  # e.g. the segmentation guard tripped
            print(line, file=sys.stderr)
    res = json.loads(proc.stdout.decode("utf-8"))
    wrong = [(row, r) for row, r in zip(rows, res) if "down" in r]
    m = {"n": len(rows), "Wrong": len(wrong) / len(rows)}
    for method in ("down", "cursor"):
        ok = [r[method]["got"] == text for (_, text), r in wrong]
        m[method] = {"Fixed": sum(ok) / max(len(wrong), 1),
                     "Keys": sum(r[method]["keys"] for _, r in wrong) / max(len(wrong), 1)}
    rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                         capture_output=True, text=True).stdout.strip()
    stamp = dt.datetime.now().isoformat(timespec="seconds")
    print(f"## Edit eval {stamp} @ {rev} set={args.set} {args.note}".rstrip())
    print(f"lines {m['n']}, converted wrongly {m['Wrong']:.0%} ({len(wrong)})")
    print("| method | Fixed | Keys |\n|---|---:|---:|")
    for method in ("down", "cursor"):
        print(f"| {method} | {m[method]['Fixed']:.1%} | {m[method]['Keys']:.1f} |")
    if args.set == "dev":  # held-out lines are never shown one by one
        for (_, text), r in wrong:
            print(f"  {text}: first={r['first']} "
                  f"down={r['down']['got']}({r['down']['keys']}) "
                  f"cursor={r['cursor']['got']}({r['cursor']['keys']})")
    if not args.no_save:
        REPORTS.mkdir(exist_ok=True)
        with HISTORY.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"time": stamp, "rev": rev, "note": args.note, "set": args.set,
                                "metrics": m}, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
