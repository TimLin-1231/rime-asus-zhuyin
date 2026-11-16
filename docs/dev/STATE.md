# 現況（2026-11-16 改寫）

這份每次是改寫，不是追加；80 行以內。未結事項在 `OPEN.md`。

## 這台電腦上正在使用的

- 島輝 Windows 0.17.4.17，裝在 `C:\Program Files\DaoHui\daohui-0.17.4`；伺服器 `DaoHuiServer.exe` 開機自動啟動。
- 使用者目錄＝這個 repo 的 `rime/`（`HKCU\Software\DaoHui` 的 `RimeUserDir`）。改 `rime/` 等於改正在用的輸入法，下次重新部署就上線。
- 舊小狼毫 0.17.4.0 還裝著、還在輸入法清單裡，`RimeUserDir` 也指向 `rime/`（自動啟動已拿掉）。它的伺服器一啟動就會鎖住詞庫。
- 排程：`RimeLearnBackup`（00:30 起每 2 小時，閒置時備份，會停伺服器 1–3 秒；優先權 4）、`DaoHuiSync`（登入後與每天 12:30；優先權 7）。
- 同步：`installation.yaml` 的 `sync_dir` 指向 Google Drive 的鏡像資料夾；只有這一台裝置。
- AI 修正（Ctrl+R）的模型在 `%USERPROFILE%\.daohui\ai`。
- 2026-11-16 正在跑的伺服器是 00:30 被備份重啟的，還是背景優先權；A1、A2 的修正從下一次 00:30 起生效。立刻恢復：開始功能表「島輝輸入法 → 島輝算法服務」。

## 三個 repo

| repo | 本機 | 分支 | 最新發佈 |
|---|---|---|---|
| rime-asus-zhuyin（方案、Lua、工具、設定程式、更新資訊） | `D:\Document\輸入法` | `main` | 只有語言模型 `model-wanxiang-lts-20261006` |
| daohui-weasel（Windows 前端、安裝檔） | `D:\src\daohui` | `daohui` | v0.17.4.17（2026-11-02） |
| daohui-squirrel（Mac 前端） | `D:\src\daohui-mac` | `daohui` | v1.1.2.24（2026-10-31）；之後還有 4 個 commit 沒發佈 |

- 三個都是公開的。daohui-squirrel 的 `upstream`（rime/squirrel）禁止推送。
- `D:\src\weasel` 是舊的小狼毫修補原始碼，不再發佈。
- `main` 在 2026-11-16 有本機 commit 還沒推（流程檢討的修正）。

## 版本與資料

- Windows：librime 1.13.1（和小狼毫 0.17.4 同一個 `rime.dll`）。版本號第 4 位要用 `DAOHUI_BUILD` 指定（commit 數已經比發佈過的小）。
- Mac：鼠鬚管 1.1.2，librime 1.16；版本號＝`876adeb` 之後的 commit 數。
- 更新資訊在 `update/`：`appcast.xml`（Windows 0.17.4.17）、`appcast-macos-app.xml` 與 `appcast-macos.xml`（Mac 1.1.2.24）。
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
- 最新的學習資料備份完好（獨立的 LevelDB 讀取器逐塊驗證，2026-11-15）。

## 已知壞掉的（細節與負責人在 OPEN.md）

- 先顯示按鍵模式（使用者正在用）：多按兩個鍵再 Backspace 兩次，整句變回英數字。`rime/lua/asus_compact.lua` 已修好並有測試［替身］，要使用者重新部署後才生效（A8）。
- 高負載時按鍵變成英數字、沒有選字框：伺服器背景優先權＋前端逾時就放行。A1、A2 已改，從下一次 00:30 備份起生效；A3、A5 還沒做。
- 備份（A6 已改［替身］，從下一次 00:30 起生效）：失敗時留下 `backups/learn/LAST_BACKUP_FAILED.txt`，當天不再停伺服器；下一次成功的備份會順便刪掉 80 多個空殼資料夾。
- daohui-weasel 的 CI 每次都是「已取消」，不算檢查。
- Mac：組字中 ⌘V 不經過 Rime。

## 規則

- 「完成」標明證據等級：［替身］、［CI］、［真實程式：程式、誰、日期］。
- 評估的淨變化少於 6 句就寫「量不出差異」，不拿來當決定的理由。
