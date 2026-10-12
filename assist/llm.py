"""The local language model behind the writing helper: llama.cpp's server
(third_party/llama.cpp, models/*.gguf, fetched by tools/fetch_assist.py)
started on demand, listening on this machine only (127.0.0.1). Nothing
leaves the computer.

    fix(text)             the text with its grammar and spelling corrected
    rewrite(text, style)  the text reworded: natural / concise / formal / friendly
    continue_text(text)   a few words that could come next
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "third_party" / "llama.cpp" / "llama-server.exe"
MODEL = ROOT / "models" / "Qwen3.5-4B-Q4_K_M.gguf"
LOG = ROOT / ".harness" / "assist-llama.log"
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW

FIX = (
    "You are a careful proofreader. Correct the grammar, spelling and punctuation mistakes in the "
    "user's text. Change as little as possible: keep the meaning, the tone, the wording, names, "
    "numbers, code and line breaks, and leave any non-English text exactly as it is. If the text "
    "is already correct, return it unchanged. Reply with the corrected text only, no explanation "
    "and no quotation marks."
)
STYLES = {
    "natural": "Rewrite the user's text so that it reads like fluent, natural English written by a native speaker.",
    "concise": "Rewrite the user's text to be clear and concise, cutting needless words.",
    "formal": "Rewrite the user's text in a polite, professional tone suitable for a business email.",
    "friendly": "Rewrite the user's text in a warm, friendly, casual tone.",
}
REWRITE_TAIL = (
    " Keep the meaning, every fact, names, numbers and code; keep it about the same length; leave "
    "any non-English text exactly as it is. Reply with the rewritten text only, no explanation and "
    "no quotation marks."
)
CONTINUE = (
    "Continue the user's text with the words that most naturally come next: at most one sentence, "
    "in the same language, tone and voice. Reply with the continuation only; do not repeat the "
    "user's text and do not explain."
)


def available() -> bool:
    return SERVER.exists() and MODEL.exists()


class Model:
    """llama-server as a child process: started on first use, stopped by
    stop() (the app stops it after a while unused, to give the VRAM back)."""

    def __init__(self, port: int = 8719, context: int = 4096, model: Path = MODEL):
        self.port, self.context, self.model = port, context, model
        self.proc: subprocess.Popen | None = None
        self.last_used = 0.0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, timeout: float = 120.0) -> None:
        if self.running():
            return
        LOG.parent.mkdir(parents=True, exist_ok=True)
        log = LOG.open("wb")
        self.proc = subprocess.Popen(
            [str(SERVER), "-m", str(self.model), "--host", "127.0.0.1", "--port", str(self.port),
             "-ngl", "all", "-c", str(self.context), "-np", "1", "--reasoning", "off", "--no-webui"],
            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"llama-server exited with code {self.proc.returncode}; see {LOG}")
            try:
                with urllib.request.urlopen(self.url + "/health", timeout=2) as resp:
                    if resp.status == 200:
                        return
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(0.25)
        self.stop()
        raise RuntimeError(f"llama-server did not come up within {timeout:g} s; see {LOG}")

    def stop(self) -> None:
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None

    def chat(self, system: str, user: str, max_tokens: int, temperature: float = 0.0) -> str:
        self.start()
        self.last_used = time.monotonic()
        body = json.dumps({
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_tokens, "temperature": temperature, "cache_prompt": True,
        }).encode("utf-8")
        req = urllib.request.Request(self.url + "/v1/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        self.last_used = time.monotonic()
        return data["choices"][0]["message"].get("content") or ""

    # -- the three jobs ------------------------------------------------------
    def fix(self, text: str) -> str:
        return _shaped(text, self.chat(FIX, text.strip(), _budget(text)))

    def rewrite(self, text: str, style: str) -> str:
        return _shaped(text, self.chat(STYLES[style] + REWRITE_TAIL, text.strip(), _budget(text) + 64,
                                       temperature=0.3))

    def continue_text(self, text: str) -> str:
        out = self.chat(CONTINUE, text.strip()[-1500:], 40, temperature=0.3).strip()
        return out.splitlines()[0].strip() if out else ""


def _budget(text: str) -> int:
    """Tokens allowed for an answer about as long as text."""
    return max(48, int(len(text.encode("utf-8")) / 2.5) + 32)


def _shaped(original: str, answer: str) -> str:
    """The model's answer in the place of original: same outer white space,
    no added quotation marks; the original itself when the answer is empty
    or wildly longer (the model started to chat)."""
    out = answer.strip()
    core = original.strip()
    for a, b in (('"', '"'), ("“", "”"), ("'", "'")):
        if len(out) > 1 and out.startswith(a) and out.endswith(b) and not (core.startswith(a) and core.endswith(b)):
            out = out[1:-1].strip()
    if not out or len(out) > 3 * len(core) + 40:
        return original
    lead = original[:len(original) - len(original.lstrip())]
    tail = original[len(original.rstrip()):]
    return lead + out + tail
