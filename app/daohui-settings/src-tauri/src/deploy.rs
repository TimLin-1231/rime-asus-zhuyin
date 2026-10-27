//! 重新部署: runs the input method's deployer.
//! Windows: `<install dir>\DaoHuiDeployer.exe /deploy`;
//! macOS: `/Library/Input Methods/DaoHui.app/Contents/MacOS/DaoHui --reload`.

use std::path::PathBuf;
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
    if let Some(d) = crate::winutil::install_dir_from_registry() {
        v.push(d.join(DEPLOYER_EXE));
    }
    let program_files = config::env_path("ProgramW6432")
        .or_else(|| config::env_path("ProgramFiles"))
        .map(|p| p.join(DEFAULT_INSTALL_SUBDIR))
        .unwrap_or_else(|| PathBuf::from(DEFAULT_INSTALL_DIR));
    v.push(program_files.join(DEPLOYER_EXE));
    v
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
    let mut cmd = Command::new(&exe);
    cmd.args(ARGS);
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
            Ok(None) if start.elapsed() > DEPLOY_WAIT => {
                return Ok("部署仍在背景進行中，完成後輸入法會自動套用。".into())
            }
            Ok(None) => std::thread::sleep(Duration::from_millis(200)),
            Err(e) => return Err(format!("無法取得部署結果：{e}")),
        }
    }
}

#[tauri::command]
pub async fn redeploy() -> Result<String, String> {
    let exe = deployer_path().ok_or_else(|| {
        let tried: Vec<String> =
            deployer_candidates().iter().map(|p| p.display().to_string()).collect();
        if tried.is_empty() {
            "這個作業系統沒有部署程式。".to_string()
        } else {
            format!("找不到部署程式，已找過：\n{}", tried.join("\n"))
        }
    })?;
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
