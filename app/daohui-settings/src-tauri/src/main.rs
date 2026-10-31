// 島輝 設定與更新 (DaoHui settings & updates).
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod about;
mod ai;
mod autosync;
mod config;
mod deploy;
mod download;
mod features;
mod gdrive;
mod model;
mod shell;
mod sync;
mod sync_merge;
mod updates;
mod userdata;
mod version;
#[cfg(target_os = "windows")]
mod winutil;
mod yaml_patch;

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

/// State shared by the commands.
#[derive(Default)]
pub struct AppState {
    /// The release found by the last update check; `install_update` only ever
    /// installs this one (the frontend cannot pass a URL).
    pub release: Mutex<Option<updates::Release>>,
    pub update_busy: AtomicBool,
    pub update_cancel: AtomicBool,
    pub model_busy: AtomicBool,
    pub model_cancel: AtomicBool,
    pub ai_busy: AtomicBool,
    pub ai_cancel: AtomicBool,
    pub sync_busy: AtomicBool,
}

/// Holds a busy flag for as long as it lives.
pub struct Busy<'a>(&'a AtomicBool);

impl<'a> Busy<'a> {
    pub fn acquire(flag: &'a AtomicBool) -> Option<Self> {
        flag.compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
            .ok()
            .map(|_| Busy(flag))
    }
}

impl Drop for Busy<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::SeqCst);
    }
}

fn main() {
    // `--sync`: the headless sync run (Task Scheduler / LaunchAgent); no
    // window, no Tauri.
    if std::env::args().skip(1).any(|a| a == config::SYNC_FLAG) {
        std::process::exit(sync::cli_main());
    }
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(AppState::default())
        .invoke_handler(tauri::generate_handler![
            about::about_info,
            shell::open_link,
            shell::open_folder,
            updates::check_update,
            updates::install_update,
            updates::cancel_update_download,
            features::get_features,
            features::save_features,
            deploy::redeploy,
            userdata::userdata_info,
            userdata::backup_now,
            userdata::restore_backup,
            model::model_status,
            model::download_model,
            model::cancel_model_download,
            model::verify_model,
            ai::ai_status,
            ai::download_ai,
            ai::cancel_ai_download,
            sync::sync_info,
            sync::set_sync_dir,
            sync::pick_sync_dir,
            sync::sync_now,
            sync::set_auto_sync,
        ])
        .run(tauri::generate_context!())
        .expect("error while running the DaoHui settings app");
}
