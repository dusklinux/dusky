//! Poll child exit and all three pipes together, with one deadline and no helper threads.
use crate::Result;
use std::{
    io::{self, Read, Write},
    os::{
        fd::{AsRawFd, FromRawFd, OwnedFd},
        unix::process::CommandExt,
    },
    process::{Child, Command, Stdio},
    time::{Duration, Instant},
};

struct Running(Child, bool);
impl Drop for Running {
    fn drop(&mut self) {
        if !self.1 {
            // Keep the leader unreaped until all pipes close: its process-group
            // ID then remains ours even when a descendant holds a pipe open.
            unsafe {
                libc::killpg(self.0.id() as i32, libc::SIGKILL);
            }
            let _ = self.0.wait();
        }
    }
}

fn nonblocking(fd: i32) -> io::Result<()> {
    let flags = unsafe { libc::fcntl(fd, libc::F_GETFL) };
    if flags < 0 || unsafe { libc::fcntl(fd, libc::F_SETFL, flags | libc::O_NONBLOCK) } < 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

fn drain(pipe: &mut impl Read, bytes: &mut Vec<u8>) -> io::Result<bool> {
    let mut buffer = [0; 16384];
    // Bound each pass to preserve deadline and input progress under continuous output.
    for _ in 0..16 {
        match pipe.read(&mut buffer) {
            Ok(0) => return Ok(true),
            Ok(n) => bytes.extend_from_slice(&buffer[..n]),
            Err(e) if e.kind() == io::ErrorKind::WouldBlock => break,
            Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
            Err(e) => return Err(e),
        }
    }
    Ok(false)
}

pub fn command(
    name: &str,
    args: &[&str],
    input: Option<Vec<u8>>,
    timeout: Duration,
) -> Result<Vec<u8>> {
    let deadline = Instant::now()
        .checked_add(timeout)
        .ok_or("Command timeout is too large")?;
    let mut cmd = Command::new(name);
    crate::gpu::inherit_session_environment(&mut cmd);
    let mut child = Running(
        cmd.args(args)
            .process_group(0)
            .stdin(if input.is_some() {
                Stdio::piped()
            } else {
                Stdio::null()
            })
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()?,
        false,
    );
    let fd = unsafe { libc::syscall(libc::SYS_pidfd_open, child.0.id() as libc::pid_t, 0u32) };
    if fd < 0 {
        return Err(io::Error::last_os_error().into());
    }
    let process = unsafe { OwnedFd::from_raw_fd(fd as i32) };
    let mut stdout = child.0.stdout.take().ok_or("Missing stdout")?;
    let mut stderr = child.0.stderr.take().ok_or("Missing stderr")?;
    let mut stdin = child.0.stdin.take();
    for fd in [
        Some(stdout.as_raw_fd()),
        Some(stderr.as_raw_fd()),
        stdin.as_ref().map(AsRawFd::as_raw_fd),
    ]
    .into_iter()
    .flatten()
    {
        nonblocking(fd)?;
    }
    let input = input.unwrap_or_default();
    let mut written = 0;
    let mut write_error: Option<io::Error> = None;
    let (mut out, mut err) = (Vec::new(), Vec::new());
    let (mut exited, mut out_done, mut err_done) = (false, false, false);
    loop {
        if Instant::now() >= deadline {
            return Err(format!("{name} timed out").into());
        }
        if written == input.len() {
            stdin = None;
        }
        if !out_done {
            out_done = drain(&mut stdout, &mut out)?;
        }
        if !err_done {
            err_done = drain(&mut stderr, &mut err)?;
        }
        if exited && out_done && err_done && stdin.is_none() {
            let status = child.0.wait()?;
            child.1 = true;
            if !status.success() {
                return Err(format!("{name}: {}", String::from_utf8_lossy(&err).trim()).into());
            }
            if let Some(e) = write_error {
                return Err(e.into());
            }
            if written != input.len() {
                return Err("Command closed before consuming input".into());
            }
            return Ok(out);
        }
        let mut fds = [
            libc::pollfd {
                fd: if exited { -1 } else { process.as_raw_fd() },
                events: libc::POLLIN,
                revents: 0,
            },
            libc::pollfd {
                fd: if out_done { -1 } else { stdout.as_raw_fd() },
                events: libc::POLLIN,
                revents: 0,
            },
            libc::pollfd {
                fd: if err_done { -1 } else { stderr.as_raw_fd() },
                events: libc::POLLIN,
                revents: 0,
            },
            libc::pollfd {
                fd: stdin.as_ref().map_or(-1, AsRawFd::as_raw_fd),
                events: libc::POLLOUT,
                revents: 0,
            },
        ];
        let wait_ms = deadline
            .saturating_duration_since(Instant::now())
            .as_nanos()
            .div_ceil(1_000_000)
            .min(i32::MAX as u128) as i32;
        if unsafe { libc::poll(fds.as_mut_ptr(), fds.len() as _, wait_ms) } < 0 {
            let e = io::Error::last_os_error();
            if e.kind() == io::ErrorKind::Interrupted {
                continue;
            }
            return Err(e.into());
        }
        if fds.iter().any(|fd| fd.revents & libc::POLLNVAL != 0) {
            return Err("Command pipe became invalid".into());
        }
        exited |= fds[0].revents & (libc::POLLIN | libc::POLLHUP) != 0;
        if fds[3].revents != 0
            && let Some(pipe) = &mut stdin
        {
            match pipe.write(&input[written..]) {
                Ok(0) => {
                    write_error = Some(io::Error::new(
                        io::ErrorKind::WriteZero,
                        "Command input closed",
                    ));
                    stdin = None;
                }
                Ok(n) => written += n,
                Err(e)
                    if matches!(
                        e.kind(),
                        io::ErrorKind::WouldBlock | io::ErrorKind::Interrupted
                    ) => {}
                Err(e) => {
                    write_error = Some(e);
                    stdin = None;
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exchanges_large_input_while_draining_both_output_pipes() {
        let input = vec![b'x'; 2 * 1024 * 1024];
        let out = command(
            "sh",
            &["-c", "head -c 1048576 /dev/zero >&2; cat"],
            Some(input.clone()),
            Duration::from_secs(3),
        )
        .unwrap();
        assert_eq!(out, input);
    }

    #[test]
    fn inherited_pipes_remain_bounded_after_leader_exits() {
        let start = Instant::now();
        assert!(
            command(
                "sh",
                &["-c", "sleep 30 & exit 0"],
                None,
                Duration::from_millis(50)
            )
            .unwrap_err()
            .to_string()
            .contains("timed out")
        );
        assert!(start.elapsed() < Duration::from_secs(2));
    }

    #[test]
    fn closed_pipes_do_not_finish_a_running_process() {
        let start = Instant::now();
        assert!(
            command(
                "sh",
                &["-c", "exec >/dev/null 2>&1; sleep 30"],
                None,
                Duration::from_millis(50)
            )
            .is_err()
        );
        assert!(start.elapsed() < Duration::from_secs(2));
    }

    #[test]
    fn inherited_input_remains_bounded_after_output_and_leader_exit() {
        let start = Instant::now();
        let error = command(
            "sh",
            &["-c", "exec 3<&0; (exec >/dev/null 2>&1; sleep 30) & exit 0"],
            Some(vec![1; 1024 * 1024]),
            Duration::from_millis(50),
        )
        .unwrap_err();
        assert!(error.to_string().contains("timed out"));
        assert!(start.elapsed() < Duration::from_secs(2));
    }

    #[test]
    fn failures_and_empty_input_are_reported() {
        assert!(
            command(
                "/nonexistent/dusky-snap-command",
                &[],
                None,
                Duration::from_secs(1)
            )
            .is_err()
        );
        assert!(
            command(
                "sh",
                &["-c", "echo fixture >&2; exit 7"],
                None,
                Duration::from_secs(1)
            )
            .unwrap_err()
            .to_string()
            .contains("fixture")
        );
        assert!(
            command("cat", &[], Some(Vec::new()), Duration::from_secs(1))
                .unwrap()
                .is_empty()
        );
        assert!(command("true", &[], Some(vec![1; 1048576]), Duration::from_secs(1)).is_err());
    }
}
