"""The project config loads whole: no error in the librime log, and the data
a plain Rime lacks is there.

    python tests/test_infra.py

One session with the shipped config (no custom patch):
  * 這個程式很好用 needs the Taiwan preferences (opencc/asus_tw.json; without
    them the language model's 城市 comes back)
  * 第2章寫完了 needs the language model (grammar.yaml -> the .gram file)
  * after committing 機器, the next words are predicted (opencc/asus_next.json;
    asus_predict.lua offers SHOW = 3)
  * the librime log has no ERROR or FATAL line at all (glog: E or F, then the
    date), not only none that mention Lua: a missing data file, a YAML error or
    a Lua component that raises on every key all log one. Each such line is
    printed.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from rime_harness import open_rime  # noqa: E402
from zhuyin import reading_keys  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
# A glog line of severity ERROR or FATAL: the letter, then the date (E20261116 ...).
LOG_ERROR = re.compile(r"[EF]\d{8} ")


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def keys(text: str) -> str:
    return "".join(reading_keys(text)[0])


def log_errors(r) -> list[str]:
    """The ERROR and FATAL lines of a closed session's librime log, each once
    (the INFO, WARNING and ERROR files all hold them)."""
    lines = [line for f in sorted(r.log_dir.glob("*.log*"))
             for line in f.read_text(encoding="utf-8", errors="replace").splitlines()
             if LOG_ERROR.match(line)]
    return list(dict.fromkeys(lines))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    r = open_rime("infra")

    def commit(k: str) -> str:
        r.clear(); r.type(k); r.key("Return")
        return "".join(r.committed)

    got = commit(keys("這個程式很好用"))
    check("Taiwan preferences: 這個程式很好用", got == "這個程式很好用", got)
    got = commit(keys("第") + "2" + keys("章寫完了"))
    check("language model: 第2章寫完了", got == "第2章寫完了", got)

    got = commit(keys("機器"))
    s = r.snapshot()
    check("after committing 機器, three next words are predicted",
          got == "機器" and s.composing and len(s.candidates) == 3,
          f"committed={got!r} composing={s.composing} cands={s.candidates}")
    check("prediction rows show the word in the comment, the text stays empty",
          bool(s.candidates) and all(t == "" and c != "" for t, c in s.candidates), f"{s.candidates}")
    r.clear()

    r.close()
    errors = log_errors(r)
    check("no error lines in the librime log", not errors, f"{len(errors)} line(s), listed above")

    if errors:
        print(f"librime log ({r.log_dir}): {len(errors)} error line(s)")
        for line in errors[:10]:
            print("  " + line[:300])
        if len(errors) > 10:
            print(f"  ...and {len(errors) - 10} more")
        print()
    width = max(len(n) for n, _, _ in RESULTS)
    failed = 0
    for name, ok, detail in RESULTS:
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {'' if ok else detail}")
    print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
