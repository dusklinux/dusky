//! Same Matugen palette source as Dusky Tray, with no desktop toolkit lookup.
use iced::{Color, Theme, theme::Palette};
use std::path::PathBuf;
pub fn path() -> PathBuf {
    std::env::var_os("XDG_CONFIG_HOME")
        .filter(|s| !s.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| crate::output::home().join(".config"))
        .join("matugen/generated/dusky_tray.json")
}
pub fn load() -> Theme {
    let value = std::fs::read(path())
        .ok()
        .and_then(|b| serde_json::from_slice::<serde_json::Value>(&b).ok());
    let color = |key: &str, fallback: Color| {
        value
            .as_ref()
            .and_then(|v| v.get(key))
            .and_then(|v| v.as_str())
            .and_then(hex)
            .unwrap_or(fallback)
    };
    Theme::custom(
        "Dusky",
        Palette {
            background: color("bg", Color::from_rgb8(12, 14, 19)),
            text: color("fg", Color::from_rgb8(226, 226, 233)),
            primary: color("accent", Color::from_rgb8(173, 198, 255)),
            success: color("profile_green", Color::from_rgb8(158, 228, 170)),
            warning: color("tertiary", Color::from_rgb8(222, 188, 223)),
            danger: color("danger", Color::from_rgb8(255, 180, 171)),
        },
    )
}
pub fn hex(s: &str) -> Option<Color> {
    let s = s.trim().trim_start_matches('#');
    if s.len() != 6 || !s.is_ascii() {
        return None;
    }
    Some(Color::from_rgb8(
        u8::from_str_radix(&s[..2], 16).ok()?,
        u8::from_str_radix(&s[2..4], 16).ok()?,
        u8::from_str_radix(&s[4..], 16).ok()?,
    ))
}

pub fn mix(a: Color, b: Color, amount: f32) -> Color {
    Color {
        r: a.r + (b.r - a.r) * amount,
        g: a.g + (b.g - a.g) * amount,
        b: a.b + (b.b - a.b) * amount,
        a: 1.0,
    }
}
pub fn panel(theme: &Theme) -> iced::widget::container::Style {
    let p = theme.palette();
    iced::widget::container::Style {
        background: Some(mix(p.background, p.text, 0.045).into()),
        text_color: Some(p.text),
        border: iced::Border {
            color: p.text.scale_alpha(0.12),
            width: 1.0,
            radius: 16.0.into(),
        },
        shadow: iced::Shadow {
            color: Color::BLACK.scale_alpha(0.25),
            offset: iced::Vector::new(0.0, 6.0),
            blur_radius: 20.0,
        },
        ..Default::default()
    }
}
pub fn control(
    theme: &Theme,
    status: iced::widget::button::Status,
    active: bool,
) -> iced::widget::button::Style {
    let p = theme.palette();
    let hover = matches!(
        status,
        iced::widget::button::Status::Hovered | iced::widget::button::Status::Pressed
    );
    iced::widget::button::Style {
        background: Some(
            if active {
                p.primary.scale_alpha(0.18)
            } else if hover {
                mix(p.background, p.text, 0.10)
            } else {
                Color::TRANSPARENT
            }
            .into(),
        ),
        text_color: if active {
            p.primary
        } else {
            p.text.scale_alpha(if hover { 1.0 } else { 0.82 })
        },
        border: iced::Border {
            radius: 10.0.into(),
            ..Default::default()
        },
        ..Default::default()
    }
}

pub fn input(
    theme: &Theme,
    status: iced::widget::text_input::Status,
) -> iced::widget::text_input::Style {
    let p = theme.palette();
    iced::widget::text_input::Style {
        background: mix(p.background, p.text, 0.04).into(),
        border: iced::Border {
            radius: 8.0.into(),
            width: 1.0,
            color: if matches!(status, iced::widget::text_input::Status::Focused { .. }) {
                p.primary
            } else {
                p.text.scale_alpha(0.12)
            },
        },
        icon: p.text,
        placeholder: p.text.scale_alpha(0.45),
        value: p.text,
        selection: p.primary.scale_alpha(0.25),
    }
}
pub fn slider(theme: &Theme, _: iced::widget::slider::Status) -> iced::widget::slider::Style {
    use iced::widget::slider::{Handle, HandleShape, Rail, Style};
    let p = theme.palette();
    Style {
        rail: Rail {
            backgrounds: (p.primary.into(), p.text.scale_alpha(0.13).into()),
            width: 3.0,
            border: iced::Border {
                radius: 3.0.into(),
                ..Default::default()
            },
        },
        handle: Handle {
            shape: HandleShape::Circle { radius: 5.0 },
            background: p.primary.into(),
            border_width: 0.0,
            border_color: Color::TRANSPARENT,
        },
    }
}
