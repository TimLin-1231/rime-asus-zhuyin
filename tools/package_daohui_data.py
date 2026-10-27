"""Build the Rime data that the public 島輝 (DaoHui) installer ships.

One command builds a clean data set (no personal data, no Taiwan MOE data)
into the DaoHui fork's output folder; the installer copies it into DaoHui's
shared data dir, on top of the stock Weasel/Rime data. See DATA.md in the
DaoHui repository for the file-by-file inventory and licences.

    python tools/package_daohui_data.py            # build + --check
    python tools/package_daohui_data.py --check    # only scan the output for private data
    python tools/package_daohui_data.py --smoke    # deploy a Rime staged only from the output
    python tools/package_daohui_data.py --smoke --no-gram

What is built (all paths relative to --out):
  asus_zhuyin.schema.yaml        rime/ copy; asus_lang/raw_keys is ON (product default)
  terra_pinyin.asus.dict.yaml    rime/ copy (imports the five tables below)
  terra_pinyin.mcb.dict.yaml     build_lexicon.py --public (McBopomofo, MIT)
  terra_pinyin.tw.dict.yaml      empty stub (MOE data, CC BY-ND, is not redistributed)
  terra_pinyin.personal.dict.yaml empty stub (a user's own file in the user dir wins)
  terra_pinyin.synced.dict.yaml  empty stub (the settings app's sync writes the user's own)
  grammar.yaml                   rime/ copy (names the downloaded wanxiang LM)
  default.yaml, weasel.yaml      the fork's stock files + rime/default.custom.yaml and
                                 rime/weasel.custom.yaml baked in as a root __patch,
                                 which ends with <id>.custom:/patch? so a user's own
                                 custom file still applies (and cannot drop these)
  shortcuts.example.txt          template only: lua reads shortcuts.txt from the user dir
  lua/*.lua                      rime/lua (git-tracked files) + public asus_zhuyin_data.lua
  opencc/asus_*.json, *.ocd2     build_lang_data.py --public (easy_en, SymSpell, McBopomofo)
  DAOHUI_DATA_LICENSES.txt       attribution / licence texts for the data
  FILES.txt                      every file above, one relative path per line (for NSIS)

Never shipped: *.userdb, user.yaml, installation.yaml, emails.txt, shortcuts.txt,
*.custom.yaml, *.gram (the 410 MB LM is downloaded by the settings app),
terra_pinyin.tw from MOE, personal words, personal English words.

Outputs and scratch files must live outside D:\\Document (Google Drive): the
defaults are D:\\src\\daohui\\output\\data_daohui and D:\\.rime-harness\\pkg.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
RIME = ROOT / "rime"
sys.path.insert(0, str(TOOLS))
# No __pycache__ churn under D:\Document (Google Drive), here or in the tools run below.
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

DEFAULT_OUT = Path(r"D:\src\daohui\output\data_daohui")
DEFAULT_SCRATCH = Path(r"D:\.rime-harness\pkg")
DEFAULT_FORK_DATA = Path(r"D:\src\daohui\output\data")
DEFAULT_RIME_DLL = Path(r"D:\src\daohui\output\rime.dll")
GRAM = "wanxiang-lts-zh-hant.gram"

CC_NAMES = ["asus_en", "asus_en_prefix", "asus_en_fix", "asus_en_follow", "asus_next", "asus_seg", "asus_tw"]
COPY_AS_IS = ["terra_pinyin.asus.dict.yaml", "grammar.yaml", "shortcuts.example.txt"]
FROM_BUILD = ["terra_pinyin.mcb.dict.yaml", "terra_pinyin.tw.dict.yaml", "terra_pinyin.personal.dict.yaml",
              "terra_pinyin.synced.dict.yaml"]
STUBS = ["terra_pinyin.tw.dict.yaml", "terra_pinyin.personal.dict.yaml", "terra_pinyin.synced.dict.yaml"]
GENERATED_LUA = "asus_zhuyin_data.lua"
LICENSES = "DAOHUI_DATA_LICENSES.txt"
FILES_LIST = "FILES.txt"

# File names that must never be in the package (private state or not redistributable).
FORBIDDEN_NAME = re.compile(
    r"(\.userdb|^user\.yaml$|^installation\.yaml$|^emails\.txt$|^shortcuts\.txt$|"
    r"\.custom\.yaml$|\.gram$|custom_phrase|\.txt\.bak$)", re.I)

OPENCC_DICT = ROOT / "third_party" / "opencc-build" / "src" / "tools" / "Release" / "opencc_dict.exe"
HAN = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")


# ----------------------------------------------------------------- helpers
def die(msg: str) -> None:
    raise SystemExit(f"package_daohui_data: {msg}")


def is_reparse(p: Path) -> bool:
    """Junction or symlink (never delete through one: .harness is a junction)."""
    try:
        st = os.lstat(p)
    except FileNotFoundError:
        return False
    return bool(getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT) \
        or stat.S_ISLNK(st.st_mode)


def guard_outside_document(p: Path, what: str) -> Path:
    """Refuse any output/scratch path under D:\\Document (Google Drive backup)."""
    p = p.resolve()
    doc = ROOT.parent.resolve()
    if str(p).lower().startswith(str(doc).lower() + os.sep) or p == doc:
        die(f"{what} {p} is under {doc} (Google Drive); use a folder outside it")
    return p


def fresh_dir(p: Path, marker: str | None = None) -> None:
    """Empty `p` (created if missing). Only deletes a real directory that is
    empty or carries `marker` (a file this script wrote), never a junction."""
    if is_reparse(p):
        die(f"{p} is a junction/symlink; refusing to delete through it")
    if p.exists():
        if marker and any(p.iterdir()) and not (p / marker).exists():
            die(f"{p} exists, is not empty and has no {marker}; not deleting it")
        for child in p.rglob("*"):
            if is_reparse(child):
                die(f"{child} is a junction/symlink; refusing to delete {p}")
        shutil.rmtree(p)
    p.mkdir(parents=True)


def write_lf(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.replace("\r\n", "\n").encode("utf-8"))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8").stdout.strip()


def run_tool(script: str, *args: str) -> None:
    proc = subprocess.run([sys.executable, str(TOOLS / script), *args], capture_output=True)
    out = proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace")
    print(f"  {script}: {out.strip()}")
    if proc.returncode:
        die(f"{script} failed with code {proc.returncode}")


# ----------------------------------------------------------------- transforms
RAW_KEYS_SUBS = [
    # (old, new): each must occur exactly once in rime/asus_zhuyin.schema.yaml.
    ('    # "Raw keys first" inline preedit mode (lua/asus_lang.lua), off by default.\n'
     '    asus_lang/raw_keys: false\n',
     '    # "Raw keys first" inline preedit mode (lua/asus_lang.lua): ON in DaoHui\n'
     '    # (set by tools/package_daohui_data.py; the repository schema keeps it off).\n'
     '    asus_lang/raw_keys: true\n'),
    ('# "Raw keys first" inline preedit mode (lua/asus_lang.lua), off by default:\n',
     '# "Raw keys first" inline preedit mode (lua/asus_lang.lua), on by default in DaoHui:\n'),
    ('asus_lang:\n  raw_keys: false\n', 'asus_lang:\n  raw_keys: true\n'),
]


def product_schema(text: str) -> str:
    """The repository schema with the product default raw_keys: true (the
    developer's own choice, kept in his asus_zhuyin.custom.yaml). Baked into
    the schema, not shipped as a shared asus_zhuyin.custom.yaml: the settings
    app writes the user's asus_zhuyin.custom.yaml, which would hide a shared
    one completely (Rime resolves a config file user dir first, no merging)."""
    import yaml
    text = text.replace("\r\n", "\n")
    for old, new in RAW_KEYS_SUBS:
        if text.count(old) != 1:
            die(f"asus_zhuyin.schema.yaml changed: expected exactly one {old.strip()!r}")
        text = text.replace(old, new)
    doc = yaml.safe_load(text)
    if doc["asus_lang"]["raw_keys"] is not True or doc["__patch"][0]["asus_lang/raw_keys"] is not True \
            or doc["__patch"][-1] != "asus_zhuyin.custom:/patch?":
        die("raw_keys transform did not produce the expected schema")
    return text


def bake_patch(base: str, custom: str, config_id: str, source: str) -> str:
    """Append rime/<id>.custom.yaml's `patch:` map to the stock <id>.yaml as a
    root `__patch` list ending with `<id>.custom:/patch?`. librime skips its
    automatic <id>.custom patch when the root has an explicit __patch, hence
    the explicit last item: a user's own <id>.custom.yaml still applies on top.
    (Shipping a shared <id>.custom.yaml instead would be hidden as a whole by
    the user's file, which Weasel's own schema/style dialogs write.)"""
    import yaml
    base = base.replace("\r\n", "\n")
    custom = custom.replace("\r\n", "\n")
    if re.search(r"^__patch\s*:", base, re.M):
        die(f"stock {config_id}.yaml already has a root __patch; merge by hand")
    lines = custom.split("\n")
    try:
        start = lines.index("patch:") + 1
    except ValueError:
        die(f"{source}: no top-level 'patch:' line")
    block = lines[start:]
    out, first = [], True
    for line in block:
        if line.strip() and not line.startswith("  "):
            die(f"{source}: unexpected top-level line after patch: {line!r}")
        if not line.strip():
            out.append("")
            continue
        line = "  " + line
        if first and not line.lstrip().startswith("#"):
            line = "  - " + line[4:]
            first = False
        out.append(line)
    while out and not out[-1]:
        out.pop()
    section = ("\n# ---------------------------------------------------------------------------\n"
               f"# 島輝 DaoHui product defaults, from {source} of the input-method project\n"
               "# (tools/package_daohui_data.py). The last item applies the user's own\n"
               f"# {config_id}.custom.yaml on top, as librime would do by itself.\n"
               "__patch:\n" + "\n".join(out) + f"\n  - {config_id}.custom:/patch?\n")
    result = base.rstrip("\n") + "\n" + section
    # PyYAML rejects the tab before a comment in stock weasel.yaml (yaml-cpp
    # accepts it); tabs are replaced for this verification only.
    want = yaml.safe_load(custom.replace("\t", " "))["patch"]
    got = yaml.safe_load(result.replace("\t", " "))
    stock = yaml.safe_load(base.replace("\t", " "))
    if got["__patch"] != [want, f"{config_id}.custom:/patch?"]:
        die(f"baked __patch of {config_id}.yaml does not equal {source}'s patch")
    if {k: v for k, v in got.items() if k != "__patch"} != stock:
        die(f"baking changed the stock keys of {config_id}.yaml")
    return result


def licenses_text() -> str:
    mcb = (ROOT / "third_party" / "mcbopomofo" / "LICENSE.txt").read_text(encoding="utf-8").strip()
    sym = (ROOT / "third_party" / "symspell" / "LICENSE").read_text(encoding="utf-8").strip()
    commit = ROOT / "third_party" / "mcbopomofo" / "SOURCE_COMMIT.txt"
    m = re.search(r"[0-9a-f]{40}", commit.read_text(encoding="utf-8")) if commit.exists() else None
    commit_s = m.group()[:12] if m else "?"
    return f"""島輝 DaoHui - data files: sources and licences
==============================================

The schema, Lua scripts and configuration files (asus_zhuyin.schema.yaml,
lua/*.lua, grammar.yaml, terra_pinyin.asus.dict.yaml, the DaoHui part of
default.yaml / weasel.yaml) are part of the DaoHui input-method project
(https://github.com/TimLin-1231/rime-asus-zhuyin), GPL-3.0.

Generated data and where it comes from:

* terra_pinyin.mcb.dict.yaml, opencc/asus_next.*, opencc/asus_seg.*,
  opencc/asus_tw.* (partly)
  Taiwan phrase readings and frequencies from McBopomofo 小麥注音
  (https://github.com/openvanilla/McBopomofo, Source/Data, commit {commit_s}),
  MIT licence (text below). BPMFMappings.txt was originally derived from
  libtabe's tsi.src (BSD licence). Frequencies are rescaled with, and
  asus_tw.* also uses, essay.txt and terra_pinyin.dict.yaml of the Rime
  project (rime-essay, rime-terra-pinyin; LGPL-3.0), shipped with Weasel.

* opencc/asus_en.*, opencc/asus_en_prefix.*, opencc/asus_en_fix.*,
  opencc/asus_en_follow.*
  English words and ranks from easy_en (rime-easy-en by BlindingDark,
  https://github.com/BlindingDark/rime-easy-en, LGPL-3.0; its word list credits
  ECDICT), combined with SymSpell's English word and word-pair frequencies
  (https://github.com/wolfgarbe/SymSpell, MIT licence, text below; the word
  frequencies were made from Google Books Ngram data, CC BY 3.0, and SCOWL).

* lua/asus_zhuyin_data.lua
  Zhuyin syllables of terra_pinyin.dict.yaml (Rime, LGPL-3.0).

* terra_pinyin.tw.dict.yaml, terra_pinyin.personal.dict.yaml,
  terra_pinyin.synced.dict.yaml
  Empty on purpose. A file of the same name in the user folder replaces them.

* Language model (not included; downloaded by the settings app):
  wanxiang-lts-zh-hant.gram from RIME-LMDG by amzxyz
  (https://github.com/amzxyz/RIME-LMDG), CC-BY-4.0.

The ocd2 files are in OpenCC's binary dictionary format (OpenCC, Apache-2.0).

----------------------------------------------------------------------------
McBopomofo data
----------------------------------------------------------------------------
{mcb}

----------------------------------------------------------------------------
SymSpell
----------------------------------------------------------------------------
{sym}
"""


# ----------------------------------------------------------------- build
def build(out: Path, scratch: Path, fork_data: Path) -> None:
    import yaml  # noqa: F401  (fail early if missing)
    if not OPENCC_DICT.exists():
        die(f"{OPENCC_DICT} is missing (the installer ships ocd2 dicts only; see README)")
    for f in ("default.yaml", "weasel.yaml"):
        if not (fork_data / f).exists():
            die(f"{fork_data / f} is missing (--fork-data)")
    work = scratch / "build"
    fresh_dir(work)
    print(f"building public data in {work}")
    run_tool("build_lexicon.py", "--public", "--out", str(work))
    run_tool("build_lang_data.py", "--public", "--out", str(work))

    fresh_dir(out, marker=FILES_LIST)
    write_lf(out / FILES_LIST, "")  # marker first: a failed run can be rebuilt over
    print(f"assembling {out}")
    write_lf(out / "asus_zhuyin.schema.yaml",
             product_schema((RIME / "asus_zhuyin.schema.yaml").read_text(encoding="utf-8")))
    for name in COPY_AS_IS:
        shutil.copyfile(RIME / name, out / name)
    for name in FROM_BUILD:
        shutil.copyfile(work / name, out / name)
    for cid in ("default", "weasel"):
        write_lf(out / f"{cid}.yaml", bake_patch(
            (fork_data / f"{cid}.yaml").read_text(encoding="utf-8"),
            (RIME / f"{cid}.custom.yaml").read_text(encoding="utf-8"), cid, f"rime/{cid}.custom.yaml"))
    tracked = [Path(p) for p in git("ls-files", "rime/lua").splitlines() if p.endswith(".lua")]
    if not tracked:
        die("git ls-files rime/lua returned nothing")
    (out / "lua").mkdir()
    for rel in tracked:
        if rel.name == GENERATED_LUA:
            continue
        shutil.copyfile(ROOT / rel, out / "lua" / rel.name)
    shutil.copyfile(work / "lua" / GENERATED_LUA, out / "lua" / GENERATED_LUA)
    (out / "opencc").mkdir()
    for name in CC_NAMES:
        for ext in (".json", ".ocd2"):
            src = work / "opencc" / f"{name}{ext}"
            if not src.exists():
                die(f"{src} was not built")
            shutil.copyfile(src, out / "opencc" / src.name)
        if '"ocd2"' not in (out / "opencc" / f"{name}.json").read_text(encoding="utf-8"):
            die(f"{name}.json does not point at the ocd2 dict")
    write_lf(out / LICENSES, licenses_text())

    files = sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file() and p.name != FILES_LIST)
    write_lf(out / FILES_LIST, "\n".join(files) + "\n")
    info = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "rev": git("rev-parse", "--short", "HEAD"),
            "dirty": bool(git("status", "--porcelain", "--", "rime", "tools")),
            "files": {f: {"size": (out / f).stat().st_size, "sha256": sha256(out / f)} for f in files}}
    write_lf(scratch / "package_info.json", json.dumps(info, ensure_ascii=False, indent=1))
    total = sum(v["size"] for v in info["files"].values())
    print(f"{len(files)} files, {total / 1e6:.1f} MB, from rev {info['rev']}"
          f"{' (rime/ or tools/ has uncommitted changes)' if info['dirty'] else ''}")


# ----------------------------------------------------------------- check
def private_strings(extra_file: Path | None = None) -> dict[str, set[str]]:
    """Strings from the developer's private files, by kind."""
    zh: set[str] = set()
    en: set[str] = set()
    mail: set[str] = set()

    def add_word(w: str) -> None:
        w = w.strip()
        if len(w) >= 2 and HAN.search(w):
            zh.add(w)
        elif len(w) >= 3 and re.search(r"[A-Za-z]", w):
            en.add(w.lower())

    p = ROOT / "lexicon" / "personal_words.txt"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#"):
                add_word(line.split("\t")[0])
    p = ROOT / "lexicon" / "english_words.txt"
    if p.exists():
        for w in p.read_text(encoding="utf-8").split():
            if not w.startswith("#"):
                en.add(w.lower())
    p = ROOT / "UserDictionary.txt"
    if p.exists():
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            if line.split():
                add_word(line.split()[0])
    p = ROOT / "tests" / "eval" / "private" / "asus_words.tsv"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) > 1:
                add_word(parts[1])
    p, ex = RIME / "shortcuts.txt", RIME / "shortcuts.example.txt"
    if p.exists():
        example = set(ex.read_text(encoding="utf-8").splitlines()) if ex.exists() else set()
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#") and line not in example:
                for part in line.split("\t")[1:]:
                    add_word(part)
                    mail.update(m.lower() for m in EMAIL.findall(part))
    p = RIME / "emails.txt"
    if p.exists():
        mail.update(m.lower() for m in EMAIL.findall(p.read_text(encoding="utf-8", errors="replace")))
    # Learned e-mail addresses live in asus_learn.userdb (LevelDB); the live IME
    # locks some of its files, so the newest backup copy is read as well.
    dbs = list(RIME.glob("*.userdb*"))
    backups = sorted((ROOT / "backups" / "learn").glob("2*"))
    if backups:
        dbs += list(backups[-1].glob("*.userdb*")) + list(backups[-1].glob("emails.txt"))
    for db in dbs:
        for f in db.rglob("*") if db.is_dir() else [db]:
            try:
                data = f.read_bytes()
            except OSError:
                continue  # the live IME locks the LevelDB log
            mail.update(m.decode("ascii", "replace").lower()
                        for m in re.findall(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}", data))
    for cfg in ("user.email",):
        v = git("config", cfg)
        if v:
            mail.add(v.lower())
    # More private strings, one per line, from a file that is never committed
    # (e.g. addresses that are in no file of this machine's Rime data).
    if extra_file and extra_file.exists():
        for line in extra_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                if EMAIL.fullmatch(line):
                    mail.add(line.lower())
                else:
                    add_word(line)
    # Local parts that could identify someone: 4+ characters with a letter.
    local = {m.split("@")[0] for m in mail
             if len(m.split("@")[0]) >= 4 and re.search(r"[a-z]", m.split("@")[0])}
    return {"zh": zh, "en": en, "mail": mail, "mail_local": local}


@lru_cache(maxsize=1)
def public_corpus() -> tuple[str, set[str]]:
    """Text of the public sources the package is built from: a private string
    that occurs there (an ordinary word such as 曲線圖) cannot prove a leak."""
    from rime_harness import weasel_root
    zh_parts = []
    data = weasel_root() / "data"
    for f in (data / "essay.txt", data / "terra_pinyin.dict.yaml",
              ROOT / "third_party" / "mcbopomofo" / "phrase.occ",
              ROOT / "third_party" / "mcbopomofo" / "BPMFMappings.txt"):
        zh_parts.append(f.read_text(encoding="utf-8"))
    en_words: set[str] = set()
    import build_lang_data as bld
    if bld.EASY_EN.exists():
        for line in bld.EASY_EN.read_text(encoding="utf-8-sig").splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                en_words.add(parts[0].lower())
                en_words.add(parts[1].lower())
    for name in ("frequency_dictionary_en_82_765.txt", "frequency_bigramdictionary_en_243_342.txt"):
        f = bld.SYM_DIR / name
        if f.exists():
            for line in f.read_text(encoding="utf-8-sig").splitlines():
                en_words.update(w.lower() for w in line.split(" ")[:-1])
    return "\n".join(zh_parts), en_words


def moe_only_words() -> set[str]:
    """Words only the Taiwan MOE dictionaries have (CC BY-ND): none may be an
    entry of the package. Empty when the MOE files are not on this machine."""
    from zhuyin import moe_words
    moe = moe_words()
    if not moe:
        return set()
    corpus, _ = public_corpus()
    words = set()
    for line in corpus.splitlines():
        w = re.split(r"[\t ]", line, maxsplit=1)[0]
        if w:
            words.add(w)
    return {w for w in moe if w not in words}


def entry_words(path: Path, text: str) -> set[str]:
    """Words that are entries (not comments) of a dict yaml / OpenCC text dict."""
    out: set[str] = set()
    lines = text.splitlines()
    if path.name.endswith(".dict.yaml"):
        lines = lines[lines.index("...") + 1:] if "..." in lines else []
        for line in lines:
            if line and not line.startswith("#"):
                out.add(line.split("\t")[0])
    else:
        for line in lines:
            for tok in re.split(r"[\t ,|]", line):
                tok = tok.split(":", 1)[-1]
                if tok:
                    out.add(tok)
    return out


def ocd2_sources(out: Path, scratch: Path) -> dict[Path, str]:
    """The text dict each shipped ocd2 was made from, after proving it by
    converting the text again and comparing bytes (an ocd2 trie cannot be
    grepped)."""
    texts: dict[Path, str] = {}
    tmp = scratch / "check_ocd2"
    fresh_dir(tmp)
    for ocd in sorted((out / "opencc").glob("*.ocd2")):
        txt = scratch / "build" / "opencc" / (ocd.stem + ".txt")
        if not txt.exists():
            die(f"{txt} is missing: run the build again before --check")
        shutil.copyfile(txt, tmp / txt.name)
        subprocess.run([str(OPENCC_DICT), "-i", txt.name, "-o", ocd.name, "-f", "text", "-t", "ocd2"],
                       check=True, capture_output=True, cwd=tmp)
        if (tmp / ocd.name).read_bytes() != ocd.read_bytes():
            die(f"{ocd} is not the conversion of {txt}")
        texts[ocd] = txt.read_text(encoding="utf-8")
    return texts


def is_generated(rel: Path) -> bool:
    """Data built from word lists (vs. text taken from git-tracked sources)."""
    return rel.parts[0] == "opencc" or rel.name in FROM_BUILD or rel.name == GENERATED_LUA


def check(out: Path, scratch: Path, extra_file: Path | None = None) -> int:
    """Fails on: forbidden files, non-empty stubs, MOE-only entries, private
    e-mail addresses anywhere, personal words in generated data. Personal
    words inside text taken from git-tracked sources (Lua comments, the
    schema) are warnings: they are in the repository too and must be fixed
    there, and listing them here would put them in this script."""
    print(f"checking {out} for private or non-redistributable data")
    problems: list[str] = []
    warnings: list[str] = []
    listed = set((out / FILES_LIST).read_text(encoding="utf-8").split()) if (out / FILES_LIST).exists() else set()
    files = sorted(p for p in out.rglob("*") if p.is_file())
    actual = {str(p.relative_to(out)) for p in files} - {FILES_LIST}
    if listed != actual:
        problems.append(f"{FILES_LIST} does not match the files: missing {sorted(actual - listed)}, "
                        f"extra {sorted(listed - actual)}")
    for p in files:
        if FORBIDDEN_NAME.search(p.name):
            problems.append(f"forbidden file {p.relative_to(out)}")
    texts: dict[Path, str] = {}
    for p in files:
        if p.suffix == ".ocd2":
            continue
        try:
            texts[p] = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            problems.append(f"unexpected binary file {p.relative_to(out)}")
    texts.update(ocd2_sources(out, scratch))
    for stub in STUBS:
        p = out / stub
        if not p.exists() or entry_words(p, texts[p]):
            problems.append(f"{stub} must exist and have no entries")

    priv = private_strings(extra_file)
    corpus, en_public = public_corpus()
    zh = {w for w in priv["zh"] if w not in corpus}
    en = {w for w in priv["en"] if w not in en_public}
    print(f"  private strings: {len(priv['zh'])} Chinese ({len(zh)} not in any public source), "
          f"{len(priv['en'])} English ({len(en)} not in any public source), "
          f"{len(priv['mail'])} e-mail addresses")

    def token_re(words: set[str]) -> re.Pattern | None:
        alt = "|".join(sorted(map(re.escape, words), key=len, reverse=True))
        return re.compile(r"(?<![a-z0-9])(" + alt + r")(?![a-z0-9])") if words else None
    en_re, local_re = token_re(en), token_re(priv["mail_local"])
    source_hits: list[str] = []
    for p, text in texts.items():
        rel = p.relative_to(out)
        low = text.lower()
        found = [f"personal word {w!r}" for w in sorted(zh) if w in text]
        if en_re:
            found += [f"personal English word {m!r}" for m in sorted(set(en_re.findall(low)))]
        (problems if is_generated(rel) else source_hits).extend(f"{rel}: {f}" for f in found)
        for m in priv["mail"]:
            if m in low:
                problems.append(f"{rel}: private e-mail address")
        if local_re and local_re.search(low):
            problems.append(f"{rel}: local part of a private e-mail address")
        for m in set(EMAIL.findall(text)):
            loc = m.split("@")[0].lower()
            if len(loc) >= 4 and any(x.startswith(loc[:4]) for x in priv["mail_local"]):
                warnings.append(f"{rel}: e-mail-shaped example {m!r} begins like a private address")
    moe = moe_only_words()
    if moe:
        # Whole-word entries only (dictionaries, Taiwan spellings, the word
        # list of asus_seg); asus_next holds phrase prefixes and tails.
        hits = set()
        for p, text in texts.items():
            if p.name.endswith(".dict.yaml") or p.stem in ("asus_tw", "asus_seg"):
                hits |= entry_words(p, text) & moe
        print(f"  MOE-only words checked: {len(moe)}; entries of the package among them: {len(hits)}")
        if hits:
            problems.append(f"{len(hits)} dictionary entries exist only in the MOE dictionaries")
    else:
        warnings.append("MOE dictionaries not found: MOE-only entry check skipped")
    for w in warnings:
        print(f"  warning: {w}")
    for s in source_hits:
        print(f"  warning (source text, also in the git repository; reword it there): {s}")
    for pr in problems:
        print(f"  PROBLEM: {pr}")
    print("check: " + ("FAILED" if problems else "ok (no private strings found)"))
    return 1 if problems else 0


# ----------------------------------------------------------------- smoke test
def product_root(scratch: Path, out: Path, fork_data: Path, rime_dll: Path, name: str = "root") -> Path:
    """<scratch>/<name>: rime.dll + data/ = the fork's stock data with the
    package on top, i.e. DaoHui's shared data dir after installation."""
    root = scratch / name
    fresh_dir(root)
    shutil.copytree(fork_data, root / "data")
    shutil.copytree(out, root / "data", dirs_exist_ok=True)
    shutil.copyfile(rime_dll, root / "rime.dll")
    return root


def cached_gram(scratch: Path, src: Path) -> Path:
    """A private copy of the LM under scratch (hard-linked into user dirs);
    linking straight to rime/ would pin the live IME's mapped file."""
    cache = scratch / "_cache" / src.name
    st = src.stat()
    if not cache.exists() or cache.stat().st_size != st.st_size or cache.stat().st_mtime < st.st_mtime:
        cache.parent.mkdir(parents=True, exist_ok=True)
        print(f"copying {src.name} ({st.st_size / 1e6:.0f} MB) to {cache.parent}")
        shutil.copy2(src, cache)
    return cache


def user_dir(scratch: Path, name: str, gram: Path | None, extra: dict[str, str] | None = None) -> Path:
    user = scratch / name / "user"
    fresh_dir(user)
    if gram:
        try:
            os.link(gram, user / gram.name)
        except OSError:
            shutil.copy2(gram, user / gram.name)
    for rel, text in (extra or {}).items():
        write_lf(user / rel, text)
    return user


def open_product(root: Path, user: Path, log_name: str):
    """rime_harness.Rime on DaoHui's layout: shared = root/data, user = user."""
    import rime_harness as rh
    rh.weasel_root = lambda: root  # Rime() takes rime.dll and data/ from here
    rime = rh.Rime(user, log_name=log_name)
    if not rime.select_schema("asus_zhuyin"):
        die(f"schema asus_zhuyin not available; logs in {rime.log_dir}")
    return rime


SMOKE = [("su3cl3", "你好"), ("ji3g4", "我是"), ("5j4up ", "注音"), ("rup wu0 wu0 fu4cp3cl3", "今天天氣很好")]

# A user's own custom files on top of the product defaults (--smoke runs this
# in a second process): each must win where it says something, and must not
# drop the product defaults it does not mention.
USER_CUSTOM = {
    "default.custom.yaml": "patch:\n  menu/page_size: 7\n",
    "weasel.custom.yaml": "patch:\n  style/color_scheme: aqua\n",
    "asus_zhuyin.custom.yaml": "patch:\n  asus_lang/raw_keys: false\n",
}


def deployed(user: Path) -> dict[str, dict]:
    import yaml
    out = {}
    for name in ("default", "weasel", "asus_zhuyin.schema"):
        p = user / "build" / f"{name}.yaml"
        out[name] = yaml.safe_load(p.read_text(encoding="utf-8").replace("\t", " ")) if p.exists() else {}
    return out


def config_problems(cfg: dict[str, dict], custom: bool) -> list[str]:
    d, w, s = cfg["default"], cfg["weasel"], cfg["asus_zhuyin.schema"]
    want = [
        ("default.yaml schema_list = [asus_zhuyin]", d.get("schema_list") == [{"schema": "asus_zhuyin"}]),
        ("default.yaml Shift_L noop", d.get("ascii_composer", {}).get("switch_key", {}).get("Shift_L") == "noop"),
        ("default.yaml menu/page_size", d.get("menu", {}).get("page_size") == (7 if custom else 9)),
        ("weasel.yaml color_scheme", w.get("style", {}).get("color_scheme") == ("aqua" if custom else "asus_light")),
        ("weasel.yaml asus_dark preset", "asus_dark" in w.get("preset_color_schemes", {})),
        ("weasel.yaml app_options cmd.exe", w.get("app_options", {}).get("cmd.exe", {}).get("asus_en_direct") is True),
        ("weasel.yaml vertical list", w.get("style", {}).get("horizontal") is False),
        ("schema raw_keys", s.get("asus_lang", {}).get("raw_keys") is (not custom)),
    ]
    return [name for name, ok in want if not ok]


def smoke_custom(out: Path, scratch: Path, fork_data: Path, rime_dll: Path) -> int:
    root = scratch / "smoke-root"
    user = user_dir(scratch, "smoke-custom", None, USER_CUSTOM)
    rime = open_product(root, user, "daohui-smoke-custom")
    rime.api.deploy_config_file(b"weasel.yaml", b"config_version")
    rime.close()
    bad = config_problems(deployed(user), custom=True)
    print(f"  user custom files on top: {'ok' if not bad else 'WRONG ' + ', '.join(bad)}")
    return 1 if bad else 0


def smoke(out: Path, scratch: Path, fork_data: Path, rime_dll: Path, gram_src: Path | None) -> int:
    root = product_root(scratch, out, fork_data, rime_dll, "smoke-root")
    gram = cached_gram(scratch, gram_src) if gram_src else None
    user = user_dir(scratch, "smoke-" + ("gram" if gram else "nogram"), gram)
    t0 = time.perf_counter()
    rime = open_product(root, user, "daohui-smoke")
    rime.api.deploy_config_file(b"weasel.yaml", b"config_version")  # as DaoHuiDeployer does
    deploy_s = time.perf_counter() - t0
    built = sorted(p.name for p in (user / "build").glob("*")) if (user / "build").exists() else []
    missing = [f for f in ("asus_zhuyin.schema.yaml", "asus_zhuyin.prism.bin", "terra_pinyin.asus.table.bin",
                           "default.yaml", "weasel.yaml") if f not in built]
    bad_cfg = config_problems(deployed(user), custom=False)
    ok = not missing and not bad_cfg
    fails = 0
    for keys, want in SMOKE:
        rime.clear()
        rime.type(keys)
        preview = rime.snapshot().preview
        rime.key("Return")
        got = "".join(rime.committed)
        print(f"  {keys!r}: preview {preview!r}, Enter commits {got!r}" + ("" if got == want else f"  (want {want!r})"))
        fails += got != want
    rime.close()
    errors = [f.read_text(encoding="utf-8", errors="replace") for f in rime.log_dir.glob("*ERROR*")]
    err_lines = [l for e in errors for l in e.splitlines() if re.match(r"^[EF]\d{4,8} ", l)]
    for l in err_lines[:20]:
        print(f"  log: {l}")
    print(f"smoke ({'with' if gram else 'without'} {GRAM}): deploy+open {deploy_s:.1f} s, "
          f"build files {'ok' if not missing else 'MISSING ' + str(missing)}, "
          f"deployed config {'ok' if not bad_cfg else 'WRONG ' + ', '.join(bad_cfg)}, "
          f"{len(SMOKE) - fails}/{len(SMOKE)} typed right, "
          f"{len(err_lines)} error log lines (logs: {rime.log_dir})")
    proc = subprocess.run([sys.executable, __file__, "--smoke-custom", "--out", str(out), "--scratch", str(scratch),
                           "--fork-data", str(fork_data), "--rime-dll", str(rime_dll)], capture_output=True)
    text = proc.stdout.decode("utf-8", "replace")
    print("\n".join(l for l in text.splitlines() if l.startswith("  user custom")) or text)
    return 0 if ok and not fails and not proc.returncode else 1


# ----------------------------------------------------------------- main
def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--scratch", type=Path, default=DEFAULT_SCRATCH)
    ap.add_argument("--fork-data", type=Path, default=DEFAULT_FORK_DATA,
                    help="the fork's stock shared data (default.yaml, weasel.yaml, ...)")
    ap.add_argument("--rime-dll", type=Path, default=DEFAULT_RIME_DLL)
    ap.add_argument("--check", action="store_true", help="only scan the existing output")
    ap.add_argument("--smoke", action="store_true", help="only deploy and type with the existing output")
    ap.add_argument("--gram", type=Path, default=RIME / GRAM, help="LM used by --smoke")
    ap.add_argument("--no-gram", action="store_true", help="--smoke without the LM")
    ap.add_argument("--private-file", type=Path, default=None,
                    help="extra private strings for --check, one per line (default: "
                         "<scratch>/extra_private.txt if it exists; never commit it)")
    ap.add_argument("--smoke-custom", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    out = guard_outside_document(args.out, "--out")
    scratch = guard_outside_document(args.scratch, "--scratch")
    scratch.mkdir(parents=True, exist_ok=True)
    if args.smoke_custom:
        return smoke_custom(out, scratch, args.fork_data, args.rime_dll)
    if args.smoke:
        return smoke(out, scratch, args.fork_data, args.rime_dll, None if args.no_gram else args.gram)
    if not args.check:
        build(out, scratch, args.fork_data)
    return check(out, scratch, args.private_file or scratch / "extra_private.txt")


if __name__ == "__main__":
    sys.exit(main())
