//! 重新部署: runs the input method's deployer.
//! Windows: `<install dir>\DaoHuiDeployer.exe /deploy`;
//! macOS: `/Library/Input Methods/DaoHui.app/Contents/MacOS/DaoHui --reload`.

use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{Duration, Instant};

#[allow(unused_imports)]
use crate::config::{self, *};

/// Give up waiting (the deployer keeps running) after this long.
const DEPLOY_WAIT: Duration = Duration::from_secs(180);

/// Places the deployer may be, most specific first.
#[cfg(target_os = "windows")]
pub fn deployer_candidates() -> Vec<PathBuf> {
    let mut v = Vec::new();
    if let Some(d) = config::env_path(ENV_INSTALL_DIR) {
        v.push(d.join(DEPLOYER_EXE));
    }
    if let Some(dir) = std::env::current_exe().ok().and_then(|e| e.parent().map(PathBuf::from)) {
        v.push(dir.join(DEPLOYER_EXE));
    }
    let program_files = config::env_path("ProgramW6432")
        .or_else(|| config::env_path("ProgramFiles"))
        .map(|p| p.join(DEFAULT_INSTALL_SUBDIR))
        .unwrap_or_else(|| PathBuf::from(DEFAULT_INSTALL_DIR));
    // The installer's InstallDir (and the default) is the parent of the
    // versioned folder that holds the binaries (Program Files\DaoHui\daohui-<ver>).
    for base in crate::winutil::install_dir_from_registry().into_iter().chain(std::iter::once(program_files)) {
        v.push(base.join(DEPLOYER_EXE));
        v.extend(versioned_dirs(&base).into_iter().map(|d| d.join(DEPLOYER_EXE)));
    }
    v
}

/// `daohui-<version>` folders under `base`, newest version first.
#[cfg(target_os = "windows")]
fn versioned_dirs(base: &Path) -> Vec<PathBuf> {
    let mut dirs: Vec<(String, PathBuf)> = std::fs::read_dir(base)
        .into_iter()
        .flatten()
        .flatten()
        .filter(|e| e.path().is_dir())
        .filter_map(|e| {
            let name = e.file_name().to_string_lossy().to_string();
            name.strip_prefix("daohui-").map(|ver| (ver.to_string(), e.path()))
        })
        .collect();
    dirs.sort_by(|a, b| crate::version::compare(&b.0, &a.0));
    dirs.into_iter().map(|(_, p)| p).collect()
}

#[cfg(target_os = "macos")]
pub fn deployer_candidates() -> Vec<PathBuf> {
    let mut v = Vec::new();
    if let Some(d) = config::env_path(ENV_INSTALL_DIR) {
        v.push(d.join(DEPLOYER_EXE_IN_INSTALL_DIR));
    }
    v.push(PathBuf::from(DEPLOYER_EXE));
    v
}

#[cfg(not(any(target_os = "windows", target_os = "macos")))]
pub fn deployer_candidates() -> Vec<PathBuf> {
    Vec::new()
}

#[cfg(any(target_os = "windows", target_os = "macos"))]
const ARGS: &[&str] = DEPLOYER_ARGS;
#[cfg(not(any(target_os = "windows", target_os = "macos")))]
const ARGS: &[&str] = &[];
#[cfg(any(target_os = "windows", target_os = "macos"))]
const SYNC_ARGS: &[&str] = DEPLOYER_SYNC_ARGS;
#[cfg(not(any(target_os = "windows", target_os = "macos")))]
const SYNC_ARGS: &[&str] = &[];
/// Rime's sync exports and merges every user dictionary: allow more time.
const SYNC_WAIT: Duration = Duration::from_secs(600);

#[cfg(any(target_os = "windows", target_os = "macos"))]
const ASYNC: bool = DEPLOY_IS_ASYNC;
#[cfg(not(any(target_os = "windows", target_os = "macos")))]
const ASYNC: bool = false;

/// Shown when the deployer exits successfully.
fn done_message(deploy_is_async: bool) -> &'static str {
    if deploy_is_async {
        // macOS: `DaoHui --reload` only tells the running input method to
        // redeploy; it reports the result with its own notification.
        "已通知輸入法重新部署，完成後會自動套用。"
    } else {
        "重新部署完成。"
    }
}

pub fn deployer_path() -> Option<PathBuf> {
    deployer_candidates().into_iter().find(|p| p.is_file())
}

fn run(exe: PathBuf) -> Result<String, String> {
    run_args(&exe, ARGS, DEPLOY_WAIT)
}

fn run_args(exe: &Path, args: &[&str], wait: Duration) -> Result<String, String> {
    let mut cmd = Command::new(exe);
    cmd.args(args);
    if let Some(dir) = exe.parent() {
        cmd.current_dir(dir);
    }
    let mut child = cmd.spawn().map_err(|e| format!("無法執行 {}：{e}", exe.display()))?;
    let start = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(status)) if status.success() => return Ok(done_message(ASYNC).into()),
            Ok(Some(status)) => {
                return Err(match status.code() {
                    Some(c) => format!("部署程式回報錯誤（結束代碼 {c}）。"),
                    None => "部署程式異常結束。".into(),
                })
            }
            Ok(None) if start.elapsed() > wait => {
                return Ok("部署仍在背景進行中，完成後輸入法會自動套用。".into())
            }
            Ok(None) => std::thread::sleep(Duration::from_millis(200)),
            Err(e) => return Err(format!("無法取得部署結果：{e}")),
        }
    }
}

fn find_deployer() -> Result<PathBuf, String> {
    deployer_path().ok_or_else(|| {
        let tried: Vec<String> =
            deployer_candidates().iter().map(|p| p.display().to_string()).collect();
        if tried.is_empty() {
            "這個作業系統沒有部署程式。".to_string()
        } else {
            format!("找不到部署程式，已找過：\n{}", tried.join("\n"))
        }
    })
}

/// Redeploys and waits (Windows) or notifies the input method (macOS).
pub fn deploy_blocking() -> Result<String, String> {
    run(find_deployer()?)
}

/// Rime's own sync of the user data: `DaoHuiDeployer.exe /sync` (waits),
/// `DaoHui --sync` (notifies the running input method).
pub fn rime_sync_blocking() -> Result<String, String> {
    let exe = find_deployer()?;
    let msg = run_args(&exe, SYNC_ARGS, SYNC_WAIT)?;
    Ok(if ASYNC {
        "已通知輸入法執行 Rime 同步。".to_string()
    } else if msg == done_message(false) {
        "Rime 同步完成。".to_string()
    } else {
        msg
    })
}

#[tauri::command]
pub async fn redeploy() -> Result<String, String> {
    let exe = find_deployer()?;
    tauri::async_runtime::spawn_blocking(move || run(exe))
        .await
        .map_err(|e| e.to_string())?
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn done_messages() {
        assert_eq!(done_message(false), "重新部署完成。");
        assert!(done_message(true).starts_with("已通知輸入法重新部署"));
        #[cfg(target_os = "windows")]
        assert_eq!(done_message(ASYNC), "重新部署完成。");
        #[cfg(target_os = "macos")]
        {
            assert_eq!(done_message(ASYNC), done_message(true));
            assert_eq!(ARGS, &["--reload"]);
            let ime = PathBuf::from("/Library/Input Methods/DaoHui.app/Contents/MacOS/DaoHui");
            assert!(deployer_candidates().contains(&ime));
        }
    }
}
