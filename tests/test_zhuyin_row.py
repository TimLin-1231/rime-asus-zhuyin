"""程式 before 城市, and the keys as Zhuyin in the open list.

    python tests/test_zhuyin_row.py

ㄔㄥˊ ㄕˋ is 程式 (tools/build_lexicon.py PREFER_OVER: the word alone by
weight, in sentences by the asus_tw rule 城市 -> !程式); 城市 is one pick
away. The open list (Down) has the keys typed as Zhuyin (ㄔㄥˊㄕˋ) as the
last row of its first page (rime/lua/asus_compact.lua): in the ordinary list
for the whole input, in the list at the editing cursor (Down at the end of a
sentence lists its last word) for the rest of the input; picking it gives
the Zhuyin and teaches nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from rime_harness import open_rime  # noqa: E402
from zhuyin import reading_keys  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
ZHUYIN = set(chr(c) for c in range(0x3105, 0x3130)) | set("ˊˇˋ˙")
NO_LEARN = "  asus_learn/enabled: false\n  translator/enable_user_dict: false\n"


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def keys(text: str) -> str:
    return "".join(reading_keys(text)[0])


def is_zhuyin(text: str) -> bool:
    return text != "" and set(text) <= ZHUYIN


class Session:
    def __init__(self, name: str, patch: str) -> None:
        self.r = open_rime(name, extra={"asus_zhuyin.custom.yaml": "patch:\n" + patch})
        self.r.set_option("prediction", False)

    def typed(self, k: str, *more: str):
        r = self.r
        r.clear()
        r.committed.clear()
        r.type(k)
        for key in more:
            r.key(key)
        return r.snapshot()

    def rows(self) -> list[str]:
        return [c for c, _ in self.r.snapshot().candidates]

    def committed(self) -> str:
        return "".join(self.r.committed)

    def enter(self, k: str) -> str:
        self.typed(k)
        self.r.key("Return")
        return self.committed()

    def pick(self, text: str) -> bool:
        rows = self.rows()
        if text not in rows:
            return False
        for _ in range(rows.index(text) - self.r.snapshot().highlighted):
            self.r.key("Down")
        self.r.key("space")
        return True

    def fix_first_word(self, k: str, word: str) -> str:
        """Type k, put the cursor on the first character, pick word from its
        list, close the list and commit (the user's way of correcting)."""
        self.typed(k, "Home", "Down")
        self.pick(word)
        self.r.key("Escape")
        self.r.key("Return")
        return self.committed()


def preference(s: Session, mode: str) -> None:
    st = s.typed("t/6g4")
    check(f"{mode}: ㄔㄥˊㄕˋ alone: 程式", st.top == "程式", f"{st.candidates}")
    s.typed("t/6g4", "Down")
    check(f"{mode}: the list: 程式, then 城市", s.rows()[:2] == ["程式", "城市"], f"{s.rows()}")
    for text in ("這個程式很好用", "程式有問題", "打開程式", "我在寫程式", "電腦程式", "學程式"):
        got = s.enter(keys(text))
        check(f"{mode}: sentence: {text}", got == text, got)
    for text in ("城市規劃", "大城市", "城市發展"):
        got = s.enter(keys(text))
        check(f"{mode}: a word with 城市 in it stays: {text}", got == text, got)
    city = "城市很熱鬧"
    got = s.enter(keys(city))
    check(f"{mode}: 城市 in a sentence becomes 程式", got == "程式很熱鬧", got)
    got = s.fix_first_word(keys(city), "城市")
    check(f"{mode}: and 城市 is one pick away at the cursor", got == city, got)


def zhuyin_rows(s: Session, mode: str) -> None:
    r = s.r
    st = s.typed("t/6g4")
    check(f"{mode}: compact bubble: no Zhuyin row", not any(is_zhuyin(c) for c, _ in st.candidates),
          f"{st.candidates}")
    st = s.typed("t/6g4", "Down")
    rows = s.rows()
    check(f"{mode}: open list: ㄔㄥˊㄕˋ is row 9", len(rows) == 9 and rows[8] == "ㄔㄥˊㄕˋ"
          and st.labels[8] == "9" and sum(map(is_zhuyin, rows)) == 1, f"{list(zip(st.labels, rows))}")
    r.key("9")
    check(f"{mode}: 9 commits the Zhuyin", s.committed() == "ㄔㄥˊㄕˋ" and not r.snapshot().composing,
          f"{r.committed!r}")

    s.typed("t/6g4", *(["Down"] * 9))
    st = r.snapshot()
    check(f"{mode}: highlighted: the text shown is the Zhuyin", st.highlighted == 8 and st.preview == "ㄔㄥˊㄕˋ",
          f"{st.highlighted} {st.preview!r}")
    r.key("Return")
    check(f"{mode}: Enter on it commits the Zhuyin", s.committed() == "ㄔㄥˊㄕˋ", f"{r.committed!r}")

    s.typed("t/6g4", "Down", "Right")  # the next page
    check(f"{mode}: not on the next page again", s.rows() and not any(map(is_zhuyin, s.rows())), f"{s.rows()}")

    for k, want in (("cc", "ㄏㄏ"), ("1", "ㄅ"), ("a2", "ㄇㄉ"), ("2k7", "ㄉㄜ˙"), ("8 ", "ㄚ"),
                    ("su3cl3", "ㄋㄧˇㄏㄠˇ")):
        s.typed(k, "Down")
        rows = s.rows()
        at = rows.index(want) if want in rows else -1
        check(f"{mode}: {k!r}: {want} is the last row of page 1", at >= 0 and at == min(8, len(rows) - 1),
              f"{rows}")
        if at >= 0:
            r.key(str(at + 1))
            check(f"{mode}: {k!r}: picking it commits {want}", s.committed() == want
                  and not r.snapshot().composing, f"{r.committed!r}")

    # Down at the end of a sentence lists its last word: the row is that
    # word's Zhuyin (with stray keys before it: ㄏㄏ), the sentence stays.
    # (寫程式 is one word to the language model.)
    for text_keys, want, done in (("ji3vu;3vu,3t/6g4", "ㄒㄧㄝˇㄔㄥˊㄕˋ", "我想ㄒㄧㄝˇㄔㄥˊㄕˋ"),
                                  ("cl3x87cc", "ㄏㄏ", "好啦ㄏㄏ"),
                                  ("t/6g4cc", "ㄏㄏ", "程式ㄏㄏ")):
        s.typed(text_keys, "Down")
        rows = s.rows()
        check(f"{mode}: {text_keys!r}: the last word's list has {want}", want in rows, f"{rows}")
        if want in rows:
            s.pick(want)
            shown = r.snapshot().preview
            r.key("Return")
            check(f"{mode}: {text_keys!r}: picked and Enter: {done}", shown == done and s.committed() == done,
                  f"{shown!r} {r.committed!r}")

    # At the cursor: the rest of the input as Zhuyin, if all of it is Chinese.
    s.typed("t/6g4a83", "Left", "Down")
    rows = s.rows()
    check(f"{mode}: list at the cursor (碼): ㄇㄚˇ", "ㄇㄚˇ" in rows and sum(map(is_zhuyin, rows)) == 1, f"{rows}")
    s.typed("t/6g4hello", "Home", "Down")
    check(f"{mode}: list at the cursor with English after it: no Zhuyin row",
          s.rows() and not any(map(is_zhuyin, s.rows())), f"{s.rows()}")
    s.typed("hello", "Down")
    check(f"{mode}: English: no Zhuyin row", s.rows() and not any(map(is_zhuyin, s.rows())), f"{s.rows()}")

    # A span with a typo fix (tagged asus_fix like an AI correction).
    st = s.typed("t/6g4a83;7g;6")
    check(f"{mode}: typo-fixed input: one row in the compact bubble", len(st.candidates) == 1, f"{st.candidates}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    for raw in ("true", "false"):
        mode = f"raw_keys {raw}"
        s = Session(f"zhuyin_row_{raw}", NO_LEARN + f"  asus_lang/raw_keys: {raw}\n")
        preference(s, mode)
        zhuyin_rows(s, mode)
        s.r.close()

    s = Session("zhuyin_row_off", NO_LEARN + "  asus_tw/prefer: false\n")
    got = s.enter(keys("這個程式很好用"))
    check("asus_tw/prefer: false: the language model's 城市 again", got == "這個城市很好用", got)
    got = s.enter(keys("這個坐標"))
    check("asus_tw/prefer: false: Taiwan spellings stay", got == "這個座標", got)
    s.r.close()

    # Learning on -----------------------------------------------------------------
    s = Session("zhuyin_row_learn", "  asus_lang/raw_keys: true\n")
    # (The same text committed before each: the last commit is the language
    # model's left context.)
    s.enter("su3cl3")
    before = s.typed("8 ").top
    for _ in range(2):
        s.typed("8 ", "Down")
        s.pick("ㄚ")
    s.enter("su3cl3")
    st = s.typed("8 ")
    check("the Zhuyin picked twice is not learned", st.top == before and not is_zhuyin(st.top),
          f"{before} -> {st.candidates[:2]}")
    s.typed("t/6g4", "Down")
    s.pick("ㄔㄥˊㄕˋ")
    st = s.typed("t/6g4")
    check("ㄔㄥˊㄕˋ picked as Zhuyin: still 程式", st.top == "程式", f"{st.candidates[:2]}")

    # The preference holds after 城市 was picked once (程式 is a word the user
    # types: the correction stays in its context).
    s.enter(keys("程式有問題"))
    got = s.fix_first_word(keys("城市很熱鬧"), "城市")
    check("(setup) 城市很熱鬧 corrected", got == "城市很熱鬧", got)
    got = s.enter(keys("學程式"))
    check("after 城市 was picked once: 程式 elsewhere", got == "學程式", got)
    got = s.enter(keys("城市很熱鬧"))
    check("...and 城市 where it was picked", got == "城市很熱鬧", got)
    s.r.close()

    failed = [x for x in RESULTS if not x[1]]
    for name, ok, detail in RESULTS:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not ok else ""))
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
