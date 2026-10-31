//! AI 修正: what the input method's Ctrl+R needs (lua/asus_fix.lua in the
//! data, DaoHuiAI.cpp in the Windows fork, DaoHuiAI.swift in the macOS one):
//! llama.cpp's server and the Qwen3.5-4B model, downloaded into the folder
//! those look in (streaming, the model resumable, SHA-256 checked). Text is
//! only ever processed on this computer.
//!
//! Windows: `%LOCALAPPDATA%\DaoHui\ai\llama\llama-server.exe` (the Vulkan
//! build, which falls back to the CPU) and `...\ai\<MODEL_FILE>`.
//! macOS: `~/Library/DaoHui/ai/llama-b11559/llama-server` and the model, the
//! same layout the input menu's own download makes.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::Ordering;

use serde::Serialize;
use tauri::{AppHandle, Emitter, State};

use crate::download::{self, Job, ProgressEvent};
use crate::{config, AppState, Busy};

pub const PROGRESS_EVENT: &str = "ai-progress";

/// Qwen3.5-4B (Apache-2.0), 4-bit GGUF converted by unsloth; measured by
/// tools/eval_fix.py (held-out: 544 -> 583 of 720 sentences right).
pub const MODEL_FILE: &str = "Qwen3.5-4B-Q4_K_M.gguf";
const MODEL_URL: &str = "https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/\
e87f176479d0855a907a41277aca2f8ee7a09523/Qwen3.5-4B-Q4_K_M.gguf";
const MODEL_SHA256: &str = "00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4";
const MODEL_SIZE: u64 = 2_740_937_888;

/// llama.cpp b11559 (MIT): (archive URL, SHA-256, size).
#[cfg(target_os = "windows")]
const SERVER: Option<(&str, &str, u64)> = Some((
    "https://github.com/ggml-org/llama.cpp/releases/download/b11559/llama-b11559-bin-win-vulkan-x64.zip",
    "bf2fb2a7fc5d7669ccabbb6de3f6dd6b54058f213b32e731abc62b590f8566dc",
    32_084_119,
));
#[cfg(all(target_os = "macos", target_arch = "aarch64"))]
const SERVER: Option<(&str, &str, u64)> = Some((
    "https://github.com/ggml-org/llama.cpp/releases/download/b11559/llama-b11559-bin-macos-arm64.tar.gz",
    "c6cfeaa709b8d2e47e8a50f6833d22071f7ee7079b0b1a6d704e482afe6ee2c6",
    12_105_699,
));
#[cfg(all(target_os = "macos", not(target_arch = "aarch64")))]
const SERVER: Option<(&str, &str, u64)> = Some((
    "https://github.com/ggml-org/llama.cpp/releases/download/b11559/llama-b11559-bin-macos-x64.tar.gz",
    "630d5ab669876748b4f715def6b1cbbc2a8cf836ca8310cb9e4585d5f61fe8d4",
    11_597_115,
));
#[cfg(not(any(target_os = "windows", target_os = "macos")))]
const SERVER: Option<(&str, &str, u64)> = None;

/// The folder the input method looks in.
pub fn ai_dir() -> Option<PathBuf> {
    #[cfg(target_os = "windows")]
    {
        config::env_path("LOCALAPPDATA").map(|d| d.join("DaoHui").join("ai"))
    }
    #[cfg(target_os = "macos")]
    {
        config::env_path("HOME").map(|d| d.join("Library").join("DaoHui").join("ai"))
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    {
        None
    }
}

fn server_exe(dir: &Path) -> PathBuf {
    if cfg!(target_os = "windows") {
        dir.join("llama").join("llama-server.exe")
    } else {
        dir.join("llama-b11559").join("llama-server")
    }
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AiStatus {
    /// False where there is no build to download (not Windows or macOS).
    pub configured: bool,
    pub dir: String,
    pub server_ready: bool,
    pub model_ready: bool,
    /// Bytes of an unfinished model download (`.part`), resumable.
    pub partial_bytes: u64,
    /// What is still to download.
    pub size_hint: u64,
    pub downloading: bool,
}

fn status(state: &AppState) -> AiStatus {
    let dir = ai_dir();
    let server_ready = dir.as_deref().map(|d| server_exe(d).is_file()).unwrap_or(false);
    let model = dir.as_deref().map(|d| d.join(MODEL_FILE));
    let model_ready = model.as_deref().map(|m| m.is_file()).unwrap_or(false);
    let partial = model
        .as_deref()
        .and_then(|m| fs::metadata(download::part_path(m)).ok())
        .map(|m| m.len())
        .unwrap_or(0);
    let server_size = SERVER.map(|s| s.2).unwrap_or(0);
    AiStatus {
        configured: SERVER.is_some() && dir.is_some(),
        dir: dir.map(|d| d.display().to_string()).unwrap_or_default(),
        server_ready,
        model_ready,
        partial_bytes: partial,
        size_hint: if server_ready { 0 } else { server_size }
            + if model_ready { 0 } else { MODEL_SIZE.saturating_sub(partial) },
        downloading: state.ai_busy.load(Ordering::SeqCst),
    }
}

#[tauri::command]
pub fn ai_status(state: State<'_, AppState>) -> AiStatus {
    status(&state)
}

/// Unpacks the server archive with the system's tar (Windows 10 1803 and
/// later ship a bsdtar that reads .zip).
fn unpack(archive: &Path, dir: &Path) -> Result<(), String> {
    let (tar, target, flags) = if cfg!(target_os = "windows") {
        let system = config::env_path("SystemRoot").unwrap_or_else(|| PathBuf::from(r"C:\Windows"));
        (system.join("System32").join("tar.exe"), dir.join("llama"), "-xf")
    } else {
        (PathBuf::from("/usr/bin/tar"), dir.to_path_buf(), "-xzf")
    };
    if target.exists() && target != dir {
        fs::remove_dir_all(&target).map_err(|e| format!("無法清除舊的 {}：{e}", target.display()))?;
    }
    fs::create_dir_all(&target).map_err(|e| format!("無法建立 {}：{e}", target.display()))?;
    let mut cmd = Command::new(&tar);
    cmd.arg(flags).arg(archive).arg("-C").arg(&target);
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    let out = cmd.output().map_err(|e| format!("無法執行 {}：{e}", tar.display()))?;
    if !out.status.success() {
        return Err(format!("解壓縮失敗：{}", String::from_utf8_lossy(&out.stderr).trim()));
    }
    Ok(())
}

#[tauri::command]
pub async fn download_ai(app: AppHandle, state: State<'_, AppState>) -> Result<AiStatus, String> {
    let (server_url, server_sha, server_size) = SERVER.ok_or("這個系統沒有可下載的 AI 修正程式。")?;
    let dir = ai_dir().ok_or("找不到存放 AI 修正模型的資料夾。")?;
    let _busy = Busy::acquire(&state.ai_busy).ok_or("AI 修正模型正在下載中。")?;
    state.ai_cancel.store(false, Ordering::SeqCst);
    let client = download::client(&app.package_info().version.to_string())?;
    let emitter = app.clone();
    let progress = move |p| {
        let _ = emitter.emit(PROGRESS_EVENT, ProgressEvent::download(p));
    };

    if !server_exe(&dir).is_file() {
        let name = server_url.rsplit('/').next().unwrap_or("llama-server-archive");
        let archive = dir.join(name);
        let job = Job { url: server_url, dest: &archive, sha256: Some(server_sha), length: Some(server_size),
                        resume: false };
        let fetched = download::fetch(&client, &job, &state.ai_cancel, progress.clone()).await;
        if let Err(e) = fetched {
            let _ = app.emit(PROGRESS_EVENT, ProgressEvent::phase("done"));
            return Err(e.to_string());
        }
        let _ = app.emit(PROGRESS_EVENT, ProgressEvent::phase("unpack"));
        let unpacked = {
            let (archive, dir) = (archive.clone(), dir.clone());
            tauri::async_runtime::spawn_blocking(move || unpack(&archive, &dir))
                .await
                .map_err(|e| e.to_string())?
        };
        let _ = fs::remove_file(&archive);
        unpacked?;
        if !server_exe(&dir).is_file() {
            return Err("解壓縮後找不到 llama-server。".into());
        }
    }

    let model = dir.join(MODEL_FILE);
    if !model.is_file() {
        let job = Job { url: MODEL_URL, dest: &model, sha256: Some(MODEL_SHA256), length: Some(MODEL_SIZE),
                        resume: true };
        let fetched = download::fetch(&client, &job, &state.ai_cancel, progress).await;
        let _ = app.emit(PROGRESS_EVENT, ProgressEvent::phase("done"));
        fetched.map_err(|e| e.to_string())?;
    }
    let _ = app.emit(PROGRESS_EVENT, ProgressEvent::phase("done"));
    drop(_busy);
    Ok(status(&state))
}

#[tauri::command]
pub fn cancel_ai_download(state: State<'_, AppState>) {
    state.ai_cancel.store(true, Ordering::SeqCst);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn layout_matches_the_input_methods() {
        let dir = Path::new("x");
        #[cfg(target_os = "windows")]
        assert_eq!(server_exe(dir), dir.join("llama").join("llama-server.exe"));
        #[cfg(target_os = "macos")]
        assert_eq!(server_exe(dir), dir.join("llama-b11559").join("llama-server"));
        assert!(MODEL_URL.ends_with(MODEL_FILE));
        assert_eq!(MODEL_SHA256.len(), 64);
        if let Some((url, sha, size)) = SERVER {
            assert!(url.starts_with("https://github.com/ggml-org/llama.cpp/releases/download/b11559/"));
            assert_eq!(sha.len(), 64);
            assert!(size > 1_000_000);
        }
    }

    #[test]
    #[cfg(target_os = "windows")]
    fn ai_dir_is_local_app_data() {
        let d = ai_dir().expect("LOCALAPPDATA");
        assert!(d.ends_with(Path::new("DaoHui").join("ai")));
    }
}
