use crate::config::{Config, DeviceEntry, SignalLossAction, mac_to_dbus_suffix};
use std::{
    collections::{HashMap, VecDeque},
    time::{Duration, Instant},
};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProximityState {
    Near,
    Far,
    Unknown,
}
#[derive(Debug)]
pub struct TrackedDevice {
    pub config: DeviceEntry,
    pub last_seen: Option<Instant>,
    pub filtered_rssi: Option<i16>,
    pub state: ProximityState,
    pub connected: bool,
    reported_connection: Option<bool>,
    connection_since: Option<Instant>,
    last_sample: Option<Instant>,
    samples: VecDeque<i16>,
    near_since: Option<Instant>,
    weak_since: Option<Instant>,
    consecutive_weak: usize,
}
impl TrackedDevice {
    fn new(entry: &DeviceEntry) -> Self {
        Self {
            config: entry.clone(),
            last_seen: None,
            filtered_rssi: None,
            state: ProximityState::Unknown,
            connected: false,
            reported_connection: Some(false),
            connection_since: None,
            last_sample: None,
            samples: VecDeque::new(),
            near_since: None,
            weak_since: None,
            consecutive_weak: 0,
        }
    }
    fn clear_evidence(&mut self) {
        self.samples.clear();
        self.near_since = None;
        self.weak_since = None;
        self.consecutive_weak = 0;
        self.last_sample = None;
        self.filtered_rssi = None;
    }
}
pub struct StateTracker {
    pub current_state: ProximityState,
    pub devices: HashMap<String, TrackedDevice>,
    startup_time: Instant,
    last_far_time: Option<Instant>,
}
#[derive(Debug, PartialEq, Eq)]
pub enum ActionRequired {
    None,
    Far {
        reason: String,
        rssi: Option<i16>,
        device_name: String,
        mac: String,
    },
    Near {
        device_name: String,
        mac: String,
        rssi: i16,
    },
}
impl StateTracker {
    pub fn new(devices: &[DeviceEntry]) -> Self {
        Self::new_at(devices, Instant::now())
    }
    fn new_at(devices: &[DeviceEntry], now: Instant) -> Self {
        Self {
            current_state: ProximityState::Unknown,
            devices: devices
                .iter()
                .map(|d| (mac_to_dbus_suffix(&d.mac), TrackedDevice::new(d)))
                .collect(),
            startup_time: now,
            last_far_time: None,
        }
    }
    /// After suspend/stall, retain aggregate presence but discard stale measurement evidence.
    pub fn reset_observations(&mut self) {
        self.reset_at(Instant::now());
    }
    /// Intentional scan gaps do not prove absence or continuous signal strength.
    pub fn exclude_scan_pause(&mut self, duration: Duration) {
        let now = Instant::now();
        self.startup_time = (self.startup_time + duration).min(now);
        for d in self.devices.values_mut() {
            if let Some(last_seen) = &mut d.last_seen {
                *last_seen = (*last_seen + duration).min(now);
            }
            d.clear_evidence();
        }
    }
    fn reset_at(&mut self, now: Instant) {
        self.startup_time = now;
        for d in self.devices.values_mut() {
            d.clear_evidence();
            d.last_seen = None;
            d.state = ProximityState::Unknown;
        }
    }
    pub fn handle_rssi_sample(&mut self, key: &str, rssi: i16, config: &Config) -> ActionRequired {
        self.sample_at(key, rssi, config, Instant::now())
    }
    fn sample_at(&mut self, key: &str, rssi: i16, config: &Config, now: Instant) -> ActionRequired {
        // BlueZ/HCI uses 127 for an unavailable RSSI. Do not count it as presence.
        if !(-127..=20).contains(&rssi) {
            return ActionRequired::None;
        }
        let Some(d) = self.devices.get_mut(key) else {
            return ActionRequired::None;
        };
        if !config.proximity_enabled || !d.config.proximity {
            return ActionRequired::None;
        }
        let limits = config.thresholds(&d.config);
        if d.last_sample.is_some_and(|t| {
            now.duration_since(t) > Duration::from_secs(config.sample_max_gap_secs)
        }) {
            d.clear_evidence();
        }
        d.last_seen = Some(now);
        d.last_sample = Some(now);
        d.samples.push_back(rssi);
        while d.samples.len() > config.rssi_window {
            d.samples.pop_front();
        }
        let mut values = [0i16; 31];
        for (dst, src) in values.iter_mut().zip(&d.samples) {
            *dst = *src;
        }
        let len = d.samples.len();
        let (_, median, _) = values[..len].select_nth_unstable(len / 2);
        let filtered = *median;
        d.filtered_rssi = Some(filtered);
        if rssi < limits.far && filtered < limits.far {
            d.near_since = None;
            d.consecutive_weak = d.consecutive_weak.saturating_add(1);
            let since = *d.weak_since.get_or_insert(now);
            if d.consecutive_weak >= config.consecutive_drops
                && now.duration_since(since) >= Duration::from_secs(config.lock_hold_secs)
            {
                d.state = ProximityState::Far;
            }
        } else {
            d.weak_since = None;
            d.consecutive_weak = 0;
            if rssi >= limits.near && filtered >= limits.near {
                let since = *d.near_since.get_or_insert(now);
                if now.duration_since(since) >= Duration::from_secs(config.unlock_hold_secs) {
                    d.state = ProximityState::Near;
                }
            } else {
                d.near_since = None;
                // Initial presence within the hysteresis band establishes a baseline without unlocking.
                if d.state == ProximityState::Unknown
                    && self.current_state != ProximityState::Far
                    && filtered >= limits.far
                {
                    d.state = ProximityState::Near;
                }
            }
        }
        self.transition(config, now, Some(key), Some((rssi, filtered)))
    }
    /// Advertisement data proves reception even when BlueZ does not change RSSI.
    /// It does not fabricate a new strength measurement or extend a strength hold.
    pub fn heartbeat(&mut self, key: &str) {
        self.heartbeat_at(key, Instant::now());
    }
    fn heartbeat_at(&mut self, key: &str, now: Instant) {
        if let Some(d) = self.devices.get_mut(key) {
            d.last_seen = Some(now);
        }
    }
    pub fn seed_connections(&mut self, snapshot: &HashMap<String, bool>, config: &Config) {
        for (key, d) in &mut self.devices {
            d.connected = snapshot.get(key).copied().unwrap_or(false);
            d.reported_connection = if config.run_initial_connection_hooks {
                None
            } else {
                Some(d.connected)
            };
            d.connection_since = if config.run_initial_connection_hooks {
                Some(self.startup_time)
            } else {
                None
            };
        }
    }
    pub fn observe_connection(&mut self, key: &str, connected: bool) {
        self.connection_at(key, connected, Instant::now());
    }
    fn connection_at(&mut self, key: &str, connected: bool, now: Instant) {
        if let Some(d) = self.devices.get_mut(key)
            && d.connected != connected
        {
            d.connected = connected;
            d.connection_since = if d.reported_connection == Some(connected) {
                None
            } else {
                Some(now)
            };
        }
    }
    pub fn connection_actions(&mut self, config: &Config) -> Vec<(String, bool)> {
        self.connections_at(config, Instant::now())
    }
    pub fn has_pending_connections(&self) -> bool {
        self.devices.values().any(|d| d.connection_since.is_some())
    }
    fn connections_at(&mut self, config: &Config, now: Instant) -> Vec<(String, bool)> {
        let mut actions = Vec::new();
        for (key, d) in &mut self.devices {
            let hold = if d.connected {
                config.connect_hold_secs
            } else {
                config.disconnect_hold_secs
            };
            if let Some(since) = d.connection_since
                && now.duration_since(since) >= Duration::from_secs(hold)
            {
                d.connection_since = None;
                if d.reported_connection != Some(d.connected) {
                    d.reported_connection = Some(d.connected);
                    actions.push((key.clone(), d.connected));
                }
            }
        }
        actions.sort_unstable_by(|a, b| a.0.cmp(&b.0));
        actions
    }
    pub fn check_timeouts(&mut self, config: &Config) -> ActionRequired {
        self.timeouts_at(config, Instant::now())
    }
    fn timeouts_at(&mut self, config: &Config, now: Instant) -> ActionRequired {
        let mut expired = None;
        if !config.has_proximity_devices() {
            return ActionRequired::None;
        }
        for (key, d) in &mut self.devices {
            if !d.config.proximity {
                continue;
            }
            let limits = config.thresholds(&d.config);
            let age = now.duration_since(d.last_seen.unwrap_or(self.startup_time));
            if age >= Duration::from_secs(limits.timeout.saturating_add(config.lock_hold_secs)) {
                if config.signal_loss_action == SignalLossAction::Far {
                    if d.state != ProximityState::Far
                        && expired.as_ref().is_none_or(|old| key < old)
                    {
                        expired = Some(key.clone());
                    }
                    d.state = ProximityState::Far;
                }
                d.clear_evidence();
            }
        }
        self.transition(config, now, expired.as_deref(), None)
    }
    fn transition(
        &mut self,
        config: &Config,
        now: Instant,
        key: Option<&str>,
        weak_sample: Option<(i16, i16)>,
    ) -> ActionRequired {
        if !config.proximity_enabled || !self.devices.values().any(|d| d.config.proximity) {
            return ActionRequired::None;
        }
        let near = self
            .devices
            .iter()
            .filter(|(_, d)| {
                d.config.proximity
                    && d.state == ProximityState::Near
                    && (self.current_state != ProximityState::Far
                        || (d.last_sample.is_some_and(|t| {
                            now.duration_since(t) <= Duration::from_secs(config.sample_max_gap_secs)
                        }) && d.near_since.is_some_and(|t| {
                            now.duration_since(t) >= Duration::from_secs(config.unlock_hold_secs)
                        })))
            })
            .min_by_key(|(k, _)| *k);
        if let Some((_, d)) = near {
            if self.current_state == ProximityState::Unknown {
                self.current_state = ProximityState::Near;
                return ActionRequired::None;
            }
            if self.current_state == ProximityState::Far
                && !self.last_far_time.is_some_and(|t| {
                    now.duration_since(t) < Duration::from_secs(config.lock_cooldown_secs)
                })
            {
                self.current_state = ProximityState::Near;
                return ActionRequired::Near {
                    device_name: d.config.name.clone(),
                    mac: d.config.mac.clone(),
                    rssi: d.filtered_rssi.unwrap_or(0),
                };
            }
        } else if self
            .devices
            .values()
            .filter(|d| d.config.proximity)
            .all(|d| d.state == ProximityState::Far)
            && self.current_state != ProximityState::Far
            && now.duration_since(self.startup_time)
                >= Duration::from_secs(config.startup_grace_secs)
        {
            self.current_state = ProximityState::Far;
            self.last_far_time = Some(now);
            if let Some(d) = key.and_then(|k| self.devices.get(k)).or_else(|| {
                self.devices
                    .iter()
                    .filter(|(_, d)| d.config.proximity)
                    .min_by_key(|(k, _)| *k)
                    .map(|(_, d)| d)
            }) {
                let reason = match weak_sample {
                    Some((raw, filtered)) => {
                        format!("sustained weak signal ({filtered} dBm filtered, {raw} dBm raw)")
                    }
                    None if key.is_some() => format!(
                        "{} signal timeout ({}s + hold {}s)",
                        d.config.name,
                        config.thresholds(&d.config).timeout,
                        config.lock_hold_secs
                    ),
                    None => "all proximity targets remain far".to_owned(),
                };
                return ActionRequired::Far {
                    reason,
                    rssi: d.filtered_rssi,
                    device_name: d.config.name.clone(),
                    mac: d.config.mac.clone(),
                };
            }
        }
        ActionRequired::None
    }
    pub fn format_rssi_meter(rssi: i16) -> String {
        let filled = usize::try_from((rssi.clamp(-100, -40) + 100) / 6).unwrap_or(0);
        format!(
            "[{}{}] {rssi:>4} dBm",
            "█".repeat(filled),
            "░".repeat(10 - filled)
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    const KEY: &str = "dev_AA_BB_CC_DD_EE_FF";
    fn fixture() -> (StateTracker, Config, Instant) {
        let c = Config {
            device_mac: "AA:BB:CC:DD:EE:FF".into(),
            rssi_window: 1,
            signal_timeout_secs: 45,
            startup_grace_secs: 0,
            ..Config::default()
        };
        let now = Instant::now();
        (StateTracker::new_at(&c.active_devices(), now), c, now)
    }
    #[test]
    fn intentional_scan_pause_preserves_silence_budget_and_clears_strength() {
        let (mut tracker, c, _) = fixture();
        let now = Instant::now();
        let before = now - Duration::from_secs(30);
        tracker.startup_time = before;
        let d = tracker.devices.get_mut(KEY).unwrap();
        d.last_seen = Some(before);
        d.state = ProximityState::Near;
        d.near_since = Some(before);
        d.consecutive_weak = 2;
        tracker.current_state = ProximityState::Near;
        tracker.exclude_scan_pause(Duration::from_secs(20));
        let d = &tracker.devices[KEY];
        assert_eq!(d.last_seen, Some(before + Duration::from_secs(20)));
        assert!(d.near_since.is_none());
        assert_eq!(d.consecutive_weak, 0);
        assert_eq!(d.state, ProximityState::Near);
        assert!(matches!(tracker.timeouts_at(&c, now), ActionRequired::None));
        tracker.reset_observations();
        tracker.exclude_scan_pause(Duration::from_secs(20));
        assert!(matches!(tracker.check_timeouts(&c), ActionRequired::None));
    }
    #[test]
    fn stress_mixed_devices_noise_gaps_heartbeats_and_resets() {
        let mut departures = 0;
        let mut returns = 0;
        for window in [1, 5, 31] {
            for policy in [SignalLossAction::Far, SignalLossAction::Hold] {
                let (_, mut c, start) = fixture();
                c.rssi_window = window;
                c.signal_loss_action = policy;
                c.signal_timeout_secs = 3;
                c.sample_max_gap_secs = 2;
                c.lock_hold_secs = 1;
                c.unlock_hold_secs = 1;
                c.lock_cooldown_secs = 1;
                c.devices = vec![
                    DeviceEntry {
                        mac: "AA:BB:CC:DD:EE:FF".into(),
                        ..DeviceEntry::default()
                    },
                    DeviceEntry {
                        mac: "11:22:33:44:55:66".into(),
                        ..DeviceEntry::default()
                    },
                    DeviceEntry {
                        mac: "00:11:22:33:44:55".into(),
                        proximity: false,
                        ..DeviceEntry::default()
                    },
                ];
                let keys = [KEY, "dev_11_22_33_44_55_66", "dev_00_11_22_33_44_55"];
                let mut tracker = StateTracker::new_at(&c.active_devices(), start);
                let mut random = 7u64;
                let mut elapsed = Duration::ZERO;
                for index in 0..100_000 {
                    random = random.wrapping_mul(6364136223846793005).wrapping_add(1);
                    elapsed += Duration::from_millis(if index % 997 == 0 { 4000 } else { 20 });
                    let now = start + elapsed;
                    if index % 1531 == 0 {
                        tracker.reset_at(now);
                    }
                    let key = keys[(random % 3) as usize];
                    let rssi = if index % 113 == 0 {
                        127
                    } else {
                        (if index / 500 % 2 == 0 { -50 } else { -100 })
                            + ((random >> 8) % 11) as i16
                    };
                    if index % 17 == 0 {
                        tracker.heartbeat_at(KEY, now);
                    }
                    for timeout in [false, true] {
                        let previous = tracker.current_state;
                        let action = if timeout {
                            tracker.timeouts_at(&c, now)
                        } else {
                            tracker.sample_at(key, rssi, &c, now)
                        };
                        match action {
                            ActionRequired::Far { .. } => {
                                departures += 1;
                                assert_ne!(previous, ProximityState::Far);
                                assert!(
                                    tracker
                                        .devices
                                        .values()
                                        .filter(|d| d.config.proximity)
                                        .all(|d| d.state == ProximityState::Far)
                                );
                            }
                            ActionRequired::Near { mac, rssi, .. } => {
                                returns += 1;
                                assert_eq!(previous, ProximityState::Far);
                                let d = &tracker.devices[&mac_to_dbus_suffix(&mac)];
                                assert!(d.config.proximity);
                                assert!(rssi >= c.thresholds(&d.config).near);
                                assert!(
                                    now.duration_since(d.last_sample.unwrap())
                                        <= Duration::from_secs(c.sample_max_gap_secs)
                                );
                                assert!(
                                    now.duration_since(d.near_since.unwrap())
                                        >= Duration::from_secs(c.unlock_hold_secs)
                                );
                            }
                            ActionRequired::None => {}
                        }
                    }
                    for d in tracker.devices.values() {
                        assert!(d.samples.len() <= window);
                        if let Some(filtered) = d.filtered_rssi {
                            let mut sorted: Vec<_> = d.samples.iter().copied().collect();
                            sorted.sort_unstable();
                            assert_eq!(filtered, sorted[sorted.len() / 2]);
                        }
                        if !d.config.proximity {
                            assert!(d.samples.is_empty());
                            assert_eq!(d.state, ProximityState::Unknown);
                        }
                    }
                }
            }
        }
        assert!(departures > 100 && returns > 100);
    }
    fn sample(
        t: &mut StateTracker,
        c: &Config,
        now: Instant,
        sec: u64,
        rssi: i16,
    ) -> ActionRequired {
        t.sample_at(KEY, rssi, c, now + Duration::from_secs(sec))
    }
    #[test]
    fn no_unlock_on_startup_and_no_lock_on_one_outlier() {
        let (mut t, c, n) = fixture();
        sample(&mut t, &c, n, 0, -40);
        assert_eq!(sample(&mut t, &c, n, 2, -40), ActionRequired::None);
        assert_eq!(t.current_state, ProximityState::Near);
        assert_eq!(sample(&mut t, &c, n, 3, -99), ActionRequired::None);
        assert_eq!(
            t.timeouts_at(&c, n + Duration::from_secs(4)),
            ActionRequired::None
        );
        sample(&mut t, &c, n, 5, -40);
        assert_eq!(t.current_state, ProximityState::Near);
    }
    #[test]
    fn observed_short_gaps_do_not_lock_but_true_loss_does() {
        let (mut t, c, n) = fixture();
        sample(&mut t, &c, n, 0, -40);
        sample(&mut t, &c, n, 2, -40);
        for sec in [8, 12, 30, 50] {
            assert_eq!(
                t.timeouts_at(&c, n + Duration::from_secs(sec)),
                ActionRequired::None
            );
        }
        assert!(matches!(
            t.timeouts_at(&c, n + Duration::from_secs(53)),
            ActionRequired::Far { .. }
        ));
        assert_eq!(
            t.timeouts_at(&c, n + Duration::from_secs(54)),
            ActionRequired::None
        );
    }
    #[test]
    fn far_and_near_require_continuous_samples_and_holds() {
        let (mut t, c, n) = fixture();
        sample(&mut t, &c, n, 0, -40);
        sample(&mut t, &c, n, 2, -40);
        sample(&mut t, &c, n, 3, -90);
        sample(&mut t, &c, n, 5, -90);
        assert!(matches!(
            sample(&mut t, &c, n, 9, -90),
            ActionRequired::Far { .. }
        ));
        sample(&mut t, &c, n, 10, -40);
        sample(&mut t, &c, n, 11, -74); // neutral resets near hold
        sample(&mut t, &c, n, 12, -40);
        assert_eq!(sample(&mut t, &c, n, 13, -40), ActionRequired::None);
        assert!(matches!(
            sample(&mut t, &c, n, 14, -40),
            ActionRequired::Near { .. }
        ));
    }
    #[test]
    fn stale_samples_cannot_complete_a_hold() {
        let (mut t, c, n) = fixture();
        sample(&mut t, &c, n, 0, -40);
        sample(&mut t, &c, n, 2, -40);
        sample(&mut t, &c, n, 3, -90);
        sample(&mut t, &c, n, 4, -90);
        assert_eq!(sample(&mut t, &c, n, 20, -90), ActionRequired::None);
    }
    #[test]
    fn median_rejects_a_brief_weak_burst() {
        let (mut t, mut c, n) = fixture();
        c.rssi_window = 5;
        for sec in 0..5 {
            sample(&mut t, &c, n, sec, -40);
        }
        sample(&mut t, &c, n, 5, -99);
        sample(&mut t, &c, n, 6, -99);
        sample(&mut t, &c, n, 7, -40);
        assert_eq!(t.devices[KEY].filtered_rssi, Some(-40));
        assert_eq!(t.current_state, ProximityState::Near);
    }
    #[test]
    fn unknown_second_device_prevents_premature_departure() {
        let (mut t, mut c, n) = fixture();
        c.devices = c.active_devices();
        c.devices.push(DeviceEntry {
            mac: "11:22:33:44:55:66".into(),
            name: "second".into(),
            enabled: true,
            ..DeviceEntry::default()
        });
        t.devices.insert(
            "dev_11_22_33_44_55_66".into(),
            TrackedDevice::new(&c.devices[1]),
        );
        for sec in [0, 3, 6] {
            assert_eq!(sample(&mut t, &c, n, sec, -90), ActionRequired::None);
        }
        assert!(matches!(
            t.timeouts_at(&c, n + Duration::from_secs(51)),
            ActionRequired::Far { .. }
        ));
    }
    #[test]
    fn disconnect_does_not_prove_distance() {
        let (mut t, c, n) = fixture();
        sample(&mut t, &c, n, 0, -40);
        sample(&mut t, &c, n, 2, -40);
        t.connection_at(KEY, true, n);
        assert_eq!(t.connections_at(&c, n), vec![(KEY.into(), true)]);
        t.connection_at(KEY, false, n + Duration::from_secs(1));
        assert!(t.connections_at(&c, n + Duration::from_secs(2)).is_empty());
        assert_eq!(
            t.connections_at(&c, n + Duration::from_secs(3)),
            vec![(KEY.into(), false)]
        );
        assert_eq!(
            t.timeouts_at(&c, n + Duration::from_secs(3)),
            ActionRequired::None
        );
    }
    #[test]
    fn unavailable_rssi_does_not_count_as_presence() {
        let (mut t, c, n) = fixture();
        sample(&mut t, &c, n, 0, 127);
        assert!(t.devices[KEY].last_seen.is_none());
    }
    #[test]
    fn data_heartbeat_prevents_false_timeout_without_completing_hold() {
        let (mut t, c, n) = fixture();
        sample(&mut t, &c, n, 0, -40);
        sample(&mut t, &c, n, 2, -40);
        t.heartbeat_at(KEY, n + Duration::from_secs(45));
        assert_eq!(
            t.timeouts_at(&c, n + Duration::from_secs(60)),
            ActionRequired::None
        );
    }
    #[test]
    fn neutral_signal_after_resume_cannot_unlock_a_far_session() {
        let (mut t, c, n) = fixture();
        assert!(matches!(
            t.timeouts_at(&c, n + Duration::from_secs(51)),
            ActionRequired::Far { .. }
        ));
        t.reset_at(n + Duration::from_secs(52));
        assert_eq!(sample(&mut t, &c, n, 53, -74), ActionRequired::None);
        assert_eq!(t.current_state, ProximityState::Far);
        assert_eq!(sample(&mut t, &c, n, 54, -40), ActionRequired::None);
        assert!(matches!(
            sample(&mut t, &c, n, 56, -40),
            ActionRequired::Near { .. }
        ));
    }
    #[test]
    fn one_near_target_prevents_departure_when_another_expires() {
        let (mut t, mut c, n) = fixture();
        c.devices = c.active_devices();
        c.devices.push(DeviceEntry {
            mac: "11:22:33:44:55:66".into(),
            name: "second".into(),
            enabled: true,
            ..DeviceEntry::default()
        });
        t.devices.insert(
            "dev_11_22_33_44_55_66".into(),
            TrackedDevice::new(&c.devices[1]),
        );
        for sec in [0, 2, 10, 20, 30, 40, 50, 60] {
            sample(&mut t, &c, n, sec, -40);
            assert_eq!(
                t.timeouts_at(&c, n + Duration::from_secs(sec)),
                ActionRequired::None
            );
        }
        assert_eq!(t.current_state, ProximityState::Near);
        assert_eq!(
            t.devices["dev_11_22_33_44_55_66"].state,
            ProximityState::Far
        );
    }
    #[test]
    fn hold_policy_preserves_presence_but_discards_stale_strength() {
        let (mut t, mut c, n) = fixture();
        c.signal_loss_action = SignalLossAction::Hold;
        sample(&mut t, &c, n, 0, -40);
        sample(&mut t, &c, n, 2, -40);
        assert_eq!(
            t.timeouts_at(&c, n + Duration::from_secs(600)),
            ActionRequired::None
        );
        assert_eq!(t.current_state, ProximityState::Near);
        assert!(t.devices[KEY].filtered_rssi.is_none());
        for sec in [601, 604, 607] {
            sample(&mut t, &c, n, sec, -90);
        }
        assert_eq!(t.current_state, ProximityState::Far);
    }
    #[test]
    fn stale_approach_during_cooldown_cannot_unlock() {
        let (mut t, mut c, n) = fixture();
        c.signal_loss_action = SignalLossAction::Hold;
        c.lock_cooldown_secs = 100;
        for sec in [0, 3, 6] {
            sample(&mut t, &c, n, sec, -90);
        }
        sample(&mut t, &c, n, 7, -40);
        sample(&mut t, &c, n, 9, -40);
        assert_eq!(
            t.timeouts_at(&c, n + Duration::from_secs(110)),
            ActionRequired::None
        );
        assert_eq!(t.current_state, ProximityState::Far);
    }
    #[test]
    fn connection_baseline_prevents_missed_disconnect_and_duplicate_startup() {
        let (mut t, c, n) = fixture();
        t.seed_connections(&HashMap::from([(KEY.into(), true)]), &c);
        assert!(t.connections_at(&c, n).is_empty());
        t.connection_at(KEY, false, n + Duration::from_secs(1));
        assert_eq!(
            t.connections_at(&c, n + Duration::from_secs(3)),
            vec![(KEY.into(), false)]
        );
        assert!(t.connections_at(&c, n + Duration::from_secs(4)).is_empty());
        assert!(!t.has_pending_connections());
    }
    #[test]
    fn brief_disconnect_and_connect_are_debounced_without_reset_by_duplicates() {
        let (mut t, mut c, n) = fixture();
        c.connect_hold_secs = 2;
        t.seed_connections(&HashMap::from([(KEY.into(), true)]), &c);
        t.connection_at(KEY, false, n);
        t.connection_at(KEY, true, n + Duration::from_secs(1));
        assert!(t.connections_at(&c, n + Duration::from_secs(3)).is_empty());
        t.connection_at(KEY, false, n + Duration::from_secs(4));
        t.connection_at(KEY, false, n + Duration::from_secs(5));
        assert_eq!(
            t.connections_at(&c, n + Duration::from_secs(6)),
            vec![(KEY.into(), false)]
        );
        t.connection_at(KEY, true, n + Duration::from_secs(7));
        assert!(t.connections_at(&c, n + Duration::from_secs(8)).is_empty());
        assert_eq!(
            t.connections_at(&c, n + Duration::from_secs(9)),
            vec![(KEY.into(), true)]
        );
    }
    #[test]
    fn initial_connection_hooks_are_opt_in_for_both_states() {
        for connected in [false, true] {
            let (mut t, mut c, n) = fixture();
            c.run_initial_connection_hooks = true;
            t.seed_connections(&HashMap::from([(KEY.into(), connected)]), &c);
            let hold = if connected {
                c.connect_hold_secs
            } else {
                c.disconnect_hold_secs
            };
            assert_eq!(
                t.connections_at(&c, n + Duration::from_secs(hold)),
                vec![(KEY.into(), connected)]
            );
            assert!(
                t.connections_at(&c, n + Duration::from_secs(hold + 1))
                    .is_empty()
            );
        }
    }
    #[test]
    fn connection_only_targets_do_not_participate_in_proximity() {
        let (_, mut c, n) = fixture();
        c.devices = c.active_devices();
        c.devices[0].proximity = false;
        let mut t = StateTracker::new_at(&c.active_devices(), n);
        assert_eq!(sample(&mut t, &c, n, 0, -90), ActionRequired::None);
        assert_eq!(
            t.timeouts_at(&c, n + Duration::from_secs(500)),
            ActionRequired::None
        );
        t.connection_at(KEY, true, n);
        assert_eq!(t.connections_at(&c, n), vec![(KEY.into(), true)]);
    }
    #[test]
    fn per_device_timeout_overrides_the_global_timeout() {
        let (_, mut c, n) = fixture();
        c.devices = c.active_devices();
        c.devices[0].signal_timeout_secs = Some(120);
        let mut t = StateTracker::new_at(&c.active_devices(), n);
        assert_eq!(
            t.timeouts_at(&c, n + Duration::from_secs(125)),
            ActionRequired::None
        );
        assert!(matches!(
            t.timeouts_at(&c, n + Duration::from_secs(126)),
            ActionRequired::Far { .. }
        ));
    }
}
