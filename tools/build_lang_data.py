"""Build data for Chinese/English mixed input (lua/asus_lang.lua).

  rime/opencc/asus_en.txt, .json OpenCC text dict: key<TAB>rank:word (rank 0 = personal)
  rime/opencc/asus_en_prefix.*   prefix -> best completions (rank:word ...)
  rime/opencc/asus_en_fix.*      spelling correction keys -> words (rank:word ...)
  rime/opencc/asus_en_follow.*   English word -> the words that most often follow it
  rime/lua/asus_zhuyin_data.lua  Dachen key strings of valid Zhuyin syllables

English source: easy_en.dict.yaml from the old Weasel folder (its weight is
~1e6 - frequency rank), top --top words of length >= 3, plus
lexicon/english_words.txt (personal, always included, any length).
Which words are suggested first (completions, corrections) follows the word
frequencies of SymSpell's dictionary (Google Books Ngram x SCOWL, third_party/
symspell, fetched by tools/fetch_models.py): easy_en's ranks put neurone
before neural and optimise before optimize and give "occurred" no rank at
all. Recognising a word as English still uses easy_en's ranks. The word
pairs of the same source give the words that follow a word.
Zhuyin syllables come from terra_pinyin char readings.

    python tools/build_lang_data.py [--top 100000]
"""

from __future__ import annotations

import argparse
import itertools
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from zhuyin import keys_to_pinyin_map  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EASY_EN = Path(r"D:\Document\小狼豪輸入法\easy_en.dict.yaml")
PERSONAL_EN = ROOT / "lexicon" / "english_words.txt"
TONES = " 6347"


ZERO_RANK = 150000   # rank given to weight-0 words (plurals, email, ...)
PREFIX_MAX_RANK = 60000  # only common words are offered as completions
PREFIX_TOP = 5           # completions kept per prefix
FIX_MAX_RANK = 40000     # words offered as spelling corrections
FIX_MIN_LEN = 3
FIX_TOP = 5
ZERO_MIN_LEN = 4


def load_easy_en(top: int) -> dict[str, tuple[str, int]]:
    """key (lowercase letters) -> (display word, rank).

    Top `top` ranked words of length >= 3, plus weight-0 words of length >=
    ZERO_MIN_LEN (many plurals and newer words such as "slides", "email" have
    weight 0 in easy_en) at the poor rank ZERO_RANK.
    """
    best: dict[str, tuple[str, int]] = {}
    zero: dict[str, str] = {}
    body = False
    for line in EASY_EN.read_text(encoding="utf-8-sig").splitlines():
        if not body:
            body = line == "..."
            continue
        parts = line.split("\t")
        if len(parts) != 3 or not parts[2].isdigit():
            continue
        word, key, weight = parts[0], parts[1], int(parts[2])
        if len(key) < 3 or not key.isalpha() or not key.islower():
            continue
        if not word.replace("-", "").isalpha():
            continue
        if weight <= 0:
            if len(key) >= ZERO_MIN_LEN and (word == key or key not in zero):
                zero[key] = word
            continue
        if key not in best or weight > best[key][1]:
            best[key] = (word, weight)
    ranked = sorted(best.items(), key=lambda kv: -kv[1][1])[:top]
    out = {k: (w, rank + 1) for rank, (k, (w, _)) in enumerate(ranked)}
    for k, w in zero.items():
        out.setdefault(k, (w, ZERO_RANK))
    return out


SYM_DIR = ROOT / "third_party" / "symspell"
FOLLOW_TOP = 40      # followers kept per word


def load_symspell() -> dict[str, int]:
    """word -> frequency rank (1 = most frequent), {} without the data."""
    path = SYM_DIR / "frequency_dictionary_en_82_765.txt"
    if not path.exists():
        return {}
    out: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        word = line.split(" ")[0]
        if word.isalpha():
            out.setdefault(word.lower(), len(out) + 1)
    return out


def guess_rank(key: str, rank: int, sym: dict[str, int]) -> int:
    """Rank used to order suggestions (personal words, rank 0, stay first):
    the geometric mean of the two sources' ranks. Each is off on its own:
    easy_en has neurone before neural and optimise before optimize, SymSpell
    has classifieds before classification and no dataset. A word only one of
    them ranks keeps that rank."""
    if rank == 0 or key not in sym:
        return rank
    if rank >= ZERO_RANK:
        return sym[key]
    return round((sym[key] * rank) ** 0.5)


def build_follow_table(words: dict[str, tuple[str, int]]) -> int:
    """word -> the FOLLOW_TOP words that most often come right after it (best
    first), from SymSpell's word pairs. OpenCC text dict opencc/asus_en_follow.*."""
    path = SYM_DIR / "frequency_bigramdictionary_en_243_342.txt"
    if not path.exists():
        return 0
    pairs: dict[str, list[tuple[int, str]]] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        parts = line.split(" ")
        if len(parts) == 3 and parts[0].isalpha() and parts[1].isalpha() and parts[2].isdigit():
            a, b = parts[0].lower(), parts[1].lower()
            if len(b) >= 2:
                pairs.setdefault(a, []).append((int(parts[2]), words.get(b, (b, 0))[0]))
    lines = []
    for a in sorted(pairs, key=lambda s: s.encode()):
        best = sorted(pairs[a], key=lambda cw: -cw[0])[:FOLLOW_TOP]
        lines.append(f"{a}\t{' '.join(w for _, w in best)}")
    cc_dir = ROOT / "rime" / "opencc"
    (cc_dir / "asus_en_follow.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (cc_dir / "asus_en_follow.json").write_text(
        '{\n  "name": "asus_en_follow (GENERATED by tools/build_lang_data.py)",\n'
        '  "segmentation": {"type": "mmseg", "dict": {"type": "text", "file": "asus_en_follow.txt"}},\n'
        '  "conversion_chain": [{"dict": {"type": "text", "file": "asus_en_follow.txt"}}]\n}\n',
        encoding="utf-8")
    return len(lines)


def load_personal() -> dict[str, tuple[str, int]]:
    out = {}
    if PERSONAL_EN.exists():
        for word in PERSONAL_EN.read_text(encoding="utf-8").split():
            if not word.startswith("#"):
                out[word.lower()] = (word, 0)
    return out


def zhuyin_sets() -> tuple[set[str], set[str], set[str]]:
    toned = set(keys_to_pinyin_map())  # e.g. 'cl3', 'u ' (tone 1 = space)
    toneless = {k[:-1] for k in toned if k[-1] in TONES and len(k) > 1}
    prefixes = {t[:i] for t in toneless for i in range(1, len(t) + 1)}
    return toned, toneless, prefixes


MCB_DIR = ROOT / "third_party" / "mcbopomofo"
NEXT_TOP = 5          # continuations kept per prefix
NEXT_MAX_PREFIX = 4   # characters of committed text used as the lookup key


def build_next_table() -> int:
    """Prefix -> likely continuations, from McBopomofo phrases and Taiwan counts.

    Every phrase P with count c contributes (P[:k] -> P[k:], c) for each split
    k, so after "機器" the table suggests 學習 / 人. Written as an OpenCC text
    dict (opencc/asus_next.*): key<TAB>cont1 cont2 ... (best first).
    """
    occ: dict[str, int] = {}
    for line in (MCB_DIR / "phrase.occ").read_text(encoding="utf-8").splitlines():
        word, _, n = line.rpartition(" ")
        if word and n.isdigit() and len(word) >= 2:
            occ[word] = int(n)
    personal = ROOT / "lexicon" / "personal_words.txt"
    if personal.exists():  # personal words always count as frequent phrases
        for line in personal.read_text(encoding="utf-8").splitlines():
            word = line.split("\t")[0]
            if line and not line.startswith("#") and len(word) >= 2 and word.isalpha():
                occ[word] = max(occ.get(word, 0), 1000)
    scores: dict[str, dict[str, int]] = {}
    for phrase, n in occ.items():
        if not all("㐀" <= ch <= "鿿" for ch in phrase):
            continue
        for k in range(1, len(phrase)):
            prefix, cont = phrase[:k], phrase[k:]
            if len(prefix) > NEXT_MAX_PREFIX:
                break
            bucket = scores.setdefault(prefix, {})
            bucket[cont] = bucket.get(cont, 0) + n
    lines = []
    for prefix in sorted(scores, key=lambda s: s.encode()):
        best = sorted(scores[prefix].items(), key=lambda kv: -kv[1])[:NEXT_TOP]
        lines.append(f"{prefix}\t{' '.join(c for c, _ in best)}")
    cc_dir = ROOT / "rime" / "opencc"
    (cc_dir / "asus_next.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (cc_dir / "asus_next.json").write_text(
        '{\n  "name": "asus_next (GENERATED by tools/build_lang_data.py)",\n'
        '  "segmentation": {"type": "mmseg", "dict": {"type": "text", "file": "asus_next.txt"}},\n'
        '  "conversion_chain": [{"dict": {"type": "text", "file": "asus_next.txt"}}]\n}\n',
        encoding="utf-8")
    return len(lines)


TW_MIN_ESSAY = 50   # X must be a real word (essay.txt weight)
TW_MIN_COUNT = 20   # Y must be common in Taiwan (McBopomofo count)
PARTICLES = set("的得地在再")


def build_tw_table() -> int:
    """Mainland-only words -> the Taiwan word with the same reading.

    X: a word of librime's essay.txt (weight >= TW_MIN_ESSAY, 2-4 characters)
    that McBopomofo's Taiwan corpus never uses (宕機, 坐標, 賬號).
    Y: a McBopomofo word with count >= TW_MIN_COUNT whose reading is one of
    X's possible readings (當機, 座標, 帳號). OpenCC text dict
    opencc/asus_tw.* : X<TAB>Y,<Dachen keys of Y with "_" for Space>; the
    filter in lua/asus_tw.lua checks the typed keys before replacing.
    """
    from zhuyin import pinyin_to_symbols, symbols_to_keys, weasel_root

    def norm(syl: str) -> str:
        return syl.replace("ˉ", "")

    occ: dict[str, int] = {}
    for line in (MCB_DIR / "phrase.occ").read_text(encoding="utf-8").splitlines():
        word, _, n = line.rpartition(" ")
        if word and n.isdigit():
            occ[word] = int(n)
    # Taiwan words by reading: (syllables...) -> (count, word)
    by_reading: dict[tuple[str, ...], tuple[int, str]] = {}
    for line in (MCB_DIR / "BPMFMappings.txt").read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        word, syls = parts[0], tuple(norm(s) for s in parts[1:])
        n = occ.get(word, 0)
        if 2 <= len(word) <= 4 and len(syls) == len(word) and n >= TW_MIN_COUNT:
            if syls not in by_reading or n > by_reading[syls][0]:
                by_reading[syls] = (n, word)
    # Every reading of every character (terra_pinyin).
    readings: dict[str, set[str]] = {}
    data = weasel_root() / "data"
    body = False
    for line in (data / "terra_pinyin.dict.yaml").read_text(encoding="utf-8").splitlines():
        if not body:
            body = line == "..."
            continue
        parts = line.split("\t")
        if len(parts) >= 2 and len(parts[0]) == 1:
            try:
                readings.setdefault(parts[0], set()).add(norm(pinyin_to_symbols(parts[1])))
            except Exception:
                pass
    lines = []
    for line in (data / "essay.txt").read_text(encoding="utf-8").splitlines():
        word, _, w = line.partition("\t")
        if not (2 <= len(word) <= 4) or not w.isdigit() or int(w) < TW_MIN_ESSAY or occ.get(word, 0):
            continue
        options = [sorted(readings.get(ch, ())) for ch in word]
        if not all(options):
            continue
        best = None
        for combo in itertools.product(*options):
            hit = by_reading.get(tuple(combo))
            # Only a variant spelling: one character differs (坐標 -> 座標);
            # two differing characters are different words (愛與 / 礙於).
            # 的/得/地 and 在/再 are lua/asus_grammar.lua's job (有效地 stays).
            diff = [(a, b) for a, b in zip(word, hit[1]) if a != b] if hit else []
            if (hit and len(diff) == 1 and not set(diff[0]) <= PARTICLES
                    and (best is None or hit[0] > best[0])):
                best = (hit[0], hit[1], combo)
        if best:
            keys = symbols_to_keys(" ".join(best[2])).replace(" ", "_")
            lines.append(f"{word}\t{best[1]},{keys}")
    cc_dir = ROOT / "rime" / "opencc"
    lines.sort(key=lambda s: s.encode())
    (cc_dir / "asus_tw.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (cc_dir / "asus_tw.json").write_text(
        '{\n  "name": "asus_tw (GENERATED by tools/build_lang_data.py)",\n'
        '  "segmentation": {"type": "mmseg", "dict": {"type": "text", "file": "asus_tw.txt"}},\n'
        '  "conversion_chain": [{"dict": {"type": "text", "file": "asus_tw.txt"}}]\n}\n',
        encoding="utf-8")
    return len(lines)


SEG_MAX_LEN = 4  # longest word used to split committed text


def build_seg_table() -> int:
    """Word list for splitting committed text into words (lua/asus_learn.lua).

    OpenCC text dict (opencc/asus_seg.*) mapping each word to itself plus "|";
    OpenCC's maximum matching then returns "我們|下午|開會|". Words are the
    McBopomofo phrases (single characters included) and personal words.
    """
    words: set[str] = set()
    for line in (MCB_DIR / "phrase.occ").read_text(encoding="utf-8").splitlines():
        word = line.rpartition(" ")[0]
        if word and len(word) <= SEG_MAX_LEN and all("㐀" <= ch <= "鿿" for ch in word):
            words.add(word)
    personal = ROOT / "lexicon" / "personal_words.txt"
    if personal.exists():
        for line in personal.read_text(encoding="utf-8").splitlines():
            word = line.split("\t")[0]
            if (line and not line.startswith("#") and len(word) <= SEG_MAX_LEN
                    and all("㐀" <= ch <= "鿿" for ch in word)):
                words.add(word)
    cc_dir = ROOT / "rime" / "opencc"
    lines = [f"{w}\t{w}|" for w in sorted(words, key=lambda s: s.encode())]
    (cc_dir / "asus_seg.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (cc_dir / "asus_seg.json").write_text(
        '{\n  "name": "asus_seg (GENERATED by tools/build_lang_data.py)",\n'
        '  "segmentation": {"type": "mmseg", "dict": {"type": "text", "file": "asus_seg.txt"}},\n'
        '  "conversion_chain": [{"dict": {"type": "text", "file": "asus_seg.txt"}}]\n}\n',
        encoding="utf-8")
    return len(lines)


OPENCC_DICT = ROOT / "third_party" / "opencc-build" / "src" / "tools" / "Release" / "opencc_dict.exe"
CC_NAMES = ["asus_en", "asus_en_prefix", "asus_en_fix", "asus_en_follow", "asus_next", "asus_seg", "asus_tw"]


def to_ocd2() -> int:
    """Convert the OpenCC text dicts to the binary ocd2 format (marisa trie),
    which librime's OpenCC loads ~3.5x faster (1.8 s -> 0.5 s for all of them
    at the first session after Weasel starts). Needs opencc_dict.exe, built
    from OpenCC 1.1.9 (see README); without it the text dicts stay in use."""
    if not OPENCC_DICT.exists():
        return 0
    cc_dir = ROOT / "rime" / "opencc"
    done = 0
    for name in CC_NAMES:
        txt, bin_ = cc_dir / f"{name}.txt", cc_dir / f"{name}.ocd2"
        if not txt.exists():
            continue
        # Relative names: opencc_dict cannot open non-ASCII paths (輸入法).
        bin_.unlink(missing_ok=True)
        subprocess.run([str(OPENCC_DICT), "-i", txt.name, "-o", bin_.name, "-f", "text", "-t", "ocd2"],
                       check=True, capture_output=True, cwd=cc_dir)
        if not bin_.exists():
            raise RuntimeError(f"opencc_dict did not write {bin_}")
        (cc_dir / f"{name}.json").write_text(
            '{\n  "name": "%s (GENERATED by tools/build_lang_data.py)",\n'
            '  "segmentation": {"type": "mmseg", "dict": {"type": "ocd2", "file": "%s.ocd2"}},\n'
            '  "conversion_chain": [{"dict": {"type": "ocd2", "file": "%s.ocd2"}}]\n}\n' % (name, name, name),
            encoding="utf-8")
        done += 1
    return done


def lua_set(name: str, items: set[str]) -> str:
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')
    body = ",".join(f'["{esc(s)}"]=true' for s in sorted(items))
    return f"M.{name} = {{{body}}}\n"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--top", type=int, default=100000)
    args = ap.parse_args()

    words = load_easy_en(args.top) if EASY_EN.exists() else {}
    words.update(load_personal())
    # The lexicon lives in an OpenCC text dictionary (loaded in C++ by
    # librime-lua's Opencc) rather than a Lua table: a 56k-entry Lua table
    # made every keystroke ~40 ms slower through librime-lua's per-call GC.
    # Value format: "<rank>:<display word>" (rank 0 = personal word).
    cc_dir = ROOT / "rime" / "opencc"
    cc_dir.mkdir(exist_ok=True)
    lines = [f"{k}\t{r}:{w}" for k, (w, r) in sorted(words.items(), key=lambda kv: kv[0].encode())]
    (cc_dir / "asus_en.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (cc_dir / "asus_en.json").write_text(
        '{\n  "name": "asus_en (GENERATED by tools/build_lang_data.py)",\n'
        '  "segmentation": {"type": "mmseg", "dict": {"type": "text", "file": "asus_en.txt"}},\n'
        '  "conversion_chain": [{"dict": {"type": "text", "file": "asus_en.txt"}}]\n}\n',
        encoding="utf-8")
    max_en_len = max((len(k) for k in words), default=0)

    # English autocomplete: prefix (>= 3 letters) -> the PREFIX_TOP best
    # completions (best first) among the common words (rank <=
    # PREFIX_MAX_RANK) and personal words. Ranks here and in the spelling
    # table are guess_rank(): SymSpell's frequencies where it has the word.
    sym = load_symspell()
    comps: dict[str, list[tuple[int, int, str]]] = {}
    for key, (word, rank) in words.items():
        rank = guess_rank(key, rank, sym)
        if rank > PREFIX_MAX_RANK or not key.isalpha():
            continue
        for n in range(3, len(key)):
            comps.setdefault(key[:n], []).append((rank, len(word), word))
    lines = []
    for p in sorted(comps, key=lambda s: s.encode()):
        best = sorted(comps[p])[:PREFIX_TOP]
        lines.append(f"{p}\t{' '.join(f'{r}:{w}' for r, _, w in best)}")
    (cc_dir / "asus_en_prefix.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (cc_dir / "asus_en_prefix.json").write_text(
        '{\n  "name": "asus_en_prefix (GENERATED by tools/build_lang_data.py)",\n'
        '  "segmentation": {"type": "mmseg", "dict": {"type": "text", "file": "asus_en_prefix.txt"}},\n'
        '  "conversion_chain": [{"dict": {"type": "text", "file": "asus_en_prefix.txt"}}]\n}\n',
        encoding="utf-8")

    # Spelling correction (symmetric delete): every common word and each of
    # its one-letter deletions -> words, best first. A typo matches a word
    # when the two share a key, i.e. one extra, missing, wrong or swapped
    # letter (recieve / receive share "receve"). lua/asus_lang.lua checks the
    # real edit distance.
    fixes: dict[str, list[tuple[int, str]]] = {}
    for key, (word, rank) in words.items():
        rank = guess_rank(key, rank, sym)
        if rank > FIX_MAX_RANK or len(key) < FIX_MIN_LEN or not key.isalpha():
            continue
        for d in {key} | {key[:i] + key[i + 1:] for i in range(len(key))}:
            fixes.setdefault(d, []).append((rank, word))
    lines = [f"{d}\t{' '.join(f'{r}:{w}' for r, w in sorted(fixes[d])[:FIX_TOP])}"
             for d in sorted(fixes, key=lambda s: s.encode())]
    (cc_dir / "asus_en_fix.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (cc_dir / "asus_en_fix.json").write_text(
        '{\n  "name": "asus_en_fix (GENERATED by tools/build_lang_data.py)",\n'
        '  "segmentation": {"type": "mmseg", "dict": {"type": "text", "file": "asus_en_fix.txt"}},\n'
        '  "conversion_chain": [{"dict": {"type": "text", "file": "asus_en_fix.txt"}}]\n}\n',
        encoding="utf-8")
    n_fix = len(lines)

    toned, toneless, prefixes = zhuyin_sets()
    lua = ("-- GENERATED by tools/build_lang_data.py -- do not edit.\n"
           "-- Dachen key strings of Zhuyin syllables (terra_pinyin readings).\n"
           "local M = {}\n" + lua_set("toned", toned) + lua_set("toneless", toneless)
           + lua_set("prefix", prefixes) + f"M.max_en_len = {max_en_len}\n" + "return M\n")
    (ROOT / "rime" / "lua" / "asus_zhuyin_data.lua").write_text(lua, encoding="utf-8")
    n_follow = build_follow_table(words)
    n_next = build_next_table() if MCB_DIR.exists() else 0
    n_seg = build_seg_table() if MCB_DIR.exists() else 0
    n_tw = build_tw_table() if MCB_DIR.exists() else 0
    n_bin = to_ocd2()
    print(f"english: {len(words)} words; zhuyin: {len(toned)} toned, "
          f"{len(toneless)} toneless, {len(prefixes)} prefixes; next-word prefixes: {n_next}; "
          f"segmentation words: {n_seg}; spelling keys: {n_fix}; Taiwan word fixes: {n_tw}; "
          f"English words with followers: {n_follow}; binary dicts: {n_bin}")


if __name__ == "__main__":
    main()
