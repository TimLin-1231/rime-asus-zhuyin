# librime、librime-lua 和前端的眉角

做這個專案時踩過、而且之後還會用到的事實。每一條都是當時實際量到或試出來的；librime 換版本後要重新確認。版本：Windows 是 librime 1.13.1（小狼毫 0.17.4 內附），Mac 是 librime 1.16（鼠鬚管 1.1.2）。

## 方案與設定

- 在 custom 檔裡用一個 map 蓋掉原廠的純量值，整個 include 都會壞掉：改用 `__patch`。
- custom patch 裡 include 方案的值要寫成 `asus_zhuyin.schema:/translator`。
- librime 內建的 corrector 會讓一般句子的正確率掉很多（84% → 61%），所以關掉。
- 詞典的權重在句子裡幾乎沒有作用（加到 30 倍結果一樣），句子由語言模型（octagram）決定；要改句子的結果得在 filter 裡換。
- 小狼毫的外觀設定視窗會把 `weasel.custom.yaml` 整份重寫、註解全丟：值一樣的話從 git 還原。
- 小狼毫 0.17.4 的候選排列方向只能整個方案一起設定，沒有「只有展開時直排」。
- 小狼毫的 `app_options` 用小寫的 exe 檔名，可以設任何選項（`asus_en_direct` 等都是這樣來的）。
- `default.yaml` 的 `key_binder` 預設有 emacs 式按鍵：Control+v 是 Page_Down（所以組字中的 Ctrl+V 要在 Lua 裡先攔下來）。
- `terra_pinyin.synced.dict.yaml` 不存在時，詞典編譯會整個失敗：使用者目錄要先有空的 stub。

## 斷詞（segmentor）

- segmentor 呼叫後如果結尾在起點或起點之前，librime 的斷詞迴圈就停了：倒回之後要把到呼叫起點為止的每一段都加回去。
- 保留下來的 segment 和這次的斷詞計畫對不上，會造成「倒回 → 重加 → 對不上」的無窮迴圈（撇號標點、整段被改字取代都發生過）。`asus_lang.lua` 有每秒 300 次的保護。
- `Segmentation::Reset` 會保留結尾在變動位置之前的 segment（連同選單）；同樣大小的 `add_segment` 只會合併 tag。要讓保留的 segment 重新轉換，必須把它 pop 掉。
- librime 每按一鍵都把整串輸入重新轉換：每鍵延遲和還沒送出的字數成正比（10 字約 4 ms、50 字約 20 ms，原廠方案也一樣）。

## Lua 元件

- Windows 內附的 librime-lua 沒有 `rime_api.get_time_ms`；MSVC 的 `os.clock()` 是 rime.dll 載入後的牆上時間（毫秒），2^31 毫秒（24.86 天）之後永遠是 -1；其他平台是 CPU 時間。統一用 `rime/lua/asus_clock.lua`，它在 -1 之後改用 `os.time()`。
- 每個 Lua 模組都要自己定義 `kAccepted`／`kNoop` 等常數：沒有全域的，少了會出現 `bad argument #1 to '?'`。
- translator 拿到的 `seg` 是唯讀的；要改提示文字用 `ctx.composition:back()`。
- filter 會對每一個 segment 的轉換結果各跑一次：不要用 `composition:back()` 決定行為，要看候選自己的 type 和 span。
- 經過 OpenCC 轉換的候選 type 是 `simplified`：任何檢查 `cand.type` 的 filter 都要把它算進去。
- uniquifier 會把插入的候選重新包一層：用 span＋文字比對，不要用物件比對。
- `Component.Translator(env.engine, "", "script_translator@translator")` 再 `query()` 可以在 Lua 裡另外轉換；句子權重用 `cand:get_genuine():to_phrase().weight`；句子裡的 `DictEntry.commit_count` 永遠是 0。
- librime-lua 沒辦法替「指定的一段文字」打語言模型分數，所以依前後文判斷的學習只能做到看相鄰字。
- 大的詞表要放在 OpenCC 字典裡，不要放 Lua table：放 table 每鍵多約 40 ms（每次呼叫的 GC）。
- `Composition` 沒有 `size()`。
- `select_notifier` 觸發時 `Engine::OnSelect` 已經往下走了：要記錄使用者選了什麼，得在 processor 裡、`asus_keys` 之前記。
- 在選字當中呼叫會改選單的東西（例如記住英文字）會把選單在選字底下重建：延到送出時再做。
- librime 的預覽一定會把 `cand._end` 之後的輸入原樣接在後面；反白比整段短的候選時，行內就會出現按鍵（`asus_list.lua` 用替身解決）。
- librime-lua 的 Lua 狀態是整個行程共用的：模組的 local 變數所有程式共用。
- Rime 對空輸入的 segment 也會回報 `is_composing`：前端會因此開一個 TSF 組字，邊顯示小框邊放行按鍵並不安全。
- `UserDb`（LevelDB）可以用，但查詢的 iterator 可能還活著時不能關掉（會當掉）：開著用到行程結束，iterator 用完 `collectgarbage()`。
- Lua 的 `io.open` 可以開具名管道；讀到已關閉的一端時 `f:read("a")` 回傳 nil（所以伺服器送一行、Lua 讀 `"l"`）；只有一個管道實例時連續寫入會掉資料（開 4 個）。
- 小狼毫把 VK_F24 對應成 0xFFD5、F23 是 0xFFD4（島輝拿來當「答案好了」的按鍵）。

## 學習與資料

- Return 送出、Space 是第一聲；Ctrl／Shift+Return 照打的字母送出。
- Rime 自己會從連續選的單字學到人名。
- `asus_learn` 只在整串輸入都是中文音節時學改字。
- 伺服器用 share mode 0 鎖住 LevelDB 的 `.log` 和 `MANIFEST`：執行中複製只拿得到 `.ldb`，備份一定要先停伺服器。兩個伺服器（島輝和小狼毫）用同一個使用者目錄時，後開的打不開資料庫。
- Rime 自己的同步會在 `asus_learn.userdb` 裡加上 `w \t…` 的鍵：不影響我們的值，但快照不要收它們。
- 評估時不要開學習，每個案例前先送出一個「。」：不然 contextual_suggestions 會把上一題帶到下一題。
- `make_eval_set` 把「台」打成 ㄧˊ：混打的評估句不要用「台」。
- `opencc_dict` 開不了含非 ASCII 字元的路徑：切到那個資料夾、用相對路徑。
- 要讀同音字清單的工具一定要先按 Down 展開：不然 `asus_compact` 只留 1、2 個候選。
- 除錯：`RIME_LOG_LEVEL=0` 加上 `asus_lang/debug: true` 會把斷詞計畫印進測試替身的紀錄。

## Windows

- 真正被載入的 TSF DLL 是 `C:\Windows\System32\daohui.dll`（x64）和 `SysWOW64`（x86）；`Program Files` 裡的只是安裝來源。裝了新的 DLL 之後，已經開著的程式要重開才會換。
- 從 Claude 桌面版（MSIX 包裝）啟動的程式，寫到 `AppData` 的檔案會被重新導向到 `AppData\Local\Packages\Claude_…\LocalCache`，一般程式看不到：測試檔放 `%USERPROFILE%` 底下。
- NSIS 寫的是 32 位元的登錄檔檢視（`HKLM\SOFTWARE\WOW6432Node\DaoHui`）。
- `Start-Process -Wait` 會等安裝程式啟動的子行程（DaoHuiServer）結束，永遠不會回來：改用 `-PassThru` 再對安裝程式本身 `WaitForExit()`。
- Windows PowerShell 5.1 的 `.ps1` 要存成有 BOM 的 UTF-8，中文才不會壞。
- SendInput 的方向鍵和 Home 要加 `KEYEVENTF_EXTENDEDKEY`，不然 Down 變成數字鍵盤的 2、Shift 會掉。
- ctypes 用 `addressof()` 傳的暫存 buffer 會在呼叫前被釋放。
- Python 用有緩衝的 `open()` 開具名管道會得到 EINVAL：`buffering=0`。
- Git Bash 把中文參數交給 Python 時會用 cp950 弄壞：中文經由檔案傳。
- heredoc 會吃掉或改掉反斜線（`\a`、`\t`、`\U`）；`sed -i` 和 Python 的 `write_text` 會改掉 CRLF。寫檔用 Write／Edit 工具。
- `cmd | cut` 這種管線會把前一個指令的失敗藏起來：重要的步驟分開跑、看結束碼。
- 一長串清單不要手抄進工具呼叫（會漏、會重複）：寫成檔案，用雜湊核對。

## Mac

- 鼠鬚管在 App 自己結束組字時（網頁、Electron 的編輯器在 Backspace 時會這樣做）原本送出的是原始按鍵：島輝改成送出畫面上的文字。
- 鼠鬚管的 `keyDown` 不處理帶 Command 的鍵，所以 ⌘V 不經過 Rime。
- 從 root 用 `sudo -u <使用者>` 執行輸入法註冊時，用的還是 root 的 bootstrap：要用 `launchctl asuser <uid> sudo -u <使用者>`。
- 換掉 App 之後，它會從使用者的「已啟用輸入方式」裡消失，但 TIS 還回報「已啟用」：要無條件重新註冊、停用再啟用、選取（`DaoHui --update-input-source`）。還是灰色時登出再登入。
- Sparkle 不輸入密碼就沒辦法把 `.pkg` 或任何東西裝進 `/Library`：所以 App 放在 `~/Library/Input Methods`，自動更新用 zip。
- postinstall 寫進已簽章的 App 會破壞簽章：預先編好的資料要在簽章前放進 `SharedSupport/build`。
