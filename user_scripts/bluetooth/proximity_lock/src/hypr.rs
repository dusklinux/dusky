use std::{
    fs, io,
    os::{
        fd::{AsRawFd, FromRawFd, OwnedFd},
        unix::fs::MetadataExt,
    },
    path::{Path, PathBuf},
    process::Stdio,
    time::Duration,
};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::UnixStream,
};

/// Open a stable process handle before checking its identity; avoid PID reuse races.
fn hyprlock_handle() -> Option<OwnedFd> {
    let uid = unsafe { libc::getuid() };
    for entry in fs::read_dir("/proc").ok()?.flatten() {
        let Some(pid) = entry
            .file_name()
            .to_str()
            .and_then(|s| s.parse::<libc::pid_t>().ok())
        else {
            continue;
        };
        if entry.metadata().ok().is_none_or(|m| m.uid() != uid)
            || !fs::read_to_string(entry.path().join("comm"))
                .ok()
                .is_some_and(|s| s.trim() == "hyprlock")
        {
            continue;
        }
        let fd = unsafe { libc::syscall(libc::SYS_pidfd_open, pid, 0u32) } as i32;
        if fd < 0 {
            continue;
        }
        // SAFETY: successful pidfd_open returns a new descriptor owned by us.
        let handle = unsafe { OwnedFd::from_raw_fd(fd) };
        if fs::read_to_string(entry.path().join("comm"))
            .ok()
            .is_some_and(|s| s.trim() == "hyprlock")
        {
            return Some(handle);
        }
    }
    None
}
pub fn is_hyprlock_running() -> bool {
    hyprlock_handle().is_some()
}
pub fn unlock_via_signal() -> bool {
    let Some(handle) = hyprlock_handle() else {
        return false;
    };
    signal_unlock(&handle)
}
fn signal_unlock(handle: &OwnedFd) -> bool {
    // SAFETY: live owned pidfd, SIGUSR1, null siginfo and documented zero flags.
    let result = unsafe {
        libc::syscall(
            libc::SYS_pidfd_send_signal,
            handle.as_raw_fd(),
            libc::SIGUSR1,
            std::ptr::null::<libc::siginfo_t>(),
            0u32,
        )
    };
    if result == 0 {
        eprintln!("[hyprlock] Sent SIGUSR1");
        true
    } else {
        eprintln!("[hyprlock] SIGUSR1: {}", io::Error::last_os_error());
        false
    }
}
fn socket_path() -> io::Result<PathBuf> {
    let runtime = std::env::var_os("XDG_RUNTIME_DIR")
        .map(PathBuf::from)
        .ok_or_else(|| io::Error::other("XDG_RUNTIME_DIR unset"))?;
    let hypr = runtime.join("hypr");
    if let Some(sig) = std::env::var_os("HYPRLAND_INSTANCE_SIGNATURE").filter(|s| !s.is_empty()) {
        return Ok(hypr.join(sig).join(".socket.sock"));
    }
    let mut sockets = fs::read_dir(hypr)?
        .filter_map(Result::ok)
        .map(|e| e.path().join(".socket.sock"))
        .filter(|p| p.exists());
    let path = sockets
        .next()
        .ok_or_else(|| io::Error::other("no Hyprland socket"))?;
    if sockets.next().is_some() {
        return Err(io::Error::other(
            "multiple Hyprland instances; set HYPRLAND_INSTANCE_SIGNATURE",
        ));
    }
    Ok(path)
}
async fn wake_socket(path: &Path) -> io::Result<()> {
    let mut stream = UnixStream::connect(path).await?;
    stream
        .write_all(b"dispatch hl.dsp.dpms({ action = \"enable\" })")
        .await?;
    // Hyprland closes after replying. Read a complete bounded reply, including
    // replies split across socket reads, instead of treating a partial reply as failure.
    let mut reply = Vec::new();
    stream.take(128).read_to_end(&mut reply).await?;
    if std::str::from_utf8(&reply).is_ok_and(|s| s.trim() == "ok") {
        Ok(())
    } else {
        Err(io::Error::other(format!(
            "DPMS response: {}",
            String::from_utf8_lossy(&reply)
        )))
    }
}
pub async fn wake_display_direct() {
    let result = tokio::time::timeout(Duration::from_secs(2), async {
        wake_socket(&socket_path()?).await
    })
    .await;
    match result {
        Ok(Ok(())) => {}
        Ok(Err(e)) => eprintln!("[hyprland] {e}"),
        Err(_) => eprintln!("[hyprland] DPMS timed out"),
    }
}
/// Commands within an event run in order; event tasks may overlap so a long-running
/// locker never prevents its corresponding approach/unlock action.
pub fn execute_script_series(
    scripts: Vec<String>,
    event: &'static str,
    device_mac: String,
    device_name: String,
    rssi: Option<i16>,
) {
    if scripts.is_empty() {
        return;
    }
    tokio::spawn(async move {
        let rssi = rssi.map(|r| r.to_string()).unwrap_or_else(|| "N/A".into());
        let state = match event {
            "near" => "NEAR",
            "far" => "FAR",
            _ => "UNKNOWN",
        };
        for script in scripts {
            if event == "far"
                && (script == "hyprlock" || script == crate::config::DEFAULT_LOCK_COMMAND)
                && is_hyprlock_running()
            {
                eprintln!("[hyprlock] Already running; skipping default lock command");
                continue;
            }
            eprintln!("[hook] {event}: {script}");
            let result = tokio::process::Command::new("/bin/sh")
                .args(["-c", &script])
                .env("PROXIMITY_EVENT", event)
                .env("PROXIMITY_STATE", state)
                .env("PROXIMITY_DEVICE_MAC", &device_mac)
                .env("PROXIMITY_DEVICE_NAME", &device_name)
                .env("PROXIMITY_RSSI", &rssi)
                .stdin(Stdio::null())
                .stdout(Stdio::inherit())
                .stderr(Stdio::inherit())
                .status()
                .await;
            match result {
                Ok(s) if s.success() => {}
                Ok(s) => eprintln!("[hook] {event}: exit {s}"),
                Err(e) => eprintln!("[hook] {event}: {e}"),
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn stable_pidfd_signals_only_its_child_and_detects_exit() {
        use std::os::unix::process::ExitStatusExt;
        let mut child = std::process::Command::new("/usr/bin/sleep")
            .arg("30")
            .spawn()
            .unwrap();
        let fd = unsafe { libc::syscall(libc::SYS_pidfd_open, child.id(), 0u32) } as i32;
        assert!(fd >= 0);
        let handle = unsafe { OwnedFd::from_raw_fd(fd) };
        assert!(signal_unlock(&handle));
        assert_eq!(child.wait().unwrap().signal(), Some(libc::SIGUSR1));
        assert!(!signal_unlock(&handle));
    }
    #[tokio::test]
    async fn dpms_handles_fragmented_replies() {
        let tmp = tempfile::tempdir().unwrap();
        let path = tmp.path().join("hypr.sock");
        let listener = tokio::net::UnixListener::bind(&path).unwrap();
        let server = tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.unwrap();
            let expected = b"dispatch hl.dsp.dpms({ action = \"enable\" })";
            let mut request = vec![0u8; expected.len()];
            stream.read_exact(&mut request).await.unwrap();
            assert_eq!(request, expected);
            stream.write_all(b"o").await.unwrap();
            tokio::time::sleep(Duration::from_millis(2)).await;
            stream.write_all(b"k").await.unwrap();
        });
        tokio::time::timeout(Duration::from_secs(2), wake_socket(&path))
            .await
            .unwrap()
            .unwrap();
        server.await.unwrap();
    }
}
