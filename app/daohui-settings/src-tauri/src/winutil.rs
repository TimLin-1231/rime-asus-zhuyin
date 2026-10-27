//! Small Win32 helpers (Windows only).

use std::ffi::OsStr;
use std::os::windows::ffi::OsStrExt;
use std::path::{Path, PathBuf};

use windows_sys::Win32::Foundation::{HWND, LPARAM};
use windows_sys::Win32::System::Registry::{
    RegGetValueW, HKEY, HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE, RRF_RT_REG_SZ, RRF_SUBKEY_WOW6432KEY,
    RRF_SUBKEY_WOW6464KEY,
};
use windows_sys::Win32::UI::Shell::ShellExecuteW;
use windows_sys::Win32::UI::WindowsAndMessaging::SW_SHOWNORMAL;

fn wide(s: &OsStr) -> Vec<u16> {
    s.encode_wide().chain(std::iter::once(0)).collect()
}

/// A REG_SZ value from the 64-bit view of the registry (read only).
fn reg_string(root: HKEY, key: &str, value: &str) -> Option<String> {
    reg_string_view(root, key, value, RRF_SUBKEY_WOW6464KEY)
}

/// The installed DaoHui's version ("0.17.4.7"): DisplayVersion of its
/// uninstall entry, which the (32-bit) NSIS installer writes to the
/// WOW6432Node view.
pub fn installed_daohui_version() -> Option<String> {
    use crate::config::{UNINSTALL_REG_KEY, UNINSTALL_VERSION_VALUE};
    reg_string_view(HKEY_LOCAL_MACHINE, UNINSTALL_REG_KEY, UNINSTALL_VERSION_VALUE, RRF_SUBKEY_WOW6432KEY)
        .or_else(|| reg_string(HKEY_LOCAL_MACHINE, UNINSTALL_REG_KEY, UNINSTALL_VERSION_VALUE))
}

fn reg_string_view(root: HKEY, key: &str, value: &str, view: u32) -> Option<String> {
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
            RRF_RT_REG_SZ | view,
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

/// The user dir chosen with `DaoHuiSetup.exe /userdir:<dir>` (HKCU).
pub fn user_dir_from_registry() -> Option<PathBuf> {
    use crate::config::{USER_DIR_REG_KEY, USER_DIR_REG_VALUE};
    reg_string(HKEY_CURRENT_USER, USER_DIR_REG_KEY, USER_DIR_REG_VALUE).map(PathBuf::from)
}

/// A REG_BINARY value (HKCU, read only).
pub fn hkcu_binary(key: &str, value: &str) -> Option<Vec<u8>> {
    use windows_sys::Win32::System::Registry::RRF_RT_REG_BINARY;
    let key = wide(OsStr::new(key));
    let value = wide(OsStr::new(value));
    let mut buf = vec![0u8; 64 * 1024];
    let mut size = buf.len() as u32;
    // SAFETY: valid NUL-terminated strings; buf holds `size` bytes.
    let rc = unsafe {
        RegGetValueW(
            HKEY_CURRENT_USER,
            key.as_ptr(),
            value.as_ptr(),
            RRF_RT_REG_BINARY,
            std::ptr::null_mut(),
            buf.as_mut_ptr().cast(),
            &mut size,
        )
    };
    if rc != 0 {
        return None;
    }
    buf.truncate(size as usize);
    Some(buf)
}

/// Text in the system's ANSI code page (e.g. a `.lnk` file's path).
pub fn ansi_to_string(bytes: &[u8]) -> String {
    use windows_sys::Win32::Globalization::{MultiByteToWideChar, CP_ACP};
    if bytes.is_empty() {
        return String::new();
    }
    let mut out = vec![0u16; bytes.len() * 2 + 1];
    // SAFETY: lengths are those of the buffers passed.
    let n = unsafe {
        MultiByteToWideChar(CP_ACP, 0, bytes.as_ptr(), bytes.len() as i32, out.as_mut_ptr(), out.len() as i32)
    };
    String::from_utf16_lossy(&out[..n.max(0) as usize])
}

unsafe extern "system" fn browse_callback(hwnd: HWND, msg: u32, _lparam: LPARAM, data: LPARAM) -> i32 {
    use windows_sys::Win32::UI::Shell::{BFFM_INITIALIZED, BFFM_SETSELECTIONW};
    use windows_sys::Win32::UI::WindowsAndMessaging::SendMessageW;
    if msg == BFFM_INITIALIZED && data != 0 {
        // SAFETY: `data` is the NUL-terminated path passed in BROWSEINFOW.
        unsafe { SendMessageW(hwnd, BFFM_SETSELECTIONW, 1, data) };
    }
    0
}

/// The shell's folder picker; None when cancelled. Runs its own modal loop,
/// so call it off the main thread. `owner`: the window to put it above.
pub fn pick_folder(owner: isize, title: &str, initial: Option<&Path>) -> Option<PathBuf> {
    use windows_sys::Win32::System::Com::{CoInitializeEx, CoTaskMemFree, CoUninitialize, COINIT_APARTMENTTHREADED};
    use windows_sys::Win32::UI::Shell::{
        SHBrowseForFolderW, SHGetPathFromIDListW, BIF_EDITBOX, BIF_NEWDIALOGSTYLE, BIF_RETURNONLYFSDIRS, BROWSEINFOW,
    };
    let title = wide(OsStr::new(title));
    let initial = initial.map(|p| wide(p.as_os_str()));
    let mut name = vec![0u16; 260];
    // SAFETY: COM is initialised for this thread and released below; every
    // pointer in `info` outlives the call; the returned PIDL is freed.
    unsafe {
        let hr = CoInitializeEx(std::ptr::null(), COINIT_APARTMENTTHREADED as u32);
        let info = BROWSEINFOW {
            hwndOwner: owner as HWND,
            pidlRoot: std::ptr::null_mut(),
            pszDisplayName: name.as_mut_ptr(),
            lpszTitle: title.as_ptr(),
            ulFlags: BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE | BIF_EDITBOX,
            lpfn: Some(browse_callback),
            lParam: initial.as_ref().map_or(0, |p| p.as_ptr() as LPARAM),
            iImage: 0,
        };
        let pidl = SHBrowseForFolderW(&info);
        let mut result = None;
        if !pidl.is_null() {
            let mut buf = vec![0u16; 32768];
            if SHGetPathFromIDListW(pidl, buf.as_mut_ptr()) != 0 {
                let n = buf.iter().position(|&c| c == 0).unwrap_or(buf.len());
                result = Some(PathBuf::from(String::from_utf16_lossy(&buf[..n])));
            }
            CoTaskMemFree(pidl as *const _);
        }
        if hr >= 0 {
            CoUninitialize();
        }
        result
    }
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
