# 島輝 updates: the appcast format

The settings app (page 檢查更新) reads a Sparkle / WinSparkle style RSS feed:

    https://raw.githubusercontent.com/TimLin-1231/rime-asus-zhuyin/main/update/appcast.xml

(`APPCAST_URL` in `src-tauri/src/config.rs`; for testing it can be overridden
with the environment variable `DAOHUI_APPCAST_URL`, https or `http://127.0.0.1`
only). A missing feed (HTTP 404) is shown as "更新資訊尚未發布", not as an error.

Nothing is downloaded or installed until the user presses 「下載並安裝」.

## Example

```xml
<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"
     xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"
     xmlns:daohui="https://github.com/TimLin-1231/rime-asus-zhuyin/ns/appcast">
  <channel>
    <title>島輝</title>
    <item>
      <title>島輝 0.2.0</title>
      <pubDate>Sat, 31 Oct 2026 12:00:00 +0800</pubDate>
      <sparkle:version>0.2.0</sparkle:version>
      <sparkle:shortVersionString>0.2.0</sparkle:shortVersionString>
      <sparkle:releaseNotesLink xml:lang="zh-TW">https://github.com/TimLin-1231/rime-asus-zhuyin/releases/tag/v0.2.0</sparkle:releaseNotesLink>
      <description><![CDATA[
        <ul>
          <li>模型下載可以暫停與續傳</li>
          <li>部署更快</li>
        </ul>
      ]]></description>
      <enclosure url="https://github.com/TimLin-1231/rime-asus-zhuyin/releases/download/v0.2.0/DaoHui-0.2.0-setup.exe"
                 sparkle:os="windows-x64"
                 sparkle:installerArguments=""
                 length="31457280"
                 type="application/octet-stream"
                 daohui:sha256="3f7a…64 hex digits…" />
      <enclosure url="https://github.com/TimLin-1231/rime-asus-zhuyin/releases/download/v0.2.0/DaoHui-0.2.0.pkg"
                 sparkle:os="macos"
                 length="28311552"
                 type="application/octet-stream"
                 daohui:sha256="9c01…64 hex digits…" />
    </item>
  </channel>
</rss>
```

## What is read

| Element / attribute | Use |
| --- | --- |
| `item > sparkle:version` or `enclosure@sparkle:version` | **Required.** The product version, compared with the settings app's own version (`Cargo.toml` `version`). Dotted numbers with an optional SemVer pre-release (`0.3.0-beta.1`); a leading `v` is allowed. The enclosure attribute wins over the item element. |
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
running version.

## Install flow

1. The installer is downloaded to `%TEMP%\DaoHui-update\<file name>`
   (macOS: `$TMPDIR/DaoHui-update/`), as `<file>.part` first.
2. Size (if `length` is given) and SHA-256 are checked; on mismatch the file is
   deleted and nothing is launched.
3. Windows: the `.exe` is started with `ShellExecute` ("open"), so an installer
   that requires administrator rights gets its UAC prompt; `installerArguments`
   are passed along. macOS: `open <file>.pkg` (Installer.app).
4. The settings app quits after about 2.5 s so the installer can replace it.

## Making a release

1. Bump `version` in `app/daohui-settings/src-tauri/Cargo.toml` (and the IME's
   own version) and build the installers.
2. Upload them to a GitHub release.
3. `certutil -hashfile DaoHui-x.y.z-setup.exe SHA256` (Windows) or
   `shasum -a 256 DaoHui-x.y.z.pkg` (macOS) and put the hashes into
   `update/appcast.xml`, newest item first; commit to `main`.
