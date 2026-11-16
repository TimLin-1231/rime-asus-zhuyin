"""Check the published releases against the update feeds (read-only).

For the newest item of each feed in update/ (appcast.xml for Windows,
appcast-macos.xml and appcast-macos-app.xml for macOS), the enclosure's URL,
length and daohui:sha256 must equal the GitHub release asset of that name, as
'gh release view <tag> -R <owner/repo> --json assets' reports it (GitHub's
own sha256 digest of the uploaded file). The Windows release must also carry
DaoHui-Setup.exe (the stable download link of the README), identical to the
installer. The active gh account must be the project's (TimLin-1231): a
release published from another account lands in the wrong place.

Nothing is downloaded, uploaded or changed: only 'gh release view' and
'gh auth status' are run.

    python tools/check_release.py            # exit 1 on any mismatch
    python tools/check_release.py --json     # the findings as JSON as well
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPDATE = ROOT / "update"
FEEDS = [("Windows", "appcast.xml"), ("macOS (pkg)", "appcast-macos.xml"), ("macOS (app)", "appcast-macos-app.xml")]
WINDOWS_STABLE = "DaoHui-Setup.exe"
ACCOUNT = "TimLin-1231"
GH_DEFAULT = Path(r"C:\Program Files\GitHub CLI\gh.exe")
NS = {"sparkle": "http://www.andymatuschak.org/xml-namespaces/sparkle",
      "daohui": "https://github.com/TimLin-1231/rime-asus-zhuyin/ns/appcast"}
DOWNLOAD = re.compile(r"^https://github\.com/([^/]+/[^/]+)/releases/download/([^/]+)/([^/?#]+)$")


def gh_path() -> str:
    return shutil.which("gh") or str(GH_DEFAULT)


def gh(*args: str) -> tuple[int, str]:
    proc = subprocess.run([gh_path(), *args], capture_output=True, timeout=120)
    return proc.returncode, (proc.stdout or proc.stderr).decode("utf-8", "replace")


def version_key(v: str) -> tuple:
    return tuple(int(x) if x.isdigit() else x for x in re.split(r"[.\-]", v))


def newest_item(path: Path) -> dict:
    """The item with the highest sparkle:version, with its enclosure."""
    root = ET.parse(path).getroot()
    items = []
    for pos, item in enumerate(root.iter("item")):
        enc = item.find("enclosure")
        ver = item.findtext("sparkle:version", default="", namespaces=NS) or (
            enc.get(f"{{{NS['sparkle']}}}version", "") if enc is not None else "")
        items.append({"pos": pos, "title": item.findtext("title", default=""), "version": ver,
                      "url": enc.get("url", "") if enc is not None else "",
                      "length": enc.get("length", "") if enc is not None else "",
                      "sha256": (enc.get(f"{{{NS['daohui']}}}sha256", "") if enc is not None else "").lower(),
                      "enc_version": enc.get(f"{{{NS['sparkle']}}}version", "") if enc is not None else ""})
    if not items:
        raise ValueError(f"{path.name}: no <item>")
    return max(items, key=lambda it: version_key(it["version"]))


def release_assets(repo: str, tag: str, cache: dict) -> tuple[list[dict] | None, str]:
    key = (repo, tag)
    if key not in cache:
        rc, out = gh("release", "view", tag, "-R", repo, "--json", "assets")
        if rc:
            cache[key] = (None, out.strip().splitlines()[-1] if out.strip() else f"gh exit {rc}")
        else:
            cache[key] = (json.loads(out)["assets"], "")
    return cache[key]


def check_feed(label: str, name: str, cache: dict) -> tuple[list[str], list[str]]:
    """(lines to print, problems) for one feed."""
    lines, problems = [], []
    path = UPDATE / name
    if not path.exists():
        return lines, [f"{name}: file missing"]
    try:
        it = newest_item(path)
    except (ET.ParseError, ValueError) as e:
        return lines, [f"{name}: cannot read the feed: {e}"]
    lines.append(f"{label}: {name} newest item {it['version']} ({it['title']})")
    if it["pos"] != 0:
        problems.append(f"{name}: the newest version {it['version']} is not the first item")
    if it["enc_version"] and it["enc_version"] != it["version"]:
        problems.append(f"{name}: enclosure sparkle:version {it['enc_version']} != item version {it['version']}")
    if not re.fullmatch(r"[0-9a-f]{64}", it["sha256"]):
        problems.append(f"{name}: enclosure has no valid daohui:sha256")
    m = DOWNLOAD.match(it["url"])
    if not m:
        problems.append(f"{name}: enclosure URL is not a GitHub release download: {it['url']}")
        return lines, problems
    repo, tag, file = m.groups()
    if tag != f"v{it['version']}":
        problems.append(f"{name}: release tag {tag} does not match version {it['version']}")
    assets, err = release_assets(repo, tag, cache)
    if assets is None:
        problems.append(f"{name}: gh release view {tag} -R {repo} failed: {err}")
        return lines, problems
    by_name = {a["name"]: a for a in assets}
    asset = by_name.get(file)
    if asset is None:
        problems.append(f"{name}: release {repo} {tag} has no asset {file} (assets: {sorted(by_name)})")
        return lines, problems
    digest = (asset.get("digest") or "").lower().removeprefix("sha256:")
    checks = [("url", it["url"], asset.get("url", "")),
              ("length", it["length"], str(asset.get("size", ""))),
              ("sha256", it["sha256"], digest or "(GitHub reports no digest)")]
    for what, feed_v, gh_v in checks:
        ok = feed_v == gh_v
        lines.append(f"  {what:7} {'ok      ' if ok else 'MISMATCH'} feed {feed_v}" + ("" if ok else f" | release {gh_v}"))
        if not ok:
            problems.append(f"{name}: {what} of {file} differs from release {repo} {tag}")
    if asset.get("state") not in (None, "uploaded"):
        problems.append(f"{name}: asset {file} state is {asset.get('state')!r}, not uploaded")
    if label == "Windows":
        stable = by_name.get(WINDOWS_STABLE)
        if stable is None:
            problems.append(f"{name}: release {repo} {tag} has no {WINDOWS_STABLE}")
            lines.append(f"  {WINDOWS_STABLE} MISSING from the release")
        else:
            same = (stable.get("size") == asset.get("size")
                    and (stable.get("digest") or "").lower() == (asset.get("digest") or "").lower())
            lines.append(f"  {WINDOWS_STABLE} {'ok, same file as the installer' if same else 'DIFFERS from the installer'}")
            if not same:
                problems.append(f"{name}: {WINDOWS_STABLE} in {tag} is not the same file as {file}")
    return lines, problems


def active_account() -> tuple[str | None, str]:
    rc, out = gh("auth", "status", "--active", "--hostname", "github.com", "--json", "hosts")
    if rc:
        return None, out.strip()
    try:
        hosts = json.loads(out)["hosts"]["github.com"]
        return next(h["login"] for h in hosts if h.get("active")), ""
    except (KeyError, StopIteration, json.JSONDecodeError) as e:
        return None, f"cannot read gh auth status: {e}"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="also print the findings as one JSON object")
    args = ap.parse_args()
    cache: dict = {}
    problems: list[str] = []
    for label, name in FEEDS:
        lines, probs = check_feed(label, name, cache)
        print("\n".join(lines))
        problems += probs
    login, err = active_account()
    if login == ACCOUNT:
        print(f"gh active account: {login} ok")
    else:
        print(f"gh active account: {login or '?'} (want {ACCOUNT}; run: gh auth switch -u {ACCOUNT}) {err}".rstrip())
        problems.append(f"gh active account is {login or '?'}, not {ACCOUNT}")
    for p in problems:
        print(f"PROBLEM: {p}")
    print("check_release: " + (f"FAILED ({len(problems)} problem(s))" if problems else "ok"))
    if args.json:
        print(json.dumps({"problems": problems, "account": login}, ensure_ascii=False))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
