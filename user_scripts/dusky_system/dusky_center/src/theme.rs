//! Dynamic theme loader for Dusky Center.
//!
//! Loads dynamically generated colors from Matugen, blending with the active wallpaper.
//! Falls back smoothly if Matugen hasn't generated colors yet.

use iced_core::Color;
use std::{fs, path::PathBuf};

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
            bg: Color::from_rgb8(12, 14, 19),          // #0c0e13
            sidebar_bg: Color::from_rgb8(13, 16, 20),  // #0d1014
            surface: Color::from_rgb8(13, 16, 20),     // #0d1014
            card_bg: Color::from_rgb8(17, 19, 24),     // #111318
            card_hover: Color::from_rgb8(21, 24, 30),  // #15181e
            border: Color::from_rgb8(68, 71, 79),      // #44474f
            fg: Color::from_rgb8(226, 226, 233),       // #e2e2e9
            fg_muted: Color::from_rgb8(196, 198, 208), // #c4c6d0
            accent: Color::from_rgb8(173, 198, 255),   // #adc6ff
            accent_hover: Color::from_rgb8(216, 226, 255), // #d8e2ff
            accent_fg: Color::from_rgb8(16, 47, 96),   // #102f60
            secondary: Color::from_rgb8(191, 198, 220),
            tertiary: Color::from_rgb8(222, 188, 223),
            profiles: [
                Color::from_rgb8(158, 228, 170),
                Color::from_rgb8(138, 180, 250),
                Color::from_rgb8(239, 140, 179),
            ],
            danger: Color::from_rgb8(255, 180, 171),   // #ffb4ab
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
