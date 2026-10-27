"""Multi-device sync of the learned data (lua/asus_sync.lua, lua/asus_learn.lua).

Three staged devices, used one after another (each step in its own process:
librime initialises once per process), one sync folder under
D:\\.rime-harness\\sync-test (never a real cloud folder):
  A, B  installation.yaml with sync_dir (installation_id dev-a, dev-b)
  C     no sync_dir (dev-c)
The settings app's part is played by the test: it makes the device folders
and writes daohui_sync_request (a new first line + the other devices' ids).

    python tests/test_sync.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import rime_harness as rh  # noqa: E402
from eval_learn import Typist  # noqa: E402
from rime_harness import CONTROL_MASK, SHIFT_MASK, XK  # noqa: E402
from zhuyin import reading_keys  # noqa: E402

XK["Delete"] = 0xFFFF
XK["F20"] = 0xFFD1  # the dumper below
SYNC = Path(r"D:\.rime-harness\sync-test\test_sync")
DEVICES = {"a": "dev-a", "b": "dev-b", "c": "dev-c"}
REQUEST = "daohui_sync_request"
APP, TEACH = "請重新啟動應用程式", "我把程式碼上傳到伺服器了"
HALF_LIFE = 90 * 86400

# Test only: F20 writes all of asus_learn.userdb to <user>/dbdump.txt
# (control characters escaped, the real tab separates key and value).
DUMPER = r'''
local function esc(s)
  return (s:gsub("[%z\1-\31\\]", function(c)
    if c == "\t" then return "\\t" end
    return string.format("\\x%02x", c:byte())
  end))
end
local function func(key, env)
  if key.keycode ~= 0xffd1 or key:release() then return 2 end
  local db = require("asus_learn").db()
  local f = io.open(rime_api.get_user_data_dir() .. "/dbdump.txt", "wb")
  if db then
    local q = db:query("")
    for k, v in q:iter() do f:write(esc(k), "\t", esc(v), "\n") end
    q = nil
    collectgarbage()
  end
  f:close()
  return 1
end
return { func = func }
'''


def keys(text: str) -> str:
    return "".join(reading_keys(text)[0])


def user(dev: str) -> Path:
    return rh.HARNESS_DIR / f"sync-{dev}" / "user"


def snapshot_path(dev: str) -> Path:
    return SYNC / "daohui" / DEVICES[dev] / "asus_learn.txt"


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.exists() else ""


def lines_of(text: str) -> dict[str, str]:
    """Snapshot lines: key -> value (split at the last tab)."""
    out = {}
    for line in text.splitlines():
        if line and not line.startswith("#") and "\t" in line:
            k, v = line.rsplit("\t", 1)
            out[k] = v
    return out


def unescape(s: str) -> str:
    return re.sub(r"\\(t|x[0-9a-f]{2})", lambda m: "\t" if m.group(1) == "t" else chr(int(m.group(1)[1:], 16)), s)


def dump_rows(text: str) -> dict[str, str]:
    """The dumper's lines: key -> value, with the real control characters."""
    out = {}
    for line in text.splitlines():
        if "\t" in line:
            k, v = line.split("\t", 1)
            out[unescape(k)] = unescape(v)
    return out


def request(dev: str, n: int, *peers: str) -> None:
    (user(dev) / REQUEST).write_bytes(("\n".join([str(n), *peers]) + "\n").encode())


# --------------------------------------------------------------------- steps
class Step:
    """One device in one process; checks are printed as JSON for main()."""

    def __init__(self, dev: str):
        self.dev = dev
        self.r = rh.Rime(user(dev), log_name=f"sync-{dev}")
        if not self.r.select_schema("asus_zhuyin"):
            raise RuntimeError("schema asus_zhuyin not available")
        self.r.set_option("prediction", False)
        self.t = Typist(self.r, SHIFT_MASK, CONTROL_MASK)
        self.checks: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append((f"{self.dev}: {name}", bool(ok), detail))

    def top(self, text: str) -> tuple[str, str]:
        """(preview, comment of the first candidate) of text's keys."""
        self.t.reset_context()
        self.t.type(keys(text))
        s = self.r.snapshot()
        self.r.clear()
        return s.preview, (s.candidates[0][1] if s.candidates else "")

    def poke(self) -> None:
        """A key press: asus_learn looks at the sync request (check_seconds 0)."""
        self.r.clear()
        self.r.key("Escape")
        self.r.clear()

    def dump(self) -> dict[str, str]:
        self.r.clear()
        self.r.key("F20")
        return dump_rows(read(user(self.dev) / "dbdump.txt"))

    def finish(self, extra: dict | None = None) -> None:
        self.r.close()
        errors = [line for f in self.r.log_dir.glob("*.log*")
                  for line in f.read_text(encoding="utf-8", errors="replace").splitlines()
                  if line.startswith("E") and "lua" in line.lower()]
        self.check("no Lua errors in the librime log", not errors, errors[0] if errors else "")
        print("RESULT " + json.dumps({"checks": self.checks, **(extra or {})}, ensure_ascii=False))


def step_a1() -> None:
    s = Step("a")
    snap = read(snapshot_path("a"))
    s.check("IME start with sync on writes a (still empty) snapshot",
            snap.startswith("#daohui-asus-learn v1\tdev-a\t") and snap.endswith("#end\t0\n"), snap[:80])
    before, _ = s.top(APP)
    got, picks = s.t.enter(keys(TEACH), TEACH)
    s.check("teaching a correction", got == TEACH and picks > 0, f"{got} {picks}")
    after, comment = s.top(APP)
    s.check("the correction is applied here (學)", after == APP and comment.startswith("學"), f"{after} {comment}")
    s.check("learning alone does not export yet (every 30 minutes at most)", read(snapshot_path("a")) == snap)
    s.t.english("kubectl")
    request("a", 2, "dev-b")
    s.poke()
    snap = read(snapshot_path("a"))
    rows = lines_of(snap)
    n = sum(1 for line in snap.splitlines() if not line.startswith("#"))
    s.check("a sync request exports the snapshot",
            any(k.startswith("c\t") for k in rows) and snap.endswith(f"#end\t{n}\n"), snap[:200])
    s.check("...with English words", "e\tkubectl" in rows, str([k for k in rows if k.startswith("e\t")]))
    dump = s.dump()
    s.finish({"before": before, "snap": snap, "dump": dump})


def step_b1(before: str) -> None:
    s = Step("b")
    after, comment = s.top(APP)
    s.check("a correction learned on A applies on B after a sync (學)",
            after == APP and comment.startswith("學") and before != APP, f"{after} {comment} (before {before})")
    s.check("...and A's English word", s.t.preview("kubectl") == "kubectl", s.t.preview("kubectl"))
    s.finish({"snap": read(snapshot_path("b"))})


def step_a2() -> None:
    s = Step("a")
    snap = read(snapshot_path("a"))
    request("a", 3, "dev-b")
    s.poke()
    s.finish({"snap": snap, "snap2": read(snapshot_path("a")), "dump": s.dump()})


def step_b2() -> None:
    s = Step("b")
    snap = read(snapshot_path("b"))
    s.t.reset_context(); s.t.type(keys(APP))
    handled = s.r.key("Delete", SHIFT_MASK); s.r.clear()
    after, comment = s.top(APP)
    s.check("Shift+Delete on B forgets the rewrite", handled and not comment.startswith("學"), f"{after} {comment}")
    s.check("...not exported before a request or 30 minutes", read(snapshot_path("b")) == snap)
    request("b", 2, "dev-a")
    s.poke()
    s.finish({"snap": read(snapshot_path("b")), "forgotten": after})


def step_a3(forgotten: str) -> None:
    s = Step("a")
    after, comment = s.top(APP)
    s.check("forgetting on B wins on A after a sync", not comment.startswith("學"), f"{after} {comment}")
    s.check("...A shows what B shows", after == forgotten, f"A {after} / B {forgotten}")
    s.check("...and A still has the corrections B did not forget",
            s.top("我把程式碼寄給你")[0] == "我把程式碼寄給你", s.top("我把程式碼寄給你")[0])
    snap = read(snapshot_path("a"))
    # A third device's snapshot, written by hand: a stale copy of the
    # forgotten pair, entries older / newer than A's, an incomplete file.
    rows = lines_of(snap)
    now = int(time.time())
    w_old, w_new = [k for k in rows if k.startswith("w\t")][1:3]
    crafted = [
        ("c\tt/6g4\t城市", f"程式 5.000 {now - 3600}"),              # older than B's tombstone
        ("c\tzz9\t甲", f"乙 2.000 {now - HALF_LIFE}"),               # new key: 1.0 now
        (w_old, f"n 1.500 {now - 2 * HALF_LIFE}"),                   # 0.375 now: A's 1.0 stays
        (w_new, f"n 3.000 {now - 86400}"),                           # ~2.98 now: wins
        ("e\tzzword", f"Zzword 2.000 {now}"),
    ]
    incomplete = [("c\tyy9\t丙", f"丁 2.000 {now}")]
    for dev, what, tail in (("dev-x", crafted, f"#end\t{len(crafted)}\n"), ("dev-y", incomplete, "")):
        d = SYNC / "daohui" / dev
        d.mkdir(parents=True, exist_ok=True)
        body = "".join(f"{k}\t{v}\n" for k, v in what)
        (d / "asus_learn.txt").write_bytes(f"#daohui-asus-learn v1\t{dev}\t{now}\n{body}{tail}".encode())
    request("a", 4, "dev-b", "dev-x", "dev-y")
    s.poke()
    merged = read(snapshot_path("a"))
    # The same snapshot again under a new header: nothing may change.
    x = SYNC / "daohui" / "dev-x" / "asus_learn.txt"
    x.write_bytes(read(x).replace(f"\tdev-x\t{now}\n", f"\tdev-x\t{now + 1}\n", 1).encode())
    time.sleep(1.1)
    request("a", 5, "dev-b", "dev-x", "dev-y")
    s.poke()
    s.finish({"snap": snap, "merged": merged, "again": read(snapshot_path("a")),
              "w_old": w_old, "w_new": w_new, "crafted": crafted, "dump": s.dump()})


def step_c1() -> None:
    s = Step("c")
    s.t.enter(keys(TEACH), TEACH)
    s.t.reset_context(); s.t.type(keys(APP))
    s.r.key("Delete", SHIFT_MASK); s.r.clear()
    request("c", 2, "dev-a")
    s.poke()
    s.finish({"dump": s.dump()})


def step_rime_sync() -> None:
    ok, log_dir = rh.sync_user_data(user("a"), "sync-a-rime")
    print("RESULT " + json.dumps({"ok": ok}))


def step_a4() -> None:
    s = Step("a")
    s.finish({"snap": read(snapshot_path("a")), "dump": s.dump()})


STEPS = {"a1": step_a1, "b1": step_b1, "a2": step_a2, "b2": step_b2, "a3": step_a3, "c1": step_c1,
         "rime_sync": step_rime_sync, "a4": step_a4}


# --------------------------------------------------------------------- main
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def run(step: str, *args: str) -> dict:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    p = subprocess.run([sys.executable, __file__, "--step", step, *args], capture_output=True, env=env)
    out = p.stdout.decode("utf-8", "replace")
    line = next((l for l in reversed(out.splitlines()) if l.startswith("RESULT ")), None)
    if p.returncode or line is None:
        check(f"step {step} ran", False, (out + p.stderr.decode("utf-8", "replace"))[-2000:])
        return {"checks": []}
    res = json.loads(line[7:])
    RESULTS.extend(tuple(c) for c in res.get("checks", []))
    return res


def files(root: Path) -> dict[str, float]:
    return {str(p.relative_to(root)): p.stat().st_mtime for p in root.rglob("*") if p.is_file()} \
        if root.exists() else {}


def setup() -> None:
    assert str(SYNC).lower().startswith(r"d:\.rime-harness\sync-test"), SYNC
    if SYNC.exists():
        if os.lstat(SYNC).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise SystemExit(f"{SYNC} is a junction; not deleting it")
        shutil.rmtree(SYNC)
    custom = ("patch:\n  asus_sync/check_seconds: 0\n"
              "  engine/processors/@before 0: lua_processor@*asus_dbdump\n")
    for dev, did in DEVICES.items():
        inst = f'installation_id: "{did}"\n'
        if dev == "a":
            inst += f'sync_dir: "{SYNC.as_posix()}"\n'
        elif dev == "b":  # (a Windows path in YAML's escaped form)
            inst += 'sync_dir: "' + str(SYNC).replace("\\", "\\\\") + '"\n'
        rh.stage_user_dir(f"sync-{dev}", "project", extra={
            "installation.yaml": inst, "asus_zhuyin.custom.yaml": custom, "lua/asus_dbdump.lua": DUMPER})
        # (The settings app makes the device folders; also C's, so that a
        # wrong write would land somewhere.)
        (SYNC / "daohui" / did).mkdir(parents=True, exist_ok=True)
    request("a", 1, "dev-b")
    request("b", 1, "dev-a")
    request("c", 1, "dev-a")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if sys.argv[1:2] == ["--step"]:
        STEPS[sys.argv[2]](*sys.argv[3:])
        return 0
    setup()
    a1 = run("a1")
    b1 = run("b1", a1.get("before", ""))
    def entries(snap: str) -> dict[str, set[str]]:  # (the order inside a value may differ)
        return {k: set(v.split("|")) for k, v in lines_of(snap).items() if k[:2] in ("c\t", "x\t", "r\t")}
    a_c, b_c = entries(a1.get("snap", "")), entries(b1.get("snap", ""))
    check("B's snapshot has A's corrections, entries as A wrote them", a_c and a_c == b_c,
          str([(k, v, b_c.get(k)) for k, v in a_c.items() if b_c.get(k) != v][:3]))

    a2 = run("a2")
    check("round trip A -> B -> A: A's snapshot is not rewritten (nothing grew)",
          a2.get("snap") and a2.get("snap") == a1.get("snap"),
          f"{a1.get('snap', '')[:60]!r} -> {a2.get('snap', '')[:60]!r}")
    check("...nor by another request without changes", a2.get("snap2") == a2.get("snap"))
    a1d, a2d = a1.get("dump", {}), a2.get("dump", {})
    same = {k: v for k, v in a1d.items() if not k.startswith(("\x01", "\x02"))}
    check("...and A's stored values are byte-identical",
          same and all(a2d.get(k) == v for k, v in same.items()),
          str([(k, v, a2d.get(k)) for k, v in same.items() if a2d.get(k) != v][:3]))

    b2 = run("b2")
    b_rows = lines_of(b2.get("snap", ""))
    tombs = [k for k in b_rows if k.startswith("~\t")]
    check("B exports tombstones for what it forgot",
          "~\tc\tt/6g4\t城市" in b_rows and "~\tx\tt/6g4\t城市\t程式" in b_rows and "~\tr\tt/6g4\t程式" in b_rows,
          str(tombs))
    check("...and no longer the forgotten entry", "程式 " not in b_rows.get("c\tt/6g4\t城市", ""),
          b_rows.get("c\tt/6g4\t城市", ""))

    a3 = run("a3", b2.get("forgotten", ""))
    rows, merged, again = lines_of(a3.get("snap", "")), lines_of(a3.get("merged", "")), a3.get("again", "")
    check("A's snapshot after the sync: the forgotten pair is gone, the tombstones kept",
          "程式 " not in rows.get("c\tt/6g4\t城市", "") and "x\tt/6g4\t城市\t程式" not in rows
          and "r\tt/6g4\t程式" not in rows and "~\tc\tt/6g4\t城市" in rows,
          str({k: v for k, v in rows.items() if "t/6g4\t" in k}))
    crafted = dict(a3.get("crafted", []))
    city, new_key = "c\tt/6g4\t城市", "c\tzz9\t甲"
    check("a third device's stale copy (older than the tombstone) does not come back",
          "程式 " not in merged.get(city, ""), merged.get(city, ""))
    check("a new entry is taken as written", merged.get(new_key) == crafted.get(new_key),
          repr(merged.get(new_key)))
    w_old, w_new = a3.get("w_old", ""), a3.get("w_new", "")
    check("merge keeps the larger weight after decay: A's entry stays",
          merged.get(w_old) == rows.get(w_old) and merged.get(w_old) != crafted.get(w_old),
          f"{rows.get(w_old)} + {crafted.get(w_old)} -> {merged.get(w_old)}")
    check("...the other device's heavier entry wins", merged.get(w_new) == crafted.get(w_new),
          f"{rows.get(w_new)} + {crafted.get(w_new)} -> {merged.get(w_new)}")
    check("an incomplete snapshot (no #end line) is skipped", "c\tyy9\t丙" not in merged)
    check("importing the same snapshot again changes nothing (not rewritten)", again == a3.get("merged"),
          f"{len(again)} vs {len(a3.get('merged', ''))}")
    check("...English word imported", merged.get("e\tzzword") == crafted.get("e\tzzword"))

    sync_before, c_before = files(SYNC), set(files(user("c")))
    c1 = run("c1")
    c_new = {f for f in set(files(user("c"))) - c_before
             if not f.startswith(("build", "asus_learn.userdb", "terra_pinyin.userdb")) and f not in
             ("dbdump.txt", "user.yaml", "installation.yaml")}
    check("no sync_dir: nothing is written to the sync folder", files(SYNC) == sync_before,
          str(set(files(SYNC)) ^ set(sync_before)))
    check("...nor any sync file in the user folder", not c_new, str(c_new))
    cd = c1.get("dump", {})
    check("...no tombstones, nothing imported",
          cd and not any(k.startswith(("~\t", "\x02")) for k in cd) and "e\tkubectl" not in cd,
          str([k for k in cd if k.startswith(("~", "\x02"))][:5]))

    # librime's own sync (RimeSyncUserData: DaoHuiDeployer /sync, Squirrel
    # --sync) on asus_learn.userdb, twice: backup, then merge of the backup.
    before = a3.get("dump", {})
    for _ in range(2):
        r = run("rime_sync")
        check("librime's own sync runs", r.get("ok"), str(r))
    a4 = run("a4")
    rime_copy = read(SYNC / "dev-a" / "asus_learn.userdb.txt")
    check("librime's own sync backs up asus_learn.userdb (two-field keys only)",
          "\nw\t" in rime_copy and "\nc\t" not in rime_copy, rime_copy[:200])
    after = a4.get("dump", {})
    ours = {k: v for k, v in before.items() if not k.startswith("\x01")}
    junk = [k for k in after if k[1:3] == " \t" and after[k].startswith("c=")]
    check("...its merge adds keys of its own ('w \\t...' = 'c=0 d=0 t=1'), never read by asus_learn",
          junk and all(k not in before for k in junk), str(junk[:3]))
    check("...and leaves every value of asus_learn as it was",
          ours and all(after.get(k) == v for k, v in ours.items()),
          str([(k, v, after.get(k)) for k, v in ours.items() if after.get(k) != v][:3]))
    check("...A's snapshot is unchanged by it", a4.get("snap") == a3.get("again"))

    width = max(len(n) for n, _, _ in RESULTS)
    failed = 0
    for name, ok, detail in RESULTS:
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {'' if ok else detail}")
    print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
