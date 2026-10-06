# 小狼毫 0.17.4 修補：焦點離開時保留沒送出的字

原版小狼毫在切換視窗、點到別處、或應用程式結束組字時，會把還沒送出的字刪掉。
`keep-composition.patch` 改成保留畫面上已顯示的字（跟微軟注音、華碩輸入法一樣），並清掉 Rime 裡的輸入。
只改 `WeaselTSF`（`weasel.dll` / `weaselx64.dll`），不動 Rime 的設定和學習資料。

## 重新編譯（例如重灌之後）
1. 原始碼：`git clone --depth 1 --branch 0.17.4 https://github.com/rime/weasel.git D:\src\weasel`，然後 `git apply keep-composition.patch`。
2. 需要 VS 2022 Build Tools（C++、ATL）和 Boost 1.84.0（`D:\src\deps\boost_1_84_0`）。
   - Boost 1.84 的 b2 認不得 MSVC 14.44，所以 serialization / wserialization / thread / chrono 四個靜態函式庫是直接用 `cl` 編的。
   - 沒裝 MFC：`include\afxres.h` 放一個只 `#include <winres.h>` 的替代檔。
   - 這個環境有設 `NoDefaultCurrentDirectoryInExePath`，批次檔裡要先清掉。
3. `msbuild weasel.sln /t:WeaselTSF /p:Configuration=Release /p:Platform=x64`（以及 `Win32`），產出在 `output\`。

## 安裝／還原
- 安裝：以系統管理員執行 `install_tsf.ps1`（原檔改名成 `*.orig-<時間>` 留在原資料夾）。已經開著的程式要重開才會用到新版。
- 還原：以系統管理員執行 `uninstall_tsf.ps1`；備份也在 `backups\20261006-131301-weasel-tsf`。
