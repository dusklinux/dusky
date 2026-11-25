use dusky_casts::db;
use iced::{
    Background, Border, Color, Shadow, Theme, Vector,
    theme::Palette,
    widget::{button, container, pick_list, scrollable, slider, text_input},
};
use serde_json::Value;
use std::{
    path::PathBuf,
    time::{Duration, Instant, SystemTime},
};

#[derive(Clone, Copy, Debug)]
pub struct Colors {
    pub bg: Color,
    pub sidebar: Color,
    pub surface: Color,
    pub card: Color,
    pub hover: Color,
    pub border: Color,
    pub fg: Color,
    pub muted: Color,
    pub accent: Color,
    pub on_accent: Color,
    pub danger: Color,
}

#[derive(Clone, Copy, Debug)]
pub enum Button {
    Accent,
    Selected,
    Soft,
    Quiet,
    Danger,
}

pub struct Appearance {
    pub colors: Colors,
    pub iced: Theme,
    checked_at: Instant,
    stamp: Option<(PathBuf, Option<SystemTime>, u64)>,
}

impl Default for Colors {
    fn default() -> Self {
        Self {
            bg: hex("#140c0a").unwrap(),
            sidebar: hex("#140c0a").unwrap(),
            surface: hex("#140c0a").unwrap(),
            card: hex("#1a110f").unwrap(),
            hover: hex("#201513").unwrap(),
            border: hex("#53433f").unwrap(),
            fg: hex("#f1dfda").unwrap(),
            muted: hex("#d8c2bb").unwrap(),
            accent: hex("#ffb59d").unwrap(),
            on_accent: hex("#55200c").unwrap(),
            danger: hex("#ffb4ab").unwrap(),
        }
    }
}

fn palette_path() -> PathBuf {
    if let Some(custom) = std::env::var_os("DUSKY_CASTS_THEME").filter(|s| !s.is_empty()) {
        return PathBuf::from(custom);
    }
    let dir = db::xdg("XDG_CONFIG_HOME", ".config").join("matugen/generated");
    let husky = dir.join("dusky.json");
    if husky.is_file() {
        return husky;
    }
    let center = dir.join("dusky_center.json");
    if center.is_file() {
        return center;
    }
    husky
}

fn hex(s: &str) -> Option<Color> {
    let s = s.trim().strip_prefix('#').unwrap_or(s.trim());
    if s.len() != 6 && s.len() != 8 {
        return None;
    }
    let r = u8::from_str_radix(&s[0..2], 16).ok()?;
    let g = u8::from_str_radix(&s[2..4], 16).ok()?;
    let b = u8::from_str_radix(&s[4..6], 16).ok()?;
    let a = if s.len() == 8 {
        u8::from_str_radix(&s[6..8], 16).ok()? as f32 / 255.0
    } else {
        1.0
    };
    Some(Color::from_rgba(
        r as f32 / 255.0,
        g as f32 / 255.0,
        b as f32 / 255.0,
        a,
    ))
}

fn pick_color(v: &Value, keys: &[&str], fallback: Color) -> Color {
    keys.iter()
        .find_map(|k| v.get(*k).and_then(Value::as_str).and_then(hex))
        .unwrap_or(fallback)
}

fn parse_colors(text: &str) -> Option<Colors> {
    let v: Value = serde_json::from_str(text).ok()?;
    let d = Colors::default();
    let bg = hex(v.get("bg")?.as_str()?)?;
    let fg = hex(v.get("fg")?.as_str()?)?;
    let accent = pick_color(&v, &["accent", "primary"], d.accent);
    Some(Colors {
        bg,
        sidebar: pick_color(&v, &["sidebar_bg", "surface", "bg"], bg),
        surface: pick_color(&v, &["surface", "sidebar_bg", "bg"], bg),
        card: pick_color(&v, &["card_bg", "surface"], mix(bg, fg, 0.05)),
        hover: pick_color(&v, &["card_hover", "accent_hover"], mix(bg, fg, 0.09)),
        border: pick_color(&v, &["border", "muted"], mix(bg, fg, 0.22)),
        fg,
        muted: pick_color(&v, &["fg_muted", "secondary", "muted"], mix(bg, fg, 0.72)),
        accent,
        on_accent: pick_color(&v, &["accent_fg"], bg),
        danger: pick_color(&v, &["danger", "error", "profile_red"], d.danger),
    })
}

fn to_iced(c: Colors) -> Theme {
    Theme::custom(
        "Dusky",
        Palette {
            background: c.bg,
            text: c.fg,
            primary: c.accent,
            success: c.accent,
            warning: c.accent,
            danger: c.danger,
        },
    )
}

impl Appearance {
    pub fn load() -> Self {
        let colors = Colors::default();
        let mut app = Self {
            colors,
            iced: to_iced(colors),
            checked_at: Instant::now() - Duration::from_secs(10),
            stamp: None,
        };
        app.refresh();
        app
    }
    pub fn refresh(&mut self) {
        if self.checked_at.elapsed() < Duration::from_millis(500) {
            return;
        }
        self.checked_at = Instant::now();
        let path = palette_path();
        let Ok(meta) = std::fs::metadata(&path) else {
            return;
        };
        let current = (path.clone(), meta.modified().ok(), meta.len());
        if self.stamp.as_ref() == Some(&current) {
            return;
        }
        if let Ok(content) = std::fs::read_to_string(&path)
            && let Some(colors) = parse_colors(&content)
        {
            self.colors = colors;
            self.iced = to_iced(colors);
            self.stamp = Some(current);
        }
    }
}

pub fn mix(a: Color, b: Color, factor: f32) -> Color {
    let t = factor.clamp(0.0, 1.0);
    Color::from_rgba(
        a.r + (b.r - a.r) * t,
        a.g + (b.g - a.g) * t,
        a.b + (b.b - a.b) * t,
        a.a + (b.a - a.a) * t,
    )
}

pub fn panel(c: Colors, bg: Color, radius: f32) -> container::Style {
    container::Style {
        background: Some(Background::Color(bg)),
        text_color: Some(c.fg),
        border: Border {
            color: Color::TRANSPARENT,
            width: 0.0,
            radius: radius.into(),
        },
        ..Default::default()
    }
}

pub fn drawer_box(c: Colors) -> container::Style {
    container::Style {
        background: Some(Background::Color(mix(c.card, c.hover, 0.42))),
        text_color: Some(c.fg),
        border: Border {
            color: mix(c.card, c.border, 0.72),
            width: 1.0,
            radius: 14.0.into(),
        },
        shadow: Shadow {
            color: Color::from_rgba(0.0, 0.0, 0.0, 0.48),
            offset: Vector::new(0.0, 8.0),
            blur_radius: 26.0,
        },
        ..Default::default()
    }
}

pub fn scrollbar() -> scrollable::Scrollbar {
    scrollable::Scrollbar::new()
        .width(8)
        .scroller_width(8)
        .margin(2)
}

pub fn scroll(theme: &Theme, c: Colors, status: scrollable::Status) -> scrollable::Style {
    let mut style = scrollable::default(theme, status);
    let (v_scroller, v_rail) = match status {
        scrollable::Status::Dragged {
            is_vertical_scrollbar_dragged: true,
            ..
        } => (c.accent, Some(Background::Color(mix(c.bg, c.hover, 0.72)))),
        scrollable::Status::Hovered {
            is_vertical_scrollbar_hovered: true,
            ..
        } => (
            mix(c.muted, c.accent, 0.55),
            Some(Background::Color(mix(c.bg, c.hover, 0.45))),
        ),
        scrollable::Status::Hovered { .. } => (mix(c.bg, c.muted, 0.48), None),
        _ => (mix(c.bg, c.border, 0.58), None),
    };
    style.vertical_rail.background = v_rail;
    style.vertical_rail.border = Border {
        color: Color::TRANSPARENT,
        width: 0.0,
        radius: 4.0.into(),
    };
    style.vertical_rail.scroller.background = Background::Color(v_scroller);
    style.vertical_rail.scroller.border = Border {
        color: Color::TRANSPARENT,
        width: 0.0,
        radius: 4.0.into(),
    };
    style.horizontal_rail.background = None;
    style.horizontal_rail.border = Border {
        color: Color::TRANSPARENT,
        width: 0.0,
        radius: 4.0.into(),
    };
    style.horizontal_rail.scroller.background = Background::Color(mix(c.bg, c.border, 0.58));
    style.horizontal_rail.scroller.border = Border {
        color: Color::TRANSPARENT,
        width: 0.0,
        radius: 4.0.into(),
    };
    style
}

pub fn button_style(c: Colors, kind: Button, status: button::Status) -> button::Style {
    let hovered = matches!(status, button::Status::Hovered | button::Status::Pressed);
    let (bg, fg, border) = match kind {
        Button::Accent => (
            if hovered {
                mix(c.accent, c.fg, 0.14)
            } else {
                c.accent
            },
            c.on_accent,
            Color::TRANSPARENT,
        ),
        Button::Selected => (
            mix(c.card, c.accent, if hovered { 0.22 } else { 0.15 }),
            c.fg,
            mix(c.card, c.accent, 0.35),
        ),
        Button::Soft => (
            if hovered {
                c.hover
            } else {
                mix(c.card, c.fg, 0.04)
            },
            c.fg,
            mix(c.bg, c.border, 0.35),
        ),
        Button::Quiet => (
            if hovered { c.hover } else { Color::TRANSPARENT },
            if hovered { c.fg } else { c.muted },
            Color::TRANSPARENT,
        ),
        Button::Danger => (
            mix(c.card, c.danger, if hovered { 0.22 } else { 0.12 }),
            c.danger,
            mix(c.card, c.danger, 0.3),
        ),
    };
    button::Style {
        background: Some(Background::Color(bg)),
        text_color: fg,
        border: Border {
            color: border,
            width: if border == Color::TRANSPARENT {
                0.0
            } else {
                1.0
            },
            radius: 9.0.into(),
        },
        ..Default::default()
    }
}

pub fn input(c: Colors, status: text_input::Status) -> text_input::Style {
    let focused = matches!(status, text_input::Status::Focused { .. });
    text_input::Style {
        background: Background::Color(c.card),
        border: Border {
            color: if focused {
                c.accent
            } else {
                mix(c.bg, c.border, 0.45)
            },
            width: 1.0,
            radius: 10.0.into(),
        },
        icon: c.muted,
        placeholder: mix(c.bg, c.muted, 0.65),
        value: c.fg,
        selection: mix(c.card, c.accent, 0.3),
    }
}

pub fn pick(c: Colors, status: pick_list::Status) -> pick_list::Style {
    let active = matches!(
        status,
        pick_list::Status::Hovered | pick_list::Status::Opened { .. }
    );
    pick_list::Style {
        text_color: c.fg,
        placeholder_color: c.muted,
        handle_color: c.muted,
        background: Background::Color(if active { c.hover } else { c.card }),
        border: Border {
            radius: 8.0.into(),
            width: 1.0,
            color: mix(c.bg, c.border, 0.45),
        },
    }
}

pub fn slider_style(c: Colors, status: slider::Status) -> slider::Style {
    let active = matches!(status, slider::Status::Hovered | slider::Status::Dragged);
    slider::Style {
        rail: slider::Rail {
            backgrounds: (
                Background::Color(c.accent),
                Background::Color(mix(c.card, c.border, 0.45)),
            ),
            width: 4.0,
            border: Border {
                radius: 2.0.into(),
                width: 0.0,
                color: Color::TRANSPARENT,
            },
        },
        handle: slider::Handle {
            shape: slider::HandleShape::Circle {
                radius: if active { 6.5 } else { 5.0 },
            },
            background: Background::Color(c.accent),
            border_width: 0.0,
            border_color: Color::TRANSPARENT,
        },
    }
}
