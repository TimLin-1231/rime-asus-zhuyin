//! 自動同步: runs `DaoHuiSettings --sync` without a window.
//!
//! Windows: a Task Scheduler task `DaoHuiSync` of the current user (no
//! administrator rights): at logon and once a day, only while the computer
//! is idle (a sync briefly stops the input method and redeploys).
//! macOS: a LaunchAgent `~/Library/LaunchAgents/io.github.timlin-1231.daohui.sync.plist`
//! (RunAtLoad = at login, StartInterval = daily).
//!
//! With `DAOHUI_AUTOSYNC_DRY_RUN=<folder>` nothing is registered: the task
//! XML / plist and the commands that would run are written to that folder.

use std::fs;
use std::path::{Path, PathBuf};
#[cfg(any(target_os = "windows", target_os = "macos"))]
use std::process::Command;

use serde::Serialize;

use crate::config::{self, ENV_AUTOSYNC_DRY_RUN, SYNC_AGENT_LABEL, SYNC_FLAG, SYNC_TASK_NAME};

/// Seconds between runs of the LaunchAgent.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
pub const AGENT_INTERVAL: u32 = 86400;

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Status {
    pub enabled: bool,
    pub available: bool,
    /// What is registered, for the page ("工作排程器「DaoHuiSync」").
    pub detail: String,
    pub dry_run: bool,
}

fn xml_escape(s: &str) -> String {
    s.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;").replace('"', "&quot;").replace('\'', "&apos;")
}

/// The Task Scheduler task (schema 1.2, Windows 7+). `user`: DOMAIN\name of
/// the current user; `start`: local date-time of the first daily run.
#[cfg_attr(not(target_os = "windows"), allow(dead_code))]
pub fn task_xml(exe: &str, user: &str, start: &str) -> String {
    let dir = xml_escape(exe.rfind(['\\', '/']).map_or("", |i| &exe[..i]));
    let (exe, user, start) = (xml_escape(exe), xml_escape(user), xml_escape(start));
    format!(
        r#"<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>島輝 DaoHui</Author>
    <Description>島輝輸入法：透過同步資料夾（Google 雲端硬碟）同步學習資料、個人詞與設定（DaoHuiSettings {SYNC_FLAG}）。可在「島輝 設定」的「同步」頁關閉。</Description>
    <URI>\{SYNC_TASK_NAME}</URI>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user}</UserId>
      <Delay>PT2M</Delay>
    </LogonTrigger>
    <CalendarTrigger>
      <StartBoundary>{start}</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <Duration>PT5M</Duration>
      <WaitTimeout>PT12H</WaitTimeout>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>true</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT30M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{exe}</Command>
      <Arguments>{SYNC_FLAG}</Arguments>
      <WorkingDirectory>{dir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"#
    )
}

/// What schtasks /XML reads: UTF-16 LE with a BOM.
#[cfg_attr(not(target_os = "windows"), allow(dead_code))]
pub fn utf16_with_bom(text: &str) -> Vec<u8> {
    let mut out = vec![0xFF, 0xFE];
    for u in text.replace("\r\n", "\n").replace('\n', "\r\n").encode_utf16() {
        out.extend(u.to_le_bytes());
    }
    out
}

/// The LaunchAgent: at login (RunAtLoad) and every `AGENT_INTERVAL` seconds.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
pub fn launch_agent_plist(exe: &str) -> String {
    let exe = xml_escape(exe);
    format!(
        r#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>{SYNC_AGENT_LABEL}</string>
	<key>ProgramArguments</key>
	<array>
		<string>{exe}</string>
		<string>{SYNC_FLAG}</string>
	</array>
	<key>RunAtLoad</key>
	<true/>
	<key>StartInterval</key>
	<integer>{AGENT_INTERVAL}</integer>
	<key>ProcessType</key>
	<string>Background</string>
	<key>LowPriorityIO</key>
	<true/>
	<key>Nice</key>
	<integer>10</integer>
</dict>
</plist>
"#
    )
}

fn exe_path() -> Result<String, String> {
    let exe = std::env::current_exe().map_err(|e| format!("找不到設定程式的位置：{e}"))?;
    let s = exe.display().to_string();
    let s = s.strip_prefix(r"\\?\").unwrap_or(&s).to_string();
    if s.contains("/AppTranslocation/") {
        return Err("請先把「島輝設定」移到「應用程式」資料夾，再開啟自動同步。".into());
    }
    Ok(s)
}

fn dry_run_dir() -> Option<PathBuf> {
    config::env_path(ENV_AUTOSYNC_DRY_RUN)
}

fn dry_run_log(dir: &Path, line: &str) -> Result<(), String> {
    use std::io::Write;
    fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    let mut f = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(dir.join("commands.txt"))
        .map_err(|e| e.to_string())?;
    writeln!(f, "{line}").map_err(|e| e.to_string())
}

// ---------------------------------------------------------------- Windows

#[cfg(target_os = "windows")]
fn task_file_name() -> String {
    format!("{SYNC_TASK_NAME}.xml")
}

#[cfg(target_os = "windows")]
fn schtasks(args: &[&str]) -> Result<std::process::Output, String> {
    use std::os::windows::process::CommandExt;
    use windows_sys::Win32::System::Threading::CREATE_NO_WINDOW;
    Command::new("schtasks.exe")
        .args(args)
        .creation_flags(CREATE_NO_WINDOW)
        .output()
        .map_err(|e| format!("無法執行 schtasks：{e}"))
}

#[cfg(target_os = "windows")]
fn current_user() -> String {
    let name = config::env_string("USERNAME").unwrap_or_default();
    match config::env_string("USERDOMAIN") {
        Some(d) => format!("{d}\\{name}"),
        None => name,
    }
}

#[cfg(target_os = "windows")]
fn current_task_xml() -> Result<String, String> {
    let start = chrono::Local::now().format("%Y-%m-%dT12:30:00").to_string();
    Ok(task_xml(&exe_path()?, &current_user(), &start))
}

#[cfg(target_os = "windows")]
pub fn status() -> Status {
    let detail = format!("工作排程器「{SYNC_TASK_NAME}」：登入時與每天一次，電腦閒置時執行");
    if let Some(dir) = dry_run_dir() {
        let enabled = dir.join(task_file_name()).is_file();
        return Status { enabled, available: true, detail, dry_run: true };
    }
    let enabled = schtasks(&["/Query", "/TN", SYNC_TASK_NAME]).map(|o| o.status.success()).unwrap_or(false);
    Status { enabled, available: true, detail, dry_run: false }
}

#[cfg(target_os = "windows")]
pub fn enable() -> Result<(), String> {
    let xml = current_task_xml()?;
    if let Some(dir) = dry_run_dir() {
        let file = dir.join(task_file_name());
        fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
        fs::write(&file, utf16_with_bom(&xml)).map_err(|e| e.to_string())?;
        return dry_run_log(&dir, &format!("schtasks /Create /TN {SYNC_TASK_NAME} /XML \"{}\" /F", file.display()));
    }
    let file = std::env::temp_dir().join(format!("DaoHui-{}", task_file_name()));
    fs::write(&file, utf16_with_bom(&xml)).map_err(|e| format!("無法寫入 {}：{e}", file.display()))?;
    let path = file.display().to_string();
    let out = schtasks(&["/Create", "/TN", SYNC_TASK_NAME, "/XML", &path, "/F"]);
    let _ = fs::remove_file(&file);
    let out = out?;
    if out.status.success() {
        Ok(())
    } else {
        Err(format!(
            "無法建立排程工作（schtasks 結束代碼 {}）。{}",
            out.status.code().unwrap_or(-1),
            String::from_utf8_lossy(&out.stderr).trim()
        ))
    }
}

#[cfg(target_os = "windows")]
pub fn disable() -> Result<(), String> {
    if let Some(dir) = dry_run_dir() {
        let _ = fs::remove_file(dir.join(task_file_name()));
        return dry_run_log(&dir, &format!("schtasks /Delete /TN {SYNC_TASK_NAME} /F"));
    }
    if !status().enabled {
        return Ok(());
    }
    let out = schtasks(&["/Delete", "/TN", SYNC_TASK_NAME, "/F"])?;
    if out.status.success() {
        Ok(())
    } else {
        Err(format!("無法刪除排程工作（schtasks 結束代碼 {}）。", out.status.code().unwrap_or(-1)))
    }
}

// ---------------------------------------------------------------- macOS

#[cfg(target_os = "macos")]
fn plist_name() -> String {
    format!("{SYNC_AGENT_LABEL}.plist")
}

#[cfg(target_os = "macos")]
fn plist_path() -> Option<PathBuf> {
    Some(config::env_path("HOME")?.join("Library/LaunchAgents").join(plist_name()))
}

#[cfg(target_os = "macos")]
fn launchctl(args: &[&str]) -> Result<std::process::Output, String> {
    Command::new("/bin/launchctl").args(args).output().map_err(|e| format!("無法執行 launchctl：{e}"))
}

#[cfg(target_os = "macos")]
fn gui_domain() -> String {
    // SAFETY: getuid has no preconditions.
    format!("gui/{}", unsafe { libc::getuid() })
}

#[cfg(target_os = "macos")]
pub fn status() -> Status {
    let detail = format!("LaunchAgent {SYNC_AGENT_LABEL}：登入時與每天一次");
    if let Some(dir) = dry_run_dir() {
        return Status { enabled: dir.join(plist_name()).is_file(), available: true, detail, dry_run: true };
    }
    let enabled = plist_path().is_some_and(|p| p.is_file());
    Status { enabled, available: plist_path().is_some(), detail, dry_run: false }
}

#[cfg(target_os = "macos")]
pub fn enable() -> Result<(), String> {
    let plist = launch_agent_plist(&exe_path()?);
    let domain = gui_domain();
    if let Some(dir) = dry_run_dir() {
        let file = dir.join(plist_name());
        fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
        fs::write(&file, plist).map_err(|e| e.to_string())?;
        return dry_run_log(&dir, &format!("launchctl bootstrap {domain} \"{}\"", file.display()));
    }
    let path = plist_path().ok_or("找不到家目錄。")?;
    if let Some(d) = path.parent() {
        fs::create_dir_all(d).map_err(|e| format!("無法建立 {}：{e}", d.display()))?;
    }
    fs::write(&path, plist).map_err(|e| format!("無法寫入 {}：{e}", path.display()))?;
    let target = format!("{domain}/{SYNC_AGENT_LABEL}");
    let _ = launchctl(&["bootout", &target]); // an older copy
    let p = path.display().to_string();
    let out = launchctl(&["bootstrap", &domain, &p])?;
    if out.status.success() {
        Ok(())
    } else {
        let _ = fs::remove_file(&path);
        Err(format!(
            "無法載入 LaunchAgent（launchctl 結束代碼 {}）。{}",
            out.status.code().unwrap_or(-1),
            String::from_utf8_lossy(&out.stderr).trim()
        ))
    }
}

#[cfg(target_os = "macos")]
pub fn disable() -> Result<(), String> {
    let domain = gui_domain();
    if let Some(dir) = dry_run_dir() {
        let _ = fs::remove_file(dir.join(plist_name()));
        return dry_run_log(&dir, &format!("launchctl bootout {domain}/{SYNC_AGENT_LABEL}"));
    }
    let _ = launchctl(&["bootout", &format!("{domain}/{SYNC_AGENT_LABEL}")]);
    if let Some(path) = plist_path().filter(|p| p.exists()) {
        fs::remove_file(&path).map_err(|e| format!("無法刪除 {}：{e}", path.display()))?;
    }
    Ok(())
}

// ---------------------------------------------------------------- elsewhere

#[cfg(not(any(target_os = "windows", target_os = "macos")))]
pub fn status() -> Status {
    let _ = (dry_run_dir, dry_run_log, exe_path);
    Status { enabled: false, available: false, detail: "這個作業系統不支援自動同步。".into(), dry_run: false }
}

#[cfg(not(any(target_os = "windows", target_os = "macos")))]
pub fn enable() -> Result<(), String> {
    Err("這個作業系統不支援自動同步。".into())
}

#[cfg(not(any(target_os = "windows", target_os = "macos")))]
pub fn disable() -> Result<(), String> {
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    const NS: &str = "http://schemas.microsoft.com/windows/2004/02/mit/task";

    fn child<'a>(n: roxmltree::Node<'a, 'a>, name: &str) -> roxmltree::Node<'a, 'a> {
        n.children().find(|c| c.has_tag_name((NS, name))).unwrap_or_else(|| panic!("no <{name}>"))
    }

    fn text<'a>(n: roxmltree::Node<'a, 'a>, path: &[&str]) -> &'a str {
        path.iter().fold(n, |n, p| child(n, p)).text().unwrap_or("")
    }

    #[test]
    fn task_xml_parses_and_says_what_it_should() {
        let exe = r"C:\Program Files\DaoHui & Co\DaoHuiSettings.exe";
        let xml = task_xml(exe, r"LAPTOP\User", "2026-10-27T12:30:00");
        // roxmltree takes the text; the file itself is UTF-16.
        let doc = roxmltree::Document::parse(&xml).unwrap();
        let task = doc.root_element();
        assert!(task.has_tag_name((NS, "Task")));
        assert_eq!(text(task, &["RegistrationInfo", "URI"]), r"\DaoHuiSync");
        let triggers = child(task, "Triggers");
        assert_eq!(text(triggers, &["LogonTrigger", "UserId"]), r"LAPTOP\User");
        assert_eq!(text(triggers, &["CalendarTrigger", "ScheduleByDay", "DaysInterval"]), "1");
        assert_eq!(text(triggers, &["CalendarTrigger", "StartBoundary"]), "2026-10-27T12:30:00");
        let principal = child(child(task, "Principals"), "Principal");
        assert_eq!(text(principal, &["LogonType"]), "InteractiveToken");
        assert_eq!(text(principal, &["RunLevel"]), "LeastPrivilege");
        let settings = child(task, "Settings");
        assert_eq!(text(settings, &["RunOnlyIfIdle"]), "true");
        assert_eq!(text(settings, &["DisallowStartIfOnBatteries"]), "false");
        assert_eq!(text(settings, &["MultipleInstancesPolicy"]), "IgnoreNew");
        let exec = child(child(task, "Actions"), "Exec");
        assert_eq!(text(exec, &["Command"]), exe);
        assert_eq!(text(exec, &["Arguments"]), "--sync");
        assert_eq!(text(exec, &["WorkingDirectory"]), r"C:\Program Files\DaoHui & Co");

        let bytes = utf16_with_bom(&xml);
        assert_eq!(&bytes[..4], &[0xFF, 0xFE, b'<', 0]);
        let units: Vec<u16> = bytes[2..].chunks_exact(2).map(|c| u16::from_le_bytes([c[0], c[1]])).collect();
        let back = String::from_utf16(&units).unwrap();
        assert!(back.contains("\r\n  <Triggers>\r\n") && !back.contains("\r\r"));
    }

    #[test]
    fn launch_agent_plist_parses() {
        let exe = "/Applications/島輝設定.app/Contents/MacOS/daohui-settings";
        let plist = launch_agent_plist(exe);
        let doc = roxmltree::Document::parse_with_options(
            &plist,
            roxmltree::ParsingOptions { allow_dtd: true, ..Default::default() },
        )
        .unwrap();
        let dict = doc.root_element().children().find(|n| n.has_tag_name("dict")).unwrap();
        let items: Vec<roxmltree::Node> = dict.children().filter(|n| n.is_element()).collect();
        let value = |key: &str| {
            let i = items.iter().position(|n| n.has_tag_name("key") && n.text() == Some(key)).unwrap();
            items[i + 1]
        };
        assert_eq!(value("Label").text(), Some("io.github.timlin-1231.daohui.sync"));
        assert!(value("RunAtLoad").has_tag_name("true"));
        assert_eq!(value("StartInterval").text(), Some("86400"));
        let args: Vec<&str> = value("ProgramArguments").children().filter_map(|n| n.text()).filter(|t| !t.trim().is_empty()).collect();
        assert_eq!(args, vec![exe, "--sync"]);
    }

    #[test]
    fn dry_run_writes_files_instead_of_registering() {
        // Only through DAOHUI_AUTOSYNC_DRY_RUN; never registers anything.
        let dir = std::env::temp_dir().join(format!("daohui-autosync-dry-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        std::env::set_var(ENV_AUTOSYNC_DRY_RUN, &dir);
        #[cfg(any(target_os = "windows", target_os = "macos"))]
        {
            assert!(!status().enabled && status().dry_run);
            enable().unwrap();
            assert!(status().enabled);
            disable().unwrap();
            assert!(!status().enabled);
            let cmds = fs::read_to_string(dir.join("commands.txt")).unwrap();
            assert_eq!(cmds.lines().count(), 2, "{cmds}");
        }
        std::env::remove_var(ENV_AUTOSYNC_DRY_RUN);
        let _ = fs::remove_dir_all(&dir);
    }
}
