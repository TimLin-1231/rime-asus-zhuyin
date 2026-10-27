//! Finding Google Drive's "My Drive" folder, to suggest
//! `<My Drive>/DaoHui-sync` as the sync folder. Read only.
//!
//! Windows: Google Drive for desktop mounts a drive letter (the mount point
//! is in `HKCU\Software\Google\DriveFS\Share` `SyncTargets`, a protobuf).
//! When "My Drive" is mirrored to a local folder, the drive's root holds a
//! shortcut `我的雲端硬碟.lnk` / `My Drive.lnk` to it; when it is streamed,
//! a folder of that name.
//! macOS: `~/Library/CloudStorage/GoogleDrive-<account>/<My Drive>`, the
//! folder name in the system language.

// The .lnk and protobuf readers are used on Windows only (tested everywhere).
#![cfg_attr(not(target_os = "windows"), allow(dead_code))]

use std::path::{Path, PathBuf};

/// "My Drive" in the languages Google Drive is offered in (most used first).
pub const MY_DRIVE_NAMES: &[&str] = &[
    "My Drive",
    "我的雲端硬碟",
    "我的云端硬盘",
    "マイドライブ",
    "내 드라이브",
    "Meine Ablage",
    "Mon Drive",
    "Mi unidad",
    "Il mio Drive",
    "Meu Drive",
    "O meu disco",
    "Mijn Drive",
    "Мой диск",
    "Mój dysk",
    "Min enhet",
    "Mit drev",
    "Oma Drive",
    "Drive saya",
    "Drive Saya",
    "Ổ của tôi",
    "ไดรฟ์ของฉัน",
    "Drive'ım",
];

/// Other folders at a Drive root.
const NOT_MY_DRIVE: &[&str] = &[
    "Shared drives",
    "共用雲端硬碟",
    "共享云端硬盘",
    "共有ドライブ",
    "Other computers",
    "其他電腦",
    "其他计算机",
    "パソコン",
    "Computers",
    "$RECYCLE.BIN",
    "System Volume Information",
];

/// The entry of a Drive root that is "My Drive": a known name, else the
/// only folder that is not one of the other known ones.
pub fn pick_my_drive(names: &[String]) -> Option<String> {
    if let Some(n) = MY_DRIVE_NAMES.iter().find_map(|k| names.iter().find(|n| n.as_str() == *k)) {
        return Some(n.clone());
    }
    let rest: Vec<&String> = names
        .iter()
        .filter(|n| !n.starts_with('.') && !NOT_MY_DRIVE.contains(&n.as_str()) && !n.ends_with(".lnk"))
        .collect();
    match rest.as_slice() {
        [only] => Some((*only).clone()),
        _ => None,
    }
}

/// Where a Shell Link (`.lnk`) points: its LinkInfo LocalBasePath.
#[derive(Debug, PartialEq, Eq)]
pub enum LnkPath {
    Unicode(String),
    /// In the system's ANSI code page.
    Ansi(Vec<u8>),
}

fn u16_at(d: &[u8], i: usize) -> Option<usize> {
    Some(u16::from_le_bytes(d.get(i..i + 2)?.try_into().ok()?) as usize)
}

fn u32_at(d: &[u8], i: usize) -> Option<usize> {
    Some(u32::from_le_bytes(d.get(i..i + 4)?.try_into().ok()?) as usize)
}

/// [MS-SHLLINK]: header (0x4C bytes), optional LinkTargetIDList, LinkInfo.
pub fn lnk_local_path(d: &[u8]) -> Option<LnkPath> {
    const HAS_ID_LIST: usize = 0x1;
    const HAS_LINK_INFO: usize = 0x2;
    const VOLUME_ID_AND_LOCAL_BASE_PATH: usize = 0x1;
    if u32_at(d, 0)? != 0x4C {
        return None;
    }
    let flags = u32_at(d, 0x14)?;
    if flags & HAS_LINK_INFO == 0 {
        return None;
    }
    let mut pos = 0x4C;
    if flags & HAS_ID_LIST != 0 {
        pos += 2 + u16_at(d, pos)?;
    }
    let info = d.get(pos..pos + u32_at(d, pos)?)?;
    let header_size = u32_at(info, 4)?;
    if u32_at(info, 8)? & VOLUME_ID_AND_LOCAL_BASE_PATH == 0 {
        return None;
    }
    if header_size >= 0x24 {
        let off = u32_at(info, 0x1C)?;
        let units: Vec<u16> = info
            .get(off..)?
            .chunks_exact(2)
            .map(|c| u16::from_le_bytes([c[0], c[1]]))
            .take_while(|&u| u != 0)
            .collect();
        return Some(LnkPath::Unicode(String::from_utf16(&units).ok()?));
    }
    let off = u32_at(info, 0x10)?;
    let bytes: Vec<u8> = info.get(off..)?.iter().copied().take_while(|&b| b != 0).collect();
    (!bytes.is_empty()).then_some(LnkPath::Ansi(bytes))
}

fn varint(d: &[u8], pos: &mut usize) -> Option<u64> {
    let mut v = 0u64;
    for shift in (0..64).step_by(7) {
        let b = *d.get(*pos)?;
        *pos += 1;
        v |= u64::from(b & 0x7f) << shift;
        if b & 0x80 == 0 {
            return Some(v);
        }
    }
    None
}

/// The length-delimited fields of a protobuf message: (field number, bytes).
fn proto_fields(d: &[u8]) -> Vec<(u64, &[u8])> {
    let mut out = Vec::new();
    let mut pos = 0;
    while pos < d.len() {
        let Some(tag) = varint(d, &mut pos) else { break };
        match tag & 7 {
            0 => {
                if varint(d, &mut pos).is_none() {
                    break;
                }
            }
            1 => pos += 8,
            5 => pos += 4,
            2 => {
                let Some(len) = varint(d, &mut pos) else { break };
                let Some(end) = pos.checked_add(len as usize).filter(|&e| e <= d.len()) else { break };
                out.push((tag >> 3, &d[pos..end]));
                pos = end;
            }
            _ => break,
        }
    }
    out
}

/// Mount points ("G:") in DriveFS's `SyncTargets`: field 1 per account,
/// which holds the drive letter as field 2.
pub fn drivefs_mounts(d: &[u8]) -> Vec<String> {
    let mut out = Vec::new();
    for (f, account) in proto_fields(d) {
        if f != 1 {
            continue;
        }
        for (g, v) in proto_fields(account) {
            if let (2, Ok(s)) = (g, std::str::from_utf8(v)) {
                let b = s.as_bytes();
                if b.len() == 2 && b[0].is_ascii_alphabetic() && b[1] == b':' && !out.iter().any(|m| m == s) {
                    out.push(s.to_string());
                }
            }
        }
    }
    out
}

fn dir_names(dir: &Path) -> Vec<String> {
    let Ok(rd) = std::fs::read_dir(dir) else { return Vec::new() };
    let mut v: Vec<String> = rd
        .flatten()
        .filter(|e| e.file_type().map(|t| t.is_dir()).unwrap_or(false))
        .map(|e| e.file_name().to_string_lossy().to_string())
        .collect();
    v.sort();
    v
}

/// Exactly the file's length: Google Drive's virtual drive answers a read
/// past the end of its shortcut files with ERROR_INVALID_PARAMETER, so
/// `fs::read` (which reads until it gets 0 bytes) fails there.
#[cfg(target_os = "windows")]
fn read_exact_len(path: &Path) -> Option<Vec<u8>> {
    use std::io::Read;
    let mut f = std::fs::File::open(path).ok()?;
    let len = f.metadata().ok()?.len();
    if len > 1 << 20 {
        return None;
    }
    let mut data = vec![0u8; len as usize];
    f.read_exact(&mut data).ok()?;
    Some(data)
}

#[cfg(target_os = "windows")]
fn lnk_target(path: &Path) -> Option<PathBuf> {
    let data = read_exact_len(path)?;
    let p = match lnk_local_path(&data)? {
        LnkPath::Unicode(s) => s,
        LnkPath::Ansi(b) => crate::winutil::ansi_to_string(&b),
    };
    let p = PathBuf::from(p);
    p.is_dir().then_some(p)
}

/// "My Drive" under a DriveFS drive root (`G:\`).
#[cfg(target_os = "windows")]
fn my_drive_in_root(root: &Path) -> Option<PathBuf> {
    // Mirrored: a shortcut to the local folder.
    for name in MY_DRIVE_NAMES {
        if let Some(t) = lnk_target(&root.join(format!("{name}.lnk"))) {
            return Some(t);
        }
    }
    let links: Vec<PathBuf> = std::fs::read_dir(root)
        .ok()?
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.extension().is_some_and(|x| x.eq_ignore_ascii_case("lnk")))
        .collect();
    if let [only] = links.as_slice() {
        if let Some(t) = lnk_target(only) {
            return Some(t);
        }
    }
    // Streamed: a folder on the drive.
    pick_my_drive(&dir_names(root)).map(|n| root.join(n))
}

#[cfg(target_os = "windows")]
pub fn detect_my_drive() -> Option<PathBuf> {
    let mut mounts = crate::winutil::hkcu_binary(r"Software\Google\DriveFS\Share", "SyncTargets")
        .map(|d| drivefs_mounts(&d))
        .unwrap_or_default();
    if mounts.is_empty() {
        mounts.push("G:".into()); // Google Drive's default letter
    }
    mounts.iter().find_map(|m| {
        let root = PathBuf::from(format!("{m}\\"));
        root.is_dir().then(|| my_drive_in_root(&root)).flatten()
    })
}

#[cfg(target_os = "macos")]
pub fn detect_my_drive() -> Option<PathBuf> {
    let home = crate::config::env_path("HOME")?;
    let cloud = home.join("Library/CloudStorage");
    let accounts: Vec<String> =
        dir_names(&cloud).into_iter().filter(|n| n.starts_with("GoogleDrive-")).collect();
    for a in accounts {
        let root = cloud.join(a);
        if let Some(n) = pick_my_drive(&dir_names(&root)) {
            return Some(root.join(n));
        }
    }
    // Drive File Stream before macOS 12.1 mounted a volume.
    let volume = PathBuf::from("/Volumes/GoogleDrive");
    pick_my_drive(&dir_names(&volume)).map(|n| volume.join(n))
}

#[cfg(not(any(target_os = "windows", target_os = "macos")))]
pub fn detect_my_drive() -> Option<PathBuf> {
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    fn names(v: &[&str]) -> Vec<String> {
        v.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn my_drive_names() {
        assert_eq!(pick_my_drive(&names(&["共用雲端硬碟", "我的雲端硬碟", "其他電腦"])).as_deref(), Some("我的雲端硬碟"));
        assert_eq!(pick_my_drive(&names(&[".shortcut-targets-by-id", "My Drive", "Shared drives"])).as_deref(), Some("My Drive"));
        // An unknown language: the only folder left.
        assert_eq!(pick_my_drive(&names(&[".Trash", "Min Disk X", "Shared drives"])).as_deref(), Some("Min Disk X"));
        // Ambiguous or nothing: no guess.
        assert_eq!(pick_my_drive(&names(&["A", "B"])), None);
        assert_eq!(pick_my_drive(&names(&["$RECYCLE.BIN", "共用雲端硬碟", "其他電腦"])), None);
    }

    /// A minimal .lnk: header, a 4-byte ID list, LinkInfo with LocalBasePath.
    fn lnk(path: &[u8], unicode: Option<&str>) -> Vec<u8> {
        let mut d = vec![0u8; 0x4C];
        d[0] = 0x4C;
        d[0x14] = 0x83; // HasLinkTargetIDList | HasLinkInfo | IsUnicode
        d.extend([4, 0, 0, 0, 0, 0]); // IDListSize 4 + 4 bytes
        let header: usize = if unicode.is_some() { 0x24 } else { 0x1C };
        let mut info = vec![0u8; header];
        info[4..8].copy_from_slice(&(header as u32).to_le_bytes());
        info[8] = 1; // VolumeIDAndLocalBasePath
        info[0x10..0x14].copy_from_slice(&(header as u32).to_le_bytes());
        info.extend(path);
        info.push(0);
        if let Some(u) = unicode {
            let off = info.len() as u32;
            info[0x1C..0x20].copy_from_slice(&off.to_le_bytes());
            for c in u.encode_utf16() {
                info.extend(c.to_le_bytes());
            }
            info.extend([0, 0]);
        }
        let size = info.len() as u32;
        info[0..4].copy_from_slice(&size.to_le_bytes());
        d.extend(info);
        d.extend([0u8; 8]); // string data / extra blocks follow
        d
    }

    #[test]
    fn shell_link_local_path() {
        assert_eq!(lnk_local_path(&lnk(br"D:\GoogleDrive", None)), Some(LnkPath::Ansi(br"D:\GoogleDrive".to_vec())));
        assert_eq!(
            lnk_local_path(&lnk(b"D:\\?", Some(r"D:\雲端"))),
            Some(LnkPath::Unicode(r"D:\雲端".to_string()))
        );
        assert_eq!(lnk_local_path(b"not a link"), None);
        let mut no_info = lnk(b"C:\\", None);
        no_info[0x14] = 0x01;
        assert_eq!(lnk_local_path(&no_info), None);
        assert_eq!(lnk_local_path(&lnk(b"C:\\", None)[..0x60]), None); // truncated
    }

    /// `cargo test detect_on_this_computer -- --ignored --nocapture`: what
    /// this computer has (reads the registry and the Drive root only).
    #[test]
    #[ignore]
    fn detect_on_this_computer() {
        #[cfg(target_os = "windows")]
        {
            let reg = crate::winutil::hkcu_binary(r"Software\Google\DriveFS\Share", "SyncTargets");
            println!("DriveFS mount points: {:?}", reg.map(|d| drivefs_mounts(&d)));
        }
        println!("My Drive: {:?}", detect_my_drive());
    }

    #[test]
    fn drivefs_sync_targets() {
        // 0a <len> { 0a <len> "123" ; 12 02 "G:" ; 1a <len> "D:\Docs" }
        let mut account = vec![0x0a, 3];
        account.extend(b"123");
        account.extend([0x12, 2]);
        account.extend(b"G:");
        account.extend([0x1a, 7]);
        account.extend(br"D:\Docs");
        let mut d = vec![0x0a, account.len() as u8];
        d.extend(&account);
        d.extend([0x10, 0x96, 0x01]); // a varint field is skipped
        assert_eq!(drivefs_mounts(&d), vec!["G:".to_string()]);
        assert!(drivefs_mounts(&[0x0a, 0x7f, 1]).is_empty()); // truncated
        assert!(drivefs_mounts(&[]).is_empty());
    }
}
