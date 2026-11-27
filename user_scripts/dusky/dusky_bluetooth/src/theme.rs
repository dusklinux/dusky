//! Dynamic Matugen theme loader for Dusky Bluetooth.
//!
//! Reads `~/.config/matugen/generated/dusky.json` (with graceful fallbacks) using
//! stat-gated `ThemeStamp` checks so idle ticks cost a single `stat()` syscall
//! and wallpaper changes re-harmonize the entire UI and semantic status badges.

use iced_core::Color;
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

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct BadgeColors {
    pub bg: Color,
    pub border: Color,
    pub fg: Color,
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
    pub success: Color,
    pub warning: Color,
    pub info: Color,
    pub danger: Color,
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
            success: Color::from_rgb8(158, 228, 169),      // #9ee4a9
            warning: Color::from_rgb8(255, 184, 104),      // #ffb868
            info: Color::from_rgb8(138, 180, 250),         // #8ab4fa
            danger: Color::from_rgb8(255, 180, 171),       // #ffb4ab
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
            "matugen/generated/dusky_bluetooth.json",
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

    pub fn load_with_stamp() -> (Self, Option<ThemeStamp>) {
        match Self::load_if_changed(None) {
            Some((theme, stamp)) => (theme, Some(stamp)),
            None => (Self::default(), None),
        }
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
        let light = relative_luminance(bg) > 0.45;

        let default_success = if light {
            Color::from_rgb8(24, 128, 56) // #188038
        } else {
            defaults.success
        };
        let default_warning = if light {
            Color::from_rgb8(176, 96, 0) // #b06000
        } else {
            defaults.warning
        };
        let default_info = if light {
            Color::from_rgb8(26, 115, 232) // #1a73e8
        } else {
            defaults.info
        };
        let default_danger = if light {
            Color::from_rgb8(186, 26, 26) // #ba1a1a
        } else {
            defaults.danger
        };

        let sidebar_bg = color("sidebar_bg").unwrap_or(bg);
        let surface = color("surface").unwrap_or(sidebar_bg);
        let card_bg = color("card_bg").unwrap_or_else(|| mix(bg, fg, 0.03));
        let card_hover = color("card_hover").unwrap_or_else(|| mix(card_bg, fg, 0.045));
        let border = color("border")
            .or_else(|| color("muted"))
            .unwrap_or_else(|| mix(bg, fg, 0.22));
        let fg_muted = color("fg_muted").unwrap_or_else(|| mix(bg, fg, 0.65));

        let success = color("success")
            .or_else(|| color("profile_green"))
            .unwrap_or(default_success);
        let warning = color("warning")
            .or_else(|| color("tertiary"))
            .unwrap_or(default_warning);
        let info = color("info")
            .or_else(|| color("profile_blue"))
            .unwrap_or(default_info);
        let danger = color("danger")
            .or_else(|| color("error"))
            .unwrap_or(default_danger);

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
            success,
            warning,
            info,
            danger,
        })
    }

    pub fn is_light(&self) -> bool {
        relative_luminance(self.bg) > 0.45
    }

    /// Luminance-aware tonal separation for window chrome when Matugen emits
    /// `surface` / `sidebar_bg` identical to `bg`.
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
        mix(
            self.bg,
            self.border,
            if self.is_light() { 0.36 } else { 0.30 },
        )
    }

    pub fn grid_line_subtle(&self) -> Color {
        mix(
            self.bg,
            self.border,
            if self.is_light() { 0.18 } else { 0.14 },
        )
    }

    pub fn surface_border(&self) -> Color {
        mix(
            self.bg,
            self.border,
            if self.is_light() { 0.55 } else { 0.48 },
        )
    }

    pub fn muted_fg(&self) -> Color {
        mix(self.bg, self.fg_muted, 0.62)
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

    /// Opaque sRGB status pill palette with guaranteed readability in both dark and light themes.
    pub fn status_badge(&self, semantic: Color) -> BadgeColors {
        let light = self.is_light();
        let bg = mix(self.card_bg, semantic, if light { 0.18 } else { 0.16 });
        let border = mix(self.card_bg, semantic, if light { 0.48 } else { 0.42 });
        let fg = if light {
            mix(self.fg, semantic, 0.35)
        } else {
            mix(self.fg, semantic, 0.68)
        };
        BadgeColors { bg, border, fg }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};

    static TEST_DIR_SEQ: AtomicU64 = AtomicU64::new(0);

    fn unique_test_dir(tag: &str) -> PathBuf {
        let seq = TEST_DIR_SEQ.fetch_add(1, Ordering::Relaxed);
        std::env::temp_dir().join(format!(
            "dusky-bluetooth-{tag}-{}-{seq}",
            std::process::id()
        ))
    }

    #[test]
    fn theme_stamp_skips_unchanged_detects_atomic_replace_and_retries_malformed() {
        let root = unique_test_dir("theme-stamp");
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

    fn color_to_hex(color: Color) -> String {
        let r = (color.r.clamp(0.0, 1.0) * 255.0).round() as u8;
        let g = (color.g.clamp(0.0, 1.0) * 255.0).round() as u8;
        let b = (color.b.clamp(0.0, 1.0) * 255.0).round() as u8;
        format!("#{r:02x}{g:02x}{b:02x}")
    }

    fn contrast_ratio(a: Color, b: Color) -> f32 {
        let la = relative_luminance(a);
        let lb = relative_luminance(b);
        let (hi, lo) = if la >= lb { (la, lb) } else { (lb, la) };
        (hi + 0.05) / (lo + 0.05)
    }

    #[test]
    fn hex_conversion_round_trips_and_rejects_invalid_inputs() {
        let color = hex_to_color("#afc6ff").unwrap();
        assert_eq!(color_to_hex(color), "#afc6ff");
        assert_eq!(
            hex_to_color("9ee4a9").map(color_to_hex).as_deref(),
            Some("#9ee4a9")
        );
        assert!(hex_to_color("bad").is_none());
        assert!(hex_to_color("#zzzzzz").is_none());
    }

    #[test]
    fn calibrated_surfaces_and_contrasting_fg_preserve_contrast_in_dark_and_light() {
        let dark = AppTheme::default();
        assert!(!dark.is_light());
        assert_ne!(dark.chrome_bg(), dark.bg);
        assert!(relative_luminance(contrasting_fg(dark.bg)) > 0.7);
        assert!(relative_luminance(contrasting_fg(dark.accent)) < 0.1);

        let dark_badge = dark.status_badge(dark.success);
        assert!(contrast_ratio(dark_badge.fg, dark_badge.bg) >= 4.5);
        let dark_danger = dark.status_badge(dark.danger);
        assert!(contrast_ratio(dark_danger.fg, dark_danger.bg) >= 4.5);

        let light = AppTheme::parse(
            r##"{"bg":"#f5f6fa","fg":"#181a20","accent":"#2563eb"}"##,
        )
        .unwrap();
        assert!(light.is_light());
        assert_ne!(light.chrome_bg(), light.bg);
        assert!(relative_luminance(contrasting_fg(light.bg)) < 0.1);

        for semantic in [light.success, light.warning, light.info, light.danger] {
            let badge = light.status_badge(semantic);
            assert!(
                contrast_ratio(badge.fg, badge.bg) >= 4.5,
                "light badge contrast too low: {}",
                contrast_ratio(badge.fg, badge.bg)
            );
        }
    }

    #[test]
    fn minimal_json_parses_without_secondary_or_tertiary_in_dark_and_light() {
        let dark = AppTheme::parse(r##"{"bg":"#101218","fg":"#eceef4","primary":"#8ab4fa"}"##)
            .unwrap();
        assert!(!dark.is_light());
        assert_eq!(dark.accent, Color::from_rgb8(0x8a, 0xb4, 0xfa));
        assert!(contrast_ratio(dark.status_badge(dark.danger).fg, dark.status_badge(dark.danger).bg) >= 4.5);

        let light = AppTheme::parse(r##"{"bg":"#f7f8fc","fg":"#171920","accent":"#1d4ed8"}"##)
            .unwrap();
        assert!(light.is_light());
        assert!(relative_luminance(light.danger) < 0.25);
        assert!(contrast_ratio(light.status_badge(light.danger).fg, light.status_badge(light.danger).bg) >= 4.5);
    }
}
