//! 關於: product name, version, license and credits.

use serde::Serialize;
use tauri::AppHandle;

use crate::config;

#[derive(Debug, Serialize)]
pub struct Credit {
    pub name: &'static str,
    pub role: &'static str,
    pub license: &'static str,
    pub url: &'static str,
}

pub const CREDITS: &[Credit] = &[
    Credit {
        name: "RIME 中州韻輸入法引擎（librime）",
        role: "輸入法引擎",
        license: "BSD-3-Clause",
        url: "https://github.com/rime/librime",
    },
    Credit {
        name: "小狼毫 Weasel",
        role: "Windows 版的基礎",
        license: "GPL-3.0",
        url: "https://github.com/rime/weasel",
    },
    Credit {
        name: "鼠鬚管 Squirrel",
        role: "macOS 版的基礎",
        license: "GPL-3.0",
        url: "https://github.com/rime/squirrel",
    },
    Credit {
        name: "librime-lua",
        role: "Lua 擴充（中英混打、預測、學習）",
        license: "BSD-3-Clause",
        url: "https://github.com/hchunhui/librime-lua",
    },
    Credit {
        name: "librime-octagram",
        role: "語言模型外掛",
        license: "GPL-3.0",
        url: "https://github.com/lotem/librime-octagram",
    },
    Credit {
        name: "萬象語言模型 wanxiang（RIME-LMDG，amzxyz）",
        role: "整句選字的語言模型",
        license: "CC-BY-4.0",
        url: "https://github.com/amzxyz/RIME-LMDG",
    },
    Credit {
        name: "小麥注音 McBopomofo",
        role: "台灣詞頻資料",
        license: "MIT",
        url: "https://github.com/openvanilla/McBopomofo",
    },
    Credit {
        name: "地球拼音、注音方案（rime-terra-pinyin、rime-bopomofo）",
        role: "字典與方案",
        license: "LGPL-3.0",
        url: "https://github.com/rime/rime-terra-pinyin",
    },
    Credit {
        name: "easy_en（rime-easy-en）",
        role: "英文詞表",
        license: "LGPL-3.0",
        url: "https://github.com/BlindingDark/rime-easy-en",
    },
    Credit {
        name: "SymSpell",
        role: "英文詞頻",
        license: "MIT",
        url: "https://github.com/wolfgarbe/SymSpell",
    },
    Credit {
        name: "OpenCC",
        role: "字詞對照表",
        license: "Apache-2.0",
        url: "https://github.com/BYVoid/OpenCC",
    },
    Credit {
        name: "Tauri",
        role: "這個設定程式的框架",
        license: "MIT / Apache-2.0",
        url: "https://tauri.app",
    },
];

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AboutInfo {
    pub name: &'static str,
    pub name_en: &'static str,
    pub version: String,
    pub license: &'static str,
    pub license_url: &'static str,
    pub repo_url: &'static str,
    pub credits: &'static [Credit],
    pub os: &'static str,
    pub arch: &'static str,
    pub tauri_version: &'static str,
    pub user_dir: String,
}

#[tauri::command]
pub fn about_info(app: AppHandle) -> AboutInfo {
    AboutInfo {
        name: config::PRODUCT_NAME,
        name_en: config::PRODUCT_NAME_EN,
        version: app.package_info().version.to_string(),
        license: config::LICENSE,
        license_url: config::LICENSE_URL,
        repo_url: config::REPO_URL,
        credits: CREDITS,
        os: std::env::consts::OS,
        arch: std::env::consts::ARCH,
        tauri_version: tauri::VERSION,
        user_dir: config::user_dir().display().to_string(),
    }
}
