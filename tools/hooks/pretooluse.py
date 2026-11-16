"""PreToolUse hook for Claude Code (.claude/settings.json): turns three lessons
that were written down again and again into checks that cannot be skipped.

1. deny: a Python or PowerShell heredoc / here-string whose body contains a
   backslash. Backslashes in script bodies passed through the shell were
   mangled repeatedly (start_assist.bat shipped with a BEL byte for "\\a").
   Write the script with the Write tool and run the file instead.
2. deny: running tests/test_*.py, tools/eval_*.py or rime_harness.py without a
   hard timeout. tools/run_tests.py is the way to run tests (it has its own
   timeouts); anything else needs an explicit `timeout <seconds>` (Bash) or a
   WaitForExit(<ms>) / Wait-Process -Timeout (PowerShell).
3. ask: commands that touch the live IME (DaoHuiServer, WeaselServer, a
   deployer) or that take over the keyboard (tools/tsf_*.py, *smoke_test.py,
   asus_compare.py).

Input: the hook JSON on stdin (tool_name, tool_input.command). Output: nothing
(no opinion) or one hookSpecificOutput JSON object; exit code is always 0.
Any internal error prints nothing, so a bug here never blocks the session.

Tests: tests/test_pretooluse_hook.py.
"""

from __future__ import annotations

import json
import re
import sys

# A Python interpreter as a command word: python, python3, python3.11, pythonw,
# py, with or without .exe, bare or at the end of a path, optionally quoted.
PY_WORD = re.compile(
    r"""(?:^|(?<=[\s"'&(;|`=]))(?:[^\s"';|&]*[\\/])?(?:python(?:w|3(?:\.\d+)?)?|py)(?:\.exe)?(?=["'\s;|&)]|$)""",
    re.IGNORECASE,
)
PS_WORD = re.compile(
    r"\b(?:powershell(?:\.exe)?|pwsh(?:\.exe)?|Invoke-Expression|iex|ScriptBlock\]::Create)\b|\.ps1\b",
    re.IGNORECASE,
)
PY_TARGET = re.compile(r"\.py\b", re.IGNORECASE)

# Bash heredoc operator (not the here-string <<<), with its delimiter.
HEREDOC = re.compile(
    r"""(?<!<)<<(?!<)(-?)[ \t]*(?:'([^'\n]+)'|"([^"\n]+)"|\\?([A-Za-z_][\w.-]*))"""
)
HERESTRING = re.compile(r"""<<<[ \t]*(?:'([^']*)'|"((?:[^"\\]|\\.)*)"|\$'((?:[^'\\]|\\.)*)')""")
# PowerShell here-string: @' ... '@ or @" ... "@, closing marker at column 0.
PS_HERESTRING = re.compile(r"""@(['"])[ \t]*\r?\n(.*?)\r?\n\1@""", re.DOTALL)

# Statement separators for finding which program runs which script.
SEGMENT_SPLIT = re.compile(r"\|\||&&|[;\n|]|(?<![>&\d])&(?![>&])")
PY_FILE = re.compile(r"""[^\s"'=]*\.py\b""", re.IGNORECASE)

TIMEOUT_MARKS = [
    # coreutils timeout (Git Bash): timeout [opts] DURATION cmd
    re.compile(r"(?:^|[\s;&|(\"'])timeout(?:\.exe)?\s+(?:-{1,2}[\w-]+(?:[= ]\S+)?\s+)*\d+(?:\.\d+)?[smhd]?\s+\S"),
    re.compile(r"WaitForExit\(\s*\d"),
    re.compile(r"\bWait-(?:Process|Job)\b[^\n]*-Timeout\s+\d", re.IGNORECASE),
]

LIVE_IME = re.compile(r"\b(?:DaoHuiServer|WeaselServer|[A-Za-z_]*Deployer)(?:\.exe)?\b")

GUARDED = re.compile(r"^(?:test_\w*|eval_\w*|rime_harness)\.py$", re.IGNORECASE)
KEYBOARD = re.compile(r"^(?:tsf_\w*|\w*smoke_test|asus_compare)\.py$", re.IGNORECASE)
RUNNER = "run_tests.py"


# Words that may stand before the interpreter in a statement without making
# it an argument of something else (env assignments, launchers, timeouts).
LAUNCHER = re.compile(
    r"""^(?:|&|=|!|\{|do|then|else|if|while|\$\w+|\w+=\S*|exec|time|nohup|nice|env|timeout(?:\.exe)?|-[skn]|--[\w-]+(?:=\S+)?"""
    r"""|\d+(?:\.\d+)?[smhd]?|Start-Process|-FilePath|-Wait|-NoNewWindow|-PassThru|cmd(?:\.exe)?|/[cCkK]|start"""
    r"""|(?:ba|z)?sh(?:\.exe)?|-l?c)$""",
    re.IGNORECASE,
)


def _basename(token: str) -> str:
    return re.split(r"[\\/]", token.strip("\"'"))[-1]


def python_call(seg: str) -> re.Match | None:
    """The interpreter word of a statement, if the statement runs Python."""
    for m in PY_WORD.finditer(seg):
        before = [t.strip("\"'(") for t in seg[: m.start()].split()]
        if all(LAUNCHER.match(t) for t in before):
            return m
    return None


def runs_python(text: str) -> bool:
    return any(python_call(seg) for seg in SEGMENT_SPLIT.split(text))


def heredoc_problems(tool: str, command: str) -> list[str]:
    """Python/PowerShell heredocs or here-strings whose body has a backslash."""
    found = []
    if tool == "PowerShell":
        bodies = [(m.group(2)) for m in PS_HERESTRING.finditer(command)]
        outside = PS_HERESTRING.sub("@''@", command)
        kind = None
        if runs_python(outside) or PY_TARGET.search(outside):
            kind = "Python"
        elif PS_WORD.search(outside):
            kind = "PowerShell"
        if kind and any("\\" in b for b in bodies):
            found.append(f"{kind} here-string 的內容有反斜線")
        return found

    lines = command.split("\n")
    offsets = []
    pos = 0
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1
    for m in HEREDOC.finditer(command):
        strip_tabs = m.group(1) == "-"
        delim = m.group(2) or m.group(3) or m.group(4)
        line_no = command.count("\n", 0, m.start())
        header = lines[line_no]
        kind = None
        if runs_python(header) or PY_TARGET.search(header):
            kind = "Python"
        elif PS_WORD.search(header):
            kind = "PowerShell"
        if not kind:
            continue
        body = []
        for line in lines[line_no + 1:]:
            check = line.rstrip("\r")
            if strip_tabs:
                check = check.lstrip("\t")
            if check.strip() == delim:
                break
            body.append(line)
        if any("\\" in line for line in body):
            found.append(f"{kind} heredoc（<<{delim}）的內容有反斜線")
    for m in HERESTRING.finditer(command):
        text = next((g for g in m.groups() if g is not None), "")
        line_no = command.count("\n", 0, m.start())
        header = lines[line_no][: m.start() - offsets[line_no]]
        if "\\" in text and (runs_python(header) or PS_WORD.search(header)):
            found.append("here-string（<<<）的內容有反斜線")
    return found


def strip_bodies(command: str) -> str:
    """The command without heredoc / here-string bodies (they are data)."""
    text = PS_HERESTRING.sub("@''@", command)
    lines = text.split("\n")
    keep = []
    i = 0
    while i < len(lines):
        keep.append(lines[i])
        for m in HEREDOC.finditer(lines[i]):
            delim = m.group(2) or m.group(3) or m.group(4)
            i += 1
            while i < len(lines) and lines[i].rstrip("\r").strip() != delim:
                i += 1
        i += 1
    return "\n".join(keep)


def scripts_run_in(command: str) -> list[tuple[str, str]]:
    """(statement, basename) of each .py file a Python interpreter is asked to run."""
    text = strip_bodies(command)
    out = []
    inline_code = False
    for seg in SEGMENT_SPLIT.split(text):
        m = python_call(seg)
        if not m:
            continue
        rest = seg[m.end():]
        args = rest.split()
        opts = [a.strip("\"'") for a in args]
        if "-c" in opts[:4]:
            # python -c "...": no file of its own, but the code may run one
            # (runpy.run_path("tests/test_x.py"), subprocess, exec(open(...))).
            # The code itself is split at its ';', so look at the whole command.
            inline_code = True
            continue
        if "-m" in opts[:4]:
            mod = opts[opts.index("-m") + 1] if opts.index("-m") + 1 < len(opts) else ""
            if mod == "pytest":
                out += [(seg, _basename(f)) for f in PY_FILE.findall(rest)]
            elif mod.startswith(("tests.test_", "tools.eval_")) or mod.endswith("rime_harness"):
                out.append((seg, mod.split(".")[-1] + ".py"))
            elif mod.endswith("run_tests"):
                out.append((seg, RUNNER))
            continue
        first = PY_FILE.search(rest)
        if first:
            out.append((seg, _basename(first.group(0))))
    if inline_code:
        named = {(text, _basename(f)) for f in PY_FILE.findall(text) if GUARDED.match(_basename(f))}
        out += [x for x in named if x[1] not in {name for _, name in out}]
    return out


def scripts_run(command: str) -> list[str]:
    """Basenames of the .py files that a Python interpreter is asked to run."""
    return [name for _, name in scripts_run_in(command)]


def has_timeout(command: str) -> bool:
    return any(p.search(command) for p in TIMEOUT_MARKS)


def decide(payload: dict) -> tuple[str, str] | None:
    tool = payload.get("tool_name", "")
    if tool not in ("Bash", "PowerShell"):
        return None
    command = (payload.get("tool_input") or {}).get("command") or ""
    if not command:
        return None

    deny = []
    for problem in heredoc_problems(tool, command):
        deny.append(problem + "：shell 會吃掉或改掉反斜線。請先用 Write 工具把腳本寫成檔案，再執行那個檔案。")

    calls = scripts_run_in(command)
    run = [name for _, name in calls]
    # Bash: the timeout must wrap the statement that runs the script (not some
    # other statement). PowerShell starts a process in one statement and waits
    # with a limit in another, so there the whole command counts.
    guarded = [name for seg, name in calls if GUARDED.match(name)
               and not has_timeout(seg if tool == "Bash" else command)]
    if guarded:
        deny.append(
            "執行 " + "、".join(sorted(set(guarded))) + " 沒有逾時：librime 卡住時會一直跑下去。"
            "跑測試請用 C:\\Users\\User\\anaconda3\\python.exe tools\\run_tests.py；"
            "其他情況加上 `timeout 900`（Bash）或 WaitForExit(毫秒)（PowerShell），並在逾時時連子行程一起結束。"
        )
    if deny:
        return "deny", " ".join(deny)

    ask = []
    live = sorted(set(LIVE_IME.findall(command)))
    if live:
        ask.append("這個指令提到 " + "、".join(live) + "：會動到這台電腦正在使用的輸入法。")
    keyboard = sorted({s for s in run if KEYBOARD.match(s)})
    if keyboard:
        ask.append("執行 " + "、".join(keyboard) + " 會佔用鍵盤和真的視窗。")
    if ask:
        return "ask", " ".join(ask) + "需要使用者在這一則訊息裡同意。"
    return None


def main() -> int:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
        result = decide(payload)
    except Exception:  # never block the session because of a bug in this hook
        return 0
    if result:
        decision, reason = result
        out = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": decision,
                "permissionDecisionReason": reason,
            }
        }
        sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
