"""Health check of the input method on this PC (read-only, changes nothing).

Things that went wrong before without anybody noticing:
  - the server ran with background priorities inherited from the backup task
    (keys reached applications as plain letters under load);
  - a second Rime server (old 小狼毫) held the shared user folder for 9 days
    and every backup failed;
  - the backup log was the only place a failure showed up (a failed backup
    now also leaves backups/learn/LAST_BACKUP_FAILED.txt, reported as FAIL).

    python tools/doctor.py

FAIL = affects typing or the learned data now; WARN = housekeeping.
Exit code 1 if anything FAILs.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import procprio  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RIME = ROOT / "rime"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
GH_ACCOUNT = "TimLin-1231"
TASKS = ("RimeLearnBackup", "DaoHuiSync")
BACKUP_MARKER = "LAST_BACKUP_FAILED.txt"  # backup_learn.py MARKER
# Written at run time by librime or the settings app, not configuration.
RUNTIME_FILES = {"user.yaml", "installation.yaml", "daohui_sync_state.json", "daohui_sync_request"}

results: list[tuple[str, str, str, str]] = []  # (level, name, detail, fix)


def report(level: str, name: str, detail: str = "", fix: str = "") -> None:
    results.append((level, name, detail, fix))


def run(args: list[str], timeout: float = 30) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(args, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None


def text(b: bytes) -> str:
    for enc in ("utf-8", "mbcs"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    return b.decode("utf-8", "replace")


# -- checks -------------------------------------------------------------------
def check_server() -> None:
    daohui = procprio.pids("DaoHuiServer.exe")
    weasel = procprio.pids("WeaselServer.exe")
    if not daohui:
        report("FAIL", "島輝伺服器", "DaoHuiServer.exe 沒有在執行", "開始功能表 → 島輝輸入法 → 島輝算法服務")
    for pid in daohui:
        s = procprio.state(pid)
        detail = f"pid {pid}: CPU {s['cpu']}、I/O {s['io']}、記憶體 {s['page']}"
        if procprio.is_normal(s):
            report("OK", "島輝伺服器優先權", detail)
        else:
            report("FAIL", "島輝伺服器優先權", detail + "（背景優先權：電腦忙時按鍵會變成英數字）",
                   "開始功能表 → 島輝輸入法 → 島輝算法服務（以正常優先權重啟）")
        elev = procprio.elevated(pid)
        if elev:
            report("WARN", "島輝伺服器權限", f"pid {pid} 是系統管理員權限：由安裝或更新啟動（0.17.4.18 和更早的安裝檔都會這樣），"
                   "學習資料會由系統管理員程式寫入", "登出再登入，或等下一次備份重啟伺服器")
        elif elev is None:
            report("WARN", "島輝伺服器權限", f"pid {pid}：讀不到權限")
        else:
            report("OK", "島輝伺服器權限", f"pid {pid}：一般使用者")
    if weasel:
        report("FAIL", "小狼毫伺服器也在執行", f"WeaselServer.exe pid {weasel}：會鎖住共用的詞庫，備份會失敗",
               "結束 WeaselServer，並把小狼毫從輸入法清單移除")
    else:
        report("OK", "只有一個 Rime 伺服器", "小狼毫的 WeaselServer 沒有在執行")


def check_tasks() -> None:
    for name in TASKS:
        p = run(["schtasks", "/Query", "/TN", name, "/XML"])
        if not p or p.returncode != 0:
            report("WARN", f"排程 {name}", "找不到這個排程")
            continue
        xml = text(p.stdout)
        m = re.search(r"<Priority>(\d+)</Priority>", xml)
        prio = int(m.group(1)) if m else 7  # Task Scheduler's default
        if prio <= 6:
            report("OK", f"排程 {name} 優先權", str(prio))
        elif name == "RimeLearnBackup":  # it stops and restarts the server
            report("FAIL", f"排程 {name} 優先權", f"{prio}（背景）：它重啟的伺服器會繼承低優先權",
                   "python tools/backup_learn.py --install")
        else:  # the sync asks the running server; it would matter only if it started one
            report("WARN", f"排程 {name} 優先權", f"{prio}（背景）：它啟動的程式會繼承低優先權",
                   "改 app/daohui-settings/src-tauri/src/autosync.rs 的 <Priority> 並重新建立排程")


def check_backup() -> None:
    path = ROOT / "backups" / "learn" / "backup.log"
    if not path.exists():
        report("FAIL", "學習資料備份", "沒有 backups/learn/backup.log", "python tools/backup_learn.py --install")
        return
    lines = [ln for ln in path.read_text(encoding="utf-8-sig", errors="replace").splitlines() if ln.strip()]
    stamped = [ln for ln in lines if re.match(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d ", ln)]
    ok = [ln for ln in stamped if ln[20:].startswith("ok ")]
    last = stamped[-1] if stamped else ""
    if not ok:
        report("FAIL", "學習資料備份", "從來沒有成功過")
        return
    when = dt.datetime.strptime(ok[-1][:19], "%Y-%m-%d %H:%M:%S")
    age = (dt.datetime.now() - when).total_seconds() / 3600
    bad_after = [ln for ln in stamped[stamped.index(ok[-1]) + 1:] if ln[20:].startswith("BAD")]
    detail = f"最近一次成功 {ok[-1][:16]}（{age:.0f} 小時前）"
    if bad_after:
        report("FAIL", "學習資料備份", f"{detail}；之後失敗 {len(bad_after)} 次：{bad_after[-1][20:120]}",
               "看 backups/learn/backup.log；常見原因是小狼毫伺服器佔住詞庫")
    elif age > 26:
        report("FAIL", "學習資料備份", detail + "，超過一天沒有備份")
    else:
        report("OK", "學習資料備份", detail)
    if last and not last[20:].startswith(("ok", "BAD")):
        report("WARN", "備份紀錄最後一行", last[:120])


def check_backup_marker() -> None:
    """backups/learn/LAST_BACKUP_FAILED.txt: left by a failed backup
    (tools/backup_learn.py write_marker), removed by the next good one."""
    path = ROOT / "backups" / "learn" / BACKUP_MARKER
    if not path.exists():
        report("OK", "備份失敗標記", f"沒有 backups/learn/{BACKUP_MARKER}")
        return
    body = path.read_text(encoding="utf-8-sig", errors="replace")
    m = re.search(r"^failed_at=(\S+)", body, re.M)
    when = m.group(1) if m else f"{dt.datetime.fromtimestamp(path.stat().st_mtime):%Y-%m-%dT%H:%M:%S}（檔案時間）"
    head = []
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("怎麼辦") or len(head) == 6:  # the advice goes in the fix line
            break
        if line and not line.startswith("failed_at="):
            head.append(line)
    detail = "；".join(head).replace("：；", "：")  # "原因：" runs on into its first reason
    report("FAIL", "備份失敗標記", f"failed_at {when}：{detail[:300]}{' …' if len(detail) > 300 else ''}",
           f"看 backups/learn/{BACKUP_MARKER} 的「怎麼辦」；下一次備份成功後它會自動刪除")


def check_deploy() -> None:
    build = RIME / "build"
    if not build.is_dir():
        report("FAIL", "部署", "rime/build 不存在", "開始功能表 → 島輝輸入法 → 重新部署")
        return
    built = max((f.stat().st_mtime for f in build.rglob("*") if f.is_file()), default=0)
    sources = [f for pat in ("*.yaml", "lua/*.lua", "opencc/*", "*.txt")
               for f in RIME.glob(pat) if f.is_file() and f.name not in RUNTIME_FILES]
    newest = max(sources, key=lambda f: f.stat().st_mtime, default=None)
    if newest and newest.stat().st_mtime > built + 1:
        when = dt.datetime.fromtimestamp(newest.stat().st_mtime)
        report("FAIL", "部署", f"{newest.relative_to(ROOT).as_posix()} 改於 {when:%m-%d %H:%M}，晚於上次部署"
               f" {dt.datetime.fromtimestamp(built):%m-%d %H:%M}：正在用的不是目前的設定",
               "開始功能表 → 島輝輸入法 → 重新部署")
    else:
        report("OK", "部署", f"上次部署 {dt.datetime.fromtimestamp(built):%m-%d %H:%M}，之後設定沒有改過")


def check_slow_log() -> None:
    temps = [Path(os.environ.get("TEMP", "")), Path(os.environ.get("LOCALAPPDATA", "")) / "Temp"]
    path = next((t / "rime.daohui" / "DaoHuiServer.slow.log" for t in temps
                 if (t / "rime.daohui" / "DaoHuiServer.slow.log").exists()), None)
    if not path:
        report("WARN", "伺服器慢速紀錄", "找不到 DaoHuiServer.slow.log")
        return
    since = dt.datetime.now() - dt.timedelta(hours=24)
    slow = []
    pat = re.compile(r"^(\S+ \S+) (\w+) .* waited (\d+) ms for the lock, handled in (\d+) ms")
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = pat.match(line)
        if not m:
            continue
        try:
            t = dt.datetime.strptime(m.group(1)[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        total = int(m.group(3)) + int(m.group(4))
        if t >= since and m.group(2) == "key" and total >= 500:
            slow.append(total)
    if slow:
        report("WARN", "伺服器慢速紀錄", f"最近 24 小時有 {len(slow)} 個按鍵處理超過 0.5 秒（最慢 {max(slow)} ms）。"
               "超過 1 秒的按鍵會直接交給應用程式")
    else:
        report("OK", "伺服器慢速紀錄", "最近 24 小時沒有超過 0.5 秒的按鍵")


def check_repo() -> None:
    p = run(["git", "-C", str(ROOT), "worktree", "list", "--porcelain"])
    if p and p.returncode == 0:
        trees = [ln[9:] for ln in text(p.stdout).splitlines() if ln.startswith("worktree ")]
        if len(trees) > 1:
            report("WARN", "git worktree", f"除了主目錄還有 {len(trees) - 1} 個",
                   "分支已合併的用 git worktree remove 清掉（先 cmd /c rmdir 各自的 .harness 連結）")
        else:
            report("OK", "git worktree", "只有主目錄")
    p = run(["git", "-C", str(ROOT), "status", "--porcelain"])
    if p and p.returncode == 0:
        untracked = [ln[3:] for ln in text(p.stdout).splitlines() if ln.startswith("??")]
        if untracked:
            report("WARN", "未追蹤的檔案", f"{len(untracked)} 個：{', '.join(untracked[:4])}{' …' if len(untracked) > 4 else ''}")
        else:
            report("OK", "未追蹤的檔案", "沒有")


def check_environment() -> None:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            value = winreg.QueryValueEx(k, "WEASEL_TSF_LOG")[0]
        report("WARN", "除錯紀錄變數", f"WEASEL_TSF_LOG = {value}（舊的除錯版 weasel.dll 會一直寫紀錄）",
               "移除使用者環境變數 WEASEL_TSF_LOG")
    except OSError:
        report("OK", "除錯紀錄變數", "沒有設定")
    gh = shutil.which("gh") or r"C:\Program Files\GitHub CLI\gh.exe"
    p = run([gh, "auth", "status"]) if Path(gh).exists() or shutil.which("gh") else None
    if not p:
        report("WARN", "GitHub 帳號", "找不到 gh")
        return
    out = text(p.stdout + p.stderr)
    active = None
    account = None
    for line in out.splitlines():
        m = re.search(r"account (\S+)", line)
        if m:
            account = m.group(1)
        if "Active account: true" in line:
            active = account
    if active == GH_ACCOUNT:
        report("OK", "GitHub 帳號", active)
    else:
        report("WARN", "GitHub 帳號", f"目前是 {active}，發佈要用 {GH_ACCOUNT}", f"gh auth switch -u {GH_ACCOUNT}")


def main() -> int:
    if sys.stdout:
        sys.stdout.reconfigure(encoding="utf-8")
    for check in (check_server, check_tasks, check_backup, check_backup_marker, check_deploy, check_slow_log,
                  check_repo, check_environment):
        try:
            check()
        except Exception as e:  # one broken check must not hide the others
            report("WARN", check.__name__, f"檢查本身出錯：{e!r}")
    order = {"FAIL": 0, "WARN": 1, "OK": 2}
    for level, name, detail, fix in sorted(results, key=lambda r: order[r[0]]):
        print(f"{level:<4}  {name}：{detail}")
        if fix and level != "OK":
            print(f"      → {fix}")
    fails = sum(r[0] == "FAIL" for r in results)
    warns = sum(r[0] == "WARN" for r in results)
    print(f"\n{fails} 項失敗、{warns} 項提醒、{len(results) - fails - warns} 項正常")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
