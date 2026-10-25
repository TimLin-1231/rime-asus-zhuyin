# 島輝 DaoHui for macOS：計畫

目標：同一套 `rime/`（方案、Lua、詞庫、語言模型）在 Windows（小狼毫 fork）和 macOS 上都能用，行為一樣。

## 先看結論

- **推薦方案 A：fork 鼠鬚管（Squirrel）改名成島輝。** 鼠鬚管 1.1.2 已經內建我們需要的 librime 1.16 + librime-lua / octagram / predict（而且有 `rime_api.get_time_ms`），也已經有 Xcode 專案、GitHub Actions、`.pkg` 安裝檔和 Sparkle 自動更新。我們的功能幾乎全在 Lua／YAML 裡，換前端要動的只有改名、外觀設定和打包。Windows 端也是 fork 小狼毫，兩邊同一個模式、同一個授權（GPLv3）。
- **方案 B（自己寫 Swift 輸入法殼，參考融鍵 BlendKey）** 可行，librime 有現成的 macOS 編譯版可以直接嵌入，但鍵盤事件、行內組字、候選窗、更新都要自己寫，工作量是 A 的好幾倍，而且 IMK 的各種 App 相容問題要重新踩一次。建議等 A 上線、確定鼠鬚管的候選窗做不出想要的樣子時再考慮；到時可以借用 BlendKey 的 ModeHUD／CandidatePanel／安裝腳本（MIT）。
- **Lua 端已經可以跨平台**（commit 7ab0577）：計時改用 `rime/lua/asus_clock.lua`，Windows 的具名管道在 macOS 上自動關閉。Windows 上行為不變（測試全部通過）。

## 1. 哪些東西只在 Windows 上成立

| 項目 | 位置 | macOS 上 |
|---|---|---|
| `os.clock()` 當牆上時間 | `asus_shift.lua`（連按 Shift）、`asus_tab.lua`（連按 Tab）、`asus_lang.lua`（切分迴圈保護） | **已處理**。MS C runtime 的 `clock()` 是牆上時間；macOS/Linux 的是 CPU 時間（等下一個鍵時不會走），兩次 Shift 隔一秒也會被當成連按。改用 `asus_clock.lua`：有 `rime_api.get_time_ms` 就用它（鼠鬚管 1.1.x 有），Windows 用 `os.clock()`，都沒有時才開子行程讀時間（見第 9 節） |
| 具名管道 `\\.\pipe\asus_zhuyin_assist_in/out` | `asus_english.lua` | **已處理**：只在 Windows 開。在 macOS 上這個字串只是一個普通檔名，`io.open` 會在工作目錄（鼠鬚管是 `.app/Contents/SharedSupport`，用 `make install` 裝的話還可以寫入）建出怪檔案、破壞 App 簽章。macOS 上「停頓後續寫」先關掉 |
| F24 喚醒鍵 | `asus_english.lua`、`assist/bridge.py`（SendInput） | Mac 鍵盤只到 F20，也沒有 SendInput。之後要由 Swift 端收訊息、自己把 F24 交給 librime（不經過系統鍵盤事件，也不需要輔助使用權限） |
| `app_options/<程式>.exe` | `weasel.custom.yaml` | 鼠鬚管用 **bundle id**：`com.apple.Terminal`、`com.googlecode.iterm2`、`dev.warp.Warp-Stable`、`net.kovidgoyal.kitty`、`org.alacritty`、`com.github.wez.wezterm`、`com.microsoft.VSCode`、`com.todesktop.230313mzl4w4u92`（Cursor）、`com.jetbrains.pycharm`、`com.jetbrains.intellij`、`com.sublimetext.4`、`com.apple.dt.Xcode`。機制一樣（設成 option，Lua 用 `get_option` 讀） |
| 外觀設定 | `weasel.custom.yaml` | 改寫成鼠鬚管的 `squirrel.yaml`（fork 後改名），對照見第 6 節。顏色要轉：小狼毫這裡用 `color_format: argb`，鼠鬚管只吃 `0xAABBGGRR` |
| 字型 Microsoft JhengHei UI | 同上 | `PingFang TC` |
| Ctrl+Space 切中英 | `asus_keys.lua` | macOS 預設 Ctrl+Space 是「選取上一個輸入方式」，系統會先攔走。要嘛在「系統設定 › 鍵盤 › 鍵盤快速鍵 › 輸入方式」關掉，要嘛只用連按 Shift |
| Caps Lock | `default.custom.yaml` | macOS 預設「用大寫鎖定鍵切換 ABC」，會直接換掉輸入法而不是送給 Rime；說明文件要提醒 |
| Shift+Del／Ctrl+Del 忘記學錯的字 | `asus_learn.lua` | Mac 沒有 Delete 鍵：fn+Delete（= XK_Delete），即 Shift+fn+Delete |
| Home/End/PageUp/PageDown、數字鍵盤 | 多處 | 筆電要按 fn+方向鍵；沒有數字鍵盤，功能不受影響 |
| `tools/rime_harness.py` | 測試 | `winreg` 找小狼毫、載入 `rime.dll`、`%TEMP%`、`robocopy` 複製 userdb。移植要點：用 `ctypes.CDLL` 載入 `DaoHui.app/Contents/Frameworks/librime.1.dylib`、shared data 指到 `Contents/SharedSupport`、`tempfile.gettempdir()`、`shutil.copytree` |
| `tools/backup_learn.py`、`asus_compare.py`、`tsf_test.py`、`assist_test.py`、`ime_continue_test.py` | 工具 | 操作 WeaselServer、TSF、記事本，只適用 Windows |
| `tools/build_lexicon.py`、`build_lang_data.py` | 產生資料 | 用 `weasel_root()` 找 terra_pinyin、`opencc_dict.exe`。產生出來的 `.dict.yaml`、`.ocd2`、`asus_zhuyin_data.lua` 都跨平台，繼續在 Windows（或 CI）產生即可 |
| `assist/` 寫作小幫手 | 整個模組 | user32/kernel32（RegisterHotKey、SendInput、剪貼簿、具名管道、Mutex）、`llama-server.exe`、`harper-cli.exe`。macOS 版是另一個專案：全域快捷鍵、Cmd+C/Cmd+V 需要「輔助使用」權限，llama.cpp/Harper 有 macOS 版。不在第一版範圍 |

不受影響：`.gram`、`.ocd2`、`.dict.yaml`、`asus_zhuyin_data.lua`、userdb（LevelDB）都是跨平台格式；Lua 讀檔一律用 `rime_api.get_user_data_dir() .. "/..."`，`shortcuts.txt` 讀檔時已去掉 `\r`。

### 一個 Windows 上的隱憂（不在這次修）
小狼毫 0.17.4 的 librime-lua 沒有 `get_time_ms`，所以 Windows 一直用 `os.clock()`。依微軟文件，`clock()` 超過 `clock_t`（32 位元毫秒）能表示的範圍、也就是 **WeaselServer 連續跑 24.8 天** 後會固定回傳 -1，之後每兩下 Shift 都算連按、每兩下 Tab 都算連按。島輝 Windows 版改用新版 librime（有 `get_time_ms`）就自動解決，`asus_clock.lua` 會優先用它。

## 2. 鼠鬚管現況（研究結果）

來源：`git clone --depth 1 https://github.com/rime/squirrel`（master 46962d2，2026-10-21）與 tag 1.1.2、librime 子模組 33e7814、GitHub API。

- **版本**：最新正式版 **1.1.2（2026-01-13）**，`action-install.sh` 下載 **librime 1.16.0（a251145）**；master／nightly 已換成 **librime 1.17.0（33e7814，2026-06-05）**。
- **外掛**：librime 的 `release-ci.yml` 對 macOS 和 Windows 都指定 `rime_plugins: hchunhui/librime-lua lotem/librime-octagram rime/librime-predict`，macOS 用 `make release`（`BUILD_MERGED_PLUGINS=OFF`、`ENABLE_EXTERNAL_PLUGINS=ON`）編成獨立的 `librime-lua.dylib`、`librime-octagram.dylib`、`librime-predict.dylib`。鼠鬚管的 Xcode 專案用「Copy Rime plugins」把它們放進 `Squirrel.app/Contents/Frameworks/rime-plugins/`；librime 啟動時從「自己那個 dylib 所在目錄/rime-plugins」載入（`plugins/plugins_module.cc`）。
- **librime-lua 版本**：librime-lua 沒有正式版號，librime 的 CI 在打包當下 `git clone --depth 1` 它的 master，並把確切的 commit 寫進壓縮檔裡的 `version-info.txt`。`get_time_ms` 是 2025-06-28 加的（commit 701cb4e，PR #409，用 `std::chrono::steady_clock`）。librime 1.16.0 的 release 在 2026-01-06 打包，所以鼠鬚管 1.1.2 的 librime-lua 有 `get_time_ms`；小狼毫 0.17.4（librime 1.13.1）沒有。（我沒有下載壓縮檔打開 `version-info.txt` 確認，這是由日期推得的；在 Mac 上用 `asus_clock.source()` 或 log 一看就知道。）
- **建置**：`.github/workflows/commit-ci.yml`／`release-ci.yml` 在 `macos-26` runner、Xcode 26.5 上跑 `./action-build.sh package|archive`：`action-install.sh` 下載 librime 預編譯包（`rime-<hash>-macOS-universal.tar.bz2` 與 `rime-deps-…`）和 Sparkle 2.6.2、用 plum 裝內建方案（含 `lotem/rime-octagram-data@hant`），然後 `make` → `xcodebuild`（arm64+x86_64 universal）。不需要自己編 librime 或 Boost。
- **打包**：`package/make_package` 用 `pkgbuild`＋`productbuild` 做 `.pkg`，安裝到 `/Library/Input Methods/Squirrel.app`；`scripts/postinstall` 會 `--register-input-source`、在 SharedSupport 裡 `--build`（預先部署）、`--enable-input-source`、`--select-input-source`。有 `DEV_ID` 時另外 `codesign`（hardened runtime）、`productsign`、`notarytool submit --wait`、`stapler staple`。
- **更新**：Sparkle 2（`SPUStandardUpdaterController`）。`Info.plist` 的 `SUFeedURL` 指向 `https://rime.github.io/release/squirrel/appcast.xml`，`SUPublicEDKey` 是 EdDSA 公鑰；`package/make_archive` 用 `sign_update` 簽 `.pkg` 並產生 `appcast.xml`（正式／測試／本機三個頻道）；`SUEnableInstallerLauncherService` 讓 Sparkle 能裝 `.pkg`。
- **資料夾**：使用者資料夾寫死在 `sources/Main.swift`：`~/Library/Rime`；共用資料是 `.app/Contents/SharedSupport`；log 在 `$TMPDIR/rime.squirrel`；行程的工作目錄是 SharedSupport。
- **授權**：GPLv3（小狼毫也是）。

## 3. 兩條路比較

**A：fork 鼠鬚管。** **B：自己寫 Swift IMK 殼**（照 BlendKey 的樣子：SwiftPM、`IMKInputController` 子類別、SwiftUI 候選窗、安裝到 `~/Library/Input Methods`），透過 librime 的 C API（`rime_api.h`、`rime_get_api()`）載入我們的方案和 Lua。

| | A：fork 鼠鬚管 | B：自製殼（參考 BlendKey） |
|---|---|---|
| librime＋lua/octagram/predict | 已內建（下載 librime 官方 macOS universal 預編譯包） | 一樣下載同一個預編譯包（`dist/lib/librime.1.dylib`、`dist/lib/rime-plugins/*.dylib`、`dist/include/rime_api.h`），放進 `Contents/Frameworks/` 和 `Contents/Frameworks/rime-plugins/`，自己處理 rpath（`@executable_path/../Frameworks`）和簽章。不用在 CI 編 librime；想鎖定外掛版本才需要自己編（多 15–25 分鐘） |
| 行內組字（preview） | `style/inline_preedit: true` + `inline_candidate: true`（= 小狼毫的 `preedit_type: preview`）；終端機可用 `no_inline` | 自己用 `setMarkedText` 組字、設游標和底線 |
| 精簡泡泡、直式清單 | 泡泡的內容全在 Lua（`asus_compact.lua`），不分前端；直式 = `candidate_list_layout: stacked`；藏「1」用 `hilited_candidate_label_color` 等於反白底色（同小狼毫的作法） | 完全自由，可以畫得跟華碩一模一樣（藍色讀音泡泡、灰色建議泡泡），但全部要自己畫、自己算位置 |
| 候選窗樣式 | 圓角、陰影、顏色、字型、深淺色主題都有設定；形狀和版面受限於鼠鬚管的 `SquirrelView` | SwiftUI（BlendKey 的 `CandidatePanel.swift` 可借） |
| 中／英提示 | 鼠鬚管在切換 option 時會顯示狀態（`show_notifications_when`、選單列圖示） | BlendKey 的 `ModeHUD.swift` 可借 |
| Sparkle 自動更新 | 已有，只要換 feed URL、EdDSA 金鑰 | BlendKey 沒有；要自己加 Sparkle（有 SwiftPM 套件） |
| 打包／安裝 | `.pkg` 裝到 `/Library/Input Methods`（要管理員密碼），postinstall 自動註冊、部署、啟用 | BlendKey：`swift build` + 腳本組 `.app`、ad-hoc 簽章、裝到 `~/Library/Input Methods`（不用密碼），發佈用 zip + 雙擊 `.command` 安裝程式（先 `xattr -dr com.apple.quarantine`） |
| 需要 Xcode？ | 要完整 Xcode（`xcodebuild`；CI runner 已內建） | 只要 Command Line Tools（`swift build`） |
| 未簽章時的 Gatekeeper | 從網路下載的未簽章 `.pkg` 會被擋，要到「隱私權與安全性」按「強制打開」；在自己 Mac 上編的沒有隔離屬性，不受影響 | 同左；BlendKey 用 `.command` 去掉隔離屬性，但 `.command` 本身第一次也要按「強制打開」 |
| 授權 | GPLv3（島輝要公開原始碼；Windows 的小狼毫 fork 本來就是） | 自由選；BlendKey 是 MIT，借用要保留版權聲明。**不能**抄鼠鬚管的程式碼（GPL） |
| 工作量 | 改名＋設定＋CI：小。主要時間在 Mac 上實測 | 鼠鬚管的 Swift 有 3,600 行，處理了一堆 IMK 邊角（遠端桌面送來 keyCode 0 的 flagsChanged、Caps Lock 順序、修飾鍵放開順序、行內候選造成的當機、多螢幕位置、深色模式）。自製殼估計 1,500–3,000 行，外加長尾的 App 相容問題 |
| 風險 | 跟上游 rebase；pbxproj 改名要小心；被 Xcode 版本綁住 | IMK 在不同 App（Chrome、Word、Electron、終端機）的行內組字行為各異，要一個一個修；自己維護更新、部署、log、選單 |

**推薦 A。** 理由：我們真正的差異化（混打、精簡泡泡、學習、更正）都在 Lua，前端只要「把 librime 的結果忠實畫出來」，鼠鬚管已經做到；A 一開始就有 CI、`.pkg`、Sparkle，和 Windows 端的作法一致。B 留作備案：如果在 Mac 上實測發現鼠鬚管的候選窗無法接近華碩的樣子（例如要「讀音泡泡」和「建議泡泡」分開兩個框），再評估；可以先在 A 的 fork 裡借 BlendKey 的 ModeHUD 這類獨立元件（MIT 程式碼放進 GPL 專案沒問題，反過來不行）。

## 4. 方案 A：fork 要改的東西

新 repo（例如 `TimLin-1231/daohui-mac`），由 `rime/squirrel` fork：

1. **識別碼**：`PRODUCT_BUNDLE_IDENTIFIER`（pbxproj 兩處）、`Info.plist` 的 `TISInputSourceID` 與 `ComponentInputModeDict`（只留一個繁中模式，例如 `<id>.Hant`，`TISIntendedLanguage: zh-Hant`）、`sources/InputSource.swift` 的模式列舉、`SquirrelApplicationDelegate.swift` 裡 `hasPrefix("im.rime.inputmethod.Squirrel")` 兩處、`InputMethodConnectionName`、`package/make_package` 的 `BUNDLE_IDENTIFIER`、`package/Distribution` 的 pkg-ref／choice id。
2. **名稱與路徑**：`CFBundleName`／執行檔名（`Squirrel` → `DaoHui`）、`Main.swift` 的 `appDir`（`/Library/Input Methods/DaoHui.app`）、`scripts/postinstall`、`Makefile` 的 `SQUIRREL_APP_ROOT`、`Squirrel-component.plist`、`resources/InfoPlist.xcstrings`（顯示名稱「島輝」）、`distribution_name`／`distribution_code_name`／`app_name`（`rime.daohui`）、log 目錄 `$TMPDIR/rime.daohui`、`DistributedNotificationCenter` 的通知名稱（`SquirrelReloadNotification` 等五個，不改的話 `--reload` 會同時叫到鼠鬚管）。
3. **使用者資料夾**：`Main.swift` 的 `userDir`（見第 5 節）。
4. **設定檔名**：`SquirrelConfig.swift` 開的是 `squirrel` 設定（`squirrel.yaml`／`squirrel.custom.yaml`）。可以改成 `daohui`，或保留（使用者資料夾已經分開，不會衝突）。
5. **圖示**：`Rime.icon`（macOS 26 Icon Composer 格式）、`Assets.xcassets`、`resources/rime.pdf`（選單列模板圖示）、`RimeIcon.icns` — 由 `D:\Document\輸入法\branding` 那邊提供。
6. **Sparkle**：用 `generate_keys` 產生新的 EdDSA 金鑰，公鑰寫進 `SUPublicEDKey`、私鑰放 GitHub Secrets；`SUFeedURL` 改成自己的（GitHub Pages，例如 `https://timlin-1231.github.io/daohui/appcast.xml`）；`package/make_archive` 的下載網址、標題、頻道。
7. **資料**：把本 repo 的 `rime/` 公開檔案放進 SharedSupport（見第 5 節）；`data/squirrel.yaml` 換成島輝的外觀和 `app_options`；`default.yaml` 的 `schema_list` 只留 `asus_zhuyin`。我們的方案 `__include: bopomofo_tw.schema:/`、詞典 import terra_pinyin，這兩個已在 plum 的預設套件裡（`preset-packages.conf`），不用另外加；`SQUIRREL_BUNDLED_RECIPES` 保留 octagram 資料。
8. **CI**：把兩個 workflow 的產物名稱、release 檔名改掉；拿掉 nightly 的 `rime/squirrel` 判斷。

## 5. 資料放哪裡

librime-lua 找 Lua 模組的順序是「使用者資料夾/lua」再「共用資料夾/lua」（`src/modules.cc`），詞典的 import 檔也是先找使用者資料夾。所以：

- **App 內的共用資料（`DaoHui.app/Contents/SharedSupport`，唯讀、隨更新換掉）**：`asus_zhuyin.schema.yaml`、`terra_pinyin.asus.dict.yaml`、`terra_pinyin.mcb.dict.yaml`、`terra_pinyin.tw.dict.yaml`、`grammar.yaml`、`lua/*.lua`（含產生的 `asus_zhuyin_data.lua`）、`opencc/asus_*`、`default.yaml`、`daohui.yaml`（外觀），以及安裝時 postinstall 預先部署的 `build/`。
- **使用者資料夾**：`*.custom.yaml`（例如 `asus_lang/raw_keys: true`）、`terra_pinyin.personal.dict.yaml`（個人詞，不進 git、不進安裝檔）、`shortcuts.txt`、`emails.txt`、學到的資料（`*.userdb`）、`build/`。
- **語言模型 `wanxiang-lts-zh-hant.gram`（410 MB，CC-BY-4.0）**：放進 `.pkg` 會讓每次更新都很大。建議放使用者資料夾，第一次安裝時下載（或沿用 `tools/fetch_models.py` 的來源）。待決。
- **使用者資料夾位置**：鼠鬚管是 `~/Library/Rime`。建議島輝用自己的 **`~/Library/DaoHui`**：同一台 Mac 也裝鼠鬚管時，兩個輸入法共用一個資料夾會互相改 `default.custom.yaml`、搶同一個 userdb 的鎖。缺點是 Rime 社群的說明文件都寫 `~/Library/Rime`。待決。
- **從 Windows 搬學習資料**：在 Windows 用 Rime 的「同步使用者資料」或 `tools/backup_learn.py` 匯出 `*.userdb.txt`，Mac 上放進使用者資料夾的 `sync/` 再同步；個人詞檔直接複製。

## 6. 外觀設定對照（`weasel.custom.yaml` → 鼠鬚管）

| 小狼毫 | 鼠鬚管 |
|---|---|
| `style/horizontal: false` | `style/candidate_list_layout: stacked` |
| `style/inline_preedit: true`、`style/preedit_type: preview` | `style/inline_preedit: true`、`style/inline_candidate: true` |
| `style/label_format: "%s"` | `style/candidate_format: "[label] [candidate] [comment]"` |
| `style/font_face: "Microsoft JhengHei UI"` | `style/font_face: "PingFang TC"`（label／comment 同） |
| `style/layout/corner_radius`、`round_corner` | `style/corner_radius`、`hilited_corner_radius` |
| `style/layout/margin_*`、`spacing`、`candidate_spacing`、`hilite_padding_*` | `style/border_width`、`border_height`、`line_spacing`、`spacing` |
| `style/layout/shadow_*` | `style/shadow_size` |
| `style/color_scheme`、`color_scheme_dark` | 同名 |
| `show_notifications`、`show_notifications_time` | `show_notifications_when: appropriate`（在 `daohui.yaml` 頂層） |
| `display_tray_icon` | `status_icon/show` |
| `ascii_tip_follow_cursor`、`hover_type` | 沒有 |
| `color_format: argb` | 沒有；一律 `0xAABBGGRR`：`0xFF006CE1`（藍）→ `0xFFE16C00`，`0xFF5F6B7A` → `0xFF7A6B5F`，`0xFFD7E7FB` → `0xFFFBE7D7`，`0xFFF0EFEC` → `0xFFECEFF0`，`0xFFB9C7D8` → `0xFFD8C7B9`；灰色不變 |
| `hilited_label_color`（藏「1」） | `hilited_candidate_label_color` 設成和 `hilited_candidate_back_color` 一樣 |
| `app_options/code.exe: { asus_en_noauto: true }` | `app_options/com.microsoft.VSCode: { asus_en_noauto: true }`（bundle id 對照見第 1 節） |
| — | `keyboard_layout: default`（強制 ABC 鍵位，大千對應不會因為使用者用了別的英文鍵盤而錯；BlendKey 也這樣做） |

## 7. 建置與 GitHub Actions

**每次 push（commit-ci）**，`macos-26`（或 `macos-15`）runner：

1. checkout 島輝 mac fork（含子模組 plum、Sparkle）＋本 repo（取 `rime/` 公開檔案）。
2. `action-install.sh`：下載 librime 1.16.0/1.17.0 macOS universal 預編譯包（含三個外掛）、Sparkle、plum 方案（bopomofo、terra-pinyin、octagram 資料）。
3. 複製 `rime/` 公開檔案到 `data/`，由 `package/add_data_files` 加進 Xcode 專案的 SharedSupport。
4. `make package`：`xcodebuild` universal → `DaoHui.pkg`（Xcode 預設 ad-hoc 簽章，未公證）。上傳成 artifact。
5. **在 runner 上跑 Lua 測試**：把 `tools/rime_harness.py` 移植到 macOS（第 1 節），載入剛編好的 `DaoHui.app/Contents/Frameworks/librime.1.dylib`，跑 `tests/test_*.py`（略過只屬於 Windows 的 `test_stage16.py` 具名管道）。這一步不需要安裝輸入法、不需要螢幕，可以證明 Lua 在 macOS 的 librime（`get_time_ms`、`package.config`、`os.clock` 都是 macOS 的）上行為一樣。限制：`.gram` 要先下載（用 `actions/cache` 存起來）；`lexicon/personal_words.txt`、`tests/eval/private/` 不在 git，CI 用空的個人詞檔、跳過私人評估集。

**打 tag（release-ci）**：同上，再（有憑證時）`codesign` → `productsign` → `notarytool submit --wait` → `stapler staple`；`sign_update` 用 Secrets 裡的 EdDSA 私鑰簽 `.pkg`、產生 `appcast.xml`，發 GitHub Release 並把 appcast 推到 GitHub Pages。

**費用**：公開 repo 的 GitHub 代管 runner 免費。私人 repo 用方案內含的分鐘數，macOS 分鐘數扣得比 Linux 快很多（以往官方是 10 倍；2026-01 起 macOS 價格約 US$0.062/分鐘，請以 docs.github.com 為準）。一次建置約 10–15 分鐘。

## 8. 在 Mac 上要做的事（使用者）

**準備（一次）**
1. 從 App Store 安裝 Xcode（鼠鬚管目前指定 26.5），開一次同意授權，然後 `sudo xcode-select -s /Applications/Xcode.app`。
2. 安裝 Homebrew（可選，只有要跑 swiftlint 或自己編 librime 才需要）；`git` 隨 Xcode 附帶。
3. （建議）在 Mac 上也裝 Claude Code：它能自己跑建置、看 Xcode 錯誤、讀 `$TMPDIR/rime.daohui/*.log`、在 Mac 的 librime 上跑 Python 測試、修 Lua 再重部署，速度比來回傳截圖快很多。它不能代你點系統設定、輸入管理員密碼或登出登入，這些還是要你來。

**建置與安裝（每次）**
```sh
git clone --recursive https://github.com/TimLin-1231/daohui-mac.git
cd daohui-mac
./action-build.sh package              # 下載 librime＋外掛、Sparkle、方案資料，編 universal，產生 package/DaoHui.pkg
sudo installer -pkg package/DaoHui.pkg -target /
```
（不要用鼠鬚管的 `make install-release`：它會把 `/Library/Input Methods` 的擁有者改成你。）

第一次安裝後：「系統設定 › 鍵盤 › 輸入方式 › 編輯 › ＋ › 繁體中文 › 島輝」；如果清單裡找不到，登出再登入一次（BlendKey 的作者向 Apple 確認過，新輸入法第一次可能要重新登入才會出現）。

**放個人資料**：把 Windows 上的 `rime/terra_pinyin.personal.dict.yaml`、`shortcuts.txt`、`emails.txt`、`*.gram`、`asus_zhuyin.custom.yaml` 複製到 `~/Library/DaoHui/`，選單列圖示 › 重新部署。

**要在 Mac 上親手測的清單**
- 連按兩下 Shift 切中英、單按 Shift 不切；Shift+字母打大寫不會誤切。
- Tab 切換最後一個詞的中英、連按兩下 Tab 補完。
- 行內預覽首選、精簡泡泡的讀音與按鍵、↓ 直式清單、1–9、Esc。
- `` ` `` 符號小鍵盤、Ctrl+標點、英文後的半形標點。
- 英文模式的猜字與空白鍵修正；在「終端機」直接輸出、在 VS Code 不修正（`app_options`）。
- 在 Safari、Chrome、Word、LINE、VS Code、終端機各打一段，看行內組字和候選窗位置。
- Ctrl+Space（會被系統攔走）、Caps Lock 的行為。
- 學到的字重開機後還在；log 沒有 Lua 錯誤：`grep -i lua $TMPDIR/rime.daohui/*.ERROR*`。
- 在 Mac 上確認時鐘來源：log 沒有 `asus_clock.lua: no millisecond clock` 的警告就代表用的是 `get_time_ms`。

## 9. 沒有 Mac 能測什麼、一定要 Mac 的是什麼

**不用 Mac**
- 所有 Lua 行為：Windows 上的 `tests/`（同一份 Lua）。平台分支由 `tests/test_clock.py` 用替身測：假裝不是 Windows（`package.config`）、假的 `io.popen` 輸出（GNU date、BSD date、zsh）、有或沒有 `get_time_ms`、具名管道只在 Windows 開。
- 新版 librime：librime 1.16/1.17 也有 Windows 版預編譯包（`rime-<hash>-Windows-msvc-x64.7z`）。讓 `rime_harness.py` 能指定 `rime.dll` 路徑，就能在 Windows 上用跟 Mac 同版本的 librime／librime-lua 跑整套測試（待做）。
- 建置、打包、Lua 測試：GitHub Actions 的 macOS runner（第 7 節）。

**一定要 Mac**
- 真實按鍵：flagsChanged 送來的 Shift 按下／放開與時間、Caps Lock、Ctrl+Space 被系統攔截、fn 鍵。
- 各 App 的行內組字、候選窗位置與外觀、深淺色、多螢幕、全螢幕 App。
- 輸入法註冊、登出登入、`.pkg` 安裝、Gatekeeper 提示、Sparkle 更新流程（未簽章時尤其要實測）。
- 「安全輸入」（密碼欄、終端機的「安全鍵盤輸入」）時的行為；手感與速度。

**關於 `asus_clock.lua` 的子行程後備**：只在 librime-lua 沒有 `get_time_ms` 且不是 Windows 時才用（鼠鬚管 1.1.x 不會走到）。它只在被計時的鍵（Shift 按下／放開、組字中的 Tab）開一次 `/bin/sh` → `zsh`（macOS）或 `date`（Linux），一般打字不會；切分迴圈保護用的是不會卡住的 `fast_ms()`（CPU 時間，對忙碌的迴圈來說跟牆上時間一樣快）。我在 Windows 上沒辦法量 macOS 開子行程要多久；在 Mac 上可以這樣量：`time (for i in {1..100}; do /bin/zsh -fc 'zmodload zsh/datetime && print -r -- $EPOCHREALTIME' >/dev/null; done)`，除以 100 就是每次 Shift／Tab 多出的延遲。

## 10. 簽章、公證、Gatekeeper、費用

- **只給自己用**：在自己的 Mac 上編、自己裝，檔案沒有「隔離」屬性，Gatekeeper 不會擋，不需要任何付費帳號。Apple Silicon 上的程式至少要 ad-hoc 簽章，Xcode／`swift build` 會自動做。
- **給別人下載、不簽章**：下載的 `.pkg`／`.app` 會被標記隔離，Gatekeeper 擋下。macOS 15 起不能再用「按住 Control 點開」略過，要先打開一次被擋、再到「系統設定 › 隱私權與安全性」按「強制打開」並輸入密碼（或在終端機 `xattr -dr com.apple.quarantine`）。鼠鬚管的 nightly、BlendKey 都是這樣發佈的。Sparkle 的更新用 EdDSA 簽章驗證，跟 Apple 簽章無關；未簽章 App 透過 Sparkle 裝 `.pkg` 的流程要在 Mac 上實測。
- **Developer ID 簽章＋公證**：需要 Apple Developer Program，**每年 US$99**（非營利／教育機構可申請減免）。拿到「Developer ID Application」（簽 App）和「Developer ID Installer」（簽 pkg）兩張憑證後，`notarytool` 公證免費、通常幾分鐘。好處：使用者雙擊就能裝、沒有警告；Sparkle 更新更順。可以在 GitHub Actions 裡做（憑證 `.p12` 和 App Store Connect API 金鑰放 Secrets）。

## 11. 待決問題

1. **方案**：同意用 A（fork 鼠鬚管）嗎？還是要先做 B 的小原型比較候選窗？
2. **bundle id／輸入法 id**：例如 `io.github.timlin1231.daohui`（不需要擁有網域，但要獨一無二）。顯示名稱只用「島輝」，還是「島輝 DaoHui」？
3. **使用者資料夾**：`~/Library/DaoHui`（建議，可以和鼠鬚管並存）還是 `~/Library/Rime`？Windows 版要不要對應地用 `%APPDATA%\DaoHui`？
4. **語言模型 410 MB**：放進安裝檔，還是第一次啟動時下載？
5. **個人詞與學習資料**：Mac 上要不要跟 Windows 同步？以前決定「只存本機」——搬一次就好，還是要定期同步（Rime 的 sync 資料夾放在哪）？
6. **發佈**：只給自己用（不用付費）、還是要給別人下載？要的話付 US$99/年簽章公證嗎？
7. **repo**：新開 `daohui-mac`（GPLv3，原始碼要公開）可以嗎？本 repo 是公開還是私人（影響 CI 的 macOS 分鐘數費用）？
8. **更新頻道**：Sparkle appcast 放 GitHub Pages 可以嗎？要不要「測試版」頻道？
9. **寫作小幫手**（停頓後續寫、Ctrl+Alt+W）：macOS 版要做嗎？要做的話 Swift 端要加一條通道（Unix socket）取代具名管道和 F24，且需要「輔助使用」權限。
10. **Ctrl+Space**：在 Mac 上要改成別的組合，還是請使用者關掉系統的輸入法切換快速鍵？
11. **Mac 的硬體與系統版本**：Apple Silicon 還是 Intel？macOS 幾版？（鼠鬚管 1.1.x 需要 macOS 13 以上；Xcode 26.5 需要較新的 macOS，若太舊就改在 CI 上建置、Mac 只負責安裝測試。）
