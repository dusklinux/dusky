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

const SVG_NOTES: &[u8] = br##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2L4 7v10l8 5 8-5V7l-8-5zm0 2.3l5.5 3.4L12 11.1 6.5 7.7 12 4.3zm-6 5.2l5 3.1v6.3l-5-3.1V9.5zm7 9.4v-6.3l5-3.1v6.3l-5 3.1z"/></svg>"##;

const SVG_WINDOW: &[u8] = br##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2.5"/><line x1="3" y1="9" x2="21" y2="9"/><circle cx="6.5" cy="6.5" r="0.8" fill="#ffffff"/><circle cx="9.5" cy="6.5" r="0.8" fill="#ffffff"/></svg>"##;

#[cfg(not(test))]
const EMOJI_FONT_CANDIDATES: &[&str] = &[
    "/usr/share/fonts/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/TTF/Twemoji.ttf",
    "/usr/share/fonts/twemoji/TwemojiMozilla.ttf",
];

#[cfg(not(test))]
const NERD_FONT_CANDIDATES: &[&str] = &[
    "/usr/share/fonts/TTF/SymbolsNerdFontMono-Regular.ttf",
    "/usr/share/fonts/TTF/SymbolsNerdFont-Regular.ttf",
    "/usr/share/fonts/TTF/JetBrainsMonoNerdFont-Regular.ttf",
    "/usr/share/fonts/TTF/JetBrainsMonoNerdFontMono-Regular.ttf",
];

#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub enum AccentSlot {
    #[default]
    Auto,
    Accent,
    Secondary,
    Tertiary,
    Muted,
    Custom(Color),
}

impl AccentSlot {
    pub fn from_str(s: &str) -> Self {
        let lower = s.trim().to_ascii_lowercase();
        match lower.as_str() {
            "" | "auto" | "default" => Self::Auto,
            "accent" | "primary" => Self::Accent,
            "secondary" => Self::Secondary,
            "tertiary" | "special" => Self::Tertiary,
            "muted" | "outline" => Self::Muted,
            _ => parse_custom_color(&lower).map_or(Self::Auto, Self::Custom),
        }
    }

    #[inline]
    pub fn is_custom(self) -> bool {
        matches!(self, Self::Custom(_))
    }
}

pub fn parse_custom_color(input: &str) -> Option<Color> {
    let trimmed = input.trim().to_ascii_lowercase();
    if trimmed.is_empty() {
        return None;
    }

    // 1. Check `rgb(r, g, b)` or `rgba(r, g, b, a)` functional syntax
    if let Some(inner) = trimmed
        .strip_prefix("rgba(")
        .or_else(|| trimmed.strip_prefix("rgb("))
        .and_then(|s| s.strip_suffix(')'))
    {
        let parts: Vec<&str> = inner.split(',').map(str::trim).collect();
        if parts.len() == 3 || parts.len() == 4 {
            let r = parts[0].parse::<u8>().ok()?;
            let g = parts[1].parse::<u8>().ok()?;
            let b = parts[2].parse::<u8>().ok()?;
            let a = if parts.len() == 4 {
                let raw_a = parts[3].parse::<f32>().ok()?;
                if raw_a > 1.0 {
                    (raw_a / 255.0).clamp(0.0, 1.0)
                } else {
                    raw_a.clamp(0.0, 1.0)
                }
            } else {
                1.0
            };
            return Some(Color::from_rgba8(r, g, b, a));
        }
    }

    // 2. Named colors (normalized so "light-purple", "light_purple", and "lightpurple" all match)
    let normalized: String = trimmed
        .chars()
        .filter(|c| !matches!(c, ' ' | '-' | '_'))
        .collect();

    let named = match normalized.as_str() {
        "purple" | "amethyst" => Some((0xa8, 0x55, 0xf7)),
        "lightpurple" | "lavender" => Some((0xc0, 0x84, 0xfc)),
        "darkpurple" | "deeppurple" => Some((0x7e, 0x22, 0xce)),
        "mauve" | "catppuccinmauve" => Some((0xcb, 0xa6, 0xf7)),
        "violet" => Some((0x8b, 0x5c, 0xf6)),
        "indigo" => Some((0x63, 0x66, 0xf1)),
        "blue" | "azure" => Some((0x3b, 0x82, 0xf6)),
        "lightblue" | "sky" | "skyblue" => Some((0x38, 0xbd, 0xf8)),
        "sapphire" => Some((0x74, 0xc7, 0xec)),
        "cyan" | "aqua" => Some((0x06, 0xb6, 0xd4)),
        "teal" | "turquoise" => Some((0x14, 0xb8, 0xa6)),
        "mint" | "emerald" => Some((0x10, 0xb9, 0x81)),
        "green" => Some((0x22, 0xc5, 0x5e)),
        "lightgreen" => Some((0x4a, 0xde, 0x80)),
        "lime" | "chartreuse" => Some((0x84, 0xcc, 0x16)),
        "olive" => Some((0x84, 0xcc, 0x16)),
        "yellow" => Some((0xea, 0xb3, 0x08)),
        "amber" | "gold" => Some((0xf5, 0x9e, 0x0b)),
        "peach" | "apricot" => Some((0xfa, 0xb3, 0x87)),
        "orange" | "tangerine" => Some((0xf9, 0x73, 0x16)),
        "coral" | "salmon" => Some((0xfb, 0x71, 0x85)),
        "red" | "crimson" => Some((0xef, 0x44, 0x44)),
        "maroon" | "burgundy" => Some((0xbe, 0x12, 0x3c)),
        "rose" | "rosewater" | "flamingo" => Some((0xf4, 0x3f, 0x5e)),
        "pink" | "hotpink" => Some((0xec, 0x48, 0x99)),
        "magenta" | "fuchsia" => Some((0xd9, 0x46, 0xef)),
        "white" | "snow" => Some((0xf8, 0xfa, 0xfc)),
        "silver" | "lightgray" | "lightgrey" => Some((0xcb, 0xd5, 0xe1)),
        "gray" | "grey" | "slate" => Some((0x94, 0xa3, 0xb8)),
        _ => None,
    };
    if let Some((r, g, b)) = named {
        return Some(Color::from_rgb8(r, g, b));
    }

    // 3. Hex colors: #RGB, #RGBA, #RRGGBB, #RRGGBBAA (with or without leading '#')
    let clean = trimmed.strip_prefix('#').unwrap_or(&trimmed);
    match clean.len() {
        3 => {
            let r = u8::from_str_radix(&clean[0..1], 16).ok()? * 17;
            let g = u8::from_str_radix(&clean[1..2], 16).ok()? * 17;
            let b = u8::from_str_radix(&clean[2..3], 16).ok()? * 17;
            Some(Color::from_rgb8(r, g, b))
        }
        4 => {
            let r = u8::from_str_radix(&clean[0..1], 16).ok()? * 17;
            let g = u8::from_str_radix(&clean[1..2], 16).ok()? * 17;
            let b = u8::from_str_radix(&clean[2..3], 16).ok()? * 17;
            let a = u8::from_str_radix(&clean[3..4], 16).ok()? * 17;
            Some(Color::from_rgba8(r, g, b, f32::from(a) / 255.0))
        }
        6 => {
            let r = u8::from_str_radix(&clean[0..2], 16).ok()?;
            let g = u8::from_str_radix(&clean[2..4], 16).ok()?;
            let b = u8::from_str_radix(&clean[4..6], 16).ok()?;
            Some(Color::from_rgb8(r, g, b))
        }
        8 => {
            let r = u8::from_str_radix(&clean[0..2], 16).ok()?;
            let g = u8::from_str_radix(&clean[2..4], 16).ok()?;
            let b = u8::from_str_radix(&clean[4..6], 16).ok()?;
            let a = u8::from_str_radix(&clean[6..8], 16).ok()?;
            Some(Color::from_rgba8(r, g, b, f32::from(a) / 255.0))
        }
        _ => None,
    }
}

#[derive(Debug, Clone)]
pub enum IconVisual {
    Svg { handle: svg::Handle, tint: bool },
    Glyph(String),
}

#[derive(Debug, Clone)]
pub struct IconRule {
    pub icon: Option<String>,
    pub inline_visual: Option<IconVisual>,
    pub label: Option<String>,
    pub accent: Option<AccentSlot>,
    pub terminal_only: bool,
    pub class: Vec<String>,
    pub title: Vec<String>,
}

#[derive(Debug, Clone)]
pub struct ResolvedAppIcon {
    pub visual: IconVisual,
    pub category_key: String,
    pub display_badge: Option<String>,
    pub accent_slot: AccentSlot,
}

impl ResolvedAppIcon {
    pub fn resolve_color(&self, palette: &ThemePalette) -> Color {
        match self.accent_slot {
            AccentSlot::Custom(color) => color,
            AccentSlot::Accent => palette.accent,
            AccentSlot::Secondary => palette.secondary,
            AccentSlot::Tertiary => palette.tertiary,
            AccentSlot::Muted => palette.muted,
            AccentSlot::Auto => match self.category_key.as_str() {
                "browser" | "terminal" => palette.accent,
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
                _ => palette.secondary,
            },
        }
    }
}

pub struct IconRegistry {
    visuals: HashMap<String, IconVisual>,
    category_accents: HashMap<String, AccentSlot>,
    fallback_visual: IconVisual,
    terminal_classes: Vec<String>,
    rules: Vec<IconRule>,
}

impl IconRegistry {
    pub fn load() -> Self {
        let mut visuals = HashMap::with_capacity(16);
        let mut category_accents = HashMap::with_capacity(8);

        let builtin_svg = |bytes: &'static [u8]| IconVisual::Svg {
            handle: svg::Handle::from_memory(bytes),
            tint: true,
        };

        visuals.insert(String::from("terminal"), builtin_svg(SVG_TERMINAL));
        visuals.insert(String::from("browser"), builtin_svg(SVG_BROWSER));
        visuals.insert(String::from("files"), builtin_svg(SVG_FILES));
        visuals.insert(String::from("code"), builtin_svg(SVG_CODE));
        visuals.insert(String::from("editor"), builtin_svg(SVG_EDITOR));
        visuals.insert(String::from("media"), builtin_svg(SVG_MEDIA));
        visuals.insert(String::from("chat"), builtin_svg(SVG_CHAT));
        visuals.insert(String::from("system"), builtin_svg(SVG_SYSTEM));
        visuals.insert(String::from("document"), builtin_svg(SVG_DOCUMENT));
        visuals.insert(String::from("notes"), builtin_svg(SVG_NOTES));
        let fallback_visual = builtin_svg(SVG_WINDOW);
        visuals.insert(String::from("window"), fallback_visual.clone());

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
                    let key = name.trim().to_ascii_lowercase();
                    if key.is_empty() {
                        continue;
                    }
                    if let Some(visual) = Self::build_custom_visual(def) {
                        visuals.insert(key.clone(), visual);
                    }
                    if let Some(slot) = Self::extract_accent_slot(def)
                        && slot != AccentSlot::Auto
                    {
                        category_accents.insert(key, slot);
                    }
                }
            }

            // Support top-level `"colors"` or `"category_colors"` map for quick category-wide overrides
            for map_key in ["colors", "category_colors"] {
                if let Some(cat_colors) = user_cfg.get(map_key).and_then(Value::as_object) {
                    for (name, val) in cat_colors {
                        let key = name.trim().to_ascii_lowercase();
                        if let Some(color_str) = val.as_str() {
                            let slot = AccentSlot::from_str(color_str);
                            if slot != AccentSlot::Auto {
                                category_accents.insert(key, slot);
                            }
                        }
                    }
                }
            }

            if let Some(tui_map) = user_cfg.get("tui").and_then(Value::as_object) {
                for (titles_key, spec_val) in tui_map {
                    if let Some(parsed) = Self::parse_compact_entry(titles_key, spec_val, true) {
                        rules.push(parsed);
                    }
                }
            }

            if let Some(apps_map) = user_cfg.get("apps").and_then(Value::as_object) {
                for (classes_key, spec_val) in apps_map {
                    if let Some(parsed) = Self::parse_compact_entry(classes_key, spec_val, false) {
                        rules.push(parsed);
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

        let registry = Self {
            visuals,
            category_accents,
            fallback_visual,
            terminal_classes,
            rules,
        };

        #[cfg(not(test))]
        registry.load_supplementary_glyph_fonts_if_needed();

        registry
    }

    #[cfg(not(test))]
    fn load_supplementary_glyph_fonts_if_needed(&self) {
        use std::sync::atomic::{AtomicBool, Ordering};
        static EMOJI_LOADED: AtomicBool = AtomicBool::new(false);
        static NERD_LOADED: AtomicBool = AtomicBool::new(false);

        let mut needs_emoji = false;
        let mut needs_nerd = false;

        let inspect_glyph = |s: &str, emoji: &mut bool, nerd: &mut bool| {
            for ch in s.chars() {
                let cp = ch as u32;
                if matches!(cp, 0xE000..=0xF8FF | 0xF0000..=0x10FFFF) {
                    *nerd = true;
                } else if cp >= 0x1F000 {
                    *emoji = true;
                }
            }
        };

        for vis in self.visuals.values() {
            if let IconVisual::Glyph(s) = vis {
                inspect_glyph(s, &mut needs_emoji, &mut needs_nerd);
            }
        }
        for rule in &self.rules {
            if let Some(IconVisual::Glyph(ref s)) = rule.inline_visual {
                inspect_glyph(s, &mut needs_emoji, &mut needs_nerd);
            }
        }

        let load_emoji = needs_emoji && !EMOJI_LOADED.swap(true, Ordering::Relaxed);
        let load_nerd = needs_nerd && !NERD_LOADED.swap(true, Ordering::Relaxed);
        if !load_emoji && !load_nerd {
            return;
        }

        if let Ok(mut font_sys) = iced_graphics::text::font_system().write() {
            let db = font_sys.raw().db_mut();
            if load_emoji {
                for &candidate in EMOJI_FONT_CANDIDATES {
                    let path = std::path::Path::new(candidate);
                    if path.is_file() && db.load_font_file(path).is_ok() {
                        break;
                    }
                }
            }
            if load_nerd {
                for &candidate in NERD_FONT_CANDIDATES {
                    let path = std::path::Path::new(candidate);
                    if path.is_file() && db.load_font_file(path).is_ok() {
                        break;
                    }
                }
            }
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

        let mut override_accent: Option<AccentSlot> = None;
        let mut override_label: Option<String> = None;

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

            if !class_ok || !title_ok {
                continue;
            }

            if override_accent.is_none() {
                override_accent = rule.accent;
            }
            if override_label.is_none() {
                override_label.clone_from(&rule.label);
            }

            // If this rule only overrides color/label without specifying an icon or inline visual,
            // keep scanning to inherit the underlying icon category from a subsequent rule.
            if rule.inline_visual.is_none() && rule.icon.is_none() {
                continue;
            }

            let category_key = rule
                .icon
                .clone()
                .unwrap_or_else(|| String::from("custom"));

            let visual = rule
                .inline_visual
                .clone()
                .or_else(|| self.visuals.get(&category_key).cloned())
                .unwrap_or_else(|| self.fallback_visual.clone());

            let rule_slot = match override_accent {
                Some(slot) if slot != AccentSlot::Auto => Some(slot),
                _ => rule.accent.or(override_accent),
            };
            let accent_slot = match rule_slot {
                Some(slot) if slot != AccentSlot::Auto => slot,
                _ if rule.terminal_only => self
                    .category_accents
                    .get("tui")
                    .or_else(|| self.category_accents.get("terminal"))
                    .copied()
                    .unwrap_or(AccentSlot::Accent),
                _ => self
                    .category_accents
                    .get(&category_key)
                    .copied()
                    .or(rule_slot)
                    .unwrap_or(AccentSlot::Auto),
            };

            return ResolvedAppIcon {
                visual,
                category_key,
                display_badge: override_label.or_else(|| rule.label.clone()),
                accent_slot,
            };
        }

        let fallback_accent = override_accent
            .or_else(|| self.category_accents.get("window").copied())
            .unwrap_or(AccentSlot::Auto);

        ResolvedAppIcon {
            visual: self.fallback_visual.clone(),
            category_key: String::from("window"),
            display_badge: override_label,
            accent_slot: fallback_accent,
        }
    }

    fn read_user_json() -> Option<Value> {
        let parse_file = |path: &std::path::Path| -> Option<Value> {
            let raw = fs::read_to_string(path).ok()?;
            let cleaned = strip_jsonc(&raw);
            match serde_json::from_str::<Value>(&cleaned) {
                Ok(val) => Some(val),
                Err(err) => {
                    eprintln!(
                        "dusky_overview: ignoring invalid JSON in {}: {err}",
                        path.display()
                    );
                    None
                }
            }
        };

        if let Some(custom_path) = env::var_os("DUSKY_ICON_CONFIG") {
            let path = PathBuf::from(custom_path);
            if let Some(parsed) = parse_file(&path) {
                return Some(parsed);
            }
        }

        if let Some(home) = env::var_os("HOME") {
            let home_p = PathBuf::from(home);
            let primary = home_p.join("user_scripts/dusky/dusky_overview/custom_icons/app_icons.json");
            if let Some(parsed) = parse_file(&primary) {
                return Some(parsed);
            }

            if let Some(parsed) = parse_file(&home_p.join(".config/dusky/app_icons.json")) {
                return Some(parsed);
            }
        }

        if let Some(xdg) = env::var_os("XDG_CONFIG_HOME") {
            let xdg_path = PathBuf::from(xdg).join("dusky/app_icons.json");
            if let Some(parsed) = parse_file(&xdg_path) {
                return Some(parsed);
            }
        }

        None
    }

    fn extract_accent_slot(v: &Value) -> Option<AccentSlot> {
        v.get("color")
            .or_else(|| v.get("accent"))
            .and_then(Value::as_str)
            .map(AccentSlot::from_str)
    }

    fn parse_rule(v: &Value) -> Option<IconRule> {
        let inline_visual = Self::build_custom_visual(v);
        let icon = v
            .get("icon")
            .and_then(Value::as_str)
            .map(|s| s.trim().to_ascii_lowercase())
            .filter(|s| !s.is_empty() && s != "auto");

        let label = v
            .get("label")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(String::from);

        let accent = Self::extract_accent_slot(v);

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
                    .collect::<Vec<_>>()
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
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();

        if icon.is_none()
            && inline_visual.is_none()
            && accent.is_none()
            && label.is_none()
        {
            return None;
        }

        Some(IconRule {
            icon,
            inline_visual,
            label,
            accent,
            terminal_only,
            class,
            title,
        })
    }

    fn build_custom_visual(def: &Value) -> Option<IconVisual> {
        // Allow shorthand `"icon_name": "✦"` or `"icon_name": "🦊"` in `custom_icons`
        if let Some(shorthand_glyph) = def.as_str() {
            let trimmed = shorthand_glyph.trim();
            if !trimmed.is_empty() {
                return Some(IconVisual::Glyph(trimmed.to_owned()));
            }
        }

        let tint = def
            .get("tint")
            .or_else(|| def.get("tint_svg"))
            .and_then(Value::as_bool)
            .unwrap_or(true);

        if let Some(file_str) = def.get("svg_file").and_then(Value::as_str) {
            let expanded = expand_tilde(file_str.trim());
            if let Ok(bytes) = fs::read(&expanded) {
                return Some(IconVisual::Svg {
                    handle: svg::Handle::from_memory(bytes),
                    tint,
                });
            }
            if let Some(home) = env::var_os("HOME") {
                let custom_dir_svg = PathBuf::from(home)
                    .join("user_scripts/dusky/dusky_overview/custom_icons")
                    .join(file_str.trim());
                if let Ok(bytes) = fs::read(custom_dir_svg) {
                    return Some(IconVisual::Svg {
                        handle: svg::Handle::from_memory(bytes),
                        tint,
                    });
                }
            }
        }
        if let Some(raw_svg) = def.get("svg_data").and_then(Value::as_str) {
            return Some(IconVisual::Svg {
                handle: svg::Handle::from_memory(raw_svg.as_bytes().to_vec()),
                tint,
            });
        }
        if let Some(path_d) = def.get("svg_path").and_then(Value::as_str) {
            let wrapped = format!(
                r##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><path d="{path_d}"/></svg>"##
            );
            return Some(IconVisual::Svg {
                handle: svg::Handle::from_memory(wrapped.into_bytes()),
                tint: true,
            });
        }
        if let Some(glyph_str) = def
            .get("glyph")
            .or_else(|| def.get("emoji"))
            .or_else(|| def.get("symbol"))
            .or_else(|| def.get("text"))
            .and_then(Value::as_str)
        {
            let trimmed = glyph_str.trim();
            if !trimmed.is_empty() {
                return Some(IconVisual::Glyph(trimmed.to_owned()));
            }
        }
        None
    }
    fn parse_compact_entry(key: &str, val: &Value, terminal_only: bool) -> Option<IconRule> {
        let patterns: Vec<String> = key
            .split([',', '|'])
            .map(|s| s.trim().to_ascii_lowercase())
            .filter(|s| !s.is_empty())
            .collect();
        if patterns.is_empty() {
            return None;
        }

        let (class, title) = if terminal_only {
            (Vec::new(), patterns)
        } else {
            (patterns, Vec::new())
        };

        // 1. Object value: `{ "color": "purple", "glyph": "✦", "icon": "browser" }`
        if val.is_object() {
            let mut rule = Self::parse_rule(val)?;
            rule.terminal_only = terminal_only;
            if rule.class.is_empty() && !class.is_empty() {
                rule.class = class;
            }
            if rule.title.is_empty() && !title.is_empty() {
                rule.title = title;
            }
            return Some(rule);
        }

        // 2. Compact string value:
        //    - `"auto"` / `"purple"` / `"#a855f7"` / `"secondary"` (color-only override)
        //    - `"browser:purple"` / `"gamepad:tertiary"` (icon + color)
        //    - `"files:tertiary:yazi"` (icon + color + badge label)
        //    - `"✦:purple"` or `"🦊:orange"` (inline glyph/emoji + color)
        let raw = val.as_str()?.trim();
        if raw.is_empty() {
            return None;
        }

        let parts: Vec<&str> = raw.splitn(3, ':').map(str::trim).collect();
        let mut icon: Option<String> = None;
        let mut inline_visual: Option<IconVisual> = None;
        let mut accent: Option<AccentSlot> = None;
        let mut label: Option<String> = None;

        let is_known_accent = |s: &str| -> bool {
            let low = s.trim().to_ascii_lowercase();
            matches!(
                low.as_str(),
                "auto" | "default" | "accent" | "primary" | "secondary" | "tertiary" | "special" | "muted" | "outline"
            ) || parse_custom_color(&low).is_some()
        };

        let parse_visual_or_icon =
            |token: &str, out_icon: &mut Option<String>, out_vis: &mut Option<IconVisual>| {
                let t = token.trim();
                if t.is_empty() || t.eq_ignore_ascii_case("auto") {
                    return;
                }
                if let Some(g) = t.strip_prefix("glyph:").or_else(|| t.strip_prefix("emoji:")) {
                    let g = g.trim();
                    if !g.is_empty() {
                        *out_vis = Some(IconVisual::Glyph(g.to_owned()));
                    }
                } else if !t.is_ascii() || t.chars().count() == 1 {
                    *out_vis = Some(IconVisual::Glyph(t.to_owned()));
                } else {
                    *out_icon = Some(t.to_ascii_lowercase());
                }
            };

        match parts.as_slice() {
            [single] => {
                if is_known_accent(single) {
                    accent = Some(AccentSlot::from_str(single));
                } else {
                    parse_visual_or_icon(single, &mut icon, &mut inline_visual);
                }
            }
            [first, second] => {
                if is_known_accent(first) && !is_known_accent(second) {
                    accent = Some(AccentSlot::from_str(first));
                    if !second.is_empty() {
                        label = Some((*second).to_owned());
                    }
                } else {
                    parse_visual_or_icon(first, &mut icon, &mut inline_visual);
                    if !second.is_empty() {
                        accent = Some(AccentSlot::from_str(second));
                    }
                }
            }
            [first, second, third] => {
                parse_visual_or_icon(first, &mut icon, &mut inline_visual);
                if !second.is_empty() {
                    accent = Some(AccentSlot::from_str(second));
                }
                if !third.is_empty() {
                    label = Some((*third).to_owned());
                }
            }
            _ => return None,
        }

        if icon.is_none() && inline_visual.is_none() && accent.is_none() && label.is_none() {
            return None;
        }

        Some(IconRule {
            icon,
            inline_visual,
            label,
            accent,
            terminal_only,
            class,
            title,
        })
    }
}

/// Strips `//`, `#`, and `/* ... */` comments and trailing commas outside string literals
/// so `app_icons.json` can be edited comfortably by hand.
fn strip_jsonc(input: &str) -> String {
    let bytes = input.as_bytes();
    let len = bytes.len();
    let mut no_comments = Vec::with_capacity(len);
    let mut i = 0;
    let mut in_string = false;

    while i < len {
        let b = bytes[i];
        if in_string {
            no_comments.push(b);
            if b == b'\\' && i + 1 < len {
                no_comments.push(bytes[i + 1]);
                i += 2;
                continue;
            }
            if b == b'"' {
                in_string = false;
            }
            i += 1;
        } else if b == b'"' {
            in_string = true;
            no_comments.push(b);
            i += 1;
        } else if b == b'/' && i + 1 < len && bytes[i + 1] == b'/' {
            i += 2;
            while i < len && bytes[i] != b'\n' {
                i += 1;
            }
        } else if b == b'#' {
            i += 1;
            while i < len && bytes[i] != b'\n' {
                i += 1;
            }
        } else if b == b'/' && i + 1 < len && bytes[i + 1] == b'*' {
            i += 2;
            while i + 1 < len && !(bytes[i] == b'*' && bytes[i + 1] == b'/') {
                i += 1;
            }
            i = (i + 2).min(len);
        } else {
            no_comments.push(b);
            i += 1;
        }
    }

    // Second pass: remove trailing commas before `}` or `]` outside strings
    let mut out = Vec::with_capacity(no_comments.len());
    let nc_len = no_comments.len();
    let mut j = 0;
    in_string = false;

    while j < nc_len {
        let b = no_comments[j];
        if in_string {
            out.push(b);
            if b == b'\\' && j + 1 < nc_len {
                out.push(no_comments[j + 1]);
                j += 2;
                continue;
            }
            if b == b'"' {
                in_string = false;
            }
            j += 1;
        } else if b == b'"' {
            in_string = true;
            out.push(b);
            j += 1;
        } else if b == b',' {
            let mut k = j + 1;
            while k < nc_len && no_comments[k].is_ascii_whitespace() {
                k += 1;
            }
            if k < nc_len && (no_comments[k] == b'}' || no_comments[k] == b']') {
                j += 1;
                continue;
            }
            out.push(b);
            j += 1;
        } else {
            out.push(b);
            j += 1;
        }
    }

    String::from_utf8(out).unwrap_or_else(|_| input.to_owned())
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
    if pattern.len() >= 4
        && let Some(first_seg) = last_rdns.split(['-', '_']).next()
        && first_seg == pattern
    {
        return true;
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
        "gnome-terminal",
        "org.gnome.terminal",
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
        icon: Some(String::from(icon)),
        inline_visual: None,
        label: label.map(String::from),
        accent: Some(accent),
        terminal_only,
        class: class.iter().map(|s| (*s).to_owned()).collect(),
        title: title.iter().map(|s| (*s).to_owned()).collect(),
    };

    vec![
        // 1. Terminal-hosted TUI applications (inspected via title inside terminals).
        //    Default to AccentSlot::Auto so TUI windows inherit the consistent terminal
        //    accent unless overridden in `app_icons.json`.
        rule(
            "files",
            Some("yazi"),
            AccentSlot::Auto,
            true,
            &[],
            &["yazi", "superfile", "spf", "ranger", "nnn", "lf", "broot", "mc"],
        ),
        rule(
            "code",
            Some("nvim"),
            AccentSlot::Auto,
            true,
            &[],
            &["nvim", "neovim", "vim", "helix", "hx", "kakoune", "emacs"],
        ),
        rule(
            "editor",
            Some("edit"),
            AccentSlot::Auto,
            true,
            &[],
            &["nano", "micro"],
        ),
        rule(
            "browser",
            Some("lynx"),
            AccentSlot::Auto,
            true,
            &[],
            &["lynx"],
        ),
        rule(
            "media",
            Some("media"),
            AccentSlot::Auto,
            true,
            &[],
            &["cava", "ncmpcpp", "cmus", "rmpc", "mpv", "musikcube"],
        ),
        rule(
            "system",
            Some("sys"),
            AccentSlot::Auto,
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
                "google-chrome-stable",
                "brave",
                "brave-browser",
                "helium",
                "helium-browser",
                "microsoft-edge",
                "microsoft-edge-stable",
                "edge",
                "vivaldi",
                "vivaldi-stable",
                "qutebrowser",
                "lynx",
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
                "superfile",
                "spf",
                "ranger",
                "lf",
                "nnn",
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
                "neovim",
                "helix",
                "hx",
                "zed",
                "zeditor",
                "dev.zed.zed",
                "emacs",
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
                "org.xfce.mousepad",
                "gedit",
                "org.gnome.texteditor",
                "gnome-text-editor",
                "nano",
                "micro",
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
        // 8. Notes & Knowledge Vaults
        rule(
            "notes",
            None,
            AccentSlot::Secondary,
            false,
            &["obsidian", "logseq"],
            &[],
        ),
        // 9. Documents & PDF Viewers
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
                "libreoffice*",
                "soffice",
                "foliate",
            ],
            &[],
        ),
        // 10. System Utilities & Monitors
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
        // 11. Terminals (Evaluated after terminal_only TUI rules)
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
                "gnome-terminal",
                "org.gnome.terminal",
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

    #[test]
    fn test_custom_colors_glyphs_and_color_only_override_rules() {
        let palette = ThemePalette::default();

        // Named, hex, and rgb() color parsing
        let purple = AccentSlot::from_str("purple");
        assert!(purple.is_custom());
        assert_eq!(
            purple,
            AccentSlot::Custom(Color::from_rgb8(0xa8, 0x55, 0xf7))
        );
        assert_eq!(
            AccentSlot::from_str("light-purple"),
            AccentSlot::Custom(Color::from_rgb8(0xc0, 0x84, 0xfc))
        );
        assert_eq!(
            AccentSlot::from_str("#ff5500"),
            AccentSlot::Custom(Color::from_rgb8(0xff, 0x55, 0x00))
        );
        assert_eq!(
            AccentSlot::from_str("rgb(168, 85, 247)"),
            AccentSlot::Custom(Color::from_rgb8(168, 85, 247))
        );

        // Color-only rule without "icon" inherits "browser" icon from builtin rules
        let mut reg = IconRegistry::load();
        let color_only_rule: Value = serde_json::from_str(
            r#"{
                "class": ["firefox"],
                "color": "purple"
            }"#,
        )
        .unwrap();
        reg.rules
            .insert(0, IconRegistry::parse_rule(&color_only_rule).unwrap());

        let ff = reg.resolve("firefox", "Navigator", "Mozilla Firefox");
        assert_eq!(ff.category_key, "browser");
        assert_eq!(
            ff.resolve_color(&palette),
            Color::from_rgb8(0xa8, 0x55, 0xf7)
        );
    }
}
