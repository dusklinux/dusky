use crate::{Snapshot, atomic_write};
use std::{
    fs, io,
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Counters {
    pub iface: String,
    pub carrier: Option<u64>,
    pub rx: u64,
    pub tx: u64,
}

// One procfs read returns both counters; separate sysfs reads each dump the
// entire kernel statistics structure. No ip/nmcli subprocess is needed.
pub fn parse_counters(text: &str, iface: &str) -> Option<(u64, u64)> {
    for line in text.lines().skip(2) {
        let Some((name, fields)) = line.rsplit_once(':') else {
            continue;
        };
        if name.trim() == iface {
            let mut fields = fields.split_whitespace();
            let rx = fields.next()?.parse().ok()?;
            let tx = fields.nth(7)?.parse().ok()?;
            return Some((rx, tx));
        }
    }
    None
}

fn default_routes(text: &str) -> impl Iterator<Item = &str> {
    text.lines().skip(1).filter_map(|line| {
        let mut fields = line.split_whitespace();
        let iface = fields.next()?;
        (fields.next()? == "00000000").then_some(iface)
    })
}

pub struct LinuxSource {
    proc: PathBuf,
    sys: PathBuf,
    iface: Option<String>,
    discovery: Option<Instant>,
}

impl LinuxSource {
    pub fn new(proc: PathBuf, sys: PathBuf) -> Self {
        Self {
            proc,
            sys,
            iface: None,
            discovery: None,
        }
    }

    fn usable(&self, iface: &str) -> bool {
        let path = self.sys.join("class/net").join(iface);
        path.join("statistics/rx_bytes").is_file()
            && fs::read_to_string(path.join("operstate"))
                .is_ok_and(|state| matches!(state.trim(), "up" | "unknown"))
    }

    fn discover(&self) -> Option<String> {
        if let Ok(routes) = fs::read_to_string(self.proc.join("net/route")) {
            for iface in default_routes(&routes) {
                if self.usable(iface) {
                    return Some(iface.to_owned());
                }
            }
        }
        let mut devices: Vec<_> = fs::read_dir(self.sys.join("class/net"))
            .ok()?
            .filter_map(Result::ok)
            .collect();
        devices.sort_by_key(|entry| entry.file_name());
        // Preserve the shell's physical-interface fallback, including virtio.
        for state in ["up", "unknown"] {
            for entry in &devices {
                let path = entry.path();
                if entry.file_name() == "lo"
                    || !path.join("device").exists()
                    || !path.join("statistics/rx_bytes").is_file()
                {
                    continue;
                }
                if fs::read_to_string(path.join("operstate")).is_ok_and(|s| s.trim() == state) {
                    return entry.file_name().into_string().ok();
                }
            }
        }
        None
    }

    pub fn sample(&mut self, now: Instant) -> Option<Counters> {
        if self.iface.is_none()
            || self
                .discovery
                .is_none_or(|last| now.duration_since(last) >= Duration::from_secs(5))
            || self.iface.as_ref().is_some_and(|i| !self.usable(i))
        {
            self.iface = self.discover();
            self.discovery = Some(now);
        }
        let iface = self.iface.as_ref()?;
        let text = fs::read_to_string(self.proc.join("net/dev")).ok()?;
        let Some((rx, tx)) = parse_counters(&text, iface) else {
            self.iface = None;
            return None;
        };
        let carrier = fs::read_to_string(
            self.sys
                .join("class/net")
                .join(iface)
                .join("carrier_up_count"),
        )
        .ok()
        .and_then(|value| value.trim().parse().ok());
        Some(Counters {
            iface: iface.clone(),
            carrier,
            rx,
            tx,
        })
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct Session {
    iface: String,
    carrier: Option<u64>,
    rx: u64,
    tx: u64,
}

impl Session {
    fn parse(text: &str) -> Option<Self> {
        let mut fields = text.split_whitespace();
        let iface = fields.next()?.to_owned();
        let carrier = match fields.next()? {
            "-" => None,
            s => Some(s.parse().ok()?),
        };
        let rx = fields.next()?.parse().ok()?;
        let tx = fields.next()?.parse().ok()?;
        if fields.next().is_some() {
            return None;
        }
        Some(Self {
            iface,
            carrier,
            rx,
            tx,
        })
    }

    fn matches(&self, counters: &Counters) -> bool {
        self.iface == counters.iface
            && self.carrier == counters.carrier
            && self.rx <= counters.rx
            && self.tx <= counters.tx
    }
}

pub struct Meter {
    previous: Option<(Counters, Instant)>,
    session: Option<Session>,
    session_file: PathBuf,
}

impl Meter {
    pub fn new(runtime: &Path) -> Self {
        let session_file = runtime.join("conn_session");
        let session = fs::read_to_string(&session_file)
            .ok()
            .and_then(|text| Session::parse(&text));
        Self {
            previous: None,
            session,
            session_file,
        }
    }

    pub fn update(&mut self, counters: Option<Counters>, now: Instant) -> io::Result<Snapshot> {
        let Some(counters) = counters else {
            self.previous = None;
            if self.session.take().is_some() {
                match fs::remove_file(&self.session_file) {
                    Ok(()) => {}
                    Err(e) if e.kind() == io::ErrorKind::NotFound => {}
                    Err(e) => return Err(e),
                }
            }
            return Ok(Snapshot::default());
        };
        let counter_reset = self.previous.as_ref().is_some_and(|(previous, _)| {
            previous.iface == counters.iface
                && (counters.rx < previous.rx || counters.tx < previous.tx)
        });
        let new_session =
            counter_reset || self.session.as_ref().is_none_or(|s| !s.matches(&counters));
        if new_session {
            let session = Session {
                iface: counters.iface.clone(),
                carrier: counters.carrier,
                rx: counters.rx,
                tx: counters.tx,
            };
            atomic_write(
                &self.session_file,
                &format!(
                    "{} {} {} {}\n",
                    session.iface,
                    session
                        .carrier
                        .map_or_else(|| "-".into(), |n| n.to_string()),
                    session.rx,
                    session.tx
                ),
            )?;
            self.session = Some(session);
        }
        let session = self.session.as_ref().unwrap();
        let mut snapshot = Snapshot {
            iface: counters.iface.clone(),
            session_rx: counters.rx - session.rx,
            session_tx: counters.tx - session.tx,
            boot_rx: counters.rx,
            boot_tx: counters.tx,
            ..Snapshot::default()
        };
        if !new_session && let Some((previous, then)) = &self.previous {
            let dt = now.duration_since(*then);
            if previous.iface == counters.iface
                && previous.carrier == counters.carrier
                && counters.rx >= previous.rx
                && counters.tx >= previous.tx
                && (Duration::from_millis(400)..=Duration::from_millis(2500)).contains(&dt)
            {
                snapshot.rx_rate = rate(counters.rx - previous.rx, dt);
                snapshot.tx_rate = rate(counters.tx - previous.tx, dt);
            }
        }
        self.previous = Some((counters, now));
        Ok(snapshot)
    }
}

fn rate(delta: u64, dt: Duration) -> u64 {
    let micros = dt.as_micros();
    ((delta as u128 * 1_000_000 + micros / 2) / micros).min(u64::MAX as u128) as u64
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_rx_tx_and_routes() {
        let text = "header\nheader\n lo: 1 2 3 4 5 6 7 8 9 10\n eth0: 123 0 0 0 0 0 0 0 456 0\n";
        assert_eq!(parse_counters(text, "eth0"), Some((123, 456)));
        assert_eq!(parse_counters(text, "missing"), None);
        assert_eq!(parse_counters("h\nh\neth0: broken\n", "eth0"), None);
        assert_eq!(
            default_routes("Iface Destination\neth1 00110000\neth0 00000000\n").collect::<Vec<_>>(),
            vec!["eth0"]
        );
    }

    #[test]
    fn rates_use_elapsed_time_without_overflow() {
        assert_eq!(rate(3000, Duration::from_millis(1500)), 2000);
        assert_eq!(rate(u64::MAX, Duration::from_secs(1)), u64::MAX);
        assert_eq!(rate(u64::MAX, Duration::from_millis(400)), u64::MAX);
    }

    #[test]
    fn session_restoration_rejects_reconnect_and_counter_reset() {
        let s = Session::parse("eth0 3 100 200\n").unwrap();
        let c = Counters {
            iface: "eth0".into(),
            carrier: Some(3),
            rx: 150,
            tx: 250,
        };
        assert!(s.matches(&c));
        assert!(!s.matches(&Counters {
            carrier: Some(4),
            ..c.clone()
        }));
        assert!(!s.matches(&Counters { rx: 0, ..c.clone() }));
        assert!(!s.matches(&Counters {
            iface: "eth1".into(),
            ..c
        }));
        assert!(Session::parse("eth0 bogus 100 200").is_none());
    }

    #[test]
    fn discovery_fallback_hotplug_and_interface_switch() {
        let root = crate::tests::fixture();
        let proc = root.join("proc");
        let sys = root.join("sys");
        fs::create_dir_all(proc.join("net")).unwrap();
        fs::create_dir_all(sys.join("class/net")).unwrap();
        for (iface, state, physical) in [
            ("lo", "up", false),
            ("bridge0", "up", false),
            ("eth0", "unknown", true),
            ("wlan0", "up", true),
        ] {
            let path = sys.join("class/net").join(iface);
            fs::create_dir_all(path.join("statistics")).unwrap();
            fs::write(path.join("statistics/rx_bytes"), "0\n").unwrap();
            fs::write(path.join("operstate"), state).unwrap();
            if physical {
                fs::create_dir(path.join("device")).unwrap();
            }
        }
        let mut source = LinuxSource::new(proc.clone(), sys.clone());
        assert_eq!(source.discover(), Some("wlan0".into()));
        fs::write(sys.join("class/net/wlan0/operstate"), "down").unwrap();
        assert_eq!(source.discover(), Some("eth0".into()));
        fs::write(
            proc.join("net/route"),
            "Iface Destination\nbridge0 00000000\n",
        )
        .unwrap();
        assert_eq!(source.discover(), Some("bridge0".into()));
        fs::write(
            proc.join("net/route"),
            "Iface Destination\nmissing0 00000000\nbridge0 00000000\n",
        )
        .unwrap();
        assert_eq!(source.discover(), Some("bridge0".into()));
        fs::write(
            proc.join("net/dev"),
            "h\nh\ninvalid line\nbridge0: 123 0 0 0 0 0 0 0 456 0\neth0: 1 0 0 0 0 0 0 0 2 0\n",
        )
        .unwrap();
        let now = Instant::now();
        assert_eq!(source.sample(now).unwrap().rx, 123);
        // A stale route must not keep a down device selected; a later default
        // and physical fallback remain eligible, including unknown-state virtio.
        fs::write(sys.join("class/net/bridge0/operstate"), "lowerlayerdown").unwrap();
        assert_eq!(
            source
                .sample(now + Duration::from_millis(500))
                .unwrap()
                .iface,
            "eth0"
        );
        fs::remove_dir_all(sys.join("class/net/bridge0")).unwrap();
        assert_eq!(
            source.sample(now + Duration::from_secs(1)).unwrap().iface,
            "eth0"
        );
        fs::remove_dir_all(sys.join("class/net/eth0")).unwrap();
        assert!(source.sample(now + Duration::from_secs(2)).is_none());
        fs::write(sys.join("class/net/wlan0/operstate"), "up").unwrap();
        fs::write(
            proc.join("net/dev"),
            "h\nh\nwlan0: 100 0 0 0 0 0 0 0 200 0\n",
        )
        .unwrap();
        assert_eq!(
            source.sample(now + Duration::from_secs(3)).unwrap().iface,
            "wlan0"
        );
        fs::remove_dir_all(root).unwrap();
    }
}
