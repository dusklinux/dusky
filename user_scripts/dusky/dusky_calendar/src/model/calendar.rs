//! Local calendar metadata and Matugen-harmonized color bindings.

use crate::theme::{AppTheme, SemanticToken, color_to_hex, hex_to_color};
use iced_core::Color;
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use uuid::Uuid;

/// A calendar's accent color: either dynamically bound to a Matugen semantic token
/// (re-harmonizing automatically on wallpaper change) or pinned to a custom RGB hex color.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum CalendarColor {
    Token(SemanticToken),
    Custom([u8; 3]),
}

impl Default for CalendarColor {
    fn default() -> Self {
        Self::Token(SemanticToken::Accent)
    }
}

impl CalendarColor {
    pub fn resolve(self, theme: &AppTheme) -> Color {
        match self {
            Self::Token(token) => theme.resolve_token(token),
            Self::Custom([r, g, b]) => Color::from_rgb8(r, g, b),
        }
    }

    pub fn from_hex(raw: &str) -> Option<Self> {
        let color = hex_to_color(raw)?;
        Some(Self::Custom([
            (color.r * 255.0).round() as u8,
            (color.g * 255.0).round() as u8,
            (color.b * 255.0).round() as u8,
        ]))
    }

    pub fn to_hex(self, theme: &AppTheme) -> String {
        color_to_hex(self.resolve(theme))
    }

    pub fn custom_hex(self) -> Option<String> {
        match self {
            Self::Custom([r, g, b]) => Some(format!("#{r:02x}{g:02x}{b:02x}")),
            Self::Token(_) => None,
        }
    }

    pub fn token(self) -> Option<SemanticToken> {
        match self {
            Self::Token(tok) => Some(tok),
            Self::Custom(_) => None,
        }
    }

    pub fn parse_serialized(raw: &str) -> Option<Self> {
        let trimmed = raw.trim();
        let token_name = trimmed.strip_prefix("token:").unwrap_or(trimmed);
        let parsed_token = match token_name.to_ascii_lowercase().as_str() {
            "accent" => Some(SemanticToken::Accent),
            "secondary" => Some(SemanticToken::Secondary),
            "tertiary" => Some(SemanticToken::Tertiary),
            "profile_green" | "green" | "mint" => Some(SemanticToken::ProfileGreen),
            "profile_blue" | "blue" | "sky" => Some(SemanticToken::ProfileBlue),
            "profile_red" | "red" | "rose" => Some(SemanticToken::ProfileRed),
            _ => None,
        };
        if let Some(tok) = parsed_token {
            return Some(Self::Token(tok));
        }
        Self::from_hex(trimmed)
    }

    pub fn to_serialized_string(self) -> String {
        match self {
            Self::Token(token) => {
                let name = match token {
                    SemanticToken::Accent => "accent",
                    SemanticToken::Secondary => "secondary",
                    SemanticToken::Tertiary => "tertiary",
                    SemanticToken::ProfileGreen => "profile_green",
                    SemanticToken::ProfileBlue => "profile_blue",
                    SemanticToken::ProfileRed => "profile_red",
                };
                format!("token:{name}")
            }
            Self::Custom([r, g, b]) => format!("#{r:02x}{g:02x}{b:02x}"),
        }
    }
}

impl Serialize for CalendarColor {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        serializer.serialize_str(&self.to_serialized_string())
    }
}

impl<'de> Deserialize<'de> for CalendarColor {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        let raw = String::deserialize(deserializer)?;
        Self::parse_serialized(&raw)
            .ok_or_else(|| serde::de::Error::custom(format!("invalid calendar color: {raw}")))
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CalendarMeta {
    pub id: String,
    pub name: String,
    #[serde(default)]
    pub color: CalendarColor,
    #[serde(default = "default_true")]
    pub visible: bool,
    #[serde(default)]
    pub is_default: bool,
    pub file_name: String,
}

fn default_true() -> bool {
    true
}

impl CalendarMeta {
    pub fn new(name: impl Into<String>, color: CalendarColor, is_default: bool) -> Self {
        let id = Uuid::new_v4().to_string();
        let file_name = format!("{id}.ics");
        Self {
            id,
            name: name.into(),
            color,
            visible: true,
            is_default,
            file_name,
        }
    }

    pub fn default_personal() -> Self {
        Self::new("Personal", CalendarColor::Token(SemanticToken::Accent), true)
    }

    pub fn resolved_color(&self, theme: &AppTheme) -> Color {
        self.color.resolve(theme)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn calendar_color_round_trips_in_toml() {
        #[derive(Debug, PartialEq, Serialize, Deserialize)]
        struct Wrapper {
            token: CalendarColor,
            custom: CalendarColor,
        }

        let original = Wrapper {
            token: CalendarColor::Token(SemanticToken::ProfileGreen),
            custom: CalendarColor::Custom([0x42, 0x87, 0xf5]),
        };

        let encoded = toml::to_string(&original).unwrap();
        assert!(encoded.contains("token:profile_green"));
        assert!(encoded.contains("#4287f5"));

        let decoded: Wrapper = toml::from_str(&encoded).unwrap();
        assert_eq!(decoded, original);
    }

    #[test]
    fn default_personal_calendar_uses_accent_token() {
        let cal = CalendarMeta::default_personal();
        assert_eq!(cal.name, "Personal");
        assert!(cal.visible);
        assert!(cal.is_default);
        assert!(cal.file_name.ends_with(".ics"));
        assert_eq!(cal.color, CalendarColor::Token(SemanticToken::Accent));
    }
}
