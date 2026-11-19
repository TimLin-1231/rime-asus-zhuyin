# 現況（2026-11-18 改寫）

這份每次是改寫，不是追加；80 行以內。未結事項在 `OPEN.md`。

## 這台電腦上正在使用的

- 島輝 Windows 0.17.4.18，裝在 `C:\Program Files\DaoHui\daohui-0.17.4`；伺服器 `DaoHuiServer.exe` 開機自動啟動。
- 使用者目錄＝這個 repo 的 `rime/`（`HKCU\Software\DaoHui` 的 `RimeUserDir`）。改 `rime/` 等於改正在用的輸入法，下次重新部署就上線。
- 舊小狼毫 0.17.4.0 還裝著、還在輸入法清單裡，`RimeUserDir` 也指向 `rime/`（自動啟動已拿掉）。它的伺服器一啟動就會鎖住詞庫。使用者 11-18 同意移除，等使用者解除安裝。
- 排程：`RimeLearnBackup`（00:30 起每 2 小時，閒置時備份，會停伺服器 1–3 秒；優先權 4）、`DaoHuiSync`（登入後與每天 12:30；優先權 4，11-18 重新登記）。
- 0.17.4.18（前端紀錄被放行的按鍵、伺服器自己設定正常優先權；daohui-weasel 4809790）2026-11-19 發佈並裝在這台電腦（DisplayVersion 0.17.4.18，安裝時已重新部署，doctor 0 FAIL）。安裝檔啟動的伺服器是系統管理員權限（見 OPEN.md），下一次登入或備份重啟後才是一般權限。
- 同步：`installation.yaml` 的 `sync_dir` 指向 Google Drive 的鏡像資料夾；只有這一台裝置。
- AI 修正（Ctrl+R）的模型在 `%USERPROFILE%\.daohui\ai`。
- 伺服器由備份排程重啟後仍是正常優先權：2026-11-17 00:30 的備份停機 0.6 秒、重啟後 CPU、I/O、記憶體都正常（`tools/doctor.py`）。同一次備份把 `backups/learn` 從 139 個資料夾清到 44 個（剩下 2 個是 10-25 還原時留下的 `_before-restore-*`，刻意保留）。

## 三個 repo

| repo | 本機 | 分支 | 最新發佈 |
|---|---|---|---|
| rime-asus-zhuyin（方案、Lua、工具、設定程式、更新資訊） | `D:\Document\輸入法` | `main` | 只有語言模型 `model-wanxiang-lts-20261006` |
| daohui-weasel（Windows 前端、安裝檔） | `D:\src\daohui` | `daohui` | v0.17.4.18（2026-11-19，資料來自 `main` ec4ef06） |
| daohui-squirrel（Mac 前端） | `D:\src\daohui-mac` | `daohui` | v1.1.2.29（2026-11-19，資料來自 `main` ec4ef06） |

- 三個都是公開的。daohui-squirrel 的 `upstream`（rime/squirrel）禁止推送。
- `D:\src\weasel` 是舊的小狼毫修補原始碼，不再發佈。
- 11-18 清掉 10 個已合併的 worktree（約 3.2 GB）和 Antigravity 留下的檔案（壓縮在 `D:\.rime-harness\archive\antigravity-leftovers-20261118.zip`）。`git` 刪不掉 `.git\worktrees` 底下的資料夾（Permission denied），PowerShell 的 `Remove-Item` 可以：先拆掉 worktree 裡的 `.harness` 目錄連結再刪。

## 版本與資料

- Windows：librime 1.13.1（和小狼毫 0.17.4 同一個 `rime.dll`）。版本號第 4 位要用 `DAOHUI_BUILD` 指定（commit 數已經比發佈過的小）。
- Mac：鼠鬚管 1.1.2，librime 1.16；版本號＝`876adeb` 之後的 commit 數。
- 更新資訊在 `update/`：`appcast.xml`（Windows 0.17.4.18）、`appcast-macos-app.xml` 與 `appcast-macos.xml`（Mac 1.1.2.29）。
- 語言模型：萬象 LTS 2026-10-06 版，410,679,340 bytes，從我們自己的 Release 下載並比對 SHA-256。

## 開發環境

- Python：`C:\Users\User\anaconda3\python.exe`（3.11）。另有 VS2022 Build Tools、Rust、NSIS、`gh`。
- 發佈前先 `gh auth switch -u TimLin-1231`，`tools/check_release.py` 會確認帳號。
- 測試替身在 `.harness/`（連到 `D:\.rime-harness` 的目錄連結）。大檔案、建置輸出放 `D:\.rime-harness\` 或 `D:\src\`。
- 從 Claude 桌面版啟動的程式寫 `AppData` 會被重新導向到 MSIX 的私有資料夾；測試檔放 `%USERPROFILE%` 底下。

## 驗證到哪一層

- 測試：用 `tools/run_tests.py` 跑，結果檔在 `.harness/test_runs/`；最新一次的檔名寫在提交訊息裡［替身］。
- 評估：44 組的數字和 10 月 31 日的紀錄完全相同［替身，2026-11-15］。
- 組字中 Ctrl+V 貼上、ㄔㄥˊㄕˋ＝程式、清單最後一列的注音［真實程式：使用者，2026-10-31／11-02］。
- 先顯示按鍵模式：`su3xx`、Backspace 兩次、Enter 送出「你」［真實程式：記事本，使用者，2026-11-16］。
- 2026-11-16 20:18 重新部署；重啟後的伺服器 CPU、I/O、記憶體都是正常優先權（`tools/doctor.py`）。
- 最新的學習資料備份完好（獨立的 LevelDB 讀取器逐塊驗證，2026-11-15）。

## 已知壞掉的（細節與負責人在 OPEN.md）

- 先顯示按鍵模式（使用者正在用）：小框顯示舊注音、email 提示不見，已修好並部署，還沒在真的程式裡看過；11-18 改成縮寫也顯示並送出展開的內容，11-19 安裝 0.17.4.18 時已部署（見 OPEN.md）。
- 高負載時按鍵變成英數字、沒有選字框：伺服器背景優先權＋前端逾時就放行。現在的伺服器是正常優先權。11-18 在 Claude 桌面版又發生一次，但伺服器正常、CPU 4%、伺服器紀錄每鍵 0.5 秒內處理完，原因不明；使用者選維持放行（乙），4809790 會把放行的原因記下來，要裝上才有紀錄。
- 備份（A6 已改［替身］，從下一次 00:30 起生效）：失敗時留下 `backups/learn/LAST_BACKUP_FAILED.txt`，當天不再停伺服器；下一次成功的備份會順便刪掉 80 多個空殼資料夾。
- daohui-weasel 的 CI 每次都是「已取消」，不算檢查。
- Mac：組字中 ⌘V 不經過 Rime。

## 規則

- 「完成」標明證據等級：［替身］、［CI］、［真實程式：程式、誰、日期］。
- 評估的淨變化少於 6 句就寫「量不出差異」，不拿來當決定的理由。
