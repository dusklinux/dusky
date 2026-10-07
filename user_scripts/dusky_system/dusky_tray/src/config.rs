//! Panel configuration: layout flags + quick-toggle grid.
//!
//! Mirrors `user_scripts/dusky_system/quickpanal/config.toml` and the
//! `DEFAULT_TOML_CONFIG` fallback embedded in `dusky_quickpanal.py`.
//! All persistent config lives on SSD (`~/.config/dusky/quickpanal/`).
//! All *runtime* caches live on tmpfs via [`crate::backend::runtime_dir`].

use std::fs;
use std::path::PathBuf;

#[derive(Debug, Clone)]
pub struct Layout {
    pub show_weather: bool,
    pub show_metrics: bool,
    pub show_quick_toggles: bool,
    pub show_power_profiles: bool,
    pub show_sliders: bool,
    pub show_notifications: bool,
    #[allow(dead_code)] // Reserved config key; media controls are not implemented.
    pub show_media: bool,
}

impl Default for Layout {
    fn default() -> Self {
        Self {
            show_weather: true,
            show_metrics: true,
            show_quick_toggles: true,
            show_power_profiles: true,
            show_sliders: true,
            show_notifications: true,
            show_media: false,
        }
    }
}

#[derive(Debug, Clone, Default)]
pub struct Toggle {
    pub id: String,
    pub icon: String,
    pub label: String,
    pub tooltip: String,
    pub on_left: String,
    pub on_middle: String,
    pub on_right: String,
}

/// Rust-only appearance options, independent of the shared GTK layout config.
#[derive(Debug, Clone, Copy, PartialEq, serde::Deserialize)]
#[serde(default)]
pub struct Appearance {
    pub opacity: f32,
    pub blur: bool,
}

impl Default for Appearance {
    fn default() -> Self {
        Self {
            opacity: 0.94,
            blur: true,
        }
    }
}

impl Appearance {
    pub fn path() -> PathBuf {
        let xdg = std::env::var_os("XDG_CONFIG_HOME")
            .filter(|p| !p.is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(".config")
            })
            .join("dusky/tray/appearance.toml");
        if xdg.is_file() {
            return xdg;
        }
        // A generic package symlink resolves to /usr/bin, so look up the user's
        // installed defaults independently of the executable's real location.
        if let Some(home) = std::env::var_os("HOME") {
            let local = PathBuf::from(home).join(".local/share/dusky/dusky_tray/appearance.toml");
            if local.is_file() {
                return local;
            }
        }
        // Installed defaults live beside the binary; source-tree builds used bin/.
        if let Ok(exe) = std::env::current_exe()
            && let Some(directory) = exe.parent()
        {
            for base in [Some(directory), directory.parent()].into_iter().flatten() {
                let local = base.join("appearance.toml");
                if local.is_file() {
                    return local;
                }
            }
        }
        let packaged = PathBuf::from("/usr/share/dusky-tray/appearance.toml");
        if packaged.is_file() { packaged } else { xdg }
    }

    pub fn load() -> Option<Self> {
        Self::parse(&fs::read_to_string(Self::path()).ok()?)
    }

    fn parse(raw: &str) -> Option<Self> {
        let settings: Self = toml::from_str(raw).ok()?;
        (settings.opacity.is_finite() && (0.0..=1.0).contains(&settings.opacity))
            .then_some(settings)
    }
}

/// Map existing GTK symbolic names to the embedded vector icon keys.
/// These keys preserve existing configuration; no icon font is used at runtime.
pub fn icon_glyph(gtk_name: &str) -> &'static str {
    match gtk_name {
        "network-wireless-symbolic" => "󰖩",
        "network-wireless-disconnected-symbolic" => "󰖪",
        "timer-symbolic" => "󰓛",
        "view-reveal-symbolic" => "󰈉",
        "preferences-desktop-appearance-symbolic" => "◐",
        "applications-graphics-symbolic" => "◑",
        "folder-download-symbolic" => "󰇚",
        "audio-input-microphone-symbolic" => "\u{F02EC}",
        "audio-input-microphone-muted-symbolic" => "\u{F02ED}",
        "notification-symbolic" => "󰂚",
        "notifications-disabled-symbolic" => "󰂛",
        "bluetooth-active-symbolic" => "󰂯",
        "bluetooth-disabled-symbolic" => "󰂲",
        "power-profile-power-saver-symbolic" => "",
        "power-profile-balanced-symbolic" => "󰀄",
        "power-profile-performance-symbolic" => "",
        "system-shutdown-symbolic" => "⏻︎",
        "weather-few-clouds-symbolic" => "☁︎",
        "drive-harddisk-symbolic" => "▤",
        "cpu-symbolic" => "▣",
        "window-close-symbolic" => "\u{F0156}",
        "edit-clear-all-symbolic" => "\u{F0156}",
        "pan-end-symbolic" => "\u{F0142}",
        "pan-down-symbolic" => "\u{F0140}",
        _ => "●",
    }
}

#[derive(Debug, Clone)]
pub struct AppConfig {
    pub layout: Layout,
    pub toggles: Vec<Toggle>,
}

impl AppConfig {
    pub fn paths() -> (PathBuf, PathBuf) {
        let home = std::env::var_os("HOME")
            .filter(|v| !v.is_empty())
            .map(PathBuf::from)
            .expect("HOME must be set in the user session");
        let dir = std::env::var_os("XDG_CONFIG_HOME")
            .filter(|p| !p.is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| home.join(".config"))
            .join("dusky/quickpanal");
        let file = dir.join("config.toml");
        (dir, file)
    }

    pub fn default_toml() -> &'static str {
        r#"[layout]
show_weather = true
show_metrics = true
show_quick_toggles = true
show_power_profiles = true
show_sliders = true
show_notifications = true
show_media = false

[[toggles]]
id = "wifi"
icon = "network-wireless-symbolic"
label = "Wi-Fi"
tooltip = "Wi-Fi\nLMB: Network Manager"
on_left = "foot --app-id=dusky_tui python ~/user_scripts/dusky_tui/python/main/main.py ~/user_scripts/network_manager/tui_dusky_network.py"

[[toggles]]
id = "idle"
icon = "timer-symbolic"
label = "Hypridle"
tooltip = "Hypridle\nLMB: Toggle | RMB: Lock Screen"
on_left = "~/user_scripts/waybar/toggle_hypridle.sh"
on_right = "~/user_scripts/hyprlock/lock.sh"

[[toggles]]
id = "blur"
icon = "preferences-desktop-appearance-symbolic"
label = "Visuals"
tooltip = "Visuals\nLMB: Toggle Blur/Shadow"
on_left = "~/user_scripts/hypr/hypr_blur_opacity_shadow_toggle.sh toggle"

[[toggles]]
id = "updates"
icon = "folder-download-symbolic"
label = "Updates"
tooltip = "Updates\nLMB: System Update | RMB: Dusky Update"
on_left = "dusky-run kitty --class system_update.sh --hold sh -c '~/user_scripts/update_dusky/system_update.sh --all'"
on_right = "dusky-run kitty --class update_dusky.py --hold sh -c '~/user_scripts/update_dusky/python/update_dusky_supervisor.py'"

[[toggles]]
id = "audio"
icon = "audio-input-microphone-symbolic"
label = "Voice DSP"
tooltip = "Voice DSP & Noise Cancellation\nLMB: Open Studio | RMB: Toggle ON/OFF"
on_left = "python3 ~/user_scripts/audio/dusky_audio_studio/dusky_audio_studio.py --gui-only"
on_right = "python3 ~/user_scripts/audio/dusky_audio_studio/dusky_audio_studio.py --toggle"
"#
    }

    pub fn load() -> Self {
        let (dir, file) = Self::paths();
        let _ = fs::create_dir_all(&dir);
        if !file.exists() {
            let _ = fs::write(&file, Self::default_toml());
        }
        let raw = fs::read_to_string(&file).unwrap_or_else(|_| Self::default_toml().to_owned());
        Self::parse(&raw)
    }

    pub fn parse(raw: &str) -> Self {
        let mut layout = Layout::default();
        let mut toggles = Vec::new();
        let mut explicit_toggles = false;
        if let Ok(table) = raw.parse::<toml::Table>() {
            if let Some(layout_table) = table.get("layout").and_then(|v| v.as_table()) {
                let get_bool = |key: &str, fallback: bool| {
                    layout_table
                        .get(key)
                        .and_then(|v| v.as_bool())
                        .unwrap_or(fallback)
                };
                layout = Layout {
                    show_weather: get_bool("show_weather", true),
                    show_metrics: get_bool("show_metrics", true),
                    show_quick_toggles: get_bool("show_quick_toggles", true),
                    show_power_profiles: get_bool("show_power_profiles", true),
                    show_sliders: get_bool("show_sliders", true),
                    show_notifications: get_bool("show_notifications", true),
                    show_media: get_bool("show_media", false),
                };
            }
            if let Some(list) = table.get("toggles").and_then(|v| v.as_array()) {
                explicit_toggles = true;
                for item in list.iter().filter_map(|v| v.as_table()) {
                    let s = |key: &str| {
                        item.get(key)
                            .and_then(|v| v.as_str())
                            .unwrap_or("")
                            .to_owned()
                    };
                    // Validate like the Python loader: id/icon/label/tooltip/cmds must be strings.
                    toggles.push(Toggle {
                        id: s("id"),
                        icon: if s("icon").is_empty() {
                            "applications-system-symbolic".into()
                        } else {
                            s("icon")
                        },
                        label: s("label"),
                        tooltip: s("tooltip"),
                        on_left: s("on_left"),
                        on_middle: s("on_middle"),
                        on_right: s("on_right"),
                    });
                }
            }
        }
        if toggles.is_empty() && !explicit_toggles {
            // Fall back to the embedded default grid (5 essentials).
            let defaults = Self::parse(Self::default_toml());
            return Self {
                layout,
                toggles: defaults.toggles,
            };
        }
        Self { layout, toggles }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn appearance_defaults_and_edits_are_validated_as_one_snapshot() {
        assert_eq!(Appearance::parse("blur=false").unwrap().opacity, 0.94);
        assert_eq!(
            Appearance::parse("opacity=0.55\nblur=false").unwrap(),
            Appearance {
                opacity: 0.55,
                blur: false
            }
        );
        for invalid in ["opacity=", "opacity=nan", "opacity=-1", "opacity=1.1"] {
            assert!(Appearance::parse(invalid).is_none());
        }
    }
    #[test]
    fn default_toml_parses_to_five_toggles() {
        let cfg = AppConfig::parse(AppConfig::default_toml());
        assert_eq!(cfg.toggles.len(), 5);
        assert_eq!(cfg.toggles[0].id, "wifi");
        assert!(cfg.layout.show_weather);
        assert!(!cfg.layout.show_media);
    }

    #[test]
    fn invalid_toml_falls_back_to_defaults() {
        let cfg = AppConfig::parse("not = [valid");
        assert_eq!(cfg.toggles.len(), 5);
    }

    #[test]
    fn layout_only_and_empty_toggle_configs_preserve_intent() {
        let cfg = AppConfig::parse("[layout]\nshow_sliders=false");
        assert!(!cfg.layout.show_sliders);
        assert_eq!(cfg.toggles.len(), 5);
        let cfg = AppConfig::parse("toggles=[]\n[layout]\nshow_sliders=false");
        assert!(!cfg.layout.show_sliders);
        assert!(cfg.toggles.is_empty());
    }

    #[test]
    fn icon_glyph_maps_known_names() {
        assert_eq!(icon_glyph("network-wireless-symbolic"), "󰖩");
        assert_eq!(icon_glyph("system-shutdown-symbolic"), "⏻︎");
        assert_eq!(icon_glyph("timer-symbolic"), "\u{F04DB}");
        assert_eq!(icon_glyph("view-reveal-symbolic"), "\u{F0209}");
        assert_eq!(icon_glyph("folder-download-symbolic"), "\u{F01DA}");
        assert_eq!(icon_glyph("power-profile-performance-symbolic"), "");
        assert_eq!(icon_glyph("unknown-foo"), "●");
    }
}
