# 島輝 sync: folder layout, formats, the `--sync` run

Several computers share one folder, normally Google Drive's "My Drive" /
`DaoHui-sync` (Windows mirror: `D:\GoogleDrive\DaoHui-sync`; macOS:
`~/Library/CloudStorage/GoogleDrive-<account>/<My Drive>/DaoHui-sync`). The
settings app's 「同步」 page chooses it; `DaoHuiSettings --sync` runs a sync
without a window. Code: `src-tauri/src/sync.rs` (run, commands),
`sync_merge.rs` (the merges, pure), `autosync.rs` (task / LaunchAgent),
`gdrive.rs` (finding "My Drive").

## Configuration (user dir)

`installation.yaml` (Rime's file, Rime's keys; edited line by line, everything
else is kept):

```yaml
installation_id: "2489a486-0e52-406d-93e2-548612575a43"   # kept; a new device gets e.g. "mac-tims-macbook-air"
sync_dir: "D:\\GoogleDrive\\DaoHui-sync"                   # double-quoted like Rime writes it
```

Other files in the user dir:

| File | |
| --- | --- |
| `terra_pinyin.synced.dict.yaml` | Personal words from the other devices (Rime dict, `name: terra_pinyin.synced`), imported by `terra_pinyin.asus.dict.yaml`. Generated. |
| `daohui_sync_request` | Written (unix time) at the start of each run; asus_learn.lua imports/exports its snapshot when it is newer than its last import. |
| `daohui_sync_state.json` | Per-key change times seen by the last run (settings, shortcuts, deletions). |
| `daohui_sync.lock` | Held during a run (stale after 30 min). |
| `log/sync.log` | The run's log (rotated to `sync.log.old` at 512 KB). |
| `shortcuts.txt.bak`, `asus_zhuyin.custom.yaml.bak` | The previous version when a sync changes them. |

## Sync folder

```
<sync_dir>/<installation_id>/            Rime's own sync (userdb snapshots, yaml/txt backups)
<sync_dir>/daohui/<installation_id>/     DaoHui's, written only by that device:
    asus_learn.txt   asus_learn.lua's snapshot (see the shared spec)
    personal.txt     word<TAB>pinyin<TAB>weight — own ∪ synced
    emails.txt       one address per line
    shortcuts.txt    the shortcuts file as is, then stamp lines (below)
    settings.txt     key<TAB>value<TAB>unix time
    device.txt       installation_id<TAB>OS<TAB>hostname<TAB>last sync<TAB>version
```

Files are written only when their content changes (temporary file + rename).
Nothing else is written there: no databases, no links.

`shortcuts.txt` ends with comment lines that asus_shortcuts.lua ignores:

```
# 島輝同步：每個縮寫最後修改的時間（由同步程式維護）
#daohui-sync	ㄉㄓ	1793122087
#daohui-sync	ㄆㄆ	1793125687	deleted
```

`settings.txt` keys: `asus_lang/raw_keys`, `prediction`
(= `switches/@last/reset`), `asus_tab/double_ms`, `asus_learn/enabled`,
`asus_english/compose`; values `true`/`false` or milliseconds.

## Merge rules

- Personal words: own = `terra_pinyin.personal.dict.yaml`; synced = union of
  the existing synced dict and every other device's `personal.txt`, larger
  weight per (word, pinyin), without own words. Removing a word does not
  propagate (v1).
- E-mail addresses: union (case-insensitive); new ones are appended to the
  local `emails.txt`.
- Shortcuts: per abbreviation (spaces ignored) the newest change wins,
  deletions included. A local change is stamped with the file's modification
  time when its value differs from what the last run saw.
- Settings: per key the newest change wins; a key that was never set
  (default) has time 0.

All merges are idempotent and do not depend on the order of devices
(unit tests in `sync_merge.rs`, an end-to-end test in `sync.rs`).

## A run (`DaoHuiSettings --sync`)

1. Read `installation.yaml`; create the sync folder if only its last part is
   missing; add an `installation_id` if there is none.
2. Write `daohui_sync_request`.
3. Rime's sync: `<install dir>\DaoHuiDeployer.exe /sync` (waits) /
   `DaoHui.app/Contents/MacOS/DaoHui --sync` (notifies the running IME).
4. Merge personal words, e-mail addresses, shortcuts, settings; export this
   device's files.
5. If a file in the user dir changed: redeploy (`/deploy` / `--reload`).
6. Write `device.txt`.

Exit codes: 0 done, 4 done with warnings (e.g. the deployer failed), 1
failed, 2 not configured (no user dir or no `sync_dir`), 3 another run in
progress. `DAOHUI_SKIP_DEPLOYER=1` skips steps 3 and 5; `DAOHUI_USER_DIR`
chooses the user dir.

## Automatic sync

Switched on the 「同步」 page.

- Windows: Task Scheduler task `\DaoHuiSync` for the current user (no
  administrator rights), created with `schtasks /Create /TN DaoHuiSync /XML
  <file> /F` from a UTF-16 task XML: at logon (2 min delay) and daily at
  12:30, only after 5 idle minutes (waits up to 12 h), runs
  `DaoHuiSettings.exe --sync`. Removed with `schtasks /Delete /TN DaoHuiSync /F`.
- macOS: `~/Library/LaunchAgents/io.github.timlin-1231.daohui.sync.plist`
  (RunAtLoad, StartInterval 86400), loaded with `launchctl bootstrap
  gui/<uid>`, removed with `launchctl bootout` and deleting the file. The app
  must not be run from a translocated location (move it to /Applications).

`DAOHUI_AUTOSYNC_DRY_RUN=<folder>`: turning it on or off only writes the XML /
plist and `commands.txt` into that folder.
