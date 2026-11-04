mod format;
mod network;

use format::Mode;
use network::{LinuxSource, Meter};
use std::{
    env,
    fs::{self, File, OpenOptions},
    io::{self, Write},
    path::{Path, PathBuf},
    process::ExitCode,
    thread,
    time::{Duration, Instant, SystemTime},
};

const HELP: &str = "dusky-network — Linux network meter for Waybar
Usage: dusky-network [stream|daemon|watch|read|osd|once] [MODE] [OPTIONS]
  stream    Emit JSON every second; sleep 3s disconnected (default; no service)
  daemon    Sample and publish a snapshot every second; sleep 3s disconnected
  watch     Emit the daemon's snapshot as JSON every second, without relaunching
  read      Emit the daemon's latest snapshot once (no automatic service start)
  osd       Emit a plain-text reading for Dusky Glance from the shared snapshot
  once      Take two samples a second apart and emit one JSON result
MODE: --horizontal (default), --vertical, unit, up, down, combined,
      session-down, session-up, session-total, boot-down, boot-up, boot-total
      Upload/download and session/boot aliases are accepted.
OPTIONS:
  --runtime-dir PATH  Override $XDG_RUNTIME_DIR/waybar-net-rust
  --proc-root PATH    Override /proc (for isolated fixtures)
  --sys-root PATH     Override /sys (for isolated fixtures)
  --help, --version
Only one sampler (stream/daemon/once) may use a runtime directory.
Use daemon + watch for multiple bars or modules. Missing/stale snapshots
produce Disconnected. No subprocesses are launched by this binary.";

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Snapshot {
    iface: String,
    rx_rate: u64,
    tx_rate: u64,
    session_rx: u64,
    session_tx: u64,
    boot_rx: u64,
    boot_tx: u64,
}

impl Snapshot {
    fn encode(&self) -> String {
        format!(
            "v1 {} {} {} {} {} {} {}\n",
            if self.iface.is_empty() {
                "-"
            } else {
                &self.iface
            },
            self.rx_rate,
            self.tx_rate,
            self.session_rx,
            self.session_tx,
            self.boot_rx,
            self.boot_tx
        )
    }

    fn parse(text: &str) -> Option<Self> {
        let mut fields = text.split_whitespace();
        if fields.next()? != "v1" {
            return None;
        }
        let iface = fields.next()?;
        let snapshot = Self {
            iface: if iface == "-" {
                String::new()
            } else {
                iface.into()
            },
            rx_rate: fields.next()?.parse().ok()?,
            tx_rate: fields.next()?.parse().ok()?,
            session_rx: fields.next()?.parse().ok()?,
            session_tx: fields.next()?.parse().ok()?,
            boot_rx: fields.next()?.parse().ok()?,
            boot_tx: fields.next()?.parse().ok()?,
        };
        if fields.next().is_some() {
            return None;
        }
        Some(snapshot)
    }
}

pub fn atomic_write(path: &Path, text: &str) -> io::Result<()> {
    let mut name = path.as_os_str().to_os_string();
    name.push(format!(".{}.tmp", std::process::id()));
    let temporary = PathBuf::from(name);
    let result = fs::write(&temporary, text).and_then(|()| fs::rename(&temporary, path));
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

#[derive(Debug)]
struct Options {
    command: String,
    mode: Mode,
    runtime: Option<PathBuf>,
    proc: PathBuf,
    sys: PathBuf,
}

fn options(args: impl Iterator<Item = String>) -> io::Result<Options> {
    let mut result = Options {
        command: "stream".into(),
        mode: Mode::Horizontal,
        runtime: None,
        proc: "/proc".into(),
        sys: "/sys".into(),
    };
    let mut args = args.peekable();
    let mut command_set = false;
    let mut mode_set = false;
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "stream" | "daemon" | "watch" | "read" | "osd" | "once" if !command_set => {
                result.command = arg;
                command_set = true;
            }
            "--runtime-dir" | "--proc-root" | "--sys-root" => {
                let value = args
                    .next()
                    .filter(|s| !s.is_empty() && !s.starts_with("--"))
                    .ok_or_else(|| invalid(format!("{arg} requires a path")))?;
                match arg.as_str() {
                    "--runtime-dir" => result.runtime = Some(value.into()),
                    "--proc-root" => result.proc = value.into(),
                    _ => result.sys = value.into(),
                }
            }
            _ => {
                let mode = Mode::parse(&arg)
                    .filter(|_| !mode_set)
                    .ok_or_else(|| invalid(format!("unknown or duplicate argument: {arg}")))?;
                result.mode = mode;
                mode_set = true;
            }
        }
    }
    Ok(result)
}

fn invalid(message: impl Into<String>) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, message.into())
}

fn runtime_dir(explicit: Option<PathBuf>) -> io::Result<PathBuf> {
    if let Some(path) = explicit {
        return Ok(path);
    }
    if let Some(path) = env::var_os("XDG_RUNTIME_DIR").filter(|v| !v.is_empty()) {
        return Ok(PathBuf::from(path).join("waybar-net-rust"));
    }
    // procfs reports the effective UID; HOME's ownership can differ.
    let status = fs::read_to_string("/proc/self/status")?;
    let uid = status
        .lines()
        .find_map(|line| line.strip_prefix("Uid:"))
        .and_then(|line| line.split_whitespace().nth(1))
        .ok_or_else(|| invalid("cannot determine effective UID"))?;
    Ok(PathBuf::from(format!("/run/user/{uid}/waybar-net-rust")))
}

fn sampler_lock(runtime: &Path) -> io::Result<File> {
    fs::create_dir_all(runtime)?;
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(runtime.join("sampler.lock"))?;
    file.try_lock().map_err(|e| {
        io::Error::other(format!(
            "sampler lock: {e}; use daemon + watch for multiple modules"
        ))
    })?;
    Ok(file)
}

fn read_snapshot(path: &Path) -> Snapshot {
    // Inspect and read the same inode: an atomic rename cannot mix two samples.
    let result = (|| -> io::Result<Snapshot> {
        use std::io::Read;
        let mut file = File::open(path)?;
        let modified = file.metadata()?.modified()?;
        if SystemTime::now()
            .duration_since(modified)
            .map_or(true, |age| age > Duration::from_secs(3))
        {
            return Ok(Snapshot::default());
        }
        let mut text = String::new();
        file.read_to_string(&mut text)?;
        Ok(Snapshot::parse(&text).unwrap_or_default())
    })();
    result.unwrap_or_default()
}

fn emit(out: &mut impl Write, snapshot: &Snapshot, mode: Mode) -> io::Result<()> {
    writeln!(out, "{}", format::render(snapshot, mode))?;
    out.flush()
}

fn run(options: Options) -> io::Result<()> {
    let runtime = runtime_dir(options.runtime)?;
    let state = runtime.join("state");
    let stdout = io::stdout();
    let mut out = stdout.lock();
    if options.command == "osd" {
        return writeln!(
            out,
            "{}",
            format::render_osd(&read_snapshot(&state), options.mode)
        );
    }
    if options.command == "read" {
        return emit(&mut out, &read_snapshot(&state), options.mode);
    }
    if options.command == "watch" {
        loop {
            let started = Instant::now();
            emit(&mut out, &read_snapshot(&state), options.mode)?;
            thread::sleep(Duration::from_secs(1).saturating_sub(started.elapsed()));
        }
    }
    let _lock = sampler_lock(&runtime)?;
    let mut source = LinuxSource::new(options.proc, options.sys);
    let mut meter = Meter::new(&runtime);
    let mut first = true;
    loop {
        let started = Instant::now();
        let counters = source.sample(started);
        let snapshot = meter.update(counters, started)?;
        if options.command == "daemon" {
            atomic_write(&state, &snapshot.encode())?;
        } else if options.command != "once" || !first {
            emit(&mut out, &snapshot, options.mode)?;
        }
        if options.command == "once" && !first {
            return Ok(());
        }
        first = false;
        let interval = if snapshot.iface.is_empty() && options.command != "once" {
            Duration::from_secs(3)
        } else {
            Duration::from_secs(1)
        };
        thread::sleep(interval.saturating_sub(started.elapsed()));
    }
}

fn main() -> ExitCode {
    let args: Vec<_> = env::args().skip(1).collect();
    if args.iter().any(|arg| arg == "--help" || arg == "-h") {
        println!("{HELP}");
        return ExitCode::SUCCESS;
    }
    if args == ["--version"] {
        println!("dusky-network {}", env!("CARGO_PKG_VERSION"));
        return ExitCode::SUCCESS;
    }
    match options(args.into_iter()).and_then(run) {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) if e.kind() == io::ErrorKind::BrokenPipe => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("dusky-network: {e}");
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use network::Counters;
    use std::sync::atomic::{AtomicUsize, Ordering};

    static NEXT: AtomicUsize = AtomicUsize::new(0);
    pub(super) fn fixture() -> PathBuf {
        let path = env::temp_dir().join(format!(
            "dusky-network-test-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir_all(&path).unwrap();
        path
    }

    #[test]
    fn snapshot_roundtrip_and_invalid_input() {
        let s = Snapshot {
            iface: "eth0".into(),
            rx_rate: u64::MAX,
            ..Snapshot::default()
        };
        assert_eq!(Snapshot::parse(&s.encode()), Some(s));
        assert!(Snapshot::parse("v2 eth0 1 2 3 4 5 6").is_none());
        assert!(Snapshot::parse("v1 eth0 1 2 3 4 5 6 extra").is_none());
        assert!(Snapshot::parse("v1 eth0 -1 2 3 4 5 6").is_none());
    }

    #[test]
    fn reconnect_reset_restart_and_long_delay() {
        let dir = fixture();
        let mut meter = Meter::new(&dir);
        let now = Instant::now();
        let c = Counters {
            iface: "eth0".into(),
            carrier: Some(1),
            rx: 1000,
            tx: 2000,
        };
        assert_eq!(meter.update(Some(c.clone()), now).unwrap().rx_rate, 0);
        let next = Counters {
            rx: 4000,
            tx: 3500,
            ..c.clone()
        };
        let s = meter
            .update(Some(next.clone()), now + Duration::from_millis(1500))
            .unwrap();
        assert_eq!((s.rx_rate, s.tx_rate, s.session_rx), (2000, 1000, 3000));
        let mut restored = Meter::new(&dir);
        assert_eq!(
            restored.update(Some(next.clone()), now).unwrap().session_rx,
            3000
        );
        assert_eq!(
            restored
                .update(
                    Some(Counters {
                        rx: 8000,
                        ..next.clone()
                    }),
                    now + Duration::from_secs(20)
                )
                .unwrap()
                .rx_rate,
            0
        );
        let reconnected = Counters {
            carrier: Some(2),
            ..next.clone()
        };
        let s = meter
            .update(Some(reconnected.clone()), now + Duration::from_secs(2))
            .unwrap();
        assert_eq!((s.rx_rate, s.session_rx), (0, 0));
        let s = meter
            .update(
                Some(Counters {
                    rx: 1,
                    tx: 1,
                    ..reconnected
                }),
                now + Duration::from_secs(3),
            )
            .unwrap();
        assert_eq!((s.rx_rate, s.session_rx, s.session_tx), (0, 0, 0));
        assert_eq!(
            meter.update(None, now + Duration::from_secs(4)).unwrap(),
            Snapshot::default()
        );
        assert!(!dir.join("conn_session").exists());
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn reset_above_session_baseline_is_persisted() {
        let dir = fixture();
        let mut meter = Meter::new(&dir);
        let now = Instant::now();
        let c = Counters {
            iface: "eth0".into(),
            carrier: None,
            rx: 100,
            tx: 200,
        };
        meter.update(Some(c.clone()), now).unwrap();
        meter
            .update(
                Some(Counters {
                    rx: 1000,
                    tx: 2000,
                    ..c.clone()
                }),
                now + Duration::from_secs(1),
            )
            .unwrap();
        let dropped = Counters {
            rx: 500,
            tx: 1500,
            ..c
        };
        let result = meter
            .update(Some(dropped.clone()), now + Duration::from_secs(2))
            .unwrap();
        assert_eq!(
            (result.rx_rate, result.session_rx, result.session_tx),
            (0, 0, 0)
        );
        let mut restored = Meter::new(&dir);
        assert_eq!(restored.update(Some(dropped), now).unwrap().session_rx, 0);
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn lock_is_released_by_process_file_lifetime() {
        let dir = fixture();
        let first = sampler_lock(&dir).unwrap();
        assert!(sampler_lock(&dir).is_err());
        drop(first);
        assert!(sampler_lock(&dir).is_ok());
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn missing_malformed_and_stale_snapshots_disconnect() {
        let dir = fixture();
        let path = dir.join("state");
        assert_eq!(read_snapshot(&path), Snapshot::default());
        atomic_write(&path, "partial").unwrap();
        assert_eq!(read_snapshot(&path), Snapshot::default());
        let s = Snapshot {
            iface: "eth0".into(),
            ..Snapshot::default()
        };
        atomic_write(&path, &s.encode()).unwrap();
        assert_eq!(read_snapshot(&path), s);
        File::options()
            .write(true)
            .open(&path)
            .unwrap()
            .set_times(
                fs::FileTimes::new().set_modified(SystemTime::now() - Duration::from_secs(10)),
            )
            .unwrap();
        assert_eq!(read_snapshot(&path), Snapshot::default());
        File::options()
            .write(true)
            .open(&path)
            .unwrap()
            .set_times(
                fs::FileTimes::new().set_modified(SystemTime::now() + Duration::from_secs(3600)),
            )
            .unwrap();
        assert_eq!(read_snapshot(&path), Snapshot::default());
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn arguments_accept_aliases_and_reject_typos() {
        assert_eq!(
            options(["read", "upload"].into_iter().map(String::from))
                .unwrap()
                .mode,
            Mode::Up
        );
        assert!(options(["--runtime-dir"].into_iter().map(String::from)).is_err());
        assert!(
            options(
                ["--runtime-dir", "--vertical"]
                    .into_iter()
                    .map(String::from)
            )
            .is_err()
        );
        assert!(options(["daemon", "watch"].into_iter().map(String::from)).is_err());
        assert!(options(["--horzontal"].into_iter().map(String::from)).is_err());
    }

    #[test]
    fn atomic_publication_never_mixes_samples_and_retains_old_on_failure() {
        let dir = fixture();
        let path = dir.join("state");
        let a = Snapshot {
            iface: "eth0".into(),
            rx_rate: 123,
            ..Snapshot::default()
        };
        let b = Snapshot {
            iface: "wlan0".into(),
            tx_rate: u64::MAX,
            ..Snapshot::default()
        };
        atomic_write(&path, &a.encode()).unwrap();
        thread::scope(|scope| {
            scope.spawn(|| {
                for _ in 0..1000 {
                    atomic_write(&path, &b.encode()).unwrap();
                    atomic_write(&path, &a.encode()).unwrap();
                }
            });
            for _ in 0..2000 {
                let snapshot = read_snapshot(&path);
                assert!(snapshot == a || snapshot == b);
            }
        });
        let mut temporary = path.as_os_str().to_os_string();
        temporary.push(format!(".{}.tmp", std::process::id()));
        fs::create_dir(PathBuf::from(&temporary)).unwrap();
        assert!(atomic_write(&path, &b.encode()).is_err());
        assert_eq!(read_snapshot(&path), a);
        fs::remove_dir_all(dir).unwrap();
    }
}
