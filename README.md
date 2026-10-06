# 華碩風智慧注音（Rime / 小狼毫）

在小狼毫 0.17.4（librime 1.13.1）上重現並強化華碩智慧輸入法的體驗。
所有設定都在 `rime/`。小狼毫的使用者目錄（登錄檔 `HKCU\Software\Rime\Weasel\RimeUserDir`）直接指向這個資料夾。

## 目錄

| 路徑 | 內容 |
|---|---|
| `rime/` | 小狼毫使用者目錄：方案、Lua、外觀設定 |
| `rime/lua/asus_keys.lua` | 連按兩下 Shift／Ctrl+Space 切換中英、Shift 防誤觸、展開選單後的按鍵 |
| `rime/lua/asus_compact.lua` | 精簡候選泡泡（只顯示首選，以及尚未打聲調的注音） |
| `tools/rime_harness.py` | 用 Python 驅動 `rime.dll`，在 `.harness/` 的複本上測試，不會碰到正在使用的設定 |
| `tools/eval_ime.py` | 量化評估（整句正確率、字正確率、個人詞 Top1／Top9、每鍵延遲） |
| `tools/make_eval_set.py` | 從 `tests/eval/sentences.txt` 產生評估用的按鍵序列（採台灣教育部讀音） |
| `tools/fetch_models.py` | 下載語言模型 `.gram`（不進 git） |
| `tests/test_stage1.py` | 階段 1 的行為測試 |
| `reports/eval_history.jsonl` | 歷次評估結果 |
| `backups/` | 改動前的完整備份（不進 git） |

## 功能速查
| 操作 | 效果 |
|---|---|
| 打注音 | 首選直接出現在行內；小框只顯示首選，還沒打聲調時顯示注音 |
| ↓ | 展開候選（1–9 選字、空白鍵選反白、Esc 收合） |
| 連按兩下 Shift／Ctrl+Space | 切換中英（會先送出已打好的中文） |
| `` ` `` | 符號小鍵盤：按對應的鍵輸出符號；Tab 切換一般／數學／特殊 |
| Ctrl+, . ' ; | ， 。 、 ； |
| Ctrl+Shift+; / 1 [ ] 9 0 | ： ？ ！ 『 』 （ ） |
| ( ) | 半形括號（全形用 Ctrl+Shift+9/0） |
| 注音縮寫，例如 ㄉㄓ | 快捷文字本的展開文字（`rime/shortcuts.txt`） |

## 詞庫
- **個人詞彙**：`lexicon/personal_words.txt`（不進 git），一行一個詞：`詞<Tab>注音<Tab>來源`，注音可以留空，會自動查。
  第一次使用時由 `tools/import_personal.py` 從華碩、新注音、舊小狼毫的匯出檔合併產生。
- **台灣補充詞庫**：從教育部國語辭典產生。補上台灣讀音（晚上 ㄕㄤˋ）、台灣寫法（螢幕勝過熒幕），以及缺漏的詞。
- 修改詞庫後執行 `python tools/build_lexicon.py`，再重新部署。
- **快捷文字本**：`rime/shortcuts.txt`（不進 git，範例在 `shortcuts.example.txt`）。改完要重新部署。
- **符號鍵盤配置**：`rime/asus_zhuyin.schema.yaml` 的 `asus_symbols`。

## 部署
1. 修改 `rime/` 裡的檔案。
2. 系統匣的小狼毫圖示按右鍵 →「**重新部署**」。
3. 如果是改了 `RimeUserDir`，或是第一次在新電腦上使用：先跑 `python tools/fetch_models.py` 和 `python tools/build_lexicon.py`，再重新啟動小狼毫服務（重開機最簡單），然後重新部署。

## 測試
```
C:\Users\User\anaconda3\python.exe tests\test_stage1.py
C:\Users\User\anaconda3\python.exe tests\test_stage2.py
C:\Users\User\anaconda3\python.exe tools\eval_ime.py --note "說明這次改了什麼"
```
評估表格的括號裡是**同一版評估集**上一次的數字，可以直接前後對照。

## 回到上一版
- **設定回到某個 commit**：`git log` 找到版本 → `git checkout <commit> -- rime/` → 重新部署。
- **完全回到改動前的小狼毫**：
  1. 匯入 `backups\20261006-003720-HKCU-Rime.reg`，把使用者目錄改回 `D:\Document\小狼豪輸入法`。
  2. 重新啟動小狼毫服務。
  3. 重新部署。
- **改回華碩輸入法**：在 Windows 的語言設定切換輸入法就好，這個專案不會改到華碩的任何東西。

## 隱私
- 學習資料（`*.userdb`）、建置檔、同步目錄、個人詞彙匯出檔都列在 `.gitignore`，而且只存在本機。
- 沒有設定任何雲端同步目錄。舊設定裡的 Google Drive 同步路徑沒有沿用。
