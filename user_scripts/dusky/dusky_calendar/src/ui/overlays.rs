//! Backdrop-isolated overlays: Quick-Add card, Event Preview popover, Day Overflow popover,
//! Full Event Editor modal (with inline Date Chooser), Calendar Management modal,
//! Search results dropdown, `.ics` Drag-and-Drop overlay, and Toast banner.

use crate::icons::render_icon;
use crate::model::{
    CalendarColor, CalendarMeta, EditScope, EventOccurrence, RecurrenceEnd, RecurrenceFreq,
    RecurrenceRule, build_location_map_url, event::REMINDER_PRESETS, extract_meeting_links,
    format_quick_add_heading, format_relative_date_short, format_reminder_minutes,
};
use crate::storage::UserPrefs;
use crate::theme::{AppTheme, SemanticToken, contrasting_fg};
use crate::ui::header::HEADER_HEIGHT;
use crate::ui::sidebar::view_date_chooser;
use crate::ui::style;
use crate::ui::{
    CalendarManagerState, EditorDateTarget, Element, EventEditorDraft, Message, Overlay,
    QuickAddDraft, ToastAction, ToastNotice,
};
use chrono::{DateTime, Local, NaiveDate, NaiveTime};
use iced_core::{Alignment, Length, Padding};
use iced_widget::{
    Space, Stack, button, column, container, mouse_area, opaque, row, scrollable, text,
    text_editor, text_input, tooltip,
};

pub fn quick_add_input_id() -> iced_core::widget::Id {
    iced_core::widget::Id::new("dusky-calendar-quick-add-title")
}

pub fn editor_summary_input_id() -> iced_core::widget::Id {
    iced_core::widget::Id::new("dusky-calendar-editor-summary")
}

fn calendar_meta<'a>(calendars: &'a [CalendarMeta], id: &str) -> Option<&'a CalendarMeta> {
    calendars.iter().find(|c| c.id == id)
}

fn parse_hm_preview(raw: &str) -> Option<NaiveTime> {
    let trimmed = raw.trim();
    if let Ok(t) = NaiveTime::parse_from_str(trimmed, "%H:%M") {
        return Some(t);
    }
    let upper = trimmed.to_ascii_uppercase();
    for fmt in ["%I:%M %p", "%I:%M%p", "%I %p", "%I%p"] {
        if let Ok(t) = NaiveTime::parse_from_str(&upper, fmt) {
            return Some(t);
        }
    }
    None
}

fn wrap_modal_backdrop<'a>(
    theme: AppTheme,
    card: Element<'a>,
    max_width: f32,
) -> Element<'a> {
    let centered = container(
        mouse_area(
            container(card)
                .max_width(max_width)
                .width(Length::Fill)
                .style(style::card_surface(theme, 14.0)),
        )
        .on_press(Message::NoOp),
    )
    .width(Length::Fill)
    .height(Length::Fill)
    .padding(Padding::from([24, 18]))
    .align_x(Alignment::Center)
    .align_y(Alignment::Center);

    opaque(
        mouse_area(
            container(centered)
                .width(Length::Fill)
                .height(Length::Fill)
                .style(style::backdrop_scrim(theme)),
        )
        .on_press(Message::CloseOverlay),
    )
    .into()
}

fn view_quick_add<'a>(
    theme: AppTheme,
    draft: &'a QuickAddDraft,
    calendars: &'a [CalendarMeta],
    today: NaiveDate,
    use_24h: bool,
) -> Element<'a> {
    let heading = format_quick_add_heading(
        draft.start_date,
        draft.end_date,
        draft.all_day,
        parse_hm_preview(&draft.start_time),
        parse_hm_preview(&draft.end_time),
        today,
        use_24h,
    );
    let end_date = draft.end_date.max(draft.start_date);
    let date_subline = if end_date > draft.start_date {
        let days = (end_date - draft.start_date).num_days() + 1;
        format!(
            "{} – {} · {days} days",
            draft.start_date.format("%a, %b %-d"),
            end_date.format("%a, %b %-d, %Y")
        )
    } else {
        draft.start_date.format("%A, %B %-d, %Y").to_string()
    };

    let close_btn = button(render_icon("close", 15.0, theme.fg_muted))
        .padding([5, 6])
        .style(style::icon_button(theme, false))
        .on_press(Message::CloseOverlay);

    let header = row![
        column![
            text(heading)
                .size(15)
                .font(style::bold_font())
                .color(theme.fg),
            text(date_subline).size(12).color(theme.fg_muted),
        ]
        .spacing(2)
        .width(Length::Fill),
        style::tooltip(theme, close_btn, "Close (Esc)", tooltip::Position::Bottom),
    ]
    .spacing(8)
    .align_y(Alignment::Center);

    let title_input = text_input("Event title…", &draft.title)
        .id(quick_add_input_id())
        .on_input(Message::QuickAddTitleChanged)
        .on_submit(Message::QuickAddSubmit)
        .padding([9, 12])
        .size(14)
        .style(style::text_input_style(theme));

    let all_day_btn = button(
        row![
            render_icon(
                if draft.all_day { "check" } else { "clock" },
                13.5,
                if draft.all_day {
                    contrasting_fg(theme.accent)
                } else {
                    theme.fg_muted
                },
            ),
            text("All day").size(13).font(if draft.all_day {
                style::bold_font()
            } else {
                style::regular_font()
            }),
        ]
        .spacing(6)
        .align_y(Alignment::Center),
    )
    .padding([6, 11])
    .style(style::toggle_chip(theme, draft.all_day))
    .on_press(Message::QuickAddToggleAllDay);

    let mut time_row = row![all_day_btn].spacing(8).align_y(Alignment::Center);

    if !draft.all_day {
        let time_w = if use_24h { 74.0 } else { 90.0 };
        let start_ph = if use_24h { "09:00" } else { "9:00 AM" };
        let end_ph = if use_24h { "10:00" } else { "10:00 AM" };
        let range_group = row![
            text_input(start_ph, &draft.start_time)
                .on_input(Message::QuickAddStartTimeChanged)
                .on_submit(Message::QuickAddSubmit)
                .padding([6, 8])
                .size(12.5)
                .font(style::mono_font())
                .width(Length::Fixed(time_w))
                .style(style::text_input_style(theme)),
            text("–").size(13).color(theme.fg_muted),
            text_input(end_ph, &draft.end_time)
                .on_input(Message::QuickAddEndTimeChanged)
                .on_submit(Message::QuickAddSubmit)
                .padding([6, 8])
                .size(12.5)
                .font(style::mono_font())
                .width(Length::Fixed(time_w))
                .style(style::text_input_style(theme)),
        ]
        .spacing(6)
        .align_y(Alignment::Center);

        time_row = time_row.push(range_group);
    }

    let time_controls = time_row.wrap().vertical_spacing(6);

    // Calendar selector: collapse to a compact badge when only one calendar exists.
    let cal_section: Element<'a> = if calendars.len() <= 1 {
        let active_cal = calendar_meta(calendars, &draft.calendar_id).or_else(|| calendars.first());
        let color = active_cal
            .map(|c| c.resolved_color(&theme))
            .unwrap_or(theme.accent);
        let name = active_cal.map(|c| c.name.as_str()).unwrap_or("Personal");
        container(
            row![
                container(Space::new().width(9.0).height(9.0))
                    .style(style::color_dot(color, 999.0)),
                text(name)
                    .size(12.5)
                    .font(style::bold_font())
                    .color(theme.fg),
                Space::new().width(Length::Fill),
                text("Calendar").size(11.5).color(theme.muted_day_fg()),
            ]
            .spacing(8)
            .align_y(Alignment::Center),
        )
        .width(Length::Fill)
        .padding([7, 10])
        .style(style::flat_card(theme, 9.0))
        .into()
    } else {
        let mut cal_list = column![].spacing(3);
        for cal in calendars {
            let selected = cal.id == draft.calendar_id;
            let dot = container(Space::new().width(10.0).height(10.0))
                .style(style::color_dot(cal.resolved_color(&theme), 999.0));
            let mut r = row![
                dot,
                text(&cal.name).size(13).color(theme.fg),
                Space::new().width(Length::Fill),
            ]
            .spacing(9)
            .align_y(Alignment::Center);
            if selected {
                r = r.push(render_icon("check", 13.0, theme.accent));
            }
            cal_list = cal_list.push(
                button(r)
                    .width(Length::Fill)
                    .padding([7, 10])
                    .style(style::subtle_row_button(theme, selected))
                    .on_press(Message::QuickAddSelectCalendar(cal.id.clone())),
            );
        }

        container(
            scrollable(cal_list)
                .height(Length::Shrink)
                .style(style::clean_scrollable(theme)),
        )
        .max_height(145.0)
        .padding(4)
        .style(style::flat_card(theme, 9.0))
        .into()
    };

    let mut body = column![header, title_input, time_controls, cal_section]
        .spacing(12)
        .padding(18);

    if let Some(err) = &draft.error {
        body = body.push(
            container(text(err).size(12).color(theme.danger_fg))
                .width(Length::Fill)
                .padding([7, 10])
                .style(style::error_banner(theme)),
        );
    }

    let can_save = !draft.title.trim().is_empty();
    let mut save_btn = button(
        container(text("Save Event").size(13).font(style::bold_font()))
            .width(Length::Fill)
            .align_x(Alignment::Center),
    )
    .width(Length::FillPortion(1))
    .padding([9, 12])
    .style(style::primary_button(theme));
    if can_save {
        save_btn = save_btn.on_press(Message::QuickAddSave);
    }

    let actions = row![
        button(
            container(text("More Details…").size(13).font(style::bold_font()))
                .width(Length::Fill)
                .align_x(Alignment::Center),
        )
        .width(Length::FillPortion(1))
        .padding([9, 12])
        .style(style::secondary_button(theme))
        .on_press(Message::QuickAddPromoteToEditor),
        save_btn,
    ]
    .spacing(10)
    .width(Length::Fill);

    body = body.push(actions);
    wrap_modal_backdrop(theme, body.into(), 390.0)
}

fn view_event_preview<'a>(
    theme: AppTheme,
    occ: &'a EventOccurrence,
    confirm_delete: bool,
    calendars: &'a [CalendarMeta],
    today: NaiveDate,
    use_24h: bool,
) -> Element<'a> {
    let cal = calendar_meta(calendars, &occ.calendar_id);
    let event_color = cal
        .map(|c| c.resolved_color(&theme))
        .unwrap_or(theme.accent);
    let cal_name = cal
        .map(|c| c.name.clone())
        .unwrap_or_else(|| "Calendar".to_string());

    let trash_msg = if occ.is_recurring_instance {
        Message::PreviewRequestDelete
    } else {
        Message::PreviewConfirmDelete(EditScope::All)
    };

    let export_btn = button(render_icon("export", 15.0, theme.fg_muted))
        .padding([6, 7])
        .style(style::icon_button(theme, false))
        .on_press(Message::PreviewExportEvent);
    let edit_btn = button(render_icon("edit", 15.0, theme.fg))
        .padding([6, 7])
        .style(style::icon_button(theme, false))
        .on_press(Message::PreviewEditEvent);
    let delete_btn = button(render_icon("trash", 15.0, theme.danger))
        .padding([6, 7])
        .style(style::icon_button(theme, confirm_delete))
        .on_press(trash_msg);
    let close_btn = button(render_icon("close", 15.0, theme.fg_muted))
        .padding([6, 7])
        .style(style::icon_button(theme, false))
        .on_press(Message::CloseOverlay);

    let action_sep_left = container(Space::new().width(1).height(16))
        .width(Length::Fixed(1.0))
        .height(Length::Fixed(16.0))
        .style(style::hairline_rule(theme));
    let action_sep_right = container(Space::new().width(1).height(16))
        .width(Length::Fixed(1.0))
        .height(Length::Fixed(16.0))
        .style(style::hairline_rule(theme));

    let top_bar = row![
        container(Space::new().width(10.0).height(10.0))
            .style(style::color_dot(event_color, 999.0)),
        text(cal_name)
            .size(12)
            .font(style::bold_font())
            .color(theme.fg_muted),
        Space::new().width(Length::Fill),
        style::tooltip(
            theme,
            export_btn,
            "Export .ics to Downloads",
            tooltip::Position::Bottom,
        ),
        style::tooltip(theme, edit_btn, "Edit Event", tooltip::Position::Bottom),
        action_sep_left,
        style::tooltip(
            theme,
            delete_btn,
            "Delete Event",
            tooltip::Position::Bottom,
        ),
        action_sep_right,
        style::tooltip(theme, close_btn, "Close (Esc)", tooltip::Position::Bottom),
    ]
    .spacing(6)
    .align_y(Alignment::Center);

    let title_block = column![
        text(&occ.summary)
            .size(18)
            .font(style::bold_font())
            .color(theme.fg),
        row![
            render_icon("clock", 14.0, theme.fg_muted),
            text(occ.format_relative_date_time_summary(today, use_24h))
                .size(12.5)
                .color(theme.fg_muted),
        ]
        .spacing(7)
        .align_y(Alignment::Center),
    ]
    .spacing(6);

    let mut body = column![top_bar, title_block].spacing(12).padding(18);

    if let Some(rrule_str) = &occ.rrule {
        let rule = RecurrenceRule::from_rrule_string(Some(rrule_str));
        body = body.push(
            row![
                render_icon("repeat", 14.0, theme.fg_muted),
                text(rule.human_summary()).size(12.5).color(theme.fg_muted),
            ]
            .spacing(8)
            .align_y(Alignment::Center),
        );
    }

    let (meeting_links, location_is_url) =
        extract_meeting_links(occ.location.as_deref(), occ.description.as_deref());

    if !meeting_links.is_empty() {
        let mut links_col = column![].spacing(6).width(Length::Fill);
        for (idx, link) in meeting_links.into_iter().enumerate() {
            let is_primary = idx == 0;
            let icon_color = if is_primary {
                contrasting_fg(theme.accent)
            } else {
                theme.fg_muted
            };
            let btn_content = row![
                text(link.button_label()).size(13).font(style::bold_font()),
                Space::new().width(Length::Fill),
                render_icon("chevron_right", 14.0, icon_color),
            ]
            .spacing(8)
            .align_y(Alignment::Center)
            .width(Length::Fill);

            let btn = if is_primary {
                button(btn_content)
                    .width(Length::Fill)
                    .padding([8, 12])
                    .style(style::primary_button(theme))
                    .on_press(Message::OpenExternalUrl(link.url))
            } else {
                button(btn_content)
                    .width(Length::Fill)
                    .padding([7, 12])
                    .style(style::secondary_button(theme))
                    .on_press(Message::OpenExternalUrl(link.url))
            };
            links_col = links_col.push(btn);
        }
        body = body.push(links_col);
    }

    if let Some(loc) = &occ.location {
        if !location_is_url {
            let mut loc_row = row![
                render_icon("location", 14.0, theme.fg_muted),
                text(loc)
                    .size(13)
                    .color(theme.fg)
                    .width(Length::Fill)
                    .wrapping(iced_core::text::Wrapping::WordOrGlyph),
            ]
            .spacing(8)
            .align_y(Alignment::Center)
            .width(Length::Fill);

            if let Some(map_url) = build_location_map_url(loc) {
                loc_row = loc_row.push(
                    button(
                        row![
                            text("Open Map").size(11.5).font(style::bold_font()),
                            render_icon("chevron_right", 11.5, theme.fg_muted),
                        ]
                        .spacing(3)
                        .align_y(Alignment::Center),
                    )
                    .padding([4, 9])
                    .style(style::secondary_button(theme))
                    .on_press(Message::OpenExternalUrl(map_url)),
                );
            }

            body = body.push(loc_row);
        }
    }

    if !occ.reminders.is_empty() {
        let rem_str = occ
            .reminders
            .iter()
            .map(|&m| format_reminder_minutes(m))
            .collect::<Vec<_>>()
            .join(", ");
        body = body.push(
            row![
                render_icon("bell", 14.0, theme.fg_muted),
                text(rem_str).size(12.5).color(theme.fg_muted),
            ]
            .spacing(8)
            .align_y(Alignment::Center),
        );
    }

    if let Some(desc) = &occ.description {
        let sep = container(Space::new().width(Length::Fill).height(1))
            .width(Length::Fill)
            .height(Length::Fixed(1.0))
            .style(style::hairline_rule(theme));
        let notes_scroll = container(
            scrollable(text(desc).size(13).color(theme.fg))
                .height(Length::Shrink)
                .style(style::clean_scrollable(theme)),
        )
        .max_height(180.0);
        body = body.push(sep).push(notes_scroll);
    }

    if confirm_delete && occ.is_recurring_instance {
        let scope_picker = column![
            text("Delete recurring event?")
                .size(13)
                .font(style::bold_font())
                .color(theme.danger_fg),
            row![
                button(text("Only This Occurrence").size(12).font(style::bold_font()))
                    .padding([7, 10])
                    .style(style::danger_button(theme))
                    .on_press(Message::PreviewConfirmDelete(EditScope::Single)),
                button(text("All in Series").size(12).font(style::bold_font()))
                    .padding([7, 10])
                    .style(style::danger_button(theme))
                    .on_press(Message::PreviewConfirmDelete(EditScope::All)),
                button(text("Cancel").size(12))
                    .padding([7, 10])
                    .style(style::secondary_button(theme))
                    .on_press(Message::PreviewCancelDelete),
            ]
            .spacing(6)
            .align_y(Alignment::Center)
            .wrap()
            .vertical_spacing(6),
        ]
        .spacing(8);

        body = body.push(
            container(scope_picker)
                .width(Length::Fill)
                .padding(10)
                .style(style::error_banner(theme)),
        );
    }

    wrap_modal_backdrop(theme, body.into(), 430.0)
}

fn view_day_overflow<'a>(
    theme: AppTheme,
    date: NaiveDate,
    occurrences: &'a [EventOccurrence],
    calendars: &'a [CalendarMeta],
    now: DateTime<Local>,
    use_24h: bool,
) -> Element<'a> {
    let header = row![
        column![
            text(date.format("%A").to_string())
                .size(12)
                .color(theme.fg_muted),
            text(date.format("%B %-d, %Y").to_string())
                .size(18)
                .font(style::bold_font())
                .color(theme.fg),
        ]
        .spacing(2),
        Space::new().width(Length::Fill),
        button(render_icon("close", 15.0, theme.fg_muted))
            .padding([6, 7])
            .style(style::icon_button(theme, false))
            .on_press(Message::CloseOverlay),
    ]
    .align_y(Alignment::Center);

    let mut list = column![].spacing(6).width(Length::Fill);
    if occurrences.is_empty() {
        list = list.push(
            text("No events on this day.")
                .size(13)
                .font(style::italic_font())
                .color(theme.muted_day_fg()),
        );
    } else {
        for occ in occurrences {
            let color = calendar_meta(calendars, &occ.calendar_id)
                .map(|c| c.resolved_color(&theme))
                .unwrap_or(theme.accent);
            let past = occ.is_past(now);
            let palette = theme.event_pill_colors(color, past, false);

            let item_row = row![
                container(Space::new().width(4.0).height(26.0))
                    .style(style::color_dot(palette.edge, 2.0)),
                column![
                    text(&occ.summary)
                        .size(13)
                        .font(style::bold_font())
                        .color(palette.text),
                    text(occ.format_time_range(use_24h))
                        .size(11)
                        .font(style::mono_font())
                        .color(theme.fg_muted),
                ]
                .spacing(1)
                .width(Length::Fill),
            ]
            .spacing(9)
            .align_y(Alignment::Center);

            list = list.push(
                button(container(item_row).width(Length::Fill).padding([6, 10]))
                    .width(Length::Fill)
                    .padding(0)
                    .style(style::event_pill_button(theme, color, past, false, false))
                    .on_press(Message::OpenEventPreview(occ.clone())),
            );
        }
    }

    let scroll = container(
        scrollable(list)
            .height(Length::Shrink)
            .style(style::clean_scrollable(theme)),
    )
    .max_height(320.0);

    let add_btn = button(
        row![
            render_icon("plus", 14.0, theme.accent_fg),
            text("New Event…").size(13).font(style::bold_font()),
        ]
        .spacing(6)
        .align_y(Alignment::Center),
    )
    .width(Length::Fill)
    .padding([9, 12])
    .style(style::primary_button(theme))
    .on_press(Message::OpenQuickAdd {
        date: Some(date),
        hour: None,
    });

    let body = column![header, scroll, add_btn].spacing(14).padding(18);
    wrap_modal_backdrop(theme, body.into(), 370.0)
}

fn view_event_editor<'a>(
    theme: AppTheme,
    draft: &'a EventEditorDraft,
    calendars: &'a [CalendarMeta],
    today: NaiveDate,
    week_start_monday: bool,
    use_24h: bool,
) -> Element<'a> {
    let is_editing = draft.original_key.is_some();
    let heading = if is_editing {
        "Edit Event"
    } else {
        "New Event"
    };
    let can_save = !draft.summary.trim().is_empty();

    let cancel_btn = button(text("Cancel").size(13))
        .padding([7, 12])
        .style(style::secondary_button(theme))
        .on_press(Message::CloseOverlay);

    let mut save_btn = button(text("Save").size(13).font(style::bold_font()))
        .padding([7, 16])
        .style(style::primary_button(theme));
    if can_save {
        save_btn = save_btn.on_press(Message::EditorSave);
    }

    let header = row![
        style::tooltip(theme, cancel_btn, "Cancel (Esc)", tooltip::Position::Bottom),
        container(
            text(heading)
                .size(15.5)
                .font(style::bold_font())
                .color(theme.fg),
        )
        .width(Length::Fill)
        .align_x(Alignment::Center),
        style::tooltip(
            theme,
            save_btn,
            if can_save {
                "Save Event (Ctrl+S)"
            } else {
                "Enter an event title to save"
            },
            tooltip::Position::Bottom,
        ),
    ]
    .spacing(10)
    .align_y(Alignment::Center);

    let header_sep = container(Space::new().width(Length::Fill).height(1))
        .width(Length::Fill)
        .height(Length::Fixed(1.0))
        .style(style::hairline_rule(theme));

    let mut form = column![].spacing(14).padding(Padding {
        top: 14.0,
        right: 18.0,
        bottom: 18.0,
        left: 18.0,
    });

    if let Some(err) = &draft.error {
        form = form.push(
            container(text(err).size(12.5).color(theme.danger_fg))
                .width(Length::Fill)
                .padding([8, 12])
                .style(style::error_banner(theme)),
        );
    }

    // Recurring edit scope selector when editing an existing recurring event.
    if is_editing && draft.is_recurring_source {
        let single_active = draft.edit_scope == EditScope::Single;
        let all_active = draft.edit_scope == EditScope::All;
        let scope_bar = container(
            row![
                text("Apply changes to:")
                    .size(13)
                    .font(style::bold_font())
                    .color(theme.fg),
                button(
                    text("Only This Occurrence")
                        .size(12.5)
                        .font(if single_active {
                            style::bold_font()
                        } else {
                            style::regular_font()
                        }),
                )
                .padding([6, 11])
                .style(style::toggle_chip(theme, single_active))
                .on_press(Message::EditorScopeChanged(EditScope::Single)),
                button(
                    text("All in Series")
                        .size(12.5)
                        .font(if all_active {
                            style::bold_font()
                        } else {
                            style::regular_font()
                        }),
                )
                .padding([6, 11])
                .style(style::toggle_chip(theme, all_active))
                .on_press(Message::EditorScopeChanged(EditScope::All)),
            ]
            .spacing(8)
            .align_y(Alignment::Center)
            .wrap()
            .vertical_spacing(6),
        )
        .width(Length::Fill)
        .padding([8, 12])
        .style(style::flat_card(theme, 9.0));
        form = form.push(scope_bar);
    }

    // 1. Summary & Calendar section.
    let summary_input = text_input("Title", &draft.summary)
        .id(editor_summary_input_id())
        .on_input(Message::EditorSummaryChanged)
        .on_submit(Message::EditorSave)
        .padding([10, 12])
        .size(14.5)
        .style(style::text_input_style(theme));

    let allow_calendar_switch =
        !(is_editing && draft.is_recurring_source && draft.edit_scope == EditScope::Single);

    let cal_selector: Element<'a> = if allow_calendar_switch {
        let mut cal_chips = row![].spacing(6).align_y(Alignment::Center);
        for cal in calendars {
            let selected = cal.id == draft.calendar_id;
            let color = cal.resolved_color(&theme);
            cal_chips = cal_chips.push(
                button(
                    row![
                        container(Space::new().width(9.0).height(9.0))
                            .style(style::color_dot(color, 999.0)),
                        text(&cal.name).size(12.5),
                    ]
                    .spacing(6)
                    .align_y(Alignment::Center),
                )
                .padding([6, 10])
                .style(style::subtle_row_button(theme, selected))
                .on_press(Message::EditorSelectCalendar(cal.id.clone())),
            );
        }

        scrollable(cal_chips)
            .direction(scrollable::Direction::Horizontal(
                scrollable::Scrollbar::new().width(4).scroller_width(4),
            ))
            .style(style::clean_scrollable(theme))
            .into()
    } else {
        let locked_cal = calendar_meta(calendars, &draft.calendar_id);
        let color = locked_cal
            .map(|c| c.resolved_color(&theme))
            .unwrap_or(theme.accent);
        let name = locked_cal
            .map(|c| c.name.as_str())
            .unwrap_or("Calendar");
        container(
            row![
                container(Space::new().width(9.0).height(9.0))
                    .style(style::color_dot(color, 999.0)),
                text(name).size(12.5).font(style::bold_font()).color(theme.fg),
                text("· Single-occurrence edits stay in this calendar")
                    .size(11.5)
                    .color(theme.muted_day_fg()),
            ]
            .spacing(7)
            .align_y(Alignment::Center),
        )
        .padding([6, 10])
        .style(style::flat_card(theme, 8.0))
        .into()
    };

    form = form.push(
        column![
            text("Event")
                .size(12)
                .font(style::bold_font())
                .color(theme.fg_muted),
            summary_input,
            cal_selector,
        ]
        .spacing(8),
    );

    // 2. Schedule section (All-Day toggle, Start/End Date + Time with steppers & inline Date Chooser).
    let all_day_row = row![
        text("Schedule")
            .size(12)
            .font(style::bold_font())
            .color(theme.fg_muted),
        Space::new().width(Length::Fill),
        button(
            row![
                render_icon(
                    if draft.all_day { "check" } else { "clock" },
                    13.5,
                    if draft.all_day {
                        contrasting_fg(theme.accent)
                    } else {
                        theme.fg_muted
                    },
                ),
                text("All Day").size(12.5).font(style::bold_font()),
            ]
            .spacing(5)
            .align_y(Alignment::Center),
        )
        .padding([6, 11])
        .style(style::toggle_chip(theme, draft.all_day))
        .on_press(Message::EditorToggleAllDay),
    ]
    .align_y(Alignment::Center);

    let time_input_w = if use_24h { 70.0 } else { 88.0 };
    let start_time_ph = if use_24h { "09:00" } else { "9:00 AM" };
    let end_time_ph = if use_24h { "10:00" } else { "10:00 AM" };

    let start_row = {
        let start_picker_btn = button(render_icon("calendar", 14.0, theme.fg_muted))
            .padding([6, 7])
            .style(style::icon_button(
                theme,
                draft
                    .active_date_picker
                    .is_some_and(|(t, _)| t == EditorDateTarget::Start),
            ))
            .on_press(Message::EditorToggleDatePicker(EditorDateTarget::Start));

        let date_group = row![
            container(text("Starts").size(12.5).color(theme.fg_muted))
                .width(Length::Fixed(48.0)),
            button(render_icon("chevron_left", 13.0, theme.fg_muted))
                .padding([6, 7])
                .style(style::secondary_button(theme))
                .on_press(Message::EditorStepDate {
                    is_start: true,
                    delta_days: -1,
                }),
            text_input("YYYY-MM-DD", &draft.start_date_text)
                .on_input(Message::EditorStartDateChanged)
                .padding([6, 8])
                .size(12.5)
                .font(style::mono_font())
                .width(Length::Fixed(108.0))
                .style(style::text_input_style(theme)),
            button(render_icon("chevron_right", 13.0, theme.fg_muted))
                .padding([6, 7])
                .style(style::secondary_button(theme))
                .on_press(Message::EditorStepDate {
                    is_start: true,
                    delta_days: 1,
                }),
            style::tooltip(
                theme,
                start_picker_btn,
                "Pick Start Date",
                tooltip::Position::Top,
            ),
        ]
        .spacing(5)
        .align_y(Alignment::Center);

        let mut r = row![date_group].spacing(10).align_y(Alignment::Center);

        if !draft.all_day {
            let time_group = row![
                button(render_icon("minus", 12.0, theme.fg_muted))
                    .padding([6, 6])
                    .style(style::secondary_button(theme))
                    .on_press(Message::EditorStepTime {
                        is_start: true,
                        delta_minutes: -15,
                    }),
                text_input(start_time_ph, &draft.start_time_text)
                    .on_input(Message::EditorStartTimeChanged)
                    .padding([6, 8])
                    .size(12.5)
                    .font(style::mono_font())
                    .width(Length::Fixed(time_input_w))
                    .style(style::text_input_style(theme)),
                button(render_icon("plus", 12.0, theme.fg_muted))
                    .padding([6, 6])
                    .style(style::secondary_button(theme))
                    .on_press(Message::EditorStepTime {
                        is_start: true,
                        delta_minutes: 15,
                    }),
            ]
            .spacing(5)
            .align_y(Alignment::Center);

            r = r.push(time_group);
        }

        r.wrap().vertical_spacing(6)
    };

    let end_row = {
        let end_picker_btn = button(render_icon("calendar", 14.0, theme.fg_muted))
            .padding([6, 7])
            .style(style::icon_button(
                theme,
                draft
                    .active_date_picker
                    .is_some_and(|(t, _)| t == EditorDateTarget::End),
            ))
            .on_press(Message::EditorToggleDatePicker(EditorDateTarget::End));

        let date_group = row![
            container(text("Ends").size(12.5).color(theme.fg_muted))
                .width(Length::Fixed(48.0)),
            button(render_icon("chevron_left", 13.0, theme.fg_muted))
                .padding([6, 7])
                .style(style::secondary_button(theme))
                .on_press(Message::EditorStepDate {
                    is_start: false,
                    delta_days: -1,
                }),
            text_input("YYYY-MM-DD", &draft.end_date_text)
                .on_input(Message::EditorEndDateChanged)
                .padding([6, 8])
                .size(12.5)
                .font(style::mono_font())
                .width(Length::Fixed(108.0))
                .style(style::text_input_style(theme)),
            button(render_icon("chevron_right", 13.0, theme.fg_muted))
                .padding([6, 7])
                .style(style::secondary_button(theme))
                .on_press(Message::EditorStepDate {
                    is_start: false,
                    delta_days: 1,
                }),
            style::tooltip(
                theme,
                end_picker_btn,
                "Pick End Date",
                tooltip::Position::Top,
            ),
        ]
        .spacing(5)
        .align_y(Alignment::Center);

        let mut r = row![date_group].spacing(10).align_y(Alignment::Center);

        if !draft.all_day {
            let time_group = row![
                button(render_icon("minus", 12.0, theme.fg_muted))
                    .padding([6, 6])
                    .style(style::secondary_button(theme))
                    .on_press(Message::EditorStepTime {
                        is_start: false,
                        delta_minutes: -15,
                    }),
                text_input(end_time_ph, &draft.end_time_text)
                    .on_input(Message::EditorEndTimeChanged)
                    .padding([6, 8])
                    .size(12.5)
                    .font(style::mono_font())
                    .width(Length::Fixed(time_input_w))
                    .style(style::text_input_style(theme)),
                button(render_icon("plus", 12.0, theme.fg_muted))
                    .padding([6, 6])
                    .style(style::secondary_button(theme))
                    .on_press(Message::EditorStepTime {
                        is_start: false,
                        delta_minutes: 15,
                    }),
            ]
            .spacing(5)
            .align_y(Alignment::Center);

            r = r.push(time_group);
        }

        r.wrap().vertical_spacing(6)
    };

    let mut schedule_col = column![all_day_row, start_row, end_row].spacing(8);

    if let Some((target, picker_month)) = draft.active_date_picker {
        let selected_for_picker = match target {
            EditorDateTarget::Start => draft.start_date,
            EditorDateTarget::End => draft.end_date,
            EditorDateTarget::RecurrenceUntil => match draft.recurrence.end {
                RecurrenceEnd::Until(d) => d,
                _ => draft.end_date,
            },
        };
        let picker_card = container(view_date_chooser(
            theme,
            picker_month,
            selected_for_picker,
            today,
            week_start_monday,
            false,
            false,
            None,
            Message::EditorDatePickerShiftMonth,
            None,
            Message::EditorDatePickerSelect,
        ))
        .max_width(290.0)
        .style(style::flat_card(theme, 10.0));

        schedule_col = schedule_col.push(picker_card);
    }

    form = form.push(
        container(schedule_col)
            .width(Length::Fill)
            .padding(12)
            .style(style::flat_card(theme, 10.0)),
    );

    // 3. Recurrence section (hidden when editing only a single occurrence override).
    let show_recurrence = !(is_editing && draft.is_recurring_source && draft.edit_scope == EditScope::Single);
    if show_recurrence {
        let mut freq_row = row![].spacing(5).align_y(Alignment::Center);
        for freq in RecurrenceFreq::ALL {
            let active = draft.recurrence.freq == freq;
            let label = match freq {
                RecurrenceFreq::None => "Never",
                other => other.label(),
            };
            freq_row = freq_row.push(
                button(text(label).size(12.5).font(if active {
                    style::bold_font()
                } else {
                    style::regular_font()
                }))
                .padding([6, 10])
                .style(style::toggle_chip(theme, active))
                .on_press(Message::EditorRecurrenceFreqChanged(freq)),
            );
        }

        let mut rec_col = column![
            row![
                render_icon("repeat", 13.5, theme.fg_muted),
                text("Repeat")
                    .size(12)
                    .font(style::bold_font())
                    .color(theme.fg_muted),
            ]
            .spacing(6)
            .align_y(Alignment::Center),
            freq_row.wrap().vertical_spacing(5),
        ]
        .spacing(8);

        if draft.recurrence.freq != RecurrenceFreq::None {
            let unit = match draft.recurrence.freq {
                RecurrenceFreq::Daily => "day(s)",
                RecurrenceFreq::Weekly => "week(s)",
                RecurrenceFreq::Monthly => "month(s)",
                RecurrenceFreq::Yearly => "year(s)",
                RecurrenceFreq::None => "",
            };
            rec_col = rec_col.push(
                row![
                    text("Every").size(12.5).color(theme.fg_muted),
                    text_input("1", &draft.interval_text)
                        .on_input(Message::EditorRecurrenceIntervalChanged)
                        .padding([5, 8])
                        .size(12.5)
                        .font(style::mono_font())
                        .width(Length::Fixed(54.0))
                        .style(style::text_input_style(theme)),
                    text(unit).size(12.5).color(theme.fg_muted),
                ]
                .spacing(8)
                .align_y(Alignment::Center),
            );

            if draft.recurrence.freq == RecurrenceFreq::Weekly {
                let labels = ["M", "T", "W", "T", "F", "S", "S"];
                let mut days_row = row![text("On").size(12.5).color(theme.fg_muted)]
                    .spacing(5)
                    .align_y(Alignment::Center);
                for (idx, lbl) in labels.iter().enumerate() {
                    let active = draft.recurrence.by_weekday[idx];
                    let weekend = idx >= 5;
                    let day_text = if !active && weekend {
                        text(*lbl)
                            .size(12)
                            .font(style::bold_font())
                            .color(style::weekend_color(theme))
                    } else {
                        text(*lbl).size(12).font(style::bold_font())
                    };
                    days_row = days_row.push(
                        button(
                            container(day_text)
                                .width(Length::Fixed(26.0))
                                .align_x(Alignment::Center),
                        )
                        .padding([5, 2])
                        .style(style::toggle_chip(theme, active))
                        .on_press(Message::EditorRecurrenceToggleWeekday(idx)),
                    );
                }
                rec_col = rec_col.push(days_row.wrap().vertical_spacing(5));
            }

            let end_mode: u8 = match draft.recurrence.end {
                RecurrenceEnd::Never => 0,
                RecurrenceEnd::Until(_) => 1,
                RecurrenceEnd::Count(_) => 2,
            };

            let mut end_row = row![
                text("Ends").size(12.5).color(theme.fg_muted),
                button(text("Never").size(12.5).font(if end_mode == 0 {
                    style::bold_font()
                } else {
                    style::regular_font()
                }))
                .padding([5, 10])
                .style(style::toggle_chip(theme, end_mode == 0))
                .on_press(Message::EditorRecurrenceEndModeChanged(0)),
                button(text("On date").size(12.5).font(if end_mode == 1 {
                    style::bold_font()
                } else {
                    style::regular_font()
                }))
                .padding([5, 10])
                .style(style::toggle_chip(theme, end_mode == 1))
                .on_press(Message::EditorRecurrenceEndModeChanged(1)),
                button(text("After").size(12.5).font(if end_mode == 2 {
                    style::bold_font()
                } else {
                    style::regular_font()
                }))
                .padding([5, 10])
                .style(style::toggle_chip(theme, end_mode == 2))
                .on_press(Message::EditorRecurrenceEndModeChanged(2)),
            ]
            .spacing(5)
            .align_y(Alignment::Center);

            if end_mode == 1 {
                let until_picker_btn = button(render_icon("calendar", 13.0, theme.fg_muted))
                    .padding([5, 6])
                    .style(style::icon_button(
                        theme,
                        draft
                            .active_date_picker
                            .is_some_and(|(t, _)| t == EditorDateTarget::RecurrenceUntil),
                    ))
                    .on_press(Message::EditorToggleDatePicker(
                        EditorDateTarget::RecurrenceUntil,
                    ));

                let until_group = row![
                    text_input("YYYY-MM-DD", &draft.until_date_text)
                        .on_input(Message::EditorRecurrenceUntilChanged)
                        .padding([5, 8])
                        .size(12)
                        .font(style::mono_font())
                        .width(Length::Fixed(104.0))
                        .style(style::text_input_style(theme)),
                    style::tooltip(
                        theme,
                        until_picker_btn,
                        "Pick End Date",
                        tooltip::Position::Top,
                    ),
                ]
                .spacing(5)
                .align_y(Alignment::Center);

                end_row = end_row.push(until_group);
            } else if end_mode == 2 {
                let count_group = row![
                    text_input("10", &draft.count_text)
                        .on_input(Message::EditorRecurrenceCountChanged)
                        .padding([5, 8])
                        .size(12)
                        .font(style::mono_font())
                        .width(Length::Fixed(56.0))
                        .style(style::text_input_style(theme)),
                    text("times").size(12).color(theme.fg_muted),
                ]
                .spacing(5)
                .align_y(Alignment::Center);

                end_row = end_row.push(count_group);
            }

            rec_col = rec_col.push(end_row.wrap().vertical_spacing(6));
        }

        form = form.push(
            container(rec_col)
                .width(Length::Fill)
                .padding(12)
                .style(style::flat_card(theme, 10.0)),
        );
    }

    // 4. Reminder section.
    let none_active = draft.reminder_minutes.is_none();
    let mut rem_chips = row![
        button(text("None").size(12.5).font(if none_active {
            style::bold_font()
        } else {
            style::regular_font()
        }))
        .padding([6, 10])
        .style(style::toggle_chip(theme, none_active))
        .on_press(Message::EditorReminderChanged(None)),
    ]
    .spacing(5)
    .align_y(Alignment::Center);

    for &(mins, _) in REMINDER_PRESETS {
        let active = draft.reminder_minutes == Some(mins);
        let short = match mins {
            0 => "At start",
            5 => "5m",
            15 => "15m",
            30 => "30m",
            60 => "1h",
            1440 => "1d",
            _ => "Custom",
        };
        rem_chips = rem_chips.push(
            button(text(short).size(12.5).font(if active {
                style::bold_font()
            } else {
                style::regular_font()
            }))
            .padding([6, 10])
            .style(style::toggle_chip(theme, active))
            .on_press(Message::EditorReminderChanged(Some(mins))),
        );
    }

    form = form.push(
        container(
            column![
                row![
                    render_icon("bell", 13.5, theme.fg_muted),
                    text("Reminder")
                        .size(12)
                        .font(style::bold_font())
                        .color(theme.fg_muted),
                ]
                .spacing(6)
                .align_y(Alignment::Center),
                rem_chips.wrap().vertical_spacing(5),
            ]
            .spacing(8),
        )
        .width(Length::Fill)
        .padding(12)
        .style(style::flat_card(theme, 10.0)),
    );

    // 5. Location & Notes section.
    let loc_input = text_input("Add location…", &draft.location)
        .on_input(Message::EditorLocationChanged)
        .padding([8, 10])
        .size(13)
        .style(style::text_input_style(theme));

    let notes_editor = text_editor(&draft.notes)
        .placeholder("Add description or notes…")
        .on_action(Message::EditorNotesAction)
        .height(Length::Fixed(96.0))
        .padding(9)
        .style(style::text_editor_style(theme));

    form = form.push(
        column![
            row![
                render_icon("location", 13.5, theme.fg_muted),
                text("Location")
                    .size(12)
                    .font(style::bold_font())
                    .color(theme.fg_muted),
            ]
            .spacing(6)
            .align_y(Alignment::Center),
            loc_input,
            row![
                render_icon("notes", 13.5, theme.fg_muted),
                text("Notes")
                    .size(12)
                    .font(style::bold_font())
                    .color(theme.fg_muted),
            ]
            .spacing(6)
            .align_y(Alignment::Center),
            notes_editor,
        ]
        .spacing(7),
    );

    // 6. Delete section (only when editing an existing event).
    // Non-recurring events defer-delete immediately with a 5-second Undo toast;
    // recurring events ask only for Single vs. Series scope first.
    if is_editing {
        if draft.confirm_delete && draft.is_recurring_source {
            let scope_picker = column![
                text("Delete recurring event?")
                    .size(13)
                    .font(style::bold_font())
                    .color(theme.danger_fg),
                row![
                    button(text("Only This Occurrence").size(12).font(style::bold_font()))
                        .padding([7, 10])
                        .style(style::danger_button(theme))
                        .on_press(Message::EditorConfirmDelete(EditScope::Single)),
                    button(text("All in Series").size(12).font(style::bold_font()))
                        .padding([7, 10])
                        .style(style::danger_button(theme))
                        .on_press(Message::EditorConfirmDelete(EditScope::All)),
                    button(text("Cancel").size(12))
                        .padding([7, 10])
                        .style(style::secondary_button(theme))
                        .on_press(Message::EditorCancelDelete),
                ]
                .spacing(6)
                .align_y(Alignment::Center)
                .wrap()
                .vertical_spacing(6),
            ]
            .spacing(8);

            form = form.push(
                container(scope_picker)
                    .width(Length::Fill)
                    .padding(10)
                    .style(style::error_banner(theme)),
            );
        } else {
            let delete_msg = if draft.is_recurring_source {
                Message::EditorRequestDelete
            } else {
                Message::EditorConfirmDelete(EditScope::All)
            };
            form = form.push(
                button(
                    row![
                        render_icon("trash", 14.0, theme.danger_fg),
                        text("Delete Event").size(13).font(style::bold_font()),
                    ]
                    .spacing(7)
                    .align_y(Alignment::Center),
                )
                .width(Length::Fill)
                .padding([9, 12])
                .style(style::danger_button(theme))
                .on_press(delete_msg),
            );
        }
    }

    let modal_body = column![
        container(header).padding(Padding::from([12, 16])),
        header_sep,
        container(
            scrollable(form)
                .height(Length::Shrink)
                .style(style::clean_scrollable(theme)),
        )
        .max_height(560.0),
    ];

    wrap_modal_backdrop(theme, modal_body.into(), 580.0)
}

fn view_calendar_manager<'a>(
    theme: AppTheme,
    state: &'a CalendarManagerState,
    calendars: &'a [CalendarMeta],
    prefs: &'a UserPrefs,
) -> Element<'a> {
    let close_mgr_btn = button(render_icon("close", 15.0, theme.fg_muted))
        .padding([6, 7])
        .style(style::icon_button(theme, false))
        .on_press(Message::CloseOverlay);

    let header = row![
        text("Manage Calendars")
            .size(16)
            .font(style::bold_font())
            .color(theme.fg),
        Space::new().width(Length::Fill),
        button(
            row![
                render_icon("plus", 14.0, contrasting_fg(theme.accent)),
                text("New Calendar").size(13).font(style::bold_font()),
            ]
            .spacing(5)
            .align_y(Alignment::Center),
        )
        .padding([7, 12])
        .style(style::primary_button(theme))
        .on_press(Message::CalendarManagerStartCreate),
        style::tooltip(
            theme,
            close_mgr_btn,
            "Close (Esc)",
            tooltip::Position::Bottom,
        ),
    ]
    .spacing(8)
    .align_y(Alignment::Center);

    let sep = container(Space::new().width(Length::Fill).height(1))
        .width(Length::Fill)
        .height(Length::Fixed(1.0))
        .style(style::hairline_rule(theme));

    let mut content = column![].spacing(14).padding(Padding {
        top: 14.0,
        right: 18.0,
        bottom: 18.0,
        left: 18.0,
    });

    if let Some(err) = &state.error {
        content = content.push(
            container(text(err).size(12.5).color(theme.danger_fg))
                .width(Length::Fill)
                .padding([8, 12])
                .style(style::error_banner(theme)),
        );
    }

    // Inline create/edit form when active.
    if let Some(form) = &state.form {
        let title = if form.editing_id.is_some() {
            "Edit Calendar"
        } else {
            "New Local Calendar"
        };

        let mut token_row = row![].spacing(7).align_y(Alignment::Center);
        for token in SemanticToken::ALL {
            let color = theme.resolve_token(token);
            let selected = form.color == CalendarColor::Token(token);
            let swatch_btn = button(
                container(if selected {
                    render_icon("check", 12.0, contrasting_fg(color))
                } else {
                    Space::new().width(12).height(12).into()
                })
                .width(Length::Fixed(24.0))
                .height(Length::Fixed(24.0))
                .align_x(Alignment::Center)
                .align_y(Alignment::Center),
            )
            .padding(0)
            .style(style::swatch_button(theme, color, selected))
            .on_press(Message::CalendarManagerSelectToken(token));

            token_row = token_row.push(style::tooltip(
                theme,
                swatch_btn,
                format!("{} · Dynamic Matugen palette", token.label()),
                tooltip::Position::Top,
            ));
        }

        let hex_group = row![
            text("Custom Hex:").size(12).color(theme.fg_muted),
            text_input("#afc6ff", &form.custom_hex_text)
                .on_input(Message::CalendarManagerHexChanged)
                .padding([5, 8])
                .size(12)
                .font(style::mono_font())
                .width(Length::Fixed(96.0))
                .style(style::text_input_style(theme)),
        ]
        .spacing(8)
        .align_y(Alignment::Center);

        let form_box = container(
            column![
                text(title)
                    .size(13)
                    .font(style::bold_font())
                    .color(theme.fg),
                text_input("Calendar name…", &form.name)
                    .on_input(Message::CalendarManagerNameChanged)
                    .on_submit(Message::CalendarManagerSaveForm)
                    .padding([8, 10])
                    .size(13)
                    .style(style::text_input_style(theme)),
                row![
                    text("Matugen Token:").size(12).color(theme.fg_muted),
                    token_row,
                ]
                .spacing(10)
                .align_y(Alignment::Center)
                .wrap()
                .vertical_spacing(6),
                row![
                    hex_group,
                    button(
                        row![
                            render_icon(
                                "check",
                                13.0,
                                if form.make_default {
                                    contrasting_fg(theme.accent)
                                } else {
                                    theme.fg_muted
                                },
                            ),
                            text("Set as Default").size(12.5).font(if form.make_default {
                                style::bold_font()
                            } else {
                                style::regular_font()
                            }),
                        ]
                        .spacing(5)
                        .align_y(Alignment::Center),
                    )
                    .padding([6, 11])
                    .style(style::toggle_chip(theme, form.make_default))
                    .on_press(Message::CalendarManagerToggleDefault),
                ]
                .spacing(10)
                .align_y(Alignment::Center)
                .wrap()
                .vertical_spacing(6),
                row![
                    Space::new().width(Length::Fill),
                    button(text("Cancel").size(12.5))
                        .padding([6, 12])
                        .style(style::secondary_button(theme))
                        .on_press(Message::CalendarManagerCancelForm),
                    button(text("Save Calendar").size(12.5).font(style::bold_font()))
                        .padding([6, 14])
                        .style(style::primary_button(theme))
                        .on_press(Message::CalendarManagerSaveForm),
                ]
                .spacing(8),
            ]
            .spacing(10),
        )
        .width(Length::Fill)
        .padding(12)
        .style(style::flat_card(theme, 10.0));

        content = content.push(form_box);
    }

    // Existing calendars list.
    let mut cal_list = column![].spacing(6);
    for cal in calendars {
        let swatch = cal.resolved_color(&theme);
        let color_caption = match cal.color {
            CalendarColor::Token(tok) => format!("Dynamic · {}", tok.label()),
            CalendarColor::Custom(_) => cal.color.to_hex(&theme),
        };

        let is_deleting = state.confirm_delete_id.as_deref() == Some(cal.id.as_str());

        let mut row_controls = row![
            container(Space::new().width(14.0).height(14.0))
                .style(style::color_dot(swatch, 999.0)),
            column![
                row![
                    text(&cal.name)
                        .size(13.5)
                        .font(style::bold_font())
                        .color(theme.fg),
                    if cal.is_default {
                        text("Default")
                            .size(12)
                            .font(style::bold_font())
                            .color(theme.accent)
                    } else {
                        text("").size(12)
                    },
                ]
                .spacing(8)
                .align_y(Alignment::Center),
                text(color_caption).size(12).color(theme.fg_muted),
            ]
            .spacing(1)
            .width(Length::Fill),
        ]
        .spacing(10)
        .align_y(Alignment::Center);

        if is_deleting {
            row_controls = row_controls
                .push(
                    button(text("Cancel").size(11.5))
                        .padding([5, 9])
                        .style(style::secondary_button(theme))
                        .on_press(Message::CalendarManagerCancelDelete),
                )
                .push(
                    button(text("Confirm Delete").size(11.5).font(style::bold_font()))
                        .padding([5, 9])
                        .style(style::danger_button(theme))
                        .on_press(Message::CalendarManagerConfirmDelete(cal.id.clone())),
                );
        } else {
            let vis_btn = button(render_icon(
                if cal.visible { "eye" } else { "eye_off" },
                14.0,
                if cal.visible {
                    theme.fg
                } else {
                    theme.muted_day_fg()
                },
            ))
            .padding([5, 6])
            .style(style::icon_button(theme, false))
            .on_press(Message::ToggleCalendarVisible(cal.id.clone()));

            let exp_btn = button(render_icon("export", 14.0, theme.fg_muted))
                .padding([5, 6])
                .style(style::icon_button(theme, false))
                .on_press(Message::CalendarManagerExport(cal.id.clone()));

            let edit_btn = button(render_icon("edit", 14.0, theme.fg))
                .padding([5, 6])
                .style(style::icon_button(theme, false))
                .on_press(Message::CalendarManagerStartEdit(cal.id.clone()));

            row_controls = row_controls
                .push(style::tooltip(
                    theme,
                    vis_btn,
                    if cal.visible {
                        "Hide Calendar"
                    } else {
                        "Show Calendar"
                    },
                    tooltip::Position::Top,
                ))
                .push(style::tooltip(
                    theme,
                    exp_btn,
                    "Export .ics to Downloads",
                    tooltip::Position::Top,
                ))
                .push(style::tooltip(
                    theme,
                    edit_btn,
                    "Edit Calendar",
                    tooltip::Position::Top,
                ));

            if calendars.len() > 1 {
                let del_btn = button(render_icon("trash", 14.0, theme.danger))
                    .padding([5, 6])
                    .style(style::icon_button(theme, false))
                    .on_press(Message::CalendarManagerRequestDelete(cal.id.clone()));

                row_controls = row_controls.push(style::tooltip(
                    theme,
                    del_btn,
                    "Delete Calendar",
                    tooltip::Position::Top,
                ));
            }
        }

        cal_list = cal_list.push(
            container(row_controls)
                .width(Length::Fill)
                .padding([8, 12])
                .style(style::flat_card(theme, 9.0)),
        );
    }

    content = content.push(cal_list);

    // .ics File Import section.
    let mut target_chips = row![].spacing(6).align_y(Alignment::Center);
    for cal in calendars {
        let selected = cal.id == state.import_target_calendar_id;
        target_chips = target_chips.push(
            button(
                row![
                    container(Space::new().width(8.5).height(8.5))
                        .style(style::color_dot(cal.resolved_color(&theme), 999.0)),
                    text(&cal.name).size(12.5).font(if selected {
                        style::bold_font()
                    } else {
                        style::regular_font()
                    }),
                ]
                .spacing(6)
                .align_y(Alignment::Center),
            )
            .padding([5, 10])
            .style(style::subtle_row_button(theme, selected))
            .on_press(Message::CalendarManagerImportTargetChanged(cal.id.clone())),
        );
    }

    let import_box = container(
        column![
            row![
                render_icon("import", 14.0, theme.fg_muted),
                text("Import .ics File (or drag & drop anywhere onto window)")
                    .size(12.5)
                    .font(style::bold_font())
                    .color(theme.fg_muted),
            ]
            .spacing(6)
            .align_y(Alignment::Center),
            row![
                text_input("~/Downloads/events.ics", &state.import_path_text)
                    .on_input(Message::CalendarManagerImportPathChanged)
                    .on_submit(Message::CalendarManagerImportSubmit)
                    .padding([7, 10])
                    .size(12.5)
                    .style(style::text_input_style(theme)),
                button(text("Import").size(12.5).font(style::bold_font()))
                    .padding([7, 14])
                    .style(style::secondary_button(theme))
                    .on_press(Message::CalendarManagerImportSubmit),
            ]
            .spacing(8)
            .align_y(Alignment::Center),
            row![
                text("Target:").size(12.5).color(theme.fg_muted),
                scrollable(target_chips)
                    .direction(scrollable::Direction::Horizontal(
                        scrollable::Scrollbar::new().width(4).scroller_width(4),
                    ))
                    .style(style::clean_scrollable(theme)),
            ]
            .spacing(8)
            .align_y(Alignment::Center),
        ]
        .spacing(8),
    )
    .width(Length::Fill)
    .padding(12)
    .style(style::flat_card(theme, 10.0));

    content = content.push(import_box);

    // Compact Display Preferences strip (wraps cleanly at narrow window widths).
    let prefs_box = container(
        row![
            text("Display")
                .size(13)
                .font(style::bold_font())
                .color(theme.fg),
            button(
                text(if prefs.use_24h_time {
                    "24-Hour Clock"
                } else {
                    "12-Hour Clock"
                })
                .size(12.5)
                .font(if prefs.use_24h_time {
                    style::bold_font()
                } else {
                    style::regular_font()
                }),
            )
            .padding([6, 11])
            .style(style::toggle_chip(theme, prefs.use_24h_time))
            .on_press(Message::TogglePref24h),
            button(
                text(if prefs.week_start_monday {
                    "Week: Mon"
                } else {
                    "Week: Sun"
                })
                .size(12.5)
                .font(if prefs.week_start_monday {
                    style::bold_font()
                } else {
                    style::regular_font()
                }),
            )
            .padding([6, 11])
            .style(style::toggle_chip(theme, prefs.week_start_monday))
            .on_press(Message::TogglePrefWeekStartMonday),
            button(
                text("Week #")
                    .size(12.5)
                    .font(if prefs.show_week_numbers {
                        style::bold_font()
                    } else {
                        style::regular_font()
                    }),
            )
            .padding([6, 11])
            .style(style::toggle_chip(theme, prefs.show_week_numbers))
            .on_press(Message::TogglePrefShowWeekNumbers),
        ]
        .spacing(8)
        .align_y(Alignment::Center)
        .wrap()
        .vertical_spacing(6),
    )
    .width(Length::Fill)
    .padding([10, 12])
    .style(style::flat_card(theme, 10.0));

    content = content.push(prefs_box);

    let modal_body = column![
        container(header).padding(Padding::from([12, 16])),
        sep,
        container(
            scrollable(content)
                .height(Length::Shrink)
                .style(style::clean_scrollable(theme)),
        )
        .max_height(540.0),
    ];

    wrap_modal_backdrop(theme, modal_body.into(), 540.0)
}

#[allow(clippy::too_many_arguments)]
pub fn view_active_overlay<'a>(
    theme: AppTheme,
    overlay: &'a Overlay,
    calendars: &'a [CalendarMeta],
    prefs: &'a UserPrefs,
    day_overflow_items: &'a [EventOccurrence],
    today: NaiveDate,
    now: DateTime<Local>,
) -> Element<'a> {
    match overlay {
        Overlay::QuickAdd(draft) => {
            view_quick_add(theme, draft, calendars, today, prefs.use_24h_time)
        }
        Overlay::EventPreview {
            occurrence,
            confirm_delete,
        } => view_event_preview(
            theme,
            occurrence,
            *confirm_delete,
            calendars,
            today,
            prefs.use_24h_time,
        ),
        Overlay::DayOverflow { date } => view_day_overflow(
            theme,
            *date,
            day_overflow_items,
            calendars,
            now,
            prefs.use_24h_time,
        ),
        Overlay::EventEditor(draft) => view_event_editor(
            theme,
            draft,
            calendars,
            today,
            prefs.week_start_monday,
            prefs.use_24h_time,
        ),
        Overlay::CalendarManager(state) => view_calendar_manager(theme, state, calendars, prefs),
    }
}

/// Live search results dropdown anchored neatly below the top-right search bar.
pub fn view_search_dropdown<'a>(
    theme: AppTheme,
    query: &'a str,
    results: &'a [EventOccurrence],
    calendars: &'a [CalendarMeta],
    today: NaiveDate,
    use_24h: bool,
) -> Element<'a> {
    let mut rows = column![].spacing(4).padding(8);

    if results.is_empty() {
        rows = rows.push(
            container(
                text(format!("No events matching \"{}\"", query.trim()))
                    .size(12.5)
                    .font(style::italic_font())
                    .color(theme.muted_day_fg()),
            )
            .padding([12, 10]),
        );
    } else {
        for occ in results.iter().take(24) {
            let color = calendar_meta(calendars, &occ.calendar_id)
                .map(|c| c.resolved_color(&theme))
                .unwrap_or(theme.accent);
            let item = row![
                container(Space::new().width(8.0).height(8.0))
                    .style(style::color_dot(color, 999.0)),
                column![
                    text(&occ.summary)
                        .size(13)
                        .font(style::bold_font())
                        .color(theme.fg)
                        .wrapping(iced_core::text::Wrapping::None),
                    text(format!(
                        "{} · {}",
                        format_relative_date_short(occ.start_date, today),
                        occ.format_time_range(use_24h)
                    ))
                    .size(11)
                    .color(theme.fg_muted),
                ]
                .spacing(1)
                .width(Length::Fill),
            ]
            .spacing(9)
            .align_y(Alignment::Center);

            rows = rows.push(
                button(item)
                    .width(Length::Fill)
                    .padding([7, 10])
                    .style(style::subtle_row_button(theme, false))
                    .on_press(Message::SelectSearchResult(occ.clone())),
            );
        }
    }

    let card = opaque(
        container(
            scrollable(rows)
                .height(Length::Shrink)
                .style(style::clean_scrollable(theme)),
        )
        .width(Length::Fixed(340.0))
        .max_height(360.0)
        .style(style::card_surface(theme, 12.0)),
    );

    let dismiss_backdrop = mouse_area(
        container(Space::new().width(Length::Fill).height(Length::Fill))
            .width(Length::Fill)
            .height(Length::Fill),
    )
    .on_press(Message::ClearSearch);

    let card_layer = container(card)
        .width(Length::Fill)
        .height(Length::Fill)
        .padding(Padding {
            top: 4.0,
            right: 16.0,
            bottom: 16.0,
            left: 16.0,
        })
        .align_x(Alignment::End)
        .align_y(Alignment::Start);

    let below_header = Stack::new()
        .push(dismiss_backdrop)
        .push(card_layer)
        .width(Length::Fill)
        .height(Length::Fill);

    column![
        Space::new()
            .width(Length::Fill)
            .height(Length::Fixed(HEADER_HEIGHT)),
        below_header,
    ]
    .width(Length::Fill)
    .height(Length::Fill)
    .into()
}

/// Full-window drag-and-drop overlay displayed while hovering `.ics` file(s).
pub fn view_drop_overlay<'a>(theme: AppTheme) -> Element<'a> {
    opaque(
        container(
            container(
                column![
                    render_icon("import", 38.0, theme.accent),
                    text("Drop .ics Calendar Files to Import")
                        .size(18)
                        .font(style::bold_font())
                        .color(theme.fg),
                    text("Events will be added to your default calendar.")
                        .size(13)
                        .color(theme.fg_muted),
                ]
                .spacing(10)
                .align_x(Alignment::Center),
            )
            .width(Length::Fill)
            .height(Length::Fill)
            .align_x(Alignment::Center)
            .align_y(Alignment::Center)
            .style(style::drop_overlay(theme)),
        )
        .width(Length::Fill)
        .height(Length::Fill)
        .padding(18),
    )
    .into()
}

/// Bottom-center non-blocking toast banner for feedback and due in-app reminders.
pub fn view_toast<'a>(theme: AppTheme, toast: &'a ToastNotice) -> Element<'a> {
    let icon_name = match toast.kind {
        super::ToastKind::Info => "bell",
        super::ToastKind::Success => "check",
        super::ToastKind::Error => "warning",
    };
    let icon_color = match toast.kind {
        super::ToastKind::Info => theme.accent,
        super::ToastKind::Success => theme.profiles[0],
        super::ToastKind::Error => theme.danger,
    };

    let mut content = row![
        render_icon(icon_name, 15.0, icon_color),
        text(&toast.message).size(13).color(theme.fg),
    ]
    .spacing(9)
    .align_y(Alignment::Center);

    if let Some(action) = &toast.action {
        match action {
            ToastAction::UndoDelete => {
                content = content.push(
                    button(text("Undo").size(12).font(style::bold_font()))
                        .padding([4, 11])
                        .style(style::toast_action_button(theme))
                        .on_press(Message::UndoDelete),
                );
            }
            ToastAction::JumpTo { date, occurrence } => {
                content = content.push(
                    button(text("View").size(12).font(style::bold_font()))
                        .padding([4, 10])
                        .style(style::toast_action_button(theme))
                        .on_press(Message::ToastJump(*date, occurrence.clone())),
                );
            }
        }
    }

    let dismiss_btn = button(render_icon("close", 13.0, theme.fg_muted))
        .padding([4, 5])
        .style(style::icon_button(theme, false))
        .on_press(Message::DismissToast(toast.id));

    content = content.push(style::tooltip(
        theme,
        dismiss_btn,
        "Dismiss",
        tooltip::Position::Top,
    ));

    let card = opaque(
        container(content)
            .padding([9, 14])
            .style(style::toast_card(theme, toast.kind)),
    );

    container(card)
        .width(Length::Fill)
        .height(Length::Fill)
        .padding(Padding {
            top: 0.0,
            right: 16.0,
            bottom: 18.0,
            left: 16.0,
        })
        .align_x(Alignment::Center)
        .align_y(Alignment::End)
        .into()
}
