//! Every path, file name and URL the app uses, in one place.
//!
//! OS-specific values sit behind `cfg(target_os)`. Environment overrides
//! (`DAOHUI_*`) exist for development and testing only.

use std::path::PathBuf;

// ---------------------------------------------------------------- product

pub const PRODUCT_NAME: &str = "島輝";
pub const PRODUCT_NAME_EN: &str = "DaoHui";
pub const LICENSE: &str = "GPL-3.0-or-later";
pub const LICENSE_URL: &str = "https://www.gnu.org/licenses/gpl-3.0.html";
pub const REPO_URL: &str = "https://github.com/TimLin-1231/rime-asus-zhuyin";

// ---------------------------------------------------------------- updates

// The appcast URL is per platform: `APPCAST_URL` in `platform` below
// (format: app/daohui-settings/UPDATES.md).
pub const SPARKLE_NS: &str = "http://www.andymatuschak.org/xml-namespaces/sparkle";
/// Namespace of our own appcast extensions (`daohui:sha256`).
pub const DAOHUI_NS: &str = "https://github.com/TimLin-1231/rime-asus-zhuyin/ns/appcast";
/// Sub-directory of the system temp dir that receives downloaded installers.
pub const UPDATE_TEMP_SUBDIR: &str = "DaoHui-update";

// ---------------------------------------------------------------- Rime user data

/// Schema patch file edited on the 功能開關 page.
pub const CUSTOM_PATCH_FILE: &str = "asus_zhuyin.custom.yaml";
/// LevelDB user dictionaries are directories named `<dict>.userdb`.
pub const USERDB_SUFFIX: &str = ".userdb";
/// Backups go to `<user dir>/backups/<timestamp>/`.
pub const BACKUP_DIR_NAME: &str = "backups";
/// Files copied into a backup besides the `*.userdb` folders and `*.custom.yaml`.
pub const BACKUP_EXTRA_FILES: &[&str] = &["shortcuts.txt", "emails.txt", "custom_phrase.txt"];

// Patch keys (see rime/asus_zhuyin.schema.yaml and rime/lua/*.lua).
/// asus_lang.lua: show raw keys until a tone completes the syllable. Default false.
pub const KEY_RAW_KEYS: &str = "asus_lang/raw_keys";
/// The `prediction` switch is appended last to `switches` by the schema's
/// `__patch`, immediately before `asus_zhuyin.custom:/patch` is applied, so
/// `@last` addresses it. `reset: 1` (default) = on at session start.
pub const KEY_PREDICTION_RESET: &str = "switches/@last/reset";
/// asus_tab.lua: window for a double Tab, in ms. Default 400.
pub const KEY_TAB_DOUBLE_MS: &str = "asus_tab/double_ms";
/// asus_learn.lua: self-learning on/off. Default true.
pub const KEY_LEARN_ENABLED: &str = "asus_learn/enabled";
/// asus_english.lua: English mode composes words (suggestions, autocorrect)
/// instead of typing each key directly. Default false.
pub const KEY_ENGLISH_COMPOSE: &str = "asus_english/compose";

/// On in the schema that DaoHui ships (tools/package_daohui_data.py).
pub const DEFAULT_RAW_KEYS: bool = true;
pub const DEFAULT_PREDICTION: bool = true;
pub const DEFAULT_TAB_DOUBLE_MS: u32 = 400;
pub const DEFAULT_LEARN_ENABLED: bool = true;
pub const DEFAULT_ENGLISH_COMPOSE: bool = false;
pub const TAB_DOUBLE_MS_RANGE: (u32, u32) = (150, 1500);

// ---------------------------------------------------------------- language model

pub const MODEL_FILE_NAME: &str = "wanxiang-lts-zh-hant.gram";
// Upstream, as in tools/fetch_models.py (amzxyz/RIME-LMDG, CC-BY-4.0). The
// "LTS" tag can be re-uploaded: then the hash no longer matches and the
// download is refused, so a pinned mirror on a DaoHui release is still wanted.
pub const MODEL_URL: &str =
    "https://github.com/amzxyz/RIME-LMDG/releases/download/LTS/wanxiang-lts-zh-hant.gram";
pub const MODEL_SHA256: &str = "53b678ce17f1d80c75dd51976b697a5d1fc2cd6a13670652069b227b40157c98";
/// Approximate size, for display before the download starts.
pub const MODEL_SIZE_HINT: u64 = 410_679_340;
pub const MODEL_LICENSE: &str = "CC-BY-4.0";
pub const MODEL_SOURCE_URL: &str = "https://github.com/amzxyz/RIME-LMDG";

// ---------------------------------------------------------------- environment overrides

pub const ENV_USER_DIR: &str = "DAOHUI_USER_DIR";
pub const ENV_INSTALL_DIR: &str = "DAOHUI_INSTALL_DIR";
pub const ENV_APPCAST_URL: &str = "DAOHUI_APPCAST_URL";
pub const ENV_MODEL_URL: &str = "DAOHUI_MODEL_URL";
pub const ENV_MODEL_SHA256: &str = "DAOHUI_MODEL_SHA256";

// ---------------------------------------------------------------- platform

#[cfg(target_os = "windows")]
mod platform {
    /// Feed of the Windows installer (DaoHui-x.y.z-setup.exe).
    pub const APPCAST_URL: &str =
        "https://raw.githubusercontent.com/TimLin-1231/rime-asus-zhuyin/main/update/appcast.xml";
    /// Rime user data dir: `%APPDATA%\DaoHui` unless the registry names one.
    pub const USER_DIR_APPDATA_SUBDIR: &str = "DaoHui";
    /// Deployer inside the install dir.
    pub const DEPLOYER_EXE: &str = "DaoHuiDeployer.exe";
    pub const DEPLOYER_ARGS: &[&str] = &["/deploy"];
    /// The deployer returns when the deploy is done.
    pub const DEPLOY_IS_ASYNC: bool = false;
    /// Used when the deployer is neither next to this exe nor found via the
    /// registry: `%ProgramFiles%\DaoHui` (or this path if %ProgramFiles% is unset).
    pub const DEFAULT_INSTALL_SUBDIR: &str = "DaoHui";
    pub const DEFAULT_INSTALL_DIR: &str = r"C:\Program Files\DaoHui";
    /// HKLM value written by the installer (D:\src\daohui output\install.nsi).
    pub const INSTALL_DIR_REG_KEY: &str = r"SOFTWARE\DaoHui";
    pub const INSTALL_DIR_REG_VALUE: &str = "InstallDir";
    /// HKCU value written by `DaoHuiSetup.exe /userdir:<dir>`; when set it is
    /// the user dir instead of `%APPDATA%\DaoHui`.
    pub const USER_DIR_REG_KEY: &str = r"Software\DaoHui";
    pub const USER_DIR_REG_VALUE: &str = "RimeUserDir";
    /// `sparkle:os` values accepted for this platform (by arch).
    #[cfg(target_arch = "aarch64")]
    pub const APPCAST_OS: &[&str] = &["windows-arm64"];
    #[cfg(target_arch = "x86_64")]
    pub const APPCAST_OS: &[&str] = &["windows-x64", "windows"];
    #[cfg(not(any(target_arch = "aarch64", target_arch = "x86_64")))]
    pub const APPCAST_OS: &[&str] = &["windows-x86", "windows"];
    /// Installer types we can launch.
    pub const INSTALLER_EXTENSIONS: &[&str] = &["exe"];
}

#[cfg(target_os = "macos")]
mod platform {
    /// The feed the input method's own Sparkle updater reads (its `SUFeedURL`);
    /// this app reads the same one and offers the `.pkg` from it.
    pub const APPCAST_URL: &str =
        "https://raw.githubusercontent.com/TimLin-1231/rime-asus-zhuyin/main/update/appcast-macos.xml";
    /// Rime user data dir of the DaoHui fork of Squirrel: `~/Library/DaoHui`
    /// (relative to $HOME; Squirrel itself uses `~/Library/Rime`). The model,
    /// `asus_zhuyin.custom.yaml`, the `*.userdb` folders and `backups/` live here.
    pub const USER_DIR_HOME_SUBDIR: &str = "Library/DaoHui";
    /// The input method's executable (bundle id
    /// `io.github.timlin-1231.inputmethod.DaoHui`). `--reload` is Squirrel's
    /// "deploy" flag (sources/Main.swift): it posts a distributed notification
    /// to the running input method, which redeploys and reloads, and exits at
    /// once. (`--build` would deploy in this process, but the running input
    /// method would not reload.)
    pub const DEPLOYER_EXE: &str = "/Library/Input Methods/DaoHui.app/Contents/MacOS/DaoHui";
    /// The same, relative to `DAOHUI_INSTALL_DIR` (an "Input Methods" folder).
    pub const DEPLOYER_EXE_IN_INSTALL_DIR: &str = "DaoHui.app/Contents/MacOS/DaoHui";
    pub const DEPLOYER_ARGS: &[&str] = &["--reload"];
    /// `--reload` only notifies the input method; the deploy runs there.
    pub const DEPLOY_IS_ASYNC: bool = true;
    pub const APPCAST_OS: &[&str] = &["macos"];
    pub const INSTALLER_EXTENSIONS: &[&str] = &["pkg"];
    /// The downloaded `.pkg` is opened with `open -b <this>` (Installer.app),
    /// whatever app the user has made the default for `.pkg` files.
    pub const INSTALLER_APP_BUNDLE_ID: &str = "com.apple.installer";
}

#[cfg(not(any(target_os = "windows", target_os = "macos")))]
mod platform {
    /// Not a target yet; keeps the crate building elsewhere.
    pub const APPCAST_URL: &str =
        "https://raw.githubusercontent.com/TimLin-1231/rime-asus-zhuyin/main/update/appcast.xml";
    pub const USER_DIR_HOME_SUBDIR: &str = ".local/share/daohui/rime";
    pub const APPCAST_OS: &[&str] = &["linux"];
    pub const INSTALLER_EXTENSIONS: &[&str] = &[];
}

pub use platform::*;

pub fn env_path(name: &str) -> Option<PathBuf> {
    std::env::var_os(name).filter(|v| !v.is_empty()).map(PathBuf::from)
}

pub fn env_string(name: &str) -> Option<String> {
    std::env::var(name).ok().map(|s| s.trim().to_string()).filter(|s| !s.is_empty())
}

/// The Rime user data dir (`DAOHUI_USER_DIR` overrides it).
pub fn user_dir() -> PathBuf {
    if let Some(p) = env_path(ENV_USER_DIR) {
        return p;
    }
    default_user_dir()
}

#[cfg(target_os = "windows")]
fn default_user_dir() -> PathBuf {
    if let Some(dir) = crate::winutil::user_dir_from_registry() {
        return dir;
    }
    let base = env_path("APPDATA").unwrap_or_else(|| {
        let home = env_path("USERPROFILE").unwrap_or_else(|| PathBuf::from(r"C:\"));
        home.join("AppData").join("Roaming")
    });
    base.join(USER_DIR_APPDATA_SUBDIR)
}

#[cfg(not(target_os = "windows"))]
fn default_user_dir() -> PathBuf {
    env_path("HOME").unwrap_or_else(|| PathBuf::from("/")).join(USER_DIR_HOME_SUBDIR)
}

pub fn appcast_url() -> String {
    env_string(ENV_APPCAST_URL).unwrap_or_else(|| APPCAST_URL.to_string())
}

pub fn model_path() -> PathBuf {
    user_dir().join(MODEL_FILE_NAME)
}

/// (url, sha256) of the model, or None while the constants are placeholders
/// and no environment override is set.
pub fn model_source() -> Option<(String, String)> {
    let url = env_string(ENV_MODEL_URL).unwrap_or_else(|| MODEL_URL.to_string());
    let sha = env_string(ENV_MODEL_SHA256).unwrap_or_else(|| MODEL_SHA256.to_string());
    let placeholder = url.contains("TODO") || url.contains(".invalid") || sha.len() != 64;
    if placeholder {
        None
    } else {
        Some((url, sha.to_ascii_lowercase()))
    }
}

pub fn update_temp_dir() -> PathBuf {
    std::env::temp_dir().join(UPDATE_TEMP_SUBDIR)
}

pub fn user_agent(version: &str) -> String {
    format!("DaoHuiSettings/{version} ({})", std::env::consts::OS)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn user_dir_layout() {
        let dir = default_user_dir();
        #[cfg(target_os = "macos")]
        assert!(dir.ends_with("Library/DaoHui"), "{}", dir.display());
        #[cfg(target_os = "windows")]
        assert!(
            crate::winutil::user_dir_from_registry().is_some() || dir.ends_with("DaoHui"),
            "{}",
            dir.display()
        );
        let _ = dir;
    }
}
