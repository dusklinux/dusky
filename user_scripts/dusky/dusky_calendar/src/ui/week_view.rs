//! Week view with sticky day headers, collapsible multi-day all-day banner strip,
//! scrollable zoomable 24-hour time grid, overlapping columns, and live current-time line.

use crate::icons::render_icon;
use crate::layout::WeekLayout;
use crate::model::{CalendarMeta, EventOccurrence, format_clock_time, weekday_short_name};
use crate::theme::{AppTheme, contrasting_fg, mix};
use crate::ui::grid_widgets::{
    WEEK_HOUR_GUTTER, WeekAllDayGrid, WeekBannerPlacement, WeekTimeGrid, WeekTimedPlacement,
};
use crate::ui::style;
use crate::ui::{Element, Message};
use chrono::{DateTime, Datelike, Local, NaiveDate, NaiveTime, TimeDelta, Timelike};
use iced_core::{Alignment, Color, Length, Padding};
use iced_widget::{Space, button, column, container, row, scrollable, text, tooltip};

pub const WEEK_SCROLL_ID: &str = "dusky-calendar-week-scroll";
const COLLAPSED_ALL_DAY_LANES: usize = 3;

pub fn week_scroll_id() -> iced_core::widget::Id {
    iced_core::widget::Id::new(WEEK_SCROLL_ID)
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

fn build_event_tooltip<'a>(
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
fn build_all_day_pill<'a>(
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

    content = content.push(
        container(
            text(occ.summary.clone())
                .size(11.5)
                .font(style::bold_font())
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
                right: if continues_right { 3.0 } else { 6.0 },
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

    let tip = build_event_tooltip(theme, occ, event_color, cal_name, use_24h);
    style::rich_tooltip(theme, pill_btn, tip, tooltip::Position::Top)
}

#[allow(clippy::too_many_arguments)]
fn build_timed_block<'a>(
    theme: AppTheme,
    occ: &EventOccurrence,
    event_color: Color,
    cal_name: &str,
    past: bool,
    block_height: f32,
    continues_prev: bool,
    continues_next: bool,
    use_24h: bool,
) -> Element<'a> {
    let palette = theme.event_pill_colors(event_color, past, false);
    let edge = container(Space::new().width(3.5).height(Length::Fill))
        .width(Length::Fixed(3.5))
        .height(Length::Fill)
        .style(style::color_dot(palette.edge, 2.0));

    let mut body = column![].spacing(2).width(Length::Fill);

    if block_height < 34.0 {
        // Ultra-compact single-line layout for short 15–30 min slots.
        let mut line = row![].spacing(5).align_y(Alignment::Center);
        if continues_prev {
            line = line.push(render_icon("chevron_up", 10.0, palette.edge));
        }
        line = line
            .push(
                text(occ.summary.clone())
                    .size(11.5)
                    .font(style::bold_font())
                    .wrapping(iced_core::text::Wrapping::None),
            )
            .push(
                text(occ.format_short_start(use_24h))
                    .size(10)
                    .font(style::mono_font())
                    .color(theme.fg_muted)
                    .wrapping(iced_core::text::Wrapping::None),
            );
        if continues_next {
            line = line.push(render_icon("chevron_down", 10.0, palette.edge));
        }
        body = body.push(line);
    } else {
        let mut title_row = row![].spacing(4).align_y(Alignment::Center);
        if continues_prev {
            title_row = title_row.push(render_icon("chevron_up", 10.0, palette.edge));
        }
        title_row = title_row.push(
            text(occ.summary.clone())
                .size(12)
                .font(style::bold_font())
                .wrapping(iced_core::text::Wrapping::WordOrGlyph),
        );
        body = body.push(title_row);

        let time_label = if continues_prev || continues_next {
            occ.format_time_range(use_24h)
        } else {
            format!(
                "{} – {}",
                format_clock_time(occ.start_local.time(), use_24h),
                format_clock_time(occ.end_local.time(), use_24h),
            )
        };
        let mut time_row = row![
            text(time_label)
                .size(10.5)
                .font(style::mono_font())
                .color(theme.fg_muted)
                .wrapping(iced_core::text::Wrapping::None),
        ]
        .spacing(4)
        .align_y(Alignment::Center);
        if continues_next {
            time_row = time_row.push(render_icon("chevron_down", 10.0, palette.edge));
        }
        body = body.push(time_row);

        if block_height >= 64.0 {
            if let Some(loc) = &occ.location {
                body = body.push(
                    text(loc.clone())
                        .size(10.5)
                        .color(theme.fg_muted)
                        .wrapping(iced_core::text::Wrapping::None),
                );
            }
        }
    }

    let inner = row![
        edge,
        container(body)
            .width(Length::Fill)
            .height(Length::Fill)
            .padding(Padding::from([3, 6]))
            .clip(true),
    ]
    .width(Length::Fill)
    .height(Length::Fill);

    let block_btn = button(inner)
        .width(Length::Fill)
        .height(Length::Fill)
        .padding(0)
        .clip(true)
        .style(style::timed_block_button(
            theme,
            event_color,
            past,
            continues_prev,
            continues_next,
        ))
        .on_press(Message::OpenEventPreview(occ.clone()));

    let tip = build_event_tooltip(theme, occ, event_color, cal_name, use_24h);
    style::rich_tooltip(theme, block_btn, tip, tooltip::Position::Top)
}

#[allow(clippy::too_many_arguments)]
pub fn view_week<'a>(
    theme: AppTheme,
    layout: &'a WeekLayout,
    selected_date: NaiveDate,
    now: DateTime<Local>,
    hour_height: f32,
    window_height: f32,
    all_day_expanded: bool,
    use_24h: bool,
    is_empty_calendar: bool,
    calendars: &'a [CalendarMeta],
) -> Element<'a> {
    // 1. Sticky Day Headers row (aligned with WEEK_HOUR_GUTTER).
    let mut day_headers_row = row![].align_y(Alignment::Center).width(Length::Fill);

    let iso_week_label = container(
        text(format!("W{:02}", layout.week_start.iso_week().week()))
            .size(11)
            .font(style::mono_font())
            .color(mix(theme.chrome_bg(), theme.fg_muted, 0.72)),
    )
    .width(Length::Fixed(WEEK_HOUR_GUTTER))
    .align_x(Alignment::Center);
    day_headers_row = day_headers_row.push(iso_week_label);

    for day in &layout.days {
        let is_selected = day.date == selected_date;
        let date = day.date;

        let num_badge: Element<'a> = if day.is_today {
            container(
                text(format!("{}", day.date.day()))
                    .size(14)
                    .line_height(1.0)
                    .font(style::bold_font())
                    .color(contrasting_fg(theme.accent))
                    .align_x(Alignment::Center)
                    .align_y(Alignment::Center),
            )
            .width(Length::Fixed(28.0))
            .height(Length::Fixed(24.0))
            .align_x(Alignment::Center)
            .align_y(Alignment::Center)
            .style(style::today_badge(theme))
            .into()
        } else {
            container(
                text(format!("{}", day.date.day()))
                    .size(14)
                    .line_height(1.0)
                    .font(if is_selected {
                        style::bold_font()
                    } else {
                        style::regular_font()
                    })
                    .color(if day.is_weekend {
                        style::weekend_color(theme)
                    } else if is_selected {
                        theme.accent
                    } else {
                        theme.fg
                    })
                    .align_x(Alignment::Center)
                    .align_y(Alignment::Center),
            )
            .width(Length::Fixed(28.0))
            .height(Length::Fixed(24.0))
            .align_x(Alignment::Center)
            .align_y(Alignment::Center)
            .into()
        };

        let day_btn = button(
            column![
                text(weekday_short_name(day.date.weekday()))
                    .size(11.5)
                    .font(style::bold_font())
                    .color(if day.is_today {
                        theme.accent
                    } else if day.is_weekend {
                        style::weekend_color(theme)
                    } else {
                        theme.fg_muted
                    }),
                num_badge,
            ]
            .spacing(2)
            .align_x(Alignment::Center)
            .width(Length::Fill),
        )
        .width(Length::FillPortion(1))
        .padding([5, 2])
        .style(style::subtle_row_button(theme, is_selected && !day.is_today))
        .on_press(Message::SelectDate(date));

        day_headers_row = day_headers_row.push(day_btn);
    }

    let mut top_section = column![
        container(day_headers_row)
            .width(Length::Fill)
            .style(style::header_bar(theme)),
    ]
    .width(Length::Fill);

    let week_start = layout.week_start;

    // 2. Sticky All-Day / Multi-Day Banner strip (always available for click/drag creation).
    let can_expand = layout.total_all_day_lanes > COLLAPSED_ALL_DAY_LANES;
    let visible_lanes = if layout.total_all_day_lanes == 0 {
        1
    } else if all_day_expanded || !can_expand {
        layout.total_all_day_lanes
    } else {
        COLLAPSED_ALL_DAY_LANES
    };

    let mut banner_items = Vec::new();
    for span in &layout.all_day_spans {
        if span.lane >= visible_lanes {
            continue;
        }
        let (color, cal_name) = calendar_info(calendars, &span.occurrence.calendar_id, &theme);
        let past = span.occurrence.is_past(now);
        let pill = build_all_day_pill(
            theme,
            &span.occurrence,
            color,
            cal_name,
            past,
            span.continues_left,
            span.continues_right,
            use_24h,
        );
        banner_items.push((
            WeekBannerPlacement {
                start_col: span.start_col,
                end_col: span.end_col,
                lane: span.lane,
                continues_left: span.continues_left,
                continues_right: span.continues_right,
            },
            pill,
        ));
    }

    let left_gutter: Element<'a> = if can_expand {
        let hidden_count = layout.total_all_day_lanes.saturating_sub(visible_lanes);
        let label_widget: Element<'a> = if all_day_expanded {
            render_icon("chevron_up", 14.0, theme.fg_muted)
        } else {
            row![
                text(format!("+{hidden_count}"))
                    .size(10.5)
                    .font(style::bold_font())
                    .color(theme.fg_muted),
                render_icon("chevron_down", 12.0, theme.fg_muted),
            ]
            .spacing(2)
            .align_y(Alignment::Center)
            .into()
        };
        let expand_tip = if all_day_expanded {
            "Collapse all-day events".to_string()
        } else {
            format!("Show all {} all-day rows", layout.total_all_day_lanes)
        };
        let expand_btn = button(label_widget)
            .padding([4, 6])
            .style(style::icon_button(theme, all_day_expanded))
            .on_press(Message::ToggleWeekAllDayExpanded);

        container(style::tooltip(
            theme,
            expand_btn,
            expand_tip,
            tooltip::Position::Right,
        ))
        .width(Length::Fixed(WEEK_HOUR_GUTTER))
        .align_x(Alignment::Center)
        .align_y(Alignment::Center)
        .into()
    } else {
        container(text("All day").size(10).color(theme.muted_day_fg()))
            .width(Length::Fixed(WEEK_HOUR_GUTTER))
            .align_x(Alignment::Center)
            .align_y(Alignment::Center)
            .into()
    };

    let banner_grid = WeekAllDayGrid::new(
        theme,
        visible_lanes,
        banner_items,
        move |start_col, end_col| Message::QuickAddDateRange {
            start_date: week_start + TimeDelta::days(start_col as i64),
            end_date: week_start + TimeDelta::days(end_col as i64),
        },
    );

    let max_all_day_h = (window_height * 0.35).clamp(120.0, 260.0);
    let banner_area: Element<'a> = if all_day_expanded && can_expand {
        container(
            scrollable(banner_grid)
                .direction(scrollable::Direction::Vertical(
                    scrollable::Scrollbar::new().width(4).scroller_width(4),
                ))
                .height(Length::Shrink)
                .style(style::clean_scrollable(theme)),
        )
        .width(Length::Fill)
        .max_height(max_all_day_h)
        .into()
    } else {
        banner_grid.into()
    };

    let banner_row = container(row![left_gutter, banner_area].align_y(Alignment::Center))
        .width(Length::Fill)
        .style(style::sidebar_surface(theme));

    let thin_sep = container(Space::new().width(Length::Fill).height(1))
        .width(Length::Fill)
        .height(Length::Fixed(1.0))
        .style(style::hairline_rule(theme));

    let bottom_header_divider = container(Space::new().width(Length::Fill).height(1))
        .width(Length::Fill)
        .height(Length::Fixed(1.0))
        .style(style::hairline_rule(theme));

    top_section = top_section
        .push(thin_sep)
        .push(banner_row)
        .push(bottom_header_divider);

    // 3. Scrollable 24-hour time grid.
    let weekend_cols: [bool; 7] = std::array::from_fn(|i| layout.days[i].is_weekend);
    let today_col = layout.days.iter().position(|d| d.is_today);
    let selected_col = layout.days.iter().position(|d| d.date == selected_date);
    let now_minutes = today_col.map(|_| (now.hour() as f32) * 60.0 + (now.minute() as f32));

    let mut hour_labels = Vec::with_capacity(23);
    for hour in 1..24u32 {
        let t = NaiveTime::from_hms_opt(hour, 0, 0).expect("valid hour");
        let label = container(
            text(format_clock_time(t, use_24h))
                .size(11)
                .font(style::mono_font())
                .color(mix(theme.chrome_bg(), theme.fg_muted, 0.78)),
        )
        .width(Length::Fill)
        .align_x(Alignment::End);
        hour_labels.push((hour, label.into()));
    }

    let mut blocks = Vec::with_capacity(layout.timed_blocks.len());
    for block in &layout.timed_blocks {
        let (color, cal_name) = calendar_info(calendars, &block.occurrence.calendar_id, &theme);
        let past = block.occurrence.is_past(now);
        let block_height =
            (((block.end_minutes - block.start_minutes) / 60.0) * hour_height - 2.0).max(18.0);
        let el = build_timed_block(
            theme,
            &block.occurrence,
            color,
            cal_name,
            past,
            block_height,
            block.continues_from_prev_day,
            block.continues_to_next_day,
            use_24h,
        );
        blocks.push((
            WeekTimedPlacement {
                day_col: block.day_col,
                start_minutes: block.start_minutes,
                end_minutes: block.end_minutes,
                col: block.col,
                col_span: block.col_span,
                total_cols: block.total_cols,
            },
            el,
        ));
    }

    let time_grid = WeekTimeGrid::new(
        theme,
        hour_height,
        use_24h,
        weekend_cols,
        today_col,
        selected_col,
        now_minutes,
        hour_labels,
        blocks,
        move |col, start_minutes, end_minutes| Message::QuickAddTimeRange {
            date: week_start + TimeDelta::days(col as i64),
            start_minutes,
            end_minutes,
        },
    )
    .on_zoom(Message::ZoomWeek);

    let scroll = scrollable(time_grid)
        .id(week_scroll_id())
        .width(Length::Fill)
        .height(Length::Fill)
        .style(style::clean_scrollable(theme));

    let mut root = column![top_section, scroll]
        .width(Length::Fill)
        .height(Length::Fill);

    if is_empty_calendar {
        let hint_divider = container(Space::new().width(Length::Fill).height(1))
            .width(Length::Fill)
            .height(Length::Fixed(1.0))
            .style(style::hairline_rule(theme));

        let hint_bar = container(
            row![
                render_icon("info", 13.0, theme.accent),
                text("Click or drag any time slot to schedule · Drag the All day strip for multi-day · Ctrl+Scroll to zoom · Drop .ics files to import")
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

        root = root.push(hint_divider).push(hint_bar);
    }

    root.into()
}
