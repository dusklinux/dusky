//! Runtime-discovered XDG JSON persistence for Dusky Bluetooth preferences.

use crate::model::{VisibilityMode, normalize_mac};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use std::{env, fs};

pub const CONFIG_VERSION: u32 = 1;
pub const DEFAULT_DISCOVERABLE_TIMEOUT_SECS: u32 = 180;

fn default_version() -> u32 {
    CONFIG_VERSION
}

fn default_hide_unnamed() -> bool {
    true
}

fn default_discoverable_timeout() -> u32 {
    DEFAULT_DISCOVERABLE_TIMEOUT_SECS
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct AppConfig {
    #[serde(default = "default_version")]
    pub version: u32,
    #[serde(default = "default_hide_unnamed")]
    pub hide_unnamed: bool,
    pub discoverable_always: bool,
    #[serde(default = "default_discoverable_timeout")]
    pub discoverable_timeout_secs: u32,
    pub hidden_devices: BTreeSet<String>,
    pub last_adapter: Option<String>,
}

impl Default for AppConfig {
    fn default() -> Self {
        Self {
            version: CONFIG_VERSION,
            hide_unnamed: true,
            discoverable_always: false,
            discoverable_timeout_secs: DEFAULT_DISCOVERABLE_TIMEOUT_SECS,
            hidden_devices: BTreeSet::new(),
            last_adapter: None,
        }
    }
}

impl AppConfig {
    pub fn config_path() -> PathBuf {
        let base = env::var_os("XDG_CONFIG_HOME")
            .filter(|p| !p.is_empty())
            .map(PathBuf::from)
            .filter(|p| p.is_absolute())
            .unwrap_or_else(|| {
                PathBuf::from(env::var_os("HOME").unwrap_or_default()).join(".config")
            });
        base.join("dusky_bluetooth").join("config.json")
    }

    pub fn load() -> Self {
        Self::load_from(&Self::config_path())
    }

    pub fn load_from(path: &Path) -> Self {
        let Ok(raw) = fs::read_to_string(path) else {
            return Self::default();
        };
        let Ok(mut cfg) = serde_json::from_str::<Self>(&raw) else {
            return Self::default();
        };
        cfg.normalize();
        cfg
    }

    pub fn save(&self) -> Result<(), String> {
        self.save_to(&Self::config_path())
    }

    pub fn save_to(&self, path: &Path) -> Result<(), String> {
        let mut normalized = self.clone();
        normalized.normalize();

        let parent = path
            .parent()
            .ok_or_else(|| format!("Invalid config path: {}", path.display()))?;
        fs::create_dir_all(parent)
            .map_err(|e| format!("Could not create {}: {e}", parent.display()))?;

        let serialized = serde_json::to_string_pretty(&normalized)
            .map_err(|e| format!("Could not serialize config: {e}"))?
            + "\n";

        let tmp = path.with_file_name(format!(".config.{}.tmp", std::process::id()));
        fs::write(&tmp, serialized)
            .map_err(|e| format!("Could not write {}: {e}", tmp.display()))?;
        if let Err(err) = fs::rename(&tmp, path) {
            let _ = fs::remove_file(&tmp);
            return Err(format!("Could not replace {}: {err}", path.display()));
        }
        Ok(())
    }

    pub fn normalize(&mut self) {
        self.version = CONFIG_VERSION;
        self.discoverable_timeout_secs =
            VisibilityMode::clamp_timeout(self.discoverable_timeout_secs);
        self.hidden_devices = self
            .hidden_devices
            .iter()
            .map(|mac| normalize_mac(mac))
            .filter(|mac| !mac.is_empty())
            .collect();
        self.last_adapter = self
            .last_adapter
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_string);
    }

    pub fn preferred_visible_mode(&self) -> VisibilityMode {
        if self.discoverable_always {
            VisibilityMode::AlwaysVisible
        } else {
            VisibilityMode::Temporary {
                timeout_secs: VisibilityMode::clamp_timeout(self.discoverable_timeout_secs),
            }
        }
    }

    pub fn record_visibility_preset(&mut self, mode: VisibilityMode) {
        match mode {
            VisibilityMode::Hidden => {}
            VisibilityMode::AlwaysVisible => {
                self.discoverable_always = true;
            }
            VisibilityMode::Temporary { timeout_secs } => {
                self.discoverable_always = false;
                self.discoverable_timeout_secs = VisibilityMode::clamp_timeout(timeout_secs);
            }
        }
    }

    pub fn hide_device(&mut self, address: &str) -> bool {
        let mac = normalize_mac(address);
        if mac.is_empty() {
            return false;
        }
        self.hidden_devices.insert(mac)
    }

    pub fn unhide_device(&mut self, address: &str) -> bool {
        let mac = normalize_mac(address);
        self.hidden_devices.remove(&mac)
    }

    pub fn is_hidden(&self, address: &str) -> bool {
        let mac = normalize_mac(address);
        !mac.is_empty() && self.hidden_devices.contains(&mac)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};

    static SEQ: AtomicU64 = AtomicU64::new(0);

    fn temp_config_file(tag: &str) -> PathBuf {
        let n = SEQ.fetch_add(1, Ordering::Relaxed);
        env::temp_dir()
            .join(format!("dusky-bt-cfg-{tag}-{}-{n}", std::process::id()))
            .join("config.json")
    }

    #[test]
    fn config_round_trips_normalizes_macs_and_overwrites_atomically() {
        let path = temp_config_file("roundtrip");
        let dir = path.parent().unwrap().to_path_buf();
        let _ = fs::remove_dir_all(&dir);

        let mut cfg = AppConfig::default();
        cfg.hide_unnamed = false;
        cfg.discoverable_always = true;
        cfg.discoverable_timeout_secs = 5; // clamped to 30
        cfg.hide_device("aa-bb-cc-dd-ee-ff");
        cfg.hide_device("AA:BB:CC:DD:EE:FF");
        cfg.last_adapter = Some("hci0".into());
        cfg.save_to(&path).unwrap();

        let loaded = AppConfig::load_from(&path);
        assert_eq!(loaded.version, CONFIG_VERSION);
        assert!(!loaded.hide_unnamed);
        assert!(loaded.discoverable_always);
        assert_eq!(loaded.discoverable_timeout_secs, 30);
        assert_eq!(loaded.hidden_devices.len(), 1);
        assert!(loaded.is_hidden("aa:bb:cc:dd:ee:ff"));
        assert_eq!(loaded.last_adapter.as_deref(), Some("hci0"));

        let mut updated = loaded.clone();
        updated.unhide_device("aa-bb-cc-dd-ee-ff");
        updated.record_visibility_preset(VisibilityMode::Temporary { timeout_secs: 300 });
        updated.save_to(&path).unwrap();

        let reloaded = AppConfig::load_from(&path);
        assert!(!reloaded.discoverable_always);
        assert_eq!(reloaded.discoverable_timeout_secs, 300);
        assert!(reloaded.hidden_devices.is_empty());

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn config_handles_partial_and_malformed_json_gracefully() {
        let path = temp_config_file("resilient");
        let dir = path.parent().unwrap().to_path_buf();
        fs::create_dir_all(&dir).unwrap();

        fs::write(&path, r#"{"hide_unnamed": false}"#).unwrap();
        let partial = AppConfig::load_from(&path);
        assert!(!partial.hide_unnamed);
        assert_eq!(
            partial.discoverable_timeout_secs,
            DEFAULT_DISCOVERABLE_TIMEOUT_SECS
        );
        assert!(partial.hidden_devices.is_empty());

        fs::write(&path, "{not valid json").unwrap();
        let fallback = AppConfig::load_from(&path);
        assert_eq!(fallback, AppConfig::default());

        let _ = fs::remove_dir_all(&dir);
    }
}
