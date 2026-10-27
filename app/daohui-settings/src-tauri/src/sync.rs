//! 同步: DaoHui's multi-device sync through a shared folder (Google Drive),
//! see SYNC.md. The 「同步」 page and `DaoHuiSettings --sync` (headless, run
//! by the Task Scheduler / a LaunchAgent) both call `run`.
//!
//! A run: trigger file (asus_learn.lua imports/exports its snapshot) →
//! Rime's own sync through the deployer → merge personal words, e-mail
//! addresses, shortcuts and settings → redeploy if anything local changed →
//! device.txt. Files are only written when their content changes, whole
//! (temporary file + rename).

use std::collections::BTreeSet;
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde::Serialize;
use tauri::State;

use crate::config::{self, *};
use crate::features::{self, FeatureSettings};
use crate::sync_merge::{self as merge, Device, SyncState, Time, Word};
use crate::yaml_patch::{double_quoted, PatchFile};
use crate::{autosync, deploy, gdrive, AppState, Busy};

/// A lock older than this is from a run that died.
const STALE_LOCK_SECS: Time = 30 * 60;
const LOG_ROTATE_BYTES: u64 = 512 * 1024;

pub fn now() -> Time {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs() as Time).unwrap_or(0)
}

fn mtime(path: &Path) -> Option<Time> {
    let t = fs::metadata(path).ok()?.modified().ok()?;
    t.duration_since(UNIX_EPOCH).ok().map(|d| d.as_secs() as Time)
}

/// The whole file. Google Drive's virtual drive (Windows) can answer the
/// final read at the end of a file with an error instead of 0 bytes: that
/// counts as the end once the file's length has been read.
fn read_bytes(path: &Path) -> io::Result<Vec<u8>> {
    use std::io::Read;
    let mut f = fs::File::open(path)?;
    let len = f.metadata()?.len();
    let mut buf = Vec::with_capacity(len as usize);
    match f.read_to_end(&mut buf) {
        Ok(_) => Ok(buf),
        Err(_) if len > 0 && buf.len() as u64 >= len => Ok(buf),
        Err(e) => Err(e),
    }
}

/// The file's text, None if it does not exist; not UTF-8 is an error.
fn read_text(path: &Path) -> Result<Option<String>, String> {
    match read_bytes(path) {
        Ok(b) => String::from_utf8(b)
            .map(Some)
            .map_err(|_| format!("{} 不是 UTF-8 編碼，略過。", path.display())),
        Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(format!("無法讀取 {}：{e}", path.display())),
    }
}

/// Writes `content` unless the file already holds exactly that: to a
/// temporary file next to it, then renamed over it (retried a few times
/// while another program, e.g. Google Drive, has it open).
pub fn write_if_changed(path: &Path, content: &str) -> io::Result<bool> {
    if read_bytes(path).ok().as_deref() == Some(content.as_bytes()) {
        return Ok(false);
    }
    if let Some(dir) = path.parent() {
        fs::create_dir_all(dir)?;
    }
    let mut tmp = path.as_os_str().to_owned();
    tmp.push(".tmp");
    let tmp = PathBuf::from(tmp);
    fs::write(&tmp, content)?;
    let mut tries = 0;
    loop {
        match fs::rename(&tmp, path) {
            Ok(()) => return Ok(true),
            Err(_) if tries < 10 => {
                tries += 1;
                std::thread::sleep(Duration::from_millis(200));
            }
            Err(e) => {
                let _ = fs::remove_file(&tmp);
                return Err(e);
            }
        }
    }
}

fn write_err(path: &Path) -> impl Fn(io::Error) -> String + '_ {
    move |e| format!("無法寫入 {}：{e}", path.display())
}

// ---------------------------------------------------------------- log

/// `<user dir>/log/sync.log`, also kept in memory for the page.
pub struct Log {
    file: Option<fs::File>,
    pub lines: Vec<String>,
}

pub fn log_path(user_dir: &Path) -> PathBuf {
    user_dir.join(LOG_DIR_NAME).join(SYNC_LOG_FILE)
}

impl Log {
    pub fn open(user_dir: &Path) -> Log {
        let mut file = None;
        if user_dir.is_dir() {
            let path = log_path(user_dir);
            if let Some(dir) = path.parent() {
                let _ = fs::create_dir_all(dir);
            }
            if fs::metadata(&path).map(|m| m.len() > LOG_ROTATE_BYTES).unwrap_or(false) {
                let _ = fs::rename(&path, path.with_extension("log.old"));
            }
            file = OpenOptions::new().create(true).append(true).open(&path).ok();
        }
        Log { file, lines: Vec::new() }
    }

    pub fn line(&mut self, msg: impl AsRef<str>) {
        let msg = msg.as_ref();
        if let Some(f) = &mut self.file {
            let _ = writeln!(f, "{} {msg}", chrono::Local::now().format("%Y-%m-%d %H:%M:%S"));
        }
        self.lines.push(msg.to_string());
    }
}

// ---------------------------------------------------------------- installation.yaml

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Installation {
    pub exists: bool,
    pub sync_dir: Option<String>,
    pub installation_id: Option<String>,
}

pub fn parse_installation(text: Option<&str>) -> Installation {
    let doc = PatchFile::parse(text.unwrap_or(""));
    let get = |k: &str| doc.get_top(k).map(|s| s.trim().to_string()).filter(|s| !s.is_empty());
    Installation { exists: text.is_some(), sync_dir: get(KEY_SYNC_DIR), installation_id: get(KEY_INSTALLATION_ID) }
}

pub fn read_installation(user_dir: &Path) -> Result<Installation, String> {
    Ok(parse_installation(read_text(&user_dir.join(INSTALLATION_FILE))?.as_deref()))
}

/// installation.yaml with `sync_dir` set (None: removed) and an
/// `installation_id` added if there is none; every other line is kept.
pub fn installation_text(existing: Option<&str>, sync_dir: Option<&str>, new_id: &str) -> String {
    let mut doc = PatchFile::parse(existing.unwrap_or(""));
    match sync_dir {
        Some(d) if doc.get_top(KEY_SYNC_DIR).as_deref() != Some(d) => doc.set_top(KEY_SYNC_DIR, &double_quoted(d)),
        Some(_) => {}
        None => {
            doc.remove_top(KEY_SYNC_DIR);
        }
    }
    if doc.get_top(KEY_INSTALLATION_ID).is_none_or(|s| s.trim().is_empty()) {
        doc.set_top(KEY_INSTALLATION_ID, &double_quoted(new_id));
    }
    doc.render()
}

#[cfg(target_os = "windows")]
const ID_PREFIX: &str = "win";
#[cfg(target_os = "macos")]
const ID_PREFIX: &str = "mac";
#[cfg(not(any(target_os = "windows", target_os = "macos")))]
const ID_PREFIX: &str = "pc";

#[cfg(target_os = "windows")]
pub fn hostname() -> String {
    config::env_string("COMPUTERNAME").unwrap_or_else(|| "windows".into())
}

#[cfg(unix)]
pub fn hostname() -> String {
    let mut buf = [0u8; 256];
    // SAFETY: the buffer is valid for its length; gethostname NUL-terminates
    // when the name fits.
    let ok = unsafe { libc::gethostname(buf.as_mut_ptr().cast(), buf.len() - 1) } == 0;
    let n = buf.iter().position(|&b| b == 0).unwrap_or(0);
    let name = if ok { String::from_utf8_lossy(&buf[..n]).to_string() } else { String::new() };
    let name = name.strip_suffix(".local").unwrap_or(&name).to_string();
    if name.is_empty() {
        "mac".into()
    } else {
        name
    }
}

/// A readable installation id for a new device: `mac-tims-macbook-air`.
pub fn readable_id(prefix: &str, host: &str) -> String {
    let mut s = String::new();
    for c in host.to_lowercase().chars() {
        if c.is_ascii_alphanumeric() {
            s.push(c);
        } else if !s.ends_with('-') {
            s.push('-');
        }
    }
    let s = s.trim_matches('-');
    if s.is_empty() {
        format!("{prefix}-{:x}", now())
    } else {
        format!("{prefix}-{s}")
    }
}

/// An installation id usable as a folder name.
fn valid_id(id: &str) -> bool {
    !id.is_empty()
        && id != "."
        && id != ".."
        && id != SYNC_DAOHUI_SUBDIR
        && !id.contains(['/', '\\', ':', '\t', '\n', '*', '?', '"', '<', '>', '|'])
}

/// Sets (or with None removes) `sync_dir`, keeping or creating the id.
pub fn set_sync_dir_in(user_dir: &Path, sync_dir: Option<&Path>) -> Result<Installation, String> {
    if !user_dir.is_dir() {
        return Err(format!("找不到使用者資料夾 {}。請先安裝並啟用一次島輝輸入法。", user_dir.display()));
    }
    let dir = match sync_dir {
        None => None,
        Some(d) => {
            if !d.is_absolute() {
                return Err("請選擇完整的資料夾路徑。".into());
            }
            let s = d.display().to_string();
            let s = s.trim_end_matches(['\\', '/']).to_string();
            let d = PathBuf::from(&s);
            if d == user_dir || d.starts_with(user_dir) {
                return Err("同步資料夾不能在使用者資料夾裡面。".into());
            }
            if !d.is_dir() {
                match d.parent() {
                    Some(p) if p.is_dir() => fs::create_dir(&d).map_err(|e| format!("無法建立 {}：{e}", d.display()))?,
                    _ => return Err(format!("找不到 {} 的上層資料夾。", d.display())),
                }
            }
            Some(s)
        }
    };
    let path = user_dir.join(INSTALLATION_FILE);
    let existing = read_text(&path)?;
    let text = installation_text(existing.as_deref(), dir.as_deref(), &readable_id(ID_PREFIX, &hostname()));
    write_if_changed(&path, &text).map_err(write_err(&path))?;
    Ok(parse_installation(Some(&text)))
}

// ---------------------------------------------------------------- state, lock

fn load_state(user_dir: &Path) -> SyncState {
    fs::read_to_string(user_dir.join(SYNC_STATE_FILE))
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or_default()
}

fn save_state(user_dir: &Path, state: &SyncState) -> Result<(), String> {
    let path = user_dir.join(SYNC_STATE_FILE);
    let json = serde_json::to_string_pretty(state).map_err(|e| e.to_string())? + "\n";
    write_if_changed(&path, &json).map(|_| ()).map_err(write_err(&path))
}

struct Lock(PathBuf);

impl Lock {
    fn acquire(user_dir: &Path) -> Result<Lock, SyncError> {
        let path = user_dir.join(SYNC_LOCK_FILE);
        for _ in 0..2 {
            match OpenOptions::new().write(true).create_new(true).open(&path) {
                Ok(mut f) => {
                    let _ = write!(f, "{}", std::process::id());
                    return Ok(Lock(path));
                }
                Err(e) if e.kind() == io::ErrorKind::AlreadyExists => {
                    if mtime(&path).is_some_and(|t| now() - t > STALE_LOCK_SECS) {
                        let _ = fs::remove_file(&path);
                        continue;
                    }
                    break;
                }
                Err(e) => return Err(SyncError::Failed(format!("無法建立 {}：{e}", path.display()))),
            }
        }
        Err(SyncError::Busy("另一個同步正在進行中。".into()))
    }
}

impl Drop for Lock {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}

// ---------------------------------------------------------------- devices

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DeviceInfo {
    #[serde(flatten)]
    pub device: Device,
    pub is_self: bool,
}

/// Device folders under `<sync_dir>/daohui/`, sorted by name.
fn device_dirs(root: &Path) -> Vec<(String, PathBuf)> {
    let Ok(rd) = fs::read_dir(root) else { return Vec::new() };
    let mut v: Vec<(String, PathBuf)> = rd
        .flatten()
        .filter(|e| e.file_type().map(|t| t.is_dir()).unwrap_or(false))
        .map(|e| (e.file_name().to_string_lossy().to_string(), e.path()))
        .filter(|(n, _)| valid_id(n) && !n.starts_with('.'))
        .collect();
    v.sort();
    v
}

pub fn list_devices(sync_dir: &Path, own_id: Option<&str>) -> Vec<DeviceInfo> {
    device_dirs(&sync_dir.join(SYNC_DAOHUI_SUBDIR))
        .into_iter()
        .map(|(name, dir)| {
            let device = read_text(&dir.join(SYNC_DEVICE_FILE))
                .ok()
                .flatten()
                .and_then(|t| merge::parse_device(&t))
                .unwrap_or(Device { id: name.clone(), os: String::new(), host: String::new(), time: 0, version: String::new() });
            DeviceInfo { is_self: own_id == Some(name.as_str()), device }
        })
        .collect()
}

// ---------------------------------------------------------------- settings as key/value

/// Synced settings: (key in settings.txt, key in asus_zhuyin.custom.yaml).
pub const SETTING_KEYS: [(&str, &str); 5] = [
    ("asus_lang/raw_keys", KEY_RAW_KEYS),
    ("prediction", KEY_PREDICTION_RESET),
    ("asus_tab/double_ms", KEY_TAB_DOUBLE_MS),
    ("asus_learn/enabled", KEY_LEARN_ENABLED),
    ("asus_english/compose", KEY_ENGLISH_COMPOSE),
];

pub fn setting_values(s: &FeatureSettings) -> std::collections::BTreeMap<String, String> {
    let b = |v: bool| if v { "true" } else { "false" }.to_string();
    [
        ("asus_lang/raw_keys", b(s.raw_keys)),
        ("prediction", b(s.prediction)),
        ("asus_tab/double_ms", s.tab_double_ms.to_string()),
        ("asus_learn/enabled", b(s.learn_enabled)),
        ("asus_english/compose", b(s.english_compose)),
    ]
    .into_iter()
    .map(|(k, v)| (k.to_string(), v))
    .collect()
}

pub fn valid_setting(key: &str, value: &str) -> bool {
    match key {
        "asus_tab/double_ms" => value
            .parse::<u32>()
            .is_ok_and(|v| (TAB_DOUBLE_MS_RANGE.0..=TAB_DOUBLE_MS_RANGE.1).contains(&v)),
        _ => value == "true" || value == "false",
    }
}

pub fn apply_setting_values(base: FeatureSettings, values: &std::collections::BTreeMap<String, String>) -> FeatureSettings {
    let mut s = base;
    for (k, v) in values {
        if !valid_setting(k, v) {
            continue;
        }
        let on = v == "true";
        match k.as_str() {
            "asus_lang/raw_keys" => s.raw_keys = on,
            "prediction" => s.prediction = on,
            "asus_tab/double_ms" => s.tab_double_ms = v.parse().unwrap_or(s.tab_double_ms),
            "asus_learn/enabled" => s.learn_enabled = on,
            "asus_english/compose" => s.english_compose = on,
            _ => {}
        }
    }
    s
}

// ---------------------------------------------------------------- the run

#[derive(Debug, Clone)]
pub struct RunOptions {
    /// Do not call the deployer (Rime sync, redeploy): `DAOHUI_SKIP_DEPLOYER=1`.
    pub skip_deployer: bool,
    pub app_version: String,
}

impl RunOptions {
    pub fn from_env() -> Self {
        RunOptions {
            skip_deployer: config::env_string(ENV_SKIP_DEPLOYER).is_some_and(|v| v != "0"),
            app_version: crate::updates::installed_version().unwrap_or_else(|| env!("CARGO_PKG_VERSION").to_string()),
        }
    }
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SyncReport {
    pub time: Time,
    pub sync_dir: String,
    pub installation_id: String,
    /// Other devices found in the sync folder.
    pub devices: Vec<String>,
    /// Files changed in the user dir.
    pub changed: Vec<String>,
    /// Files written to this device's folder in the sync folder.
    pub exported: Vec<String>,
    pub redeployed: bool,
    pub warnings: Vec<String>,
    pub log: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SyncError {
    /// No user dir or no `sync_dir` (exit code 2).
    NotConfigured(String),
    /// Another run holds the lock (exit code 3).
    Busy(String),
    /// Exit code 1.
    Failed(String),
}

impl SyncError {
    pub fn exit_code(&self) -> i32 {
        match self {
            SyncError::Failed(_) => 1,
            SyncError::NotConfigured(_) => 2,
            SyncError::Busy(_) => 3,
        }
    }

    pub fn message(&self) -> &str {
        match self {
            SyncError::NotConfigured(m) | SyncError::Busy(m) | SyncError::Failed(m) => m,
        }
    }
}

/// Exit code of `--sync` when it finished but something was skipped.
pub const EXIT_WARNINGS: i32 = 4;

struct Ctx<'a> {
    user_dir: &'a Path,
    mine: PathBuf,
    remotes: Vec<(String, PathBuf)>,
    log: &'a mut Log,
    changed: Vec<String>,
    exported: Vec<String>,
    warnings: Vec<String>,
}

impl Ctx<'_> {
    fn warn(&mut self, msg: String) {
        self.log.line(format!("警告：{msg}"));
        self.warnings.push(msg);
    }

    /// Another device's file; unreadable ones are reported and skipped.
    fn remote_text(&mut self, dir: &Path, name: &str) -> Option<(String, Time)> {
        let path = dir.join(name);
        match read_text(&path) {
            Ok(t) => t.map(|t| (t, mtime(&path).unwrap_or(0))),
            Err(e) => {
                self.warn(e);
                None
            }
        }
    }

    fn remote_texts(&mut self, name: &str) -> Vec<(String, Time)> {
        let dirs: Vec<PathBuf> = self.remotes.iter().map(|(_, d)| d.clone()).collect();
        dirs.iter().filter_map(|d| self.remote_text(d, name)).collect()
    }

    /// Writes one of this device's files in the sync folder.
    fn export(&mut self, name: &str, content: &str) -> Result<(), String> {
        let path = self.mine.join(name);
        if content.is_empty() && !path.exists() {
            return Ok(());
        }
        if write_if_changed(&path, content).map_err(write_err(&path))? {
            self.exported.push(name.to_string());
        }
        Ok(())
    }

    /// Writes a file in the user dir.
    fn write_local(&mut self, name: &str, content: &str, what: String) -> Result<(), String> {
        let path = self.user_dir.join(name);
        if write_if_changed(&path, content).map_err(write_err(&path))? {
            self.log.line(format!("已更新 {name}：{what}"));
            self.changed.push(format!("{name}（{what}）"));
        }
        Ok(())
    }
}

fn sync_personal(cx: &mut Ctx) -> Result<bool, String> {
    let own = read_text(&cx.user_dir.join(PERSONAL_DICT_FILE))?.map(|t| merge::parse_dict(&t)).unwrap_or_default();
    let synced = read_text(&cx.user_dir.join(SYNCED_DICT_FILE))?.map(|t| merge::parse_dict(&t)).unwrap_or_default();
    let others: Vec<Vec<Word>> =
        cx.remote_texts(SYNC_PERSONAL_FILE).iter().map(|(t, _)| merge::parse_words(t)).collect();
    let m = merge::merge_personal(&own, &synced, &others);
    cx.log.line(format!("個人詞：這台 {} 個，其他裝置來的 {} 個", own.len(), m.synced.len()));
    let mut changed = false;
    if m.synced != merge::union_words(&[&synced]) {
        let added = m.synced.len().saturating_sub(synced.len());
        cx.write_local(SYNCED_DICT_FILE, &merge::render_synced_dict(&m.synced), format!("新增 {added} 個詞"))?;
        changed = true;
    }
    cx.export(SYNC_PERSONAL_FILE, &merge::render_words(&m.export))?;
    Ok(changed)
}

fn sync_emails(cx: &mut Ctx) -> Result<bool, String> {
    let local = read_text(&cx.user_dir.join(EMAILS_FILE))?;
    let others: Vec<Vec<String>> = cx.remote_texts(SYNC_EMAILS_FILE).iter().map(|(t, _)| merge::parse_emails(t)).collect();
    let m = merge::merge_emails(local.as_deref(), &others);
    let mut changed = false;
    if let Some(text) = &m.local {
        let before = local.as_deref().map(|t| merge::parse_emails(t).len()).unwrap_or(0);
        let added = merge::parse_emails(text).len().saturating_sub(before);
        cx.write_local(EMAILS_FILE, text, format!("新增 {added} 個地址"))?;
        changed = true;
    }
    cx.export(SYNC_EMAILS_FILE, &m.export)?;
    Ok(changed)
}

fn sync_shortcuts(cx: &mut Ctx, state: &mut SyncState) -> Result<bool, String> {
    let path = cx.user_dir.join(SHORTCUTS_FILE);
    let local = read_text(&path)?;
    let remotes: Vec<merge::RemoteShortcuts> =
        cx.remote_texts(SYNC_SHORTCUTS_FILE).iter().map(|(t, mt)| merge::remote_shortcuts(t, *mt)).collect();
    let m = merge::merge_shortcuts(local.as_deref(), mtime(&path).unwrap_or(0), &state.shortcuts, &remotes);
    let mut changed = false;
    if let Some(text) = &m.local {
        if path.is_file() {
            let _ = fs::copy(&path, cx.user_dir.join(format!("{SHORTCUTS_FILE}.bak")));
        }
        let n = merge::parse_shortcuts(text).len();
        cx.write_local(SHORTCUTS_FILE, text, format!("合併後 {n} 個縮寫"))?;
        changed = true;
    }
    state.shortcuts = m.state;
    cx.export(SYNC_SHORTCUTS_FILE, &m.export)?;
    Ok(changed)
}

fn sync_settings(cx: &mut Ctx, state: &mut SyncState) -> Result<bool, String> {
    let path = cx.user_dir.join(CUSTOM_PATCH_FILE);
    let text = read_text(&path)?;
    let doc = PatchFile::parse(text.as_deref().unwrap_or(""));
    let current = FeatureSettings::read(&doc);
    let explicit: BTreeSet<String> =
        SETTING_KEYS.iter().filter(|(_, pk)| doc.contains(pk)).map(|(k, _)| k.to_string()).collect();
    let local = merge::local_setting_stamps(&setting_values(&current), &explicit, mtime(&path).unwrap_or(0), &state.settings);
    let remotes: Vec<_> = cx.remote_texts(SYNC_SETTINGS_FILE).iter().map(|(t, _)| merge::parse_settings(t)).collect();
    let won = merge::merge_settings(&local, &remotes, valid_setting);
    let values = won.iter().map(|(k, s)| (k.clone(), s.value.clone())).collect();
    let new = apply_setting_values(current, &values);
    let mut changed = false;
    if new != current {
        let keys: Vec<&str> =
            won.iter().filter(|(k, s)| local.get(*k).map(|l| &l.value) != Some(&s.value)).map(|(k, _)| k.as_str()).collect();
        features::save_in(cx.user_dir, &new)?;
        cx.log.line(format!("已更新 {CUSTOM_PATCH_FILE}：{}", keys.join("、")));
        cx.changed.push(format!("{CUSTOM_PATCH_FILE}（{}）", keys.join("、")));
        changed = true;
    }
    state.settings = won.clone();
    cx.export(SYNC_SETTINGS_FILE, &merge::render_settings(&won))?;
    Ok(changed)
}

/// One sync run (see the module comment). Errors in one of the merges are
/// reported as warnings; the others still run.
pub fn run(user_dir: &Path, opts: &RunOptions) -> Result<SyncReport, SyncError> {
    let mut log = Log::open(user_dir);
    if !user_dir.is_dir() {
        return Err(SyncError::NotConfigured(format!("找不到使用者資料夾 {}。", user_dir.display())));
    }
    let result = run_in(user_dir, opts, &mut log);
    match &result {
        Ok(r) if r.warnings.is_empty() => log.line("同步完成。"),
        Ok(r) => log.line(format!("同步完成，有 {} 個警告。", r.warnings.len())),
        Err(e) => log.line(format!("同步失敗：{}", e.message())),
    }
    result.map(|mut r| {
        r.log = std::mem::take(&mut log.lines);
        r
    })
}

fn run_in(user_dir: &Path, opts: &RunOptions, log: &mut Log) -> Result<SyncReport, SyncError> {
    let failed = SyncError::Failed;
    let inst = read_installation(user_dir).map_err(failed)?;
    let Some(sync_dir) = inst.sync_dir.clone() else {
        return Err(SyncError::NotConfigured(format!(
            "還沒有設定同步資料夾（{} 的 {KEY_SYNC_DIR}）。",
            user_dir.join(INSTALLATION_FILE).display()
        )));
    };
    let sync_path = PathBuf::from(&sync_dir);
    if !sync_path.is_dir() {
        match sync_path.parent() {
            Some(p) if p.is_dir() => fs::create_dir(&sync_path).map_err(|e| failed(format!("無法建立 {sync_dir}：{e}")))?,
            _ => {
                return Err(failed(format!(
                    "找不到同步資料夾 {sync_dir}。Google 雲端硬碟是否已啟動並登入？"
                )))
            }
        }
    }
    let id = match inst.installation_id.clone() {
        Some(id) => id,
        None => {
            let installed = set_sync_dir_in(user_dir, Some(&sync_path)).map_err(failed)?;
            let id = installed.installation_id.ok_or_else(|| failed("無法建立 installation_id。".into()))?;
            log.line(format!("這台裝置的識別碼：{id}"));
            id
        }
    };
    if !valid_id(&id) {
        return Err(failed(format!("installation_id「{id}」不能當資料夾名稱。")));
    }
    let _lock = Lock::acquire(user_dir)?;
    let root = sync_path.join(SYNC_DAOHUI_SUBDIR);
    let mine = root.join(&id);
    fs::create_dir_all(&mine).map_err(|e| failed(format!("無法建立 {}：{e}", mine.display())))?;
    let time = now();
    log.line(format!("開始同步：{sync_dir}（這台：{id}）"));

    let mut cx = Ctx {
        user_dir,
        mine,
        remotes: device_dirs(&root).into_iter().filter(|(n, _)| *n != id).collect(),
        log,
        changed: Vec::new(),
        exported: Vec::new(),
        warnings: Vec::new(),
    };

    // 1. asus_learn.lua imports the others' snapshots and exports its own
    //    when it sees this file is newer than its last import.
    let trigger = user_dir.join(SYNC_TRIGGER_FILE);
    if let Err(e) = fs::write(&trigger, format!("{time}\n")) {
        cx.warn(format!("無法寫入 {}：{e}", trigger.display()));
    }

    // 2. Rime's own sync (terra_pinyin.userdb etc. through <sync_dir>/<id>/).
    if opts.skip_deployer {
        cx.log.line(format!("略過 Rime 同步（{ENV_SKIP_DEPLOYER}）。"));
    } else {
        match deploy::rime_sync_blocking() {
            Ok(m) => cx.log.line(m),
            Err(e) => cx.warn(format!("Rime 同步失敗：{e}")),
        }
    }

    // 3. DaoHui's files.
    let names: Vec<String> = cx.remotes.iter().map(|(n, _)| n.clone()).collect();
    cx.log.line(if names.is_empty() { "還沒有其他裝置。".to_string() } else { format!("其他裝置：{}", names.join("、")) });
    let mut state = load_state(user_dir);
    let mut local_changed = false;
    let steps: [(&str, fn(&mut Ctx, &mut SyncState) -> Result<bool, String>); 4] = [
        ("個人詞", |cx, _| sync_personal(cx)),
        ("電子郵件地址", |cx, _| sync_emails(cx)),
        ("快捷文字", sync_shortcuts),
        ("功能開關", sync_settings),
    ];
    for (what, step) in steps {
        match step(&mut cx, &mut state) {
            Ok(c) => local_changed |= c,
            Err(e) => cx.warn(format!("{what}：{e}")),
        }
    }
    state.last_sync = time;
    if let Err(e) = save_state(user_dir, &state) {
        cx.warn(e);
    }

    // 4. Dictionaries and settings take effect after a deploy.
    let mut redeployed = false;
    if local_changed {
        if opts.skip_deployer {
            cx.log.line(format!("需要重新部署，已略過（{ENV_SKIP_DEPLOYER}）。"));
        } else {
            match deploy::deploy_blocking() {
                Ok(m) => {
                    cx.log.line(m);
                    redeployed = true;
                }
                Err(e) => cx.warn(format!("重新部署失敗：{e}")),
            }
        }
    } else {
        cx.log.line("這台沒有需要更新的檔案。");
    }

    // 5. device.txt
    let device =
        Device { id: id.clone(), os: std::env::consts::OS.into(), host: hostname(), time, version: opts.app_version.clone() };
    if let Err(e) = cx.export(SYNC_DEVICE_FILE, &merge::render_device(&device)) {
        cx.warn(e);
    }
    if !cx.exported.is_empty() {
        cx.log.line(format!("已寫入同步資料夾：{}", cx.exported.join("、")));
    }

    Ok(SyncReport {
        time,
        sync_dir,
        installation_id: id,
        devices: names,
        changed: cx.changed,
        exported: cx.exported,
        redeployed,
        warnings: cx.warnings,
        log: Vec::new(),
    })
}

/// `DaoHuiSettings --sync`: no window; exit code 0 = done, 4 = done with
/// warnings, 1 = failed, 2 = not configured, 3 = another run in progress.
/// The log is `<user dir>/log/sync.log`.
pub fn cli_main() -> i32 {
    let user_dir = config::user_dir();
    match run(&user_dir, &RunOptions::from_env()) {
        Ok(r) => {
            for l in &r.log {
                println!("{l}");
            }
            if r.warnings.is_empty() {
                0
            } else {
                EXIT_WARNINGS
            }
        }
        Err(e) => {
            eprintln!("{}", e.message());
            e.exit_code()
        }
    }
}

// ---------------------------------------------------------------- commands

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SyncInfo {
    pub user_dir: String,
    pub user_dir_exists: bool,
    pub installation_file: String,
    pub sync_dir: Option<String>,
    pub sync_dir_exists: bool,
    pub installation_id: Option<String>,
    /// Google Drive's "My Drive", if found, and the folder suggested in it.
    pub detected_drive: Option<String>,
    pub suggested_dir: Option<String>,
    pub devices: Vec<DeviceInfo>,
    pub auto_sync: autosync::Status,
    pub last_sync: Option<Time>,
    pub log_path: String,
    pub skip_deployer: bool,
    pub warning: Option<String>,
}

fn info_for(user_dir: &Path) -> SyncInfo {
    let (inst, warning) = match read_installation(user_dir) {
        Ok(i) => (i, None),
        Err(e) => (Installation::default(), Some(e)),
    };
    let sync_path = inst.sync_dir.as_deref().map(PathBuf::from);
    let drive = gdrive::detect_my_drive();
    let state = load_state(user_dir);
    SyncInfo {
        user_dir: user_dir.display().to_string(),
        user_dir_exists: user_dir.is_dir(),
        installation_file: user_dir.join(INSTALLATION_FILE).display().to_string(),
        sync_dir_exists: sync_path.as_ref().is_some_and(|p| p.is_dir()),
        devices: sync_path.as_deref().map(|p| list_devices(p, inst.installation_id.as_deref())).unwrap_or_default(),
        sync_dir: inst.sync_dir,
        installation_id: inst.installation_id,
        suggested_dir: drive.as_ref().map(|d| d.join(DEFAULT_SYNC_SUBDIR).display().to_string()),
        detected_drive: drive.map(|d| d.display().to_string()),
        auto_sync: autosync::status(),
        last_sync: (state.last_sync > 0).then_some(state.last_sync),
        log_path: log_path(user_dir).display().to_string(),
        skip_deployer: RunOptions::from_env().skip_deployer,
        warning,
    }
}

async fn blocking<T: Send + 'static>(f: impl FnOnce() -> T + Send + 'static) -> Result<T, String> {
    tauri::async_runtime::spawn_blocking(f).await.map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn sync_info() -> Result<SyncInfo, String> {
    blocking(|| info_for(&config::user_dir())).await
}

/// `path`: the new sync folder; empty or null stops syncing (and turns
/// automatic sync off).
#[tauri::command]
pub async fn set_sync_dir(path: Option<String>) -> Result<SyncInfo, String> {
    blocking(move || {
        let dir = config::user_dir();
        let path = path.map(|p| p.trim().to_string()).filter(|p| !p.is_empty());
        set_sync_dir_in(&dir, path.as_deref().map(Path::new))?;
        if path.is_none() && autosync::status().enabled {
            autosync::disable()?;
        }
        Ok(info_for(&dir))
    })
    .await?
}

/// The system's folder picker; None when cancelled.
#[tauri::command]
pub async fn pick_sync_dir(window: tauri::WebviewWindow, start: Option<String>) -> Result<Option<String>, String> {
    #[cfg(target_os = "windows")]
    {
        let owner = window.hwnd().map(|h| h.0 as isize).unwrap_or(0);
        blocking(move || {
            let start = start.map(PathBuf::from).filter(|p| p.is_dir());
            crate::winutil::pick_folder(owner, "選擇同步資料夾（例如 Google 雲端硬碟裡的 DaoHui-sync）", start.as_deref())
                .map(|p| p.display().to_string())
        })
        .await
    }
    #[cfg(target_os = "macos")]
    {
        let _ = window;
        blocking(move || {
            let mut script = String::from("POSIX path of (choose folder with prompt \"選擇同步資料夾\"");
            if let Some(s) = start.filter(|s| Path::new(s).is_dir() && !s.contains('"')) {
                script.push_str(&format!(" default location (POSIX file \"{s}\")"));
            }
            script.push(')');
            let out = std::process::Command::new("/usr/bin/osascript")
                .args(["-e", &script])
                .output()
                .map_err(|e| format!("無法開啟選擇資料夾的視窗：{e}"))?;
            let p = String::from_utf8_lossy(&out.stdout).trim().to_string();
            Ok((out.status.success() && !p.is_empty()).then(|| p.trim_end_matches('/').to_string()))
        })
        .await?
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    {
        let _ = (window, start);
        Err("這個作業系統沒有選擇資料夾的視窗，請直接輸入路徑。".into())
    }
}

#[tauri::command]
pub async fn sync_now(state: State<'_, AppState>) -> Result<SyncReport, String> {
    let _busy = Busy::acquire(&state.sync_busy).ok_or("正在同步中。")?;
    let opts = RunOptions::from_env();
    blocking(move || run(&config::user_dir(), &opts).map_err(|e| e.message().to_string())).await?
}

#[tauri::command]
pub async fn set_auto_sync(enabled: bool) -> Result<SyncInfo, String> {
    blocking(move || {
        let dir = config::user_dir();
        if enabled {
            if read_installation(&dir)?.sync_dir.is_none() {
                return Err("請先設定同步資料夾。".to_string());
            }
            autosync::enable()?;
        } else {
            autosync::disable()?;
        }
        Ok(info_for(&dir))
    })
    .await?
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A fresh folder under `DAOHUI_TEST_DIR` (else the system temp dir).
    fn temp(tag: &str) -> PathBuf {
        let base = config::env_path("DAOHUI_TEST_DIR").unwrap_or_else(std::env::temp_dir);
        let d = base.join(format!("daohui-sync-test-{}-{tag}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn installation_yaml_edits() {
        let rime = "distribution_code_name: DaoHui\ninstallation_id: \"2489a486-0e52-406d-93e2-548612575a43\"\nrime_version: 1.13.1\n";
        let t = installation_text(Some(rime), Some(r"D:\GoogleDrive\DaoHui-sync"), "win-x");
        assert_eq!(t, format!("{rime}sync_dir: \"D:\\\\GoogleDrive\\\\DaoHui-sync\"\n"));
        let i = parse_installation(Some(&t));
        assert_eq!(i.sync_dir.as_deref(), Some(r"D:\GoogleDrive\DaoHui-sync"));
        assert_eq!(i.installation_id.as_deref(), Some("2489a486-0e52-406d-93e2-548612575a43"));
        // Same folder again: unchanged (also when Rime wrote it plainly).
        assert_eq!(installation_text(Some(&t), Some(r"D:\GoogleDrive\DaoHui-sync"), "win-x"), t);
        let plain = format!("{rime}sync_dir: /Users/a/Drive\n");
        assert_eq!(installation_text(Some(&plain), Some("/Users/a/Drive"), "x"), plain);
        // Removing it restores Rime's file.
        assert_eq!(installation_text(Some(&t), None, "win-x"), rime);
        // A new device gets the readable id.
        let fresh = installation_text(None, Some("/Users/tim/Library/CloudStorage/GoogleDrive-a@b.c/我的雲端硬碟/DaoHui-sync"), "mac-air");
        assert_eq!(
            fresh,
            "sync_dir: \"/Users/tim/Library/CloudStorage/GoogleDrive-a@b.c/我的雲端硬碟/DaoHui-sync\"\ninstallation_id: \"mac-air\"\n"
        );
        assert_eq!(parse_installation(None), Installation::default());
    }

    #[test]
    fn readable_ids() {
        assert_eq!(readable_id("mac", "Tims-MacBook-Air"), "mac-tims-macbook-air");
        assert_eq!(readable_id("win", "LAPTOP-H6 FNN_T7R"), "win-laptop-h6-fnn-t7r");
        assert!(readable_id("mac", "林的電腦").starts_with("mac-"));
        assert!(valid_id("mac-air") && !valid_id("daohui") && !valid_id("a/b") && !valid_id(".."));
    }

    #[test]
    fn settings_values_round_trip() {
        let s = FeatureSettings { raw_keys: false, tab_double_ms: 600, ..Default::default() };
        let v = setting_values(&s);
        assert_eq!(v.len(), SETTING_KEYS.len());
        assert_eq!(v["asus_tab/double_ms"], "600");
        assert_eq!(apply_setting_values(FeatureSettings::default(), &v), s);
        assert!(!valid_setting("asus_tab/double_ms", "99999") && !valid_setting("prediction", "1"));
        let mut bad = v.clone();
        bad.insert("asus_tab/double_ms".into(), "fast".into());
        assert_eq!(apply_setting_values(s, &bad).tab_double_ms, 600);
    }

    #[test]
    fn set_sync_dir_creates_and_validates() {
        let root = temp("setdir");
        let user = root.join("user");
        fs::create_dir_all(&user).unwrap();
        let sync = root.join("Drive").join(DEFAULT_SYNC_SUBDIR);
        assert!(set_sync_dir_in(&user, Some(&sync)).is_err()); // no parent
        fs::create_dir_all(root.join("Drive")).unwrap();
        let i = set_sync_dir_in(&user, Some(&sync)).unwrap();
        assert!(sync.is_dir());
        assert_eq!(i.sync_dir.as_deref(), Some(sync.to_str().unwrap()));
        assert!(i.installation_id.as_deref().unwrap().starts_with(ID_PREFIX));
        assert!(set_sync_dir_in(&user, Some(&user.join("sync"))).is_err());
        assert!(set_sync_dir_in(&user, Some(Path::new("relative"))).is_err());
        let off = set_sync_dir_in(&user, None).unwrap();
        assert_eq!(off.sync_dir, None);
        assert_eq!(off.installation_id, i.installation_id); // kept
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn not_configured_and_busy() {
        let root = temp("errors");
        let user = root.join("user");
        fs::create_dir_all(&user).unwrap();
        let opts = RunOptions { skip_deployer: true, app_version: "0.0.0".into() };
        assert_eq!(run(&root.join("missing"), &opts).unwrap_err().exit_code(), 2);
        assert_eq!(run(&user, &opts).unwrap_err().exit_code(), 2); // no sync_dir
        fs::write(user.join(INSTALLATION_FILE), "sync_dir: \"/no/such/parent/x\"\n").unwrap();
        assert_eq!(run(&user, &opts).unwrap_err().exit_code(), 1);
        set_sync_dir_in(&user, Some(&root.join("sync"))).unwrap();
        let held = Lock::acquire(&user).unwrap();
        assert_eq!(run(&user, &opts).unwrap_err().exit_code(), 3);
        drop(held);
        let r = run(&user, &opts).unwrap();
        assert!(r.warnings.is_empty(), "{r:?}");
        assert!(user.join(SYNC_TRIGGER_FILE).is_file());
        assert!(root.join("sync").join(SYNC_DAOHUI_SUBDIR).join(&r.installation_id).join(SYNC_DEVICE_FILE).is_file());
        let log = fs::read_to_string(log_path(&user)).unwrap();
        assert!(log.contains("同步失敗：另一個同步正在進行中") && log.contains("同步完成。"), "{log}");
        let _ = fs::remove_dir_all(&root);
    }

    /// Two devices and one sync folder, end to end without the deployer.
    #[test]
    fn two_devices_converge() {
        let root = temp("e2e");
        let sync = root.join("sync");
        let (pc, mac) = (root.join("pc"), root.join("mac"));
        for (d, id) in [(&pc, "pc-1"), (&mac, "mac-air")] {
            fs::create_dir_all(d).unwrap();
            fs::write(d.join(INSTALLATION_FILE), format!("installation_id: \"{id}\"\n")).unwrap();
            set_sync_dir_in(d, Some(&sync)).unwrap();
        }
        fs::write(pc.join(PERSONAL_DICT_FILE), "---\nname: terra_pinyin.personal\n...\n時域\tshi2 yu4\t20000\n").unwrap();
        fs::write(mac.join(PERSONAL_DICT_FILE), "---\nname: terra_pinyin.personal\n...\n頻域\tpin2 yu4\t20000\n").unwrap();
        fs::write(pc.join(EMAILS_FILE), "a@x.com\n").unwrap();
        fs::write(mac.join(SHORTCUTS_FILE), "ㄉㄓ\t台北市\n").unwrap();
        fs::write(mac.join(CUSTOM_PATCH_FILE), "patch:\n  asus_tab/double_ms: 600\n").unwrap();
        let opts = RunOptions { skip_deployer: true, app_version: "9.9".into() };

        let r1 = run(&pc, &opts).unwrap();
        assert!(r1.devices.is_empty() && r1.changed.is_empty(), "{r1:?}");
        let r2 = run(&mac, &opts).unwrap();
        assert_eq!(r2.devices, vec!["pc-1"]);
        assert!(r2.warnings.is_empty(), "{r2:?}");
        let r3 = run(&pc, &opts).unwrap();
        assert_eq!(r3.changed.len(), 3, "{r3:?}"); // synced dict, shortcuts, settings
        let synced = fs::read_to_string(pc.join(SYNCED_DICT_FILE)).unwrap();
        assert!(synced.contains("頻域\tpin2 yu4\t20000\n") && !synced.contains("時域"));
        assert_eq!(fs::read_to_string(pc.join(SHORTCUTS_FILE)).unwrap(), "ㄉㄓ\t台北市\n");
        assert!(fs::read_to_string(pc.join(CUSTOM_PATCH_FILE)).unwrap().contains("asus_tab/double_ms: 600"));
        let r4 = run(&mac, &opts).unwrap();
        assert!(fs::read_to_string(mac.join(EMAILS_FILE)).unwrap().contains("a@x.com"));
        assert!(fs::read_to_string(mac.join(SYNCED_DICT_FILE)).unwrap().contains("時域"));
        // Nothing left to do on either side.
        let r5 = run(&pc, &opts).unwrap();
        let r6 = run(&mac, &opts).unwrap();
        assert!(r5.changed.is_empty() && r6.changed.is_empty(), "{r5:?}\n{r6:?}");
        // Only device.txt (its time) can change now.
        assert!(r5.exported.iter().chain(&r6.exported).all(|f| f == SYNC_DEVICE_FILE), "{r5:?}\n{r6:?}");
        let devices = list_devices(&sync, Some("pc-1"));
        assert_eq!(devices.len(), 2);
        assert!(devices.iter().any(|d| d.is_self && d.device.id == "pc-1" && d.device.version == "9.9"));
        let _ = (r4, fs::remove_dir_all(&root));
    }
}
