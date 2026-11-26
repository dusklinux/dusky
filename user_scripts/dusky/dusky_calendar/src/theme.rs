//! Dynamic Matugen theme loader for Dusky Calendar.
//!
//! Reads `~/.config/matugen/generated/dusky.json` (with graceful fallbacks) using
//! stat-gated `ThemeStamp` checks so idle ticks cost a single `stat()` syscall
//! and wallpaper changes re-harmonize the entire UI and semantic calendar colors.

use iced_core::Color;
use serde::{Deserialize, Serialize};
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::time::SystemTime;
use std::{env, fs};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ThemeStamp {
    path: PathBuf,
    ino: u64,
    modified: Option<SystemTime>,
    len: u64,
}

impl ThemeStamp {
    fn read(path: &Path) -> Option<Self> {
        let meta = fs::metadata(path).ok()?;
        Some(Self {
            path: path.to_path_buf(),
            ino: meta.ino(),
            modified: meta.modified().ok(),
            len: meta.len(),
        })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SemanticToken {
    Accent,
    Secondary,
    Tertiary,
    ProfileGreen,
    ProfileBlue,
    ProfileRed,
}

impl SemanticToken {
    pub const ALL: [Self; 6] = [
        Self::Accent,
        Self::Secondary,
        Self::Tertiary,
        Self::ProfileGreen,
        Self::ProfileBlue,
        Self::ProfileRed,
    ];

    pub fn label(self) -> &'static str {
        match self {
            Self::Accent => "Accent",
            Self::Secondary => "Secondary",
            Self::Tertiary => "Tertiary",
            Self::ProfileGreen => "Mint",
            Self::ProfileBlue => "Sky",
            Self::ProfileRed => "Rose",
        }
    }

    pub fn next_for_index(index: usize) -> Self {
        Self::ALL[index % Self::ALL.len()]
    }

    pub fn resolve(self, theme: &AppTheme) -> Color {
        match self {
            Self::Accent => theme.accent,
            Self::Secondary => theme.secondary,
            Self::Tertiary => theme.tertiary,
            Self::ProfileGreen => theme.profiles[0],
            Self::ProfileBlue => theme.profiles[1],
            Self::ProfileRed => theme.profiles[2],
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct EventPillColors {
    pub bg: Color,
    pub border: Color,
    pub edge: Color,
    pub text: Color,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct AppTheme {
    pub bg: Color,
    pub sidebar_bg: Color,
    pub surface: Color,
    pub card_bg: Color,
    pub card_hover: Color,
    pub border: Color,
    pub fg: Color,
    pub fg_muted: Color,
    pub accent: Color,
    pub accent_hover: Color,
    pub accent_fg: Color,
    pub secondary: Color,
    pub tertiary: Color,
    pub profiles: [Color; 3],
    pub danger: Color,
    pub danger_bg: Color,
    pub danger_fg: Color,
}

impl Default for AppTheme {
    fn default() -> Self {
        Self {
            bg: Color::from_rgb8(13, 14, 18),              // #0d0e12
            sidebar_bg: Color::from_rgb8(13, 14, 18),      // #0d0e12
            surface: Color::from_rgb8(13, 14, 18),         // #0d0e12
            card_bg: Color::from_rgb8(18, 19, 24),         // #121318
            card_hover: Color::from_rgb8(22, 24, 30),      // #16181e
            border: Color::from_rgb8(67, 71, 80),          // #434750
            fg: Color::from_rgb8(226, 226, 232),           // #e2e2e8
            fg_muted: Color::from_rgb8(196, 198, 210),     // #c4c6d2
            accent: Color::from_rgb8(175, 198, 255),       // #afc6ff
            accent_hover: Color::from_rgb8(216, 226, 255), // #d8e2ff
            accent_fg: Color::from_rgb8(0, 45, 108),       // #002d6c
            secondary: Color::from_rgb8(186, 198, 231),    // #bac6e7
            tertiary: Color::from_rgb8(255, 184, 104),     // #ffb868
            profiles: [
                Color::from_rgb8(158, 228, 169), // #9ee4a9
                Color::from_rgb8(138, 180, 250), // #8ab4fa
                Color::from_rgb8(240, 140, 179), // #f08cb3
            ],
            danger: Color::from_rgb8(255, 180, 171), // #ffb4ab
            danger_bg: Color::from_rgb8(147, 0, 10), // #93000a
            danger_fg: Color::from_rgb8(255, 218, 214),
        }
    }
}

pub fn hex_to_color(hex: &str) -> Option<Color> {
    let hex = hex.trim().trim_start_matches('#');
    if hex.len() != 6 || !hex.is_ascii() {
        return None;
    }
    Some(Color::from_rgb8(
        u8::from_str_radix(&hex[0..2], 16).ok()?,
        u8::from_str_radix(&hex[2..4], 16).ok()?,
        u8::from_str_radix(&hex[4..6], 16).ok()?,
    ))
}

pub fn color_to_hex(color: Color) -> String {
    let r = (color.r.clamp(0.0, 1.0) * 255.0).round() as u8;
    let g = (color.g.clamp(0.0, 1.0) * 255.0).round() as u8;
    let b = (color.b.clamp(0.0, 1.0) * 255.0).round() as u8;
    format!("#{r:02x}{g:02x}{b:02x}")
}

/// Relative sRGB luminance (0.0 = black, 1.0 = white).
pub fn relative_luminance(color: Color) -> f32 {
    fn channel(c: f32) -> f32 {
        if c <= 0.03928 {
            c / 12.92
        } else {
            ((c + 0.055) / 1.055).powf(2.4)
        }
    }
    0.2126 * channel(color.r) + 0.7152 * channel(color.g) + 0.0722 * channel(color.b)
}

/// Pick a crisp high-contrast foreground color for arbitrary swatch or pill fills.
pub fn contrasting_fg(bg: Color) -> Color {
    if relative_luminance(bg) > 0.36 {
        Color::from_rgb8(14, 16, 22)
    } else {
        Color::from_rgb8(244, 246, 250)
    }
}

/// Opaque sRGB mix: prevents washed-out GPU alpha blending on dark/light wgpu surfaces.
pub fn mix(base: Color, tint: Color, amount: f32) -> Color {
    let t = amount.clamp(0.0, 1.0);
    Color::from_rgb(
        base.r + (tint.r - base.r) * t,
        base.g + (tint.g - base.g) * t,
        base.b + (tint.b - base.b) * t,
    )
}

/// Scale HSV saturation without changing hue, value, or alpha.
pub fn saturate(color: Color, factor: f32) -> Color {
    let max = color.r.max(color.g).max(color.b);
    let min = color.r.min(color.g).min(color.b);
    let chroma = max - min;
    if chroma <= f32::EPSILON {
        return color;
    }
    let factor = factor.min(max / chroma);
    Color {
        r: max - (max - color.r) * factor,
        g: max - (max - color.g) * factor,
        b: max - (max - color.b) * factor,
        a: color.a,
    }
}

impl AppTheme {
    pub fn palette_path() -> PathBuf {
        let base = env::var_os("XDG_CONFIG_HOME")
            .filter(|p| !p.is_empty())
            .map(PathBuf::from)
            .filter(|p| p.is_absolute())
            .unwrap_or_else(|| {
                PathBuf::from(env::var_os("HOME").unwrap_or_default()).join(".config")
            });

        for candidate in [
            "matugen/generated/dusky.json",
            "matugen/generated/dusky_rust.json",
            "matugen/generated/dusky_calendar.json",
            "matugen/generated/dusky_center.json",
            "matugen/generated/dusky_tray.json",
        ] {
            let path = base.join(candidate);
            if path.is_file() {
                return path;
            }
        }

        base.join("matugen/generated/dusky.json")
    }

    pub fn load() -> Self {
        Self::load_generated().unwrap_or_default()
    }

    pub fn load_with_stamp() -> (Self, Option<ThemeStamp>) {
        match Self::load_if_changed(None) {
            Some((theme, stamp)) => (theme, Some(stamp)),
            None => (Self::default(), None),
        }
    }

    pub fn load_generated() -> Option<Self> {
        Self::parse(&fs::read_to_string(Self::palette_path()).ok()?)
    }

    /// Re-read and parse the active palette only if its path, inode, mtime, or size changed.
    pub fn load_if_changed(last: Option<&ThemeStamp>) -> Option<(Self, ThemeStamp)> {
        let path = Self::palette_path();
        Self::load_path_if_changed(&path, last)
    }

    fn load_path_if_changed(path: &Path, last: Option<&ThemeStamp>) -> Option<(Self, ThemeStamp)> {
        let stamp = ThemeStamp::read(path)?;
        if last == Some(&stamp) {
            return None;
        }
        let raw = fs::read_to_string(path).ok()?;
        let theme = Self::parse(&raw)?;
        Some((theme, stamp))
    }

    fn parse(raw: &str) -> Option<Self> {
        let json: serde_json::Value = serde_json::from_str(raw).ok()?;
        let color = |key: &str| hex_to_color(json.get(key)?.as_str()?);
        let defaults = Self::default();

        let bg = color("bg")?;
        let fg = color("fg")?;
        let accent = color("accent").or_else(|| color("primary"))?;
        let accent_hover = color("accent_hover").unwrap_or_else(|| mix(accent, fg, 0.22));
        let accent_fg = color("accent_fg").unwrap_or_else(|| contrasting_fg(accent));
        let secondary = color("secondary")?;
        let tertiary = color("tertiary")?;
        let danger = color("danger")
            .or_else(|| color("error"))
            .unwrap_or(defaults.danger);

        let sidebar_bg = color("sidebar_bg").unwrap_or(bg);
        let surface = color("surface").unwrap_or(sidebar_bg);
        let card_bg = color("card_bg").unwrap_or_else(|| mix(bg, fg, 0.03));
        let card_hover = color("card_hover").unwrap_or_else(|| mix(card_bg, fg, 0.045));
        let border = color("border")
            .or_else(|| color("muted"))
            .unwrap_or_else(|| mix(bg, fg, 0.22));
        let fg_muted = color("fg_muted").unwrap_or_else(|| mix(bg, fg, 0.65));

        let profiles = [
            color("profile_green").unwrap_or(defaults.profiles[0]),
            color("profile_blue").unwrap_or(defaults.profiles[1]),
            color("profile_red").unwrap_or(defaults.profiles[2]),
        ];

        Some(Self {
            bg,
            sidebar_bg,
            surface,
            card_bg,
            card_hover,
            border,
            fg,
            fg_muted,
            accent,
            accent_hover,
            accent_fg,
            secondary,
            tertiary,
            profiles,
            danger,
            danger_bg: color("danger_bg").unwrap_or(defaults.danger_bg),
            danger_fg: color("danger_fg").unwrap_or(defaults.danger_fg),
        })
    }

    pub fn is_light(&self) -> bool {
        relative_luminance(self.bg) > 0.45
    }

    pub fn resolve_token(&self, token: SemanticToken) -> Color {
        token.resolve(self)
    }

    /// Luminance-aware tonal separation for window chrome (header, sidebar,
    /// gutters) when Matugen emits `surface` / `sidebar_bg` identical to `bg`.
    pub fn chrome_bg(&self) -> Color {
        let distinct = |candidate: Color| {
            (candidate.r - self.bg.r)
                .abs()
                .max((candidate.g - self.bg.g).abs())
                .max((candidate.b - self.bg.b).abs())
                >= 0.015
        };

        if distinct(self.sidebar_bg) {
            self.sidebar_bg
        } else if distinct(self.surface) {
            self.surface
        } else if self.is_light() {
            mix(self.bg, Color::BLACK, 0.032)
        } else {
            mix(self.bg, self.fg, 0.022)
        }
    }

    pub fn grid_line(&self) -> Color {
        mix(self.bg, self.border, if self.is_light() { 0.36 } else { 0.30 })
    }

    pub fn grid_line_subtle(&self) -> Color {
        mix(self.bg, self.border, if self.is_light() { 0.18 } else { 0.14 })
    }

    pub fn surface_border(&self) -> Color {
        mix(self.bg, self.border, if self.is_light() { 0.55 } else { 0.48 })
    }

    pub fn out_of_month_tint(&self) -> Color {
        if self.is_light() {
            mix(self.bg, Color::BLACK, 0.055)
        } else if relative_luminance(self.bg) > 0.002 {
            mix(self.bg, Color::BLACK, 0.40)
        } else {
            mix(self.bg, self.fg, 0.016)
        }
    }

    pub fn weekend_tint(&self) -> Color {
        mix(self.bg, self.fg, if self.is_light() { 0.04 } else { 0.03 })
    }

    pub fn selected_tint(&self) -> Color {
        mix(self.bg, self.accent, if self.is_light() { 0.18 } else { 0.14 })
    }

    pub fn hover_tint(&self) -> Color {
        mix(self.bg, self.fg, if self.is_light() { 0.075 } else { 0.065 })
    }

    pub fn accent_pill_hover(&self) -> Color {
        mix(self.bg, self.accent, if self.is_light() { 0.24 } else { 0.20 })
    }

    pub fn active_week_tint(&self) -> Color {
        let base = self.chrome_bg();
        mix(base, self.fg, if self.is_light() { 0.08 } else { 0.07 })
    }

    /// Hover fill for unselected mini-calendar days; strictly more prominent than
    /// `active_week_tint` in both dark and light themes so hovering inside the active
    /// week never inverts contrast.
    pub fn mini_day_hover(&self) -> Color {
        let base = self.chrome_bg();
        mix(base, self.fg, if self.is_light() { 0.14 } else { 0.135 })
    }

    pub fn muted_day_fg(&self) -> Color {
        mix(self.bg, self.fg_muted, 0.44)
    }

    pub fn input_bg(&self) -> Color {
        if self.is_light() {
            mix(self.card_bg, Color::WHITE, 0.55)
        } else {
            mix(self.bg, Color::BLACK, 0.28)
        }
    }

    pub fn backdrop(&self) -> Color {
        if self.is_light() {
            Color::from_rgba(0.05, 0.06, 0.09, 0.34)
        } else {
            Color::from_rgba(0.02, 0.03, 0.05, 0.62)
        }
    }

    pub fn event_pill_colors(&self, base: Color, past: bool, hovered: bool) -> EventPillColors {
        let light = self.is_light();
        let (fill_mix, border_mix, edge_mix, text_mix) = match (past, hovered) {
            (false, false) => (if light { 0.22 } else { 0.20 }, 0.36, 1.0, 1.0),
            (false, true) => (if light { 0.32 } else { 0.30 }, 0.62, 1.0, 1.0),
            (true, false) => (if light { 0.11 } else { 0.10 }, 0.20, 0.42, 0.58),
            (true, true) => (if light { 0.18 } else { 0.17 }, 0.36, 0.62, 0.78),
        };
        EventPillColors {
            bg: mix(self.bg, base, fill_mix),
            border: mix(self.bg, base, border_mix),
            edge: mix(self.bg, base, edge_mix),
            text: mix(self.bg, self.fg, text_mix),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn theme_stamp_skips_unchanged_detects_atomic_replace_and_retries_malformed() {
        let root = std::env::temp_dir().join(format!(
            "dusky-calendar-theme-stamp-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        let palette = root.join("dusky.json");
        let tmp = root.join("dusky.json.tmp");

        let v1 = r##"{"bg":"#112233","fg":"#eeeeee","accent":"#aabbcc","secondary":"#778899","tertiary":"#8899aa"}"##;
        let v2 = r##"{"bg":"#445566","fg":"#eeeeee","accent":"#aabbcc","secondary":"#778899","tertiary":"#8899aa"}"##;
        assert_eq!(v1.len(), v2.len());

        fs::write(&palette, v1).unwrap();
        let (theme1, stamp1) = AppTheme::load_path_if_changed(&palette, None).unwrap();
        assert_eq!(theme1.bg, Color::from_rgb8(0x11, 0x22, 0x33));
        assert!(!theme1.is_light());

        assert!(AppTheme::load_path_if_changed(&palette, Some(&stamp1)).is_none());

        fs::write(&tmp, "{").unwrap();
        fs::rename(&tmp, &palette).unwrap();
        assert!(AppTheme::load_path_if_changed(&palette, Some(&stamp1)).is_none());

        fs::write(&tmp, v2).unwrap();
        fs::rename(&tmp, &palette).unwrap();
        let (theme2, stamp2) = AppTheme::load_path_if_changed(&palette, Some(&stamp1)).unwrap();
        assert_eq!(theme2.bg, Color::from_rgb8(0x44, 0x55, 0x66));
        assert_ne!(stamp1, stamp2);
        assert!(AppTheme::load_path_if_changed(&palette, Some(&stamp2)).is_none());

        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn hex_conversion_and_semantic_tokens_round_trip() {
        let color = hex_to_color("#afc6ff").unwrap();
        assert_eq!(color_to_hex(color), "#afc6ff");
        assert!(hex_to_color("bad").is_none());

        let theme = AppTheme::default();
        assert_eq!(theme.resolve_token(SemanticToken::Accent), theme.accent);
        assert_eq!(
            theme.resolve_token(SemanticToken::ProfileGreen),
            theme.profiles[0]
        );
        assert_eq!(SemanticToken::next_for_index(6), SemanticToken::Accent);
    }

    #[test]
    fn calibrated_surfaces_separate_flat_palettes_and_preserve_hover_contrast() {
        let dark = AppTheme::default();
        assert_ne!(dark.chrome_bg(), dark.bg);
        assert_ne!(dark.out_of_month_tint(), dark.bg);
        assert!(relative_luminance(dark.mini_day_hover()) > relative_luminance(dark.active_week_tint()));
        assert!(relative_luminance(dark.hover_tint()) > relative_luminance(dark.weekend_tint()));

        let light = AppTheme {
            bg: Color::from_rgb8(245, 246, 250),
            sidebar_bg: Color::from_rgb8(245, 246, 250),
            surface: Color::from_rgb8(245, 246, 250),
            card_bg: Color::from_rgb8(255, 255, 255),
            card_hover: Color::from_rgb8(236, 238, 245),
            fg: Color::from_rgb8(24, 26, 32),
            fg_muted: Color::from_rgb8(90, 94, 108),
            ..AppTheme::default()
        };
        assert!(light.is_light());
        assert_ne!(light.chrome_bg(), light.bg);
        assert_ne!(light.out_of_month_tint(), light.bg);
        assert!(relative_luminance(light.mini_day_hover()) < relative_luminance(light.active_week_tint()));
    }
}
