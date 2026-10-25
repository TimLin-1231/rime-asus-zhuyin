//! Streaming HTTP download with progress, cancel, resume (HTTP Range) and
//! SHA-256 verification. Shared by the update and model pages.
//!
//! Data goes to `<dest>.part`; only a fully verified file is renamed to
//! `<dest>`. A cancelled or interrupted download keeps the `.part` file so
//! it can be resumed; a hash mismatch deletes it.

use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use reqwest::{header, StatusCode};
use serde::Serialize;
use sha2::{Digest, Sha256};

/// Give up when no data arrives for this long.
const STALL_TIMEOUT: Duration = Duration::from_secs(60);
const POLL: Duration = Duration::from_millis(400);
const PROGRESS_EVERY: Duration = Duration::from_millis(150);

#[derive(Debug, Clone, Copy, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Progress {
    pub downloaded: u64,
    pub total: Option<u64>,
}

/// Payload of the `update-progress` / `model-progress` events.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProgressEvent {
    /// "download", "verify", "launch" or "done".
    pub phase: &'static str,
    pub downloaded: u64,
    pub total: Option<u64>,
}

impl ProgressEvent {
    pub fn download(p: Progress) -> Self {
        ProgressEvent { phase: "download", downloaded: p.downloaded, total: p.total }
    }
    pub fn phase(phase: &'static str) -> Self {
        ProgressEvent { phase, downloaded: 0, total: None }
    }
}

pub struct Job<'a> {
    pub url: &'a str,
    pub dest: &'a Path,
    /// Expected SHA-256 (hex). None skips the check.
    pub sha256: Option<&'a str>,
    pub length: Option<u64>,
    /// Continue an existing `.part` file.
    pub resume: bool,
}

#[derive(Debug)]
pub enum Error {
    Cancelled,
    Status(u16),
    Network(String),
    Io(String),
    Length { expected: u64, actual: u64 },
    Hash { expected: String, actual: String },
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Cancelled => write!(f, "已取消下載。"),
            Error::Status(404) => write!(f, "伺服器上找不到檔案（HTTP 404）。"),
            Error::Status(code) => write!(f, "伺服器回應錯誤（HTTP {code}）。"),
            Error::Network(e) => write!(f, "網路錯誤：{e}"),
            Error::Io(e) => write!(f, "檔案寫入錯誤：{e}"),
            Error::Length { expected, actual } => {
                write!(f, "檔案大小不符（應為 {expected} 位元組，實際 {actual}），已刪除。")
            }
            Error::Hash { expected, actual } => write!(
                f,
                "SHA-256 驗證失敗，檔案已刪除。\n預期：{expected}\n實際：{actual}"
            ),
        }
    }
}

impl From<io::Error> for Error {
    fn from(e: io::Error) -> Self {
        Error::Io(e.to_string())
    }
}

impl From<reqwest::Error> for Error {
    fn from(e: reqwest::Error) -> Self {
        Error::Network(error_chain(&e))
    }
}

/// An error with its sources ("error sending request: ... : dns error").
pub fn error_chain(e: &dyn std::error::Error) -> String {
    let mut s = e.to_string();
    let mut src = e.source();
    while let Some(inner) = src {
        let t = inner.to_string();
        if !s.contains(&t) {
            s.push('：');
            s.push_str(&t);
        }
        src = inner.source();
    }
    s
}

pub fn client(app_version: &str) -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .user_agent(crate::config::user_agent(app_version))
        .connect_timeout(Duration::from_secs(20))
        .build()
        .map_err(|e| error_chain(&e))
}

/// Only https, plus plain http to this machine (local testing).
pub fn check_url(url: &str) -> Result<(), String> {
    let lower = url.to_ascii_lowercase();
    let local = ["http://127.0.0.1", "http://localhost", "http://[::1]"]
        .iter()
        .any(|p| lower.starts_with(p));
    if lower.starts_with("https://") || local {
        Ok(())
    } else {
        Err(format!("不安全的網址，已拒絕：{url}"))
    }
}

pub fn part_path(dest: &Path) -> PathBuf {
    let mut s = dest.as_os_str().to_owned();
    s.push(".part");
    PathBuf::from(s)
}

pub fn to_hex(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        s.push(HEX[(b >> 4) as usize] as char);
        s.push(HEX[(b & 15) as usize] as char);
    }
    s
}

#[cfg(test)]
pub fn sha256_hex(data: &[u8]) -> String {
    to_hex(&Sha256::digest(data))
}

/// Hashes a file, returning the hasher (to continue) and the byte count.
fn hash_prefix(path: &Path) -> io::Result<(Sha256, u64)> {
    let mut f = File::open(path)?;
    let mut h = Sha256::new();
    let mut buf = vec![0u8; 1 << 20];
    let mut n = 0u64;
    loop {
        let k = f.read(&mut buf)?;
        if k == 0 {
            break;
        }
        h.update(&buf[..k]);
        n += k as u64;
    }
    Ok((h, n))
}

pub fn sha256_file(path: &Path) -> io::Result<String> {
    hash_prefix(path).map(|(h, _)| to_hex(&h.finalize()))
}

/// Total size from `Content-Range: bytes */1234` or `bytes 0-9/1234`.
fn content_range_total(headers: &header::HeaderMap) -> Option<u64> {
    let v = headers.get(header::CONTENT_RANGE)?.to_str().ok()?;
    v.rsplit('/').next()?.trim().parse().ok()
}

/// Downloads `job.url` to `job.dest`; returns the file size.
pub async fn fetch(
    client: &reqwest::Client,
    job: &Job<'_>,
    cancel: &AtomicBool,
    mut progress: impl FnMut(Progress) + Send,
) -> Result<u64, Error> {
    let part = part_path(job.dest);
    if let Some(parent) = job.dest.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut resume = job.resume;
    for _attempt in 0..2 {
        let (mut hasher, mut have) = if resume && part.is_file() {
            let p = part.clone();
            tokio::task::spawn_blocking(move || hash_prefix(&p))
                .await
                .map_err(|e| Error::Io(e.to_string()))??
        } else {
            if part.exists() {
                fs::remove_file(&part)?;
            }
            (Sha256::new(), 0)
        };
        if job.length.is_some_and(|len| have > len) {
            resume = false;
            continue;
        }

        let mut req = client.get(job.url);
        if have > 0 {
            req = req.header(header::RANGE, format!("bytes={have}-"));
        }
        let mut resp = req.send().await?;
        let status = resp.status();
        let mut skip_body = false;
        let append = if have > 0 && status == StatusCode::PARTIAL_CONTENT {
            let expected = format!("bytes {have}-");
            let ok = resp
                .headers()
                .get(header::CONTENT_RANGE)
                .and_then(|v| v.to_str().ok())
                .is_some_and(|v| v.starts_with(&expected));
            if !ok {
                resume = false;
                continue;
            }
            true
        } else if have > 0 && status == StatusCode::RANGE_NOT_SATISFIABLE {
            // The partial file may already be complete.
            let total = content_range_total(resp.headers()).or(job.length);
            if total != Some(have) {
                resume = false;
                continue;
            }
            skip_body = true;
            true
        } else if status.is_success() {
            hasher = Sha256::new();
            have = 0;
            false
        } else {
            return Err(Error::Status(status.as_u16()));
        };

        let total = if skip_body {
            Some(have)
        } else if append {
            resp.content_length().map(|n| n + have)
        } else {
            resp.content_length()
        }
        .or(job.length);

        if !skip_body {
            let mut file = if append {
                OpenOptions::new().append(true).open(&part)?
            } else {
                File::create(&part)?
            };
            progress(Progress { downloaded: have, total });
            let mut last_emit = Instant::now();
            let mut last_data = Instant::now();
            loop {
                if cancel.load(Ordering::SeqCst) {
                    file.flush()?;
                    return Err(Error::Cancelled);
                }
                let chunk = match tokio::time::timeout(POLL, resp.chunk()).await {
                    Err(_) => {
                        if last_data.elapsed() > STALL_TIMEOUT {
                            file.flush()?;
                            return Err(Error::Network("連線逾時，沒有收到資料。".into()));
                        }
                        continue;
                    }
                    Ok(r) => r?,
                };
                let Some(chunk) = chunk else { break };
                last_data = Instant::now();
                file.write_all(&chunk)?;
                hasher.update(&chunk);
                have += chunk.len() as u64;
                if last_emit.elapsed() >= PROGRESS_EVERY {
                    progress(Progress { downloaded: have, total });
                    last_emit = Instant::now();
                }
            }
            file.flush()?;
            drop(file);
            progress(Progress { downloaded: have, total });
        }

        if let Some(expected) = job.length {
            if have != expected {
                let _ = fs::remove_file(&part);
                return Err(Error::Length { expected, actual: have });
            }
        } else if let Some(t) = total {
            if have < t {
                return Err(Error::Network("下載未完成，連線中斷；可以再按一次繼續。".into()));
            }
        }
        let actual = to_hex(&hasher.finalize());
        if let Some(expected) = job.sha256 {
            if !actual.eq_ignore_ascii_case(expected.trim()) {
                let _ = fs::remove_file(&part);
                return Err(Error::Hash { expected: expected.trim().to_ascii_lowercase(), actual });
            }
        }
        if job.dest.exists() {
            fs::remove_file(job.dest)?;
        }
        fs::rename(&part, job.dest)?;
        return Ok(have);
    }
    Err(Error::Network("伺服器不接受續傳，重新下載也失敗。".into()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::BufRead;
    use std::net::TcpListener;
    use std::sync::Arc;

    /// A tiny HTTP/1.1 server for one body, honouring `Range: bytes=N-`.
    /// `fail_after`: close the first full response after this many bytes.
    fn serve(body: Vec<u8>, support_range: bool, fail_after: Option<usize>) -> String {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        let body = Arc::new(body);
        std::thread::spawn(move || {
            let mut first = true;
            for stream in listener.incoming() {
                let Ok(mut stream) = stream else { break };
                let mut reader = io::BufReader::new(stream.try_clone().unwrap());
                let mut start = 0usize;
                loop {
                    let mut line = String::new();
                    if reader.read_line(&mut line).unwrap_or(0) == 0 {
                        break;
                    }
                    let l = line.trim().to_ascii_lowercase();
                    if l.is_empty() {
                        break;
                    }
                    if let Some(r) = l.strip_prefix("range: bytes=") {
                        start = r.trim_end_matches('-').parse().unwrap();
                    }
                }
                let n = body.len();
                let head = if start > 0 && support_range {
                    if start >= n {
                        format!("HTTP/1.1 416 Range Not Satisfiable\r\nContent-Range: bytes */{n}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                    } else {
                        format!(
                            "HTTP/1.1 206 Partial Content\r\nContent-Range: bytes {start}-{}/{n}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                            n - 1,
                            n - start
                        )
                    }
                } else {
                    start = 0;
                    format!("HTTP/1.1 200 OK\r\nContent-Length: {n}\r\nConnection: close\r\n\r\n")
                };
                let _ = stream.write_all(head.as_bytes());
                if start < n && !head.contains(" 416 ") {
                    let slice = &body[start..];
                    let cut = if first { fail_after.unwrap_or(slice.len()).min(slice.len()) } else { slice.len() };
                    let _ = stream.write_all(&slice[..cut]);
                }
                first = false;
                let _ = stream.flush();
            }
        });
        format!("http://{addr}/file.bin")
    }

    fn body(n: usize) -> Vec<u8> {
        (0..n).map(|i| (i * 7 % 251) as u8).collect()
    }

    fn tmp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("daohui-dl-test-{}-{name}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        d
    }

    fn test_client() -> reqwest::Client {
        reqwest::Client::builder().no_proxy().build().unwrap()
    }

    #[test]
    fn hex_and_sha() {
        assert_eq!(to_hex(&[0, 15, 255]), "000fff");
        assert_eq!(
            sha256_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        assert_eq!(part_path(Path::new("a/b.gram")), PathBuf::from("a/b.gram.part"));
        assert!(check_url("https://x").is_ok());
        assert!(check_url("http://127.0.0.1:8000/a").is_ok());
        assert!(check_url("http://example.com/a").is_err());
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn downloads_and_verifies() {
        let data = body(300_000);
        let url = serve(data.clone(), true, None);
        let dir = tmp("ok");
        let dest = dir.join("model.gram");
        let sha = sha256_hex(&data);
        let job = Job { url: &url, dest: &dest, sha256: Some(&sha), length: Some(300_000), resume: true };
        let mut events = 0;
        let n = fetch(&test_client(), &job, &AtomicBool::new(false), |_| events += 1).await.unwrap();
        assert_eq!(n, 300_000);
        assert!(events >= 2);
        assert_eq!(fs::read(&dest).unwrap(), data);
        assert_eq!(sha256_file(&dest).unwrap(), sha);
        assert!(!part_path(&dest).exists());
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn hash_mismatch_deletes_part() {
        let data = body(10_000);
        let url = serve(data, true, None);
        let dir = tmp("bad");
        let dest = dir.join("x.exe");
        let wrong = "0".repeat(64);
        let job = Job { url: &url, dest: &dest, sha256: Some(&wrong), length: None, resume: false };
        let err = fetch(&test_client(), &job, &AtomicBool::new(false), |_| {}).await.unwrap_err();
        assert!(matches!(err, Error::Hash { .. }), "{err}");
        assert!(!dest.exists());
        assert!(!part_path(&dest).exists());
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn resumes_an_interrupted_download() {
        let data = body(200_000);
        let sha = sha256_hex(&data);
        // First connection is cut after 50 000 bytes.
        let url = serve(data.clone(), true, Some(50_000));
        let dir = tmp("resume");
        let dest = dir.join("m.gram");
        let job = Job { url: &url, dest: &dest, sha256: Some(&sha), length: Some(200_000), resume: true };
        let first = fetch(&test_client(), &job, &AtomicBool::new(false), |_| {}).await;
        assert!(first.is_err());
        let partial = fs::metadata(part_path(&dest)).map(|m| m.len()).unwrap_or(0);
        assert!(partial > 0 && partial < 200_000, "partial = {partial}");
        let mut seen_start = None;
        fetch(&test_client(), &job, &AtomicBool::new(false), |p| {
            seen_start.get_or_insert(p.downloaded);
        })
        .await
        .unwrap();
        assert_eq!(seen_start, Some(partial)); // continued, not restarted
        assert_eq!(fs::read(&dest).unwrap(), data);
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn restarts_when_range_is_ignored() {
        let data = body(80_000);
        let sha = sha256_hex(&data);
        let url = serve(data.clone(), false, None);
        let dir = tmp("norange");
        let dest = dir.join("m.gram");
        fs::write(part_path(&dest), &data[..1000]).unwrap();
        let job = Job { url: &url, dest: &dest, sha256: Some(&sha), length: None, resume: true };
        fetch(&test_client(), &job, &AtomicBool::new(false), |_| {}).await.unwrap();
        assert_eq!(fs::read(&dest).unwrap(), data);
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn complete_part_file_is_accepted() {
        let data = body(5_000);
        let sha = sha256_hex(&data);
        let url = serve(data.clone(), true, None);
        let dir = tmp("complete");
        let dest = dir.join("m.gram");
        fs::write(part_path(&dest), &data).unwrap();
        let job = Job { url: &url, dest: &dest, sha256: Some(&sha), length: Some(5_000), resume: true };
        fetch(&test_client(), &job, &AtomicBool::new(false), |_| {}).await.unwrap();
        assert_eq!(fs::read(&dest).unwrap(), data);
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn cancel_keeps_part_and_404_is_reported() {
        let data = body(100_000);
        let url = serve(data, true, None);
        let dir = tmp("cancel");
        let dest = dir.join("m.gram");
        let job = Job { url: &url, dest: &dest, sha256: None, length: None, resume: true };
        let err = fetch(&test_client(), &job, &AtomicBool::new(true), |_| {}).await.unwrap_err();
        assert!(matches!(err, Error::Cancelled));
        assert!(!dest.exists());

        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        std::thread::spawn(move || {
            if let Ok((mut s, _)) = listener.accept() {
                let mut buf = [0u8; 1024];
                let _ = s.read(&mut buf);
                let _ = s.write_all(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
            }
        });
        let url404 = format!("http://{addr}/missing");
        let job = Job { url: &url404, dest: &dest, sha256: None, length: None, resume: false };
        let err = fetch(&test_client(), &job, &AtomicBool::new(false), |_| {}).await.unwrap_err();
        assert!(matches!(err, Error::Status(404)));
    }
}
