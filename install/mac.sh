#!/bin/bash
# 島輝 DaoHui for macOS: install or update for the logged-in user.
#
#   curl -fsSL https://raw.githubusercontent.com/TimLin-1231/rime-asus-zhuyin/main/install/mac.sh | bash
#
# Downloads the newest DaoHui.app (the zip of update/appcast-macos-app.xml,
# the same file the app's own updater installs), checks its SHA-256, puts it
# in ~/Library/Input Methods, adds and selects the input source, then
# downloads the language model into ~/Library/DaoHui. Files fetched by curl
# carry no quarantine flag, so Gatekeeper never asks, and nothing here needs
# an administrator password (except removing a pre-1.1.2.21 copy in
# /Library/Input Methods, if there is one). Running it again updates the app
# and resumes or repairs the model download.
#
#   ... | bash -s -- --no-model     skip the language model (410 MB)
#   ... | bash -s -- --uninstall    remove DaoHui.app (keeps ~/Library/DaoHui)
#
# Environment overrides (tests): DAOHUI_APPCAST_URL, DAOHUI_MODEL_URL.

# Everything runs from main, called on the last line: when the script is piped
# into bash, a download cut short runs nothing.

APPCAST_URL="${DAOHUI_APPCAST_URL:-https://raw.githubusercontent.com/TimLin-1231/rime-asus-zhuyin/main/update/appcast-macos-app.xml}"
# The language model the evaluations were run with (CC-BY-4.0, amzxyz/RIME-LMDG,
# the LTS file of 2026-10-06), kept in this project's releases because
# upstream replaces the LTS file in place. Same file as MODEL_URL /
# MODEL_SHA256 in app/daohui-settings/src-tauri/src/config.rs.
MODEL_URL="${DAOHUI_MODEL_URL:-https://github.com/TimLin-1231/rime-asus-zhuyin/releases/download/model-wanxiang-lts-20261006/wanxiang-lts-zh-hant.gram}"
MODEL_SHA256="53b678ce17f1d80c75dd51976b697a5d1fc2cd6a13670652069b227b40157c98"
MODEL_SIZE=410679340
MODEL_NAME="wanxiang-lts-zh-hant.gram"

USER_METHODS="$HOME/Library/Input Methods"
APP="$USER_METHODS/DaoHui.app"
SYSTEM_APP="/Library/Input Methods/DaoHui.app"
USER_DIR="$HOME/Library/DaoHui"

say() { printf '%s\n' "$*"; }
step() { printf '\n▶ %s\n' "$*"; }
fail() {
    printf '\n✖ %s\n' "$*" >&2
    say "  可以再貼一次同一行指令重試；還是不行的話，請把上面的文字截圖，到 https://github.com/TimLin-1231/rime-asus-zhuyin/issues 回報。" >&2
    exit 1
}

sha256_of() { /usr/bin/shasum -a 256 "$1" | /usr/bin/awk '{print $1}'; }
size_of() { /usr/bin/stat -f%z "$1" 2>/dev/null || echo 0; }

# The first value of attribute $1 in the appcast (the newest item comes first).
appcast_attr() { /usr/bin/sed -n "s/.*[[:space:]]$1=\"\\([^\"]*\\)\".*/\\1/p" "$2" | /usr/bin/head -n 1; }

installed_version() {
    /usr/libexec/PlistBuddy -c "Print :CFBundleVersion" "$APP/Contents/Info.plist" 2>/dev/null || true
}

quit_daohui() {
    if [ -x "$APP/Contents/MacOS/DaoHui" ]; then
        "$APP/Contents/MacOS/DaoHui" --quit > /dev/null 2>&1 || true
    fi
    /usr/bin/killall DaoHui > /dev/null 2>&1 || true
}

remove_system_copy() {
    [ -d "$SYSTEM_APP" ] || return 0
    step "移除裝在 /Library/Input Methods 的舊版島輝"
    say "  這一步需要這台 Mac 的登入密碼（輸入時畫面不會顯示任何字，打完按 return）。"
    if /usr/bin/sudo /bin/rm -rf "$SYSTEM_APP"; then
        say "  已移除。"
    else
        say "  沒有移除（之後可以再執行一次這個安裝指令）。選單裡可能會看到兩個島輝，選哪個都可以。"
    fi
}

install_app() {
    local work="$1" appcast url version sha zip current
    step "查詢最新版本"
    appcast="$work/appcast.xml"
    /usr/bin/curl -fsSL --retry 3 -o "$appcast" "$APPCAST_URL" || fail "連不上更新伺服器（GitHub），請確認網路後再試一次。"
    url=$(appcast_attr url "$appcast")
    version=$(appcast_attr 'sparkle:version' "$appcast")
    sha=$(appcast_attr 'daohui:sha256' "$appcast")
    [ -n "$url" ] && [ -n "$version" ] && [ ${#sha} -eq 64 ] || fail "看不懂更新資訊（$APPCAST_URL）。"
    current=$(installed_version)
    if [ "$current" = "$version" ]; then
        say "  島輝 $version 已經是最新版。"
        return 0
    fi
    if [ -n "$current" ]; then
        say "  目前是 $current，更新到 $version。"
    else
        say "  最新版是 $version。"
    fi

    step "下載島輝 $version"
    zip="$work/DaoHui.zip"
    /usr/bin/curl -fL --retry 3 --progress-bar -o "$zip" "$url" || fail "下載失敗：$url"
    [ "$(sha256_of "$zip")" = "$sha" ] || fail "下載的檔案不完整（SHA-256 不符），請再試一次。"

    step "安裝到 ~/Library/Input Methods"
    /usr/bin/ditto -x -k "$zip" "$work/unzipped" || fail "解壓縮失敗。"
    [ -x "$work/unzipped/DaoHui.app/Contents/MacOS/DaoHui" ] || fail "壓縮檔裡沒有 DaoHui.app。"
    /bin/mkdir -p "$USER_METHODS" || fail "無法建立 $USER_METHODS"
    quit_daohui
    /bin/rm -rf "$APP.new"
    /usr/bin/ditto "$work/unzipped/DaoHui.app" "$APP.new" || fail "無法寫入 $USER_METHODS"
    /bin/rm -rf "$APP"
    /bin/mv "$APP.new" "$APP" || fail "無法寫入 $APP"
    /usr/bin/xattr -dr com.apple.quarantine "$APP" > /dev/null 2>&1 || true
    say "  完成。"
}

# Register, enable and select the input source (the app's own installer;
# the package's postinstall does the same).
select_input_source() {
    local out
    step "加入並選用「島輝」輸入法"
    out=$("$APP/Contents/MacOS/DaoHui" --update-input-source 2>&1) || true
    if printf '%s' "$out" | /usr/bin/grep -q "Selection succeeds"; then
        say "  已經切換到島輝。"
        SELECTED=1
    else
        say "  已經加入，但這次沒能自動切換（剛裝好的輸入法有時要重新登入才會出現）。"
        SELECTED=0
    fi
}

install_model() {
    local model part have
    model="$USER_DIR/$MODEL_NAME"
    part="$model.part"
    step "語言模型（讓整句選字更準，約 410 MB）"
    /bin/mkdir -p "$USER_DIR" || fail "無法建立 $USER_DIR"
    if [ "$(size_of "$model")" = "$MODEL_SIZE" ] && [ "$(sha256_of "$model")" = "$MODEL_SHA256" ]; then
        say "  已經下載過了。"
        return 0
    fi
    have=$(size_of "$part")
    if [ "$have" -gt "$MODEL_SIZE" ]; then
        /bin/rm -f "$part"
        have=0
    fi
    if [ "$have" -lt "$MODEL_SIZE" ]; then
        [ "$have" -gt 0 ] && say "  接著上次下載到的地方繼續。"
        if ! /usr/bin/curl -fL --retry 5 --retry-delay 3 -C - --progress-bar -o "$part" "$MODEL_URL"; then
            say "  語言模型沒有下載完。島輝已經可以打字，只是整句選字比較不準；"
            say "  網路順的時候再貼一次同一行指令，會從中斷的地方繼續下載。"
            MODEL_OK=0
            return 0
        fi
    fi
    if [ "$(sha256_of "$part")" != "$MODEL_SHA256" ]; then
        /bin/rm -f "$part"
        say "  下載的模型檔不完整，已經刪掉，請再貼一次同一行指令重新下載。"
        MODEL_OK=0
        return 0
    fi
    /bin/mv -f "$part" "$model" || fail "無法寫入 $model"
    say "  完成。"
    # Load it now (it is found in the user folder at the next deploy).
    "$APP/Contents/MacOS/DaoHui" --reload > /dev/null 2>&1 || true
}

uninstall() {
    step "移除島輝"
    if [ -x "$APP/Contents/MacOS/DaoHui" ]; then
        "$APP/Contents/MacOS/DaoHui" --disable-input-source > /dev/null 2>&1 || true
    fi
    quit_daohui
    /bin/rm -rf "$APP" "$APP.new"
    remove_system_copy
    say ""
    say "✔ 島輝已經移除。重新登入後，輸入法選單裡就不會再出現。"
    say "  你的個人資料（學到的字詞、設定、語言模型）還留在 $USER_DIR，"
    say "  不需要的話可以在 Finder 用「前往 › 前往檔案夾…」打開這個資料夾刪掉。"
}

main() {
    local no_model=0
    for arg in "$@"; do
        case "$arg" in
            --uninstall) UNINSTALL=1 ;;
            --no-model) no_model=1 ;;
            *) fail "不認得的選項：$arg" ;;
        esac
    done
    [ "$(/usr/bin/id -u)" != 0 ] || fail "請不要用 sudo 執行：島輝會裝在你自己的帳號裡，不需要管理員權限。"
    [ "$(/usr/bin/uname -s)" = Darwin ] || fail "這個指令只能在 Mac 上執行。Windows 請下載安裝檔：https://github.com/TimLin-1231/rime-asus-zhuyin#安裝"

    say "【島輝】輸入法 安裝程式"
    if [ "${UNINSTALL:-0}" = 1 ]; then
        uninstall
        return 0
    fi

    local os_major
    os_major=$(/usr/bin/sw_vers -productVersion | /usr/bin/cut -d. -f1)
    [ "${os_major:-0}" -ge 13 ] || fail "島輝需要 macOS 13（Ventura）或更新的版本，這台 Mac 是 $(/usr/bin/sw_vers -productVersion)。"

    # (global: the EXIT trap runs after main has returned)
    WORK=$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/daohui-install.XXXXXX") || fail "無法建立暫存資料夾。"
    trap '/bin/rm -rf "$WORK"' EXIT

    MODEL_OK=1
    install_app "$WORK"
    remove_system_copy
    select_input_source
    if [ "$no_model" = 1 ]; then
        MODEL_OK=0
    else
        install_model
    fi

    say ""
    say "✔ 島輝裝好了（版本 $(installed_version)）。"
    say ""
    say "怎麼開始用："
    if [ "${SELECTED:-0}" = 1 ]; then
        say "  • 現在就可以打字了。螢幕右上角選單列的輸入法圖示會是「島」。"
    else
        say "  • 點螢幕右上角選單列的輸入法圖示，選「島輝」。"
        say "    找不到的話，先登出再登入（或重新開機），再到「系統設定 › 鍵盤 › 輸入方式 › 編輯… › ＋ › 繁體中文」加入「島輝」。"
    fi
    say "  • 切換輸入法：按 🌐（fn）鍵或 Control＋空白鍵。"
    say "  • 中文／英文切換：連按兩下 Shift。"
    if [ "$MODEL_OK" != 1 ]; then
        say "  • 語言模型還沒裝好：之後再貼一次同一行指令就會補上。"
    fi
    say "  • 以後島輝會在背景自動更新，不用再執行這個指令。"
    say ""
    say "使用說明：https://github.com/TimLin-1231/rime-asus-zhuyin#安裝"
}

main "$@"
