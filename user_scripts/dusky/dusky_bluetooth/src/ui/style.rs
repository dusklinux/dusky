//! Centralized Iced 0.14 styling helpers driven exclusively by `AppTheme` (Matugen `dusky.json`).

use crate::theme::{AppTheme, contrasting_fg, mix};
use crate::ui::{Element, ToastKind};
use iced_core::{Background, Border, Color, Font, Padding, Shadow, Vector, mouse};
use iced_widget::{button, container, mouse_area, scrollable, text, text_input};
use std::time::Duration;

const TOOLTIP_DELAY: Duration = Duration::from_millis(450);

pub fn regular_font() -> Font {
    Font::with_name(crate::fonts::DEFAULT_FAMILY)
}

pub fn bold_font() -> Font {
    Font {
        weight: iced_core::font::Weight::Bold,
        ..Font::with_name(crate::fonts::DEFAULT_FAMILY)
    }
}

pub fn mono_font() -> Font {
    Font::MONOSPACE
}

pub fn pointer<'a, Message: 'a + Clone>(
    content: impl Into<Element<'a, Message>>,
) -> Element<'a, Message> {
    mouse_area(content)
        .interaction(mouse::Interaction::Pointer)
        .into()
}

pub fn pointer_if<'a, Message: 'a + Clone>(
    enabled: bool,
    content: impl Into<Element<'a, Message>>,
) -> Element<'a, Message> {
    if enabled {
        pointer(content)
    } else {
        content.into()
    }
}

pub fn root_shell(theme: AppTheme) -> impl Fn(&iced_core::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(theme.bg)),
        text_color: Some(theme.fg),
        border: Border {
            color: theme.grid_line(),
            width: 1.0,
            radius: 0.0.into(),
        },
        ..Default::default()
    }
}

pub fn header_bar(theme: AppTheme) -> impl Fn(&iced_core::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(theme.chrome_bg())),
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

pub fn accent_hairline(theme: AppTheme) -> impl Fn(&iced_core::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(theme.accent)),
        ..Default::default()
    }
}

pub fn traffic_pill(theme: AppTheme) -> impl Fn(&iced_core::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(mix(
            theme.chrome_bg(),
            theme.fg,
            if theme.is_light() { 0.045 } else { 0.04 },
        ))),
        text_color: Some(theme.fg_muted),
        border: Border {
            color: theme.grid_line(),
            width: 1.0,
            radius: 6.0.into(),
        },
        ..Default::default()
    }
}

pub fn subtle_rule(theme: AppTheme) -> impl Fn(&iced_core::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(theme.grid_line_subtle())),
        ..Default::default()
    }
}

pub fn card_surface(
    theme: AppTheme,
    radius: f32,
) -> impl Fn(&iced_core::Theme) -> container::Style {
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

pub fn device_card(
    theme: AppTheme,
    connected: bool,
    expanded: bool,
) -> impl Fn(&iced_core::Theme) -> container::Style {
    move |_| {
        let border_color = if connected {
            mix(theme.card_bg, theme.accent, 0.48)
        } else if expanded {
            theme.surface_border()
        } else {
            theme.grid_line()
        };
        let bg = if connected {
            mix(
                theme.card_bg,
                theme.accent,
                if theme.is_light() { 0.06 } else { 0.05 },
            )
        } else {
            theme.card_bg
        };
        container::Style {
            background: Some(Background::Color(bg)),
            text_color: Some(theme.fg),
            border: Border {
                color: border_color,
                width: 1.0,
                radius: 10.0.into(),
            },
            ..Default::default()
        }
    }
}

pub fn icon_tile(
    theme: AppTheme,
    highlighted: bool,
) -> impl Fn(&iced_core::Theme) -> container::Style {
    move |_| {
        let bg = if highlighted {
            mix(
                theme.card_bg,
                theme.accent,
                if theme.is_light() { 0.22 } else { 0.20 },
            )
        } else {
            mix(
                theme.card_bg,
                theme.fg,
                if theme.is_light() { 0.06 } else { 0.055 },
            )
        };
        let border_col = if highlighted {
            mix(theme.card_bg, theme.accent, 0.45)
        } else {
            theme.grid_line_subtle()
        };
        container::Style {
            background: Some(Background::Color(bg)),
            border: Border {
                color: border_col,
                width: 1.0,
                radius: 9.0.into(),
            },
            ..Default::default()
        }
    }
}

pub fn status_pill(
    theme: AppTheme,
    semantic: Color,
) -> impl Fn(&iced_core::Theme) -> container::Style {
    let badge = theme.status_badge(semantic);
    move |_| container::Style {
        background: Some(Background::Color(badge.bg)),
        text_color: Some(badge.fg),
        border: Border {
            color: badge.border,
            width: 1.0,
            radius: 6.0.into(),
        },
        ..Default::default()
    }
}

pub fn neutral_pill(theme: AppTheme) -> impl Fn(&iced_core::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(mix(
            theme.card_bg,
            theme.fg,
            if theme.is_light() { 0.055 } else { 0.05 },
        ))),
        text_color: Some(theme.fg_muted),
        border: Border {
            color: theme.grid_line(),
            width: 1.0,
            radius: 6.0.into(),
        },
        ..Default::default()
    }
}

pub fn passkey_box(theme: AppTheme) -> impl Fn(&iced_core::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(theme.input_bg())),
        text_color: Some(theme.fg),
        border: Border {
            color: mix(theme.card_bg, theme.accent, 0.55),
            width: 1.0,
            radius: 10.0.into(),
        },
        ..Default::default()
    }
}

pub fn modal_section_box(theme: AppTheme) -> impl Fn(&iced_core::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(mix(
            theme.card_bg,
            theme.bg,
            if theme.is_light() { 0.35 } else { 0.42 },
        ))),
        text_color: Some(theme.fg),
        border: Border {
            color: theme.grid_line(),
            width: 1.0,
            radius: 8.0.into(),
        },
        ..Default::default()
    }
}

pub fn info_row_button(
    theme: AppTheme,
) -> impl Fn(&iced_core::Theme, button::Status) -> button::Style {
    move |_, status| {
        let background = match status {
            button::Status::Active | button::Status::Disabled => None,
            button::Status::Hovered => Some(Background::Color(theme.card_hover)),
            button::Status::Pressed => Some(Background::Color(mix(
                theme.card_hover,
                theme.accent,
                0.22,
            ))),
        };
        button::Style {
            background,
            text_color: theme.fg,
            border: Border {
                radius: 5.0.into(),
                ..Default::default()
            },
            ..Default::default()
        }
    }
}


pub fn notice_banner(
    theme: AppTheme,
    is_error: bool,
) -> impl Fn(&iced_core::Theme) -> container::Style {
    let badge = theme.status_badge(if is_error {
        theme.danger
    } else {
        theme.warning
    });
    move |_| container::Style {
        background: Some(Background::Color(badge.bg)),
        text_color: Some(if is_error { badge.fg } else { theme.fg }),
        border: Border {
            color: badge.border,
            width: 1.0,
            radius: 8.0.into(),
        },
        ..Default::default()
    }
}

pub fn toast_card(
    theme: AppTheme,
    kind: ToastKind,
) -> impl Fn(&iced_core::Theme) -> container::Style {
    let semantic = match kind {
        ToastKind::Info => theme.accent,
        ToastKind::Success => theme.success,
        ToastKind::Error => theme.danger,
    };
    move |_| container::Style {
        background: Some(Background::Color(mix(theme.card_bg, semantic, 0.16))),
        text_color: Some(theme.fg),
        border: Border {
            color: mix(theme.card_bg, semantic, 0.55),
            width: 1.0,
            radius: 10.0.into(),
        },
        shadow: Shadow {
            color: Color::from_rgba(0.0, 0.0, 0.0, if theme.is_light() { 0.16 } else { 0.40 }),
            offset: Vector::new(0.0, 6.0),
            blur_radius: 16.0,
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

pub fn tooltip_surface(theme: AppTheme) -> impl Fn(&iced_core::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(theme.card_bg)),
        text_color: Some(theme.fg),
        border: Border {
            color: theme.surface_border(),
            width: 1.0,
            radius: 8.0.into(),
        },
        shadow: Shadow {
            color: Color::from_rgba(0.0, 0.0, 0.0, if theme.is_light() { 0.14 } else { 0.44 }),
            offset: Vector::new(0.0, 4.0),
            blur_radius: 12.0,
        },
        ..Default::default()
    }
}

pub fn rich_tooltip<'a, Message: 'a>(
    theme: AppTheme,
    content: impl Into<Element<'a, Message>>,
    tip: impl Into<Element<'a, Message>>,
    position: iced_widget::tooltip::Position,
) -> Element<'a, Message> {
    let tip_box = container(tip)
        .padding(Padding::from([5, 9]))
        .max_width(280.0);

    iced_widget::tooltip(content, tip_box, position)
        .gap(6.0)
        .padding(0.0)
        .delay(TOOLTIP_DELAY)
        .snap_within_viewport(true)
        .style(tooltip_surface(theme))
        .into()
}

pub fn tooltip<'a, Message: 'a>(
    theme: AppTheme,
    content: impl Into<Element<'a, Message>>,
    label: impl Into<String>,
    position: iced_widget::tooltip::Position,
) -> Element<'a, Message> {
    rich_tooltip(
        theme,
        content,
        text(label.into()).size(12).color(theme.fg),
        position,
    )
}

pub fn icon_button(
    theme: AppTheme,
    active: bool,
) -> impl Fn(&iced_core::Theme, button::Status) -> button::Style {
    let badge = theme.status_badge(theme.accent);
    move |_, status| {
        let background = match status {
            button::Status::Active => active.then_some(Background::Color(badge.bg)),
            button::Status::Hovered => Some(Background::Color(if active {
                mix(badge.bg, theme.accent, 0.14)
            } else {
                theme.card_hover
            })),
            button::Status::Pressed => Some(Background::Color(if active {
                mix(badge.bg, theme.accent, 0.24)
            } else {
                mix(theme.card_hover, theme.accent, 0.18)
            })),
            button::Status::Disabled => None,
        };
        let text_color = if matches!(status, button::Status::Disabled) {
            theme.muted_fg()
        } else if active {
            badge.fg
        } else {
            theme.fg
        };
        button::Style {
            background,
            text_color,
            border: Border {
                color: if active && !matches!(status, button::Status::Disabled) {
                    badge.border
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

pub fn close_button(
    theme: AppTheme,
) -> impl Fn(&iced_core::Theme, button::Status) -> button::Style {
    move |_, status| {
        let (background, text_color) = match status {
            button::Status::Active => (None, theme.fg_muted),
            button::Status::Hovered => (
                Some(Background::Color(mix(
                    theme.chrome_bg(),
                    theme.danger,
                    0.24,
                ))),
                theme.danger,
            ),
            button::Status::Pressed => (
                Some(Background::Color(mix(
                    theme.chrome_bg(),
                    theme.danger,
                    0.36,
                ))),
                theme.danger,
            ),
            button::Status::Disabled => (None, theme.muted_fg()),
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

pub fn power_switch_button(
    theme: AppTheme,
    powered: bool,
) -> impl Fn(&iced_core::Theme, button::Status) -> button::Style {
    let badge = theme.status_badge(theme.accent);
    move |_, status| {
        if powered {
            let bg = match status {
                button::Status::Active => badge.bg,
                button::Status::Hovered => mix(badge.bg, theme.accent, 0.14),
                button::Status::Pressed => mix(badge.bg, theme.accent, 0.24),
                button::Status::Disabled => mix(theme.bg, theme.card_bg, 0.5),
            };
            let border_col = if matches!(status, button::Status::Disabled) {
                theme.grid_line()
            } else {
                mix(theme.card_bg, theme.accent, 0.65)
            };
            button::Style {
                background: Some(Background::Color(bg)),
                text_color: if matches!(status, button::Status::Disabled) {
                    theme.muted_fg()
                } else {
                    theme.fg
                },
                border: Border {
                    color: border_col,
                    width: 1.2,
                    radius: 8.0.into(),
                },
                ..Default::default()
            }
        } else {
            let bg = match status {
                button::Status::Active => theme.card_bg,
                button::Status::Hovered => theme.card_hover,
                button::Status::Pressed => mix(theme.card_hover, theme.accent, 0.14),
                button::Status::Disabled => mix(theme.bg, theme.card_bg, 0.5),
            };
            button::Style {
                background: Some(Background::Color(bg)),
                text_color: if matches!(status, button::Status::Disabled) {
                    theme.muted_fg()
                } else {
                    theme.fg_muted
                },
                border: Border {
                    color: theme.surface_border(),
                    width: 1.0,
                    radius: 8.0.into(),
                },
                ..Default::default()
            }
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
            button::Status::Disabled => mix(theme.card_bg, theme.accent, 0.25),
        };
        let fg = if matches!(status, button::Status::Disabled) {
            theme.muted_fg()
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
                theme.muted_fg()
            } else {
                theme.fg
            },
            border: Border {
                color: theme.surface_border(),
                width: 1.0,
                radius: 7.0.into(),
            },
            ..Default::default()
        }
    }
}

pub fn toggle_chip(
    theme: AppTheme,
    selected: bool,
) -> impl Fn(&iced_core::Theme, button::Status) -> button::Style {
    move |_, status| {
        if selected {
            let fill = match status {
                button::Status::Hovered => theme.accent_hover,
                button::Status::Pressed => mix(theme.accent, theme.fg, 0.12),
                button::Status::Disabled => mix(theme.card_bg, theme.accent, 0.30),
                button::Status::Active => theme.accent,
            };
            button::Style {
                background: Some(Background::Color(fill)),
                text_color: if matches!(status, button::Status::Disabled) {
                    theme.muted_fg()
                } else {
                    contrasting_fg(fill)
                },
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
                button::Status::Disabled => mix(theme.bg, theme.card_bg, 0.5),
                button::Status::Active => theme.input_bg(),
            };
            button::Style {
                background: Some(Background::Color(fill)),
                text_color: if matches!(status, button::Status::Disabled) {
                    theme.muted_fg()
                } else {
                    theme.fg
                },
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

pub fn danger_button(
    theme: AppTheme,
) -> impl Fn(&iced_core::Theme, button::Status) -> button::Style {
    let badge = theme.status_badge(theme.danger);
    move |_, status| {
        let (bg, fg, border_col) = match status {
            button::Status::Active => (badge.bg, badge.fg, badge.border),
            button::Status::Hovered => (
                mix(theme.card_bg, theme.danger, 0.28),
                badge.fg,
                theme.danger,
            ),
            button::Status::Pressed => (theme.danger, contrasting_fg(theme.danger), theme.danger),
            button::Status::Disabled => (theme.card_bg, theme.muted_fg(), theme.grid_line()),
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

pub fn confirm_danger_button(
    theme: AppTheme,
) -> impl Fn(&iced_core::Theme, button::Status) -> button::Style {
    move |_, status| {
        let bg = match status {
            button::Status::Active => theme.danger,
            button::Status::Hovered => mix(theme.danger, theme.fg, 0.14),
            button::Status::Pressed => mix(theme.danger, theme.bg, 0.16),
            button::Status::Disabled => theme.card_bg,
        };
        let fg = if matches!(status, button::Status::Disabled) {
            theme.muted_fg()
        } else {
            contrasting_fg(bg)
        };
        button::Style {
            background: Some(Background::Color(bg)),
            text_color: fg,
            border: Border {
                color: if matches!(status, button::Status::Disabled) {
                    theme.grid_line()
                } else {
                    bg
                },
                width: 1.0,
                radius: 7.0.into(),
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
            text_input::Status::Hovered => mix(theme.surface_border(), theme.fg, 0.22),
            text_input::Status::Active => theme.surface_border(),
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
            placeholder: theme.muted_fg(),
            value: theme.fg,
            selection: mix(theme.input_bg(), theme.accent, 0.35),
        }
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
