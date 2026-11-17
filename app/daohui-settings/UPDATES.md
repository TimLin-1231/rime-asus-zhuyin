# 島輝 updates: the appcast format

The settings app (page 檢查更新) reads a Sparkle / WinSparkle style RSS feed,
one per platform:

| Platform | Feed | Installer (release asset) |
| --- | --- | --- |
| Windows | `https://raw.githubusercontent.com/TimLin-1231/rime-asus-zhuyin/main/update/appcast.xml` | `daohui-0.17.4.<n>-installer.exe` (NSIS), in the `TimLin-1231/daohui-weasel` release `v0.17.4.<n>` |
| macOS | `https://raw.githubusercontent.com/TimLin-1231/rime-asus-zhuyin/main/update/appcast-macos.xml` | `DaoHui-1.1.2.<n>.pkg`, in the `TimLin-1231/daohui-squirrel` release `v1.1.2.<n>` |

Each Windows release also carries a byte-identical copy named
`DaoHui-Setup.exe`: the README's download link is
`releases/latest/download/DaoHui-Setup.exe`. The feeds always point at the
versioned name. The release steps are in `docs/dev/release-windows.md` and
`docs/dev/release-mac.md`.

(`APPCAST_URL` in the `platform` module of `src-tauri/src/config.rs`; for
testing it can be overridden with the environment variable
`DAOHUI_APPCAST_URL`, https or `http://127.0.0.1` only). A missing feed
(HTTP 404) is shown as "更新資訊尚未發布", not as an error.

Nothing is downloaded or installed until the user presses 「下載並安裝」.

Each feed is read by two programs: this app and the input method's own
updater. On Windows that is WinSparkle in DaoHuiServer (the Weasel fork;
`DAOHUI_APPCAST_URL` in `include/DaoHuiBrand.h`); on macOS it is Sparkle in
DaoHui 1.1.2.20 and older in `/Library/Input Methods/DaoHui.app` (the Squirrel
fork), whose `SUFeedURL` is `appcast-macos.xml`. A feed has to satisfy both
readers (see "macOS feed" below for what Sparkle needs).

Since 1.1.2.21 the package moves the input method into the login user's
`~/Library/Input Methods`, and its Sparkle reads `appcast-macos-app.xml`
instead: the same item with the app zipped (`DaoHui-x.y.z.zip`), which Sparkle
installs in the background without a password. This app does not read that
feed (it installs `.pkg` files only); it looks for the installed input method
in `~/Library/Input Methods` first, then in `/Library/Input Methods`
(`config::info_plist_candidates`, `deploy::deployer_candidates`).

## Windows feed (`update/appcast.xml`)

```xml
<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"
     xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"
     xmlns:daohui="https://github.com/TimLin-1231/rime-asus-zhuyin/ns/appcast">
  <channel>
    <title>島輝 Windows</title>
    <item>
      <title>島輝 0.17.4.17</title>
      <pubDate>Tue, 03 Nov 2026 02:23:00 +0000</pubDate>
      <sparkle:version>0.17.4.17</sparkle:version>
      <sparkle:shortVersionString>0.17.4.17</sparkle:shortVersionString>
      <sparkle:releaseNotesLink xml:lang="zh-TW">https://github.com/TimLin-1231/daohui-weasel/releases/tag/v0.17.4.17</sparkle:releaseNotesLink>
      <description><![CDATA[
        <ul>
          <li>模型下載可以暫停與續傳</li>
          <li>部署更快</li>
        </ul>
      ]]></description>
      <enclosure url="https://github.com/TimLin-1231/daohui-weasel/releases/download/v0.17.4.17/daohui-0.17.4.17-installer.exe"
                 sparkle:version="0.17.4.17"
                 sparkle:os="windows"
                 sparkle:installerArguments="/S"
                 length="17693415"
                 type="application/octet-stream"
                 daohui:sha256="593c…64 hex digits…" />
    </item>
  </channel>
</rss>
```

(The shape of the real items in `update/appcast.xml`. `sparkle:os="windows"`
is understood by the WinSparkle that DaoHui bundles and by this app;
`installerArguments="/S"` makes the update a silent install.)

## macOS feed (`update/appcast-macos.xml`)

```xml
<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"
     xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"
     xmlns:daohui="https://github.com/TimLin-1231/rime-asus-zhuyin/ns/appcast">
  <channel>
    <title>島輝 macOS</title>
    <item>
      <title>島輝 1.1.2.24</title>
      <pubDate>Sat, 31 Oct 2026 06:20:17 +0000</pubDate>
      <sparkle:version>1.1.2.24</sparkle:version>
      <sparkle:shortVersionString>1.1.2.24</sparkle:shortVersionString>
      <sparkle:minimumSystemVersion>13.0.0</sparkle:minimumSystemVersion>
      <sparkle:releaseNotesLink xml:lang="zh-TW">https://github.com/TimLin-1231/daohui-squirrel/releases/tag/v1.1.2.24</sparkle:releaseNotesLink>
      <description><![CDATA[
        <ul>
          <li>模型下載可以暫停與續傳</li>
        </ul>
      ]]></description>
      <enclosure url="https://github.com/TimLin-1231/daohui-squirrel/releases/download/v1.1.2.24/DaoHui-1.1.2.24.pkg"
                 sparkle:os="macos"
                 sparkle:edSignature="…base64 EdDSA signature from Sparkle's sign_update…"
                 length="26445117"
                 type="application/octet-stream"
                 daohui:sha256="1248…64 hex digits…" />
    </item>
  </channel>
</rss>
```

What Sparkle (in the input method) needs on top of what this app reads, as in
Squirrel's `package/make_archive`:

- `sparkle:edSignature` and `length` from Sparkle's `sign_update DaoHui-x.y.z.pkg`
  (the key pair whose public half is `SUPublicEDKey` in the input method's
  Info.plist). This app ignores the signature and checks `daohui:sha256`.
- `sparkle:version` is compared by Sparkle and by this app with the input
  method's `CFBundleVersion` (this app falls back to its own `Cargo.toml`
  version only when it finds no installed input method), so both offer the
  same update.
- `sparkle:minimumSystemVersion` `13.0.0` (Squirrel's deployment target, also
  `bundle.macOS.minimumSystemVersion` of this app). Sparkle skips the item on
  older systems; this app ignores the element.
- Unknown attributes such as `daohui:sha256` are ignored by Sparkle.

Both programs see the same item; whichever installs it first wins, the other
then reports it as up to date.

## What is read

| Element / attribute | Use |
| --- | --- |
| `item > sparkle:version` or `enclosure@sparkle:version` | **Required.** The product version, compared with the installed input method's version (`updates::installed_version`: Windows `DisplayVersion` under `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\DaoHui`, macOS `CFBundleVersion` of the installed `DaoHui.app`); the settings app's own version (`Cargo.toml` `version`) only when that cannot be read. Dotted numbers with an optional SemVer pre-release (`0.3.0-beta.1`); a leading `v` is allowed. The enclosure attribute wins over the item element. |
| `sparkle:shortVersionString` (item element or enclosure attribute) | Optional display version. |
| `item > title`, `pubDate` | Shown as is. |
| `item > description` | Release notes. HTML (usually in CDATA) is converted to plain text: tags dropped, `<li>` become "•" lines, `<script>`/`<style>` removed. Never rendered as HTML. |
| `item > sparkle:releaseNotesLink` | Optional "完整更新說明" link (https only). With several `xml:lang` variants a `zh*` one is preferred. |
| `item > sparkle:channel` | Items with any channel (e.g. `beta`) are **ignored** in v1. |
| `enclosure@url` | Installer URL, https only. Its file extension must be one the platform can launch: `.exe` (Windows, NSIS) or `.pkg` (macOS). |
| `enclosure@sparkle:os` | `windows-x64` or `windows` (Windows x64), `windows-arm64`, `windows-x86`, `macos`. If missing, the platform is inferred from the file extension. |
| `enclosure@length` | Optional; if given, the download must have exactly this size. |
| `enclosure@daohui:sha256` | **Required to install.** SHA-256 of the installer, 64 hex digits (case-insensitive). Namespace `https://github.com/TimLin-1231/rime-asus-zhuyin/ns/appcast`; any attribute whose local name is `sha256` is accepted. Without a valid hash the app shows the release but refuses to install it. |
| `enclosure@sparkle:installerArguments` | Windows only: command-line arguments passed to the installer (e.g. `/S` for a silent NSIS install). Leave empty for the normal installer UI. |

Ignored: `sparkle:edSignature` / `sparkle:dsaSignature` (the SHA-256 plus HTTPS
is the integrity check in v1), `sparkle:minimumSystemVersion`,
`sparkle:criticalUpdate`, `sparkle:phasedRolloutInterval`.

The newest usable item (highest version among items without a channel that
have an enclosure for this platform) is offered when it is newer than the
running version. Each feed normally holds only its own platform's
enclosures; a feed with both still works, since enclosures are picked by
`sparkle:os` and file extension.

## Install flow

1. The installer is downloaded to `%TEMP%\DaoHui-update\<file name>`
   (macOS: `$TMPDIR/DaoHui-update/`), as `<file>.part` first.
2. Size (if `length` is given) and SHA-256 are checked; on mismatch the file is
   deleted and nothing is launched.
3. Windows: the `.exe` is started with `ShellExecute` ("open"), so an installer
   that requires administrator rights gets its UAC prompt; `installerArguments`
   are passed along. macOS: `open -b com.apple.installer <file>.pkg`, i.e.
   Installer.app (whatever app is the default for `.pkg`), which shows the
   package's UI and asks for an administrator password.
4. The settings app quits after about 2.5 s so the installer can replace it.

## Making a release

1. Bump `version` in `app/daohui-settings/src-tauri/Cargo.toml` and the input
   methods' own versions (Windows installer; macOS `CFBundleVersion` /
   `CFBundleShortVersionString`), and build the installers. Unsigned builds
   of this app (`DaoHuiSettings.exe`, universal `島輝設定.app`) are the
   artifacts of `.github/workflows/settings-app.yml`.
2. Upload them to a GitHub release: Windows `daohui-0.17.4.<n>-installer.exe`
   and its copy `DaoHui-Setup.exe` to `TimLin-1231/daohui-weasel`; macOS
   `DaoHui-1.1.2.<n>.pkg` and `.zip` to `TimLin-1231/daohui-squirrel`. The
   exact steps: `docs/dev/release-windows.md`, `docs/dev/release-mac.md`.
3. Hashes: `certutil -hashfile daohui-0.17.4.<n>-installer.exe SHA256`
   (Windows), `shasum -a 256 DaoHui-1.1.2.<n>.pkg` (macOS); for the `.pkg`
   also `sign_update DaoHui-1.1.2.<n>.pkg` (Sparkle) for
   `sparkle:edSignature` and `length`.
4. Add the new item, newest first, to `update/appcast.xml` (the `.exe`) and to
   `update/appcast-macos.xml` (the `.pkg`); commit to `main`. The macOS release
   workflow (daohui-squirrel) writes both macOS feeds; copy
   `appcast-macos-app.xml` (the `.zip`) into `update/` as well.
