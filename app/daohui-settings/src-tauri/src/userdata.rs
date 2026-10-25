//! 學習資料: sizes of the `*.userdb` folders, backups to
//! `<user dir>/backups/<timestamp>/`, and restoring them.
//!
//! While the input method runs it keeps its LevelDB files open (on Windows
//! some exclusively), so a backup taken then may miss files. Each backup
//! records what it skipped in `daohui-backup.json`; an incomplete userdb is
//! never restored. Restoring refuses while a userdb is in use (its LevelDB
//! LOCK is held). No process is stopped by this app.

use std::fs::{self, File};
use std::io;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::config::{self, BACKUP_DIR_NAME, BACKUP_EXTRA_FILES, USERDB_SUFFIX};

const MANIFEST: &str = "daohui-backup.json";
/// LevelDB's lock file; recreated on open, and held by the running IME.
const LEVELDB_LOCK: &str = "LOCK";
const SAFETY_SUFFIX: &str = "before-restore";

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UserDbInfo {
    pub name: String,
    pub bytes: u64,
    pub in_use: bool,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BackupInfo {
    pub name: String,
    pub label: String,
    pub path: String,
    pub bytes: u64,
    pub files: usize,
    pub userdbs: Vec<String>,
    /// Files that could not be copied (in use).
    pub skipped: Vec<String>,
    pub automatic: bool,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UserDataInfo {
    pub user_dir: String,
    pub user_dir_exists: bool,
    pub userdbs: Vec<UserDbInfo>,
    pub total_bytes: u64,
    pub backup_root: String,
    pub backups: Vec<BackupInfo>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RestoreResult {
    pub restored: Vec<String>,
    /// Userdbs left alone because their backup was incomplete.
    pub not_restored: Vec<String>,
    pub safety_backup: String,
}

#[derive(Debug, Serialize, Deserialize)]
struct Manifest {
    created: String,
    app: String,
    user_dir: String,
    skipped: Vec<String>,
}

fn dir_size(path: &Path) -> u64 {
    let Ok(rd) = fs::read_dir(path) else { return 0 };
    rd.flatten()
        .map(|e| match e.file_type() {
            Ok(t) if t.is_dir() => dir_size(&e.path()),
            Ok(_) => e.metadata().map(|m| m.len()).unwrap_or(0),
            Err(_) => 0,
        })
        .sum()
}

fn count_files(path: &Path) -> usize {
    let Ok(rd) = fs::read_dir(path) else { return 0 };
    rd.flatten()
        .map(|e| match e.file_type() {
            Ok(t) if t.is_dir() => count_files(&e.path()),
            Ok(_) => 1,
            Err(_) => 0,
        })
        .sum()
}

/// `(name, path)` of the `*.userdb` folders in `dir`, sorted by name.
fn userdbs_in(dir: &Path) -> Vec<(String, PathBuf)> {
    let Ok(rd) = fs::read_dir(dir) else { return Vec::new() };
    let mut v: Vec<(String, PathBuf)> = rd
        .flatten()
        .filter(|e| e.file_type().map(|t| t.is_dir()).unwrap_or(false))
        .filter_map(|e| {
            let name = e.file_name().to_string_lossy().to_string();
            name.ends_with(USERDB_SUFFIX).then(|| (name, e.path()))
        })
        .collect();
    v.sort();
    v
}

/// Settings files copied besides the userdbs.
fn extra_files_in(dir: &Path) -> Vec<PathBuf> {
    let Ok(rd) = fs::read_dir(dir) else { return Vec::new() };
    let mut v: Vec<PathBuf> = rd
        .flatten()
        .filter(|e| e.file_type().map(|t| t.is_file()).unwrap_or(false))
        .filter(|e| {
            let n = e.file_name().to_string_lossy().to_string();
            n.ends_with(".custom.yaml") || BACKUP_EXTRA_FILES.contains(&n.as_str())
        })
        .map(|e| e.path())
        .collect();
    v.sort();
    v
}

/// Copies through a handle opened with full sharing (Rust's default on
/// Windows), so files the IME has open for reading or appending still copy.
fn copy_file(src: &Path, dst: &Path) -> io::Result<u64> {
    let mut r = File::open(src)?;
    let mut w = File::create(dst)?;
    io::copy(&mut r, &mut w)
}

/// Copies a folder; files that fail are appended to `skipped` (relative paths).
fn copy_dir(src: &Path, dst: &Path, rel: &str, skipped: &mut Vec<String>) -> io::Result<()> {
    fs::create_dir_all(dst)?;
    for e in fs::read_dir(src)?.flatten() {
        let name = e.file_name().to_string_lossy().to_string();
        let rel_name = format!("{rel}/{name}");
        if e.file_type()?.is_dir() {
            copy_dir(&e.path(), &dst.join(&name), &rel_name, skipped)?;
        } else if name != LEVELDB_LOCK && copy_file(&e.path(), &dst.join(&name)).is_err() {
            let _ = fs::remove_file(dst.join(&name));
            skipped.push(rel_name);
        }
    }
    Ok(())
}

/// Whether a running program holds the userdb's LevelDB lock.
pub fn userdb_in_use(db: &Path) -> bool {
    let lock = db.join(LEVELDB_LOCK);
    lock.is_file() && lock_held(&lock)
}

#[cfg(target_os = "windows")]
fn lock_held(lock: &Path) -> bool {
    // LevelDB opens LOCK with FILE_SHARE_READ only and locks it with
    // LockFile, so opening it for writing fails while it is held.
    match fs::OpenOptions::new().read(true).write(true).open(lock) {
        Err(_) => true,
        Ok(f) => match f.try_lock() {
            Ok(()) => {
                let _ = f.unlock();
                false
            }
            Err(_) => true,
        },
    }
}

#[cfg(unix)]
fn lock_held(lock: &Path) -> bool {
    use std::os::unix::io::AsRawFd;
    // LevelDB uses fcntl(F_SETLK) record locks; ask with F_GETLK.
    let Ok(f) = fs::OpenOptions::new().read(true).write(true).open(lock) else {
        return false;
    };
    // SAFETY: flock is plain data; fcntl gets a valid fd and pointer.
    unsafe {
        let mut fl: libc::flock = std::mem::zeroed();
        fl.l_type = libc::F_WRLCK as _;
        fl.l_whence = libc::SEEK_SET as _;
        fl.l_start = 0;
        fl.l_len = 0;
        libc::fcntl(f.as_raw_fd(), libc::F_GETLK, &mut fl as *mut libc::flock) == 0
            && fl.l_type != libc::F_UNLCK as libc::c_short
    }
}

#[cfg(not(any(target_os = "windows", unix)))]
fn lock_held(_lock: &Path) -> bool {
    false
}

fn backup_root(user_dir: &Path) -> PathBuf {
    user_dir.join(BACKUP_DIR_NAME)
}

fn valid_backup_name(name: &str) -> bool {
    !name.is_empty()
        && name != "."
        && name != ".."
        && !name.contains(['/', '\\', ':'])
        && !name.contains("..")
}

/// `20261025-163000[-x]` -> `2026/10/25 16:30:00`.
fn label_for(name: &str) -> String {
    let b = name.as_bytes();
    if b.len() >= 15 && b[8] == b'-' && b[..8].iter().chain(&b[9..15]).all(u8::is_ascii_digit) {
        format!(
            "{}/{}/{} {}:{}:{}",
            &name[0..4],
            &name[4..6],
            &name[6..8],
            &name[9..11],
            &name[11..13],
            &name[13..15]
        )
    } else {
        name.to_string()
    }
}

fn read_manifest(dir: &Path) -> Option<Manifest> {
    let text = fs::read_to_string(dir.join(MANIFEST)).ok()?;
    serde_json::from_str(&text).ok()
}

fn backup_info(dir: &Path) -> BackupInfo {
    let name = dir.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
    let skipped = read_manifest(dir).map(|m| m.skipped).unwrap_or_default();
    BackupInfo {
        label: label_for(&name),
        automatic: name.ends_with(SAFETY_SUFFIX),
        path: dir.display().to_string(),
        bytes: dir_size(dir),
        files: count_files(dir).saturating_sub(dir.join(MANIFEST).is_file() as usize),
        userdbs: userdbs_in(dir).into_iter().map(|(n, _)| n).collect(),
        skipped,
        name,
    }
}

pub fn list_backups(user_dir: &Path) -> Vec<BackupInfo> {
    let Ok(rd) = fs::read_dir(backup_root(user_dir)) else { return Vec::new() };
    let mut dirs: Vec<PathBuf> = rd
        .flatten()
        .filter(|e| e.file_type().map(|t| t.is_dir()).unwrap_or(false))
        .map(|e| e.path())
        .collect();
    dirs.sort();
    dirs.reverse(); // newest first (names start with a timestamp)
    dirs.iter().map(|d| backup_info(d)).collect()
}

fn timestamp() -> String {
    chrono::Local::now().format("%Y%m%d-%H%M%S").to_string()
}

/// Creates `<user dir>/backups/<name>` (a numeric suffix avoids clashes).
pub fn create_backup(user_dir: &Path, name: &str, app_version: &str) -> Result<BackupInfo, String> {
    if !user_dir.is_dir() {
        return Err(format!("找不到使用者資料夾：{}", user_dir.display()));
    }
    let root = backup_root(user_dir);
    let mut dest = root.join(name);
    let mut n = 2;
    while dest.exists() {
        dest = root.join(format!("{name}-{n}"));
        n += 1;
    }
    let err = |e: io::Error| format!("備份失敗：{e}");
    fs::create_dir_all(&dest).map_err(err)?;
    let mut skipped = Vec::new();
    for (db, path) in userdbs_in(user_dir) {
        copy_dir(&path, &dest.join(&db), &db, &mut skipped).map_err(err)?;
    }
    for f in extra_files_in(user_dir) {
        let fname = f.file_name().unwrap_or_default().to_string_lossy().to_string();
        if copy_file(&f, &dest.join(&fname)).is_err() {
            skipped.push(fname);
        }
    }
    let manifest = Manifest {
        created: chrono::Local::now().to_rfc3339(),
        app: app_version.to_string(),
        user_dir: user_dir.display().to_string(),
        skipped,
    };
    let json = serde_json::to_string_pretty(&manifest).unwrap_or_default();
    fs::write(dest.join(MANIFEST), json).map_err(err)?;
    Ok(backup_info(&dest))
}

pub fn restore(user_dir: &Path, name: &str, app_version: &str) -> Result<RestoreResult, String> {
    if !valid_backup_name(name) {
        return Err("備份名稱不正確。".into());
    }
    let src = backup_root(user_dir).join(name);
    if !src.is_dir() {
        return Err(format!("找不到備份：{name}"));
    }
    let skipped = read_manifest(&src).map(|m| m.skipped).unwrap_or_default();
    let (complete, incomplete): (Vec<_>, Vec<_>) = userdbs_in(&src)
        .into_iter()
        .partition(|(db, _)| !skipped.iter().any(|s| s.starts_with(&format!("{db}/"))));

    let busy: Vec<String> = complete
        .iter()
        .filter(|(db, _)| userdb_in_use(&user_dir.join(db)))
        .map(|(db, _)| db.clone())
        .collect();
    if !busy.is_empty() {
        return Err(format!(
            "{} 正在被輸入法使用，現在不能還原。\n請先結束島輝輸入法（例如登出 Windows 後再登入，或在輸入法選單結束服務），再回來還原。",
            busy.join("、")
        ));
    }

    let safety = create_backup(user_dir, &format!("{}-{SAFETY_SUFFIX}", timestamp()), app_version)?;
    let err = |e: io::Error| format!("還原失敗：{e}（還原前的資料在 {}）", safety.path);
    let mut restored = Vec::new();
    for (db, path) in &complete {
        let target = user_dir.join(db);
        if target.exists() {
            fs::remove_dir_all(&target).map_err(err)?;
        }
        let mut failed = Vec::new();
        copy_dir(path, &target, db, &mut failed).map_err(err)?;
        if !failed.is_empty() {
            return Err(format!("還原 {db} 時有檔案無法複製：{}", failed.join("、")));
        }
        restored.push(db.clone());
    }
    for f in extra_files_in(&src) {
        let fname = f.file_name().unwrap_or_default().to_string_lossy().to_string();
        copy_file(&f, &user_dir.join(&fname)).map_err(err)?;
        restored.push(fname);
    }
    Ok(RestoreResult {
        restored,
        not_restored: incomplete.into_iter().map(|(db, _)| db).collect(),
        safety_backup: safety.name,
    })
}

pub fn info(user_dir: &Path) -> UserDataInfo {
    let userdbs: Vec<UserDbInfo> = userdbs_in(user_dir)
        .into_iter()
        .map(|(name, path)| UserDbInfo { bytes: dir_size(&path), in_use: userdb_in_use(&path), name })
        .collect();
    UserDataInfo {
        user_dir: user_dir.display().to_string(),
        user_dir_exists: user_dir.is_dir(),
        total_bytes: userdbs.iter().map(|d| d.bytes).sum(),
        userdbs,
        backup_root: backup_root(user_dir).display().to_string(),
        backups: list_backups(user_dir),
    }
}

/// Path of a backup folder by name (for "open folder").
pub fn backup_path(name: &str) -> Result<PathBuf, String> {
    if !valid_backup_name(name) {
        return Err("備份名稱不正確。".into());
    }
    Ok(backup_root(&config::user_dir()).join(name))
}

#[tauri::command]
pub fn userdata_info() -> UserDataInfo {
    info(&config::user_dir())
}

#[tauri::command]
pub async fn backup_now(app: tauri::AppHandle) -> Result<BackupInfo, String> {
    let version = app.package_info().version.to_string();
    tauri::async_runtime::spawn_blocking(move || {
        create_backup(&config::user_dir(), &timestamp(), &version)
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
pub async fn restore_backup(app: tauri::AppHandle, name: String) -> Result<RestoreResult, String> {
    let version = app.package_info().version.to_string();
    tauri::async_runtime::spawn_blocking(move || restore(&config::user_dir(), &name, &version))
        .await
        .map_err(|e| e.to_string())?
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture(tag: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("daohui-userdata-{}-{tag}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(d.join("terra_pinyin.userdb")).unwrap();
        fs::create_dir_all(d.join("asus_learn.userdb")).unwrap();
        fs::write(d.join("terra_pinyin.userdb/000005.ldb"), vec![1u8; 1000]).unwrap();
        fs::write(d.join("terra_pinyin.userdb/CURRENT"), b"MANIFEST-000004\n").unwrap();
        fs::write(d.join("terra_pinyin.userdb/LOCK"), b"").unwrap();
        fs::write(d.join("asus_learn.userdb/000003.log"), vec![2u8; 500]).unwrap();
        fs::write(d.join("asus_zhuyin.custom.yaml"), b"patch:\n  a: 1\n").unwrap();
        fs::write(d.join("shortcuts.txt"), b"x\ty\n").unwrap();
        fs::write(d.join("unrelated.txt"), b"no").unwrap();
        d
    }

    #[test]
    fn sizes_and_names() {
        let d = fixture("info");
        let i = info(&d);
        assert_eq!(i.userdbs.len(), 2);
        assert_eq!(i.userdbs[0].name, "asus_learn.userdb");
        assert_eq!(i.total_bytes, 1000 + 16 + 500);
        assert!(i.userdbs.iter().all(|u| !u.in_use));
        assert!(i.backups.is_empty());
        assert_eq!(label_for("20261025-163000"), "2026/10/25 16:30:00");
        assert_eq!(label_for("20261025-163000-before-restore"), "2026/10/25 16:30:00");
        assert_eq!(label_for("misc"), "misc");
        assert!(!valid_backup_name("../x") && !valid_backup_name("a\\b") && valid_backup_name("20261025-1"));
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn backup_and_restore() {
        let d = fixture("roundtrip");
        let b = create_backup(&d, "20200101-120000", "0.1.0").unwrap();
        assert_eq!(b.userdbs, vec!["asus_learn.userdb", "terra_pinyin.userdb"]);
        assert!(b.skipped.is_empty());
        assert_eq!(b.files, 5); // 3 userdb files (LOCK skipped) + 2 settings files
        assert!(!Path::new(&b.path).join("unrelated.txt").exists());
        assert!(!Path::new(&b.path).join("terra_pinyin.userdb/LOCK").exists());
        // Same name again gets a suffix.
        let b2 = create_backup(&d, "20200101-120000", "0.1.0").unwrap();
        assert_eq!(b2.name, "20200101-120000-2");

        // Change the live data, then restore.
        fs::write(d.join("terra_pinyin.userdb/000005.ldb"), b"changed").unwrap();
        fs::write(d.join("terra_pinyin.userdb/000099.ldb"), b"new").unwrap();
        fs::write(d.join("asus_zhuyin.custom.yaml"), b"patch: {}\n").unwrap();
        let r = restore(&d, "20200101-120000", "0.1.0").unwrap();
        assert!(r.restored.contains(&"terra_pinyin.userdb".to_string()));
        assert!(r.safety_backup.ends_with("before-restore"));
        assert_eq!(fs::read(d.join("terra_pinyin.userdb/000005.ldb")).unwrap(), vec![1u8; 1000]);
        assert!(!d.join("terra_pinyin.userdb/000099.ldb").exists());
        assert_eq!(fs::read_to_string(d.join("asus_zhuyin.custom.yaml")).unwrap(), "patch:\n  a: 1\n");
        // The safety backup holds the changed state.
        let safety = backup_root(&d).join(&r.safety_backup);
        assert_eq!(fs::read(safety.join("terra_pinyin.userdb/000005.ldb")).unwrap(), b"changed");

        let list = list_backups(&d);
        assert_eq!(list.len(), 3);
        assert!(list[0].automatic); // newest first
        assert!(restore(&d, "../etc", "0.1.0").is_err());
        assert!(restore(&d, "nope", "0.1.0").is_err());
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn incomplete_userdb_is_not_restored() {
        let d = fixture("incomplete");
        let b = create_backup(&d, "20200101-130000", "0.1.0").unwrap();
        let m = Manifest {
            created: String::new(),
            app: String::new(),
            user_dir: String::new(),
            skipped: vec!["asus_learn.userdb/000003.log".into()],
        };
        fs::write(Path::new(&b.path).join(MANIFEST), serde_json::to_string(&m).unwrap()).unwrap();
        fs::write(d.join("asus_learn.userdb/000003.log"), b"live").unwrap();
        let r = restore(&d, &b.name, "0.1.0").unwrap();
        assert_eq!(r.not_restored, vec!["asus_learn.userdb"]);
        assert_eq!(fs::read(d.join("asus_learn.userdb/000003.log")).unwrap(), b"live");
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn held_lock_is_detected() {
        let d = fixture("lock");
        let db = d.join("terra_pinyin.userdb");
        assert!(!userdb_in_use(&db));
        let f = fs::OpenOptions::new().read(true).write(true).open(db.join("LOCK")).unwrap();
        f.lock().unwrap();
        // Another handle in this process sees the lock (Windows: LockFileEx).
        #[cfg(target_os = "windows")]
        assert!(userdb_in_use(&db));
        f.unlock().unwrap();
        drop(f);
        assert!(!userdb_in_use(&db));
        let _ = fs::remove_dir_all(&d);
    }
}
