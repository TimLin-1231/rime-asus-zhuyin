//! 功能開關: reads and writes the user's `asus_zhuyin.custom.yaml`.

use std::fs;
use std::path::Path;

use serde::{Deserialize, Serialize};

use crate::config::{self, *};
use crate::deploy;
use crate::yaml_patch::PatchFile;

/// Written when the patch file does not exist yet.
const NEW_FILE: &str = "# 島輝 使用者設定（asus_zhuyin 方案的修補檔）\n\
# encoding: utf-8\n\
#\n\
# 由「島輝 設定」寫入；也可以手動編輯，其他設定和註解都會保留。\n\
# 修改後要「重新部署」才會生效。\n\
\n\
patch:\n";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FeatureSettings {
    /// asus_lang/raw_keys
    pub raw_keys: bool,
    /// switches/@last/reset (the `prediction` switch)
    pub prediction: bool,
    /// asus_tab/double_ms
    pub tab_double_ms: u32,
    /// asus_learn/enabled
    pub learn_enabled: bool,
    /// asus_english/compose: English mode composes words with suggestions
    /// and autocorrect instead of typing each key directly
    pub english_compose: bool,
}

impl Default for FeatureSettings {
    fn default() -> Self {
        FeatureSettings {
            raw_keys: DEFAULT_RAW_KEYS,
            prediction: DEFAULT_PREDICTION,
            tab_double_ms: DEFAULT_TAB_DOUBLE_MS,
            learn_enabled: DEFAULT_LEARN_ENABLED,
            english_compose: DEFAULT_ENGLISH_COMPOSE,
        }
    }
}

fn read_prediction(doc: &PatchFile) -> Option<bool> {
    doc.get_int(KEY_PREDICTION_RESET)
        .map(|v| v != 0)
        .or_else(|| doc.get_bool(KEY_PREDICTION_RESET))
}

fn read_tab_ms(doc: &PatchFile) -> Option<u32> {
    doc.get_int(KEY_TAB_DOUBLE_MS)
        .filter(|v| *v > 0)
        .map(|v| v.min(u32::MAX as i64) as u32)
}

impl FeatureSettings {
    pub fn read(doc: &PatchFile) -> Self {
        let d = Self::default();
        FeatureSettings {
            raw_keys: doc.get_bool(KEY_RAW_KEYS).unwrap_or(d.raw_keys),
            prediction: read_prediction(doc).unwrap_or(d.prediction),
            tab_double_ms: read_tab_ms(doc).unwrap_or(d.tab_double_ms),
            learn_enabled: doc.get_bool(KEY_LEARN_ENABLED).unwrap_or(d.learn_enabled),
            english_compose: doc.get_bool(KEY_ENGLISH_COMPOSE).unwrap_or(d.english_compose),
        }
    }

    pub fn clamped(mut self) -> Self {
        let (lo, hi) = TAB_DOUBLE_MS_RANGE;
        self.tab_double_ms = self.tab_double_ms.clamp(lo, hi);
        self
    }

    /// Writes only what changed: keys already in the file are updated when
    /// their value differs (or does not parse); absent keys are added only
    /// when the new value differs from the schema default.
    pub fn write(&self, doc: &mut PatchFile) -> Result<(), String> {
        let cur = Self::read(doc);
        let bad = |doc: &PatchFile, key: &str, parsed: bool| doc.contains(key) && !parsed;

        let parsed = doc.get_bool(KEY_RAW_KEYS).is_some();
        if self.raw_keys != cur.raw_keys || bad(doc, KEY_RAW_KEYS, parsed) {
            doc.set(KEY_RAW_KEYS, if self.raw_keys { "true" } else { "false" })?;
        }
        let parsed = read_prediction(doc).is_some();
        if self.prediction != cur.prediction || bad(doc, KEY_PREDICTION_RESET, parsed) {
            doc.set(KEY_PREDICTION_RESET, if self.prediction { "1" } else { "0" })?;
        }
        let parsed = read_tab_ms(doc).is_some();
        if self.tab_double_ms != cur.tab_double_ms || bad(doc, KEY_TAB_DOUBLE_MS, parsed) {
            doc.set(KEY_TAB_DOUBLE_MS, &self.tab_double_ms.to_string())?;
        }
        let parsed = doc.get_bool(KEY_LEARN_ENABLED).is_some();
        if self.learn_enabled != cur.learn_enabled || bad(doc, KEY_LEARN_ENABLED, parsed) {
            doc.set(KEY_LEARN_ENABLED, if self.learn_enabled { "true" } else { "false" })?;
        }
        let parsed = doc.get_bool(KEY_ENGLISH_COMPOSE).is_some();
        if self.english_compose != cur.english_compose || bad(doc, KEY_ENGLISH_COMPOSE, parsed) {
            doc.set(KEY_ENGLISH_COMPOSE, if self.english_compose { "true" } else { "false" })?;
        }
        Ok(())
    }
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FeaturesState {
    pub settings: FeatureSettings,
    pub defaults: FeatureSettings,
    pub path: String,
    pub exists: bool,
    pub user_dir: String,
    pub user_dir_exists: bool,
    pub tab_min: u32,
    pub tab_max: u32,
    /// Set when the file cannot be edited automatically.
    pub warning: Option<String>,
    pub deployer: Option<String>,
}

fn read_text(path: &Path) -> Result<Option<String>, String> {
    match fs::read(path) {
        Ok(bytes) => String::from_utf8(bytes)
            .map(Some)
            .map_err(|_| format!("{} 不是 UTF-8 編碼，無法讀取。", path.display())),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(format!("無法讀取 {}：{e}", path.display())),
    }
}

/// Patch text after applying `settings` to `existing` (or a new file).
pub fn apply_to_text(existing: Option<&str>, settings: &FeatureSettings) -> Result<String, String> {
    let mut doc = PatchFile::parse(existing.unwrap_or(NEW_FILE));
    doc.check()?;
    settings.clamped().write(&mut doc)?;
    Ok(doc.render())
}

fn state_for(dir: &Path) -> Result<FeaturesState, String> {
    let path = dir.join(CUSTOM_PATCH_FILE);
    let text = read_text(&path)?;
    let doc = PatchFile::parse(text.as_deref().unwrap_or(""));
    let (lo, hi) = TAB_DOUBLE_MS_RANGE;
    Ok(FeaturesState {
        settings: FeatureSettings::read(&doc),
        defaults: FeatureSettings::default(),
        path: path.display().to_string(),
        exists: text.is_some(),
        user_dir: dir.display().to_string(),
        user_dir_exists: dir.is_dir(),
        tab_min: lo,
        tab_max: hi,
        warning: doc.check().err(),
        deployer: deploy::deployer_path().map(|p| p.display().to_string()),
    })
}

/// Writes atomically and keeps the previous version as `<file>.bak`.
pub(crate) fn save_in(dir: &Path, settings: &FeatureSettings) -> Result<bool, String> {
    let path = dir.join(CUSTOM_PATCH_FILE);
    let existing = read_text(&path)?;
    let out = apply_to_text(existing.as_deref(), settings)?;
    if existing.as_deref() == Some(out.as_str()) {
        return Ok(false);
    }
    fs::create_dir_all(dir).map_err(|e| format!("無法建立 {}：{e}", dir.display()))?;
    if existing.is_some() {
        let mut bak = path.clone().into_os_string();
        bak.push(".bak");
        let _ = fs::copy(&path, bak);
    }
    let mut tmp = path.clone().into_os_string();
    tmp.push(".tmp");
    fs::write(&tmp, out.as_bytes()).map_err(|e| format!("無法寫入設定：{e}"))?;
    fs::rename(&tmp, &path).map_err(|e| format!("無法寫入 {}：{e}", path.display()))?;
    Ok(true)
}

#[tauri::command]
pub fn get_features() -> Result<FeaturesState, String> {
    state_for(&config::user_dir())
}

#[tauri::command]
pub fn save_features(settings: FeatureSettings) -> Result<FeaturesState, String> {
    let dir = config::user_dir();
    save_in(&dir, &settings)?;
    state_for(&dir)
}

#[cfg(test)]
mod tests {
    use super::*;

    const REPO_SAMPLE: &str = include_str!("../../../../rime/asus_zhuyin.custom.yaml");

    #[test]
    fn defaults_when_missing() {
        let s = FeatureSettings::read(&PatchFile::parse(""));
        assert_eq!(s, FeatureSettings::default());
        assert!(s.prediction && s.learn_enabled && s.raw_keys);
        assert_eq!(s.tab_double_ms, 400);
    }

    #[test]
    fn repo_sample_round_trip() {
        let s = FeatureSettings::read(&PatchFile::parse(REPO_SAMPLE));
        assert!(s.raw_keys);
        // Saving unchanged settings leaves the file byte-for-byte equal.
        assert_eq!(apply_to_text(Some(REPO_SAMPLE), &s).unwrap(), REPO_SAMPLE);
        // Changing one value touches one line and appends the others only
        // when they differ from the defaults.
        let t = FeatureSettings { raw_keys: false, prediction: false, ..s };
        let out = apply_to_text(Some(REPO_SAMPLE), &t).unwrap();
        let expected = REPO_SAMPLE.replace("asus_lang/raw_keys: true", "asus_lang/raw_keys: false");
        let expected = format!("{}  \"switches/@last/reset\": 0\n", expected);
        assert_eq!(out.replace("\r\n", "\n"), expected.replace("\r\n", "\n"));
        assert_eq!(FeatureSettings::read(&PatchFile::parse(&out)), t);
    }

    #[test]
    fn new_file_has_header_and_only_changes() {
        let t = FeatureSettings { tab_double_ms: 9999, learn_enabled: false, ..Default::default() };
        let out = apply_to_text(None, &t).unwrap();
        assert!(out.starts_with("# 島輝 使用者設定"));
        assert!(out.ends_with("patch:\n  asus_tab/double_ms: 1500\n  asus_learn/enabled: false\n"));
        let back = FeatureSettings::read(&PatchFile::parse(&out));
        assert_eq!(back.tab_double_ms, 1500); // clamped
        assert!(!back.learn_enabled);
    }

    #[test]
    fn unparsable_values_are_rewritten() {
        let src = "patch:\n  asus_learn/enabled: perhaps\n  asus_tab/double_ms: fast\n";
        let out = apply_to_text(Some(src), &FeatureSettings::default()).unwrap();
        assert_eq!(out, "patch:\n  asus_learn/enabled: true\n  asus_tab/double_ms: 400\n");
    }

    #[test]
    fn save_writes_file_and_backup() {
        let dir = std::env::temp_dir().join(format!("daohui-features-test-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        let t = FeatureSettings { raw_keys: true, ..Default::default() };
        assert!(save_in(&dir, &t).unwrap());
        assert!(!save_in(&dir, &t).unwrap()); // unchanged: no write
        let state = state_for(&dir).unwrap();
        assert!(state.exists && state.settings.raw_keys);
        let t2 = FeatureSettings { raw_keys: false, ..t };
        assert!(save_in(&dir, &t2).unwrap());
        assert!(dir.join("asus_zhuyin.custom.yaml.bak").exists());
        assert!(!state_for(&dir).unwrap().settings.raw_keys);
        let _ = fs::remove_dir_all(&dir);
    }
}
