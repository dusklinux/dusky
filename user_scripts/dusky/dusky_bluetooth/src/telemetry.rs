//! Unprivileged pure-Rust Linux Bluetooth kernel telemetry:
//! - HCI adapter TX/RX byte counters via `AF_BLUETOOTH` / `BTPROTO_HCI` `HCIGETDEVINFO` ioctl
//! - Passive `bnep*` PAN interface byte counters from `/sys/class/net`
//! - Read-only Bluetooth RF-kill state from `/sys/class/rfkill`

use crate::model::{PanTraffic, RfkillState, TrafficSnapshot};
use std::fs;
use std::path::Path;
use std::time::Instant;

const BTPROTO_HCI: libc::c_int = 1;
const HCIGETDEVINFO: libc::Ioctl = 0x8004_48D3_u64 as libc::Ioctl;

/// Deltas exceeding 64 MiB on a `u32` counter decrement indicate a kernel counter
/// reset (e.g. adapter power cycle or driver reload) rather than a 4 GiB wrap.
pub const WRAP_THRESHOLD_BYTES: u32 = 64 * 1024 * 1024;

#[repr(C)]
#[derive(Debug, Default, Clone, Copy)]
pub struct HciDevStats {
    pub err_rx: u32,
    pub err_tx: u32,
    pub cmd_tx: u32,
    pub evt_rx: u32,
    pub acl_tx: u32,
    pub acl_rx: u32,
    pub sco_tx: u32,
    pub sco_rx: u32,
    pub byte_rx: u32,
    pub byte_tx: u32,
}

#[repr(C)]
#[derive(Debug, Default, Clone, Copy)]
pub struct HciDevInfo {
    pub dev_id: u16,
    pub name: [u8; 8],
    pub bdaddr: [u8; 6],
    pub flags: u32,
    pub dev_type: u8,
    pub features: [u8; 8],
    // Implicit 3-byte alignment padding before 4-byte aligned `pkt_type` (offsets 29..32).
    pub pkt_type: u32,
    pub link_policy: u32,
    pub link_mode: u32,
    pub acl_mtu: u16,
    pub acl_pkts: u16,
    pub sco_mtu: u16,
    pub sco_pkts: u16,
    pub stat: HciDevStats,
}

pub fn parse_hci_dev_id(adapter_path_or_id: &str) -> Option<u16> {
    let tail = adapter_path_or_id
        .rsplit('/')
        .next()
        .unwrap_or(adapter_path_or_id)
        .trim();
    let idx = tail.strip_prefix("hci")?;
    if idx.is_empty() || !idx.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    idx.parse::<u16>().ok()
}

pub struct HciSocket {
    fd: Option<libc::c_int>,
    unavailable: bool,
}

impl Default for HciSocket {
    fn default() -> Self {
        Self::new()
    }
}

impl HciSocket {
    pub fn new() -> Self {
        Self {
            fd: None,
            unavailable: false,
        }
    }

    fn ensure_fd(&mut self) -> Option<libc::c_int> {
        if let Some(fd) = self.fd {
            return Some(fd);
        }
        if self.unavailable {
            return None;
        }
        let fd = unsafe {
            libc::socket(
                libc::AF_BLUETOOTH,
                libc::SOCK_RAW | libc::SOCK_CLOEXEC,
                BTPROTO_HCI,
            )
        };
        if fd < 0 {
            self.unavailable = true;
            return None;
        }
        self.fd = Some(fd);
        Some(fd)
    }

    pub fn read_dev_bytes(&mut self, dev_id: u16) -> Option<(u32, u32)> {
        let fd = self.ensure_fd()?;
        let mut info = HciDevInfo {
            dev_id,
            ..Default::default()
        };
        let rc = unsafe { libc::ioctl(fd, HCIGETDEVINFO, &mut info as *mut HciDevInfo) };
        if rc < 0 {
            return None;
        }
        Some((info.stat.byte_tx, info.stat.byte_rx))
    }

    pub fn close(&mut self) {
        if let Some(fd) = self.fd.take() {
            unsafe {
                libc::close(fd);
            }
        }
    }
}

impl Drop for HciSocket {
    fn drop(&mut self) {
        self.close();
    }
}

fn advance_u32_counter(prev: u32, curr: u32) -> (u64, u64) {
    if curr >= prev {
        let delta = (curr - prev) as u64;
        (delta, delta)
    } else {
        let wrapped = curr.wrapping_sub(prev);
        if wrapped <= WRAP_THRESHOLD_BYTES {
            let delta = wrapped as u64;
            (delta, delta)
        } else if curr <= WRAP_THRESHOLD_BYTES {
            let delta = curr as u64;
            (delta, delta)
        } else {
            (0, 0)
        }
    }
}

#[derive(Debug, Default)]
pub struct TrafficTracker {
    adapter_short_id: String,
    prev_raw_tx: Option<u32>,
    prev_raw_rx: Option<u32>,
    total_tx: u64,
    total_rx: u64,
    last_sample: Option<Instant>,
    pan_prev: Option<(String, u64, u64, Instant)>,
}

impl TrafficTracker {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn reset(&mut self, adapter_short_id: impl Into<String>) {
        self.adapter_short_id = adapter_short_id.into();
        self.prev_raw_tx = None;
        self.prev_raw_rx = None;
        self.total_tx = 0;
        self.total_rx = 0;
        self.last_sample = None;
        self.pan_prev = None;
    }

    pub fn adapter_short_id(&self) -> &str {
        &self.adapter_short_id
    }

    pub fn update(
        &mut self,
        raw_tx: u32,
        raw_rx: u32,
        now: Instant,
        pan_sample: Option<(String, u64, u64)>,
    ) -> TrafficSnapshot {
        let (tx_rate_bps, rx_rate_bps) = match (self.prev_raw_tx, self.prev_raw_rx, self.last_sample)
        {
            (Some(prev_tx), Some(prev_rx), Some(prev_time)) => {
                let elapsed = now.saturating_duration_since(prev_time).as_secs_f64();
                let dt = elapsed.max(0.1);
                let (tot_dtx, rate_dtx) = advance_u32_counter(prev_tx, raw_tx);
                let (tot_drx, rate_drx) = advance_u32_counter(prev_rx, raw_rx);
                self.total_tx = self.total_tx.saturating_add(tot_dtx);
                self.total_rx = self.total_rx.saturating_add(tot_drx);
                (rate_dtx as f64 / dt, rate_drx as f64 / dt)
            }
            _ => {
                self.total_tx = u64::from(raw_tx);
                self.total_rx = u64::from(raw_rx);
                (0.0, 0.0)
            }
        };

        self.prev_raw_tx = Some(raw_tx);
        self.prev_raw_rx = Some(raw_rx);
        self.last_sample = Some(now);

        let pan = match pan_sample {
            Some((iface, ptx, prx)) => {
                let (pr_tx, pr_rx) = match &self.pan_prev {
                    Some((prev_if, prev_tx, prev_rx, prev_time)) if prev_if == &iface => {
                        let dt = now
                            .saturating_duration_since(*prev_time)
                            .as_secs_f64()
                            .max(0.1);
                        let dtx = ptx.saturating_sub(*prev_tx);
                        let drx = prx.saturating_sub(*prev_rx);
                        (dtx as f64 / dt, drx as f64 / dt)
                    }
                    _ => (0.0, 0.0),
                };
                self.pan_prev = Some((iface.clone(), ptx, prx, now));
                Some(PanTraffic {
                    iface,
                    total_tx: ptx,
                    total_rx: prx,
                    tx_rate_bps: pr_tx,
                    rx_rate_bps: pr_rx,
                })
            }
            None => {
                self.pan_prev = None;
                None
            }
        };

        TrafficSnapshot {
            adapter_short_id: self.adapter_short_id.clone(),
            total_tx: self.total_tx,
            total_rx: self.total_rx,
            tx_rate_bps,
            rx_rate_bps,
            active_tx: tx_rate_bps >= 1.0,
            active_rx: rx_rate_bps >= 1.0,
            available: true,
            pan,
        }
    }
}

/// Passive read-only lookup for an active Bluetooth PAN (`bnep*`) network interface.
pub fn read_active_bnep_bytes(net_root: &Path) -> Option<(String, u64, u64)> {
    let entries = fs::read_dir(net_root).ok()?;
    let mut names: Vec<String> = entries
        .filter_map(Result::ok)
        .filter_map(|e| e.file_name().into_string().ok())
        .filter(|name| name.starts_with("bnep"))
        .collect();
    names.sort();

    for name in names {
        let iface_dir = net_root.join(&name);
        if let Ok(state) = fs::read_to_string(iface_dir.join("operstate"))
            && state.trim().eq_ignore_ascii_case("down")
        {
            continue;
        }
        let Some(tx) = fs::read_to_string(iface_dir.join("statistics/tx_bytes"))
            .ok()
            .and_then(|s| s.trim().parse::<u64>().ok())
        else {
            continue;
        };
        let Some(rx) = fs::read_to_string(iface_dir.join("statistics/rx_bytes"))
            .ok()
            .and_then(|s| s.trim().parse::<u64>().ok())
        else {
            continue;
        };
        return Some((name, tx, rx));
    }
    None
}

fn rfkill_hci_owner(entry_dir: &Path) -> Option<String> {
    let dev_link = fs::canonicalize(entry_dir.join("device")).ok()?;
    for comp in dev_link.components() {
        if let Some(seg) = comp.as_os_str().to_str()
            && parse_hci_dev_id(seg).is_some()
        {
            return Some(seg.to_string());
        }
    }
    None
}

/// Single-pass unprivileged reader of `/sys/class/rfkill/rfkill*` for Bluetooth switches.
pub fn read_bluetooth_rfkill(rfkill_root: &Path, active_hci: Option<&str>) -> RfkillState {
    let Ok(entries) = fs::read_dir(rfkill_root) else {
        return RfkillState::default();
    };

    let mut state = RfkillState::default();
    let mut dirs: Vec<_> = entries
        .filter_map(Result::ok)
        .filter(|e| e.file_name().to_str().is_some_and(|n| n.starts_with("rfkill")))
        .map(|e| e.path())
        .collect();
    dirs.sort();

    for dir in dirs {
        let Ok(kind) = fs::read_to_string(dir.join("type")) else {
            continue;
        };
        if !kind.trim().eq_ignore_ascii_case("bluetooth") {
            continue;
        }

        if let (Some(owner), Some(active)) = (rfkill_hci_owner(&dir), active_hci)
            && owner != active
        {
            continue;
        }

        let soft = fs::read_to_string(dir.join("soft"))
            .ok()
            .is_some_and(|v| v.trim() == "1");
        let hard = fs::read_to_string(dir.join("hard"))
            .ok()
            .is_some_and(|v| v.trim() == "1");

        if soft || hard {
            state.soft_blocked |= soft;
            state.hard_blocked |= hard;
            if state.label.is_none() {
                state.label = fs::read_to_string(dir.join("name"))
                    .ok()
                    .map(|s| s.trim().to_string())
                    .filter(|s| !s.is_empty());
            }
        }
    }

    state
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::mem::{offset_of, size_of};
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::time::Duration;

    static SEQ: AtomicU64 = AtomicU64::new(0);

    fn temp_dir(tag: &str) -> PathBuf {
        let n = SEQ.fetch_add(1, Ordering::Relaxed);
        std::env::temp_dir().join(format!(
            "dusky-bt-telemetry-{tag}-{}-{n}",
            std::process::id()
        ))
    }

    #[test]
    fn hci_dev_info_and_stats_match_linux_x86_64_abi_layout() {
        assert_eq!(size_of::<HciDevStats>(), 40);
        assert_eq!(size_of::<HciDevInfo>(), 92);
        assert_eq!(offset_of!(HciDevInfo, dev_id), 0);
        assert_eq!(offset_of!(HciDevInfo, name), 2);
        assert_eq!(offset_of!(HciDevInfo, bdaddr), 10);
        assert_eq!(offset_of!(HciDevInfo, flags), 16);
        assert_eq!(offset_of!(HciDevInfo, dev_type), 20);
        assert_eq!(offset_of!(HciDevInfo, features), 21);
        assert_eq!(offset_of!(HciDevInfo, pkt_type), 32);
        assert_eq!(offset_of!(HciDevInfo, stat), 52);
        assert_eq!(
            offset_of!(HciDevInfo, stat) + offset_of!(HciDevStats, byte_rx),
            84
        );
        assert_eq!(
            offset_of!(HciDevInfo, stat) + offset_of!(HciDevStats, byte_tx),
            88
        );
    }

    #[test]
    fn traffic_tracker_handles_seeding_wrap_and_kernel_counter_reset() {
        let t0 = Instant::now();
        let mut tracker = TrafficTracker::new();
        tracker.reset("hci0");

        // First sample seeds cumulative totals with 0 B/s rate.
        let s0 = tracker.update(10_000, 20_000, t0, None);
        assert_eq!(s0.total_tx, 10_000);
        assert_eq!(s0.total_rx, 20_000);
        assert_eq!(s0.tx_rate_bps, 0.0);
        assert!(!s0.active_tx);

        // Normal 1s delta.
        let s1 = tracker.update(12_048, 24_096, t0 + Duration::from_secs(1), None);
        assert_eq!(s1.total_tx, 12_048);
        assert_eq!(s1.total_rx, 24_096);
        assert!((s1.tx_rate_bps - 2048.0).abs() < 1.0);
        assert!((s1.rx_rate_bps - 4096.0).abs() < 1.0);
        assert!(s1.active_tx && s1.active_rx);

        // Genuine 4 GiB u32 rollover (delta <= 64 MiB).
        tracker.reset("hci0");
        let _ = tracker.update(u32::MAX - 999, 100, t0, None);
        let s_wrap = tracker.update(500, 200, t0 + Duration::from_secs(1), None);
        assert_eq!(s_wrap.total_tx, u64::from(u32::MAX - 999) + 1500);
        assert!((s_wrap.tx_rate_bps - 1500.0).abs() < 1.0);

        // Kernel reset on adapter power cycle (50 MB -> 1024 B must NOT inject a 4 GiB spike).
        tracker.reset("hci0");
        let _ = tracker.update(50_000_000, 10_000_000, t0, None);
        let s_reset = tracker.update(1024, 512, t0 + Duration::from_secs(1), None);
        assert_eq!(s_reset.total_tx, 50_001_024);
        assert_eq!(s_reset.total_rx, 10_000_512);
        assert!((s_reset.tx_rate_bps - 1024.0).abs() < 1.0);
    }

    #[test]
    fn sysfs_rfkill_and_bnep_readers_parse_fixtures_accurately() {
        let root = temp_dir("sysfs");
        let rfkill = root.join("rfkill");
        let net = root.join("net");
        let hci0_dev = root.join("devices/hci0");
        let hci1_dev = root.join("devices/hci1");
        fs::create_dir_all(&hci0_dev).unwrap();
        fs::create_dir_all(&hci1_dev).unwrap();

        // Non-bluetooth wlan blocked entry must be ignored.
        let rf0 = rfkill.join("rfkill0");
        fs::create_dir_all(&rf0).unwrap();
        fs::write(rf0.join("type"), "wlan\n").unwrap();
        fs::write(rf0.join("soft"), "1\n").unwrap();
        fs::write(rf0.join("hard"), "1\n").unwrap();

        // Other adapter hci1 soft-blocked must be ignored when active is hci0.
        let rf1 = rfkill.join("rfkill1");
        fs::create_dir_all(&rf1).unwrap();
        fs::write(rf1.join("type"), "bluetooth\n").unwrap();
        fs::write(rf1.join("soft"), "1\n").unwrap();
        fs::write(rf1.join("hard"), "0\n").unwrap();
        fs::write(rf1.join("name"), "hci1\n").unwrap();
        std::os::unix::fs::symlink(&hci1_dev, rf1.join("device")).unwrap();

        assert!(!read_bluetooth_rfkill(&rfkill, Some("hci0")).is_blocked());
        assert!(read_bluetooth_rfkill(&rfkill, Some("hci1")).soft_blocked);

        // Platform bluetooth hard-blocked switch applies to all adapters.
        let rf2 = rfkill.join("rfkill2");
        fs::create_dir_all(&rf2).unwrap();
        fs::write(rf2.join("type"), "bluetooth\n").unwrap();
        fs::write(rf2.join("soft"), "0\n").unwrap();
        fs::write(rf2.join("hard"), "1\n").unwrap();
        fs::write(rf2.join("name"), "tpacpi_bluetooth_sw\n").unwrap();

        let state = read_bluetooth_rfkill(&rfkill, Some("hci0"));
        assert!(state.hard_blocked);
        assert!(!state.soft_blocked);
        assert_eq!(state.label.as_deref(), Some("tpacpi_bluetooth_sw"));

        // Passive bnep0 reader skips down interfaces and reads up ones.
        let bnep0 = net.join("bnep0/statistics");
        fs::create_dir_all(&bnep0).unwrap();
        fs::write(net.join("bnep0/operstate"), "up\n").unwrap();
        fs::write(bnep0.join("tx_bytes"), "4096\n").unwrap();
        fs::write(bnep0.join("rx_bytes"), "8192\n").unwrap();
        assert_eq!(
            read_active_bnep_bytes(&net),
            Some(("bnep0".to_string(), 4096, 8192))
        );

        let _ = fs::remove_dir_all(&root);
    }
}
