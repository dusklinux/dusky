//! Frameless Wayland header bar with drag handle, sidebar toggle, date navigation,
//! adaptive Month/Week/Agenda switcher, inline search, and Quick-Add trigger.

use crate::icons::render_icon;
use crate::theme::AppTheme;
use crate::ui::style;
use crate::ui::{Element, Message, ViewMode, ViewportTier};
use iced_core::{Alignment, Length, Padding};
use iced_widget::{Space, button, column, container, mouse_area, row, text, text_input, tooltip};

pub const HEADER_HEIGHT: f32 = 52.0;

pub fn search_input_id() -> iced_core::widget::Id {
    iced_core::widget::Id::new("dusky-calendar-search-input")
}

#[allow(clippy::too_many_arguments)]
pub fn view_header<'a>(
    theme: AppTheme,
    tier: ViewportTier,
    active_view: ViewMode,
    period_title: String,
    today_enabled: bool,
    sidebar_open: bool,
    search_open: bool,
    search_query: &'a str,
) -> Element<'a> {
    let mut left_controls = row![].spacing(6).align_y(Alignment::Center);

    // Sidebar toggle on Wide/Medium; compact Calendar Manager trigger on Narrow so
    // calendars, imports, and display preferences remain reachable without a sidebar.
    if tier != ViewportTier::Narrow {
        let sidebar_btn = button(render_icon(
            "sidebar",
            16.0,
            if sidebar_open {
                theme.accent
            } else {
                theme.fg
            },
        ))
        .padding([7, 8])
        .style(style::icon_button(theme, sidebar_open))
        .on_press(Message::ToggleSidebar);

        left_controls = left_controls.push(style::tooltip(
            theme,
            sidebar_btn,
            if sidebar_open {
                "Hide Sidebar (F9)"
            } else {
                "Show Sidebar (F9)"
            },
            tooltip::Position::Bottom,
        ));
    } else if !search_open {
        let manager_btn = button(render_icon("calendar", 15.0, theme.fg))
            .padding([7, 8])
            .style(style::icon_button(theme, false))
            .on_press(Message::OpenCalendarManager);

        left_controls = left_controls.push(style::tooltip(
            theme,
            manager_btn,
            "Manage Calendars & Preferences (F8)",
            tooltip::Position::Bottom,
        ));
    }

    // Today quick-jump button (also re-centers Week view on the current time line).
    let today_icon_color = if today_enabled {
        theme.fg
    } else {
        theme.muted_day_fg()
    };
    let today_content: Element<'a> = if tier == ViewportTier::Wide {
        row![
            render_icon("today", 14.0, today_icon_color),
            text("Today").size(13).font(style::bold_font()),
        ]
        .spacing(6)
        .align_y(Alignment::Center)
        .into()
    } else {
        row![text("Today").size(12.5).font(style::bold_font())]
            .align_y(Alignment::Center)
            .into()
    };

    let mut today_btn = button(today_content)
        .padding([6, 11])
        .style(style::secondary_button(theme));
    if today_enabled {
        today_btn = today_btn.on_press(Message::GoToday);
    }

    let today_tip = if !today_enabled {
        "Already on Today"
    } else if active_view == ViewMode::Week {
        "Jump to Today & Current Time (Ctrl+T)"
    } else {
        "Jump to Today (Ctrl+T)"
    };
    left_controls = left_controls.push(style::tooltip(
        theme,
        today_btn,
        today_tip,
        tooltip::Position::Bottom,
    ));

    // Prev / Next period chevrons (shown on Wide and Medium tiers).
    if tier != ViewportTier::Narrow {
        let (prev_tip, next_tip) = match active_view {
            ViewMode::Month => ("Previous Month (Alt+←)", "Next Month (Alt+→)"),
            ViewMode::Week => ("Previous Week (Alt+←)", "Next Week (Alt+→)"),
            ViewMode::Agenda => ("Previous 2 Weeks (Alt+←)", "Next 2 Weeks (Alt+→)"),
        };

        let prev_btn = button(render_icon("chevron_left", 15.0, theme.fg))
            .padding([6, 7])
            .style(style::icon_button(theme, false))
            .on_press(Message::NavigatePrev);
        let next_btn = button(render_icon("chevron_right", 15.0, theme.fg))
            .padding([6, 7])
            .style(style::icon_button(theme, false))
            .on_press(Message::NavigateNext);

        let nav_cluster = row![
            style::tooltip(theme, prev_btn, prev_tip, tooltip::Position::Bottom),
            style::tooltip(theme, next_btn, next_tip, tooltip::Position::Bottom),
        ]
        .spacing(2)
        .align_y(Alignment::Center);

        left_controls = left_controls.push(nav_cluster);
    }

    // Current period title (doubles as a window drag handle for floating Hyprland windows).
    let title_size = if tier == ViewportTier::Narrow {
        15.0
    } else {
        16.5
    };
    let title_drag = mouse_area(
        container(
            text(period_title)
                .size(title_size)
                .font(style::bold_font())
                .color(theme.fg)
                .wrapping(iced_core::text::Wrapping::None),
        )
        .padding(Padding::from([0, 6])),
    )
    .on_press(Message::DragWindow);

    left_controls = left_controls.push(title_drag);

    // Draggable flexible spacer.
    let drag_spacer_left = mouse_area(Space::new().width(Length::Fill).height(Length::Fill))
        .on_press(Message::DragWindow);
    let drag_spacer_right = mouse_area(Space::new().width(Length::Fill).height(Length::Fill))
        .on_press(Message::DragWindow);

    // Adaptive segmented view switcher (Wide: icon + label; Medium: icon-only; Narrow: hidden).
    let center_switcher: Option<Element<'a>> = if tier == ViewportTier::Narrow || search_open {
        None
    } else {
        let modes = [ViewMode::Month, ViewMode::Week, ViewMode::Agenda];
        let mut seg_row = row![].spacing(2).align_y(Alignment::Center);
        for mode in modes {
            let selected = active_view == mode;
            let icon_col = if selected {
                theme.accent_fg
            } else {
                theme.fg_muted
            };
            let content: Element<'a> = if tier == ViewportTier::Wide {
                row![
                    render_icon(mode.icon_name(), 14.0, icon_col),
                    text(mode.label())
                        .size(13)
                        .font(if selected {
                            style::bold_font()
                        } else {
                            style::regular_font()
                        }),
                ]
                .spacing(6)
                .align_y(Alignment::Center)
                .into()
            } else {
                render_icon(mode.icon_name(), 15.0, icon_col)
            };

            let pad = if tier == ViewportTier::Wide {
                [5, 11]
            } else {
                [6, 9]
            };

            let mode_tip = match mode {
                ViewMode::Month => "Month View (Alt+1)",
                ViewMode::Week => "Week View (Alt+2)",
                ViewMode::Agenda => "Agenda View (Alt+3)",
            };
            let seg_btn = button(content)
                .padding(pad)
                .style(style::segment_button(theme, selected))
                .on_press(Message::SwitchView(mode));

            seg_row = seg_row.push(style::tooltip(
                theme,
                seg_btn,
                mode_tip,
                tooltip::Position::Bottom,
            ));
        }

        Some(
            container(seg_row)
                .padding(3)
                .style(style::segmented_track(theme))
                .into(),
        )
    };

    // Right controls: inline Search (or toggle), + New Event, and Window Close.
    let mut right_controls = row![].spacing(6).align_y(Alignment::Center);

    if search_open {
        let input_width = match tier {
            ViewportTier::Wide => 230.0,
            ViewportTier::Medium => 185.0,
            ViewportTier::Narrow => 150.0,
        };
        let clear_btn = button(render_icon("close", 14.0, theme.fg_muted))
            .padding([6, 7])
            .style(style::icon_button(theme, false))
            .on_press(Message::ClearSearch);

        let search_box = row![
            text_input("Search events…", search_query)
                .id(search_input_id())
                .on_input(Message::SearchChanged)
                .on_submit(Message::SearchSubmit)
                .padding([5, 10])
                .size(13)
                .width(Length::Fixed(input_width))
                .style(style::text_input_style(theme)),
            style::tooltip(
                theme,
                clear_btn,
                "Close Search (Esc)",
                tooltip::Position::Bottom,
            ),
        ]
        .spacing(4)
        .align_y(Alignment::Center);
        right_controls = right_controls.push(search_box);
    } else {
        let search_btn = button(render_icon("search", 15.0, theme.fg))
            .padding([7, 8])
            .style(style::icon_button(theme, false))
            .on_press(Message::ToggleSearch);
        right_controls = right_controls.push(style::tooltip(
            theme,
            search_btn,
            "Search Events (Ctrl+F)",
            tooltip::Position::Bottom,
        ));
    }

    // Primary "+ New Event" button.
    let new_btn_content: Element<'a> = if tier == ViewportTier::Wide && !search_open {
        row![
            render_icon("plus", 14.0, theme.accent_fg),
            text("New").size(13).font(style::bold_font()),
        ]
        .spacing(5)
        .align_y(Alignment::Center)
        .into()
    } else {
        render_icon("plus", 15.0, theme.accent_fg)
    };

    let new_btn_pad = if tier == ViewportTier::Wide && !search_open {
        [6, 12]
    } else {
        [7, 8]
    };

    let new_btn = button(new_btn_content)
        .padding(new_btn_pad)
        .style(style::primary_button(theme))
        .on_press(Message::OpenQuickAdd {
            date: None,
            hour: None,
        });
    right_controls = right_controls.push(style::tooltip(
        theme,
        new_btn,
        "New Event (Ctrl+N)",
        tooltip::Position::Bottom,
    ));

    // Frameless close button.
    let close_btn = button(render_icon("close", 15.0, theme.fg_muted))
        .padding([7, 8])
        .style(style::close_button(theme))
        .on_press(Message::RequestClose);
    right_controls = right_controls.push(style::tooltip(
        theme,
        close_btn,
        "Close Window (Ctrl+Q)",
        tooltip::Position::Bottom,
    ));

    let mut bar_row = row![left_controls, drag_spacer_left]
        .spacing(8)
        .align_y(Alignment::Center)
        .height(Length::Fill);

    if let Some(switcher) = center_switcher {
        bar_row = bar_row.push(switcher).push(drag_spacer_right);
    }

    bar_row = bar_row.push(right_controls);

    let bar = container(bar_row)
        .width(Length::Fill)
        .height(Length::Fixed(HEADER_HEIGHT))
        .padding(Padding::from([0, 12]))
        .style(style::header_bar(theme));

    let divider = container(Space::new().width(Length::Fill).height(1))
        .width(Length::Fill)
        .height(Length::Fixed(1.0))
        .style(style::hairline_rule(theme));

    column![bar, divider].width(Length::Fill).into()
}
