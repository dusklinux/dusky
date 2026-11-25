//! Dynamic theme loader for Dusky Center.
//!
//! Loads dynamically generated colors from Matugen, blending with the active wallpaper.
//! Uses stat-gated `ThemeStamp` checks to skip re-reading unchanged palette files on ticks
//! and falls back smoothly if Matugen hasn't generated colors yet.

use iced_core::Color;
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::time::SystemTime;
use std::{fs, env};

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
            bg: Color::from_rgb8(12, 14, 19),              // #0c0e13
            sidebar_bg: Color::from_rgb8(13, 16, 20),      // #0d1014
            surface: Color::from_rgb8(13, 16, 20),         // #0d1014
            card_bg: Color::from_rgb8(17, 19, 24),         // #111318
            card_hover: Color::from_rgb8(21, 24, 30),      // #15181e
            border: Color::from_rgb8(68, 71, 79),          // #44474f
            fg: Color::from_rgb8(226, 226, 233),           // #e2e2e9
            fg_muted: Color::from_rgb8(196, 198, 208),     // #c4c6d0
            accent: Color::from_rgb8(173, 198, 255),       // #adc6ff
            accent_hover: Color::from_rgb8(216, 226, 255), // #d8e2ff
            accent_fg: Color::from_rgb8(16, 47, 96),       // #102f60
            secondary: Color::from_rgb8(191, 198, 220),
            tertiary: Color::from_rgb8(222, 188, 223),
            profiles: [
                Color::from_rgb8(158, 228, 170),
                Color::from_rgb8(138, 180, 250),
                Color::from_rgb8(239, 140, 179),
            ],
            danger: Color::from_rgb8(255, 180, 171), // #ffb4ab
            danger_bg: Color::from_rgb8(147, 0, 10),
            danger_fg: Color::from_rgb8(255, 218, 214),
        }
    }
}

pub fn hex_to_color(hex: &str) -> Option<Color> {
    let hex = hex.trim_start_matches('#');
    if hex.len() != 6 || !hex.is_ascii() {
        return None;
    }
    Some(Color::from_rgb8(
        u8::from_str_radix(&hex[0..2], 16).ok()?,
        u8::from_str_radix(&hex[2..4], 16).ok()?,
        u8::from_str_radix(&hex[4..6], 16).ok()?,
    ))
}

impl AppTheme {
    #[allow(clippy::too_many_arguments)]
    pub fn from_colors(
        bg: Color,
        sidebar_bg: Color,
        surface: Color,
        card_bg: Color,
        card_hover: Color,
        border: Color,
        fg: Color,
        fg_muted: Color,
        accent: Color,
        accent_hover: Color,
        accent_fg: Color,
        secondary: Color,
        tertiary: Color,
        profiles: [Color; 3],
        danger: Color,
    ) -> Self {
        Self {
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
            danger_bg: Self::default().danger_bg,
            danger_fg: Self::default().danger_fg,
        }
    }

    pub fn palette_path() -> PathBuf {
        let base = env::var_os("XDG_CONFIG_HOME")
            .filter(|p| !p.is_empty())
            .map(PathBuf::from)
            .filter(|p| p.is_absolute())
            .unwrap_or_else(|| {
                PathBuf::from(env::var_os("HOME").unwrap_or_default()).join(".config")
            });

        // 1. Unified dusky palette
        let dusky_path = base.join("matugen/generated/dusky.json");
        if dusky_path.is_file() {
            return dusky_path;
        }
        let rust_path = base.join("matugen/generated/dusky_rust.json");
        if rust_path.is_file() {
            return rust_path;
        }

        // 2. Dedicated dusky_center palette
        let center_path = base.join("matugen/generated/dusky_center.json");
        if center_path.is_file() {
            return center_path;
        }

        // 3. Fallback to dusky_tray palette
        base.join("matugen/generated/dusky_tray.json")
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
    /// Returns `None` if unchanged or if the file is missing/malformed (preserving the prior stamp).
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

        let bg = color("bg")?;
        let fg = color("fg")?;
        let accent = color("accent")?;
        let accent_hover = color("accent_hover").unwrap_or(accent);
        let accent_fg = color("accent_fg").unwrap_or_else(|| {
            // Default high-contrast dark foreground for accent background
            Color::from_rgb8(16, 47, 96)
        });
        let secondary = color("secondary")?;
        let tertiary = color("tertiary")?;
        let danger = color("danger")
            .or_else(|| color("error"))
            .unwrap_or(Color::from_rgb8(255, 180, 171));

        let sidebar_bg = color("sidebar_bg").unwrap_or(bg);
        let surface = color("surface").unwrap_or(sidebar_bg);

        let card_bg = color("card_bg").unwrap_or_else(|| {
            Color::from_rgb(
                bg.r + (fg.r - bg.r) * 0.02,
                bg.g + (fg.g - bg.g) * 0.02,
                bg.b + (fg.b - bg.b) * 0.02,
            )
        });

        let card_hover = color("card_hover").unwrap_or_else(|| {
            Color::from_rgb(
                card_bg.r + (fg.r - card_bg.r) * 0.04,
                card_bg.g + (fg.g - card_bg.g) * 0.04,
                card_bg.b + (fg.b - card_bg.b) * 0.04,
            )
        });

        let border = color("border").unwrap_or_else(|| Color::from_rgba(1.0, 1.0, 1.0, 0.1));

        let fg_muted = color("fg_muted").unwrap_or_else(|| {
            Color::from_rgb(
                bg.r + (fg.r - bg.r) * 0.65,
                bg.g + (fg.g - bg.g) * 0.65,
                bg.b + (fg.b - bg.b) * 0.65,
            )
        });

        let profiles = [
            color("profile_green").unwrap_or(Color::from_rgb8(158, 228, 170)),
            color("profile_blue").unwrap_or(Color::from_rgb8(138, 180, 250)),
            color("profile_red").unwrap_or(Color::from_rgb8(239, 140, 179)),
        ];

        let mut theme = Self::from_colors(
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
        );
        theme.danger_bg = color("danger_bg").unwrap_or(theme.danger_bg);
        theme.danger_fg = color("danger_fg").unwrap_or(theme.danger_fg);
        Some(theme)
    }
}

/// An opaque sRGB mix: GPU alpha blending otherwise makes subtle GTK tints
/// much brighter than their numerical opacity suggests.
pub fn mix(base: Color, tint: Color, amount: f32) -> Color {
    Color::from_rgb(
        base.r + (tint.r - base.r) * amount,
        base.g + (tint.g - base.g) * amount,
        base.b + (tint.b - base.b) * amount,
    )
}

/// Scale HSV saturation without changing hue, brightness, or alpha.
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn theme_stamp_skips_unchanged_detects_atomic_replace_and_retries_malformed() {
        let root = std::env::temp_dir().join(format!("dusky-theme-stamp-{}", std::process::id()));
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

        // Unchanged file skips parsing.
        assert!(AppTheme::load_path_if_changed(&palette, Some(&stamp1)).is_none());

        // Malformed intermediate write returns None and leaves prior stamp intact.
        fs::write(&tmp, "{").unwrap();
        fs::rename(&tmp, &palette).unwrap();
        assert!(AppTheme::load_path_if_changed(&palette, Some(&stamp1)).is_none());

        // Same-size atomic replacement with new inode is detected immediately.
        fs::write(&tmp, v2).unwrap();
        fs::rename(&tmp, &palette).unwrap();
        let (theme2, stamp2) = AppTheme::load_path_if_changed(&palette, Some(&stamp1)).unwrap();
        assert_eq!(theme2.bg, Color::from_rgb8(0x44, 0x55, 0x66));
        assert_ne!(stamp1, stamp2);
        assert!(AppTheme::load_path_if_changed(&palette, Some(&stamp2)).is_none());

        let _ = fs::remove_dir_all(&root);
    }
}
