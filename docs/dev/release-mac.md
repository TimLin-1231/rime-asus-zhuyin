# 發佈 Mac 版（daohui-squirrel）

照順序做，每一步確認過再做下一步。推送標籤就會在 GitHub 上發佈：使用者要在**這一則訊息**裡說要發佈。版本號以下寫成 `1.1.2.<n>`。

## 準備

1. `gh auth switch -u TimLin-1231`，再 `gh auth status`：作用中（Active account: true）的必須是 TimLin-1231。不是就停下來。
2. 三個 repo 都 `git fetch`，`git status` 乾淨；這個 repo 的 `main` 已經推上去。
3. 測試：`python tools\run_tests.py` 全部通過，記下 `.harness/test_runs/` 的結果檔名。

## 資料與版本

4. 資料：`python tools\package_daohui_data.py`，再 `python tools\package_daohui_data.py --smoke`；輸出在 `D:\src\daohui\output\data_daohui`。
5. 把輸出複製進 `D:\src\daohui-mac\daohui-data/`，照 `daohui-data/README.md` 的做法（文字檔用 LF；不要在那裡手改）。
6. 這次要發佈的改動全部提交成 commit。版本號＝`876adeb` 之後的 commit 數：`git -C D:\src\daohui-mac rev-list --count 876adeb..HEAD` 就是 `<n>`。**標籤之前不要再多一個 commit**，不然號碼會變（1.1.2.17 就是這樣跳過的）。

## 發佈

7. 只推到 `origin`，絕對不推 `upstream`（rime/squirrel，推送已停用）：`git -C D:\src\daohui-mac push origin daohui`，`git tag v1.1.2.<n>`，`git push origin v1.1.2.<n>`。
8. 等 CI：`gh run list --repo TimLin-1231/daohui-squirrel` 找到這個標籤的那次，`gh run watch <id> --repo TimLin-1231/daohui-squirrel`。必須是 success：它會編譯、檢查簽章與內容（`check_daohui_bundle`）、用新的 librime 實際部署並打字，再建立 Release。
9. 核對 Release：有 `DaoHui-1.1.2.<n>.pkg`、`DaoHui-1.1.2.<n>.zip`、`appcast-macos.xml`、`appcast-macos-app.xml`、`SHA256SUMS.txt`。沒有簽章時 Release 會標成「搶先版」，這是正常的。
10. 島輝設定（`DaoHuiSettings-macOS-universal.dmg`）有改過時，從設定程式 CI（`.github/workflows/settings-app.yml`）的產出下載，`gh release upload v1.1.2.<n> <dmg> --repo TimLin-1231/daohui-squirrel`。

## 更新資訊

11. 下載 Release 的兩個 appcast，各自把 `<description>` 換成繁體中文、寫給使用者看的說明；`pubDate` 用正確的時區。EdDSA 簽章簽的是下載的檔案，不是 XML，所以改說明不影響簽章。
12. 核對兩個 appcast 的 `length` 和 `daohui:sha256` 等於下載下來的 zip／pkg。
13. 複製到這個 repo：`update/appcast-macos-app.xml`（zip，1.1.2.21 以後的 App 讀這個，也是 `install/mac.sh` 用的）和 `update/appcast-macos.xml`（pkg，1.1.2.20 以前的版本和島輝設定讀這個）。兩個都要更新。
14. 在 `main` 提交，`git fetch` 再 rebase，推送。
15. 確認線上：`curl -s https://raw.githubusercontent.com/TimLin-1231/rime-asus-zhuyin/main/update/appcast-macos-app.xml` 最上面一項是新版本；`gh run list --repo TimLin-1231/rime-asus-zhuyin --workflow install-mac.yml` 最新一次是 success（它在 GitHub 的 Mac 上實際跑一行安裝）。

## 在真的 Mac 上

16. 請使用者在 Mac 上用島輝選單的「檢查新版本…」更新，或重跑 README 的安裝指令。
17. 確認輸入法清單裡的島輝能打字（換了 App 之後偶爾會變成灰色：登出再登入就好，並把這件事記下來）。
18. 結果用［真實程式：程式、誰、日期］寫進 `STATE.md`，並更新 `OPEN.md`。
