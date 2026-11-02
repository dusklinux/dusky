use iced_core::Color;
use std::fs;
use std::path::PathBuf;

#[derive(Debug, Clone, Copy)]
#[allow(dead_code)]
pub struct AppTheme {
    pub bg: Color,
    pub card_bg: Color,
    pub fg: Color,
    pub accent: Color,
    pub muted: Color,
    pub success: Color,
}

impl Default for AppTheme {
    fn default() -> Self {
        Self {
            bg: Color::from_rgb8(10, 12, 16),
            card_bg: Color::from_rgb8(16, 18, 26),
            fg: Color::from_rgb8(240, 243, 255),
            accent: Color::from_rgb8(255, 182, 141), // #ffb68d peach / warm amber
            muted: Color::from_rgb8(140, 150, 175),
            success: Color::from_rgb8(52, 211, 153),
        }
    }
}

impl AppTheme {
    pub fn load() -> Self {
        let base = std::env::var_os("XDG_CONFIG_HOME")
            .filter(|s| !s.is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                std::env::var("HOME")
                    .map(|h| PathBuf::from(h).join(".config"))
                    .unwrap_or_else(|_| PathBuf::from("/home/dusk/.config"))
            });

        let candidates = [
            base.join("matugen/generated/dusky.json"),
            base.join("matugen/generated/dusky_rust.json"),
            base.join("matugen/generated/dusky_tui.json"),
        ];

        for path in &candidates {
            if let Ok(content) = fs::read_to_string(path) {
                if let Ok(json) = serde_json::from_str::<serde_json::Value>(&content) {
                    let accent = json
                        .get("accent")
                        .or_else(|| json.get("primary"))
                        .and_then(|v| v.as_str())
                        .and_then(hex_to_color)
                        .unwrap_or(Color::from_rgb8(255, 182, 141));

                    let bg = json
                        .get("bg")
                        .and_then(|v| v.as_str())
                        .and_then(hex_to_color)
                        .unwrap_or(Color::from_rgb8(10, 12, 16));

                    let card_bg = json
                        .get("card_bg")
                        .or_else(|| json.get("surface"))
                        .and_then(|v| v.as_str())
                        .and_then(hex_to_color)
                        .unwrap_or(Color::from_rgb8(16, 18, 26));

                    let fg = json
                        .get("fg")
                        .and_then(|v| v.as_str())
                        .and_then(hex_to_color)
                        .unwrap_or(Color::from_rgb8(240, 243, 255));

                    let muted = json
                        .get("muted")
                        .or_else(|| json.get("fg_muted"))
                        .and_then(|v| v.as_str())
                        .and_then(hex_to_color)
                        .unwrap_or(Color::from_rgb8(140, 150, 175));

                    let success = json
                        .get("profile_green")
                        .or_else(|| json.get("success"))
                        .and_then(|v| v.as_str())
                        .and_then(hex_to_color)
                        .unwrap_or(Color::from_rgb8(52, 211, 153));

                    return Self {
                        bg,
                        card_bg,
                        fg,
                        accent,
                        muted,
                        success,
                    };
                }
            }
        }

        Self::default()
    }
}

pub fn hex_to_color(hex: &str) -> Option<Color> {
    let hex = hex.trim_start_matches('#');
    if hex.len() == 6 {
        let r = u8::from_str_radix(&hex[0..2], 16).ok()?;
        let g = u8::from_str_radix(&hex[2..4], 16).ok()?;
        let b = u8::from_str_radix(&hex[4..6], 16).ok()?;
        Some(Color::from_rgb8(r, g, b))
    } else {
        None
    }
}
