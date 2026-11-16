"""Tests for tools/hooks/pretooluse.py: feeds hook JSON on stdin, exactly as
Claude Code does, and checks the decision (deny / ask / none).

    python tools/run_tests.py tests/test_pretooluse_hook.py

No librime, no network; about a second.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "tools" / "hooks" / "pretooluse.py"
PY = r"C:\Users\User\anaconda3\python.exe"

# (name, tool, command, expected decision or None)
CASES = [
    # 1. heredocs / here-strings with a backslash in the body
    ("bash python heredoc, backslash", "Bash",
     "python - <<'EOF'\np = 'C:\\Users\\User\\anaconda3'\nprint(p)\nEOF", "deny"),
    ("bash heredoc into a .py file", "Bash",
     "cat > /d/x/fix.py <<EOF\nprint('a\\tb')\nEOF\npython /d/x/fix.py", "deny"),
    ("bash powershell heredoc", "Bash",
     "powershell -NoProfile -Command - <<'PS'\nGet-Item C:\\Windows\nPS", "deny"),
    ("python heredoc inside a for loop", "Bash",
     "for f in a b; do python -I - \"$f\" <<'EOF'\nimport re\nm = re.match(r'\\d+', '12')\nEOF\ndone", "deny"),
    ("bash python here-string <<<", "Bash",
     "python3 <<< 'print(\"C:\\\\x\")'", "deny"),
    ("powershell here-string to python", "PowerShell",
     "@'\nimport re\nprint(re.sub(r'\\s+', ' ', 'a  b'))\n'@ | " + PY + " -", "deny"),
    ("powershell here-string to .py file", "PowerShell",
     "@'\nprint('C:\\temp')\n'@ | Set-Content D:\\x\\fix.py", "deny"),
    ("powershell here-string to iex", "PowerShell",
     "Invoke-Expression @'\nGet-ChildItem C:\\Windows\n'@", "deny"),
    # 2. librime runs without a timeout
    ("bash test without timeout", "Bash", "C:/Users/User/anaconda3/python.exe tests/test_stage2.py", "deny"),
    ("powershell eval without timeout", "PowerShell",
     "$env:PYTHONIOENCODING='utf-8'; & " + PY + " tools\\eval_ime.py --sets dev", "deny"),
    ("bash harness after cd", "Bash", "cd /d/Document/x && python -B tools/rime_harness.py", "deny"),
    ("pytest on a test file", "Bash", "python -m pytest tests/test_stage2.py", "deny"),
    ("module form", "Bash", "python -m tests.test_stage2", "deny"),
    ("test then server: deny wins", "Bash", "python tests/test_tab.py; taskkill //im DaoHuiServer.exe", "deny"),
    ("background eval", "Bash", "nohup python tools/eval_learn.py --note x &", "deny"),
    ("bash -c wrapper", "Bash", "bash -c \"python tests/test_tab.py\"", "deny"),
    ("timeout on another statement", "Bash", "timeout 5 echo warmup; python tests/test_tab.py", "deny"),
    ("python -c running a test file", "Bash",
     "python -c \"import runpy; runpy.run_path('tests/test_tab.py')\"", "deny"),
    ("bash -c with its own timeout", "Bash", "bash -c \"timeout 900 python tests/test_tab.py\"", None),
    # allowed
    ("heredoc without backslash", "Bash", "python - <<'EOF'\nprint(1 + 1)\nEOF", None),
    ("commit message here-string", "PowerShell",
     "git commit -m @'\nFix tools\\doctor.py and tests\\test_x.py\n'@", None),
    ("heredoc mentioning a test", "Bash", "cat > notes.txt <<'EOF'\npython tests/test_x.py\nEOF", None),
    ("bash with timeout", "Bash", "timeout 900 C:/Users/User/anaconda3/python.exe tests/test_stage2.py", None),
    ("timeout with -k", "Bash", "timeout -k 10 600 python -B tools/eval_ime.py --sets dev", None),
    ("runner", "Bash", "C:/Users/User/anaconda3/python.exe tools/run_tests.py", None),
    ("runner with a file", "PowerShell", "& " + PY + " tools\\run_tests.py tests\\test_stage2.py", None),
    ("runner as module", "Bash", "python -m tools.run_tests", None),
    ("powershell WaitForExit", "PowerShell",
     "$p = Start-Process -FilePath " + PY + " -ArgumentList 'tests\\test_stage2.py' -PassThru -NoNewWindow; "
     "if (-not $p.WaitForExit(900000)) { taskkill /T /F /PID $p.Id }", None),
    ("grep for python in a test", "Bash", "grep -n python tests/test_stage2.py", None),
    ("git diff of a test", "Bash", "git diff tests/test_stage2.py", None),
    ("py_compile", "Bash", "python -m py_compile tests/test_stage2.py tools/eval_ime.py", None),
    ("python -c", "Bash", "python -c \"import sys; print(sys.version)\"", None),
    ("doctor", "PowerShell", "& " + PY + " tools\\doctor.py", None),
    ("other tool", "Write", "", None),
    # asked
    ("stop the server", "PowerShell", "Stop-Process -Name DaoHuiServer", "ask"),
    ("deployer", "Bash", "\"/c/Program Files/DaoHui/daohui-0.17.4/DaoHuiDeployer.exe\" //deploy", "ask"),
    ("weasel server", "Bash", "\"C:/Program Files/Rime/weasel-0.17.4/WeaselServer.exe\" /q", "ask"),
    ("real-window test", "Bash", "timeout 300 python tools/tsf_test.py", "ask"),
    ("smoke test", "PowerShell", "& " + PY + " tools\\daohui_smoke_test.py", "ask"),
    ("asus_compare", "Bash", "python tools/asus_compare.py --heldout", "ask"),
]


def run_hook(stdin: bytes) -> tuple[int, str]:
    p = subprocess.run([sys.executable, str(HOOK)], input=stdin, capture_output=True, timeout=30)
    return p.returncode, p.stdout.decode("utf-8")


def main() -> int:
    failed = 0
    for name, tool, command, want in CASES:
        payload = {"hook_event_name": "PreToolUse", "tool_name": tool,
                   "tool_input": {"command": command} if command else {"file_path": "x"}}
        code, out = run_hook(json.dumps(payload).encode("utf-8"))
        got = None
        if out.strip():
            got = json.loads(out)["hookSpecificOutput"]["permissionDecision"]
        ok = code == 0 and got == want
        failed += not ok
        print(("PASS" if ok else "FAIL"), f"{name}: want {want}, got {got} (exit {code})")
    # Broken input must never block the session.
    code, out = run_hook(b"not json")
    ok = code == 0 and not out.strip()
    failed += not ok
    print(("PASS" if ok else "FAIL"), "malformed stdin: no output, exit 0")
    total = len(CASES) + 1
    print(f"{total - failed}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
