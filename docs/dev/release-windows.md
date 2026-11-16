# 發佈 Windows 版（daohui-weasel）

照順序做，每一步確認過再做下一步。發佈、推送、上傳都是對外的動作：使用者要在**這一則訊息**裡說要發佈。版本號以下寫成 `0.17.4.<n>`。

## 準備

1. `gh auth switch -u TimLin-1231`，再 `gh auth status`：作用中（Active account: true）的必須是 TimLin-1231。不是就停下來。
2. `C:\Users\User\anaconda3\python.exe tools\doctor.py`：沒有 FAIL。
3. 三個 repo 都 `git fetch`，`git status` 乾淨；這個 repo 的 `main` 已經推上去（安裝檔裡的資料來自這裡，更新資訊也在這裡）。
4. 測試：`python tools\run_tests.py` 全部通過，記下 `.harness/test_runs/` 的結果檔名。
5. 決定 `<n>`：上一次發佈的號碼加 1（`gh release list --repo TimLin-1231/daohui-weasel` 看最新的；0.17.4.14 只有標籤沒有發佈，0.17.4.15、.16 是本機版本）。**一定要用 `DAOHUI_BUILD` 指定**：預設的「0.17.4 之後的 commit 數」已經比發佈過的號碼小。

## 建置

6. 資料：`python tools\package_daohui_data.py`（建置並自動 `--check`），再 `python tools\package_daohui_data.py --smoke`。輸出在 `D:\src\daohui\output\data_daohui`。兩個都要成功。
7. 設定程式有改過時：在 `app/daohui-settings/src-tauri` 用 `CARGO_TARGET_DIR=D:\.rime-harness\cargo-target\daohui-settings` 執行 `cargo tauri build --no-bundle`，把產生的 exe 複製成 `D:\src\daohui\output\DaoHuiSettings.exe`。
8. 安裝檔（Git Bash）：`DAOHUI_BUILD=<n> cmd //c "D:\src\daohui\build_daohui.bat" installer`。要用完整路徑。產出 `D:\src\daohui\output\archives\daohui-0.17.4.<n>-installer.exe`。
9. 確認版本：`DaoHuiServer.exe` 裡有 UTF-16 的 `0.17.4.<n>`。
10. 掃毒：用 Defender 的 `MpCmdRun.exe -Scan -ScanType 3 -File <安裝檔> -DisableRemediation`。0.17.4.14 曾被誤判成木馬；被判有毒就**不要發佈**，告訴使用者。
11. 算 SHA-256 和大小，**一個指令一個指令跑並看結束碼**（曾經有串起來的指令在 `sha256sum` 失敗後照樣發佈下去）。
12. 把安裝檔另外複製一份叫 `DaoHui-Setup.exe`（位元組完全相同）。README 的下載連結是 `releases/latest/download/DaoHui-Setup.exe`，少了它新使用者就下載不到。

## 發佈

13. daohui-weasel：提交改動，`git tag v0.17.4.<n>`，`git push origin daohui`，`git push origin v0.17.4.<n>`。
14. `gh release create v0.17.4.<n> --repo TimLin-1231/daohui-weasel <安裝檔> <DaoHui-Setup.exe> --title "島輝 0.17.4.<n>" --notes-file <說明檔>`。說明用繁體中文、寫給使用者看（改了什麼、要不要重開程式）。
15. 核對：`gh release view v0.17.4.<n> --repo TimLin-1231/daohui-weasel --json assets`，兩個檔案的大小和 digest 都等於第 11 步；`curl -sIL https://github.com/TimLin-1231/daohui-weasel/releases/latest/download/DaoHui-Setup.exe` 最後是 200、大小相同。
16. 更新資訊：在這個 repo 的 `update/appcast.xml` 最上面加一個 `<item>`，照抄上一項的格式：`sparkle:version`、下載網址、`length`、`daohui:sha256`、`sparkle:os="windows"`、`sparkle:installerArguments="/S"`（沒有它更新會跳出安裝畫面）、`pubDate`（用正確的時區）、繁體中文的 `<description>`。
17. 在 `main` 提交，`git fetch` 再 rebase，推送。
18. 確認線上的檔案：`curl -s https://raw.githubusercontent.com/TimLin-1231/rime-asus-zhuyin/main/update/appcast.xml` 最上面一項是新版本。

## 裝到這台電腦

19. 由使用者執行更新（會跳出 UAC）。已經開著的程式要重開才會載入新的前端 DLL。
20. 確認：解除安裝資訊的 `DisplayVersion` 是 `0.17.4.<n>`；`tools\doctor.py` 沒有 FAIL。
21. 使用者在真的程式裡試過這次改的東西後，把結果用［真實程式：程式、誰、日期］寫進 `STATE.md`，並更新 `OPEN.md`。

## 注意

- daohui-weasel 的 GitHub Actions 每次都是「已取消」，綠燈不代表任何事。
- `app/daohui-settings/UPDATES.md` 的「Making a release」用的安裝檔名稱是舊的，以這份為準。
