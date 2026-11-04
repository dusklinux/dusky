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

// Keep active finite-command groups owned until the application runner exits.
// Detached application launchers do not use this registry.
struct Commands {
    closing: bool,
    groups: Vec<u32>,
}
static COMMANDS: std::sync::Mutex<Commands> = std::sync::Mutex::new(Commands {
    closing: false,
    groups: Vec::new(),
});

struct ActiveGroup(Option<u32>);
impl ActiveGroup {
    fn release(&mut self) {
        if let Some(pid) = self.0.take() {
            COMMANDS
                .lock()
                .unwrap()
                .groups
                .retain(|active| *active != pid);
        }
    }
}
impl Drop for ActiveGroup {
    fn drop(&mut self) {
        self.release();
    }
}

/// Called after the UI has drained writes and its runner returns (also on a
/// graphics initialization error). Kill still-running query groups, including
/// descendants. Serialize with spawn so no command can escape this transition.
pub fn shutdown() {
    let mut commands = COMMANDS.lock().unwrap();
    commands.closing = true;
    for &pid in &commands.groups {
        unsafe { libc_killpg(pid) };
    }
}

/// Run `argv` with a timeout. Returns `None` on timeout / spawn failure.
pub fn run_command(argv: &[String], timeout: Duration, capture: bool) -> Option<CmdOutput> {
    if argv.is_empty() {
        return None;
    }
    let deadline = std::time::Instant::now().checked_add(timeout)?;
    let mut cmd = Command::new(&argv[0]);
    crate::gpu::inherit_session_environment(&mut cmd);
    if argv.len() > 1 {
        cmd.args(&argv[1..]);
    }
    cmd.stdin(Stdio::null());
    if capture {
        cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
    } else {
        cmd.stdout(Stdio::null()).stderr(Stdio::null());
    }
    // A separate process group lets timeout/shutdown kill command descendants.
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
    let mut commands = COMMANDS.lock().unwrap();
    if commands.closing {
        return None;
    }
    let mut child = cmd.spawn().ok()?;
    let pid = child.id();
    // Linux >= 7.3 provides pollable process handles. Open before reaping the
    // child, so even an immediate exit cannot race PID reuse. The fd is CLOEXEC.
    let process_fd = unsafe { libc::syscall(libc::SYS_pidfd_open, pid as libc::pid_t, 0u32) };
    if process_fd < 0 {
        unsafe { libc_killpg(pid) };
        drop(commands);
        let _ = child.wait();
        return None;
    }
    let process_fd = unsafe { OwnedFd::from_raw_fd(process_fd as i32) };
    commands.groups.push(pid);
    let mut active_group = ActiveGroup(Some(pid));
    drop(commands);
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
                active_group.release();
                let _ = child.wait();
                return None;
            }
        }
    }
    let mut out = Vec::new();
    let mut err = Vec::new();
    let mut out_done = !capture;
    let mut err_done = !capture;
    let mut exited = false;
    loop {
        if !out_done && let Some(pipe) = stdout.as_mut() {
            out_done |= drain(pipe, &mut out);
        }
        if !err_done && let Some(pipe) = stderr.as_mut() {
            err_done |= drain(pipe, &mut err);
        }
        // Keep the leader unreaped until pipes finish. Its PID cannot be reused
        // while timeout/shutdown may still need to signal the process group.
        if exited && out_done && err_done {
            active_group.release();
            let status = child.wait().ok()?;
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
            active_group.release();
            let _ = child.kill();
            let _ = child.wait();
            return None;
        }
        // Drain both pipes while the process runs. Waiting before reading can
        // deadlock once mako history exceeds the kernel's pipe capacity.
        let mut fds = [
            libc::pollfd {
                // Once exited, wait only for the remaining descendant pipes.
                // A readable exit fd must not cause a busy loop.
                fd: if !exited { process_fd.as_raw_fd() } else { -1 },
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
            .as_nanos()
            .div_ceil(1_000_000)
            .clamp(1, i32::MAX as u128) as i32;
        let ready = unsafe { libc::poll(fds.as_mut_ptr(), fds.len() as libc::nfds_t, wait_ms) };
        if ready < 0 && std::io::Error::last_os_error().kind() != std::io::ErrorKind::Interrupted {
            unsafe { libc_killpg(pid) };
            active_group.release();
            let _ = child.wait();
            return None;
        }
        exited |= fds[0].revents & (libc::POLLIN | libc::POLLHUP) != 0;
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
/// `execute_cmd(detached=True)`). The launcher gets its own process group.
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
    let mut c = Command::new(&argv[0]);
    crate::gpu::inherit_session_environment(&mut c);
    if argv.len() > 1 {
        c.args(&argv[1..]);
    }
    c.stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    // This launcher is deliberately independent of query-group shutdown.
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
    let argv = vec![
        "/usr/bin/bash".to_string(),
        "-c".to_string(),
        cmd.to_string(),
    ];
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
        let root =
            std::env::temp_dir().join(format!("dusky-command-stress-{}", std::process::id()));
        std::fs::create_dir(&root).unwrap();
        for iteration in 0..20 {
            let pidfile = root.join(iteration.to_string());
            let command = format!(
                "/usr/bin/sleep 10 & printf '%s' $! > '{}'; while :; do printf '%8192s' x; printf '%8192s' y >&2; done",
                pidfile.display()
            );
            let start = std::time::Instant::now();
            assert!(run_shell(&command, Duration::from_millis(50), true).is_none());
            assert!(start.elapsed() < Duration::from_secs(2));
            let pid = std::fs::read_to_string(pidfile).unwrap();
            let stat = std::path::PathBuf::from(format!("/proc/{pid}/stat"));
            let deadline = std::time::Instant::now() + Duration::from_secs(1);
            loop {
                // A killed grandchild may remain briefly as a zombie until
                // the system's subreaper collects it; it is no longer running.
                if std::fs::read_to_string(&stat)
                    .map(|raw| {
                        raw.split(')')
                            .nth(1)
                            .is_some_and(|tail| tail.trim_start().starts_with('Z'))
                    })
                    .unwrap_or(true)
                {
                    break;
                }
                assert!(
                    std::time::Instant::now() < deadline,
                    "child {pid} survived timeout"
                );
                std::thread::sleep(Duration::from_millis(5));
            }
        }
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn shutdown_stops_active_groups_and_rejects_new_commands() {
        const FIXTURE: &str = "DUSKY_CENTER_SHUTDOWN_FIXTURE";
        if let Some(root) = std::env::var_os(FIXTURE) {
            let root = std::path::PathBuf::from(root);
            struct Shutdown;
            impl Drop for Shutdown {
                fn drop(&mut self) {
                    shutdown();
                }
            }
            let _cleanup = Shutdown;
            let count = || std::fs::read_dir("/proc/self/fd").unwrap().count();
            let before = count();
            let workers: Vec<_> = (0..4)
                .map(|index| {
                    let marker = root.join(index.to_string());
                    std::thread::spawn(move || {
                        run_command(&[
                    "/usr/bin/bash".into(), "-c".into(),
                    "/usr/bin/sleep 30 & printf '%s %s' \"$BASHPID\" \"$!\" > \"$1\"; wait".into(),
                    "fixture".into(), marker.to_string_lossy().into_owned(),
                ], Duration::from_secs(35), true)
                    })
                })
                .collect();
            let deadline = std::time::Instant::now() + Duration::from_secs(3);
            while !(0..4).all(|index| {
                std::fs::read_to_string(root.join(index.to_string()))
                    .is_ok_and(|raw| raw.split_whitespace().count() == 2)
            }) {
                assert!(
                    std::time::Instant::now() < deadline,
                    "fixture did not start"
                );
                std::thread::sleep(Duration::from_millis(1));
            }
            shutdown();
            for worker in workers {
                assert!(worker.join().unwrap().is_none_or(|output| !output.status));
            }
            assert!(COMMANDS.lock().unwrap().groups.is_empty());
            assert_eq!(
                count(),
                before,
                "shutdown must release pidfds and captured pipes"
            );
            for index in 0..4 {
                for pid in std::fs::read_to_string(root.join(index.to_string()))
                    .unwrap()
                    .split_whitespace()
                {
                    let stat = format!("/proc/{pid}/stat");
                    let deadline = std::time::Instant::now() + Duration::from_secs(1);
                    while std::fs::read_to_string(&stat).is_ok_and(|raw| {
                        raw.rsplit_once(')')
                            .is_some_and(|(_, tail)| !tail.trim_start().starts_with('Z'))
                    }) {
                        assert!(
                            std::time::Instant::now() < deadline,
                            "command descendant {pid} survived shutdown"
                        );
                        std::thread::sleep(Duration::from_millis(1));
                    }
                }
            }
            assert!(run_command(&["/usr/bin/true".into()], Duration::from_secs(1), true).is_none());
            return;
        }
        let root =
            std::env::temp_dir().join(format!("dusky-center-shutdown-{}", std::process::id()));
        std::fs::create_dir(&root).unwrap();
        let result = Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "backend::cmd::tests::shutdown_stops_active_groups_and_rejects_new_commands",
            ])
            .env(FIXTURE, &root)
            .output()
            .unwrap();
        std::fs::remove_dir_all(root).unwrap();
        assert!(
            result.status.success(),
            "{}{}",
            String::from_utf8_lossy(&result.stdout),
            String::from_utf8_lossy(&result.stderr)
        );
    }

    #[test]
    fn shutdown_serializes_with_concurrent_spawns() {
        const CHILD: &str = "DUSKY_CENTER_SHUTDOWN_RACE";
        if std::env::var_os(CHILD).is_none() {
            let result = Command::new(std::env::current_exe().unwrap())
                .args([
                    "--exact",
                    "backend::cmd::tests::shutdown_serializes_with_concurrent_spawns",
                ])
                .env(CHILD, "1")
                .output()
                .unwrap();
            assert!(
                result.status.success(),
                "{}{}",
                String::from_utf8_lossy(&result.stdout),
                String::from_utf8_lossy(&result.stderr)
            );
            return;
        }
        let barrier = std::sync::Arc::new(std::sync::Barrier::new(33));
        let workers: Vec<_> = (0..32)
            .map(|_| {
                let barrier = barrier.clone();
                std::thread::spawn(move || {
                    barrier.wait();
                    run_command(
                        &["/usr/bin/sleep".into(), "0.5".into()],
                        Duration::from_secs(2),
                        false,
                    )
                })
            })
            .collect();
        barrier.wait();
        std::thread::sleep(Duration::from_millis(2));
        shutdown();
        for worker in workers {
            assert!(worker.join().unwrap().is_none_or(|output| !output.status));
        }
        assert!(COMMANDS.lock().unwrap().groups.is_empty());
    }

    #[test]
    fn interrupted_poll_preserves_results_and_deadlines() {
        const CHILD: &str = "DUSKY_CENTER_INTERRUPTED_POLL";
        if std::env::var_os(CHILD).is_none() {
            let result = Command::new(std::env::current_exe().unwrap())
                .args([
                    "--exact",
                    "backend::cmd::tests::interrupted_poll_preserves_results_and_deadlines",
                ])
                .env(CHILD, "1")
                .output()
                .unwrap();
            assert!(
                result.status.success(),
                "{}{}",
                String::from_utf8_lossy(&result.stdout),
                String::from_utf8_lossy(&result.stderr)
            );
            return;
        }
        extern "C" fn signal_handler(_: libc::c_int) {}
        let mut action: libc::sigaction = unsafe { std::mem::zeroed() };
        action.sa_sigaction = signal_handler as *const () as usize;
        assert_eq!(
            unsafe { libc::sigaction(libc::SIGUSR1, &action, std::ptr::null_mut()) },
            0
        );
        let thread = unsafe { libc::pthread_self() };
        for capture in [false, true] {
            for timeout in [false, true] {
                let signals = std::thread::spawn(move || {
                    for _ in 0..40 {
                        assert_eq!(unsafe { libc::pthread_kill(thread, libc::SIGUSR1) }, 0);
                        std::thread::sleep(Duration::from_millis(2));
                    }
                });
                let start = std::time::Instant::now();
                let output = run_command(
                    &[
                        "/usr/bin/bash".into(),
                        "-c".into(),
                        if timeout {
                            "sleep 2"
                        } else {
                            "sleep .1; printf done; exit 7"
                        }
                        .into(),
                    ],
                    Duration::from_millis(if timeout { 100 } else { 1000 }),
                    capture,
                );
                signals.join().unwrap();
                assert!(start.elapsed() < Duration::from_secs(1));
                if timeout {
                    assert!(output.is_none());
                } else {
                    let output = output.unwrap();
                    assert_eq!(output.code, 7);
                    if capture {
                        assert_eq!(output.stdout, "done");
                    }
                }
            }
        }
    }

    #[test]
    fn leader_stays_unreaped_while_descendant_pipes_are_pending() {
        let root = std::env::temp_dir().join(format!("dusky-center-leader-{}", std::process::id()));
        std::fs::create_dir(&root).unwrap();
        let marker = root.join("pid");
        let gate = root.join("release");
        let worker_marker = marker.clone();
        let worker_gate = gate.clone();
        let worker = std::thread::spawn(move || {
            run_command(&[
            "/usr/bin/bash".into(), "-c".into(),
            "(while [[ ! -f $2 ]]; do /usr/bin/sleep .002; done; printf tail) & printf '%s' \"$BASHPID\" > \"$1\"; exit 7".into(),
            "fixture".into(), worker_marker.to_string_lossy().into_owned(), worker_gate.to_string_lossy().into_owned(),
        ], Duration::from_secs(2), true)
        });
        let deadline = std::time::Instant::now() + Duration::from_secs(1);
        while !std::fs::read_to_string(&marker).is_ok_and(|raw| !raw.is_empty()) {
            if std::time::Instant::now() >= deadline {
                break;
            }
            std::thread::sleep(Duration::from_millis(1));
        }
        std::thread::sleep(Duration::from_millis(25));
        let state = std::fs::read_to_string(&marker)
            .ok()
            .and_then(|pid| std::fs::read_to_string(format!("/proc/{pid}/stat")).ok())
            .map(|raw| {
                raw.rsplit_once(')')
                    .unwrap()
                    .1
                    .trim_start()
                    .chars()
                    .next()
                    .unwrap()
            });
        // Release and join before asserting, so a failing fixture leaves no worker.
        std::fs::write(gate, "go").unwrap();
        let output = worker.join().unwrap().unwrap();
        std::fs::remove_dir_all(root).unwrap();
        assert_eq!(
            state,
            Some('Z'),
            "retain the leader's PID until inherited pipes finish"
        );
        assert_eq!(output.code, 7);
        assert_eq!(output.stdout, "tail");
    }

    #[test]
    fn pidfd_failure_reaps_child_and_allows_the_next_command() {
        const CHILD: &str = "DUSKY_CENTER_PIDFD_FAILURE_CHILD";
        if std::env::var_os(CHILD).is_none() {
            let result = Command::new(std::env::current_exe().unwrap())
                .args([
                    "--exact",
                    "backend::cmd::tests::pidfd_failure_reaps_child_and_allows_the_next_command",
                ])
                .env(CHILD, "1")
                .output()
                .unwrap();
            assert!(
                result.status.success(),
                "{}{}",
                String::from_utf8_lossy(&result.stdout),
                String::from_utf8_lossy(&result.stderr)
            );
            return;
        }
        let count = || std::fs::read_dir("/proc/self/fd").unwrap().count();
        let before = count();
        let first = run_command(
            &["/usr/bin/sleep".into(), "0.01".into()],
            Duration::from_secs(2),
            false,
        );
        if std::env::var_os("DUSKY_CENTER_INJECT_PIDFD_FAILURE").is_some() {
            assert!(
                first.is_none(),
                "the first pidfd_open must be fault-injected"
            );
        } else {
            assert!(first.unwrap().status);
        }
        assert!(COMMANDS.lock().unwrap().groups.is_empty());
        assert!(
            run_command(&["/usr/bin/true".into()], Duration::from_secs(2), true)
                .unwrap()
                .status
        );
        assert_eq!(count(), before);
    }

    #[test]
    fn unrepresentable_deadline_does_not_spawn_a_command() {
        let marker =
            std::env::temp_dir().join(format!("dusky-center-overflow-{}", std::process::id()));
        let output = run_command(
            &[
                "/usr/bin/bash".into(),
                "-c".into(),
                "printf started > \"$1\"".into(),
                "fixture".into(),
                marker.to_string_lossy().into_owned(),
            ],
            Duration::MAX,
            true,
        );
        assert!(output.is_none());
        assert!(!marker.exists());
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
    fn exited_leader_still_drains_descendant_pipes() {
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
        const CHILD: &str = "DUSKY_CENTER_TEST_FDS";
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
        for i in 0..300 {
            let (argv, timeout, capture, expected) = match i % 6 {
                0 => (vec!["/usr/bin/true".into()], 1000, false, Some(0)),
                1 => (vec!["/usr/bin/true".into()], 1000, true, Some(0)),
                2 => (
                    vec!["/usr/bin/bash".into(), "-c".into(), "exit 7".into()],
                    1000,
                    true,
                    Some(7),
                ),
                3 => (vec!["/missing-dusky-command".into()], 1000, true, None),
                4 => (
                    vec![
                        "/usr/bin/bash".into(),
                        "-c".into(),
                        "exec 1>&- 2>&-; sleep .002".into(),
                    ],
                    1000,
                    true,
                    Some(0),
                ),
                _ => (vec!["/usr/bin/sleep".into(), "0.1".into()], 1, true, None),
            };
            let output = run_command(&argv, Duration::from_millis(timeout), capture);
            assert_eq!(output.as_ref().map(|o| o.code), expected);
        }
        assert_eq!(count(), before);
    }
}
