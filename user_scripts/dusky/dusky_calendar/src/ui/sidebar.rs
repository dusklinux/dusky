//! Collapsible sidebar with GNOME-Calendar-inspired split Month/Year Date Chooser
//! (with event dots & active-week highlight) and Matugen-themed Calendar List.

use crate::icons::render_icon;
use crate::layout::{
    is_weekend, month_grid_bounds, ordered_weekdays, week_start_for,
};
use crate::model::CalendarMeta;
use crate::theme::{AppTheme, contrasting_fg, mix};
use crate::ui::style;
use crate::ui::{Element, Message, SIDEBAR_WIDTH};
use chrono::{Datelike, NaiveDate, TimeDelta, Weekday};
use iced_core::{Alignment, Length, Padding};
use iced_widget::{Space, Stack, button, column, container, row, scrollable, text};

fn month_short_name(month: u32) -> &'static str {
    match month {
        1 => "Jan",
        2 => "Feb",
        3 => "Mar",
        4 => "Apr",
        5 => "May",
        6 => "Jun",
        7 => "Jul",
        8 => "Aug",
        9 => "Sep",
        10 => "Oct",
        11 => "Nov",
        12 => "Dec",
        _ => "",
    }
}

fn month_full_name(month: u32) -> &'static str {
    match month {
        1 => "January",
        2 => "February",
        3 => "March",
        4 => "April",
        5 => "May",
        6 => "June",
        7 => "July",
        8 => "August",
        9 => "September",
        10 => "October",
        11 => "November",
        12 => "December",
        _ => "",
    }
}

fn weekday_initial(day: Weekday) -> &'static str {
    match day {
        Weekday::Mon => "M",
        Weekday::Tue => "T",
        Weekday::Wed => "W",
        Weekday::Thu => "T",
        Weekday::Fri => "F",
        Weekday::Sat => "S",
        Weekday::Sun => "S",
    }
}

/// Reusable mini Date Chooser used in the sidebar, Narrow Agenda View, and Event Editor.
#[allow(clippy::too_many_arguments)]
pub fn view_date_chooser<'a>(
    theme: AppTheme,
    displayed_month: NaiveDate,
    selected_date: NaiveDate,
    today: NaiveDate,
    week_start_monday: bool,
    show_week_numbers: bool,
    split_month_year: bool,
    event_dots: Option<&[bool; 42]>,
    on_shift_month: impl Fn(i32) -> Message + Copy + 'a,
    on_shift_year: Option<fn(i32) -> Message>,
    on_select_date: impl Fn(NaiveDate) -> Message + Copy + 'a,
) -> Element<'a> {
    let year = displayed_month.year();
    let month = displayed_month.month();
    let (grid_start, _) = month_grid_bounds(year, month, week_start_monday);
    let selected_week_start = week_start_for(selected_date, week_start_monday);

    // Header row: either split Month + Year steppers (sidebar) or combined Month Year (narrow/picker).
    let nav_header: Element<'a> = if split_month_year {
        let month_nav = row![
            button(render_icon("chevron_left", 13.0, theme.fg_muted))
                .padding([4, 5])
                .style(style::icon_button(theme, false))
                .on_press(on_shift_month(-1)),
            container(
                text(month_short_name(month))
                    .size(13)
                    .font(style::bold_font())
                    .color(theme.fg)
            )
            .width(Length::Fill)
            .align_x(Alignment::Center),
            button(render_icon("chevron_right", 13.0, theme.fg_muted))
                .padding([4, 5])
                .style(style::icon_button(theme, false))
                .on_press(on_shift_month(1)),
        ]
        .spacing(2)
        .align_y(Alignment::Center)
        .width(Length::FillPortion(6));

        let year_prev = on_shift_year.map(|f| f(-1)).unwrap_or_else(|| on_shift_month(-12));
        let year_next = on_shift_year.map(|f| f(1)).unwrap_or_else(|| on_shift_month(12));

        let year_nav = row![
            button(render_icon("chevron_left", 13.0, theme.fg_muted))
                .padding([4, 5])
                .style(style::icon_button(theme, false))
                .on_press(year_prev),
            container(
                text(format!("{year}"))
                    .size(13)
                    .font(style::bold_font())
                    .color(theme.fg)
            )
            .width(Length::Fill)
            .align_x(Alignment::Center),
            button(render_icon("chevron_right", 13.0, theme.fg_muted))
                .padding([4, 5])
                .style(style::icon_button(theme, false))
                .on_press(year_next),
        ]
        .spacing(2)
        .align_y(Alignment::Center)
        .width(Length::FillPortion(5));

        row![month_nav, year_nav]
            .spacing(6)
            .align_y(Alignment::Center)
            .width(Length::Fill)
            .into()
    } else {
        row![
            button(render_icon("chevron_left", 14.0, theme.fg_muted))
                .padding([4, 6])
                .style(style::icon_button(theme, false))
                .on_press(on_shift_month(-1)),
            container(
                text(format!("{} {}", month_full_name(month), year))
                    .size(13.5)
                    .font(style::bold_font())
                    .color(theme.fg)
            )
            .width(Length::Fill)
            .align_x(Alignment::Center),
            button(render_icon("chevron_right", 14.0, theme.fg_muted))
                .padding([4, 6])
                .style(style::icon_button(theme, false))
                .on_press(on_shift_month(1)),
        ]
        .spacing(4)
        .align_y(Alignment::Center)
        .width(Length::Fill)
        .into()
    };

    // Weekday initials header row (aligned with week_row padding & spacing).
    let mut weekday_row = row![].spacing(1).align_y(Alignment::Center);
    if show_week_numbers {
        weekday_row = weekday_row.push(Space::new().width(Length::Fixed(22.0)).height(16));
    }
    for wd in ordered_weekdays(week_start_monday) {
        let weekend = matches!(wd, Weekday::Sat | Weekday::Sun);
        weekday_row = weekday_row.push(
            container(
                text(weekday_initial(wd))
                    .size(11.5)
                    .font(style::bold_font())
                    .color(if weekend {
                        style::weekend_color(theme)
                    } else {
                        theme.fg_muted
                    }),
            )
            .width(Length::FillPortion(1))
            .align_x(Alignment::Center),
        );
    }

    let weekday_header = container(weekday_row)
        .padding(Padding::from([0, 2]))
        .width(Length::Fill);

    // 6 rows × 7 days grid.
    let mut grid_col = column![nav_header, weekday_header]
        .spacing(5)
        .width(Length::Fill);

    for row_idx in 0..6 {
        let week_start = grid_start + TimeDelta::days(row_idx * 7);
        let is_active_week = week_start == selected_week_start;
        let mut week_row = row![].spacing(1).align_y(Alignment::Center);

        if show_week_numbers {
            let wn = container(
                text(format!("{:02}", week_start.iso_week().week()))
                    .size(10.5)
                    .line_height(1.0)
                    .font(style::mono_font())
                    .color(mix(theme.sidebar_bg, theme.fg_muted, 0.72)),
            )
            .width(Length::Fixed(22.0))
            .height(Length::Fixed(24.0))
            .align_x(Alignment::Center)
            .align_y(Alignment::Center)
            .style(style::weeknum_chip(theme));
            week_row = week_row.push(wn);
        }

        for col_idx in 0..7 {
            let cell_index = (row_idx as usize) * 7 + col_idx;
            let date = week_start + TimeDelta::days(col_idx as i64);
            let in_month = date.year() == year && date.month() == month;
            let is_today = date == today;
            let is_selected = date == selected_date;
            let weekend = is_weekend(date);
            let has_event = event_dots.is_some_and(|dots| dots[cell_index]);

            let day_label: Element<'a> = container(
                text(format!("{}", date.day()))
                    .size(12.5)
                    .line_height(1.0)
                    .font(if is_selected || is_today {
                        style::bold_font()
                    } else {
                        style::regular_font()
                    })
                    .align_x(Alignment::Center)
                    .align_y(Alignment::Center),
            )
            .width(Length::Fill)
            .height(Length::Fill)
            .align_x(Alignment::Center)
            .align_y(Alignment::Center)
            .into();

            // Keep the numeral strictly centered; overlay the event dot at the bottom edge when present.
            let day_content: Element<'a> = if has_event {
                let dot_color = if is_selected {
                    contrasting_fg(theme.accent)
                } else if is_today {
                    theme.accent
                } else if weekend {
                    if in_month {
                        style::weekend_color(theme)
                    } else {
                        style::weekend_muted_color(theme)
                    }
                } else if in_month {
                    theme.fg_muted
                } else {
                    theme.muted_day_fg()
                };

                let dot_layer: Element<'a> = container(
                    container(Space::new().width(3.5).height(3.5))
                        .style(style::color_dot(dot_color, 999.0)),
                )
                .width(Length::Fill)
                .height(Length::Fill)
                .padding(Padding {
                    top: 0.0,
                    right: 0.0,
                    bottom: 2.0,
                    left: 0.0,
                })
                .align_x(Alignment::Center)
                .align_y(Alignment::End)
                .into();

                Stack::new()
                    .push(day_label)
                    .push(dot_layer)
                    .width(Length::Fill)
                    .height(Length::Fill)
                    .into()
            } else {
                day_label
            };

            let day_btn = button(day_content)
                .padding(0)
                .width(Length::Fixed(28.0))
                .height(Length::Fixed(28.0))
                .style(style::mini_day_button(
                    theme,
                    is_selected,
                    is_today,
                    in_month,
                    weekend,
                ))
                .on_press(on_select_date(date));

            let day_cell = container(day_btn)
                .width(Length::FillPortion(1))
                .align_x(Alignment::Center)
                .align_y(Alignment::Center);

            week_row = week_row.push(day_cell);
        }

        let wrapped_row: Element<'a> = if is_active_week {
            container(week_row)
                .padding(Padding::from([1, 2]))
                .width(Length::Fill)
                .style(style::active_week_row(theme))
                .into()
        } else {
            container(week_row)
                .padding(Padding::from([1, 2]))
                .width(Length::Fill)
                .into()
        };

        grid_col = grid_col.push(wrapped_row);
    }

    container(grid_col)
        .width(Length::Fill)
        .padding(Padding::from([8, 8]))
        .into()
}

/// Full left sidebar panel: Date Chooser + Calendar visibility list + Calendar Management trigger.
#[allow(clippy::too_many_arguments)]
pub fn view_sidebar<'a>(
    theme: AppTheme,
    displayed_month: NaiveDate,
    selected_date: NaiveDate,
    today: NaiveDate,
    week_start_monday: bool,
    show_week_numbers: bool,
    event_dots: &'a [bool; 42],
    calendars: &'a [CalendarMeta],
    as_drawer: bool,
) -> Element<'a> {
    let chooser = view_date_chooser(
        theme,
        displayed_month,
        selected_date,
        today,
        week_start_monday,
        show_week_numbers,
        true,
        Some(event_dots),
        Message::MiniCalendarShiftMonth,
        Some(Message::MiniCalendarShiftYear),
        Message::SelectDate,
    );

    let divider = container(Space::new().width(Length::Fill).height(1))
        .width(Length::Fill)
        .height(Length::Fixed(1.0))
        .style(style::hairline_rule(theme));

    let cal_header = row![
        text("Calendars")
            .size(12.5)
            .font(style::bold_font())
            .color(theme.fg_muted),
        Space::new().width(Length::Fill),
        button(render_icon("calendar", 14.0, theme.fg_muted))
            .padding([4, 6])
            .style(style::icon_button(theme, false))
            .on_press(Message::OpenCalendarManager),
    ]
    .align_y(Alignment::Center)
    .padding(Padding {
        top: 10.0,
        right: 12.0,
        bottom: 4.0,
        left: 14.0,
    });

    let mut cal_rows = column![].spacing(3).padding(Padding::from([4, 8]));

    for cal in calendars {
        let swatch = cal.resolved_color(&theme);
        let check_badge: Element<'a> = if cal.visible {
            container(render_icon("check", 10.5, contrasting_fg(swatch)))
                .width(Length::Fixed(16.0))
                .height(Length::Fixed(16.0))
                .align_x(Alignment::Center)
                .align_y(Alignment::Center)
                .style(style::color_dot(swatch, 5.0))
                .into()
        } else {
            container(Space::new().width(16).height(16))
                .width(Length::Fixed(16.0))
                .height(Length::Fixed(16.0))
                .style(style::hollow_dot(swatch, 5.0))
                .into()
        };

        let mut row_content = row![
            check_badge,
            text(&cal.name)
                .size(13)
                .color(if cal.visible {
                    theme.fg
                } else {
                    theme.muted_day_fg()
                })
                .wrapping(iced_core::text::Wrapping::None),
            Space::new().width(Length::Fill),
        ]
        .spacing(9)
        .align_y(Alignment::Center);

        if cal.is_default {
            row_content = row_content.push(
                text("Default")
                    .size(11.5)
                    .color(theme.fg_muted),
            );
        }

        let item_btn = button(row_content)
            .width(Length::Fill)
            .padding([7, 8])
            .style(style::subtle_row_button(theme, false))
            .on_press(Message::ToggleCalendarVisible(cal.id.clone()));

        cal_rows = cal_rows.push(item_btn);
    }

    let cal_scroll = scrollable(cal_rows)
        .height(Length::Fill)
        .style(style::clean_scrollable(theme));

    let manage_footer = container(
        button(
            row![
                render_icon("plus", 14.0, theme.fg),
                text("Manage Calendars")
                    .size(13)
                    .font(style::bold_font())
                    .color(theme.fg),
            ]
            .spacing(7)
            .align_y(Alignment::Center),
        )
        .width(Length::Fill)
        .padding([8, 10])
        .style(style::secondary_button(theme))
        .on_press(Message::OpenCalendarManager),
    )
    .padding(Padding {
        top: 8.0,
        right: 10.0,
        bottom: 10.0,
        left: 10.0,
    });

    let panel_col = column![chooser, divider, cal_header, cal_scroll, manage_footer]
        .width(Length::Fixed(SIDEBAR_WIDTH))
        .height(Length::Fill);

    let panel = if as_drawer {
        container(panel_col)
            .width(Length::Fixed(SIDEBAR_WIDTH))
            .height(Length::Fill)
            .style(style::card_surface(theme, 0.0))
    } else {
        container(panel_col)
            .width(Length::Fixed(SIDEBAR_WIDTH))
            .height(Length::Fill)
            .style(style::sidebar_surface(theme))
    };

    let right_border = container(Space::new().width(1).height(Length::Fill))
        .width(Length::Fixed(1.0))
        .height(Length::Fill)
        .style(style::hairline_rule(theme));

    row![panel, right_border]
        .height(Length::Fill)
        .into()
}
