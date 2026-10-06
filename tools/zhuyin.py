"""Text -> Zhuyin reading -> Dachen key sequence, using Rime's own data.

Word readings follow the Taiwan MOE dictionaries when available (what a
Taiwanese typist actually types), falling back to terra_pinyin.dict.yaml;
single characters use the most frequent terra_pinyin reading.
pinyin -> zhuyin -> keys follows the xform/xlit rules in zhuyin.yaml.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

from rime_harness import weasel_root

# zhuyin.yaml: pinyin_to_zhuyin (xform rules, applied in order)
_PINYIN_TO_ZHUYIN = [
    (r"^m(\d)$", r"mu\1"), (r"^r5$", r"er5"), (r"iu", "iou"), (r"ui", "uei"),
    (r"ong", "ung"), (r"^yi?", "i"), (r"^wu?", "u"), (r"iu", "v"),
    (r"^([jqx])u", r"\1v"), (r"([iuv])n", r"\1en"), (r"^zhi?", "Z"),
    (r"^chi?", "C"), (r"^shi?", "S"), (r"^([zcsr])i", r"\1"), (r"ai", "A"),
    (r"ei", "I"), (r"ao", "O"), (r"ou", "U"), (r"ang", "K"), (r"eng", "G"),
    (r"an", "M"), (r"en", "N"), (r"er", "R"), (r"eh", "E"), (r"([iv])e", r"\1E"),
]
_INTERNAL = "bpmfdtnlgkhjqxZCSrzcsiuvaoeEAIOUMNKGR12345"
_KEYS = "1qaz2wsxedcrfv5tgbyhnujm8ik,9ol.0p;/- 6347"
_SYMBOLS = "ㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓㄔㄕㄖㄗㄘㄙㄧㄨㄩㄚㄛㄜㄝㄞㄟㄠㄡㄢㄣㄤㄥㄦˉˊˇˋ˙"
_TO_KEYS = str.maketrans(_INTERNAL, _KEYS)
_TO_SYMBOLS = str.maketrans(_INTERNAL, _SYMBOLS)
SYMBOL_TO_KEY = dict(zip(_SYMBOLS, _KEYS))
KEY_TO_SYMBOL = dict(zip(_KEYS, _SYMBOLS))


def _internal(pinyin: str) -> str:
    s = pinyin
    for pat, rep in _PINYIN_TO_ZHUYIN:
        s = re.sub(pat, rep, s)
    return s


def pinyin_to_keys(pinyin: str) -> str:
    return _internal(pinyin).translate(_TO_KEYS)


def pinyin_to_symbols(pinyin: str) -> str:
    return _internal(pinyin).translate(_TO_SYMBOLS)


# One syllable: optional leading neutral dot, [initial][medial][final], tone.
_SYLLABLE = re.compile(r"˙?[ㄅ-ㄙ]?[ㄧㄨㄩ]?[ㄚ-ㄦ]?[ˉˊˇˋ˙]?")


def split_syllables(zhuyin: str) -> list[str]:
    """Split unseparated Zhuyin by syllable structure, not by tone marks."""
    out, pos = [], 0
    while pos < len(zhuyin):
        m = _SYLLABLE.match(zhuyin, pos)
        if not m or m.end() == pos:
            raise ValueError(f"bad zhuyin {zhuyin!r} at {pos}")
        out.append(m.group())
        pos = m.end()
    return out


def symbols_to_keys(zhuyin: str) -> str:
    """'ㄋㄧˇㄏㄠˇ' or 'ㄋㄧˇ ㄏㄠˇ' -> 'su3cl3'. Neutral-tone ˙ may lead a syllable."""
    if re.search(r"[ 　]", zhuyin.strip()):
        syllables = re.split(r"[ 　]+", zhuyin.strip())
    else:  # unseparated (ASUS export, e.g. 'ㄌㄞˊ˙ㄉㄜㄐㄧˊ')
        syllables = split_syllables(zhuyin)
    out = []
    for syl in syllables:
        if syl.startswith("˙"):
            syl = syl[1:] + "˙"
        if syl[-1] not in "ˉˊˇˋ˙":
            syl += "ˉ"
        out.append("".join(SYMBOL_TO_KEY[c] for c in syl))
    return "".join(out)


@lru_cache(maxsize=1)
def _dictionary() -> tuple[dict[str, str], dict[str, str], int]:
    path = weasel_root() / "data" / "terra_pinyin.dict.yaml"
    chars: dict[str, tuple[float, str]] = {}
    words: dict[str, str] = {}
    body = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not body:
            body = line == "..."
            continue
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        text, code = parts[0], parts[1]
        if len(text) == 1:
            weight = float(parts[2].rstrip("%")) if len(parts) > 2 and parts[2] else 100.0
            if text not in chars or weight > chars[text][0]:
                chars[text] = (weight, code)
        elif text not in words and len(code.split()) == len(text):
            words[text] = code
    max_len = max(len(w) for w in words)
    return {c: code for c, (_, code) in chars.items()}, words, max_len


# MOE (Taiwan Ministry of Education) dictionaries, cp950 TSV: word<TAB>reading.
# The 2014 concised edition carries the revised (current) standard readings,
# so it takes precedence over the older revised edition.
MOE_DIR = Path(os.environ.get(
    "MOE_DICT_DIR", r"D:\Document\新注音詞彙\教育部國語辭典\教育部國語辭典"))
MOE_FILES = [
    "dict_concised_2014_20200327.txt",
    "dict_revised_2015_20200331_2.txt",
    "dict_revised_2015_20200331_3.txt",
    "dict_idioms_2011_20200330.txt",
]


def _moe_syllables(field: str) -> list[str] | None:
    field = field.split("（變）")[0]
    field = re.sub(r"\([一二三四五六七八九十]\)", "", field)
    sylls = [s for s in re.split(r"[\u3000 ]+", field.strip()) if s]
    if not sylls or not all(re.fullmatch(r"˙?[ㄅ-ㄩ]+[ˊˇˋ˙]?", s) for s in sylls):
        return None
    return sylls


@lru_cache(maxsize=1)
def _moe_words() -> dict[str, list[str]]:
    words: dict[str, list[str]] = {}
    for name in MOE_FILES:
        path = MOE_DIR / name
        if not path.exists():
            continue
        for line in path.read_bytes().decode("cp950").splitlines():
            word, _, field = line.partition("\t")
            if len(word) < 2 or word in words:
                continue
            sylls = _moe_syllables(field)
            if sylls and len(sylls) == len(word):
                words[word] = sylls
    return words


def reading_keys(text: str, standard: str = "moe") -> tuple[list[str], list[str]]:
    """Per-syllable key sequences for text, plus the source of each word.

    Greedy longest match over dictionary words; with standard="moe" a word's
    MOE reading wins over terra_pinyin. Single characters fall back to the
    most frequent terra_pinyin reading.
    """
    chars, words, max_len = _dictionary()
    moe = _moe_words() if standard == "moe" else {}
    max_len = max(max_len, max((len(w) for w in moe), default=0))
    keys: list[str] = []
    sources: list[str] = []
    i = 0
    while i < len(text):
        for n in range(min(max_len, len(text) - i), 1, -1):
            word = text[i:i + n]
            if word in moe:
                keys.extend(symbols_to_keys(s) for s in moe[word])
                sources.append(f"{word}:moe")
            elif word in words:
                keys.extend(pinyin_to_keys(p) for p in words[word].split())
                sources.append(f"{word}:terra")
            else:
                continue
            i += n
            break
        else:
            # MOE's "(一)" order is not frequency order (的 (一) is ㄉㄧˋ), so
            # single characters use terra_pinyin frequencies.
            if text[i] not in chars:
                raise KeyError(f"no reading for {text[i]!r} in {text!r}")
            keys.append(pinyin_to_keys(chars[text[i]]))
            sources.append(f"{text[i]}:char")
            i += 1
    return keys, sources


@lru_cache(maxsize=1)
def keys_to_pinyin_map() -> dict[str, str]:
    """Dachen key sequence of one syllable -> terra_pinyin syllable (e.g. 'cl3' -> 'hao3')."""
    path = weasel_root() / "data" / "terra_pinyin.dict.yaml"
    out: dict[str, str] = {}
    body = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not body:
            body = line == "..."
            continue
        parts = line.split("\t")
        if len(parts) >= 2 and len(parts[0]) == 1:
            out.setdefault(pinyin_to_keys(parts[1]), parts[1])
    return out


def zhuyin_to_pinyin(zhuyin: str) -> list[str] | None:
    """'ㄋㄧˇ ㄏㄠˇ' -> ['ni3', 'hao3']; None if a syllable is unknown to terra_pinyin."""
    table = keys_to_pinyin_map()
    out = []
    for syl in re.split(r"[ 　]+", zhuyin.strip()):
        p = table.get(symbols_to_keys(syl))
        if p is None:
            return None
        out.append(p)
    return out


def essay_weights() -> dict[str, int]:
    path = weasel_root() / "data" / "essay.txt"
    out: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        w, _, n = line.partition("\t")
        if n:
            out[w] = int(n)
    return out


def terra_char_readings() -> dict[str, str]:
    return _dictionary()[0]


def terra_word_readings() -> dict[str, str]:
    return _dictionary()[1]


def moe_words() -> dict[str, list[str]]:
    return _moe_words()


def reading(text: str) -> list[str]:
    """terra_pinyin syllables for text (kept for reference/comparison)."""
    chars, words, max_len = _dictionary()
    out: list[str] = []
    i = 0
    while i < len(text):
        for n in range(min(max_len, len(text) - i), 1, -1):
            code = words.get(text[i:i + n])
            if code:
                out.extend(code.split())
                i += n
                break
        else:
            if text[i] not in chars:
                raise KeyError(f"no reading for {text[i]!r} in {text!r}")
            out.append(chars[text[i]])
            i += 1
    return out
