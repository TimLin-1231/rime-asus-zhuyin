"""Capitals of English words in Chinese mode: a word typed in lowercase stays
as typed, an acronym gets its capitals (tools/build_lang_data.py, display_form).

    python tests/test_en_case.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from build_lang_data import PERSONAL_EN, display_form  # noqa: E402
from rime_harness import SHIFT_MASK, open_rime  # noqa: E402
from zhuyin import reading_keys  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
NO_LEARN = "patch:\n  asus_learn/enabled: false\n  translator/enable_user_dict: false\n"


def keys(text: str) -> str:
    return "".join(reading_keys(text)[0])


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    # -- the rule ----------------------------------------------------------------
    sym = {"core": 1, "alice": 2, "unix": 3, "mccartney": 4}
    for word, key, want, why in (
            ("CORE", "core", "core", "a word of SymSpell's list is not an acronym"),
            ("ALICE", "alice", "alice", "nor is a name"),
            ("NATO", "nato", "NATO", "a short form SymSpell does not list is one"),
            ("HTML", "html", "HTML", "also with no frequency in easy_en"),
            ("CONFIG", "config", "config", "more than five letters: not an acronym"),
            ("UNESCO", "unesco", "UNESCO", "...unless listed"),
            ("UNIX", "unix", "UNIX", "listed although SymSpell has the lowercase string"),
            ("BOOL", "bool", "bool", "listed as an ordinary abbreviation"),
            ("McCartney", "mccartney", "McCartney", "mixed capitals are deliberate"),
            ("AFL-CIO", "aflcio", "AFL-CIO", "a hyphenated form is left alone"),
            ("China", "china", "China", "plain capitalisation is left alone"),
            ("python", "python", "python", "lowercase is left alone")):
        got = display_form(word, key, sym)
        check(f"display_form {word}: {why}", got == want, got)

    # -- the generated lexicon -----------------------------------------------------
    lexicon = {}
    for line in (ROOT / "rime" / "opencc" / "asus_en.txt").read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("\t")
        lexicon[key] = value.partition(":")[::2]
    personal = [w for w in (PERSONAL_EN.read_text(encoding="utf-8").split() if PERSONAL_EN.exists() else [])
                if not w.startswith("#")]
    wrong = [w for w in personal if lexicon.get(w.lower()) != ("0", w)]
    check(f"personal words keep their form ({len(personal)} in lexicon/english_words.txt)", not wrong, f"{wrong[:5]}")

    # -- typing ---------------------------------------------------------------------
    r = open_rime("en-case", extra={"asus_zhuyin.custom.yaml": NO_LEARN})
    r.set_option("prediction", False)

    def typed(before: str, word: str, after: str) -> str:
        r.clear(); r.key(">", SHIFT_MASK); r.clear()
        for ch in keys(before) + word + keys(after):
            r.key(ch, SHIFT_MASK if ch.isupper() else 0)
        r.key("Return")
        got = "".join(r.committed); r.clear()
        return got

    for word in ("core", "config", "alice", "aids", "adams", "americas", "admin", "branson"):
        got = typed("這個", word, "很好")
        check(f"{word} stays lowercase", got == f"這個{word}很好", got)
    for word in ("nato", "html", "unix", "risc", "bios", "unesco"):
        got = typed("這個", word, "很好")
        check(f"{word} becomes {word.upper()}", got == f"這個{word.upper()}很好", got)
    for word in ("Core", "CORE", "Nato"):
        got = typed("這個", word, "很好")
        check(f"{word} typed with Shift stays {word}", got == f"這個{word}很好", got)
    got = typed("把", "config", "改一下")
    check("把config改一下", got == "把config改一下", got)

    # Guesses offer the same forms.
    r.clear(); r.key(">", SHIFT_MASK); r.clear()
    r.type("alic")
    g = [c for c, _ in r.snapshot().candidates if c != "alic"]; r.clear()
    check("alic: the completion is alice, not ALICE", "alice" in g and "ALICE" not in g, f"{g}")
    r.clear(); r.key(">", SHIFT_MASK); r.clear()
    r.type("unes")
    g = [c for c, _ in r.snapshot().candidates if c != "unes"]; r.clear()
    check("unes: the completion is UNESCO", g[:1] == ["UNESCO"], f"{g}")

    r.close()
    lua_errors = [line for f in r.log_dir.glob("*.log*")
                  for line in f.read_text(encoding="utf-8", errors="replace").splitlines()
                  if line.startswith("E") and "lua" in line.lower()]
    check("no Lua runtime errors in librime log", not lua_errors, lua_errors[0] if lua_errors else "")
    width = max(len(n) for n, _, _ in RESULTS)
    failed = 0
    for name, ok, detail in RESULTS:
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {'' if ok else detail}")
    print(f"{len(RESULTS) - failed}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
