"""The writing helper: select text in any program, press the hotkey
(Ctrl+Alt+W), and a small window offers the text with its grammar corrected;
other buttons reword it or continue it. Enter puts the offered text in place
of the selection, Esc leaves everything as it was.

    pythonw -m assist            (start_assist.bat)
    python -m assist --selftest  runs the checks on a sample without a window

Everything runs on this machine: Harper (rules, explains what it found) and a
small language model served by llama.cpp on 127.0.0.1 (assist/llm.py). The
model is loaded on first use and unloaded after a while unused.

While it runs, the input method's English mode also gets its continuations
from the same model (assist/bridge.py): a pause after a word, and the bubble
offers the next few words.

Settings (optional file assist/config.json):
    {"hotkey": "ctrl+alt+w", "idle_minutes": 10, "line_if_empty": true, "port": 8719,
     "continue_in_ime": true, "pause_ms": 600}
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import difflib
import json
import queue
import re
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

from . import bridge, harper, llm, selection

ROOT = Path(__file__).resolve().parents[1]
CONFIG = Path(__file__).resolve().parent / "config.json"
DEFAULTS = {"hotkey": "ctrl+alt+w", "idle_minutes": 10, "line_if_empty": True, "port": 8719,
            "continue_in_ime": True, "pause_ms": 600}

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
MODS = {"alt": 0x1, "ctrl": 0x2, "shift": 0x4, "win": 0x8}
MOD_NOREPEAT = 0x4000
VKS = {"space": 0x20, "enter": 0x0D, **{f"f{i}": 0x6F + i for i in range(1, 13)}}

# Ctrl+C in a terminal interrupts the running program instead of copying.
TERMINALS = {"windowsterminal.exe", "openconsole.exe", "conhost.exe", "cmd.exe", "powershell.exe",
             "pwsh.exe", "mintty.exe", "wezterm-gui.exe", "alacritty.exe"}

# (key, label on the button, what the header says while it is shown)
MODES = [("fix", "修正", "文法與拼字修正"), ("natural", "自然", "改寫：更自然"),
         ("concise", "精簡", "改寫：更精簡"), ("formal", "正式", "改寫：更正式"),
         ("friendly", "親切", "改寫：更親切"), ("continue", "續寫", "續寫")]
FONT = ("Microsoft JhengHei UI", 11)
SMALL = ("Microsoft JhengHei UI", 9)
BG, FG, GRAY, MARK, BLUE = "#F5F5F5", "#1A1A1A", "#5F6B7A", "#FFE9A8", "#006CE1"
TOKEN = re.compile(r"\s+|\w+|[^\w\s]")
TITLE, READY_TITLE = "寫作小幫手", "寫作小幫手 ✓"


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG.exists():
        try:
            cfg.update(json.loads(CONFIG.read_text(encoding="utf-8")))
        except ValueError as e:
            print(f"assist/config.json ignored: {e}", file=sys.stderr)
    return cfg


def parse_hotkey(spec: str) -> tuple[int, int]:
    """"ctrl+alt+g" -> (modifier mask, virtual key)."""
    mods, vk = 0, 0
    for part in spec.lower().replace(" ", "").split("+"):
        if part in MODS:
            mods |= MODS[part]
        elif part in VKS:
            vk = VKS[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        else:
            raise ValueError(f"unknown key {part!r} in hotkey {spec!r}")
    if not vk or not mods:
        raise ValueError(f"hotkey {spec!r} needs a modifier and a key")
    return mods, vk


def changed_spans(old: str, new: str) -> list[tuple[int, int]]:
    """Character ranges of new that are not in old (word by word)."""
    a, b = TOKEN.findall(old), TOKEN.findall(new)
    starts, pos = [], 0
    for tok in b:
        starts.append(pos)
        pos += len(tok)
    starts.append(pos)
    spans = []
    for op, _, _, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op in ("replace", "insert") and j2 > j1:
            spans.append((starts[j1], starts[j2]))
    return spans


class Hotkey(threading.Thread):
    """A global hotkey; every press puts "hotkey" on the queue."""

    def __init__(self, spec: str, events: queue.Queue):
        super().__init__(daemon=True)
        self.mods, self.vk = parse_hotkey(spec)
        self.events, self.ok, self.ready = events, False, threading.Event()
        self.thread_id = 0

    def run(self) -> None:
        self.thread_id = kernel32.GetCurrentThreadId()
        self.ok = bool(user32.RegisterHotKey(None, 1, self.mods | MOD_NOREPEAT, self.vk))
        self.ready.set()
        if not self.ok:
            return
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                self.events.put(("hotkey",))
        user32.UnregisterHotKey(None, 1)

    def stop(self) -> None:
        if self.thread_id:
            user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)


class App:
    def __init__(self, cfg: dict, visible: bool = True):
        self.cfg, self.visible = cfg, visible
        self.root = tk.Tk()
        self.root.withdraw()
        self.events: queue.Queue = queue.Queue()
        self.model = llm.Model(port=int(cfg["port"])) if llm.available() else None
        self.llm_lock = threading.Lock()
        self.top: tk.Toplevel | None = None
        self.job = 0          # popups opened so far; results of older ones are dropped
        self.done = False
        self.root.after(40, self.poll)
        self.root.after(60_000, self.idle_check)

    # -- events from the hotkey thread and the workers -------------------------
    def poll(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "hotkey":
                    self.on_hotkey()
                elif event[0] == "result" and event[1] == self.job and self.top is not None:
                    self.on_result(*event[2:])
        except queue.Empty:
            pass
        if not self.done:
            self.root.after(40, self.poll)

    def idle_check(self) -> None:
        m = self.model
        if m and m.running() and time.monotonic() - m.last_used > float(self.cfg["idle_minutes"]) * 60:
            threading.Thread(target=self.stop_model, daemon=True).start()
        if not self.done:
            self.root.after(60_000, self.idle_check)

    def stop_model(self) -> None:
        with self.llm_lock:
            if self.model:
                self.model.stop()

    # -- the hotkey ------------------------------------------------------------
    def on_hotkey(self) -> None:
        if self.top is not None:  # already open: bring it back to the front
            self.top.lift()
            self.top.focus_force()
            return
        target = selection.foreground()
        if selection.process_name(target) in TERMINALS:
            self.toast("終端機裡不能用（Ctrl+C 會中斷程式）")
            return
        text, before = selection.grab(bool(self.cfg["line_if_empty"]))
        if not text.strip():
            selection.restore(before)
            self.toast("請先選取要檢查的文字")
            return
        self.open(text, target, before)

    def toast(self, message: str) -> None:
        tip = tk.Toplevel(self.root)
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        tk.Label(tip, text=message, font=FONT, bg="#4D4D4D", fg="#F0EFEC", padx=12, pady=6).pack()
        self.place(tip)
        tip.after(1800, tip.destroy)

    def place(self, win: tk.Toplevel) -> None:
        """Next to the mouse pointer, inside the screen."""
        win.update_idletasks()
        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        x = max(8, min(pt.x + 12, sw - w - 16))
        y = pt.y + 20 if pt.y + 20 + h < sh - 60 else max(8, pt.y - h - 40)
        win.geometry(f"+{x}+{y}")

    # -- the window ------------------------------------------------------------
    def open(self, text: str, target: int, before: str | None) -> None:
        self.job += 1
        self.original, self.target, self.before = text, target, before
        self.results: dict[str, str] = {}
        self.findings: list[harper.Finding] = []
        self.mode = "fix"
        top = self.top = tk.Toplevel(self.root)
        top.title(TITLE)
        top.configure(bg=BG, padx=12, pady=10)
        top.attributes("-topmost", True)
        top.attributes("-toolwindow", True)
        top.protocol("WM_DELETE_WINDOW", self.cancel)
        if not self.visible:
            top.withdraw()

        head = tk.Frame(top, bg=BG)
        head.pack(fill="x")
        self.title = tk.Label(head, text="", font=SMALL, bg=BG, fg=BLUE)
        self.title.pack(side="left")
        self.status = tk.Label(head, text="", font=SMALL, bg=BG, fg=GRAY)
        self.status.pack(side="right")

        lines = max(2, min(12, sum(len(line) // 56 + 1 for line in text.splitlines() or [""]) + 1))
        self.box = tk.Text(top, width=60, height=lines, wrap="word", font=FONT, bg="white", fg=FG,
                           relief="solid", borderwidth=1, padx=8, pady=6, undo=True)
        self.box.pack(fill="both", expand=True, pady=(6, 6))
        self.box.tag_configure("chg", background=MARK)

        self.notes = tk.Label(top, text="", font=SMALL, bg=BG, fg=GRAY, justify="left", anchor="w",
                              wraplength=560)
        self.notes.pack(fill="x")

        row = tk.Frame(top, bg=BG)
        row.pack(fill="x", pady=(8, 0))
        self.apply_button = tk.Button(row, text="套用 (Enter)", font=SMALL, command=self.apply, bg=BLUE,
                                      fg="white", activebackground="#0A5BC0", activeforeground="white",
                                      relief="flat", padx=10, pady=3)
        self.apply_button.pack(side="left")
        self.buttons: dict[str, tk.Button] = {}
        for i, (key, label, _) in enumerate(MODES, 1):
            b = tk.Button(row, text=f"{i} {label}", font=SMALL, relief="flat", bg="#E3E3E3", padx=8, pady=3,
                          command=lambda k=key: self.show(k))
            b.pack(side="left", padx=(6, 0))
            self.buttons[key] = b
            if key != "fix" and self.model is None:
                b.configure(state="disabled")
        tk.Button(row, text="結束小幫手", font=SMALL, relief="flat", bg=BG, fg=GRAY, padx=6, pady=3,
                  command=lambda: (self.cancel(), self.quit())).pack(side="right")
        tk.Button(row, text="取消 (Esc)", font=SMALL, relief="flat", bg=BG, fg=GRAY, command=self.cancel,
                  padx=6, pady=3).pack(side="right")

        top.bind("<Escape>", lambda e: self.cancel())
        top.bind("<Control-Return>", lambda e: self.apply() or "break")
        top.bind("<Return>", self.on_return)
        for i, (key, _, _) in enumerate(MODES, 1):
            top.bind(str(i), lambda e, k=key: self.on_digit(k))

        self.set_text(text)
        self.show("fix")
        threading.Thread(target=self.work_lint, args=(self.job, text), daemon=True).start()
        if self.visible:
            self.place(top)
            top.deiconify()
            top.lift()
            top.focus_force()
            self.apply_button.focus_set()
            user32.SetForegroundWindow(wt.HWND(int(top.wm_frame(), 16)))

    def on_return(self, event) -> str | None:
        if self.top.focus_get() is self.box:
            return None  # a new line while editing the text; Ctrl+Enter applies
        self.apply()
        return "break"

    def on_digit(self, key: str) -> str | None:
        if self.top.focus_get() is self.box:
            return None
        if str(self.buttons[key]["state"]) != "disabled":
            self.show(key)
        return "break"

    def set_text(self, text: str) -> None:
        self.box.delete("1.0", "end")
        self.box.insert("1.0", text)
        for start, end in changed_spans(self.original, text):
            self.box.tag_add("chg", f"1.0+{start}c", f"1.0+{end}c")

    def show(self, mode: str) -> None:
        """Show the result of mode, asking for it first when there is none yet."""
        self.mode = mode
        for key, button in self.buttons.items():
            button.configure(bg="#C9DCF7" if key == mode else "#E3E3E3")
        self.title.configure(text=next(t for k, _, t in MODES if k == mode))
        if mode in self.results:
            self.set_text(self.results[mode])
            self.describe()
            return
        self.status.configure(text="模型載入中…" if self.model and not self.model.running() else "處理中…")
        threading.Thread(target=self.work, args=(self.job, mode, self.original), daemon=True).start()

    def describe(self) -> None:
        result = self.results.get(self.mode, self.original)
        if self.mode == "fix":
            n = len(changed_spans(self.original, result))
            self.status.configure(text="沒有發現問題" if result == self.original else f"改了 {n} 處")
        else:
            self.status.configure(text="")
        lines = []
        for f in self.findings[:6] if self.mode == "fix" else []:
            fix = f" → {f.replacements[0]}" if f.replacements and f.replacements[0] else ""
            lines.append(f"• {f.text.strip() or '空白'}{fix}：{f.message}")
        self.notes.configure(text="\n".join(lines))

    # -- workers (threads; they only touch the queue) ----------------------------
    def work_lint(self, job: int, text: str) -> None:
        try:
            found = harper.lint(text)
        except Exception as e:  # noqa: BLE001 - a missing checker must not kill the window
            found = []
            print(f"harper: {e}", file=sys.stderr)
        self.events.put(("result", job, "lint", found, None))

    def work(self, job: int, mode: str, text: str) -> None:
        error = None
        try:
            if self.model is None:
                out = harper.apply(text, harper.lint(text))
            else:
                with self.llm_lock:
                    if mode == "fix":
                        out = self.model.fix(text)
                    elif mode == "continue":
                        more = self.model.continue_text(text)
                        gap = "" if not more or text[-1:].isspace() or more[:1] in ".,;:!?" else " "
                        out = text + gap + more
                    else:
                        out = self.model.rewrite(text, mode)
        except Exception as e:  # noqa: BLE001
            out, error = text, str(e)
        self.events.put(("result", job, mode, out, error))

    def on_result(self, mode: str, value, error: str | None) -> None:
        if mode == "lint":
            self.findings = value
            if self.mode == "fix":
                self.describe()
            return
        if error:
            self.status.configure(text=f"失敗：{error[:60]}")
            return
        self.results[mode] = value
        if mode == self.mode:
            self.set_text(value)
            self.describe()
            self.top.title(READY_TITLE)  # (tools/assist_test.py waits for this)

    # -- leaving -----------------------------------------------------------------
    def close(self) -> None:
        if self.top is not None:
            self.top.destroy()
            self.top = None
        self.job += 1  # late results belong to no window

    def apply(self) -> None:
        text = self.box.get("1.0", "end-1c")
        target, before, original = self.target, self.before, self.original
        self.close()
        if text == original or not self.visible:
            selection.restore(before)
            if self.visible:
                user32.SetForegroundWindow(wt.HWND(target))
            return
        if not selection.replace(target, text, before):
            selection.set_clipboard_text(text)
            self.toast("原本的視窗不見了；結果已複製到剪貼簿")

    def cancel(self) -> None:
        target, before = self.target, self.before
        self.close()
        selection.restore(before)
        if self.visible:
            user32.SetForegroundWindow(wt.HWND(target))

    def quit(self) -> None:
        self.done = True
        if self.model:
            self.model.stop()
        self.root.quit()


def selftest(text: str) -> int:
    """The whole pipeline on text, without showing a window or touching
    another program: prints what each button would offer."""
    sys.stdout.reconfigure(encoding="utf-8")
    app = App(load_config(), visible=False)
    app.open(text, 0, None)

    def wait(mode: str, timeout: float = 180.0) -> str:
        deadline = time.monotonic() + timeout
        while mode not in app.results and time.monotonic() < deadline:
            app.root.update()
            time.sleep(0.02)
        return app.results.get(mode, "(no result)")

    print("original :", text)
    for key, label, _ in MODES:
        if app.model is None and key != "fix":
            continue
        t0 = time.monotonic()
        app.show(key)
        out = wait(key)
        marked = [app.box.get(a, b) for a, b in zip(*[iter(map(str, app.box.tag_ranges("chg")))] * 2)]
        print(f"{label:<4} {time.monotonic() - t0:5.2f}s :", out, "| marked:", marked if key == "fix" else len(marked))
    app.show("fix")
    app.root.update()
    print("status   :", app.status.cget("text"))
    print("notes    :", app.notes.cget("text").replace("\n", " / "))
    app.cancel()
    app.quit()
    app.root.destroy()
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        rest = [a for a in sys.argv[1:] if a != "--selftest"]
        return selftest(rest[0] if rest else "I look forward to hear from you, and I will recieve you at the airport tomorow.")
    if sys.stderr is None:  # pythonw: keep errors somewhere
        log = ROOT / ".harness" / "assist.log"
        log.parent.mkdir(exist_ok=True)
        sys.stdout = sys.stderr = log.open("a", encoding="utf-8", buffering=1)
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    kernel32.CreateMutexW(None, False, "Local\\AsusZhuyinWritingHelper")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        user32.MessageBoxW(None, "寫作小幫手已經在執行了。", "寫作小幫手", 0x40)
        return 1
    cfg = load_config()
    app = App(cfg)
    hotkey = Hotkey(cfg["hotkey"], app.events)
    hotkey.start()
    hotkey.ready.wait(5)
    if not hotkey.ok:
        user32.MessageBoxW(None, f"快捷鍵 {cfg['hotkey']} 已經被別的程式用了。\n請在 assist\\config.json 換一個。",
                           "寫作小幫手", 0x30)
        return 1
    if app.model is not None and cfg["continue_in_ime"]:
        try:  # the input method's continuations (kept alive by its threads)
            bridge.Bridge(app.model, app.llm_lock, pause=float(cfg["pause_ms"]) / 1000)
        except OSError as e:
            print(e, file=sys.stderr)
    app.toast(f"寫作小幫手已啟動：選取文字後按 {cfg['hotkey'].upper()}")
    try:
        app.root.mainloop()
    finally:
        hotkey.stop()
        if app.model:
            app.model.stop()
    return 0
