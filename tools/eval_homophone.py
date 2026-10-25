"""Offline experiment: can a local language model fix the wrong homophones in
a finished conversion (城市馬上傳 -> 程式碼上傳) without breaking the right
ones? After BlendKey's experiment H (github.com/0rigind1865-bit/BlendKey,
experiments/ai-prediction): the model sees the conversion and writes it again,
and a llama.cpp grammar lets it write, at every position, only that character
or another character with the same reading (the IME's own candidates for the
syllable typed there). English words and numbers stay as they are.

    python tools/eval_homophone.py --prepare          convert the sets once
                                                       (.harness/homophone_cases.json)
    python tools/eval_homophone.py --model taiwan     correct them, score
    python tools/eval_homophone.py --model qwen

Dev sets list every case that changed; held-out (*_test) sets print totals
only, as everywhere in this project. Fixed = wrong before, right after;
broken = right before, wrong after.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "tests" / "eval"
CASES = ROOT / ".harness" / "homophone_cases.json"
HISTORY = ROOT / "reports" / "homophone_history.jsonl"
SETS = ["sentences", "particles", "mixed", "numbers", "sentences_test", "particles_test", "mixed_test",
        "numbers_test", "blendkey_test"]
# BlendKey's Tests/Fixtures/sentences.tsv (MIT, "ㄊㄚ-ㄆㄠˇ<TAB>他跑"), fetched by
# hand into third_party/blendkey (not versioned); held out like the others.
BLENDKEY = ROOT / "third_party" / "blendkey" / "sentences.tsv"


def load_rows(name: str) -> list[tuple[str, str, str]]:
    """(keys, Zhuyin syllables separated by spaces, text) of a set."""
    from zhuyin import symbols_to_keys

    rows = []
    if name == "blendkey_test":
        if not BLENDKEY.exists():
            return []
        for line in BLENDKEY.read_text(encoding="utf-8").splitlines():
            if line.strip():
                zy, text = line.split("\t")[:2]
                zy = zy.replace("-", " ")
                rows.append((symbols_to_keys(zy), " ".join(s if s[-1] in "ˊˇˋ˙" else s + "ˉ" for s in zy.split()),
                             text))
        return rows
    for line in (EVAL / f"{name}.tsv").read_text(encoding="utf-8").splitlines():
        if line.strip():
            keys, zhuyin, text, *_ = line.split("\t")
            rows.append((keys, zhuyin, text))
    return rows
MODELS = {
    "taiwan": ROOT / "models" / "Llama-3.2-Taiwan-3B.Q3_K_M.gguf",
    "qwen": ROOT / "models" / "Qwen3.5-4B-Q4_K_M.gguf",
}
ALTERNATIVES = 12  # same-reading characters offered per position (+ the one shown)

# Written for this experiment; none of them is in an eval set.
EXAMPLES = [
    ("我們下週在見面討論", "我們下週再見面討論"),
    ("他打的自真的很漂亮", "他打的字真的很漂亮"),
    ("今天的會議改到三點", "今天的會議改到三點"),
    ("這份合約要千名才算數", "這份合約要簽名才算數"),
    ("晚餐想吃牛肉麵", "晚餐想吃牛肉麵"),
]


def is_held_out(name: str) -> bool:
    return name.endswith("_test")


# -- prepare: conversions and the same-reading characters per position --------
def prepare() -> None:
    from rime_harness import SHIFT_MASK, open_rime
    from zhuyin import symbols_to_keys

    r = open_rime("homophone", extra={"asus_zhuyin.custom.yaml":
                                      "patch:\n  translator/enable_user_dict: false\n  asus_learn/enabled: false\n"})
    same: dict[str, list[str]] = {}

    def homophones(syllable: str) -> list[str]:
        if syllable not in same:
            r.clear()
            r.type(symbols_to_keys(syllable))
            r.key("Down")  # the full list (closed, the bubble shows one or two)
            out: list[str] = []
            for _ in range(3):  # a few pages
                for text, _ in r.snapshot().candidates:
                    if len(text) == 1 and text not in out:
                        out.append(text)
                if len(out) >= ALTERNATIVES:
                    break
                r.key("Page_Down")
            r.clear()
            same[syllable] = out[:ALTERNATIVES]
        return same[syllable]

    cases = []
    for name in SETS:
        for keys, zhuyin, text in load_rows(name):
            r.clear()
            r.key(">", SHIFT_MASK)  # commit 。: the same left context for every case
            r.clear()
            for ch in keys:
                r.key(ch, SHIFT_MASK if ch.isupper() else 0)
            r.key("Return")
            got = r.snapshot().committed
            # One slot per character: a syllable's characters, or a fixed piece.
            slots: list[list[str]] = []
            for tok in zhuyin.split():
                if tok.startswith("["):
                    slots.extend([c] for c in tok[1:-1])
                else:
                    slots.append(["?", *homophones(tok)])
            aligned = len(slots) == len(got)
            if aligned:
                for i, ch in enumerate(got):
                    if slots[i][0] == "?":
                        slots[i] = [ch] + [c for c in slots[i][1:] if c != ch]
                    elif slots[i] != [ch]:
                        aligned = False
            cases.append({"set": name, "text": text, "got": got, "aligned": aligned,
                          "slots": slots if aligned else []})
    r.close()
    CASES.parent.mkdir(exist_ok=True)
    CASES.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    print(f"{len(cases)} cases, {sum(c['aligned'] for c in cases)} aligned -> {CASES.relative_to(ROOT)}")


# -- correct ----------------------------------------------------------------
def grammar(slots: list[list[str]]) -> str:
    rules = ["root ::= " + " ".join(f"s{i}" for i in range(len(slots))) + ' "\\n"']
    for i, alts in enumerate(slots):
        rules.append(f"s{i} ::= " + " | ".join(json.dumps(a, ensure_ascii=False) for a in alts))
    return "\n".join(rules)


def prompt(text: str) -> str:
    shots = "".join(f"錯字：{a}\n正確：{b}\n\n" for a, b in EXAMPLES)
    return "以下每一句都把同音錯字改成正確的字，沒有錯就照抄。\n\n" + shots + f"錯字：{text}\n正確："


def correct(url: str, case: dict) -> tuple[str, float]:
    body = json.dumps({"prompt": prompt(case["got"]), "grammar": grammar(case["slots"]),
                       "n_predict": 4 * len(case["slots"]) + 8, "temperature": 0.0,
                       "cache_prompt": True}).encode("utf-8")
    t0 = time.perf_counter()
    req = urllib.request.Request(url + "/completion", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        out = json.loads(resp.read().decode("utf-8")).get("content") or ""
    dt = time.perf_counter() - t0
    out = out.strip("\n")
    return (out if len(out) == len(case["got"]) else case["got"]), dt


def score(model_name: str) -> None:
    from assist.llm import Model

    cases = json.loads(CASES.read_text(encoding="utf-8"))
    model = Model(port=8731, context=2048, model=MODELS[model_name])
    model.start()
    try:
        times = []
        for c in cases:
            if c["aligned"]:
                c["after"], dt = correct(model.url, c)
                times.append(dt)
            else:
                c["after"] = c["got"]
    finally:
        model.stop()

    sys.stdout.reconfigure(encoding="utf-8")
    totals = {}
    def char_moves(c: dict) -> tuple[int, int]:
        """Characters made right / made wrong (same length texts only)."""
        if len(c["got"]) != len(c["text"]) or len(c["after"]) != len(c["text"]):
            return 0, 0
        trio = list(zip(c["got"], c["after"], c["text"]))
        return (sum(g != t and a == t for g, a, t in trio), sum(g == t and a != t for g, a, t in trio))

    print(f"{'set':<16} {'n':>4} {'before':>7} {'after':>7} {'fixed':>6} {'broken':>7} {'unreach':>8}"
          f" {'chars+':>7} {'chars-':>7}")
    for name in SETS:
        cs = [c for c in cases if c["set"] == name]
        if not cs:
            continue
        before = sum(c["got"] == c["text"] for c in cs)
        after = sum(c["after"] == c["text"] for c in cs)
        fixed = sum(c["got"] != c["text"] and c["after"] == c["text"] for c in cs)
        broken = sum(c["got"] == c["text"] and c["after"] != c["text"] for c in cs)
        # wrong, and the right text cannot be written with the offered characters
        unreach = sum(c["got"] != c["text"] and (not c["aligned"] or len(c["slots"]) != len(c["text"]) or any(
            t not in s for t, s in zip(c["text"], c["slots"]))) for c in cs)
        plus = sum(char_moves(c)[0] for c in cs)
        minus = sum(char_moves(c)[1] for c in cs)
        totals[name] = {"n": len(cs), "before": before, "after": after, "fixed": fixed, "broken": broken,
                        "unreachable": unreach, "chars_fixed": plus, "chars_broken": minus}
        print(f"{name:<16} {len(cs):>4} {before:>7} {after:>7} {fixed:>6} {broken:>7} {unreach:>8}"
              f" {plus:>7} {minus:>7}")
    times.sort()
    med = times[len(times) // 2] if times else 0
    p90 = times[int(len(times) * 0.9)] if times else 0
    print(f"latency: median {med * 1000:.0f} ms, p90 {p90 * 1000:.0f} ms over {len(times)} calls")
    print("\nchanged cases (dev sets only):")
    for c in cases:
        if not is_held_out(c["set"]) and c["after"] != c["got"]:
            mark = "FIXED " if c["after"] == c["text"] else "BROKE " if c["got"] == c["text"] else "other "
            print(f"  {mark}[{c['set']}] {c['got']} -> {c['after']}  (want {c['text']})")
    with HISTORY.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": model_name,
                            "latency_ms": {"median": round(med * 1000), "p90": round(p90 * 1000)},
                            "totals": totals}, ensure_ascii=False) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prepare", action="store_true")
    ap.add_argument("--model", choices=list(MODELS))
    args = ap.parse_args()
    if args.prepare:
        prepare()
    if args.model:
        score(args.model)


if __name__ == "__main__":
    main()
