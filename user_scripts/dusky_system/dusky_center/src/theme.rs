//! Dynamic theme loader for Dusky Center.
//!
//! Loads dynamically generated colors from Matugen, blending with the active wallpaper.
//! Falls back smoothly if Matugen hasn't generated colors yet.

use iced_core::Color;
use std::{fs, path::PathBuf};

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct AppTheme {
    pub bg: Color,
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
}

impl Default for AppTheme {
    fn default() -> Self {
        Self::from_colors(
            Color::from_rgb8(12, 14, 19),
            Color::from_rgb8(26, 27, 32),
            Color::from_rgb8(30, 31, 37),
            Color::from_rgb8(40, 42, 47),
            Color::from_rgb8(68, 71, 79),
            Color::from_rgb8(226, 226, 233),
            Color::from_rgb8(196, 198, 208),
            Color::from_rgb8(173, 198, 255),
            Color::from_rgb8(216, 226, 255),
            Color::from_rgb8(16, 47, 96),
            Color::from_rgb8(191, 198, 220),
            Color::from_rgb8(222, 188, 223),
            [
                Color::from_rgb8(158, 228, 170),
                Color::from_rgb8(138, 180, 250),
                Color::from_rgb8(239, 140, 179),
            ],
        )
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
    pub fn from_colors(
        bg: Color,
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
    ) -> Self {
        Self {
            bg,
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
            danger: profiles[2],
        }
    }

    pub fn palette_path() -> PathBuf {
        let base = std::env::var_os("XDG_CONFIG_HOME")
            .filter(|p| !p.is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(".config")
            });

        // 1. Dedicated dusky_center palette
        let center_path = base.join("matugen/generated/dusky_center.json");
        if center_path.is_file() {
            return center_path;
        }

        // 2. Fallback to dusky_tray palette
        base.join("matugen/generated/dusky_tray.json")
    }

    pub fn load() -> Self {
        Self::load_generated().unwrap_or_default()
    }

    pub fn load_generated() -> Option<Self> {
        Self::parse(&fs::read_to_string(Self::palette_path()).ok()?)
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

        let surface = color("surface").unwrap_or_else(|| {
            Color::from_rgb(
                bg.r + (fg.r - bg.r) * 0.05,
                bg.g + (fg.g - bg.g) * 0.05,
                bg.b + (fg.b - bg.b) * 0.05,
            )
        });

        let card_bg = color("card_bg").unwrap_or_else(|| {
            Color::from_rgb(
                bg.r + (fg.r - bg.r) * 0.08,
                bg.g + (fg.g - bg.g) * 0.08,
                bg.b + (fg.b - bg.b) * 0.08,
            )
        });

        let card_hover = color("card_hover").unwrap_or_else(|| {
            Color::from_rgb(
                bg.r + (fg.r - bg.r) * 0.12,
                bg.g + (fg.g - bg.g) * 0.12,
                bg.b + (fg.b - bg.b) * 0.12,
            )
        });

        let border = color("border").unwrap_or_else(|| {
            Color::from_rgba(1.0, 1.0, 1.0, 0.1)
        });

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

        Some(Self::from_colors(
            bg,
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
        ))
    }
}
