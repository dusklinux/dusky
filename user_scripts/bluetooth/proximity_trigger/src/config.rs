use serde::{Deserialize, Serialize};
use std::{
    collections::HashSet,
    path::{Path, PathBuf},
};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(untagged)]
pub enum CommandList {
    Single(String),
    Multiple(Vec<String>),
}
impl Default for CommandList {
    fn default() -> Self {
        Self::Multiple(Vec::new())
    }
}
impl CommandList {
    pub fn to_vec(&self) -> Vec<String> {
        let commands = match self {
            Self::Single(s) => std::slice::from_ref(s),
            Self::Multiple(v) => v.as_slice(),
        };
        commands
            .iter()
            .filter(|s| !s.trim().is_empty())
            .cloned()
            .collect()
    }
}
pub const DEFAULT_LOCK_COMMAND: &str = "if [ -x \"$HOME/user_scripts/hyprlock/lock.sh\" ]; then exec \"$HOME/user_scripts/hyprlock/lock.sh\"; else exec hyprlock; fi";

fn default_name() -> String {
    "Bluetooth Device".into()
}
fn default_true() -> bool {
    true
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DeviceEntry {
    pub mac: String,
    #[serde(default = "default_name")]
    pub name: String,
    #[serde(default = "default_true")]
    pub enabled: bool,
    #[serde(default = "default_true")]
    pub proximity: bool,
    #[serde(default)]
    pub proximity_level: Option<u8>,
    #[serde(default, rename = "far_rssi", alias = "lock_rssi")]
    pub lock_rssi: Option<i16>,
    #[serde(default, rename = "near_rssi", alias = "unlock_rssi")]
    pub unlock_rssi: Option<i16>,
    #[serde(default)]
    pub signal_timeout_secs: Option<u64>,
    #[serde(default)]
    pub on_connect: Option<CommandList>,
    #[serde(default)]
    pub on_disconnect: Option<CommandList>,
}
impl Default for DeviceEntry {
    fn default() -> Self {
        Self {
            mac: String::new(),
            name: default_name(),
            enabled: true,
            proximity: true,
            proximity_level: None,
            lock_rssi: None,
            unlock_rssi: None,
            signal_timeout_secs: None,
            on_connect: None,
            on_disconnect: None,
        }
    }
}
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SignalLossAction {
    #[default]
    Far,
    Hold,
}
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProximitySource {
    #[default]
    Connection,
    Connected,
    Advertisement,
}
#[derive(Debug, Clone, Copy)]
pub struct DeviceThresholds {
    pub far: i16,
    pub near: i16,
    pub timeout: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Config {
    pub device_mac: String,
    pub device_name: String,
    pub devices: Vec<DeviceEntry>,
    pub transport: String,
    pub proximity_enabled: bool,
    pub proximity_source: ProximitySource,
    pub poll_interval_ms: u64,
    pub auto_connect: bool,
    pub reconnect_interval_secs: u64,
    pub signal_loss_action: SignalLossAction,
    pub connect_hold_secs: u64,
    pub disconnect_hold_secs: u64,
    pub run_initial_connection_hooks: bool,

    /// Empty selects a powered adapter, preferring one that knows a target.
    pub adapter_path: String,
    /// Optional preset: 1 is closest, 10 is weakest. Explicit CLI RSSI overrides it.
    pub proximity_level: Option<u8>,
    #[serde(rename = "far_rssi", alias = "lock_rssi")]
    pub lock_rssi: i16,
    #[serde(rename = "near_rssi", alias = "unlock_rssi")]
    pub unlock_rssi: i16,
    pub signal_timeout_secs: u64,
    pub consecutive_drops: usize,
    #[serde(rename = "far_hold_secs", alias = "lock_hold_secs")]
    pub lock_hold_secs: u64,
    #[serde(rename = "near_hold_secs", alias = "unlock_hold_secs")]
    pub unlock_hold_secs: u64,
    pub rssi_window: usize,
    pub sample_max_gap_secs: u64,
    #[serde(
        rename = "on_far",
        alias = "on_lock",
        alias = "far_commands",
        alias = "far_scripts",
        alias = "lock_commands",
        alias = "lock_scripts"
    )]
    pub on_lock: CommandList,
    #[serde(
        rename = "on_near",
        alias = "on_unlock",
        alias = "near_commands",
        alias = "near_scripts",
        alias = "unlock_commands",
        alias = "unlock_scripts"
    )]
    pub on_unlock: CommandList,
    #[serde(alias = "connect_commands", alias = "connect_scripts")]
    pub on_connect: CommandList,
    #[serde(alias = "disconnect_commands", alias = "disconnect_scripts")]
    pub on_disconnect: CommandList,
    pub lock_cmd: Option<String>,
    pub unlock_cmd: Option<String>,
    pub direct_hyprlock_signal: bool,
    pub wake_display: bool,
    pub dpms_settle_ms: u64,
    pub notifications: bool,
    pub startup_grace_secs: u64,
    #[serde(rename = "return_cooldown_secs", alias = "lock_cooldown_secs")]
    pub lock_cooldown_secs: u64,
}
impl Default for Config {
    fn default() -> Self {
        Self {
            device_mac: String::new(),
            device_name: default_name(),
            devices: Vec::new(),
            transport: "le".into(),
            proximity_enabled: true,
            proximity_source: ProximitySource::Connection,
            poll_interval_ms: 1000,
            auto_connect: false,
            reconnect_interval_secs: 15,
            signal_loss_action: SignalLossAction::Far,
            connect_hold_secs: 2,
            disconnect_hold_secs: 5,
            run_initial_connection_hooks: false,
            adapter_path: String::new(),
            proximity_level: None,
            lock_rssi: -80,
            unlock_rssi: -70,
            signal_timeout_secs: 120,
            consecutive_drops: 3,
            lock_hold_secs: 6,
            unlock_hold_secs: 2,
            rssi_window: 5,
            sample_max_gap_secs: 10,
            on_lock: CommandList::Single(DEFAULT_LOCK_COMMAND.into()),
            on_unlock: CommandList::default(),
            on_connect: CommandList::default(),
            on_disconnect: CommandList::default(),
            lock_cmd: None,
            unlock_cmd: None,
            direct_hyprlock_signal: true,
            wake_display: true,
            dpms_settle_ms: 200,
            notifications: true,
            startup_grace_secs: 10,
            lock_cooldown_secs: 3,
        }
    }
}
impl Config {
    pub fn get_far_commands(&self) -> Vec<String> {
        self.lock_cmd.as_ref().map_or_else(
            || self.on_lock.to_vec(),
            |s| CommandList::Single(s.clone()).to_vec(),
        )
    }
    pub fn get_near_commands(&self) -> Vec<String> {
        self.unlock_cmd.as_ref().map_or_else(
            || self.on_unlock.to_vec(),
            |s| CommandList::Single(s.clone()).to_vec(),
        )
    }
    pub fn preferred_config_path() -> PathBuf {
        let base = std::env::var_os("XDG_CONFIG_HOME")
            .filter(|s| !s.is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                PathBuf::from(std::env::var_os("HOME").expect("HOME must be set")).join(".config")
            });
        base.join("dusky/settings/bluetooth_proximity/config.toml")
    }
    pub fn initialize(path: &Path) -> Result<(), Box<dyn std::error::Error>> {
        let parent = path
            .parent()
            .filter(|p| !p.as_os_str().is_empty())
            .unwrap_or(Path::new("."));
        std::fs::create_dir_all(parent)?;
        write_new(path, include_str!("../config.toml"), false)?;
        let hooks = parent.join("hooks");
        std::fs::create_dir_all(&hooks)?;
        for event in ["far", "near", "connect", "disconnect"] {
            write_new(
                &hooks.join(format!("on_{event}.sh")),
                include_str!("../hook-example.sh"),
                true,
            )?;
        }
        Ok(())
    }
    pub fn load(path: Option<&Path>) -> Result<Self, Box<dyn std::error::Error>> {
        let default_path = path.is_none();
        let path = path
            .map(Path::to_path_buf)
            .unwrap_or_else(Self::preferred_config_path);
        if default_path {
            Self::initialize(&path)?;
        }
        let content =
            std::fs::read_to_string(&path).map_err(|e| format!("{}: {e}", path.display()))?;
        Ok(toml::from_str(&content).map_err(|e| format!("{}: {e}", path.display()))?)
    }
    pub fn active_devices(&self) -> Vec<DeviceEntry> {
        if self.devices.is_empty() && !self.device_mac.is_empty() {
            vec![DeviceEntry {
                mac: self.device_mac.clone(),
                name: self.device_name.clone(),
                enabled: true,
                ..DeviceEntry::default()
            }]
        } else {
            self.devices.iter().filter(|d| d.enabled).cloned().collect()
        }
    }
    pub fn has_proximity_devices(&self) -> bool {
        self.proximity_enabled
            && if self.devices.is_empty() {
                !self.device_mac.is_empty()
            } else {
                self.devices.iter().any(|d| d.enabled && d.proximity)
            }
    }
    pub fn uses_advertisements(&self) -> bool {
        self.has_proximity_devices() && self.proximity_source == ProximitySource::Advertisement
    }
    pub fn uses_connection_state(&self) -> bool {
        self.has_proximity_devices() && self.proximity_source == ProximitySource::Connection
    }
    pub fn uses_links(&self) -> bool {
        self.uses_connection_state() || self.uses_connected_rssi()
    }
    pub fn uses_connected_rssi(&self) -> bool {
        self.has_proximity_devices() && self.proximity_source == ProximitySource::Connected
    }
    pub fn thresholds(&self, dev: &DeviceEntry) -> DeviceThresholds {
        let (far, near) = dev
            .proximity_level
            .map_or((self.lock_rssi, self.unlock_rssi), |level| {
                let far = -45 - 5 * (i16::from(level) - 1);
                (far, far + 10)
            });
        DeviceThresholds {
            far: dev.lock_rssi.unwrap_or(far),
            near: dev.unlock_rssi.unwrap_or(near),
            timeout: dev.signal_timeout_secs.unwrap_or(self.signal_timeout_secs),
        }
    }
    pub fn connection_commands(&self, dev: &DeviceEntry, connected: bool) -> Vec<String> {
        if connected {
            dev.on_connect.as_ref().unwrap_or(&self.on_connect).to_vec()
        } else {
            dev.on_disconnect
                .as_ref()
                .unwrap_or(&self.on_disconnect)
                .to_vec()
        }
    }
    pub fn apply_level(&mut self) -> Result<(), Box<dyn std::error::Error>> {
        if let Some(level) = self.proximity_level {
            if !(1..=10).contains(&level) {
                return Err("proximity_level must be 1..10".into());
            }
            self.lock_rssi = -45 - 5 * i16::from(level - 1);
            self.unlock_rssi = self.lock_rssi + 10;
        }
        Ok(())
    }
    pub fn validate(&self) -> Result<(), Box<dyn std::error::Error>> {
        if !(500..=10000).contains(&self.poll_interval_ms)
            || !(5..=86400).contains(&self.reconnect_interval_secs)
        {
            return Err(
                "poll_interval_ms must be 500..10000; reconnect_interval_secs must be 5..86400"
                    .into(),
            );
        }
        if !matches!(self.transport.as_str(), "le" | "bredr" | "auto") {
            return Err("transport must be le, bredr or auto".into());
        }
        if !self.adapter_path.is_empty() {
            zbus::zvariant::ObjectPath::try_from(self.adapter_path.as_str())?;
        }
        if !(-127..=20).contains(&self.lock_rssi)
            || !(-127..=20).contains(&self.unlock_rssi)
            || self.lock_rssi >= self.unlock_rssi
        {
            return Err("RSSI thresholds must be -127..20 with far_rssi < near_rssi".into());
        }
        if self.signal_timeout_secs == 0
            || self.sample_max_gap_secs == 0
            || self.consecutive_drops == 0
            || !(1..=31).contains(&self.rssi_window)
            || self.rssi_window.is_multiple_of(2)
        {
            return Err("timeouts/counts must be positive; rssi_window must be odd, 1..31".into());
        }
        if [
            self.signal_timeout_secs,
            self.sample_max_gap_secs,
            self.lock_hold_secs,
            self.unlock_hold_secs,
            self.startup_grace_secs,
            self.lock_cooldown_secs,
            self.connect_hold_secs,
            self.disconnect_hold_secs,
        ]
        .into_iter()
        .any(|s| s > 86400)
            || self.dpms_settle_ms > 60000
        {
            return Err("timers must be <= one day; dpms_settle_ms <= 60000".into());
        }
        let active = self.active_devices();
        let mut seen = HashSet::new();
        for dev in active {
            let octets: Vec<_> = dev.mac.split(':').collect();
            if octets.len() != 6
                || octets
                    .iter()
                    .any(|s| s.len() != 2 || !s.bytes().all(|b| b.is_ascii_hexdigit()))
            {
                return Err(format!(
                    "invalid device MAC: {} (expected AA:BB:CC:DD:EE:FF)",
                    dev.mac
                )
                .into());
            }
            if dev
                .proximity_level
                .is_some_and(|level| !(1..=10).contains(&level))
            {
                return Err(format!("{}: proximity_level must be 1..10", dev.name).into());
            }
            let limits = self.thresholds(&dev);
            if !(-127..=20).contains(&limits.far)
                || !(-127..=20).contains(&limits.near)
                || limits.far >= limits.near
                || !(1..=86400).contains(&limits.timeout)
            {
                return Err(format!(
                    "{}: invalid per-device RSSI thresholds or timeout",
                    dev.name
                )
                .into());
            }
            if !seen.insert(dev.mac.to_ascii_uppercase()) {
                return Err(format!("duplicate device MAC: {}", dev.mac).into());
            }
        }
        Ok(())
    }
}
fn write_new(
    path: &Path,
    content: &str,
    executable: bool,
) -> Result<(), Box<dyn std::error::Error>> {
    use std::{io::Write, os::unix::fs::PermissionsExt};
    if path.try_exists()? {
        return Ok(());
    }
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let mut file = tempfile::NamedTempFile::new_in(parent)?;
    file.write_all(content.as_bytes())?;
    file.as_file()
        .set_permissions(std::fs::Permissions::from_mode(if executable {
            0o755
        } else {
            0o644
        }))?;
    match file.persist_noclobber(path) {
        Ok(_) => Ok(()),
        Err(e) if e.error.kind() == std::io::ErrorKind::AlreadyExists => Ok(()),
        Err(e) => Err(e.error.into()),
    }
}
pub fn mac_to_dbus_suffix(mac: &str) -> String {
    format!("dev_{}", mac.to_ascii_uppercase().replace(':', "_"))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn connected_source_and_sampling_limits_are_validated() {
        let mut c = Config {
            device_mac: "AA:BB:CC:DD:EE:FF".into(),
            ..Config::default()
        };
        assert!(c.uses_connection_state());
        assert!(!c.uses_connected_rssi());
        c.proximity_source = ProximitySource::Connected;
        assert!(c.uses_connected_rssi());
        assert!(!c.uses_advertisements());
        for interval in [0, 499, 10001, u64::MAX] {
            c.poll_interval_ms = interval;
            assert!(c.validate().is_err());
        }
        c.poll_interval_ms = 500;
        c.reconnect_interval_secs = 4;
        assert!(c.validate().is_err());
        c.reconnect_interval_secs = 5;
        c.validate().unwrap();
        c.proximity_source = ProximitySource::Advertisement;
        assert!(c.uses_advertisements());
        assert!(!c.uses_connected_rssi());
        assert!(toml::from_str::<Config>("proximity_source='guess'").is_err());
    }
    #[test]
    fn shell_command_whitespace_is_preserved_exactly() {
        let command = "printf '%s' continued\\\n";
        let commands = CommandList::Multiple(vec![" \n\t".into(), command.into()]).to_vec();
        assert_eq!(commands, [command]);
        let output = std::process::Command::new("/bin/sh")
            .args(["-c", &commands[0]])
            .output()
            .unwrap();
        assert!(output.status.success());
        assert_eq!(output.stdout, b"continued");
    }
    #[test]
    fn concurrent_initialization_publishes_complete_files_once() {
        let tmp = tempfile::tempdir().unwrap();
        let path = tmp.path().join("settings/config.toml");
        let barrier = std::sync::Barrier::new(16);
        std::thread::scope(|scope| {
            let barrier = &barrier;
            for _ in 0..16 {
                let path = &path;
                scope.spawn(move || {
                    barrier.wait();
                    for _ in 0..20 {
                        Config::initialize(path).unwrap();
                    }
                });
            }
        });
        assert_eq!(
            std::fs::read_to_string(&path).unwrap(),
            include_str!("../config.toml")
        );
        Config::load(Some(&path)).unwrap().validate().unwrap();
        for event in ["far", "near", "connect", "disconnect"] {
            assert_eq!(
                std::fs::read_to_string(
                    path.parent().unwrap().join(format!("hooks/on_{event}.sh"))
                )
                .unwrap(),
                include_str!("../hook-example.sh")
            );
        }
        assert_eq!(
            std::fs::read_dir(path.parent().unwrap()).unwrap().count(),
            2
        );
    }
    #[test]
    fn reject_misplaced_and_unknown_settings() {
        assert!(
            toml::from_str::<Config>("[[devices]]\nmac='AA:BB:CC:DD:EE:FF'\ntransport='le'")
                .is_err()
        );
        assert!(toml::from_str::<Config>("signal_timout_secs=8").is_err());
    }
    #[test]
    fn toml_1_1_inline_tables_match_the_python_editor_parser() {
        let c: Config =
            toml::from_str("devices = [{\nmac = 'AA:BB:CC:DD:EE:FF',\nenabled = true,\n}]\n")
                .unwrap();
        c.validate().unwrap();
        assert_eq!(c.active_devices().len(), 1);
    }
    #[test]
    fn presets_are_monotonic() {
        let mut cfg = Config::default();
        for level in 1..=10 {
            cfg.proximity_level = Some(level);
            cfg.apply_level().unwrap();
            assert_eq!(cfg.lock_rssi, -40 - 5 * i16::from(level));
            assert_eq!(cfg.unlock_rssi - cfg.lock_rssi, 10);
        }
        cfg.proximity_level = Some(11);
        assert!(cfg.apply_level().is_err());
    }
    #[test]
    fn disabled_devices_stay_disabled_and_empty_hooks_stay_empty() {
        let cfg: Config = toml::from_str("device_mac='AA:BB:CC:DD:EE:FF'\non_far=[]\n[[devices]]\nmac='AA:BB:CC:DD:EE:FF'\nenabled=false").unwrap();
        assert!(cfg.get_far_commands().is_empty());
        assert!(cfg.validate().is_ok());
    }
    #[test]
    fn invalid_configuration_is_rejected() {
        let mut c = Config {
            device_mac: "AA:BB:CC:DD:EE:FF".into(),
            ..Config::default()
        };
        assert!(c.validate().is_ok());
        c.rssi_window = 2;
        assert!(c.validate().is_err());
        c.rssi_window = 5;
        c.unlock_rssi = c.lock_rssi;
        assert!(c.validate().is_err());
    }
    #[test]
    fn defaults_create_a_complete_inactive_layout_without_overwriting() {
        let tmp = tempfile::tempdir().unwrap();
        let path = tmp.path().join("nested/settings/config.toml");
        Config::initialize(&path).unwrap();
        let c = Config::load(Some(&path)).unwrap();
        c.validate().unwrap();
        assert!(c.active_devices().is_empty());
        assert_eq!(c.signal_timeout_secs, 120);
        assert_eq!(c.get_far_commands(), vec![DEFAULT_LOCK_COMMAND.to_owned()]);
        for event in ["far", "near", "connect", "disconnect"] {
            use std::os::unix::fs::PermissionsExt;
            let hook = path.parent().unwrap().join(format!("hooks/on_{event}.sh"));
            assert_eq!(
                std::fs::metadata(&hook).unwrap().permissions().mode() & 0o777,
                0o755
            );
        }
        std::fs::write(&path, "proximity_enabled=false\n").unwrap();
        let hook = path.parent().unwrap().join("hooks/on_connect.sh");
        std::fs::write(&hook, "custom hook").unwrap();
        Config::initialize(&path).unwrap();
        assert_eq!(
            std::fs::read_to_string(&path).unwrap(),
            "proximity_enabled=false\n"
        );
        assert_eq!(std::fs::read_to_string(&hook).unwrap(), "custom hook");
    }
    #[test]
    fn per_device_settings_and_empty_hook_overrides_are_explicit() {
        let c:Config=toml::from_str("proximity_level=8\non_connect=['global']\n[[devices]]\nmac='AA:BB:CC:DD:EE:FF'\nproximity_level=6\nunlock_rssi=-55\nsignal_timeout_secs=180\non_connect=[]\non_disconnect=['device']").unwrap();
        c.validate().unwrap();
        let d = &c.devices[0];
        let limits = c.thresholds(d);
        assert_eq!((limits.far, limits.near, limits.timeout), (-70, -55, 180));
        assert!(c.connection_commands(d, true).is_empty());
        assert_eq!(c.connection_commands(d, false), vec!["device"]);
    }
    #[test]
    fn invalid_signal_policy_and_device_overrides_are_rejected() {
        assert!(toml::from_str::<Config>("signal_loss_action='guess'").is_err());
        let mut c = Config::default();
        c.devices.push(DeviceEntry {
            mac: "AA:BB:CC:DD:EE:FF".into(),
            proximity_level: Some(11),
            ..DeviceEntry::default()
        });
        assert!(c.validate().is_err());
        c.devices[0].proximity_level = Some(8);
        c.devices[0].signal_timeout_secs = Some(0);
        assert!(c.validate().is_err());
    }
    #[test]
    fn canonical_and_legacy_keys_have_identical_behavior() {
        let canonical: Config = toml::from_str("far_rssi=-82\nnear_rssi=-68\nfar_hold_secs=7\nnear_hold_secs=3\nreturn_cooldown_secs=4\non_far=['depart']\non_near=['return']").unwrap();
        let legacy: Config = toml::from_str("lock_rssi=-82\nunlock_rssi=-68\nlock_hold_secs=7\nunlock_hold_secs=3\nlock_cooldown_secs=4\non_lock=['depart']\non_unlock=['return']").unwrap();
        assert_eq!(
            toml::to_string(&canonical).unwrap(),
            toml::to_string(&legacy).unwrap()
        );
        assert!(toml::from_str::<Config>("far_rssi=-82\nlock_rssi=-82").is_err());
        canonical.validate().unwrap();
    }
}
