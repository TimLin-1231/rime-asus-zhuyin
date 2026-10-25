//! Opening links in the browser and folders in Explorer / Finder.

use tauri::AppHandle;
use tauri_plugin_opener::OpenerExt;

use crate::{config, userdata};

#[tauri::command]
pub fn open_link(app: AppHandle, url: String) -> Result<(), String> {
    if !url.starts_with("https://") {
        return Err("只能開啟 https 連結。".into());
    }
    app.opener().open_url(url, None::<&str>).map_err(|e| e.to_string())
}

/// `target`: "user_dir", "backups" or "backup:<name>".
#[tauri::command]
pub fn open_folder(app: AppHandle, target: String) -> Result<(), String> {
    let dir = config::user_dir();
    let path = match target.as_str() {
        "user_dir" => dir,
        "backups" => dir.join(config::BACKUP_DIR_NAME),
        other => match other.strip_prefix("backup:") {
            Some(name) => userdata::backup_path(name)?,
            None => return Err("未知的資料夾。".into()),
        },
    };
    if !path.is_dir() {
        return Err(format!("資料夾不存在：{}", path.display()));
    }
    app.opener()
        .open_path(path.display().to_string(), None::<&str>)
        .map_err(|e| e.to_string())
}
