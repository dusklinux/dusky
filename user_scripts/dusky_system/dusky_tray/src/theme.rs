//! One coherent Matugen palette, reloaded without GTK or a theme daemon.

use iced_core::Color;
use std::{fs, path::PathBuf};

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct AppTheme {
    pub bg: Color,
    pub card_bg: Color,
    pub fg: Color,
    pub accent: Color,
    pub accent_hover: Color,
    pub secondary: Color,
    pub tertiary: Color,
    pub muted: Color,
    pub profiles: [Color; 3],
    pub danger: Color,
}

impl Default for AppTheme {
    fn default() -> Self {
        Self::from_colors(
            Color::from_rgb8(12, 14, 19),
            Color::from_rgb8(226, 226, 233),
            Color::from_rgb8(190, 195, 205),
            Color::from_rgb8(230, 233, 240),
            Color::from_rgb8(185, 195, 205),
            Color::from_rgb8(200, 195, 185),
            [
                Color::from_rgb8(166, 227, 161),
                Color::from_rgb8(137, 180, 250),
                Color::from_rgb8(243, 139, 168),
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

/// Increase HSV saturation linearly with the value while preserving hue,
/// maximum channel brightness, and alpha. At 100: 1.4 × the palette saturation.
pub fn slider_label_color(color: Color, value: f32) -> Color {
    saturate(
        color,
        0.4 + crate::backend::system::clamp(value, 0.0, 100.0) / 100.0,
    )
}

/// Keep the low end subdued, with a softer 140% peak for slider icons.
pub fn slider_icon_color(color: Color, value: f32) -> Color {
    saturate(
        color,
        0.4 + crate::backend::system::clamp(value, 0.0, 100.0) / 100.0,
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
        ..color
    }
}

impl AppTheme {
    fn from_colors(
        bg: Color,
        fg: Color,
        accent: Color,
        accent_hover: Color,
        secondary: Color,
        tertiary: Color,
        profiles: [Color; 3],
    ) -> Self {
        let blend = |amount: f32| {
            Color::from_rgb(
                bg.r + (fg.r - bg.r) * amount,
                bg.g + (fg.g - bg.g) * amount,
                bg.b + (fg.b - bg.b) * amount,
            )
        };
        Self {
            bg,
            card_bg: blend(0.05),
            fg,
            accent,
            accent_hover,
            secondary,
            tertiary,
            muted: blend(0.62),
            profiles,
            danger: profiles[2],
        }
    }

    pub fn palette_path() -> PathBuf {
        std::env::var_os("XDG_CONFIG_HOME")
            .filter(|p| !p.is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(".config")
            })
            .join("matugen/generated/dusky_tray.json")
    }

    pub fn load() -> Self {
        Self::load_generated().unwrap_or_default()
    }

    // Invalid or partially written palettes must not replace a valid theme.
    pub fn load_generated() -> Option<Self> {
        Self::parse(&fs::read_to_string(Self::palette_path()).ok()?)
    }

    fn parse(raw: &str) -> Option<Self> {
        let json: serde_json::Value = serde_json::from_str(raw).ok()?;
        let color = |key: &str| hex_to_color(json.get(key)?.as_str()?);
        Some(Self::from_colors(
            color("bg")?,
            color("fg")?,
            color("accent")?,
            color("accent_hover")?,
            color("secondary")?,
            color("tertiary")?,
            [
                color("profile_green")?,
                color("profile_blue")?,
                color("profile_red")?,
            ],
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn slider_saturation_is_linear_and_preserves_brightness() {
        let base = Color::from_rgba(0.5, 0.6, 0.8, 0.9);
        for (value, factor) in [(0.0, 0.4), (50.0, 0.9), (100.0, 1.4)] {
            let color = slider_label_color(base, value);
            let saturation = (color.b - color.r) / color.b;
            assert!((saturation - 0.375 * factor).abs() < 0.00001);
            assert_eq!(color.b, base.b);
            assert_eq!(color.a, base.a);
        }
        for base in [Color::BLACK, Color::WHITE, Color::from_rgb(1.0, 0.0, 0.2)] {
            let color = slider_label_color(base, 100.0);
            assert!(
                [color.r, color.g, color.b]
                    .iter()
                    .all(|v| (0.0..=1.0).contains(v))
            );
        }
        for (value, factor) in [(0.0, 0.4), (50.0, 0.9), (100.0, 1.4)] {
            let color = slider_icon_color(base, value);
            assert!(((color.b - color.r) / color.b - 0.375 * factor).abs() < 0.00001);
        }
        let rail = saturate(base, 1.3);
        assert!(((rail.b - rail.r) / rail.b - 0.375 * 1.3).abs() < 0.00001);
    }

    #[test]
    fn hex_parses() {
        let c = hex_to_color("#89b4fa").unwrap();
        assert!((c.r - 137.0 / 255.0).abs() < 0.01);
        for invalid in ["zzz", "ééé", "#12345"] {
            assert!(hex_to_color(invalid).is_none());
        }
    }
    #[test]
    fn generated_palette_is_complete_and_opaque_before_panel_opacity() {
        let raw = r##"{"bg":"#101010","fg":"#eeeeee","accent":"#80dd80","accent_hover":"#aaeeaa","secondary":"#b0ccbb","tertiary":"#ccbb99","profile_green":"#99dd99","profile_blue":"#9999dd","profile_red":"#dd9999"}"##;
        let theme = AppTheme::parse(raw).unwrap();
        assert_eq!(theme.accent, hex_to_color("#80dd80").unwrap());
        assert_eq!(theme.bg.a, 1.0);
        assert_eq!(theme.accent.a, 1.0);
        assert!(AppTheme::parse(&raw.replace("\"accent_hover\"", "\"missing\"")).is_none());
        assert!(AppTheme::parse(&raw[..raw.len() - 1]).is_none());
    }
}
