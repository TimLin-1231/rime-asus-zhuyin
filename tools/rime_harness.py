"""Drive librime (Weasel's rime.dll) from Python for automated tests.

The harness deploys a *copy* of the config into a private staging directory
(.harness/<name>/user), so the live IME and its learned data are never touched.
librime can only be initialised once per process; run one config per process.

Example:
    python tools/rime_harness.py --keys "su3cl3"
    python tools/rime_harness.py --config stock --schema bopomofo_tw --keys "su3cl3"
"""

from __future__ import annotations

import argparse
import ctypes as C
import json
import os
import shutil
import sys
import time
import winreg
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RIME_SRC = ROOT / "rime"
HARNESS_DIR = ROOT / ".harness"

# Files in rime/ that are runtime state, not configuration.
SKIP_NAMES = {"build", "sync", "installation.yaml", "user.yaml"}
SKIP_SUFFIXES = (".userdb",)

# X11 keysyms / masks used by librime.
XK = {
    "space": 0x20, "Return": 0xFF0D, "Escape": 0xFF1B, "BackSpace": 0xFF08,
    "Tab": 0xFF09, "Left": 0xFF51, "Up": 0xFF52, "Right": 0xFF53, "Down": 0xFF54,
    "Page_Up": 0xFF55, "Page_Down": 0xFF56, "Home": 0xFF50, "End": 0xFF57,
    "Shift_L": 0xFFE1, "Shift_R": 0xFFE2, "Control_L": 0xFFE3, "Control_R": 0xFFE4,
    "Caps_Lock": 0xFFE5,
}
SHIFT_MASK, CONTROL_MASK, RELEASE_MASK = 1 << 0, 1 << 2, 1 << 30

Bool = C.c_int
RimeSessionId = C.c_size_t


class RimeTraits(C.Structure):
    _fields_ = [
        ("data_size", C.c_int),
        ("shared_data_dir", C.c_char_p),
        ("user_data_dir", C.c_char_p),
        ("distribution_name", C.c_char_p),
        ("distribution_code_name", C.c_char_p),
        ("distribution_version", C.c_char_p),
        ("app_name", C.c_char_p),
        ("modules", C.POINTER(C.c_char_p)),
        ("min_log_level", C.c_int),
        ("log_dir", C.c_char_p),
        ("prebuilt_data_dir", C.c_char_p),
        ("staging_dir", C.c_char_p),
    ]


class RimeComposition(C.Structure):
    _fields_ = [
        ("length", C.c_int), ("cursor_pos", C.c_int),
        ("sel_start", C.c_int), ("sel_end", C.c_int),
        ("preedit", C.c_char_p),
    ]


class RimeCandidate(C.Structure):
    _fields_ = [("text", C.c_char_p), ("comment", C.c_char_p), ("reserved", C.c_void_p)]


class RimeMenu(C.Structure):
    _fields_ = [
        ("page_size", C.c_int), ("page_no", C.c_int), ("is_last_page", Bool),
        ("highlighted_candidate_index", C.c_int), ("num_candidates", C.c_int),
        ("candidates", C.POINTER(RimeCandidate)), ("select_keys", C.c_char_p),
    ]


class RimeCommit(C.Structure):
    _fields_ = [("data_size", C.c_int), ("text", C.c_char_p)]


class RimeContext(C.Structure):
    _fields_ = [
        ("data_size", C.c_int),
        ("composition", RimeComposition),
        ("menu", RimeMenu),
        ("commit_text_preview", C.c_char_p),
        ("select_labels", C.POINTER(C.c_char_p)),
    ]


class RimeStatus(C.Structure):
    _fields_ = [
        ("data_size", C.c_int),
        ("schema_id", C.c_char_p), ("schema_name", C.c_char_p),
        ("is_disabled", Bool), ("is_composing", Bool), ("is_ascii_mode", Bool),
        ("is_full_shape", Bool), ("is_simplified", Bool), ("is_traditional", Bool),
        ("is_ascii_punct", Bool),
    ]


_F = C.CFUNCTYPE
_P = C.POINTER
# Order must match rime_api.h of librime 1.13.1 exactly.
_API_FIELDS = [
    ("data_size", C.c_int),
    ("setup", _F(None, _P(RimeTraits))),
    ("set_notification_handler", C.c_void_p),
    ("initialize", _F(None, _P(RimeTraits))),
    ("finalize", _F(None)),
    ("start_maintenance", _F(Bool, Bool)),
    ("is_maintenance_mode", _F(Bool)),
    ("join_maintenance_thread", _F(None)),
    ("deployer_initialize", _F(None, _P(RimeTraits))),
    ("prebuild", _F(Bool)),
    ("deploy", _F(Bool)),
    ("deploy_schema", _F(Bool, C.c_char_p)),
    ("deploy_config_file", _F(Bool, C.c_char_p, C.c_char_p)),
    ("sync_user_data", _F(Bool)),
    ("create_session", _F(RimeSessionId)),
    ("find_session", _F(Bool, RimeSessionId)),
    ("destroy_session", _F(Bool, RimeSessionId)),
    ("cleanup_stale_sessions", _F(None)),
    ("cleanup_all_sessions", _F(None)),
    ("process_key", _F(Bool, RimeSessionId, C.c_int, C.c_int)),
    ("commit_composition", _F(Bool, RimeSessionId)),
    ("clear_composition", _F(None, RimeSessionId)),
    ("get_commit", _F(Bool, RimeSessionId, _P(RimeCommit))),
    ("free_commit", _F(Bool, _P(RimeCommit))),
    ("get_context", _F(Bool, RimeSessionId, _P(RimeContext))),
    ("free_context", _F(Bool, _P(RimeContext))),
    ("get_status", _F(Bool, RimeSessionId, _P(RimeStatus))),
    ("free_status", _F(Bool, _P(RimeStatus))),
    ("set_option", _F(None, RimeSessionId, C.c_char_p, Bool)),
    ("get_option", _F(Bool, RimeSessionId, C.c_char_p)),
    ("set_property", C.c_void_p),
    ("get_property", C.c_void_p),
    ("get_schema_list", C.c_void_p),
    ("free_schema_list", C.c_void_p),
    ("get_current_schema", _F(Bool, RimeSessionId, C.c_char_p, C.c_size_t)),
    ("select_schema", _F(Bool, RimeSessionId, C.c_char_p)),
    ("schema_open", C.c_void_p),
    ("config_open", C.c_void_p),
    ("config_close", C.c_void_p),
    ("config_get_bool", C.c_void_p),
    ("config_get_int", C.c_void_p),
    ("config_get_double", C.c_void_p),
    ("config_get_string", C.c_void_p),
    ("config_get_cstring", C.c_void_p),
    ("config_update_signature", C.c_void_p),
    ("config_begin_map", C.c_void_p),
    ("config_next", C.c_void_p),
    ("config_end", C.c_void_p),
    ("simulate_key_sequence", _F(Bool, RimeSessionId, C.c_char_p)),
    ("register_module", C.c_void_p),
    ("find_module", C.c_void_p),
    ("run_task", _F(Bool, C.c_char_p)),
    ("get_shared_data_dir", C.c_void_p),
    ("get_user_data_dir", C.c_void_p),
    ("get_sync_dir", C.c_void_p),
    ("get_user_id", C.c_void_p),
    ("get_user_data_sync_dir", C.c_void_p),
    ("config_init", C.c_void_p),
    ("config_load_string", C.c_void_p),
    ("config_set_bool", C.c_void_p),
    ("config_set_int", C.c_void_p),
    ("config_set_double", C.c_void_p),
    ("config_set_string", C.c_void_p),
    ("config_get_item", C.c_void_p),
    ("config_set_item", C.c_void_p),
    ("config_clear", C.c_void_p),
    ("config_create_list", C.c_void_p),
    ("config_create_map", C.c_void_p),
    ("config_list_size", C.c_void_p),
    ("config_begin_list", C.c_void_p),
    ("get_input", _F(C.c_char_p, RimeSessionId)),
    ("get_caret_pos", _F(C.c_size_t, RimeSessionId)),
    ("select_candidate", _F(Bool, RimeSessionId, C.c_size_t)),
    ("get_version", _F(C.c_char_p)),
]


class RimeApi(C.Structure):
    _fields_ = _API_FIELDS


def weasel_root() -> Path:
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Rime\Weasel") as k:
        return Path(winreg.QueryValueEx(k, "WeaselRoot")[0])


def _link_or_copy(src: Path, dst: Path) -> None:
    """Copy small files; hard-link large ones from a private cache copy.

    Linking straight to rime/ would make staged links undeletable while the
    live Weasel server keeps the original memory-mapped.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    if src.stat().st_size < (1 << 20):
        shutil.copy2(src, dst)
        return
    cached = HARNESS_DIR / "_cache" / src.name
    st = src.stat()
    if not cached.exists() or cached.stat().st_size != st.st_size \
            or cached.stat().st_mtime < st.st_mtime:
        cached.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, cached)
    try:
        os.link(cached, dst)
    except OSError:
        shutil.copy2(cached, dst)


def stage_user_dir(name: str, config: str, with_userdb: bool = False,
                   exclude: tuple[str, ...] = (), extra: dict[str, str] | None = None) -> Path:
    """Create .harness/<name>/user from rime/ ("project") or empty ("stock").

    exclude: top-level names to leave out (e.g. "grammar.yaml" for ablations).
    extra: additional files to write, {relative path: text}.
    """
    user = HARNESS_DIR / name / "user"
    trash = HARNESS_DIR / "_trash"
    if trash.exists():
        shutil.rmtree(trash, ignore_errors=True)  # best effort
    if user.exists():
        try:
            shutil.rmtree(user)
        except PermissionError:
            # A hard link whose target the live Weasel keeps mapped cannot be
            # deleted; the directory can still be moved out of the way.
            trash.mkdir(parents=True, exist_ok=True)
            user.rename(trash / f"{name}-{time.time_ns()}")
    user.mkdir(parents=True)
    if config == "project":
        for src in RIME_SRC.rglob("*"):
            rel = src.relative_to(RIME_SRC)
            top = rel.parts[0]
            if (top in SKIP_NAMES or top in exclude or top.endswith(SKIP_SUFFIXES)
                    or not src.is_file()):
                continue
            _link_or_copy(src, user / rel)
        if with_userdb:
            for db in RIME_SRC.glob("*.userdb"):
                shutil.copytree(db, user / db.name)
    elif config != "stock":
        raise ValueError(f"unknown config {config!r}")
    for rel, text in (extra or {}).items():
        (user / rel).parent.mkdir(parents=True, exist_ok=True)
        (user / rel).write_text(text, encoding="utf-8")
    return user


@dataclass
class Snapshot:
    """What the user would see after a key sequence."""
    input: str
    preedit: str
    preview: str
    candidates: list[tuple[str, str]] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    highlighted: int = 0
    ascii_mode: bool = False
    composing: bool = False
    committed: str = ""

    @property
    def top(self) -> str:
        return self.candidates[0][0] if self.candidates else ""


class Rime:
    def __init__(self, user_dir: Path, log_name: str = "harness"):
        root = weasel_root()
        self.dll = C.CDLL(str(root / "rime.dll"))
        self.dll.rime_get_api.restype = _P(RimeApi)
        self.api = self.dll.rime_get_api().contents
        # glog cannot open non-ASCII paths on Windows, so log under %TEMP%.
        log_dir = Path(os.environ["TEMP"]) / "rime.harness" / log_name
        if log_dir.exists():
            shutil.rmtree(log_dir, ignore_errors=True)
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir = log_dir
        # Keep references alive for the lifetime of librime.
        self._strings = [str(root / "data").encode(), str(user_dir).encode(),
                         str(log_dir).encode()]
        t = RimeTraits()
        t.data_size = C.sizeof(RimeTraits) - C.sizeof(C.c_int)
        t.shared_data_dir, t.user_data_dir, t.log_dir = self._strings
        t.distribution_name = b"Rime Harness"
        t.distribution_code_name = b"harness"
        t.distribution_version = b"0.1"
        t.app_name = f"rime.{log_name}".encode()
        t.min_log_level = 0
        self.traits = t
        self.api.setup(C.byref(t))
        self.api.initialize(C.byref(t))
        if self.api.start_maintenance(1):
            self.api.join_maintenance_thread()
        self.session = self.api.create_session()
        self.committed: list[str] = []

    @property
    def version(self) -> str:
        return self.api.get_version().decode()

    def select_schema(self, schema_id: str) -> bool:
        return bool(self.api.select_schema(self.session, schema_id.encode()))

    def current_schema(self) -> str:
        buf = C.create_string_buffer(256)
        self.api.get_current_schema(self.session, buf, 256)
        return buf.value.decode()

    # -- input -------------------------------------------------------------
    def key(self, name_or_char: str, mask: int = 0) -> bool:
        code = XK.get(name_or_char)
        if code is None:
            assert len(name_or_char) == 1, name_or_char
            code = ord(name_or_char)
        handled = bool(self.api.process_key(self.session, code, mask))
        self._drain_commit()
        return handled

    def tap(self, name: str, hold_ms: float = 30) -> None:
        """Press and release a key (used for modifier taps)."""
        mask = SHIFT_MASK if name.startswith("Shift") else (
            CONTROL_MASK if name.startswith("Control") else 0)
        self.key(name, 0)
        time.sleep(hold_ms / 1000)
        self.key(name, mask | RELEASE_MASK)

    def type(self, keys: str) -> None:
        """Type printable ASCII keys one by one (space included)."""
        for ch in keys:
            self.key(ch)

    def clear(self) -> None:
        self.api.clear_composition(self.session)
        self.committed.clear()

    def caret(self) -> int:
        return int(self.api.get_caret_pos(self.session))

    def set_option(self, name: str, value: bool) -> None:
        self.api.set_option(self.session, name.encode(), int(value))

    def get_option(self, name: str) -> bool:
        return bool(self.api.get_option(self.session, name.encode()))

    def _drain_commit(self) -> None:
        commit = RimeCommit()
        commit.data_size = C.sizeof(RimeCommit) - C.sizeof(C.c_int)
        if self.api.get_commit(self.session, C.byref(commit)):
            self.committed.append(commit.text.decode())
            self.api.free_commit(C.byref(commit))

    # -- output ------------------------------------------------------------
    def snapshot(self) -> Snapshot:
        ctx = RimeContext()
        ctx.data_size = C.sizeof(RimeContext) - C.sizeof(C.c_int)
        status = RimeStatus()
        status.data_size = C.sizeof(RimeStatus) - C.sizeof(C.c_int)
        snap = Snapshot(input=(self.api.get_input(self.session) or b"").decode(),
                        preedit="", preview="", committed="".join(self.committed))
        if self.api.get_context(self.session, C.byref(ctx)):
            comp, menu = ctx.composition, ctx.menu
            snap.preedit = (comp.preedit or b"").decode()
            snap.preview = (ctx.commit_text_preview or b"").decode()
            snap.highlighted = menu.highlighted_candidate_index
            for i in range(menu.num_candidates):
                c = menu.candidates[i]
                snap.candidates.append(((c.text or b"").decode(), (c.comment or b"").decode()))
                if ctx.select_labels:
                    snap.labels.append(ctx.select_labels[i].decode())
            self.api.free_context(C.byref(ctx))
        if self.api.get_status(self.session, C.byref(status)):
            snap.ascii_mode = bool(status.is_ascii_mode)
            snap.composing = bool(status.is_composing)
            self.api.free_status(C.byref(status))
        return snap

    def close(self) -> None:
        self.api.destroy_session(self.session)
        self.api.finalize()


def open_rime(name: str = "default", config: str = "project",
              schema: str = "asus_zhuyin", with_userdb: bool = False,
              exclude: tuple[str, ...] = (), extra: dict[str, str] | None = None) -> Rime:
    user = stage_user_dir(name, config, with_userdb, exclude, extra)
    rime = Rime(user, log_name=name)
    if not rime.select_schema(schema) or rime.current_schema() != schema:
        raise RuntimeError(f"schema {schema!r} not available (got {rime.current_schema()!r}); "
                           f"see {rime.log_dir}")
    return rime


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", choices=["project", "stock"], default="project")
    ap.add_argument("--schema", default="asus_zhuyin")
    ap.add_argument("--keys", action="append", default=[], help="key sequence; repeatable")
    ap.add_argument("--expand", action="store_true", help="press Down after typing")
    args = ap.parse_args()
    rime = open_rime(f"cli-{args.config}", args.config, args.schema)
    print(f"librime {rime.version}, schema {rime.current_schema()}")
    for keys in args.keys:
        rime.clear()
        rime.type(keys)
        if args.expand:
            rime.key("Down")
        print(json.dumps(rime.snapshot().__dict__, ensure_ascii=False))
    rime.close()


if __name__ == "__main__":
    main()
