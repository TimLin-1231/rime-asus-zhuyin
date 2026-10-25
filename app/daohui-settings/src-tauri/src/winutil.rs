//! Small Win32 helpers (Windows only).

use std::ffi::OsStr;
use std::os::windows::ffi::OsStrExt;
use std::path::{Path, PathBuf};

use windows_sys::Win32::System::Registry::{
    RegGetValueW, HKEY, HKEY_LOCAL_MACHINE, RRF_RT_REG_SZ, RRF_SUBKEY_WOW6464KEY,
};
use windows_sys::Win32::UI::Shell::ShellExecuteW;
use windows_sys::Win32::UI::WindowsAndMessaging::SW_SHOWNORMAL;

fn wide(s: &OsStr) -> Vec<u16> {
    s.encode_wide().chain(std::iter::once(0)).collect()
}

/// A REG_SZ value from the 64-bit view of the registry (read only).
fn reg_string(root: HKEY, key: &str, value: &str) -> Option<String> {
    let key = wide(OsStr::new(key));
    let value = wide(OsStr::new(value));
    let mut buf = vec![0u16; 4096];
    let mut size = (buf.len() * 2) as u32;
    // SAFETY: valid NUL-terminated strings; buf holds `size` bytes.
    let rc = unsafe {
        RegGetValueW(
            root,
            key.as_ptr(),
            value.as_ptr(),
            RRF_RT_REG_SZ | RRF_SUBKEY_WOW6464KEY,
            std::ptr::null_mut(),
            buf.as_mut_ptr().cast(),
            &mut size,
        )
    };
    if rc != 0 {
        return None;
    }
    let n = (size as usize / 2).min(buf.len());
    let s = String::from_utf16_lossy(&buf[..n]);
    let s = s.trim_end_matches('\0').trim();
    (!s.is_empty()).then(|| s.to_string())
}

pub fn install_dir_from_registry() -> Option<PathBuf> {
    use crate::config::{INSTALL_DIR_REG_KEY, INSTALL_DIR_REG_VALUE};
    reg_string(HKEY_LOCAL_MACHINE, INSTALL_DIR_REG_KEY, INSTALL_DIR_REG_VALUE).map(PathBuf::from)
}

/// Opens a file the way Explorer does, so an installer that requires
/// elevation gets its UAC prompt.
pub fn shell_open(path: &Path, args: Option<&str>) -> Result<(), String> {
    let verb = wide(OsStr::new("open"));
    let file = wide(path.as_os_str());
    let params = args.map(|a| wide(OsStr::new(a)));
    let dir = path.parent().map(|p| wide(p.as_os_str()));
    // SAFETY: all pointers are NUL-terminated UTF-16 buffers alive for the call.
    let rc = unsafe {
        ShellExecuteW(
            std::ptr::null_mut(),
            verb.as_ptr(),
            file.as_ptr(),
            params.as_ref().map_or(std::ptr::null(), |p| p.as_ptr()),
            dir.as_ref().map_or(std::ptr::null(), |d| d.as_ptr()),
            SW_SHOWNORMAL,
        )
    } as isize;
    if rc > 32 {
        Ok(())
    } else if rc == 5 {
        Err("安裝程式需要系統管理員權限，但權限要求被拒絕了。".into())
    } else {
        Err(format!("無法啟動安裝程式（錯誤碼 {rc}）。"))
    }
}
