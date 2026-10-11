# 華碩風智慧注音（Rime / 小狼毫）

在小狼毫 0.17.4（librime 1.13.1）上重現並強化華碩智慧輸入法的體驗。
所有設定都在 `rime/`。小狼毫的使用者目錄（登錄檔 `HKCU\Software\Rime\Weasel\RimeUserDir`）直接指向這個資料夾。

## 目錄

| 路徑 | 內容 |
|---|---|
| `rime/` | 小狼毫使用者目錄：方案、Lua、外觀設定 |
| `rime/lua/asus_shift.lua` | 連按兩下 Shift 切換中英（排在所有處理器最前面，才看得到每一個按鍵） |
| `rime/lua/asus_keys.lua` | Ctrl+Space 切換中英、空白／Backspace、展開選單後的按鍵 |
| `rime/lua/asus_compact.lua` | 精簡候選泡泡（只顯示首選，以及尚未打聲調的注音） |
| `rime/lua/asus_learn.lua` | 自我學習：改過的字詞、英文詞、常接的下一個詞；Shift+Del 忘記 |
| `rime/lua/asus_edit.lua` | 句中改字：←/→ 游標、↓ 選游標處的詞、句中刪字插字 |
| `rime/lua/asus_typo.lua` | 打錯鍵自動更正：按到旁邊的鍵、多按一個鍵 |
| `rime/lua/asus_english.lua` | 英文模式的組字與猜字（補完、拼字更正、email） |
| `rime/lua/asus_email.lua` | Email 提示：打前幾個字就出現完整的 email，Tab 送出 |
| `tools/eval_edit.py` | 改字評估：句子打錯時改到對要按幾個鍵、能不能改好 |
| `tools/rime_harness.py` | 用 Python 驅動 `rime.dll`，在 `.harness/` 的複本上測試，不會碰到正在使用的設定 |
| `tools/eval_ime.py` | 量化評估（整句正確率、字正確率、個人詞 Top1／Top9、每鍵延遲） |
| `tools/eval_learn.py` | 學習評估：模擬一位使用者連續打字、改字，量測學到的東西有沒有用、有沒有改壞 |
| `tools/make_eval_set.py` | 從 `tests/eval/sentences.txt` 產生評估用的按鍵序列（採台灣教育部讀音） |
| `tools/fetch_models.py` | 下載語言模型 `.gram` 和英文詞頻表（不進 git） |
| `tools/tsf_test.py` | 在真的記事本裡自動打字、切視窗，檢查沒送出的字有沒有保留 |
| `tests/test_stage1.py` | 階段 1 的行為測試 |
| `reports/eval_history.jsonl` | 歷次評估結果 |
| `backups/` | 改動前的完整備份（不進 git） |

## 功能速查
| 操作 | 效果 |
|---|---|
| 打注音 | 首選直接出現在行內；小框只顯示首選，還沒打聲調時顯示注音，最後面附上你按過的鍵（空白鍵顯示成 ␣，太長只顯示最後 24 個）；不想看到按鍵：`patch:` 加上 `asus_compact/show_keys: false` |
| ↓ | 展開候選（←/→ 移動反白、1–9 選字、空白鍵選反白、Enter 選反白並送出、Esc 收合）；一長串打完才按 ↓，列的是**最後一個詞**的候選，選了只換那個詞，Esc 原樣還原 |
| 打字中按 ← / → | 游標一次移一個字（數字、英文字算一格），小框顯示「前面的字｜後面的字　注音」；Home 跳到開頭、End 回到結尾 |
| 游標在句中按 ↓ | 列出從游標開始的詞，第一個是現在的字；選了只換那一段，前後的字不變。游標在數字／英文上：數字、全形、中文讀音或英文猜字 |
| 游標在句中按 Backspace／打注音 | 刪掉游標前一個字／在游標處插入，其他字不變；Esc 離開游標（字不變） |
| 連按兩下 Shift／Ctrl+Space | 切換中英（會先送出已經打好的字） |
| `` ` `` | 符號小鍵盤：按對應的鍵輸出符號；Tab 切換一般／數學／特殊 |
| Ctrl+, . ' ; | ， 。 、 ； |
| Ctrl+Shift+; / 1 [ ] 9 0 | ： ？ ！ 『 』 （ ） |
| ( ) | 半形括號（全形用 Ctrl+Shift+9/0） |
| 注音縮寫，例如 ㄉㄓ | 快捷文字本的展開文字（`rime/shortcuts.txt`） |
| 直接打英文，例如 `python` | 自動判斷成英文；可以夾在中文之間（我用python跑model）。`I'm`、`don't`、`e-mail`、`node.js`、`C++`、`www.google.com`、email 地址也都是英文；英文字後面的 `,` `.` 是半形標點（Hello, world.）；英文後面的數字按空白會照打送出（iPhone 15 Pro） |
| 打 email 的前幾個字（至少 3 個） | 小框出現「⇥完整的 email」，按 Tab 送出；中文、英文模式都可以。用過的 email 會自動記住，也可以寫在 `rime/emails.txt`（一行一個） |
| 打英文時的猜字 | 小框顯示「打的字＋最多 3 個猜字」：沒打完的字會補完（pyth → python），拼錯的字會更正（recieve → receive）；有 ⇥ 的那個按 Tab 就送出（拼錯的整個換掉），↓ 展開後用 1–9 選其他的，空白鍵照打的送出 |
| Shift＋字母 | 大寫英文（GPU、Python） |
| 中文模式直接打數字 | 2026、100、3.14 會直接變成數字，可以跟中文混打（2026年、3點、100元）；數字後面的量詞會依前後文選（2杯、18歲、第2章寫完了、第2張照片）。剛好是完整注音的數字（204＝但、283＝打）照樣是中文，按 ↓ 第二個就是數字；數字鍵盤（numpad）永遠輸出數字 |
| 英文後按空白 | 送出目前打的內容（不自動加空格） |
| 多打一個鍵 | 判斷為贅字時會自動略過 |
| 按到旁邊的鍵 | 自動更正（小框顯示「更正」），原本打的結果在 ↓ 第二個 |
| Backspace | 已打完聲調的字一次刪整個字；還沒打完的音節、英文則一次刪一個鍵 |
| 送出文字後 | 小框顯示「接下來可能要打的詞」（灰色）；Tab 或 → 接受，↑↓ 切換，按其他鍵就消失 |
| 打字中出現 ⇥詞 | 按 Tab 直接送出「目前的字＋提示的詞」（自動完成） |
| 改過一次的字詞 | 之後在別的句子也會用你改過的（例如改過一次「程式」，「應用程式」也會對） |
| 英文詞被拆成注音 | 按 **Ctrl+Enter**（或 Shift+Enter）照打的字母送出，之後這個詞就會被認成英文、Tab 也能補完 |
| 學錯了 | 在展開的候選清單選到那個字詞（或預測的詞）按 **Shift+Del**（或 Ctrl+Del）忘掉它 |

## 英文模式
連按兩下 Shift 進入英文模式後，打的字會先組起來（行內有底線），小框顯示「打的字＋最多 3 個猜字」，跟手機鍵盤一樣：

| 按鍵 | 效果 |
|---|---|
| 空白 | 照打的送出，再打一個空格 |
| Tab | 送出 ⇥ 的猜字：補完（pyth → python）、拼字更正（recieve → receive）、縮寫（dont → don't）、用過的 email（tim → tim.lin@…）；沒有猜字時 Tab 照常送給程式 |
| ↓ | 展開猜字，1–9 選字，空白鍵選反白的字（再加空格），Enter 選反白的字送出 |
| Backspace | 刪掉最後一個字母 |
| Enter、標點、方向鍵、Ctrl 快捷鍵 | 先把打的字送出，按鍵再照常送給程式（Enter 一次就能送出聊天訊息） |

- 終端機（Windows Terminal、cmd、PowerShell、Git Bash）維持「直接輸出」：按鍵直接送進程式，沒有小框。
  其他想要直接輸出的程式（例如遊戲）：在 `rime/weasel.custom.yaml` 照樣加一行 `app_options/程式.exe: { asus_en_direct: true }`（小寫檔名），重新部署。
- 全部改回直接輸出：`rime/asus_zhuyin.custom.yaml` 的 `patch:` 加上 `asus_english/compose: false`。
- 英文模式**只記 email 地址**，其他打的字都不記（見「隱私」）。

## Email
- 打過一次的 email（中文模式裡打的，或英文模式打完送出的）會記在 `rime/asus_learn.userdb`。
- 也可以自己列在 `rime/emails.txt`（一行一個，`#` 開頭是註解，不進 git），改完重新部署。
- 打前 3 個字以上，小框就會出現最常用的那個；就算前幾個字剛好也是注音（s108 → 你不安啊），Tab 送出的還是 email。
- 不要的 email：展開候選選到它，按 Shift+Del。
- 至少要打幾個字才提示：`patch:` 加上 `asus_email/min_prefix: 4`（小框的 ⇥ 提示和英文猜字清單用同一個設定）。

## 台灣用語優先
語言模型有時會把兩個單字拼成大陸的寫法（宕機、坐標、賬號），`lua/asus_tw.lua` 會換成同音的台灣寫法（當機、座標、帳號）：
- 對照表 `rime/opencc/asus_tw.*` 由 `tools/build_lang_data.py` 產生：essay 詞表裡的詞、小麥注音的台灣語料完全沒用過，而且只差一個字就是台灣常用（至少 20 次）、讀音相同的詞。
- 只在語言模型自己分出的詞的邊界上比對（「晚上｜一起」不會變成「上衣」），的／得／地、在／再交給文法規則；你親手選過的字不換。原本的結果在 ↓ 第二個。
- 評估：一般句子開發集 95% → 96%、教育部單詞首選 91.2% → 92.2%，其他不變；數字保留測試集 82.5% → 80%（少一句）。
- 關閉：`patch:` 加上 `asus_tw/enabled: false`。

## 打錯鍵自動更正
打字時手滑的五種情況都會試著更正（小框顯示「更正」，原本打的結果在 ↓ 第二個）：

| 手滑 | 例子 |
|---|---|
| 按到旁邊的鍵 | ㄈ 打成 ㄌ：吃飯 → 喫爛 |
| 多按一個鍵 | ㄉㄨㄛ 中間多一個 ㄊ |
| 少按一個鍵 | 今 ㄐㄧㄣ 少了 ㄣ：我今天 → 我機天 |
| 兩個鍵順序顛倒 | 聲調按太早（ㄇㄧˋㄢ → 面）、下一個字的第一個鍵搶在聲調前（你好 ㄋㄧㄏˇㄠˇ） |
| 聲調不對 | ㄗㄚˋ（沒有這個音）→ 在 |

- 拼不成音節的：把上面每一種「還原」後拼得成的音節都列出來，用語言模型挑前後兩個字最通順的。
- 拼得成音節、但整句明顯不通的（換鍵、少按）：每個字前後各看兩個字，語言模型分數要高出很多（`asus_typo/margin`、`asus_typo/ins_margin`，預設 30）才更正；如果更正後語言模型把幾個單字合成一個詞（我｜機｜天 → 我｜今天），18 分就夠（`asus_typo/merge_margin`）。句尾右邊沒有上下文，門檻是 `asus_typo/end_margin`（18）。
- 第一聲的字打錯一個鍵會長得像英文（說 ㄕㄨㄛ 打成 gui）：按空白時，如果當成「打錯一鍵的注音」比當成英文字合理，空白就算第一聲，不會送出英文。
- **數字不會被當成打錯**：整串都是數字鍵（13、16）、數字後面接完整的字（3點、10日）、完整的字後面接數字（是8月），都當數字。
- 你親手選過的字（名字、術語）附近不更正。
- 評估（每句一個手滑，開發集／保留測試集，都是 100 句）：

| | 換成旁邊的鍵 | 同左，在句尾 | 多按一鍵 | 少按一鍵 | 順序顛倒 | 一般句子 |
|---|---:|---:|---:|---:|---:|---:|
| 階段 7 | 53% ／ 56% | 59% ／ 49% | 57% ／ 50% | 24% ／ 28% | 3% ／ 7% | 96% ／ 90% |
| 階段 10 | 74% ／ 75% | 69% ／ 60% | 84% ／ 79% | 56% ／ 59% | 78% ／ 77% | 96% ／ 90%（不變） |
| 階段 12 | 75% ／ 76% | 72% ／ 60% | 84% ／ 79% | 56% ／ 59% | 78% ／ 77% | 96% ／ 90%（不變） |

- 階段 12 補的洞：含有「為、裡、著、麵」這類被 OpenCC 轉成台灣字形的句子，候選的類型不一樣，之前整句被跳過、完全不會更正；現在一樣處理，更正後的字也是台灣字形。
- 代價：每個按鍵平均多 1 ms（3.6 → 4.6 ms，最慢的 1% 從 7 ms 變 10 ms）。
- `tools/latency.py` 在階段 11 改成每句打完按 Enter 送出再打下一句（跟實際使用一樣，更正用的快取每次送出都會清空），並單獨量 Enter：穩定後每鍵平均 5.2 ms、最慢 1% 約 12 ms；Enter 平均 8 ms，句尾是數字鍵要重新判斷時約 17 ms（最慢 25 ms）。（階段 11 那次量到「剛啟動第一輪特別慢」，之後重跑三次都沒有再出現，是那一次的雜訊。）
- 每鍵延遲會隨**還沒送出的字數**變長，因為 librime 每按一鍵都把整串重新轉換：10 個字約 4 ms、30 個字約 12 ms、50 個字約 20 ms（原廠注音方案同樣是線性增加，只是基數小：0.5 → 5 ms）。一口氣打超過 50 個字不送出才會開始有感覺。
- 試過但沒採用：少按一鍵的門檻降到 20 可以多救 8 句，但保留測試集有一句正確的句子被改錯，所以維持 30。
- 還是沒改到時，用 ← 把游標移過去，↓ 選字。
- 關閉：`rime/asus_zhuyin.custom.yaml` 的 `patch:` 加上 `asus_typo/enabled: false`。

## 句中改字
一長串打完才發現中間有錯字時：← 移到錯的字前面（或 Home 到開頭再 →），↓，選字，Enter 送出。
- 游標前面的字會被「鎖住」（選成固定的字），後面的字照原樣顯示，所以只有你選的那一段會變。
- 改字評估（`tests/eval/edit*.txt`，打完整句後把轉錯的地方改對）：

| | 舊方法（↓ 從句首一段一段選） | 游標 |
|---|---:|---:|
| 開發集：改得好 | 72.7% | 100% |
| 開發集：平均按鍵 | 5.9 | 4.6 |
| 保留測試集：改得好 | 83.3% | 100% |
| 保留測試集：平均按鍵 | 5.7 | 4.3 |

舊方法改不好的多半是句子裡有數字或英文的情況（↓ 只能選最後一段）。用游標選的字也會被自我學習記住。

打完直接按 ↓ 現在列的是最後一個詞：錯在最後一個詞（剛打完就發現）時只要 2 個鍵（游標要 4 個）；錯在前面時先 ↓ 再 Esc 改用游標，會多按 2 個鍵（開發集平均 6.4 鍵、保留測試集 5.7 鍵，全部改得好）。

## 數字
大千式鍵盤的每個數字鍵也是注音（1 ㄅ、2 ㄉ、5 ㄓ、8 ㄚ、9 ㄞ、0 ㄢ，3 4 6 7 是聲調），所以只在「當注音不合理」時才判斷成數字：
- 數字評估集（`tests/eval/numbers*.txt`，上排數字鍵直接打）：開發集 0% → 97.5%，保留測試集 0% → 82.5%。
- 打中文時中途不會閃出數字（`dev_partial`：0%），一般句子、中英混打、的得地、個人詞彙的分數都沒有變。
- 代價：在完整的字後面多按到一個數字鍵，以前會被當成贅字略過，現在會變成數字（多打一鍵的測試從 23%／25% 降到 21%／18%）。
- 打字途中句尾的數字鍵會先顯示成還沒打完的注音（`10` → 辦，因為你可能正要打 ㄅㄢˋ）；**按 Enter 或 Ctrl+標點送出時**會重新判斷，句尾的數字鍵就是數字（買1送1、我排第2、他是9）。只有「聲調按太早」而且語言模型也這麼認為時才當成注音（見面 ㄇㄧˋㄢ）。
- **數字後面第一個字**（階段 11 改過）：數字會把輸入切成幾段，後面那一段轉換時看不到前面的字，所以量詞常選錯（3科蘋果）。現在的做法：
  - 第一個音節在量詞表（`asus_lang.lua` 的 `MEASURE`，約 100 個）裡時，把「前面兩個字＋數字的讀法＋這一段」一起再轉一次（住三晚、考三科、第二章寫完了、第二張照片），取那個位置的字；這個字必須是該讀音常接在數字後面的詞之一（`MEASURE_ALSO`：張／章、級／集、顆／科、碗／晚、部／步…），否則用原本的字或量詞表的預設字。
  - 只改這一個字；原本的轉換結果排在 ↓ 第二個。
  - **你在數字後面選過的字會記住**：例如「第3」後面選過「集」，下次「第＋數字＋ㄐㄧˊ」就先出「集」（記的是「數字前一個字＋讀音」）。
  - 之前的做法是硬換（第N＋ㄓㄤ 一律「章」、第N＋ㄐㄧˊ 一律「集」），會把「第2張照片」「第2級警戒」改錯，而且排在個人學習之後。
  - 還是分不出來的：單獨的「第3集／第3級」「第3張／第3章」（語言模型兩個都接受），選一次就會記住。
  - 代價：硬換的規則在測試句上剛好是對的，拿掉之後數字的保留測試集少一句（80% → 77.5%）、第二個開發集少一句（88% → 86%，「這是第1集」變成「第1級」）；換來的是我另外列的 37 個「數字＋量詞」短句從對 19 個變成對 25 個，而且不再有改不回來的字。
- 第二個開發集 `tests/eval/numbers_more.txt`（50 句：句尾的數字、後面不是量詞的單一數字、小數、長數字）：74% → 88%；原本的開發集 97.5%、保留測試集 80% 不變。
- **數字鍵也是注音的聲母時**（階段 12）：1、2、5 就是 ㄅ、ㄉ、ㄓ，所以「5＋ㄨㄢˋ」是 5萬 也是「賺」、「2＋ㄨㄟˋ」是 2位 也是「對」、「12萬」是「1段」。以前一律當注音，5萬、2位、12萬、2碗飯全部打不出來。現在兩種讀法都交給語言模型比（前後各看兩個字，數字當成它的讀法：只要賺就好／只要五萬就好），數字的讀法分數夠高才採用：
  - 只在數字後面是量詞表裡的詞（萬、位、碗、億…）時，或前面已經有數字（1＋段 → 12＋萬）時才考慮。
  - 採用時小框標「數字」，原本的中文讀法在 ↓ 第二個；緊接在另一個數字前面不採用（賺25萬）；一開頭就打的要更高的分數。
  - 評估（`tests/eval/numbers_amb*.txt`，各 30 句）：開發集 0% → 76.7%、保留測試集 3.3% → 73.3%；一般句子、打字途中不閃數字（0%）、個人詞彙都不變。
  - 還分不出來的（語言模型兩種都覺得通順）：請問5位嗎、只要5萬就好、再2位、這個要12萬。這時數字的讀法排在候選**第二個**：把游標移到那個字（或按 Home）再按 ↓ 就看得到，標著「數字」。
  - **選過會記住**：不管是把中文改成數字，還是把自動選的數字改回中文，下次同樣的「前一個字的讀音＋這個讀音」就照你選的（記在 `asus_learn.userdb`）。
  - 數字鍵盤（numpad）永遠輸出數字，不受這些判斷影響。
- 還做不到的：`10大` 的按鍵和 `10284` 一樣，只能按 ↓ 選；數字後面那個詞沒有前文可參考（他是8月「升」的）。

## 詞庫
- **個人詞彙**：`lexicon/personal_words.txt`（不進 git），一行一個詞：`詞<Tab>注音<Tab>來源`，注音可以留空，會自動查。
  第一次使用時由 `tools/import_personal.py` 從華碩、新注音、舊小狼毫的匯出檔合併產生。
- **台灣補充詞庫**：從教育部國語辭典產生。補上台灣讀音（晚上 ㄕㄤˋ）、台灣寫法（螢幕勝過熒幕），以及缺漏的詞。
- 修改詞庫後執行 `python tools/build_lexicon.py`，再重新部署。
- **快捷文字本**：`rime/shortcuts.txt`（不進 git，範例在 `shortcuts.example.txt`）。改完要重新部署。
- **符號鍵盤配置**：`rime/asus_zhuyin.schema.yaml` 的 `asus_symbols`。
- **台灣詞頻**：來自[小麥注音](https://github.com/openvanilla/McBopomofo)（MIT）的 `phrase.occ` 和 `BPMFMappings.txt`，放在 `third_party/mcbopomofo/`。
  `build_lexicon.py` 會依共同詞的中位數比例，把詞頻換算成跟 `essay.txt` 相同的尺度，
  `build_lang_data.py` 也用它產生「下一個詞預測」的對照表（`rime/opencc/asus_next.*`），
  以及把送出的文字切成詞用的詞表（`rime/opencc/asus_seg.*`，自我學習用）。
- **語言模型**：`wanxiang-lts-zh-hant.gram`（[RIME-LMDG](https://github.com/amzxyz/RIME-LMDG)，CC-BY-4.0）。
  它是用開發集 A/B 評估選出來的，`rime/grammar.yaml` 裡有改回舊模型的方法。
- **英文猜字**：`rime/opencc/asus_en_prefix.*`（每個開頭最多 5 個補完）和 `asus_en_fix.*`（前 40,000 個常用字的拼字更正，差一個字母：多、少、錯、前後顛倒），由 `build_lang_data.py` 產生。小狼毫啟動後第一次打字時多花約 0.7 秒載入，只發生一次。
- **英文詞彙**：來自舊小狼毫的 `easy_en` 詞頻表，加上 `lexicon/english_words.txt`（你自己的英文專有名詞，一行一個）。
- **英文猜字的排序**（階段 13）：
  - 詞頻改用兩個來源的幾何平均：`easy_en` 加上 [SymSpell](https://github.com/wolfgarbe/SymSpell) 的詞頻表（MIT 授權，資料來自 Google Books Ngram 與 SCOWL，放在 `third_party/symspell/`，由 `fetch_models.py` 下載）。只靠 `easy_en` 時 neurone 排在 neural 前面、optimise 在 optimize 前面、occurred 完全沒有頻率；只靠 SymSpell 時 classifieds 排在 classification 前面、沒有 dataset。
  - **看前一個英文字**：`rime/opencc/asus_en_follow.*` 記每個英文字後面最常接的 40 個字（SymSpell 的詞對資料）。打完 `machine` 再打 `lea`，第一個猜的是 learning；打完 `new` 再打 `yor` 會被認成英文並猜 York。前一個字是中文或標點時不套用。
  - 「這串按鍵算不算英文」仍然只看 `easy_en` 的詞頻，所以中英混打的判斷不受影響（一開始沒分開，「幫我check一下」的 check 被吃成 checku）。
  - 評估（開發集／保留測試集，第一個就猜對）：單字 75% ／ 65% → 87.5% ／ 65%；有前一個字 50% ／ 33.3% → 70% ／ 70%。前三個內猜對：單字 90% ／ 77.5% → 95% ／ 80%；有前一個字 56.7% ／ 73.3% → 83.3% ／ 93.3%。單字的保留測試集第一名沒有進步：沒有前文時 experi 是 experience 還是 experiment 本來就猜不準。
  修改後執行 `python tools/build_lang_data.py`，再重新部署。
  詞表放在 OpenCC 字典裡（`rime/opencc/asus_en.*`），不放在 Lua 記憶體，否則每次按鍵會慢約 40 ms。

## 自我學習
小狼毫本身就會記住你打過的句子；`asus_learn.lua` 再加上三件事，學到的資料都存在 `rime/asus_learn.userdb`（只在本機）。

- **改字**：展開候選改掉某個詞再送出，就記成「這組注音：原本的詞 → 你選的詞」，連同前後的字。
  - 前後的字一樣時，立刻改用你選的詞。
  - 其他句子：只改過一次而且從來沒改回去，就直接套用；如果兩個方向都改過（例如「程式」和「城市」），就只看前後文。
  - **你自己常打的詞不會被換掉**：被換掉的詞如果你最近自己打過（例如平常就會打「城市」），學到「程式」後只在相同前後文套用；小狼毫自己的學習把它換掉時也會換回來。
  - 被學習換過的地方，小框會標「學」，↓ 第二個就是原本的。
  - 單一個字，以及只差在「在／再／的／得／地」的詞，一定要前後的字都相同才會套用。
  - 被學到的詞換掉時，原本的轉換結果會排在候選第二個，按一次就能改回來；改回來也會被記住。
  - 想要「改兩次才套用到所有句子」（比較不會誤改別的句子，但學得比較慢）：在 `rime/asus_zhuyin.custom.yaml` 的 `patch:` 加上 `asus_learn/global_after: 2`。
- **英文詞**：用 Ctrl+Enter 送出的字母、句子裡打過的英文詞。
- **下一個詞**：把你送出的文字切成詞，同樣的兩個詞前後出現兩次（或接受預測一次），之後就會排在預測的第一個。
- **淡化**：學到的東西每 90 天權重減半（`asus_learn/half_life_days`），太舊的會自動刪掉。
- **忘記**：Shift+Del（見上表）。整個清空：結束小狼毫服務 → 刪除 `rime/asus_learn.userdb` 資料夾 → 重新啟動。
- **關閉**：`rime/asus_zhuyin.custom.yaml` 的 `patch:` 加上 `asus_learn/enabled: false`，再重新部署（小狼毫自己的學習不受影響）。

評估（`tests/eval/learn.txt` 是開發集，`learn_test.txt` 是保留測試集，只看總分）：

| 保留測試集 | 原本 | 只有小狼毫學習 | 加上 asus_learn |
|---|---:|---:|---:|
| 改過的詞用在新句子（Probe） | 76% | 76% | 88% |
| 同音的另一個詞沒被改壞（Contrast） | 60% | 60% | 60% |
| 教過的英文詞 | 0% | 0% | 88% |
| 預測第一個就對（Pred@1） | 0% | 0% | 75% |
| 一般句子（學習之後） | 90% | 90% | 90% |

已知代價：只改過一次就套用到所有句子，所以你**沒打過**的同音詞會被換掉，直到你把它改回來一次（開發集上 Contrast 從 92% 降到 58%）。

兩種設定的比較（階段 12 重跑；每個「沒做到」都是你要多改一次）：

| | 改一次就套用（現在） | 改兩次才套用（`global_after: 2`） |
|---|---:|---:|
| 開發集：學到的詞用在新句子 | 86.7%（30 題錯 4） | 73.3%（錯 8） |
| 開發集：同音的另一個詞沒被改壞 | 58.3%（12 題錯 5） | 83.3%（錯 2） |
| 保留測試集：用在新句子 | 96%（25 題錯 1） | 88%（錯 3） |
| 保留測試集：沒被改壞 | 80%（5 題錯 1） | 80%（錯 1） |

兩邊加起來要多改的次數差不多（開發集 9 對 10、保留測試集 2 對 4），所以維持「改一次就套用」。想做得比這兩種都好，需要知道「這個句子裡語言模型有多確定」，目前 librime 給 Lua 的介面拿不到指定文字的分數。

有打字紀錄的評估（`tests/eval/learn_hist*.txt`：學之前先打過幾句用到同音詞的句子）：

| | 學到的詞用在新句子 | 你打過的同音詞沒被改壞 |
|---|---:|---:|
| 開發集：之前 | 100% | 25% |
| 開發集：現在 | 40% | 87.5% |
| 保留測試集：之前 | 87.5% | 100% |
| 保留測試集：現在 | 87.5% | 83.3%（打完整段後 100%） |

你自己也會打的那個詞受保護，所以學到的新詞只在相同前後文出現；要它到處都用，就在其他句子再改一次。

## 部署
1. 修改 `rime/` 裡的檔案。
2. 系統匣的小狼毫圖示按右鍵 →「**重新部署**」。
3. 如果是改了 `RimeUserDir`，或是第一次在新電腦上使用：先跑 `python tools/fetch_models.py`、`python tools/build_lexicon.py` 和 `python tools/build_lang_data.py`，再重新啟動小狼毫服務（重開機最簡單），然後重新部署。
4. 詞表的快速格式（選用，但建議）：`build_lang_data.py` 若找到 `third_party/opencc-build/src/tools/Release/opencc_dict.exe`，會把 `rime/opencc/` 的詞表轉成二進位的 ocd2，小狼毫啟動後第一次打字的載入時間從約 1.8 秒降到約 0.5 秒。編譯方法（需要 Visual Studio 2022 Build Tools）：下載 [OpenCC 1.1.9](https://github.com/BYVoid/OpenCC/archive/refs/tags/ver.1.1.9.tar.gz) 解壓到 `third_party/`，用 VS 內附的 cmake 執行 `cmake -S third_party/OpenCC-ver.1.1.9 -B third_party/opencc-build -G "Visual Studio 17 2022" -A x64 -DBUILD_DOCUMENTATION=OFF -DENABLE_GTEST=OFF -DPYTHON_EXECUTABLE=<python.exe>`，再 `cmake --build third_party/opencc-build --config Release --target opencc_dict`。沒有這個工具時照常用文字詞表。

## 測試
```
C:\Users\User\anaconda3\python.exe tests\test_stage1.py
C:\Users\User\anaconda3\python.exe tests\test_stage2.py
C:\Users\User\anaconda3\python.exe tests\test_stage3.py
C:\Users\User\anaconda3\python.exe tests\test_stage4.py
C:\Users\User\anaconda3\python.exe tests\test_stage5.py
C:\Users\User\anaconda3\python.exe tests\test_stage6.py
C:\Users\User\anaconda3\python.exe tests\test_stage7.py
C:\Users\User\anaconda3\python.exe tests\test_stage8.py
C:\Users\User\anaconda3\python.exe tests\test_stage9.py
C:\Users\User\anaconda3\python.exe tests\test_stage10.py
C:\Users\User\anaconda3\python.exe tests\test_stage11.py
C:\Users\User\anaconda3\python.exe tests\test_stage12.py
C:\Users\User\anaconda3\python.exe tests\test_stage13.py
C:\Users\User\anaconda3\python.exe tests\test_backup.py
C:\Users\User\anaconda3\python.exe tests\test_numbers.py
C:\Users\User\anaconda3\python.exe tools\eval_ime.py --note "說明這次改了什麼"
C:\Users\User\anaconda3\python.exe tools\eval_learn.py --note "說明這次改了什麼"
```
評估表格的括號裡是**同一版評估集**上一次的數字，可以直接前後對照。階段 10 起，句子的正確率看的是按 Enter 送出後的文字（之前看的是送出前行內顯示的文字），因為句尾的數字鍵要到送出時才決定是不是數字。

### 不會卡死的兩道保險
- **輸入法本身**：`asus_lang.lua` 的斷詞如果在同一個按鍵內重來超過 100 次，或 1 秒內對同一串輸入重來超過 300 次（正常打字最多幾次），那一串就改用原廠斷詞，並在日誌記一筆。按鍵內的計數是為了抓「每秒不到 300 次」的慢速迴圈。最壞的結果是那一串選字比較差，下一個鍵照常運作，不會整個凍住。
- **測試工具**：任何一個按鍵超過 30 秒沒回應（環境變數 `RIME_KEY_TIMEOUT` 可調），測試程序就自己結束（結束碼 124），只印出按鍵的形狀（字母→a、數字→0），不會印出保留測試集的內容。日誌只記警告以上。評估跑完如果保險被觸發過，會印出 `rime_harness: the segmentation guard tripped`，代表有 bug 要修。

## 回到上一版
- **設定回到某個 commit**：`git log` 找到版本 → `git checkout <commit> -- rime/` → 重新部署。
- **完全回到改動前的小狼毫**：
  1. 匯入 `backups\20261006-003720-HKCU-Rime.reg`，把使用者目錄改回 `D:\Document\小狼豪輸入法`。
  2. 重新啟動小狼毫服務。
  3. 重新部署。
- **改回華碩輸入法**：在 Windows 的語言設定切換輸入法就好，這個專案不會改到華碩的任何東西。

## 學習資料備份
學到的東西（`rime/*.userdb`、`emails.txt`、`shortcuts.txt`、`*.custom.yaml`、`lexicon/*_words.txt`）每天自動備份一份到 `backups/learn/<日期-時間>/`，只在本機、不進 git。

- **排程**：Windows 工作排程器的 `RimeLearnBackup`（只屬於你的帳號）。每 2 小時檢查一次，當天還沒備份、而且鍵盤滑鼠已經 2 分鐘沒動，才會備份；當天已有備份就什麼都不做。
- **為什麼要等閒置**：小狼毫執行時會把資料庫最新的部分鎖住，別的程式讀不到，所以備份時要把小狼毫服務停掉約 1–3 秒再啟動，那幾秒打不出中文。
- **會驗證**：每份備份都會實際打開讀一次、數裡面有幾筆（改字、下一個詞、英文詞、email…），結果寫在 `backup.json`；打不開的備份標成 BAD，不會被拿來還原。筆數比上一份少一半以上時不刪任何舊備份。
- **保留**：最近 14 個有備份的日子各留最新一份，再加最近 12 個月每月最新一份。
- 指令（都在專案資料夾執行）：

```
C:\Users\User\anaconda3\python.exe tools\backup_learn.py              現在備份一次（會停 1–3 秒）
C:\Users\User\anaconda3\python.exe tools\backup_learn.py --list       列出備份
C:\Users\User\anaconda3\python.exe tools\backup_learn.py --restore latest         看看會還原什麼（不會動）
C:\Users\User\anaconda3\python.exe tools\backup_learn.py --restore latest --yes   真的還原；現有資料先移到 backups\learn\_before-restore-…
C:\Users\User\anaconda3\python.exe tools\backup_learn.py --uninstall  取消每日排程（--install 重新註冊）
```
- 排程每次執行的結果記在 `backups/learn/backup.log`。
- **還原是全有或全無**：先核對備份的每個檔案和備份當時的雜湊值一致，在現有檔案旁邊準備好完整的一份並再核對一次，才停掉小狼毫、把現有資料移到一旁、換上新的；任何一步失敗就把已經做的步驟全部倒回去。

## 切換視窗時保留沒送出的字
原版小狼毫在切換視窗時會把還沒送出的字刪掉。`patches/weasel/` 是對小狼毫本身（C++ 的 `WeaselTSF`）的修補，讓行內已經顯示的字留在文件裡。
- 這是唯一不在 `rime/` 裡的改動：換的是 `C:\Windows\System32\weasel.dll` 和 `C:\Windows\SysWOW64\weasel.dll`（原檔備份在旁邊），重新部署不會影響它，小狼毫更新後要重裝。
- 實機測試：`python tools\tsf_test.py` 會開記事本自動打字、切視窗、讀回文字（約 1 分鐘，期間不要碰鍵盤滑鼠）。目前 6 項全過；Word、瀏覽器、VS Code 要手動試。
- 安裝、還原、重新編譯的方法，以及第一版為什麼沒生效，寫在 `patches/weasel/README.md`。

## 隱私
- 學習資料（`*.userdb`，包括 `asus_learn.userdb` 記下的「詞→下一個詞」）、建置檔、同步目錄、個人詞彙匯出檔都列在 `.gitignore`，而且只存在本機。
- 英文詞的學習只從中文模式送出的文字學。英文模式雖然會組字，但只記長得像 email 地址的字（name@host.com），其他打的字一律不記，所以就算某個程式的密碼欄位沒有關掉輸入法，密碼也不會被存下來（除非它長得像 email）。終端機是直接輸出，完全不經過組字。
- 記住的 email 存在 `rime/asus_learn.userdb`（只在本機），`rime/emails.txt` 不進 git。
- 沒有設定任何雲端同步目錄。舊設定裡的 Google Drive 同步路徑沒有沿用。
