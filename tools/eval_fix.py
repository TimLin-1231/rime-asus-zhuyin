"""Offline experiment for 組字中一鍵修正 (Ctrl+R, not built yet): a language
model is given what the IME shows for a sentence typed in Zhuyin, with the
Zhuyin that was typed, and writes the sentence the user meant, changing as
little as possible: wrong homophones, characters spoiled by a wrong, missing,
extra or swapped key, 的/得/地 and 在/再. English words and numbers stay.

Any OpenAI-compatible chat endpoint: llama.cpp's server for the local models
(started here), or a cloud model (--endpoint, --api-model, --key-file; the
key is read from the file and never printed).

    python tools/eval_fix.py --prepare            type the sets once with the
                                                  IME (.harness/fix_cases.json)
    python tools/eval_fix.py --model qwen9b       correct them, score
    python tools/eval_fix.py --model taiwan --sets dev,dev_typo
    python tools/eval_fix.py --model qwen --rescore   the answers of the last
                                                  full run through the current
                                                  guard (no model needed)

Sets come from tools/eval_ime.py (the typo sets are the same seeded
injections). Dev sets list every case that changed; held-out sets (test,
test_*, *_test) print totals only, as everywhere in this project. Fixed =
wrong before, right after; broken = right before, wrong after. Results are
appended to reports/fix_history.jsonl.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / ".harness" / "fix_cases.json"
HISTORY = ROOT / "reports" / "fix_history.jsonl"
SETS = ["dev", "dev_typo", "dev_typo_end", "dev_stray", "dev_omit", "dev_swap", "particles", "mixed", "numbers",
        "test", "test_typo", "test_typo_end", "test_stray", "test_omit", "test_swap", "particles_test",
        "mixed_test", "numbers_test"]
# Local models (tools/fetch_assist.py; the 9B outside D:\Document, which
# Google Drive backs up).
MODELS = {
    "taiwan": ROOT / "models" / "Llama-3.2-Taiwan-3B.Q3_K_M.gguf",
    "qwen": ROOT / "models" / "Qwen3.5-4B-Q4_K_M.gguf",
    "qwen9b": Path(r"D:\.rime-harness\models\Qwen3.5-9B-Q4_K_M.gguf"),
}

SYSTEM = (
    "你是台灣繁體中文注音輸入法的校正助手。使用者用注音打了一句話，輸入法把它轉成了中文，"
    "但可能有錯：選錯同音字、按錯鍵或少打、多打、打反一個鍵造成的錯字、「的／得／地」或「在／再」用錯。"
    "請根據使用者打的注音和整句的意思，寫出使用者想打的那句話。"
    "只改真的錯的字，盡量少改：不要改寫、不要潤飾、不要增刪標點、不要換成同義詞。"
    "英文單字、數字和符號保持原樣。原句沒有錯就原樣照抄。只輸出改好的句子，不要解釋。"
)
# Written for this experiment; none of them is in an eval set.
EXAMPLES = [
    ("ㄨㄛˇ ㄇㄣ˙ ㄒㄧㄚˋ ㄓㄡ ㄗㄞˋ ㄐㄧㄢˋ ㄇㄧㄢˋ", "我們下週在見面", "我們下週再見面"),
    ("ㄊㄚ ㄔㄤˋ ㄉㄜ˙ ㄈㄟ ㄔㄤˊ ㄏㄠˇ ㄊㄧㄥ", "她唱的非常好聽", "她唱得非常好聽"),
    ("ㄓㄜˋ ㄍㄜ˙ ㄐㄧㄚˋ ㄎㄜˊ ㄏㄣˇ ㄆㄧㄢˊ ㄧˊ", "這個價殼很便宜", "這個價格很便宜"),
    ("ㄓㄜˋ ㄐㄧㄚ ㄘㄢ ㄊㄧㄥ ㄉㄜ˙ ㄈㄨˊ ㄨˋ ㄏㄣˇ ㄏㄠˇ", "這家餐廳的服物很好", "這家餐廳的服務很好"),
    ("ㄒㄧㄚˋ ㄌㄧˇ ㄅㄞˋ ㄉㄜ˙ demo ㄒㄧㄢ ㄑㄩˇ ㄒㄧㄠ", "下禮拜的demo先取消", "下禮拜的demo先取消"),
]


def is_held_out(name: str) -> bool:
    return name == "test" or name.startswith("test_") or name.endswith("_test")


# Dachen (大千) layout; Space is the first tone.
DACHEN = dict(zip("1qaz2wsxedcrfv5tgbyhnujm8ik,9ol.0p;/-",
                  "ㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓㄔㄕㄖㄗㄘㄙㄧㄨㄩㄚㄛㄜㄝㄞㄟㄠㄡㄢㄣㄤㄥㄦ"))
TONES = {" ": "", "6": "ˊ", "3": "ˇ", "4": "ˋ", "7": "˙"}


def typed_zhuyin(keys: str, got: str) -> str:
    """The keys as Zhuyin, a space after each tone key; the English words and
    numbers the IME shows (in got) stay as typed."""
    literal = [w.lower() for w in re.findall(r"[A-Za-z0-9]+", got)]
    out, i = [], 0
    while i < len(keys):
        if literal and keys[i:i + len(literal[0])].lower() == literal[0]:
            out.append(f" {keys[i:i + len(literal[0])]} ")
            i += len(literal.pop(0))
            continue
        ch = keys[i]
        if ch in TONES:
            out.append(TONES[ch] + " ")
        else:
            out.append(DACHEN.get(ch.lower(), ch))
        i += 1
    return " ".join("".join(out).split())


# -- prepare: what the IME shows and commits for each case --------------------
def prepare() -> None:
    from eval_ime import VARIANTS, build_sets
    from rime_harness import SHIFT_MASK, open_rime

    sets = build_sets()
    config, schema, exclude, extra = VARIANTS["asus"]
    extra = dict(extra)
    extra[f"{schema}.custom.yaml"] = ("patch:\n  translator/enable_user_dict: false\n"
                                      "  asus_learn/enabled: false\n")
    r = open_rime("fix", config, schema, exclude=exclude, extra=extra)
    cases = []
    for name in SETS:
        for keys, text in sets.get(name, []):
            r.clear()
            r.key(">", SHIFT_MASK)  # commit 。: the same left context for every case
            r.clear()
            for ch in keys:
                r.key(ch, SHIFT_MASK if ch.isupper() else 0)
            snap = r.snapshot()
            r.key("Return")
            cases.append({"set": name, "keys": keys, "zhuyin": snap.preedit, "shown": snap.preview,
                          "got": "".join(r.committed), "text": text})
    r.close()
    CASES.parent.mkdir(exist_ok=True)
    CASES.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    print(f"{len(cases)} cases -> {CASES.relative_to(ROOT)}")


# -- correct ----------------------------------------------------------------
def messages(zhuyin: str, got: str) -> list[dict]:
    msgs = [{"role": "system", "content": SYSTEM}]
    for zy, shown, want in EXAMPLES:
        msgs.append({"role": "user", "content": f"注音：{zy}\n輸入法：{shown}"})
        msgs.append({"role": "assistant", "content": want})
    msgs.append({"role": "user", "content": f"注音：{zhuyin}\n輸入法：{got}"})
    return msgs


def text_keys(text: str) -> str | None:
    """The keys that type text (one reading per character, words first);
    English words and numbers as they are, punctuation left out."""
    from zhuyin import reading_keys

    keys = []
    for run in re.findall(r"[A-Za-z0-9]+|[㐀-鿿]+", text):
        if run.isascii():
            keys.append(run.lower())
        else:
            try:
                keys.extend(reading_keys(run)[0])
            except KeyError:
                return None
    return "".join(keys)


def key_distance(a: str, b: str) -> int:
    """Edits between two key strings: a key wrong, missing, extra, or two
    neighbouring keys swapped each count one (optimal string alignment)."""
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[len(a)][len(b)]


# The corrected sentence must read as what was typed at most this many key
# edits worse than what the IME showed (a slip is one edit; comparing with
# the IME's own text cancels readings that reading_keys gets wrong for
# polyphonic characters). Rewording (很多 -> 許多, 消息 -> 訊息) and dropped
# words fail it.
GUARD_EXTRA_EDITS = 1
# Which of these is meant cannot be read from the keys or the sentence.
PRONOUNS = set("他她它牠祂你妳您")


def guard(out: str, case: dict) -> bool:
    got, typed = case["got"], case["keys"].lower()
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, got, out, autojunk=False).get_opcodes():
        if op == "replace" and any(a != b and a in PRONOUNS and b in PRONOUNS
                                   for a, b in zip(got[i1:i2], out[j1:j2])):
            return False
    keys_out, keys_got = text_keys(out), text_keys(got)
    if keys_out is None or keys_got is None:
        return False
    d_out, d_got = key_distance(typed, keys_out), key_distance(typed, keys_got)
    # English words and numbers stay as the IME has them (no 10 -> 十, python
    # -> Python), unless they are what a slip left behind (早2攢 for 早點): then
    # the correction must read closer to the keys than the IME's text did.
    if re.findall(r"[A-Za-z0-9]+", out) != re.findall(r"[A-Za-z0-9]+", got):
        return d_out < d_got
    return d_out <= d_got + GUARD_EXTRA_EDITS


def tidy(out: str, got: str) -> str:
    """The model's sentence, or the original when the answer is not one."""
    out = out.strip().split("\n")[0].strip().strip("「」\"'“”")
    if " " not in got:
        out = out.replace(" ", "")  # spaces put around English words and numbers
    for prefix in ("輸入法：", "改正：", "修正："):
        if out.startswith(prefix):
            out = out[len(prefix):].strip()
    if not out or len(out) > 2 * len(got) + 4 or len(out) < len(got) // 2:
        return got
    # No punctuation the original did not have.
    while out and out[-1] in "。！？，" and not got.endswith(out[-1]):
        out = out[:-1]
    return out


def correct(endpoint: str, api_model: str, key: str, case: dict) -> tuple[str, float]:
    """The model's answer (tidied, before the guard) and the time it took."""
    body = {"messages": messages(typed_zhuyin(case["keys"], case["got"]), case["got"]), "max_tokens": 2 * len(case["got"]) + 32,
            "temperature": 0.0}
    if api_model:
        body["model"] = api_model
    else:
        body["cache_prompt"] = True
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(endpoint, data=json.dumps(body).encode("utf-8"), headers=headers)
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    dt = time.perf_counter() - t0
    return tidy(data["choices"][0]["message"].get("content") or "", case["got"]), dt


def guarded(raw: str, case: dict, use_guard: bool) -> str:
    return case["got"] if use_guard and raw != case["got"] and not guard(raw, case) else raw


def char_moves(c: dict) -> tuple[int, int]:
    """Characters made right / made wrong (texts of the same length only)."""
    if len(c["got"]) != len(c["text"]) or len(c["after"]) != len(c["text"]):
        return 0, 0
    trio = list(zip(c["got"], c["after"], c["text"]))
    return (sum(g != t and a == t for g, a, t in trio), sum(g == t and a != t for g, a, t in trio))


def score(label: str, cases: list[dict], times: list[float], only: list[str]) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    totals = {}
    print(f"{'set':<15} {'n':>4} {'before':>7} {'after':>7} {'fixed':>6} {'broken':>7} {'chars+':>7} {'chars-':>7}")
    for name in only:
        cs = [c for c in cases if c["set"] == name]
        if not cs:
            continue
        before = sum(c["got"] == c["text"] for c in cs)
        after = sum(c["after"] == c["text"] for c in cs)
        fixed = sum(c["got"] != c["text"] and c["after"] == c["text"] for c in cs)
        broken = sum(c["got"] == c["text"] and c["after"] != c["text"] for c in cs)
        plus = sum(char_moves(c)[0] for c in cs)
        minus = sum(char_moves(c)[1] for c in cs)
        totals[name] = {"n": len(cs), "before": before, "after": after, "fixed": fixed, "broken": broken,
                        "chars_fixed": plus, "chars_broken": minus}
        print(f"{name:<15} {len(cs):>4} {before:>7} {after:>7} {fixed:>6} {broken:>7} {plus:>7} {minus:>7}")
    for group, pred in (("dev total", lambda n: not is_held_out(n)), ("held-out total", is_held_out)):
        t = [v for n, v in totals.items() if pred(n)]
        if t:
            s = {k: sum(v[k] for v in t) for k in ("n", "before", "after", "fixed", "broken")}
            print(f"{group:<15} {s['n']:>4} {s['before']:>7} {s['after']:>7} {s['fixed']:>6} {s['broken']:>7}")
    times = sorted(times)
    med = times[len(times) // 2] if times else 0
    p90 = times[int(len(times) * 0.9)] if times else 0
    print(f"latency: median {med * 1000:.0f} ms, p90 {p90 * 1000:.0f} ms over {len(times)} calls")
    print("\nchanged cases (dev sets only):")
    for c in cases:
        if c["set"] in only and not is_held_out(c["set"]) and c["after"] != c["got"]:
            mark = "FIXED " if c["after"] == c["text"] else "BROKE " if c["got"] == c["text"] else "other "
            print(f"  {mark}[{c['set']}] {c['got']} -> {c['after']}  (want {c['text']})")
    HISTORY.parent.mkdir(exist_ok=True)
    with HISTORY.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": label,
                            "latency_ms": {"median": round(med * 1000), "p90": round(p90 * 1000)},
                            "totals": totals}, ensure_ascii=False) + "\n")


def ime_rescore(label: str, only: list[str]) -> None:
    """The answers saved by the last full run, through the IME itself:
    Ctrl+R, the answer as the front end gives it (lua/asus_fix.lua), Enter.
    Measures the guard as shipped (all readings of each character)."""
    from eval_ime import VARIANTS
    from rime_harness import CONTROL_MASK, SHIFT_MASK, open_rime

    raw = json.loads(raw_path(label).read_text(encoding="utf-8"))
    cases = [c for c in json.loads(CASES.read_text(encoding="utf-8")) if c["set"] in only]
    config, schema, exclude, extra = VARIANTS["asus"]
    extra = dict(extra)
    extra[f"{schema}.custom.yaml"] = ("patch:\n  translator/enable_user_dict: false\n"
                                      "  asus_learn/enabled: false\n")
    r = open_rime("fix-ime", config, schema, exclude=exclude, extra=extra)
    for c in cases:
        r.clear()
        r.key(">", SHIFT_MASK)
        r.clear()
        r.set_option("_asus_fix", True)
        for ch in c["keys"]:
            r.key(ch, SHIFT_MASK if ch.isupper() else 0)
        r.key("r", CONTROL_MASK)
        rid = r.get_property("asus_fix_request").partition("\t")[0]
        r.set_property("asus_fix_request", "")
        answer = raw["answers"][c["set"] + "\t" + c["keys"]]
        r.set_property("asus_fix_result", f"{rid}\t{answer}")
        r.key("F23")
        r.key("Return")
        c["after"] = "".join(r.committed)
    r.close()
    score(label + "+ime-guard (rescored)", cases, raw["times"], only)


def raw_path(label: str) -> Path:
    return ROOT / ".harness" / f"fix_raw_{re.sub(r'[^A-Za-z0-9._-]', '_', label)}.json"


def run(args: argparse.Namespace) -> None:
    only = args.sets.split(",") if args.sets else SETS
    cases = [c for c in json.loads(CASES.read_text(encoding="utf-8")) if c["set"] in only]
    suffix = "" if args.no_guard else "+guard"
    if args.ime_rescore:
        ime_rescore(args.model, only)
        return
    if args.rescore:
        # The model's answers from the last full run, through the current guard.
        label = args.model or args.api_model or args.endpoint
        raw = json.loads(raw_path(label).read_text(encoding="utf-8"))
        for c in cases:
            c["after"] = guarded(raw["answers"][c["set"] + "	" + c["keys"]], c, not args.no_guard)
        score(label + suffix + " (rescored)", cases, raw["times"], only)
        return
    model = None
    if args.endpoint:
        endpoint, label = args.endpoint, args.api_model or args.endpoint
        key = Path(args.key_file).read_text(encoding="utf-8").strip() if args.key_file else ""
    else:
        from assist.llm import Model
        model = Model(port=8733, context=4096, model=MODELS[args.model])
        model.start(timeout=300)
        endpoint, label, key = model.url + "/v1/chat/completions", args.model, ""
    times = []
    try:
        for i, c in enumerate(cases):
            c["raw"], dt = correct(endpoint, args.api_model, key, c)
            c["after"] = guarded(c["raw"], c, not args.no_guard)
            times.append(dt)
            if (i + 1) % 100 == 0:
                print(f"  {i + 1}/{len(cases)}", file=sys.stderr, flush=True)
    finally:
        if model:
            model.stop()
    if not args.sets:
        raw_path(label).write_text(json.dumps({"answers": {c["set"] + "	" + c["keys"]: c["raw"] for c in cases},
                                               "times": times}, ensure_ascii=False), encoding="utf-8")
    score(label + suffix, cases, times, only)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prepare", action="store_true")
    ap.add_argument("--model", choices=list(MODELS))
    ap.add_argument("--endpoint", help="an OpenAI-compatible chat completions URL instead of a local model")
    ap.add_argument("--api-model", default="", help="model name for --endpoint")
    ap.add_argument("--key-file", help="file holding the API key for --endpoint")
    ap.add_argument("--sets", help="comma-separated subset of: " + ",".join(SETS))
    ap.add_argument("--no-guard", action="store_true", help="keep corrections that no longer read as typed")
    ap.add_argument("--rescore", action="store_true", help="score the answers saved by the last full run again")
    ap.add_argument("--ime-rescore", action="store_true",
                    help="the saved answers through the IME (lua/asus_fix.lua): the guard as shipped")
    args = ap.parse_args()
    if args.prepare:
        prepare()
    if args.model or args.endpoint:
        run(args)


if __name__ == "__main__":
    main()
