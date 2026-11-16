# 島輝（DaoHui）注音輸入法

## 開工前

- 一律用繁體中文回覆。程式註解用英文。
- 第一個動作：`C:\Users\User\anaconda3\python.exe tools\doctor.py`（唯讀的健康檢查）。有 FAIL 先告訴使用者。
- 現況在 `docs/dev/STATE.md`，未結事項在 `docs/dev/OPEN.md`，發佈步驟在 `docs/dev/release-windows.md`、`docs/dev/release-mac.md`，librime 的眉角在 `docs/dev/librime-gotchas.md`。不要去翻舊日記。

## 正在使用的輸入法

- `rime/` 是這台電腦上正在使用的輸入法資料夾。改這裡的檔案，使用者下次按「重新部署」就上線。
  不要執行部署程式、不要啟動或停止 DaoHuiServer／WeaselServer、不要還原備份，除非使用者在這一則訊息裡要求。
- 不要在輸入法清單裡選小狼毫：它的伺服器會和島輝搶同一份學習資料。
- 會佔用鍵盤的工具（tools/tsf_*.py、*_smoke_test.py、asus_compare.py）要先問。

## 指令

- Python 用 `C:\Users\User\anaconda3\python.exe`（PATH 上的 `python` 是別的版本）。
- 跑測試只用 `python tools/run_tests.py`（有逾時、會留下結果檔）。不要自己寫迴圈。
- 同一個資料夾一次只跑一個 librime 行程；任何驅動 librime 的指令（tests/test_*.py、tools/eval_*.py、rime_harness.py）都要有逾時，逾時要連子行程一起結束。
- 寫 Python 或設定檔用 Write/Edit，不要用 heredoc（反斜線會被吃掉）。不要用 `sed -i` 或 Python `write_text` 改追蹤中的檔案（會改掉 CRLF）。
- `.claude/settings.json` 的 hook 會擋下 heredoc 裡的反斜線和沒有逾時的 librime 指令，動到輸入法或鍵盤的指令會先問。被擋就改做法，不要繞過。
- 保留測試集（`tests/eval/*_test.txt`、`test_*`）只看總分，不看、不印失敗的句子。

## 檔案放哪裡

- `D:\Document` 由 Google Drive 備份：建置輸出、暫存檔、worktree、大檔案都放 `D:\.rime-harness\` 或 `D:\src\`。
- 這個 repo 是公開的：不要提交個人字詞、email、帳號名稱（公開的 GitHub 帳號 TimLin-1231 除外）。

## repo 與發佈

- 三個 repo：這裡（rime-asus-zhuyin，`main`）、`D:\src\daohui`（Windows，daohui-weasel，分支 `daohui`）、`D:\src\daohui-mac`（Mac，daohui-squirrel，分支 `daohui`）。
- 推送前先 `git fetch` 再 rebase：別的對話（包括雲端的）也會推。
- 發佈之前先 `gh auth switch -u TimLin-1231`，再照 `docs/dev/release-*.md` 一步一步做。發佈、推送、上傳都要使用者在這一則訊息裡同意。

## 怎樣算完成

- 「完成」要標明證據等級：[替身]、[CI]、或 [真實程式：哪個程式、誰試的、日期]。
  使用者在真實程式裡回報的問題，只有第三種才算解決。
- 說「測試通過」要附 `tools/run_tests.py` 寫在 `.harness/test_runs/` 的結果檔名。
- 新的或改過的檢查，要先證明它在該壞的時候會失敗。
- 第二個驗證者必須用不同種類的證據（真視窗、另一版 librime、問使用者），同一個測試替身多看幾次沒有用。

## 工作方式

- 一個對話做一件事。對話超過 15 萬 token 或換了主題：把狀態寫進 `docs/dev/STATE.md`（改寫，不是追加），請使用者開新對話。
- 子代理只交辦小而明確的任務。涉及品味的事（圖示、介面、文字）先問使用者再做。
- 未結事項寫進 `docs/dev/OPEN.md` 的表格，結案就刪掉那一列；不要寫進 STATE.md。
