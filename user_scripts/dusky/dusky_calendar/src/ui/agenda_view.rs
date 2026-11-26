//! Chronological day-grouped Agenda view, doubling as the Narrow (< 700px) adaptive view
//! with an embedded compact Date Chooser above the upcoming events list.

use crate::icons::render_icon;
use crate::layout::AgendaLayout;
use crate::model::{
    CalendarMeta, EventOccurrence, format_agenda_day_heading, format_relative_date_short,
};
use crate::theme::AppTheme;
use crate::ui::sidebar::view_date_chooser;
use crate::ui::style;
use crate::ui::{Element, Message};
use chrono::{DateTime, Local, NaiveDate};
use iced_core::{Alignment, Color, Length, Padding};
use iced_widget::{Space, button, column, container, row, scrollable, text, tooltip};

fn calendar_meta<'a>(calendars: &'a [CalendarMeta], id: &str) -> Option<&'a CalendarMeta> {
    calendars.iter().find(|c| c.id == id)
}

fn build_agenda_row<'a>(
    theme: AppTheme,
    occ: &EventOccurrence,
    event_color: Color,
    cal_name: String,
    past: bool,
    use_24h: bool,
) -> Element<'a> {
    let palette = theme.event_pill_colors(event_color, past, false);

    let edge = container(Space::new().width(4.0).height(Length::Fill))
        .width(Length::Fixed(4.0))
        .height(Length::Fixed(40.0))
        .style(style::color_dot(palette.edge, 2.0));

    let time_col = container(
        text(occ.format_time_range(use_24h))
            .size(11.5)
            .font(style::mono_font())
            .color(if past {
                theme.muted_day_fg()
            } else {
                theme.fg_muted
            })
            .wrapping(iced_core::text::Wrapping::None),
    )
    .width(Length::Fixed(104.0));

    let mut details_col = column![
        text(occ.summary.clone())
            .size(13.5)
            .font(style::bold_font())
            .color(palette.text)
            .wrapping(iced_core::text::Wrapping::WordOrGlyph),
    ]
    .spacing(3)
    .width(Length::Fill);

    let mut meta_row = row![
        container(Space::new().width(7.0).height(7.0))
            .style(style::color_dot(event_color, 999.0)),
        text(cal_name).size(11.5).color(theme.fg_muted),
    ]
    .spacing(6)
    .align_y(Alignment::Center);

    if let Some(loc) = &occ.location {
        meta_row = meta_row
            .push(text("·").size(11.5).color(theme.muted_day_fg()))
            .push(render_icon("location", 11.5, theme.fg_muted))
            .push(
                text(loc.clone())
                    .size(11.5)
                    .color(theme.fg_muted)
                    .wrapping(iced_core::text::Wrapping::None),
            );
    }

    if occ.is_recurring_instance {
        meta_row = meta_row
            .push(text("·").size(11.5).color(theme.muted_day_fg()))
            .push(render_icon("repeat", 11.5, theme.fg_muted));
    }

    details_col = details_col.push(meta_row);

    let content = row![edge, time_col, details_col]
        .spacing(12)
        .align_y(Alignment::Center)
        .width(Length::Fill);

    let row_btn = button(
        container(content)
            .width(Length::Fill)
            .padding(Padding::from([8, 12])),
    )
    .width(Length::Fill)
    .padding(0)
    .style(style::event_pill_button(
        theme,
        event_color,
        past,
        false,
        false,
    ))
    .on_press(Message::OpenEventPreview(occ.clone()));

    let mut tip = column![
        text(occ.summary.clone())
            .size(12.5)
            .font(style::bold_font())
            .color(theme.fg)
            .wrapping(iced_core::text::Wrapping::WordOrGlyph),
        text(occ.format_time_range(use_24h))
            .size(11.5)
            .font(style::mono_font())
            .color(theme.fg_muted),
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

    style::rich_tooltip(theme, row_btn, tip, tooltip::Position::Top)
}

#[allow(clippy::too_many_arguments)]
pub fn view_agenda<'a>(
    theme: AppTheme,
    layout: &'a AgendaLayout,
    selected_date: NaiveDate,
    displayed_month: NaiveDate,
    today: NaiveDate,
    now: DateTime<Local>,
    week_start_monday: bool,
    show_week_numbers: bool,
    use_24h: bool,
    embed_date_chooser: bool,
    event_dots: &'a [bool; 42],
    calendars: &'a [CalendarMeta],
    search_query: &'a str,
) -> Element<'a> {
    let mut root_col = column![].width(Length::Fill).height(Length::Fill);

    if embed_date_chooser {
        let chooser = container(view_date_chooser(
            theme,
            displayed_month,
            selected_date,
            today,
            week_start_monday,
            show_week_numbers,
            false,
            Some(event_dots),
            Message::MiniCalendarShiftMonth,
            None,
            Message::SelectDate,
        ))
        .width(Length::Fill)
        .style(style::sidebar_surface(theme));

        let sep = container(Space::new().width(Length::Fill).height(1))
            .width(Length::Fill)
            .height(Length::Fixed(1.0))
            .style(style::hairline_rule(theme));

        root_col = root_col.push(chooser).push(sep);
    }

    let mut list_col = column![]
        .spacing(10)
        .padding(Padding {
            top: 14.0,
            right: 18.0,
            bottom: 24.0,
            left: 18.0,
        })
        .width(Length::Fill);

    if layout.groups.is_empty() {
        let empty_title = if search_query.trim().is_empty() {
            "No upcoming events"
        } else {
            "No matching events"
        };
        let empty_sub = if search_query.trim().is_empty() {
            format!(
                "Nothing scheduled from {} to {}.",
                format_relative_date_short(layout.range_start, today),
                format_relative_date_short(layout.range_end, today)
            )
        } else {
            format!("No events matched \"{}\".", search_query.trim())
        };

        let empty_card = container(
            column![
                render_icon("calendar", 28.0, theme.muted_day_fg()),
                text(empty_title)
                    .size(15)
                    .font(style::bold_font())
                    .color(theme.fg),
                text(empty_sub)
                    .size(12.5)
                    .font(style::italic_font())
                    .color(theme.fg_muted),
                button(
                    row![
                        render_icon("plus", 14.0, theme.accent_fg),
                        text("New Event").size(13).font(style::bold_font()),
                    ]
                    .spacing(6)
                    .align_y(Alignment::Center),
                )
                .padding([8, 14])
                .style(style::primary_button(theme))
                .on_press(Message::OpenQuickAdd {
                    date: Some(selected_date),
                    hour: None,
                }),
            ]
            .spacing(10)
            .align_x(Alignment::Center),
        )
        .width(Length::Fill)
        .padding(Padding::from([36, 20]))
        .align_x(Alignment::Center);

        list_col = list_col.push(empty_card);
    } else {
        for group in &layout.groups {
            let day_heading = format_agenda_day_heading(group.date, today);

            let add_day_btn = button(render_icon("plus", 13.0, theme.fg_muted))
                .padding([3, 5])
                .style(style::icon_button(theme, false))
                .on_press(Message::OpenQuickAdd {
                    date: Some(group.date),
                    hour: None,
                });

            let header_row = row![
                text(day_heading)
                    .size(13)
                    .font(style::bold_font())
                    .color(if group.is_today {
                        theme.accent
                    } else {
                        theme.fg_muted
                    }),
                Space::new().width(Length::Fill),
                style::tooltip(
                    theme,
                    add_day_btn,
                    format!(
                        "New Event on {}",
                        format_relative_date_short(group.date, today)
                    ),
                    tooltip::Position::Left,
                ),
            ]
            .align_y(Alignment::Center)
            .padding(Padding {
                top: 4.0,
                right: 2.0,
                bottom: 2.0,
                left: 2.0,
            });

            let mut day_items = column![header_row].spacing(6).width(Length::Fill);

            for occ in &group.occurrences {
                let cal = calendar_meta(calendars, &occ.calendar_id);
                let color = cal
                    .map(|c| c.resolved_color(&theme))
                    .unwrap_or(theme.accent);
                let cal_name = cal
                    .map(|c| c.name.clone())
                    .unwrap_or_else(|| "Calendar".to_string());
                let past = occ.is_past(now);

                day_items = day_items.push(build_agenda_row(
                    theme, occ, color, cal_name, past, use_24h,
                ));
            }

            list_col = list_col.push(day_items);
        }
    }

    let scroll = scrollable(
        container(list_col)
            .max_width(820.0)
            .width(Length::Fill),
    )
    .width(Length::Fill)
    .height(Length::Fill)
    .style(style::clean_scrollable(theme));

    root_col
        .push(
            container(scroll)
                .width(Length::Fill)
                .height(Length::Fill)
                .align_x(Alignment::Center),
        )
        .into()
}
