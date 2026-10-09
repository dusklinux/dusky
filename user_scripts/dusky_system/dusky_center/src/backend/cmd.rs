//! Subprocess helpers: timeout execution + detached launch.
//!
//! Nonblocking captured pipes, process-group timeouts, and detached launchers.
//! Timeouts kill the whole process group (same semantics as the Python
//! `start_new_session=True` + `killpg` path).

use std::io::Read;
use std::os::unix::process::CommandExt;
use std::process::{Command, Stdio};
use std::time::Duration;

#[derive(Debug, Clone)]
pub struct CmdOutput {
    pub status: bool,
    pub code: i32,
    pub stdout: String,
    pub stderr: String,
}

/// Run `argv` with a timeout. Returns `None` on timeout / spawn failure.
pub fn run_command(argv: &[String], timeout: Duration, capture: bool) -> Option<CmdOutput> {
    if argv.is_empty() {
        return None;
    }
    let mut cmd = Command::new(&argv[0]);
    if argv.len() > 1 {
        cmd.args(&argv[1..]);
    }
    cmd.stdin(Stdio::null());
    if capture {
        cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
    } else {
        cmd.stdout(Stdio::null()).stderr(Stdio::null());
    }
    // New session => process group leader, so a timeout can kill children too.
    cmd.process_group(0);
    cmd.env("LC_ALL", "C.UTF-8").env("LANG", "C.UTF-8");

    // Query/apply processes belong to this invocation, unlike explicitly
    // detached launchers. Linux terminates them when their worker disappears.
    let parent = unsafe { libc::getpid() };
    unsafe {
        cmd.pre_exec(move || {
            if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL) != 0 {
                return Err(std::io::Error::last_os_error());
            }
            if libc::getppid() != parent {
                libc::_exit(1);
            }
            Ok(())
        });
    }
    let mut child = cmd.spawn().ok()?;
    let pid = child.id();
    use std::os::fd::AsRawFd;
    let mut stdout = child.stdout.take();
    let mut stderr = child.stderr.take();
    for fd in stdout
        .as_ref()
        .map(AsRawFd::as_raw_fd)
        .into_iter()
        .chain(stderr.as_ref().map(AsRawFd::as_raw_fd))
    {
        unsafe {
            let flags = libc::fcntl(fd, libc::F_GETFL);
            if flags < 0 || libc::fcntl(fd, libc::F_SETFL, flags | libc::O_NONBLOCK) < 0 {
                libc_killpg(pid);
                let _ = child.wait();
                return None;
            }
        }
    }
    let mut out = Vec::new();
    let mut err = Vec::new();
    let mut out_done = !capture;
    let mut err_done = !capture;
    let mut status = None;
    let deadline = std::time::Instant::now() + timeout;
    loop {
        if let Some(pipe) = stdout.as_mut() {
            out_done |= drain(pipe, &mut out);
        }
        if let Some(pipe) = stderr.as_mut() {
            err_done |= drain(pipe, &mut err);
        }
        if status.is_none() {
            match child.try_wait() {
                Ok(observed) => status = observed,
                Err(_) => {
                    unsafe {
                        libc_killpg(pid);
                    }
                    let _ = child.wait();
                    return None;
                }
            }
        }
        if let Some(status) = status
            && out_done
            && err_done
        {
            return Some(CmdOutput {
                status: status.success(),
                code: status.code().unwrap_or(-1),
                stdout: String::from_utf8_lossy(&out).into_owned(),
                stderr: String::from_utf8_lossy(&err).into_owned(),
            });
        }
        if std::time::Instant::now() >= deadline {
            unsafe {
                libc_killpg(pid);
            }
            let _ = child.kill();
            let _ = child.wait();
            return None;
        }
        // Drain both pipes while the process runs. Waiting before reading can
        // deadlock once mako history exceeds the kernel's pipe capacity.
        let mut fds = [
            libc::pollfd {
                fd: if out_done {
                    -1
                } else {
                    stdout.as_ref().map_or(-1, AsRawFd::as_raw_fd)
                },
                events: libc::POLLIN,
                revents: 0,
            },
            libc::pollfd {
                fd: if err_done {
                    -1
                } else {
                    stderr.as_ref().map_or(-1, AsRawFd::as_raw_fd)
                },
                events: libc::POLLIN,
                revents: 0,
            },
        ];
        unsafe {
            libc::poll(fds.as_mut_ptr(), 2, 10);
        }
    }
}

fn drain(pipe: &mut impl Read, bytes: &mut Vec<u8>) -> bool {
    let mut buffer = [0u8; 8192];
    // Bound each pass so a continually writing process cannot defeat timeout.
    for _ in 0..32 {
        match pipe.read(&mut buffer) {
            Ok(0) => return true,
            Ok(n) => bytes.extend_from_slice(&buffer[..n]),
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => return false,
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(_) => return true,
        }
    }
    false
}

#[allow(clippy::missing_safety_doc)]
unsafe fn libc_killpg(pid: u32) {
    unsafe {
        libc::killpg(pid as i32, libc::SIGKILL);
    }
}

/// Fire-and-forget launch outside the panel's cgroup.
///
/// Uses `systemd-run --user --scope` when available (same as Python's
/// `execute_cmd(detached=True)`), otherwise falls back to `bash -c` with
/// `setsid`-style detachment.
pub fn execute_detached(cmd: &str) {
    let cmd = cmd.trim();
    if cmd.is_empty() {
        return;
    }
    let expanded = cmd.to_owned();
    let argv: Vec<String> = vec![
        "/usr/bin/systemd-run".into(),
        "--user".into(),
        "--scope".into(),
        "--collect".into(),
        "--quiet".into(),
        "--expand-environment=no".into(),
        "--".into(),
        "/usr/bin/bash".into(),
        "-c".into(),
        expanded,
    ];
    if argv.is_empty() {
        return;
    }
    let mut c = Command::new(&argv[0]);
    if argv.len() > 1 {
        c.args(&argv[1..]);
    }
    c.stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[allow(unused_mut)]
    let mut c = c;
    // Detach: new session so the child outlives the panel refresh workers.
    c.process_group(0);
    if let Ok(mut child) = c.spawn() {
        let _ = std::thread::Builder::new()
            .name("launcher-reaper".into())
            .spawn(move || {
                let _ = child.wait();
            });
    }
}

/// Execute a shell string asynchronously in the background.
pub fn execute_shell_detached(cmd: &str) {
    execute_detached(cmd);
}

/// Run a shell string with timeout and optional output capture.
pub fn run_shell(cmd: &str, timeout: Duration, capture: bool) -> Option<CmdOutput> {
    let argv = vec!["/usr/bin/bash".to_string(), "-c".to_string(), cmd.to_string()];
    run_command(&argv, timeout, capture)
}

/// Atomic text write (tmpfile + rename + fsync), mirroring Python.
pub fn atomic_write_text(path: &std::path::Path, text: &str) -> bool {
    use std::os::unix::fs::OpenOptionsExt;
    let Some(parent) = path.parent() else {
        return false;
    };
    if std::fs::create_dir_all(parent).is_err() {
        return false;
    }
    let tmp = parent.join(format!(
        ".{}.tmp.{}.{}",
        path.file_name().and_then(|s| s.to_str()).unwrap_or("tmp"),
        std::process::id(),
        {
            static SEQUENCE: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
            SEQUENCE.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
        }
    ));
    let Ok(file) = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&tmp)
    else {
        return false;
    };
    use std::io::Write;
    let mut file = file;
    if file.write_all(text.as_bytes()).is_err() {
        let _ = std::fs::remove_file(&tmp);
        return false;
    }
    if file.sync_all().is_err() {
        let _ = std::fs::remove_file(&tmp);
        return false;
    }
    drop(file);
    if std::fs::rename(&tmp, path).is_err() {
        let _ = std::fs::remove_file(&tmp);
        return false;
    }
    // Persist the renamed directory entry as well as the file's contents.
    // Some filesystems do not support directory fsync; match the GTK writer's
    // best effort there after the setting has already been published.
    if let Ok(directory) = std::fs::File::open(parent) {
        let _ = directory.sync_all();
    }
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn timeout_stress_reaps_children_and_continuous_writers() {
        let root = std::env::temp_dir().join(format!("dusky-command-stress-{}", std::process::id()));
        std::fs::create_dir(&root).unwrap();
        for iteration in 0..20 {
            let pidfile = root.join(iteration.to_string());
            let command = format!("/usr/bin/sleep 10 & printf '%s' $! > '{}'; while :; do printf '%8192s' x; printf '%8192s' y >&2; done", pidfile.display());
            let start = std::time::Instant::now();
            assert!(run_shell(&command, Duration::from_millis(50), true).is_none());
            assert!(start.elapsed() < Duration::from_secs(2));
            let pid = std::fs::read_to_string(pidfile).unwrap();
            let stat = std::path::PathBuf::from(format!("/proc/{pid}/stat"));
            let deadline = std::time::Instant::now() + Duration::from_secs(1);
            loop {
                // A killed grandchild may remain briefly as a zombie until
                // the system's subreaper collects it; it is no longer running.
                if std::fs::read_to_string(&stat).map(|raw| raw.split(')').nth(1)
                    .is_some_and(|tail| tail.trim_start().starts_with('Z'))).unwrap_or(true) { break; }
                assert!(std::time::Instant::now() < deadline, "child {pid} survived timeout");
                std::thread::sleep(Duration::from_millis(5));
            }
        }
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn echo_returns_output() {
        let out = run_command(
            &["/usr/bin/echo".to_string(), "hi".to_string()],
            Duration::from_secs(2),
            true,
        )
        .expect("echo should succeed");
        assert!(out.status);
        assert_eq!(out.stdout.trim(), "hi");
    }

    #[test]
    fn drains_both_large_pipes() {
        let out = run_command(
            &[
                "/usr/bin/bash".into(),
                "-c".into(),
                "head -c 262144 /dev/zero; head -c 262144 /dev/zero >&2".into(),
            ],
            Duration::from_secs(2),
            true,
        )
        .unwrap();
        assert!(out.status);
        assert_eq!(out.stdout.len(), 262144);
        assert_eq!(out.stderr.len(), 262144);
    }

    #[test]
    fn inherited_pipe_cannot_defeat_timeout() {
        let start = std::time::Instant::now();
        let out = run_command(
            &[
                "/usr/bin/bash".into(),
                "-c".into(),
                "sleep 5 & exit 0".into(),
            ],
            Duration::from_millis(100),
            true,
        );
        assert!(out.is_none());
        assert!(start.elapsed() < Duration::from_secs(1));
    }

    #[test]
    fn timeout_returns_none() {
        let out = run_command(
            &["/usr/bin/sleep".to_string(), "5".to_string()],
            Duration::from_millis(200),
            true,
        );
        assert!(out.is_none());
    }
}
