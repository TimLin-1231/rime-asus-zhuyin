//! 模型: the 410 MB language model `wanxiang-lts-zh-hant.gram`, downloaded
//! into the user dir on first use (streaming, resumable, SHA-256 checked).

use std::fs;
use std::sync::atomic::Ordering;

use serde::Serialize;
use tauri::{AppHandle, Emitter, State};

use crate::download::{self, Job, ProgressEvent};
use crate::{config, AppState, Busy};

pub const PROGRESS_EVENT: &str = "model-progress";

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ModelStatus {
    pub file_name: &'static str,
    pub path: String,
    pub exists: bool,
    pub bytes: u64,
    /// Bytes of an unfinished download (`.part`), resumable.
    pub partial_bytes: u64,
    pub size_hint: u64,
    /// False while the URL / sha256 constants are placeholders.
    pub configured: bool,
    pub url: Option<String>,
    pub sha256: Option<String>,
    pub downloading: bool,
    pub license: &'static str,
    pub source_url: &'static str,
}

fn status(state: &AppState) -> ModelStatus {
    let path = config::model_path();
    let meta = fs::metadata(&path).ok().filter(|m| m.is_file());
    let partial = fs::metadata(download::part_path(&path)).map(|m| m.len()).unwrap_or(0);
    let source = config::model_source();
    ModelStatus {
        file_name: config::MODEL_FILE_NAME,
        path: path.display().to_string(),
        exists: meta.is_some(),
        bytes: meta.map(|m| m.len()).unwrap_or(0),
        partial_bytes: partial,
        size_hint: config::MODEL_SIZE_HINT,
        configured: source.is_some(),
        url: source.as_ref().map(|s| s.0.clone()),
        sha256: source.map(|s| s.1),
        downloading: state.model_busy.load(Ordering::SeqCst),
        license: config::MODEL_LICENSE,
        source_url: config::MODEL_SOURCE_URL,
    }
}

#[tauri::command]
pub fn model_status(state: State<'_, AppState>) -> ModelStatus {
    status(&state)
}

#[tauri::command]
pub async fn download_model(app: AppHandle, state: State<'_, AppState>) -> Result<ModelStatus, String> {
    let (url, sha) = config::model_source()
        .ok_or("模型的下載網址尚未設定（開發中）。")?;
    download::check_url(&url)?;
    let _busy = Busy::acquire(&state.model_busy).ok_or("模型正在下載中。")?;
    state.model_cancel.store(false, Ordering::SeqCst);

    let dest = config::model_path();
    let client = download::client(&app.package_info().version.to_string())?;
    let job = Job { url: &url, dest: &dest, sha256: Some(&sha), length: None, resume: true };
    let emitter = app.clone();
    let result = download::fetch(&client, &job, &state.model_cancel, move |p| {
        let _ = emitter.emit(PROGRESS_EVENT, ProgressEvent::download(p));
    })
    .await;
    let _ = app.emit(PROGRESS_EVENT, ProgressEvent::phase("done"));
    result.map_err(|e| e.to_string())?;
    drop(_busy);
    Ok(status(&state))
}

#[tauri::command]
pub fn cancel_model_download(state: State<'_, AppState>) {
    state.model_cancel.store(true, Ordering::SeqCst);
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct VerifyResult {
    pub sha256: String,
    /// None when there is no expected hash to compare with.
    pub matches: Option<bool>,
}

#[tauri::command]
pub async fn verify_model(app: AppHandle) -> Result<VerifyResult, String> {
    let path = config::model_path();
    if !path.is_file() {
        return Err("模型檔案不存在。".into());
    }
    let _ = app.emit(PROGRESS_EVENT, ProgressEvent::phase("verify"));
    let sha = tauri::async_runtime::spawn_blocking(move || download::sha256_file(&path))
        .await
        .map_err(|e| e.to_string())?
        .map_err(|e| format!("無法讀取模型：{e}"))?;
    let _ = app.emit(PROGRESS_EVENT, ProgressEvent::phase("done"));
    let matches = config::model_source().map(|(_, expected)| expected.eq_ignore_ascii_case(&sha));
    Ok(VerifyResult { sha256: sha, matches })
}
