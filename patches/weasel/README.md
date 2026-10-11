# 小狼毫 0.17.4 修補：焦點離開時保留沒送出的字

原版小狼毫在切換視窗、或應用程式結束組字時，會把還沒送出的字刪掉。
`keep-composition.patch` 改成保留畫面上已顯示的字（跟微軟注音、華碩輸入法一樣），並清掉 Rime 裡的輸入。
只改 `WeaselTSF`，不動 Rime 的設定和學習資料。

## 目前狀態（2026-10-11）
- **已安裝並實測有效**：`tools/tsf_test.py` 在記事本裡自動打字、切視窗、讀回文字，6 項全過（切到別的視窗保留、快速來回切換只保留一次、接著打字不重複、點別處或按 Ctrl+A 不遺失也不重複）。
- 這支測試只涵蓋傳統記事本。Word、瀏覽器、VS Code 跟 TSF 的互動方式不同，還要手動試。
- **10/6 的第一版其實從來沒生效**，兩個原因：
  1. 裝錯位置。Windows 載入的是 `C:\Windows\System32\weasel.dll`（64 位元程式）和 `C:\Windows\SysWOW64\weasel.dll`（32 位元程式）；`C:\Program Files\Rime\weasel-0.17.4\` 裡的 `weaselx64.dll`／`weasel.dll` 只是安裝程式的來源檔。第一版只換了後者。
  2. 邏輯有洞。第一次處理後就把「這段字要保留」的旗標清掉，而失去焦點時 Windows 會通知不只一次，後到的那次就把字刪了。

## 第二版改了什麼
- 應用程式或 TSF 自己結束組字時（`OnCompositionTerminated`，記事本切視窗就是走這條），直接用當下的寫入權限處理：行內顯示的字留著，不再另外排一個之後才執行的結束動作。
- 同一段組字只會要求結束一次；後到的通知直接略過。
- 還沒執行的結束動作不會誤把後來新開的組字關掉。
- 第一個按鍵之後就離開焦點，也算行內顯示。
- 診斷用：啟動程式前設環境變數 `WEASEL_TSF_LOG=<檔案路徑>`，那個程式裡的組字事件會寫進檔案（沒設就完全不記）。

## 重新編譯（例如重灌之後）
1. 原始碼：`git clone --depth 1 --branch 0.17.4 https://github.com/rime/weasel.git D:\src\weasel`，然後 `git apply keep-composition.patch`。
2. 需要 VS 2022 Build Tools（C++、ATL）和 Boost 1.84.0（`D:\src\deps\boost_1_84_0`）。
   - Boost 1.84 的 b2 認不得 MSVC 14.44，所以 serialization / wserialization / thread / chrono 四個靜態函式庫是直接用 `cl` 編的。
   - 沒裝 MFC：`include\afxres.h` 放一個只 `#include <winres.h>` 的替代檔。
   - 這個環境有設 `NoDefaultCurrentDirectoryInExePath`，批次檔裡要先清掉，而且要用完整路徑執行批次檔。
3. `msbuild weasel.sln /t:WeaselTSF /p:Configuration=Release /p:Platform=x64`（以及 `Win32`），產出在 `output\`：`weaselx64.dll`（64 位元）和 `weasel.dll`（32 位元）。

## 安裝／還原
- 安裝：以系統管理員、64 位元 PowerShell 執行 `install_tsf_system.ps1`。它把 `System32\weasel.dll` 和 `SysWOW64\weasel.dll` 改名成 `weasel.dll.orig-<時間>` 留在原地，再放進新檔。已經開著的程式要重開才會用到新版。
- 還原成原廠：以系統管理員執行 `uninstall_tsf_system.ps1`（用最早的那份備份）。
- 驗證：`python tools\tsf_test.py`（約 1 分鐘，會搶走鍵盤焦點，期間不要碰鍵盤滑鼠）。
- 小狼毫更新或重新安裝會把系統資料夾裡的檔案換回它自己的版本，之後要重裝一次修補。
- `install_tsf.ps1`／`uninstall_tsf.ps1` 是第一版留下的，換的是 Program Files 裡那兩個沒被載入的檔，保留只為了記錄。
