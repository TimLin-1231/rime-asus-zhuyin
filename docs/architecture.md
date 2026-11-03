# 島輝的架構

島輝由三個 repo 組成，在你的電腦上分成「輸入法程式」和「島輝注音方案」兩層。這一頁用圖說明各部分怎麼接在一起、一個按鍵經過哪些步驟、AI 修正怎麼運作，以及新版本怎麼送到使用者手上。

- [全貌](#全貌)
- [一個按鍵經過的步驟](#一個按鍵經過的步驟)
- [AI 修正（Ctrl+R）](#ai-修正ctrlr)
- [發佈與更新](#發佈與更新)
- [測試與評估](#測試與評估)
- [三個 repo](#三個-repo)
- [檔案放在哪裡](#檔案放在哪裡)

## 全貌

```mermaid
flowchart TB
    KEY["鍵盤"] --> FE["島輝輸入法程式（前端）<br/>Windows：小狼毫分支<br/>Mac：鼠鬚管分支"]
    FE --> APP["正在打字的程式<br/>記事本、瀏覽器…"]
    FE -->|"Ctrl+R"| AI["本機 AI 修正<br/>llama.cpp<br/>＋ Qwen3.5-4B"]
    FE <--> RIME["librime 引擎"]
    RIME <--> SCHEMA["島輝注音方案<br/>rime/ 的設定檔<br/>rime/lua/asus_*.lua"]
    SCHEMA --> DICT["詞庫<br/>terra_pinyin.asus"]
    SCHEMA --> GRAM["語言模型<br/>wanxiang-lts-zh-hant.gram"]
    SCHEMA <--> USERDB["學習資料<br/>*.userdb"]
    SET["島輝設定<br/>（Tauri 程式）"] -.->|"下載"| GRAM
    SET -.->|"下載"| AI
    SET -.->|"改設定<br/>重新部署"| RIME
```

- **前端**負責和作業系統打交道：收按鍵、畫行內預覽和直式選字清單、把字送進目前的程式、在背景自動更新。Windows 版和 Mac 版分別是小狼毫和鼠鬚管的改名分支。
- **librime** 是 Rime 的輸入法引擎，兩個系統共用。它依照方案設定，把按鍵變成候選字。
- **島輝注音方案**是這個 repo 的 `rime/`：大千注音鍵盤、詞庫、語言模型設定，以及 20 多個 Lua 模組（自動更正、中英混打、句中改字、自我學習…）。Windows 和 Mac 用的是同一份。
- **語言模型**讓整句選字更準，約 410 MB，所以不包在安裝檔裡，第一次使用時下載。
- 打的字、學到的詞都只在你的電腦上。只有下載、檢查更新，以及你自己設定的同步資料夾（Google Drive 等，用來在多台電腦之間同步學習資料，預設關閉）會用到網路。

## 一個按鍵經過的步驟

librime 把每個按鍵依序交給四個階段。下圖是整個流程，表格列出島輝在每個階段加上的 Lua 模組（完整順序在 `rime/asus_zhuyin.schema.yaml` 的 `engine:`）。

```mermaid
flowchart TB
    K["按鍵"] --> P["① 處理按鍵 processors<br/>連按 Shift、Ctrl+R<br/>英文模式、句中改字…<br/>speller：按鍵 → 注音"]
    P --> S["② 切段 segmentors<br/>哪一段是中文<br/>哪一段是英文或多按的鍵"]
    S --> T["③ 產生候選 translators<br/>詞庫＋語言模型整句選字<br/>英文字、按錯的鍵"]
    T --> F["④ 篩選與排序 filters<br/>按錯鍵更正、台灣用語<br/>的／得／地<br/>你改過的字優先<br/>已經顯示的字不亂跳"]
    F --> UI["前端畫面<br/>行內預覽、直式選字清單"]
    UI -->|"Enter 送出"| L["asus_learn<br/>記下你改過的字"]
    L -.->|"下次排前面"| F
```

| 階段 | 島輝的 Lua 模組（`rime/lua/`） | 做什麼 |
|---|---|---|
| ① processors | `asus_shift` | 連按兩下 Shift 切中英（排第一，才看得到每一個按鍵） |
| | `asus_fix` | Ctrl+R AI 修正 |
| | `asus_learn` | Shift+Del 忘記學錯的字；送出時學習 |
| | `asus_english`、`asus_email` | 英文模式的補完與拼字更正、email 提示 |
| | `asus_quick`、`asus_tab`、`asus_predict` | Tab：數字的各種寫法、中英互換、下一個詞 |
| | `asus_symbols` | 符號鍵盤、Ctrl+標點 |
| | `asus_edit` | ←／→ 游標，句中換字、刪字、插字 |
| | `asus_keys` | Ctrl+Space、空白鍵、Backspace、清單裡的按鍵 |
| ② segmentors | `asus_lang` | 分出中文、英文、多按的鍵 |
| ③ translators | `script_translator`（Rime 內建） | 詞庫 ＋ 語言模型，整句選字 |
| | `asus_typo` | 拼不成注音的按鍵（多按、少按） |
| | `asus_lang`、`asus_shortcuts`、`asus_symbols` | 英文字、自訂縮寫、符號 |
| | `asus_edit` | 游標前已經改好的字固定不動 |
| ④ filters | `asus_typo` | 按到旁邊的鍵（ㄈ→ㄌ），用整句的分數判斷 |
| | `asus_tw` | 台灣用語：宕機→當機、坐標→座標 |
| | `asus_grammar` | 的／得／地、在／再 |
| | `asus_learn` | 你改過的字詞排前面 |
| | `asus_lang`（numbers、digits、steady） | 數字和注音混打、已經顯示的字不亂跳 |
| | `asus_quick` | 打開清單時的日期、數字寫法 |
| | `asus_edit`、`asus_compact` | 游標後的字不變、精簡的行內預覽 |

按 Enter 送出時，`asus_learn` 會比對你送出的字和島輝原本猜的字，把改過的地方記進學習資料，下次在別的句子裡也會用你選的。

## AI 修正（Ctrl+R）

Lua 不能連網路，所以由前端代為詢問本機的模型。模型是 llama.cpp 的 llama-server 加上 Qwen3.5-4B，只在 `127.0.0.1` 上聽，文字不會離開這台電腦；第一次按 Ctrl+R 時啟動，閒置一陣子就自動關掉，把記憶體還回來。

```mermaid
sequenceDiagram
    actor U as 你
    participant FE as 前端
    participant L as asus_fix.lua
    participant M as 本機 llama.cpp

    U->>FE: 組字時按 Ctrl+R
    FE->>L: 按鍵
    L-->>FE: 要問的句子（asus_fix_request）<br/>泡泡顯示「AI 修正中…」
    FE->>M: 送出句子
    M-->>FE: 修正後的句子
    FE->>L: 答案（asus_fix_result）＋ F23 鍵
    L->>L: 檢查：修正後還是讀成你打的注音嗎？
    alt 有改、而且通過檢查
        L-->>FE: 修正結果當首選，原句排第二
    else 沒改或沒通過
        L-->>FE: 換個問法再問一次（容許聲調按錯）
    end
    U->>FE: Enter 送出，Esc 回到原句
```

## 發佈與更新

方案、Lua 和詞庫只在這個 repo 改，打包後分別送進 Windows 和 Mac 的程式。使用者的島輝在背景讀更新資訊（appcast），有新版就自己下載安裝。

```mermaid
flowchart TB
    SRC["rime/<br/>方案、Lua、詞庫"] --> PKG["tools/package_daohui_data.py<br/>打包公開資料"]
    PKG --> WDATA
    PKG -->|"同一份資料"| MDATA

    subgraph WIN["Windows：daohui-weasel"]
        direction TB
        WDATA["output/data_daohui"] --> NSIS["NSIS 安裝檔<br/>DaoHui-Setup.exe"] --> WREL["GitHub Release"]
    end

    subgraph SQ["Mac：daohui-squirrel"]
        direction TB
        MDATA["daohui-data/"] --> CI["GitHub Actions<br/>打 v* 標籤時編譯"] --> MREL["GitHub Release<br/>DaoHui.pkg、DaoHui.zip"]
    end

    WREL --> ACW["update/appcast.xml"]
    MREL --> ACM["update/appcast-macos-app.xml"]
    ACW -.-> UW["使用者的 Windows<br/>WinSparkle 自動更新"]
    ACM -.-> UM["使用者的 Mac<br/>Sparkle 自動更新"]
    ACM -.-> SH["install/mac.sh<br/>Mac 一行安裝"]
```

語言模型不經過上面的流程，單獨放在這個 repo 的 Release：

```mermaid
flowchart LR
    LMDG["amzxyz/RIME-LMDG<br/>萬象語言模型<br/>CC-BY-4.0"] -->|"原樣轉存"| MR["這個 repo 的 Release<br/>model-wanxiang-lts-20261006"]
    MR --> D1["島輝設定（Windows）"]
    MR --> D2["install/mac.sh（Mac）"]
    D1 & D2 --> V["比對 SHA-256<br/>放進使用者資料夾"]
```

- Windows 有新版時會跳出通知，按「安裝更新」就會安靜裝好，不會再問任何問題；Mac 在背景下載，等你沒有打字的時候換成新版。
- 語言模型放在我們自己的 Release，是因為上游在 2026-10-12 原地換掉了 LTS 檔案。島輝用的是調校和測試過的 2026-10-06 版，下載後一律比對 SHA-256。
- `install-mac.yml` 會在 GitHub 的 Mac 上實際跑 `install/mac.sh`：安裝、再跑一次、移除，確認每一步都對。

## 測試與評估

改動都先在 `.harness/` 的複本上用 Python 直接驅動 `rime.dll` 量測，不碰正在使用的設定。

```mermaid
flowchart LR
    SENT["tests/eval/sentences.txt<br/>評估用的句子"] --> MAKE["tools/make_eval_set.py<br/>轉成注音按鍵"]
    MAKE --> EVAL["tools/eval_ime.py<br/>tools/eval_learn.py"]
    H["tools/rime_harness.py<br/>驅動 rime.dll"] --> EVAL
    H --> TEST["tests/test_stage*.py<br/>行為測試"]
    EVAL --> REP["reports/eval_history.jsonl<br/>整句正確率、延遲…"]
    REAL["tools/tsf_test.py 等<br/>在真的記事本裡打字"] -.-> TEST
```

## 三個 repo

| repo | 內容 | 給誰 |
|---|---|---|
| [rime-asus-zhuyin](https://github.com/TimLin-1231/rime-asus-zhuyin)（這裡） | 島輝注音方案（`rime/`）、評估工具、島輝設定（`app/`）、寫作小幫手（`assist/`）、Mac 一行安裝、更新資訊 | 所有人：從這裡開始 |
| [daohui-weasel](https://github.com/TimLin-1231/daohui-weasel) | Windows 輸入法程式（小狼毫 0.17.4 的分支）、NSIS 安裝檔 | Windows 版的下載 |
| [daohui-squirrel](https://github.com/TimLin-1231/daohui-squirrel) | Mac 輸入法程式（鼠鬚管 1.1.2 的分支）、自動編譯 | Mac 版的下載 |

## 檔案放在哪裡

| | Windows | Mac |
|---|---|---|
| 輸入法程式 | `C:\Program Files\DaoHui` | `~/Library/Input Methods/DaoHui.app` |
| 學習資料、設定、語言模型 | `%APPDATA%\DaoHui` | `~/Library/DaoHui` |
| AI 修正的模型 | `%USERPROFILE%\.daohui\ai` | `~/Library/DaoHui/ai` |
| 記錄檔（log） | `%TEMP%\rime.daohui` | `$TMPDIR/rime.daohui` |

移除島輝時，學習資料、設定和語言模型會留下來，重新安裝時自動接著用。
