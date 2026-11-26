//! 6-row Month view built on the unified `MonthGrid` widget with GNOME-Calendar-inspired
//! day badges, continuous multi-day spanning event pills with continuation cues,
//! per-row measured `+N more` overflow triggers, cross-row drag creation, and wheel stepping.

use crate::icons::render_icon;
use crate::layout::{MonthLayout, ordered_weekdays};
use crate::model::{CalendarMeta, EventOccurrence, weekday_short_name};
use crate::theme::{AppTheme, contrasting_fg, mix};
use crate::ui::grid_widgets::{
    MONTH_WEEKNUM_GUTTER, MonthCellMeta, MonthGrid, MonthSpanPlacement,
};
use crate::ui::style;
use crate::ui::{Element, Message};
use chrono::{DateTime, Datelike, Local, NaiveDate, Weekday};
use iced_core::{Alignment, Color, Length, Padding};
use iced_widget::{Space, button, column, container, row, text, tooltip};

fn month_abbr(month: u32) -> &'static str {
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

fn calendar_info<'a>(
    calendars: &'a [CalendarMeta],
    calendar_id: &str,
    theme: &AppTheme,
) -> (Color, &'a str) {
    calendars
        .iter()
        .find(|c| c.id == calendar_id)
        .map(|c| (c.resolved_color(theme), c.name.as_str()))
        .unwrap_or((theme.accent, "Calendar"))
}

fn build_pill_tooltip<'a>(
    theme: AppTheme,
    occ: &EventOccurrence,
    event_color: Color,
    cal_name: &str,
    use_24h: bool,
) -> Element<'a> {
    let mut tip = column![
        text(occ.summary.clone())
            .size(12.5)
            .font(style::bold_font())
            .color(theme.fg)
            .wrapping(iced_core::text::Wrapping::WordOrGlyph),
        row![
            container(Space::new().width(7.0).height(7.0))
                .style(style::color_dot(event_color, 999.0)),
            text(occ.format_time_range(use_24h))
                .size(11.5)
                .font(style::mono_font())
                .color(theme.fg_muted),
            text("·").size(11.5).color(theme.muted_day_fg()),
            text(cal_name.to_string()).size(11.5).color(theme.fg_muted),
        ]
        .spacing(6)
        .align_y(Alignment::Center),
    ]
    .spacing(3);

    if let Some(loc) = &occ.location {
        tip = tip.push(
            row![
                render_icon("location", 11.5, theme.fg_muted),
                text(loc.clone())
                    .size(11.5)
                    .color(theme.fg_muted)
                    .wrapping(iced_core::text::Wrapping::WordOrGlyph),
            ]
            .spacing(5)
            .align_y(Alignment::Center),
        );
    }

    tip.into()
}

#[allow(clippy::too_many_arguments)]
fn build_month_pill<'a>(
    theme: AppTheme,
    occ: &EventOccurrence,
    event_color: Color,
    cal_name: &str,
    past: bool,
    continues_left: bool,
    continues_right: bool,
    use_24h: bool,
) -> Element<'a> {
    let palette = theme.event_pill_colors(event_color, past, false);

    let mut content = row![].spacing(5).align_y(Alignment::Center);

    if continues_left {
        content = content.push(
            container(render_icon("chevron_left", 10.0, palette.edge))
                .padding(Padding {
                    top: 0.0,
                    right: 0.0,
                    bottom: 0.0,
                    left: 3.0,
                })
                .align_y(Alignment::Center),
        );
    } else {
        content = content.push(
            container(Space::new().width(3.5).height(Length::Fill))
                .width(Length::Fixed(3.5))
                .height(Length::Fill)
                .style(style::color_dot(palette.edge, 2.0)),
        );
    }

    if !occ.is_all_day && !continues_left {
        content = content.push(
            text(occ.format_short_start(use_24h))
                .size(10.5)
                .font(style::mono_font())
                .color(theme.fg_muted)
                .wrapping(iced_core::text::Wrapping::None),
        );
    }

    content = content.push(
        container(
            text(occ.summary.clone())
                .size(11.5)
                .font(if occ.is_all_day || occ.spans_multiple_days() {
                    style::bold_font()
                } else {
                    style::regular_font()
                })
                .wrapping(iced_core::text::Wrapping::None),
        )
        .width(Length::Fill)
        .clip(true),
    );

    if continues_right {
        content = content.push(render_icon("chevron_right", 10.0, palette.edge));
    }

    let pill_btn = button(
        container(content)
            .width(Length::Fill)
            .height(Length::Fill)
            .align_y(Alignment::Center)
            .padding(Padding {
                top: 0.0,
                right: if continues_right { 3.0 } else { 5.0 },
                bottom: 0.0,
                left: 0.0,
            })
            .clip(true),
    )
    .width(Length::Fill)
    .height(Length::Fill)
    .padding(0)
    .clip(true)
    .style(style::event_pill_button(
        theme,
        event_color,
        past,
        continues_left,
        continues_right,
    ))
    .on_press(Message::OpenEventPreview(occ.clone()));

    let tip = build_pill_tooltip(theme, occ, event_color, cal_name, use_24h);
    style::rich_tooltip(theme, pill_btn, tip, tooltip::Position::Top)
}

#[allow(clippy::too_many_arguments)]
pub fn view_month<'a>(
    theme: AppTheme,
    layout: &'a MonthLayout,
    selected_date: NaiveDate,
    now: DateTime<Local>,
    week_start_monday: bool,
    show_week_numbers: bool,
    use_24h: bool,
    is_empty_calendar: bool,
    calendars: &'a [CalendarMeta],
) -> Element<'a> {
    // Weekday column header bar.
    let mut header_row = row![].align_y(Alignment::Center).width(Length::Fill);
    if show_week_numbers {
        header_row = header_row.push(
            container(
                text("W")
                    .size(11)
                    .font(style::bold_font())
                    .color(mix(theme.chrome_bg(), theme.fg_muted, 0.72)),
            )
            .width(Length::Fixed(MONTH_WEEKNUM_GUTTER))
            .align_x(Alignment::Center),
        );
    }

    for wd in ordered_weekdays(week_start_monday) {
        let weekend = matches!(wd, Weekday::Sat | Weekday::Sun);
        header_row = header_row.push(
            container(
                text(weekday_short_name(wd))
                    .size(12.5)
                    .font(style::bold_font())
                    .color(if weekend {
                        style::weekend_color(theme)
                    } else {
                        theme.fg_muted
                    }),
            )
            .width(Length::FillPortion(1))
            .padding(Padding::from([6, 8])),
        );
    }

    let header_bar = container(header_row)
        .width(Length::Fill)
        .style(style::header_bar(theme));

    let header_divider = container(Space::new().width(Length::Fill).height(1))
        .width(Length::Fill)
        .height(Length::Fixed(1.0))
        .style(style::hairline_rule(theme));

    let fallback_date = layout.grid_start;
    let cells: [[MonthCellMeta; 7]; 6] = std::array::from_fn(|row_idx| {
        if let Some(week) = layout.weeks.get(row_idx) {
            std::array::from_fn(|col| {
                let d = &week.days[col];
                MonthCellMeta {
                    date: d.date,
                    is_weekend: d.is_weekend,
                    is_selected: d.date == selected_date,
                    is_today: d.is_today,
                    in_current_month: d.in_current_month,
                    base_overflow: d.overflow_count,
                }
            })
        } else {
            std::array::from_fn(|_| MonthCellMeta {
                date: fallback_date,
                is_weekend: false,
                is_selected: false,
                is_today: false,
                in_current_month: false,
                base_overflow: 0,
            })
        }
    });

    let mut week_nums = Vec::with_capacity(if show_week_numbers { 6 } else { 0 });
    let mut day_headers = Vec::with_capacity(42);
    let mut spans = Vec::new();

    for (row_idx, week) in layout.weeks.iter().take(6).enumerate() {
        if show_week_numbers {
            let wn: Element<'a> = container(
                text(format!("{:02}", week.iso_week))
                    .size(11)
                    .font(style::mono_font())
                    .color(mix(theme.chrome_bg(), theme.fg_muted, 0.72)),
            )
            .width(Length::Fill)
            .height(Length::Fill)
            .align_x(Alignment::Center)
            .align_y(Alignment::Center)
            .into();
            week_nums.push((row_idx, wn));
        }

        for (col, day) in week.days.iter().enumerate() {
            let is_selected = day.date == selected_date;
            let day_label_str = if day.is_first_of_month {
                format!("{} {}", month_abbr(day.date.month()), day.date.day())
            } else {
                format!("{}", day.date.day())
            };

            let badge: Element<'a> = if day.is_today {
                container(
                    text(day_label_str)
                        .size(12.5)
                        .line_height(1.0)
                        .font(style::bold_font())
                        .color(contrasting_fg(theme.accent))
                        .align_x(Alignment::Center)
                        .align_y(Alignment::Center),
                )
                .width(if day.is_first_of_month {
                    Length::Shrink
                } else {
                    Length::Fixed(24.0)
                })
                .height(Length::Fixed(22.0))
                .padding(if day.is_first_of_month {
                    Padding::from([0, 6])
                } else {
                    Padding::ZERO
                })
                .align_x(Alignment::Center)
                .align_y(Alignment::Center)
                .style(style::today_badge(theme))
                .into()
            } else if day.is_first_of_month {
                let first_color = if day.is_weekend {
                    if day.in_current_month {
                        style::weekend_color(theme)
                    } else {
                        style::weekend_muted_color(theme)
                    }
                } else if day.in_current_month {
                    theme.fg
                } else {
                    theme.fg_muted
                };
                container(
                    text(day_label_str)
                        .size(12)
                        .line_height(1.0)
                        .font(style::bold_font())
                        .color(first_color)
                        .align_x(Alignment::Center)
                        .align_y(Alignment::Center),
                )
                .height(Length::Fixed(22.0))
                .padding(Padding::from([0, 6]))
                .align_x(Alignment::Center)
                .align_y(Alignment::Center)
                .style(style::first_day_badge(theme))
                .into()
            } else {
                let color = if !day.in_current_month {
                    if day.is_weekend {
                        style::weekend_muted_color(theme)
                    } else {
                        theme.muted_day_fg()
                    }
                } else if day.is_weekend {
                    style::weekend_color(theme)
                } else if is_selected {
                    theme.accent
                } else {
                    theme.fg
                };
                container(
                    text(day_label_str)
                        .size(12.5)
                        .line_height(1.0)
                        .font(if is_selected {
                            style::bold_font()
                        } else {
                            style::regular_font()
                        })
                        .color(color)
                        .align_y(Alignment::Center),
                )
                .height(Length::Fixed(22.0))
                .padding(Padding::from([0, 4]))
                .align_y(Alignment::Center)
                .into()
            };

            let header_el: Element<'a> = row![badge, Space::new().width(Length::Fill)]
                .align_y(Alignment::Center)
                .width(Length::Fill)
                .into();
            day_headers.push((row_idx, col, header_el));
        }

        for span in &week.spans {
            let (color, cal_name) = calendar_info(calendars, &span.occurrence.calendar_id, &theme);
            let past = span.occurrence.is_past(now);
            let pill = build_month_pill(
                theme,
                &span.occurrence,
                color,
                cal_name,
                past,
                span.continues_left,
                span.continues_right,
                use_24h,
            );
            spans.push((
                MonthSpanPlacement {
                    row: row_idx,
                    start_col: span.start_col,
                    end_col: span.end_col,
                    lane: span.lane,
                    continues_left: span.continues_left,
                    continues_right: span.continues_right,
                },
                pill,
            ));
        }
    }

    let month_grid = MonthGrid::new(
        theme,
        show_week_numbers,
        cells,
        week_nums,
        day_headers,
        spans,
        Message::SelectDate,
        Message::ActivateDate,
        |start_date, end_date| Message::QuickAddDateRange {
            start_date,
            end_date,
        },
        Message::OpenDayOverflow,
    )
    .on_wheel(Message::MonthWheelStep);

    let mut root = column![header_bar, header_divider, month_grid]
        .width(Length::Fill)
        .height(Length::Fill);

    if is_empty_calendar {
        let hint_bar = container(
            row![
                render_icon("info", 13.0, theme.accent),
                text("Click + or double-click any day to create an event · Drag across days for multi-day · Drop .ics files to import")
                    .size(12)
                    .color(theme.fg_muted)
                    .wrapping(iced_core::text::Wrapping::WordOrGlyph),
            ]
            .spacing(8)
            .align_y(Alignment::Center),
        )
        .width(Length::Fill)
        .padding(Padding::from([7, 14]))
        .align_x(Alignment::Center)
        .style(style::header_bar(theme));

        root = root.push(hint_bar);
    }

    root.into()
}
