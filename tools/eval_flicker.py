"""How much does the inline text jump while a sentence is being typed?

Every sentence of the eval sets is typed key by key and the inline text is
compared before and after each key:

  Rewrite  keys after which a character that was already shown, other than
           the last one, changed or went away (the last one changes
           legitimately while its syllable is being typed), as a share of
           all keys; Sent = sentences where it happens at least once
  Far      ...three or more characters back, per 100 keys
  Mid      the same, counting only keys that are not tone keys: nothing was
           finished by such a key, so nothing already shown should change
  ms/key   mean process_key latency

The final text is not scored here (tools/eval_ime.py does that). Dev sets
may list examples (--show N); held-out sets only ever give totals.

    python tools/eval_flicker.py [--variants asus stock] [--show 10]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eval_ime  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SETS = ["dev", "test", "mixed", "mixed_test", "en_short", "en_short_test"]
TONE_KEYS = set(" 6347")


def worker(variant: str) -> None:
    from rime_harness import SHIFT_MASK, open_rime
    config, schema, exclude, extra = eval_ime.VARIANTS[variant]
    extra = dict(extra)
    custom = f"{schema}.custom.yaml"
    extra[custom] = ((extra.get(custom) or "patch:\n") + "  translator/enable_user_dict: false\n"
                     + "  asus_learn/enabled: false\n")
    for item in filter(None, os.environ.get("EVAL_PATCH", "").split(";")):
        key, _, value = item.partition("=")
        extra[custom] += f'  {key}: "{value}"\n'
    rime = open_rime(f"flicker-{variant}", config, schema, exclude=exclude, extra=extra)
    rime.set_option("prediction", False)
    sets = eval_ime.build_sets()
    out = {}
    for name in SETS:
        if name not in sets:
            continue
        rows = []
        for keys, text in sets[name]:
            if variant == "stock" and any(ch.isupper() for ch in keys):
                continue  # stock Zhuyin has no capitals
            rime.clear(); rime.key(">", SHIFT_MASK); rime.clear()
            prev, steps, t0 = "", [], time.perf_counter()
            for ch in keys:
                rime.key(ch, SHIFT_MASK if ch.isupper() else 0)
                cur = rime.snapshot().preview
                n = 0
                while n < min(len(prev), len(cur)) and prev[n] == cur[n]:
                    n += 1
                steps.append((ch, len(prev) - n, prev, cur))
                prev = cur
            ms = (time.perf_counter() - t0) * 1000 / max(len(keys), 1)
            rows.append({"text": text, "ms": ms,
                         "steps": [(ch, back, a, b) for ch, back, a, b in steps if back >= 2],
                         "keys": len(keys), "mid_keys": sum(ch not in TONE_KEYS for ch in keys)})
        out[name] = rows
    rime.close()
    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variants", nargs="+", default=["asus"], choices=list(eval_ime.VARIANTS))
    ap.add_argument("--show", type=int, default=0, help="examples to list per dev set")
    ap.add_argument("--worker", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.worker:
        worker(args.worker)
        return
    print("| variant | set | n | Rewrite | Sent | Far /100 keys | Mid | ms/key |")
    print("|---|---|---:|---:|---:|---:|---:|---:|")
    for variant in args.variants:
        proc = subprocess.run([sys.executable, __file__, "--worker", variant], capture_output=True)
        if proc.returncode:
            raise SystemExit(f"{variant} failed:\n{proc.stderr.decode('utf-8', 'replace')}")
        res = json.loads(proc.stdout.decode("utf-8"))
        for name, rows in res.items():
            keys = sum(r["keys"] for r in rows)
            mid_keys = sum(r["mid_keys"] for r in rows)
            rewrites = sum(len(r["steps"]) for r in rows)
            far = sum(sum(back >= 3 for _, back, _, _ in r["steps"]) for r in rows)
            mid = sum(sum(ch not in TONE_KEYS for ch, _, _, _ in r["steps"]) for r in rows)
            hit = sum(bool(r["steps"]) for r in rows)
            ms = sum(r["ms"] for r in rows) / max(len(rows), 1)
            print(f"| {variant} | {name} | {len(rows)} | {rewrites / max(keys, 1):.1%} | {hit / max(len(rows), 1):.0%} "
                  f"| {100 * far / max(keys, 1):.1f} | {mid / max(mid_keys, 1):.1%} | {ms:.2f} |")
            if args.show and "test" not in name:
                shown = 0
                for r in rows:
                    for ch, back, a, b in r["steps"]:
                        if shown < args.show:
                            print(f"    {r['text']}: {a!r} -> {b!r} (key {ch!r})")
                            shown += 1


if __name__ == "__main__":
    main()
