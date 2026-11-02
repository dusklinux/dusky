use crate::hyprland::Client;
use crate::ui::ThemePalette;
use iced::Color;
use iced::widget::svg;
use serde_json::Value;
use std::collections::HashMap;
use std::env;
use std::fs;
use std::path::PathBuf;

const SVG_TERMINAL: &[u8] = br##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="18" rx="3"/><polyline points="7 9 11 12 7 15"/><line x1="13" y1="15" x2="17" y2="15"/></svg>"##;

const SVG_BROWSER: &[u8] = br##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9.5"/><line x1="2.5" y1="12" x2="21.5" y2="12"/><path d="M12 2.5a14.5 14.5 0 0 1 0 19 14.5 14.5 0 0 1 0-19"/></svg>"##;

const SVG_FILES: &[u8] = br##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>"##;

const SVG_CODE: &[u8] = br##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/><line x1="14" y1="4" x2="10" y2="20"/></svg>"##;

const SVG_EDITOR: &[u8] = br##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="13" y2="17"/></svg>"##;

const SVG_MEDIA: &[u8] = br##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 10v4"/><path d="M8 6v12"/><path d="M12 3v18"/><path d="M16 7v10"/><path d="M20 10v4"/></svg>"##;

const SVG_CHAT: &[u8] = br##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>"##;

const SVG_SYSTEM: &[u8] = br##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>"##;

const SVG_DOCUMENT: &[u8] = br##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>"##;

const SVG_WINDOW: &[u8] = br##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2.5"/><line x1="3" y1="9" x2="21" y2="9"/><circle cx="6.5" cy="6.5" r="0.8" fill="#ffffff"/><circle cx="9.5" cy="6.5" r="0.8" fill="#ffffff"/></svg>"##;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum AccentSlot {
    #[default]
    Auto,
    Accent,
    Secondary,
    Tertiary,
    Muted,
}

impl AccentSlot {
    pub fn from_str(s: &str) -> Self {
        match s.trim().to_ascii_lowercase().as_str() {
            "accent" | "primary" => Self::Accent,
            "secondary" => Self::Secondary,
            "tertiary" => Self::Tertiary,
            "muted" => Self::Muted,
            _ => Self::Auto,
        }
    }
}

#[derive(Debug, Clone)]
pub struct IconRule {
    pub icon: String,
    pub label: Option<String>,
    pub accent: AccentSlot,
    pub terminal_only: bool,
    pub class: Vec<String>,
    pub title: Vec<String>,
}

#[derive(Debug, Clone)]
pub struct ResolvedAppIcon {
    pub handle: svg::Handle,
    pub category_key: String,
    pub display_badge: Option<String>,
    pub accent_slot: AccentSlot,
}

impl ResolvedAppIcon {
    pub fn resolve_color(&self, fallback_class_color: Color, palette: &ThemePalette) -> Color {
        match self.accent_slot {
            AccentSlot::Accent => palette.accent,
            AccentSlot::Secondary => palette.secondary,
            AccentSlot::Tertiary => palette.tertiary,
            AccentSlot::Muted => palette.muted,
            AccentSlot::Auto => match self.category_key.as_str() {
                "browser" => palette.accent,
                "files" => palette.tertiary,
                "code" | "editor" => palette.secondary,
                "media" => Color {
                    r: (palette.tertiary.r + palette.accent.r) * 0.5,
                    g: (palette.tertiary.g + palette.accent.g) * 0.5,
                    b: (palette.tertiary.b + palette.accent.b) * 0.5,
                    a: 1.0,
                },
                "system" => Color {
                    r: (palette.secondary.r + palette.tertiary.r) * 0.5,
                    g: (palette.secondary.g + palette.tertiary.g) * 0.5,
                    b: (palette.secondary.b + palette.tertiary.b) * 0.5,
                    a: 1.0,
                },
                _ => fallback_class_color,
            },
        }
    }
}

pub struct IconRegistry {
    handles: HashMap<String, svg::Handle>,
    fallback_handle: svg::Handle,
    terminal_classes: Vec<String>,
    rules: Vec<IconRule>,
}

impl IconRegistry {
    pub fn load() -> Self {
        let mut handles = HashMap::with_capacity(16);
        handles.insert(
            String::from("terminal"),
            svg::Handle::from_memory(SVG_TERMINAL),
        );
        handles.insert(
            String::from("browser"),
            svg::Handle::from_memory(SVG_BROWSER),
        );
        handles.insert(String::from("files"), svg::Handle::from_memory(SVG_FILES));
        handles.insert(String::from("code"), svg::Handle::from_memory(SVG_CODE));
        handles.insert(String::from("editor"), svg::Handle::from_memory(SVG_EDITOR));
        handles.insert(String::from("media"), svg::Handle::from_memory(SVG_MEDIA));
        handles.insert(String::from("chat"), svg::Handle::from_memory(SVG_CHAT));
        handles.insert(String::from("system"), svg::Handle::from_memory(SVG_SYSTEM));
        handles.insert(
            String::from("document"),
            svg::Handle::from_memory(SVG_DOCUMENT),
        );
        let fallback_handle = svg::Handle::from_memory(SVG_WINDOW);
        handles.insert(String::from("window"), fallback_handle.clone());

        let mut terminal_classes = default_terminal_classes();
        let mut rules = Vec::with_capacity(32);

        if let Some(user_cfg) = Self::read_user_json() {
            if let Some(terms) = user_cfg.get("terminal_classes").and_then(Value::as_array) {
                for tc in terms.iter().filter_map(Value::as_str) {
                    let lower = tc.trim().to_ascii_lowercase();
                    if !lower.is_empty() && !terminal_classes.contains(&lower) {
                        terminal_classes.push(lower);
                    }
                }
            }

            if let Some(custom_icons) = user_cfg.get("custom_icons").and_then(Value::as_object) {
                for (name, def) in custom_icons {
                    if let Some(handle) = Self::build_custom_handle(def) {
                        handles.insert(name.trim().to_ascii_lowercase(), handle);
                    }
                }
            }

            if let Some(user_rules) = user_cfg.get("rules").and_then(Value::as_array) {
                for rule_val in user_rules {
                    if let Some(parsed) = Self::parse_rule(rule_val) {
                        rules.push(parsed);
                    }
                }
            }
        }

        let mut defaults = builtin_rules();
        rules.append(&mut defaults);
        // Ensure terminal_only TUI rules are checked before generic terminal rules
        rules.sort_by_key(|r| !r.terminal_only);

        Self {
            handles,
            fallback_handle,
            terminal_classes,
            rules,
        }
    }

    pub fn resolve_client(&self, client: &Client) -> ResolvedAppIcon {
        self.resolve(&client.class, &client.initial_class, &client.title)
    }

    pub fn resolve(&self, class: &str, initial_class: &str, title: &str) -> ResolvedAppIcon {
        let c_low = class.trim().to_ascii_lowercase();
        let ic_low = initial_class.trim().to_ascii_lowercase();
        let t_low = title.trim().to_ascii_lowercase();

        let is_term = self.terminal_classes.iter().any(|tc| {
            class_pattern_matches(tc, &c_low)
                || (!ic_low.is_empty() && class_pattern_matches(tc, &ic_low))
        });

        for rule in &self.rules {
            if rule.terminal_only && !is_term {
                continue;
            }
            if rule.class.is_empty() && rule.title.is_empty() && !rule.terminal_only {
                continue;
            }

            let class_ok = rule.class.is_empty()
                || rule.class.iter().any(|pat| {
                    class_pattern_matches(pat, &c_low)
                        || (!ic_low.is_empty() && class_pattern_matches(pat, &ic_low))
                });

            let title_ok = rule.title.is_empty()
                || rule
                    .title
                    .iter()
                    .any(|pat| title_pattern_matches(pat, &t_low));

            if class_ok && title_ok {
                let handle = self
                    .handles
                    .get(&rule.icon)
                    .cloned()
                    .unwrap_or_else(|| self.fallback_handle.clone());
                return ResolvedAppIcon {
                    handle,
                    category_key: rule.icon.clone(),
                    display_badge: rule.label.clone(),
                    accent_slot: rule.accent,
                };
            }
        }

        ResolvedAppIcon {
            handle: self.fallback_handle.clone(),
            category_key: String::from("window"),
            display_badge: None,
            accent_slot: AccentSlot::Auto,
        }
    }

    fn read_user_json() -> Option<Value> {
        let mut candidates = Vec::with_capacity(6);
        if let Some(custom_path) = env::var_os("DUSKY_ICON_CONFIG") {
            candidates.push(PathBuf::from(custom_path));
        }
        if let Some(home) = env::var_os("HOME") {
            let home_p = PathBuf::from(home);
            let custom_dir = home_p.join("user_scripts/dusky_system/dusky_overview/custom_icons");
            candidates.push(custom_dir.join("app_icons.json"));
            if let Ok(entries) = fs::read_dir(&custom_dir) {
                for entry in entries.flatten() {
                    let p = entry.path();
                    if p.extension().is_some_and(|ext| ext == "json")
                        && p != custom_dir.join("app_icons.json")
                    {
                        candidates.push(p);
                    }
                }
            }
            candidates.push(home_p.join(".config/dusky/app_icons.json"));
        }
        if let Some(xdg) = env::var_os("XDG_CONFIG_HOME") {
            candidates.push(PathBuf::from(xdg).join("dusky/app_icons.json"));
        }

        for path in candidates {
            let Ok(raw) = fs::read_to_string(&path) else {
                continue;
            };
            if let Ok(parsed) = serde_json::from_str::<Value>(&raw) {
                return Some(parsed);
            }
        }
        None
    }

    fn parse_rule(v: &Value) -> Option<IconRule> {
        let icon = v
            .get("icon")
            .and_then(Value::as_str)?
            .trim()
            .to_ascii_lowercase();
        if icon.is_empty() {
            return None;
        }

        let label = v
            .get("label")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(String::from);

        let accent = v
            .get("accent")
            .and_then(Value::as_str)
            .map(AccentSlot::from_str)
            .unwrap_or_default();

        let terminal_only = v
            .get("terminal_only")
            .and_then(Value::as_bool)
            .unwrap_or(false);

        let class = v
            .get("class")
            .and_then(Value::as_array)
            .map(|arr| {
                arr.iter()
                    .filter_map(Value::as_str)
                    .map(|s| s.trim().to_ascii_lowercase())
                    .filter(|s| !s.is_empty())
                    .collect()
            })
            .unwrap_or_default();

        let title = v
            .get("title")
            .and_then(Value::as_array)
            .map(|arr| {
                arr.iter()
                    .filter_map(Value::as_str)
                    .map(|s| s.trim().to_ascii_lowercase())
                    .filter(|s| !s.is_empty())
                    .collect()
            })
            .unwrap_or_default();

        Some(IconRule {
            icon,
            label,
            accent,
            terminal_only,
            class,
            title,
        })
    }

    fn build_custom_handle(def: &Value) -> Option<svg::Handle> {
        if let Some(file_str) = def.get("svg_file").and_then(Value::as_str) {
            let expanded = expand_tilde(file_str.trim());
            if let Ok(bytes) = fs::read(&expanded) {
                return Some(svg::Handle::from_memory(bytes));
            }
            if let Some(home) = env::var_os("HOME") {
                let custom_dir_svg = PathBuf::from(home)
                    .join("user_scripts/dusky_system/dusky_overview/custom_icons")
                    .join(file_str.trim());
                if let Ok(bytes) = fs::read(custom_dir_svg) {
                    return Some(svg::Handle::from_memory(bytes));
                }
            }
        }
        if let Some(raw_svg) = def.get("svg_data").and_then(Value::as_str) {
            return Some(svg::Handle::from_memory(raw_svg.as_bytes().to_vec()));
        }
        if let Some(path_d) = def.get("svg_path").and_then(Value::as_str) {
            let wrapped = format!(
                r##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><path d="{path_d}"/></svg>"##
            );
            return Some(svg::Handle::from_memory(wrapped.into_bytes()));
        }
        None
    }
}

fn expand_tilde(input: &str) -> PathBuf {
    if let Some(stripped) = input.strip_prefix("~/")
        && let Some(home) = env::var_os("HOME")
    {
        return PathBuf::from(home).join(stripped);
    }
    PathBuf::from(input)
}

#[inline]
fn class_pattern_matches(pattern: &str, candidate: &str) -> bool {
    if candidate.is_empty() || pattern.is_empty() {
        return false;
    }
    let last_rdns = candidate.rsplit('.').next().unwrap_or(candidate);
    if let Some(prefix) = pattern.strip_suffix('*') {
        return candidate.starts_with(prefix) || last_rdns.starts_with(prefix);
    }
    if candidate == pattern || last_rdns == pattern {
        return true;
    }
    if pattern.len() >= 4 {
        if let Some(first_seg) = last_rdns.split(['-', '_']).next()
            && first_seg == pattern
        {
            return true;
        }
    }
    false
}

#[inline]
fn title_pattern_matches(pattern: &str, title_lower: &str) -> bool {
    if title_lower.is_empty() || pattern.is_empty() {
        return false;
    }
    if let Some(prefix) = pattern.strip_prefix('^') {
        return title_lower.starts_with(prefix);
    }
    // Token boundary matching for identifier-like patterns to prevent substring false positives
    if pattern
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
    {
        title_lower
            .split(|c: char| !c.is_ascii_alphanumeric() && c != '-' && c != '_')
            .any(|tok| tok == pattern)
    } else {
        title_lower.contains(pattern)
    }
}

fn default_terminal_classes() -> Vec<String> {
    [
        "kitty",
        "foot",
        "footclient",
        "alacritty",
        "wezterm",
        "org.wezfurlong.wezterm",
        "ghostty",
        "com.mitchellh.ghostty",
        "konsole",
        "org.kde.konsole",
        "xterm",
        "urxvt",
        "st",
        "rio",
        "contour",
        "warp",
        "blackbox",
    ]
    .into_iter()
    .map(String::from)
    .collect()
}

fn builtin_rules() -> Vec<IconRule> {
    let rule = |icon: &str,
                label: Option<&str>,
                accent: AccentSlot,
                terminal_only: bool,
                class: &[&str],
                title: &[&str]| IconRule {
        icon: String::from(icon),
        label: label.map(String::from),
        accent,
        terminal_only,
        class: class.iter().map(|s| (*s).to_owned()).collect(),
        title: title.iter().map(|s| (*s).to_owned()).collect(),
    };

    vec![
        // 1. Terminal-hosted TUI applications (inspected via title inside terminals)
        rule(
            "files",
            Some("yazi"),
            AccentSlot::Tertiary,
            true,
            &[],
            &["yazi", "ranger", "nnn", "lf", "broot", "mc"],
        ),
        rule(
            "code",
            Some("nvim"),
            AccentSlot::Secondary,
            true,
            &[],
            &["nvim", "neovim", "vim", "helix", "hx", "kakoune", "emacs"],
        ),
        rule(
            "editor",
            Some("edit"),
            AccentSlot::Secondary,
            true,
            &[],
            &["nano", "micro"],
        ),
        rule(
            "media",
            Some("media"),
            AccentSlot::Tertiary,
            true,
            &[],
            &["cava", "ncmpcpp", "cmus", "rmpc", "mpv", "musikcube"],
        ),
        rule(
            "system",
            Some("sys"),
            AccentSlot::Secondary,
            true,
            &[],
            &["btop", "htop", "nvtop", "btm", "atop", "lazygit", "gitui"],
        ),
        // 2. Web Browsers
        rule(
            "browser",
            None,
            AccentSlot::Accent,
            false,
            &[
                "firefox",
                "firefox-esr",
                "firefox-developer-edition",
                "librewolf",
                "zen",
                "zen-alpha",
                "zen-beta",
                "zen-browser",
                "chromium",
                "google-chrome",
                "brave",
                "brave-browser",
                "vivaldi",
                "qutebrowser",
                "floorp",
                "thorium",
                "waterfox",
                "epiphany",
                "luakit",
            ],
            &[],
        ),
        // 3. GUI & Standalone File Managers
        rule(
            "files",
            None,
            AccentSlot::Tertiary,
            false,
            &[
                "thunar",
                "dolphin",
                "org.kde.dolphin",
                "nautilus",
                "org.gnome.nautilus",
                "nemo",
                "pcmanfm",
                "pcmanfm-qt",
                "krusader",
                "spacefm",
                "doublecmd",
                "yazi",
                "ranger",
            ],
            &[],
        ),
        // 4. Code Editors & IDEs
        rule(
            "code",
            None,
            AccentSlot::Secondary,
            false,
            &[
                "code",
                "code-oss",
                "vscodium",
                "codium",
                "neovide",
                "nvim",
                "helix",
                "zed",
                "dev.zed.zed",
                "cursor",
                "sublime_text",
                "jetbrains-*",
                "rustrover",
                "clion",
                "intellij*",
                "pycharm*",
                "lapce",
            ],
            &[],
        ),
        // 5. Text Editors
        rule(
            "editor",
            None,
            AccentSlot::Secondary,
            false,
            &[
                "kate",
                "org.kde.kate",
                "kwrite",
                "mousepad",
                "gedit",
                "org.gnome.texteditor",
                "gnome-text-editor",
                "featherpad",
                "leafpad",
                "xed",
                "pluma",
            ],
            &[],
        ),
        // 6. Media, Audio & Graphics
        rule(
            "media",
            None,
            AccentSlot::Tertiary,
            false,
            &[
                "mpv",
                "vlc",
                "spotify",
                "amberol",
                "cava",
                "obs",
                "com.obsproject.studio",
                "imv",
                "loupe",
                "org.gnome.loupe",
                "feh",
                "sxiv",
                "nsxiv",
                "gimp",
                "inkscape",
                "kdenlive",
                "pavucontrol",
                "org.pulseaudio.pavucontrol",
                "pwvucontrol",
                "easyeffects",
            ],
            &[],
        ),
        // 7. Chat & Communication
        rule(
            "chat",
            None,
            AccentSlot::Accent,
            false,
            &[
                "discord",
                "vesktop",
                "webcord",
                "legcord",
                "telegram",
                "org.telegram.desktop",
                "signal",
                "slack",
                "element",
                "thunderbird",
                "fractal",
            ],
            &[],
        ),
        // 8. Documents & Knowledge
        rule(
            "document",
            None,
            AccentSlot::Secondary,
            false,
            &[
                "zathura",
                "org.pwmt.zathura",
                "sioyek",
                "okular",
                "evince",
                "obsidian",
                "logseq",
                "libreoffice*",
                "soffice",
                "foliate",
            ],
            &[],
        ),
        // 9. System Utilities & Monitors
        rule(
            "system",
            None,
            AccentSlot::Secondary,
            false,
            &[
                "btop",
                "missioncenter",
                "io.missioncenter.missioncenter",
                "nm-connection-editor",
                "blueman-manager",
                "nwg-look",
                "qt5ct",
                "qt6ct",
                "kvantummanager",
            ],
            &[],
        ),
        // 10. Terminals (Evaluated after terminal_only TUI rules)
        rule(
            "terminal",
            None,
            AccentSlot::Auto,
            false,
            &[
                "kitty",
                "foot",
                "footclient",
                "alacritty",
                "wezterm",
                "org.wezfurlong.wezterm",
                "ghostty",
                "com.mitchellh.ghostty",
                "konsole",
                "org.kde.konsole",
                "xterm",
                "urxvt",
                "st",
                "rio",
                "contour",
                "warp",
                "blackbox",
            ],
            &[],
        ),
    ]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_terminal_tui_detection_and_browser_isolation() {
        let reg = IconRegistry::load();

        // 1. Kitty running Yazi should resolve to "files" (File Manager), NOT generic "terminal".
        let yazi_in_kitty = reg.resolve("kitty", "kitty", "Yazi: ~/dusk/episode");
        assert_eq!(yazi_in_kitty.category_key, "files");
        assert_eq!(yazi_in_kitty.display_badge.as_deref(), Some("yazi"));

        // 2. Kitty running Neovim should resolve to "code".
        let nvim_in_kitty = reg.resolve("kitty", "kitty", "nvim src/ui.rs");
        assert_eq!(nvim_in_kitty.category_key, "code");

        // 3. Kitty running Cava should resolve to "media".
        let cava_in_kitty = reg.resolve("kitty", "kitty", "cava");
        assert_eq!(cava_in_kitty.category_key, "media");

        // 4. Plain Kitty shell should resolve to "terminal".
        let plain_kitty = reg.resolve("kitty", "kitty", "~/web_ai_bridge/signed");
        assert_eq!(plain_kitty.category_key, "terminal");

        // 5. Firefox viewing a page about Yazi or Neovim must remain "browser" (no false TUI match).
        let firefox_yazi = reg.resolve(
            "firefox",
            "Navigator",
            "sxyazi/yazi: Blazing fast terminal file manager - Mozilla Firefox",
        );
        assert_eq!(firefox_yazi.category_key, "browser");

        // 6. Short class names should not false-positive substring match.
        let steam = reg.resolve("steam", "", "Steam");
        assert_ne!(steam.category_key, "terminal"); // "st" should not match "steam"

        // 7. Unrecognized app falls back to "window".
        let unknown = reg.resolve("my_custom_unknown_app", "", "Untitled");
        assert_eq!(unknown.category_key, "window");
    }
}
