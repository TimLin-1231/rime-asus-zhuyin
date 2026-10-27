//! 檢查更新: Sparkle / WinSparkle appcast parsing, version selection, and
//! download (SHA-256 verified) + launch of the installer -- only when the
//! user clicks. The expected feed format is documented in UPDATES.md.
//!
//! Feeds: `update/appcast.xml` (Windows, `.exe`) and `update/appcast-macos.xml`
//! (macOS, `.pkg`; also read by the input method's own Sparkle updater).

use std::path::Path;
use std::sync::atomic::Ordering;
use std::time::Duration;

use serde::Serialize;
use tauri::{AppHandle, Emitter, State};

use crate::config::{self, DAOHUI_NS, SPARKLE_NS};
use crate::download::{self, Job, ProgressEvent};
use crate::{version, AppState, Busy};

pub const PROGRESS_EVENT: &str = "update-progress";

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Release {
    pub version: String,
    pub display_version: String,
    pub title: String,
    /// Plain text (HTML release notes are converted).
    pub notes: String,
    pub notes_link: Option<String>,
    pub pub_date: Option<String>,
    pub url: String,
    pub length: Option<u64>,
    pub sha256: Option<String>,
    pub installer_args: Option<String>,
    pub file_name: String,
}

#[derive(Debug, Clone, Default)]
pub struct Enclosure {
    pub url: String,
    pub version: Option<String>,
    pub short_version: Option<String>,
    pub os: Option<String>,
    pub length: Option<u64>,
    pub sha256: Option<String>,
    pub installer_args: Option<String>,
}

#[derive(Debug, Clone, Default)]
pub struct Item {
    pub title: Option<String>,
    pub version: Option<String>,
    pub short_version: Option<String>,
    pub description: Option<String>,
    pub notes_link: Option<String>,
    pub pub_date: Option<String>,
    pub channel: Option<String>,
    pub enclosures: Vec<Enclosure>,
}

type Node<'a, 'i> = roxmltree::Node<'a, 'i>;

fn is_el(n: &Node, ns: Option<&str>, name: &str) -> bool {
    n.is_element() && n.tag_name().name() == name && n.tag_name().namespace() == ns
}

fn text_of(n: Node) -> Option<String> {
    let s: String = n.descendants().filter(|d| d.is_text()).filter_map(|d| d.text()).collect();
    let s = s.trim();
    (!s.is_empty()).then(|| s.to_string())
}

fn child_text(n: Node, ns: Option<&str>, name: &str) -> Option<String> {
    n.children().find(|c| is_el(c, ns, name)).and_then(text_of)
}

fn sparkle_attr(n: Node, name: &str) -> Option<String> {
    n.attribute((SPARKLE_NS, name))
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty())
}

/// 64 hex digits, lowercased; anything else is treated as missing.
fn normalize_sha(v: &str) -> Option<String> {
    let v = v.trim().to_ascii_lowercase();
    (v.len() == 64 && v.chars().all(|c| c.is_ascii_hexdigit())).then_some(v)
}

/// Release notes link: prefer a Chinese one when several languages are given.
fn notes_link(item: Node) -> Option<String> {
    let links: Vec<(Option<&str>, String)> = item
        .children()
        .filter(|c| is_el(c, Some(SPARKLE_NS), "releaseNotesLink"))
        .filter_map(|c| {
            let lang = c.attribute(("http://www.w3.org/XML/1998/namespace", "lang"));
            text_of(c).map(|t| (lang, t))
        })
        .collect();
    let zh = links.iter().find(|(l, _)| l.is_some_and(|l| l.to_ascii_lowercase().starts_with("zh")));
    let plain = links.iter().find(|(l, _)| l.is_none());
    zh.or(plain).or(links.first()).map(|(_, t)| t.clone())
}

pub fn parse_appcast(xml: &str) -> Result<Vec<Item>, String> {
    let doc = roxmltree::Document::parse(xml).map_err(|e| format!("更新資訊格式錯誤：{e}"))?;
    let root = doc.root_element();
    if root.tag_name().name() != "rss" {
        return Err("更新資訊格式錯誤：不是 RSS（appcast）。".into());
    }
    let channel = root
        .children()
        .find(|c| is_el(c, None, "channel"))
        .ok_or("更新資訊格式錯誤：缺少 <channel>。")?;
    let items = channel
        .children()
        .filter(|c| is_el(c, None, "item"))
        .map(|item| {
            let enclosures = item
                .children()
                .filter(|c| is_el(c, None, "enclosure"))
                .filter_map(|e| {
                    let url = e.attribute("url")?.trim().to_string();
                    // Canonical: daohui:sha256; any attribute named sha256 is accepted.
                    let sha = e
                        .attribute((DAOHUI_NS, "sha256"))
                        .or_else(|| e.attributes().find(|a| a.name() == "sha256").map(|a| a.value()));
                    Some(Enclosure {
                        url,
                        version: sparkle_attr(e, "version"),
                        short_version: sparkle_attr(e, "shortVersionString"),
                        os: sparkle_attr(e, "os"),
                        length: e.attribute("length").and_then(|v| v.trim().parse().ok()).filter(|&n| n > 0),
                        sha256: sha.and_then(normalize_sha),
                        installer_args: sparkle_attr(e, "installerArguments"),
                    })
                })
                .collect();
            Item {
                title: child_text(item, None, "title"),
                version: child_text(item, Some(SPARKLE_NS), "version"),
                short_version: child_text(item, Some(SPARKLE_NS), "shortVersionString"),
                description: child_text(item, None, "description"),
                notes_link: notes_link(item),
                pub_date: child_text(item, None, "pubDate"),
                channel: child_text(item, Some(SPARKLE_NS), "channel"),
                enclosures,
            }
        })
        .collect();
    Ok(items)
}

fn url_extension(url: &str) -> Option<String> {
    let path = url.split(['?', '#']).next()?;
    let last = path.rsplit('/').next()?;
    let (_, ext) = last.rsplit_once('.')?;
    Some(ext.to_ascii_lowercase())
}

/// Last path segment, limited to safe characters.
fn file_name_for(url: &str, version: &str, ext: &str) -> String {
    let last = url.split(['?', '#']).next().and_then(|p| p.rsplit('/').next()).unwrap_or("");
    let clean: String = last
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() || matches!(c, '.' | '-' | '_') { c } else { '_' })
        .collect();
    let clean = clean.trim_start_matches('.').to_string();
    if clean.len() > ext.len() + 1 && clean.to_ascii_lowercase().ends_with(&format!(".{ext}")) {
        clean
    } else {
        format!("DaoHui-{version}-setup.{ext}")
    }
}

/// An enclosure we can install here: `sparkle:os` (if given) is one of
/// `os_values`, and the file type is one we can launch.
fn usable(enc: &Enclosure, os_values: &[&str], exts: &[&str]) -> bool {
    let ext_ok = url_extension(&enc.url).is_some_and(|e| exts.iter().any(|x| x.eq_ignore_ascii_case(&e)));
    let os_ok = enc
        .os
        .as_deref()
        .is_none_or(|os| os_values.iter().any(|o| o.eq_ignore_ascii_case(os)));
    ext_ok && os_ok
}

/// Plain text from release-notes HTML (tags dropped, lists as bullets).
pub fn html_to_text(html: &str) -> String {
    if !html.contains('<') {
        return tidy(&decode_entities(html), false);
    }
    let mut out = String::new();
    let mut rest = html;
    let mut skip_until: Option<&str> = None;
    while !rest.is_empty() {
        if let Some(lt) = rest.find('<') {
            if skip_until.is_none() {
                let text = decode_entities(&rest[..lt]);
                for (i, part) in text.split_whitespace().enumerate() {
                    if (i > 0 || text.starts_with(char::is_whitespace))
                        && !out.ends_with([' ', '\n'])
                        && !out.is_empty()
                    {
                        out.push(' ');
                    }
                    out.push_str(part);
                }
                if text.ends_with(char::is_whitespace) && !text.trim().is_empty() {
                    out.push(' ');
                }
            }
            let after = &rest[lt + 1..];
            let gt = after.find('>').map(|g| g + 1).unwrap_or(after.len());
            let tag = after[..gt].trim_end_matches('>').trim();
            rest = &after[gt..];
            let closing = tag.starts_with('/');
            let name: String = tag
                .trim_start_matches('/')
                .chars()
                .take_while(|c| c.is_ascii_alphanumeric())
                .collect::<String>()
                .to_ascii_lowercase();
            if let Some(end) = skip_until {
                if closing && name == end {
                    skip_until = None;
                }
                continue;
            }
            match name.as_str() {
                "script" | "style" if !closing => {
                    skip_until = Some(if name == "script" { "script" } else { "style" })
                }
                "br" => out.push('\n'),
                "li" if !closing => {
                    out.push('\n');
                    out.push_str("• ");
                }
                "p" | "div" | "ul" | "ol" | "tr" | "table" | "h1" | "h2" | "h3" | "h4" | "h5"
                | "h6" | "blockquote" | "pre" | "hr" => out.push('\n'),
                _ => {}
            }
        } else {
            if skip_until.is_none() {
                let text = decode_entities(rest);
                if text.starts_with(char::is_whitespace) && !out.ends_with([' ', '\n']) {
                    out.push(' ');
                }
                out.push_str(&text.split_whitespace().collect::<Vec<_>>().join(" "));
            }
            break;
        }
    }
    tidy(&out, true)
}

/// Trims lines and collapses runs of blank lines.
fn tidy(s: &str, _from_html: bool) -> String {
    let mut out: Vec<&str> = Vec::new();
    for line in s.lines().map(str::trim) {
        if line.is_empty() && out.last().is_none_or(|l| l.is_empty()) {
            continue;
        }
        out.push(line);
    }
    while out.last().is_some_and(|l| l.is_empty()) {
        out.pop();
    }
    out.join("\n")
}

fn decode_entities(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut rest = s;
    while let Some(amp) = rest.find('&') {
        out.push_str(&rest[..amp]);
        let after = &rest[amp + 1..];
        let semi = after.find(';').filter(|&i| i <= 10);
        let decoded = semi.and_then(|i| {
            let ent = &after[..i];
            let c = match ent {
                "amp" => Some('&'),
                "lt" => Some('<'),
                "gt" => Some('>'),
                "quot" => Some('"'),
                "apos" => Some('\''),
                "nbsp" => Some(' '),
                _ if ent.starts_with("#x") || ent.starts_with("#X") => {
                    u32::from_str_radix(&ent[2..], 16).ok().and_then(char::from_u32)
                }
                _ if ent.starts_with('#') => ent[1..].parse().ok().and_then(char::from_u32),
                _ => None,
            };
            c.map(|c| (c, i))
        });
        match decoded {
            Some((c, i)) => {
                out.push(c);
                rest = &after[i + 1..];
            }
            None => {
                out.push('&');
                rest = after;
            }
        }
    }
    out.push_str(rest);
    out
}

#[derive(Debug, Default)]
pub struct Selection {
    /// Newest version usable on this platform (even if not newer).
    pub latest: Option<String>,
    /// Set when `latest` is newer than the running version.
    pub release: Option<Release>,
}

pub fn select_update(items: &[Item], current: &str, os_values: &[&str], exts: &[&str]) -> Selection {
    let mut best: Option<(String, &Item, &Enclosure)> = None;
    for item in items.iter().filter(|i| i.channel.is_none()) {
        for enc in item.enclosures.iter().filter(|e| usable(e, os_values, exts)) {
            let Some(ver) = enc.version.clone().or_else(|| item.version.clone()) else { continue };
            if version::parse(&ver).is_none() {
                continue;
            }
            if best.as_ref().is_none_or(|(b, _, _)| version::is_newer(&ver, b)) {
                best = Some((ver, item, enc));
            }
        }
    }
    let Some((ver, item, enc)) = best else { return Selection::default() };
    let display = enc
        .short_version
        .clone()
        .or_else(|| item.short_version.clone())
        .unwrap_or_else(|| ver.clone());
    let ext = url_extension(&enc.url).unwrap_or_default();
    let release = version::is_newer(&ver, current).then(|| Release {
        title: item.title.clone().unwrap_or_else(|| format!("{} {display}", config::PRODUCT_NAME)),
        notes: item.description.as_deref().map(html_to_text).unwrap_or_default(),
        notes_link: item.notes_link.clone().filter(|l| l.starts_with("https://")),
        pub_date: item.pub_date.clone(),
        url: enc.url.clone(),
        length: enc.length,
        sha256: enc.sha256.clone(),
        installer_args: enc.installer_args.clone(),
        file_name: file_name_for(&enc.url, &ver, &ext),
        display_version: display.clone(),
        version: ver,
    });
    Selection { latest: Some(display), release }
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdateCheck {
    pub current: String,
    /// "available", "up_to_date", "no_feed" (404), "empty" (no items) or
    /// "no_platform" (nothing for this OS).
    pub status: &'static str,
    pub latest: Option<String>,
    pub release: Option<Release>,
    pub feed_url: String,
    pub checked_at: String,
}

/// CFBundleVersion from an XML Info.plist (None for a binary plist).
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
fn plist_bundle_version(text: &str) -> Option<String> {
    let after = &text[text.find("<key>CFBundleVersion</key>")? + "<key>CFBundleVersion</key>".len()..];
    let start = after.find("<string>")? + "<string>".len();
    let end = after[start..].find("</string>")? + start;
    let v = after[start..end].trim();
    (!v.is_empty()).then(|| v.to_string())
}

/// The installed DaoHui's version: the feed is about the input method, not
/// this app (whose own version is only the fallback).
pub(crate) fn installed_version() -> Option<String> {
    #[cfg(target_os = "windows")]
    {
        crate::winutil::installed_daohui_version()
    }
    #[cfg(target_os = "macos")]
    {
        std::fs::read_to_string(config::INFO_PLIST).ok().and_then(|t| plist_bundle_version(&t))
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    {
        None
    }
}

#[tauri::command]
pub async fn check_update(app: AppHandle, state: State<'_, AppState>) -> Result<UpdateCheck, String> {
    let current = installed_version().unwrap_or_else(|| app.package_info().version.to_string());
    let feed_url = config::appcast_url();
    download::check_url(&feed_url)?;
    let client = download::client(&current)?;
    let resp = client
        .get(&feed_url)
        .timeout(Duration::from_secs(20))
        .send()
        .await
        .map_err(|e| format!("無法連線到更新伺服器：{}", download::error_chain(&e)))?;
    let checked_at = chrono::Local::now().format("%Y/%m/%d %H:%M").to_string();
    let code = resp.status();
    let mut result = UpdateCheck {
        current: current.clone(),
        status: "no_feed",
        latest: None,
        release: None,
        feed_url,
        checked_at,
    };
    if code.as_u16() == 404 {
        *state.release.lock().unwrap() = None;
        return Ok(result);
    }
    if !code.is_success() {
        return Err(format!("無法取得更新資訊（HTTP {}）。", code.as_u16()));
    }
    let bytes = resp
        .bytes()
        .await
        .map_err(|e| format!("無法下載更新資訊：{}", download::error_chain(&e)))?;
    let items = parse_appcast(&String::from_utf8_lossy(&bytes))?;
    let sel = select_update(&items, &current, config::APPCAST_OS, config::INSTALLER_EXTENSIONS);
    result.status = if sel.release.is_some() {
        "available"
    } else if sel.latest.is_some() {
        "up_to_date"
    } else if items.is_empty() {
        "empty"
    } else {
        "no_platform"
    };
    result.latest = sel.latest;
    result.release = sel.release.clone();
    *state.release.lock().unwrap() = sel.release;
    Ok(result)
}

#[cfg(target_os = "windows")]
fn launch_installer(path: &Path, args: Option<&str>) -> Result<(), String> {
    crate::winutil::shell_open(path, args)
}

/// `open -b com.apple.installer <file>.pkg`: Installer.app shows the
/// package's own UI and asks for an administrator password; `open` returns
/// as soon as Installer.app has the file.
#[cfg(target_os = "macos")]
fn launch_installer(path: &Path, _args: Option<&str>) -> Result<(), String> {
    let out = std::process::Command::new("/usr/bin/open")
        .arg("-b")
        .arg(config::INSTALLER_APP_BUNDLE_ID)
        .arg(path)
        .output()
        .map_err(|e| format!("無法開啟安裝程式：{e}"))?;
    if out.status.success() {
        Ok(())
    } else {
        let err = String::from_utf8_lossy(&out.stderr);
        Err(format!("無法開啟安裝程式：{}", err.trim()))
    }
}

#[cfg(not(any(target_os = "windows", target_os = "macos")))]
fn launch_installer(_path: &Path, _args: Option<&str>) -> Result<(), String> {
    Err("這個作業系統不支援自動安裝。".into())
}

/// Downloads the release found by the last check, verifies its SHA-256,
/// launches the installer and then quits so files can be replaced.
#[tauri::command]
pub async fn install_update(app: AppHandle, state: State<'_, AppState>) -> Result<String, String> {
    let release = state.release.lock().unwrap().clone();
    let release = release.ok_or("請先檢查更新。")?;
    let sha = release.sha256.clone().ok_or(
        "這個版本的更新資訊沒有提供 SHA-256，無法驗證安裝程式，已停止。請到網站手動下載。",
    )?;
    download::check_url(&release.url)?;
    let _busy = Busy::acquire(&state.update_busy).ok_or("正在下載更新。")?;
    state.update_cancel.store(false, Ordering::SeqCst);

    let dest = config::update_temp_dir().join(&release.file_name);
    let client = download::client(&app.package_info().version.to_string())?;
    let job = Job {
        url: &release.url,
        dest: &dest,
        sha256: Some(&sha),
        length: release.length,
        resume: false,
    };
    let emitter = app.clone();
    let fetched = download::fetch(&client, &job, &state.update_cancel, move |p| {
        let _ = emitter.emit(PROGRESS_EVENT, ProgressEvent::download(p));
    })
    .await;
    if let Err(e) = fetched {
        let _ = app.emit(PROGRESS_EVENT, ProgressEvent::phase("done"));
        return Err(e.to_string());
    }
    let _ = app.emit(PROGRESS_EVENT, ProgressEvent::phase("launch"));
    if let Err(e) = launch_installer(&dest, release.installer_args.as_deref()) {
        let _ = app.emit(PROGRESS_EVENT, ProgressEvent::phase("done"));
        return Err(e);
    }
    let handle = app.clone();
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(Duration::from_millis(2500)).await;
        handle.exit(0);
    });
    Ok(format!("已啟動 {} {} 的安裝程式，設定程式即將關閉。", config::PRODUCT_NAME, release.display_version))
}

#[tauri::command]
pub fn cancel_update_download(state: State<'_, AppState>) {
    state.update_cancel.store(true, Ordering::SeqCst);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bundle_version_from_info_plist() {
        let plist = "<dict>\n\t<key>CFBundleShortVersionString</key>\n\t<string>1.1.2</string>\n\
                     \t<key>CFBundleVersion</key>\n\t<string>1.1.2.13</string>\n</dict>";
        assert_eq!(plist_bundle_version(plist).as_deref(), Some("1.1.2.13"));
        assert_eq!(plist_bundle_version("bplist00..."), None);
        assert_eq!(crate::version::compare("0.17.4.5", "0.17.4.7"), std::cmp::Ordering::Less);
        assert_eq!(crate::version::compare("1.1.2.13", "1.1.2.14"), std::cmp::Ordering::Less);
    }

    const FEED: &str = r#"<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"
     xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"
     xmlns:daohui="https://github.com/TimLin-1231/rime-asus-zhuyin/ns/appcast">
  <channel>
    <title>島輝</title>
    <item>
      <title>島輝 0.2.0</title>
      <pubDate>Sat, 31 Oct 2026 12:00:00 +0800</pubDate>
      <sparkle:version>0.2.0</sparkle:version>
      <description><![CDATA[
        <h2>新功能</h2>
        <ul><li>模型下載 &amp; 續傳</li><li>更快的<b>部署</b></li></ul>
        <p>感謝回報。</p>
      ]]></description>
      <sparkle:releaseNotesLink>https://example.com/notes/en</sparkle:releaseNotesLink>
      <sparkle:releaseNotesLink xml:lang="zh-TW">https://example.com/notes/zh</sparkle:releaseNotesLink>
      <enclosure url="https://github.com/x/releases/download/v0.2.0/DaoHui-0.2.0-setup.exe"
                 sparkle:os="windows-x64"
                 sparkle:installerArguments="/S"
                 length="12345678"
                 type="application/octet-stream"
                 daohui:sha256="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" />
      <enclosure url="https://github.com/x/releases/download/v0.2.0/DaoHui-0.2.0.pkg"
                 sparkle:os="macos" length="2345"
                 daohui:sha256="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" />
    </item>
    <item>
      <title>島輝 0.3.0-beta.1</title>
      <sparkle:channel>beta</sparkle:channel>
      <enclosure url="https://x/DaoHui-0.3.0-beta.1-setup.exe" sparkle:version="0.3.0-beta.1" sparkle:os="windows-x64" />
    </item>
    <item>
      <title>島輝 0.1.5</title>
      <enclosure url="https://x/DaoHui-0.1.5-setup.exe?dl=1" sparkle:version="0.1.5" sparkle:shortVersionString="0.1.5 (春)" />
    </item>
  </channel>
</rss>"#;

    const WIN: &[&str] = &["windows-x64", "windows"];

    #[test]
    fn parses_sparkle_feed() {
        let items = parse_appcast(FEED).unwrap();
        assert_eq!(items.len(), 3);
        let a = &items[0];
        assert_eq!(a.version.as_deref(), Some("0.2.0"));
        assert_eq!(a.notes_link.as_deref(), Some("https://example.com/notes/zh"));
        assert_eq!(a.enclosures.len(), 2);
        let e = &a.enclosures[0];
        assert_eq!(e.os.as_deref(), Some("windows-x64"));
        assert_eq!(e.installer_args.as_deref(), Some("/S"));
        assert_eq!(e.length, Some(12345678));
        assert_eq!(e.sha256.as_deref(), Some("a".repeat(64).as_str())); // lowercased
        assert_eq!(items[1].channel.as_deref(), Some("beta"));
        assert_eq!(items[2].enclosures[0].sha256, None);
    }

    #[test]
    fn selects_newest_for_this_platform() {
        let items = parse_appcast(FEED).unwrap();
        let s = select_update(&items, "0.1.0", WIN, &["exe"]);
        let r = s.release.unwrap();
        assert_eq!(r.version, "0.2.0");
        assert_eq!(r.file_name, "DaoHui-0.2.0-setup.exe");
        assert_eq!(r.installer_args.as_deref(), Some("/S"));
        assert_eq!(r.notes, "新功能\n\n• 模型下載 & 續傳\n• 更快的部署\n\n感謝回報。");
        // Beta channel is ignored; up to date at 0.2.0.
        let s = select_update(&items, "0.2.0", WIN, &["exe"]);
        assert!(s.release.is_none());
        assert_eq!(s.latest.as_deref(), Some("0.2.0"));
        // macOS picks the .pkg.
        let m = select_update(&items, "0.1.0", &["macos"], &["pkg"]).release.unwrap();
        assert!(m.url.ends_with(".pkg"));
        assert_eq!(m.sha256.as_deref(), Some("b".repeat(64).as_str()));
        // Nothing for Linux.
        assert!(select_update(&items, "0.1.0", &["linux"], &["deb"]).latest.is_none());
    }

    #[test]
    fn enclosure_without_os_uses_extension() {
        let items = parse_appcast(FEED).unwrap();
        let only_old = &items[2..];
        let r = select_update(only_old, "0.1.0", WIN, &["exe"]).release.unwrap();
        assert_eq!(r.display_version, "0.1.5 (春)");
        assert_eq!(r.file_name, "DaoHui-0.1.5-setup.exe");
        assert!(select_update(only_old, "0.1.0", &["macos"], &["pkg"]).release.is_none());
    }

    /// An item of `update/appcast-macos.xml` as both Sparkle (in the input
    /// method) and this app read it.
    const MAC_FEED: &str = r#"<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"
     xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"
     xmlns:daohui="https://github.com/TimLin-1231/rime-asus-zhuyin/ns/appcast">
  <channel>
    <title>島輝 macOS</title>
    <item>
      <title>島輝 0.2.0</title>
      <sparkle:version>0.2.0</sparkle:version>
      <sparkle:minimumSystemVersion>13.0.0</sparkle:minimumSystemVersion>
      <enclosure url="https://github.com/x/releases/download/v0.2.0/DaoHui-0.2.0.pkg"
                 sparkle:os="macos"
                 sparkle:edSignature="c2lnbmF0dXJl"
                 length="28311552"
                 type="application/octet-stream"
                 daohui:sha256="CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC" />
    </item>
  </channel>
</rss>"#;

    #[test]
    fn macos_feed() {
        let items = parse_appcast(MAC_FEED).unwrap();
        let r = select_update(&items, "0.1.0", &["macos"], &["pkg"]).release.unwrap();
        assert_eq!(r.file_name, "DaoHui-0.2.0.pkg");
        assert_eq!(r.length, Some(28311552));
        assert_eq!(r.sha256.as_deref(), Some("c".repeat(64).as_str()));
        assert_eq!(r.installer_args, None);
        // Nothing in it for Windows.
        assert!(select_update(&items, "0.1.0", WIN, &["exe"]).latest.is_none());
    }

    #[test]
    fn feed_and_installer_per_platform() {
        #[cfg(target_os = "windows")]
        {
            assert!(config::APPCAST_URL.ends_with("/update/appcast.xml"));
            assert_eq!(config::INSTALLER_EXTENSIONS, &["exe"]);
        }
        #[cfg(target_os = "macos")]
        {
            assert!(config::APPCAST_URL.ends_with("/update/appcast-macos.xml"));
            assert_eq!(config::APPCAST_OS, &["macos"]);
            assert_eq!(config::INSTALLER_EXTENSIONS, &["pkg"]);
        }
        assert!(config::APPCAST_URL.starts_with("https://"));
    }

    #[test]
    fn rejects_bad_feeds() {
        assert!(parse_appcast("not xml").is_err());
        assert!(parse_appcast("<html><body/></html>").is_err());
        assert!(parse_appcast("<rss/>").is_err());
        assert!(parse_appcast("<rss><channel/></rss>").unwrap().is_empty());
        // 404 pages from GitHub raw are plain text.
        assert!(parse_appcast("404: Not Found").is_err());
    }

    #[test]
    fn html_and_names() {
        assert_eq!(html_to_text("a<br>b &lt;c&gt; &#x5cf6;&#36637;"), "a\nb <c> 島輝");
        assert_eq!(html_to_text("<p>x</p><script>alert(1)</script><p>y</p>"), "x\n\ny");
        assert_eq!(html_to_text("plain\n\n\n text"), "plain\n\ntext");
        assert_eq!(html_to_text("<p>a <i>b</i> c</p>"), "a b c");
        assert_eq!(file_name_for("https://x/a%20b/..evil.exe", "1.0", "exe"), "evil.exe");
        assert_eq!(file_name_for("https://x/download?id=3", "1.0", "exe"), "DaoHui-1.0-setup.exe");
        assert_eq!(url_extension("https://x/y/Setup.EXE?x=1"), Some("exe".into()));
        assert_eq!(normalize_sha("xyz"), None);
    }
}
