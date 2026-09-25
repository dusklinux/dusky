use iced::Color;
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
            accent: Color::from_rgb8(255, 182, 141), // #ffb68d peach / warm amber (matching skwd-wall)
            muted: Color::from_rgb8(140, 150, 175),
            success: Color::from_rgb8(52, 211, 153),
        }
    }
}

impl AppTheme {
    pub fn load() -> Self {
        let home = std::env::var("HOME").map(PathBuf::from).unwrap_or_default();
        let matugen_json = home.join(".config/matugen/generated/dusky_tui.json");

        if let Ok(content) = fs::read_to_string(&matugen_json) {
            if let Ok(json) = serde_json::from_str::<serde_json::Value>(&content) {
                let accent = json
                    .get("accent")
                    .and_then(|v| v.as_str())
                    .and_then(hex_to_color)
                    .unwrap_or(Color::from_rgb8(255, 182, 141));

                let bg = json
                    .get("bg")
                    .and_then(|v| v.as_str())
                    .and_then(hex_to_color)
                    .unwrap_or(Color::from_rgb8(10, 12, 16));

                let fg = json
                    .get("fg")
                    .and_then(|v| v.as_str())
                    .and_then(hex_to_color)
                    .unwrap_or(Color::from_rgb8(240, 243, 255));

                let muted = json
                    .get("muted")
                    .and_then(|v| v.as_str())
                    .and_then(hex_to_color)
                    .unwrap_or(Color::from_rgb8(140, 150, 175));

                let success = json
                    .get("success")
                    .and_then(|v| v.as_str())
                    .and_then(hex_to_color)
                    .unwrap_or(Color::from_rgb8(52, 211, 153));

                return Self {
                    bg,
                    card_bg: Color::from_rgb8(16, 18, 26),
                    fg,
                    accent,
                    muted,
                    success,
                };
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
