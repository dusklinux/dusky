//! Centralized Iced 0.14 styling helpers driven exclusively by `AppTheme` (Matugen `dusky.json`).

use crate::theme::{AppTheme, contrasting_fg, mix, saturate};
use iced_core::border::Radius;
use iced_core::{Background, Border, Color, Font, Shadow, Vector};
use iced_widget::{button, container, scrollable, text_editor, text_input};

pub fn regular_font() -> Font {
    Font::with_name(crate::fonts::DEFAULT_FAMILY)
}

pub fn bold_font() -> Font {
    Font {
        weight: iced_core::font::Weight::Bold,
        ..Font::with_name(crate::fonts::DEFAULT_FAMILY)
    }
}

pub fn italic_font() -> Font {
    Font {
        style: iced_core::font::Style::Italic,
        ..Font::with_name(crate::fonts::DEFAULT_FAMILY)
    }
}

pub fn mono_font() -> Font {
    Font::MONOSPACE
}

/// Crisp, Matugen-derived red for Saturday & Sunday headers and day numbers.
pub fn weekend_color(theme: AppTheme) -> Color {
    saturate(theme.danger, 1.5)
}

/// Softened red for out-of-month Saturday & Sunday dates.
pub fn weekend_muted_color(theme: AppTheme) -> Color {
    mix(theme.bg, weekend_color(theme), 0.52)
}

pub fn root_shell(theme: AppTheme) -> impl Fn(&iced_core::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(theme.bg)),
        text_color: Some(theme.fg),
        ..Default::default()
    }
}

pub fn header_bar(theme: AppTheme) -> impl Fn(&iced_core::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(theme.surface)),
        text_color: Some(theme.fg),
        ..Default::default()
    }
}

pub fn sidebar_surface(theme: AppTheme) -> impl Fn(&iced_core::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(theme.sidebar_bg)),
        text_color: Some(theme.fg),
        ..Default::default()
    }
}

pub fn hairline_rule(theme: AppTheme) -> impl Fn(&iced_core::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(theme.grid_line())),
        ..Default::default()
    }
}

pub fn card_surface(theme: AppTheme, radius: f32) -> impl Fn(&iced_core::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(theme.card_bg)),
        text_color: Some(theme.fg),
        border: Border {
            color: theme.surface_border(),
            width: 1.0,
            radius: radius.into(),
        },
        shadow: Shadow {
            color: Color::from_rgba(0.0, 0.0, 0.0, if theme.is_light() { 0.14 } else { 0.42 }),
            offset: Vector::new(0.0, 8.0),
            blur_radius: 22.0,
        },
        ..Default::default()
    }
}

pub fn flat_card(theme: AppTheme, radius: f32) -> impl Fn(&iced_core::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(theme.card_bg)),
        text_color: Some(theme.fg),
        border: Border {
            color: theme.grid_line(),
            width: 1.0,
            radius: radius.into(),
        },
        ..Default::default()
    }
}

pub fn segmented_track(theme: AppTheme) -> impl Fn(&iced_core::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(theme.card_bg)),
        border: Border {
            color: theme.grid_line(),
            width: 1.0,
            radius: 8.0.into(),
        },
        ..Default::default()
    }
}

pub fn active_week_row(theme: AppTheme) -> impl Fn(&iced_core::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(theme.active_week_tint())),
        border: Border {
            radius: 7.0.into(),
            ..Default::default()
        },
        ..Default::default()
    }
}

pub fn backdrop_scrim(theme: AppTheme) -> impl Fn(&iced_core::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(theme.backdrop())),
        ..Default::default()
    }
}

pub fn drop_overlay(theme: AppTheme) -> impl Fn(&iced_core::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(Color {
            a: 0.90,
            ..mix(theme.bg, theme.accent, 0.16)
        })),
        border: Border {
            color: theme.accent,
            width: 2.0,
            radius: 14.0.into(),
        },
        ..Default::default()
    }
}

pub fn today_badge(theme: AppTheme) -> impl Fn(&iced_core::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(theme.accent)),
        text_color: Some(contrasting_fg(theme.accent)),
        border: Border {
            radius: 6.0.into(),
            ..Default::default()
        },
        ..Default::default()
    }
}

pub fn first_day_badge(theme: AppTheme) -> impl Fn(&iced_core::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(theme.card_hover)),
        text_color: Some(theme.fg),
        border: Border {
            color: theme.grid_line(),
            width: 1.0,
            radius: 6.0.into(),
        },
        ..Default::default()
    }
}

pub fn weeknum_chip(theme: AppTheme) -> impl Fn(&iced_core::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(mix(theme.sidebar_bg, theme.fg, 0.055))),
        text_color: Some(mix(theme.sidebar_bg, theme.fg_muted, 0.72)),
        border: Border {
            radius: 4.0.into(),
            ..Default::default()
        },
        ..Default::default()
    }
}

pub fn color_dot(color: Color, radius: f32) -> impl Fn(&iced_core::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(color)),
        border: Border {
            radius: radius.into(),
            ..Default::default()
        },
        ..Default::default()
    }
}

pub fn hollow_dot(
    color: Color,
    radius: f32,
) -> impl Fn(&iced_core::Theme) -> container::Style {
    move |_| container::Style {
        background: None,
        border: Border {
            color,
            width: 1.8,
            radius: radius.into(),
        },
        ..Default::default()
    }
}

pub fn error_banner(theme: AppTheme) -> impl Fn(&iced_core::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(mix(theme.card_bg, theme.danger_bg, 0.45))),
        text_color: Some(theme.danger_fg),
        border: Border {
            color: theme.danger,
            width: 1.0,
            radius: 8.0.into(),
        },
        ..Default::default()
    }
}

pub fn toast_card(
    theme: AppTheme,
    kind: super::ToastKind,
) -> impl Fn(&iced_core::Theme) -> container::Style {
    let accent = match kind {
        super::ToastKind::Info => theme.accent,
        super::ToastKind::Success => theme.profiles[0],
        super::ToastKind::Error => theme.danger,
    };
    move |_| container::Style {
        background: Some(Background::Color(mix(theme.card_bg, accent, 0.14))),
        text_color: Some(theme.fg),
        border: Border {
            color: mix(theme.card_bg, accent, 0.55),
            width: 1.0,
            radius: 10.0.into(),
        },
        shadow: Shadow {
            color: Color::from_rgba(0.0, 0.0, 0.0, 0.35),
            offset: Vector::new(0.0, 6.0),
            blur_radius: 16.0,
        },
        ..Default::default()
    }
}

pub fn toast_action_button(
    theme: AppTheme,
) -> impl Fn(&iced_core::Theme, button::Status) -> button::Style {
    move |_, status| {
        let (bg, fg, border_col) = match status {
            button::Status::Active => (
                mix(theme.card_bg, theme.accent, 0.24),
                theme.accent,
                mix(theme.card_bg, theme.accent, 0.55),
            ),
            button::Status::Hovered => (theme.accent, contrasting_fg(theme.accent), theme.accent),
            button::Status::Pressed => (
                theme.accent_hover,
                contrasting_fg(theme.accent_hover),
                theme.accent_hover,
            ),
            button::Status::Disabled => (theme.card_bg, theme.muted_day_fg(), theme.grid_line()),
        };
        button::Style {
            background: Some(Background::Color(bg)),
            text_color: fg,
            border: Border {
                color: border_col,
                width: 1.0,
                radius: 6.0.into(),
            },
            ..Default::default()
        }
    }
}

pub fn icon_button(
    theme: AppTheme,
    active: bool,
) -> impl Fn(&iced_core::Theme, button::Status) -> button::Style {
    move |_, status| {
        let base_bg = if active {
            Some(Background::Color(mix(theme.surface, theme.accent, 0.22)))
        } else {
            None
        };
        let background = match status {
            button::Status::Active => base_bg,
            button::Status::Hovered => Some(Background::Color(if active {
                mix(theme.surface, theme.accent, 0.30)
            } else {
                theme.card_hover
            })),
            button::Status::Pressed => Some(Background::Color(mix(theme.surface, theme.accent, 0.36))),
            button::Status::Disabled => None,
        };
        button::Style {
            background,
            text_color: if active { theme.accent } else { theme.fg },
            border: Border {
                color: if active {
                    mix(theme.surface, theme.accent, 0.45)
                } else {
                    Color::TRANSPARENT
                },
                width: if active { 1.0 } else { 0.0 },
                radius: 7.0.into(),
            },
            ..Default::default()
        }
    }
}

pub fn close_button(theme: AppTheme) -> impl Fn(&iced_core::Theme, button::Status) -> button::Style {
    move |_, status| {
        let (background, text_color) = match status {
            button::Status::Active => (None, theme.fg_muted),
            button::Status::Hovered => (
                Some(Background::Color(mix(theme.surface, theme.danger, 0.24))),
                theme.danger,
            ),
            button::Status::Pressed => (
                Some(Background::Color(mix(theme.surface, theme.danger, 0.36))),
                theme.danger,
            ),
            button::Status::Disabled => (None, theme.muted_day_fg()),
        };
        button::Style {
            background,
            text_color,
            border: Border {
                radius: 7.0.into(),
                ..Default::default()
            },
            ..Default::default()
        }
    }
}

pub fn secondary_button(
    theme: AppTheme,
) -> impl Fn(&iced_core::Theme, button::Status) -> button::Style {
    move |_, status| {
        let bg = match status {
            button::Status::Active => theme.card_bg,
            button::Status::Hovered => theme.card_hover,
            button::Status::Pressed => mix(theme.card_hover, theme.accent, 0.16),
            button::Status::Disabled => mix(theme.bg, theme.card_bg, 0.5),
        };
        button::Style {
            background: Some(Background::Color(bg)),
            text_color: if matches!(status, button::Status::Disabled) {
                theme.muted_day_fg()
            } else {
                theme.fg
            },
            border: Border {
                color: theme.grid_line(),
                width: 1.0,
                radius: 7.0.into(),
            },
            ..Default::default()
        }
    }
}

pub fn primary_button(
    theme: AppTheme,
) -> impl Fn(&iced_core::Theme, button::Status) -> button::Style {
    move |_, status| {
        let bg = match status {
            button::Status::Active => theme.accent,
            button::Status::Hovered => theme.accent_hover,
            button::Status::Pressed => mix(theme.accent, theme.fg, 0.12),
            button::Status::Disabled => mix(theme.card_bg, theme.accent, 0.28),
        };
        let fg = if matches!(status, button::Status::Disabled) {
            theme.fg_muted
        } else {
            contrasting_fg(bg)
        };
        button::Style {
            background: Some(Background::Color(bg)),
            text_color: fg,
            border: Border {
                radius: 7.0.into(),
                ..Default::default()
            },
            ..Default::default()
        }
    }
}

pub fn danger_button(
    theme: AppTheme,
) -> impl Fn(&iced_core::Theme, button::Status) -> button::Style {
    move |_, status| {
        let (bg, fg, border_col) = match status {
            button::Status::Active => (
                mix(theme.card_bg, theme.danger_bg, 0.52),
                theme.danger_fg,
                mix(theme.card_bg, theme.danger, 0.55),
            ),
            button::Status::Hovered => (theme.danger_bg, theme.danger_fg, theme.danger),
            button::Status::Pressed => (theme.danger, contrasting_fg(theme.danger), theme.danger),
            button::Status::Disabled => (theme.card_bg, theme.muted_day_fg(), theme.grid_line()),
        };
        button::Style {
            background: Some(Background::Color(bg)),
            text_color: fg,
            border: Border {
                color: border_col,
                width: 1.0,
                radius: 7.0.into(),
            },
            ..Default::default()
        }
    }
}

pub fn segment_button(
    theme: AppTheme,
    selected: bool,
) -> impl Fn(&iced_core::Theme, button::Status) -> button::Style {
    move |_, status| {
        let (bg, fg) = if selected {
            let fill = match status {
                button::Status::Hovered => theme.accent_hover,
                _ => theme.accent,
            };
            (Some(Background::Color(fill)), contrasting_fg(fill))
        } else {
            (
                match status {
                    button::Status::Hovered => Some(Background::Color(theme.card_hover)),
                    button::Status::Pressed => {
                        Some(Background::Color(mix(theme.card_bg, theme.accent, 0.16)))
                    }
                    _ => None,
                },
                mix(theme.fg, theme.fg_muted, 0.35),
            )
        };
        button::Style {
            background: bg,
            text_color: fg,
            border: Border {
                radius: 6.0.into(),
                ..Default::default()
            },
            ..Default::default()
        }
    }
}

/// High-contrast standalone toggle pill used inside dialogs (Display prefs, Repeat, Reminder, All Day).
pub fn toggle_chip(
    theme: AppTheme,
    selected: bool,
) -> impl Fn(&iced_core::Theme, button::Status) -> button::Style {
    move |_, status| {
        if selected {
            let fill = match status {
                button::Status::Hovered => theme.accent_hover,
                button::Status::Pressed => mix(theme.accent, theme.fg, 0.12),
                _ => theme.accent,
            };
            button::Style {
                background: Some(Background::Color(fill)),
                text_color: contrasting_fg(fill),
                border: Border {
                    color: fill,
                    width: 1.0,
                    radius: 7.0.into(),
                },
                ..Default::default()
            }
        } else {
            let fill = match status {
                button::Status::Hovered => theme.card_hover,
                button::Status::Pressed => mix(theme.card_hover, theme.accent, 0.18),
                _ => theme.input_bg(),
            };
            button::Style {
                background: Some(Background::Color(fill)),
                text_color: theme.fg,
                border: Border {
                    color: theme.surface_border(),
                    width: 1.0,
                    radius: 7.0.into(),
                },
                ..Default::default()
            }
        }
    }
}

pub fn subtle_row_button(
    theme: AppTheme,
    selected: bool,
) -> impl Fn(&iced_core::Theme, button::Status) -> button::Style {
    move |_, status| {
        let bg = match (selected, status) {
            (true, button::Status::Hovered) => {
                Some(Background::Color(mix(theme.sidebar_bg, theme.accent, 0.24)))
            }
            (true, _) => Some(Background::Color(mix(theme.sidebar_bg, theme.accent, 0.16))),
            (false, button::Status::Hovered) => Some(Background::Color(theme.card_hover)),
            (false, button::Status::Pressed) => {
                Some(Background::Color(mix(theme.sidebar_bg, theme.accent, 0.14)))
            }
            (false, _) => None,
        };
        button::Style {
            background: bg,
            text_color: theme.fg,
            border: Border {
                color: if selected {
                    mix(theme.sidebar_bg, theme.accent, 0.45)
                } else {
                    Color::TRANSPARENT
                },
                width: if selected { 1.0 } else { 0.0 },
                radius: 8.0.into(),
            },
            ..Default::default()
        }
    }
}

pub fn mini_day_button(
    theme: AppTheme,
    selected: bool,
    is_today: bool,
    in_month: bool,
    is_weekend: bool,
) -> impl Fn(&iced_core::Theme, button::Status) -> button::Style {
    move |_, status| {
        let (bg, fg, border_col, border_w) = if selected {
            let fill = match status {
                button::Status::Hovered => theme.accent_hover,
                _ => theme.accent,
            };
            (
                Some(Background::Color(fill)),
                contrasting_fg(fill),
                Color::TRANSPARENT,
                0.0,
            )
        } else {
            let fg = if is_today {
                theme.accent
            } else if !in_month {
                if is_weekend {
                    weekend_muted_color(theme)
                } else {
                    theme.muted_day_fg()
                }
            } else if is_weekend {
                weekend_color(theme)
            } else {
                theme.fg
            };
            let bg = match status {
                button::Status::Hovered => Some(Background::Color(theme.card_hover)),
                button::Status::Pressed => {
                    Some(Background::Color(mix(theme.sidebar_bg, theme.accent, 0.20)))
                }
                _ => None,
            };
            let (b_col, b_w) = if is_today {
                (mix(theme.sidebar_bg, theme.accent, 0.65), 1.2)
            } else {
                (Color::TRANSPARENT, 0.0)
            };
            (bg, fg, b_col, b_w)
        };

        button::Style {
            background: bg,
            text_color: fg,
            border: Border {
                color: border_col,
                width: border_w,
                radius: 999.0.into(),
            },
            ..Default::default()
        }
    }
}

pub fn event_pill_button(
    theme: AppTheme,
    event_color: Color,
    past: bool,
    continues_left: bool,
    continues_right: bool,
) -> impl Fn(&iced_core::Theme, button::Status) -> button::Style {
    let radius = Radius {
        top_left: if continues_left { 0.0 } else { 6.0 },
        bottom_left: if continues_left { 0.0 } else { 6.0 },
        top_right: if continues_right { 0.0 } else { 6.0 },
        bottom_right: if continues_right { 0.0 } else { 6.0 },
    };

    move |_, status| {
        let hovered = matches!(status, button::Status::Hovered | button::Status::Pressed);
        let palette = theme.event_pill_colors(event_color, past, hovered);
        button::Style {
            background: Some(Background::Color(palette.bg)),
            text_color: palette.text,
            border: Border {
                color: if hovered {
                    palette.border
                } else {
                    Color::TRANSPARENT
                },
                width: if hovered { 1.0 } else { 0.0 },
                radius,
            },
            ..Default::default()
        }
    }
}

pub fn timed_block_button(
    theme: AppTheme,
    event_color: Color,
    past: bool,
    continues_prev: bool,
    continues_next: bool,
) -> impl Fn(&iced_core::Theme, button::Status) -> button::Style {
    let radius = Radius {
        top_left: if continues_prev { 0.0 } else { 6.0 },
        top_right: if continues_prev { 0.0 } else { 6.0 },
        bottom_left: if continues_next { 0.0 } else { 6.0 },
        bottom_right: if continues_next { 0.0 } else { 6.0 },
    };

    move |_, status| {
        let hovered = matches!(status, button::Status::Hovered | button::Status::Pressed);
        let palette = theme.event_pill_colors(event_color, past, hovered);
        button::Style {
            background: Some(Background::Color(palette.bg)),
            text_color: palette.text,
            border: Border {
                color: if hovered {
                    palette.border
                } else {
                    Color::TRANSPARENT
                },
                width: if hovered { 1.0 } else { 0.0 },
                radius,
            },
            ..Default::default()
        }
    }
}

pub fn swatch_button(
    theme: AppTheme,
    color: Color,
    selected: bool,
) -> impl Fn(&iced_core::Theme, button::Status) -> button::Style {
    move |_, status| {
        let hovered = matches!(status, button::Status::Hovered);
        button::Style {
            background: Some(Background::Color(color)),
            text_color: contrasting_fg(color),
            border: Border {
                color: if selected {
                    theme.fg
                } else if hovered {
                    theme.fg_muted
                } else {
                    Color::TRANSPARENT
                },
                width: if selected {
                    2.0
                } else if hovered {
                    1.0
                } else {
                    0.0
                },
                radius: 999.0.into(),
            },
            ..Default::default()
        }
    }
}

pub fn text_input_style(
    theme: AppTheme,
) -> impl Fn(&iced_core::Theme, text_input::Status) -> text_input::Style {
    move |_, status| {
        let border_color = match status {
            text_input::Status::Focused { .. } => theme.accent,
            text_input::Status::Hovered => theme.surface_border(),
            text_input::Status::Active => theme.grid_line(),
            text_input::Status::Disabled => theme.grid_line_subtle(),
        };
        text_input::Style {
            background: Background::Color(theme.input_bg()),
            border: Border {
                color: border_color,
                width: if matches!(status, text_input::Status::Focused { .. }) {
                    1.5
                } else {
                    1.0
                },
                radius: 8.0.into(),
            },
            icon: theme.fg_muted,
            placeholder: theme.muted_day_fg(),
            value: theme.fg,
            selection: mix(theme.input_bg(), theme.accent, 0.35),
        }
    }
}

pub fn text_editor_style(
    theme: AppTheme,
) -> impl Fn(&iced_core::Theme, text_editor::Status) -> text_editor::Style {
    move |iced_theme, status| {
        let mut style = text_editor::default(iced_theme, status);
        style.background = Background::Color(theme.input_bg());
        style.border = Border {
            color: match status {
                text_editor::Status::Focused { .. } => theme.accent,
                text_editor::Status::Hovered => theme.surface_border(),
                _ => theme.grid_line(),
            },
            width: if matches!(status, text_editor::Status::Focused { .. }) {
                1.5
            } else {
                1.0
            },
            radius: 8.0.into(),
        };
        style.placeholder = theme.muted_day_fg();
        style.value = theme.fg;
        style.selection = mix(theme.input_bg(), theme.accent, 0.35);
        style
    }
}

pub fn clean_scrollable(
    theme: AppTheme,
) -> impl Fn(&iced_core::Theme, scrollable::Status) -> scrollable::Style {
    move |iced_theme, status| {
        let mut style = scrollable::default(iced_theme, status);
        style.container = container::Style::default();
        style.gap = None;

        let hovered = matches!(
            status,
            scrollable::Status::Hovered {
                is_vertical_scrollbar_hovered: true,
                ..
            } | scrollable::Status::Dragged {
                is_vertical_scrollbar_dragged: true,
                ..
            }
        );

        let scroller_color = if hovered {
            mix(theme.bg, theme.accent, 0.65)
        } else {
            mix(theme.bg, theme.border, 0.55)
        };

        style.vertical_rail.background = None;
        style.vertical_rail.border = Border::default();
        style.vertical_rail.scroller.background = Background::Color(scroller_color);
        style.vertical_rail.scroller.border = Border {
            radius: 4.0.into(),
            ..Default::default()
        };

        style.horizontal_rail.background = None;
        style.horizontal_rail.border = Border::default();
        style.horizontal_rail.scroller.background = Background::Color(scroller_color);
        style.horizontal_rail.scroller.border = Border {
            radius: 4.0.into(),
            ..Default::default()
        };

        style
    }
}
