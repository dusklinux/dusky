//! Subprocess helpers: timeout execution + detached launch.
//!
//! Nonblocking captured pipes, process-group timeouts, and detached launchers.
//! Timeouts kill the whole process group (same semantics as the Python
//! `start_new_session=True` + `killpg` path).

use std::io::Read;
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};
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
    // Linux >= 7.3 provides pollable process handles. Open before reaping the
    // child, so even an immediate exit cannot race PID reuse. The fd is CLOEXEC.
    let process_fd = unsafe { libc::syscall(libc::SYS_pidfd_open, pid as libc::pid_t, 0u32) };
    if process_fd < 0 {
        unsafe { libc_killpg(pid) };
        let _ = child.wait();
        return None;
    }
    let process_fd = unsafe { OwnedFd::from_raw_fd(process_fd as i32) };
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
                // Once reaped, only descendants holding pipes open can remain.
                // A readable exit fd must not cause a busy loop in that case.
                fd: if status.is_none() {
                    process_fd.as_raw_fd()
                } else {
                    -1
                },
                events: libc::POLLIN,
                revents: 0,
            },
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
        let wait_ms = deadline
            .saturating_duration_since(std::time::Instant::now())
            .as_millis()
            .clamp(1, i32::MAX as u128) as i32;
        let ready = unsafe { libc::poll(fds.as_mut_ptr(), fds.len() as libc::nfds_t, wait_ms) };
        if ready < 0 && std::io::Error::last_os_error().kind() != std::io::ErrorKind::Interrupted {
            unsafe { libc_killpg(pid) };
            let _ = child.wait();
            return None;
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
    true
}

#[cfg(test)]
mod tests {
    use super::*;

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

    #[test]
    fn closed_pipes_do_not_finish_a_still_running_command() {
        let start = std::time::Instant::now();
        let out = run_command(
            &[
                "/usr/bin/bash".into(),
                "-c".into(),
                "exec 1>&- 2>&-; sleep 0.2; exit 7".into(),
            ],
            Duration::from_secs(2),
            true,
        )
        .unwrap();
        assert_eq!(out.code, 7);
        assert!(!out.status);
        assert!(start.elapsed() >= Duration::from_millis(150));
    }

    #[test]
    fn reaped_leader_still_drains_descendant_pipes() {
        let out = run_command(
            &[
                "/usr/bin/bash".into(),
                "-c".into(),
                "(sleep 0.05; printf tail) & printf head; exit 7".into(),
            ],
            Duration::from_secs(2),
            true,
        )
        .unwrap();
        assert_eq!(out.code, 7);
        assert_eq!(out.stdout, "headtail");
    }

    #[test]
    fn repeated_commands_do_not_leak_file_descriptors() {
        const CHILD: &str = "DUSKY_TRAY_TEST_FDS";
        if std::env::var_os(CHILD).is_none() {
            let result = std::process::Command::new(std::env::current_exe().unwrap())
                .args([
                    "--exact",
                    "backend::cmd::tests::repeated_commands_do_not_leak_file_descriptors",
                ])
                .env(CHILD, "1")
                .output()
                .unwrap();
            assert!(
                result.status.success(),
                "{}",
                String::from_utf8_lossy(&result.stdout)
            );
            return;
        }
        let count = || std::fs::read_dir("/proc/self/fd").unwrap().count();
        let before = count();
        for i in 0..200 {
            let out = run_command(
                &["/usr/bin/true".into()],
                Duration::from_secs(2),
                i % 2 == 0,
            )
            .unwrap();
            assert!(out.status);
        }
        assert_eq!(count(), before);
    }
}
