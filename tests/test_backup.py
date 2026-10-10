"""Tests for tools/backup_learn.py in a sandbox folder (the live input method
and its data are not touched): copying, the manifest, restoring, and which
old backups are kept.

    python tests/test_backup.py
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import backup_learn as bl  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
TOOL = str(ROOT / "tools" / "backup_learn.py")


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def run(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, TOOL, "--root", str(root), "--no-service", *args],
                          capture_output=True, text=True, encoding="utf-8")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "rime" / "asus_learn.userdb"
        db.mkdir(parents=True)
        (db / "000005.ldb").write_bytes(b"table" * 100)
        (db / "000007.log").write_bytes(b"log v1")
        (db / "MANIFEST-000004").write_bytes(b"manifest")
        (db / "CURRENT").write_text("MANIFEST-000004\n")
        (db / "LOCK").write_bytes(b"")
        (root / "rime" / "emails.txt").write_text("a@b.co\n", encoding="utf-8")
        (root / "rime" / "asus_zhuyin.custom.yaml").write_text("patch: {}\n", encoding="utf-8")
        (root / "rime" / "asus_zhuyin.schema.yaml").write_text("not personal\n", encoding="utf-8")
        (root / "lexicon").mkdir()
        (root / "lexicon" / "personal_words.txt").write_text("詞\n", encoding="utf-8")
        dest = root / "backups" / "learn"

        p = run(root, "--no-verify")
        items = bl.backups(dest)
        check("a backup is made", p.returncode == 0 and len(items) == 1 and items[0][1].get("ok"),
              p.stdout + p.stderr)
        name = items[0][0] if items else ""
        b = dest / name
        check("the database is copied, without LOCK",
              (b / "rime/asus_learn.userdb/000007.log").read_bytes() == b"log v1"
              and (b / "rime/asus_learn.userdb/MANIFEST-000004").exists()
              and not (b / "rime/asus_learn.userdb/LOCK").exists())
        check("personal files are copied, the schema is not",
              (b / "rime/emails.txt").exists() and (b / "lexicon/personal_words.txt").exists()
              and (b / "rime/asus_zhuyin.custom.yaml").exists()
              and not (b / "rime/asus_zhuyin.schema.yaml").exists())
        check("the run is logged", "ok " + name in (dest / "backup.log").read_text(encoding="utf-8"))
        p = run(root, "--list")
        check("--list shows it", name in p.stdout and "ok" in p.stdout, p.stdout)

        # Damage, then restore.
        (db / "000007.log").write_bytes(b"damaged")
        (root / "rime" / "emails.txt").unlink()
        p = run(root, "--restore", "latest")
        check("--restore without --yes changes nothing",
              p.returncode == 0 and (db / "000007.log").read_bytes() == b"damaged", p.stdout)
        p = run(root, "--restore", "latest", "--yes")
        check("--restore --yes puts the data back",
              p.returncode == 0 and (db / "000007.log").read_bytes() == b"log v1"
              and (root / "rime" / "emails.txt").read_text(encoding="utf-8") == "a@b.co\n", p.stdout + p.stderr)
        aside = [d for d in dest.iterdir() if d.name.startswith("_before-restore-")]
        check("what was there before the restore is kept aside",
              len(aside) == 1 and (aside[0] / "rime/asus_learn.userdb/000007.log").read_bytes() == b"damaged")
        check("files that are not in the backup are left alone",
              (root / "rime" / "asus_zhuyin.schema.yaml").exists())

        # A backup that failed its check is never restored.
        bad = dest / "20990101-000000"
        bad.mkdir()
        (bad / "backup.json").write_text(json.dumps({"ok": False, "errors": ["x"], "files": {}}))
        p = run(root, "--restore", "latest")
        check("latest skips a backup that failed its check", name in p.stdout and "20990101" not in p.stdout, p.stdout)
        p = run(root, "--restore", "20990101-000000", "--yes")
        check("a failed backup cannot be restored by name", p.returncode == 1, p.stdout)

    # Which backups are kept: the newest of each of the last 14 days that
    # have one, and the newest of each of the last 12 months.
    ok, bad = {"ok": True}, {"ok": False}
    today = dt.date(2026, 10, 10)
    items = []
    for d in range(40):  # one a day for 40 days, two on the last day
        day = today - dt.timedelta(days=39 - d)
        items.append((f"{day:%Y%m%d}-120000", ok))
    items.append((f"{today:%Y%m%d}-180000", ok))
    items += [("20250115-120000", ok), ("20250120-120000", ok), ("20240101-120000", ok),
              ("20261009-090000", bad), ("20260901-090000", bad)]
    items.sort()
    keep = bl.to_keep(items, today)
    days = sorted({n[:8] for n in keep if n >= "20260927"})
    check("the last 14 days with a backup are kept", len(days) == 14 and "20260927" in days, f"{days}")
    check("only the newest of a day is kept",
          "20261010-180000" in keep and "20261010-120000" not in keep)
    check("older ones: the newest of each month", "20260926-120000" not in keep and "20260831-120000" not in keep
          and "20260930-120000" in keep and "20250120-120000" in keep and "20250115-120000" not in keep, f"{sorted(keep)}")
    check("months beyond the last 12 with a backup are dropped... but not when fewer exist",
          "20240101-120000" in keep, f"{sorted(keep)[:3]}")
    check("a failed backup is kept for a week only",
          "20261009-090000" in keep and "20260901-090000" not in keep)
    check("nothing is kept twice or invented", keep <= {n for n, _ in items})

    width = max(len(n) for n, _, _ in RESULTS)
    failed = 0
    for name, ok_, detail in RESULTS:
        failed += not ok_
        print(f"{'PASS' if ok_ else 'FAIL'}  {name:<{width}}  {'' if ok_ else detail}")
    print(f"{len(RESULTS) - failed}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
