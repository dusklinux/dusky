//! Root Iced 0.14 application state machine for Dusky Calendar.
//!
//! Coordinates view projections, keyboard/mouse shortcuts, Wayland file drops,
//! single-instance IPC commands, stat-gated Matugen live reloads, and in-app reminders.

use crate::layout::{
    AgendaLayout, MonthLayout, WeekLayout, build_agenda_layout, build_month_layout,
    build_week_layout, month_days_with_events, month_grid_bounds, shift_months, week_start_for,
};
use crate::model::{
    CalendarColor, EditScope, Event, EventKey, EventOccurrence, EventTime, RecurrenceEnd,
    RecurrenceFreq, RecurrenceRule, expand_events_in_range, format_clock_time,
    format_reminder_minutes, is_safe_http_url, local_timezone_name, resolve_local_datetime,
};
use crate::storage::CalendarStore;
use crate::theme::{AppTheme, SemanticToken, ThemeStamp};
use crate::ui::agenda_view::view_agenda;
use crate::ui::grid_widgets::MONTH_MAX_PACK_LANES;
use crate::ui::header::{search_input_id, view_header};
use crate::ui::month_view::view_month;
use crate::ui::overlays::{
    editor_summary_input_id, quick_add_input_id, view_active_overlay, view_drop_overlay,
    view_search_dropdown, view_toast,
};
use crate::ui::sidebar::view_sidebar;
use crate::ui::style;
use crate::ui::week_view::{view_week, week_scroll_id};
use crate::ui::{
    CalendarFormDraft, CalendarManagerState, EditorDateTarget, Element, EventEditorDraft, Message,
    Overlay, PendingDeletion, QuickAddDraft, ToastAction, ToastKind, ToastNotice, ViewMode,
    ViewportTier,
};
use crate::{IpcCommand, LaunchOptions};
use chrono::{DateTime, Datelike, Local, NaiveDate, NaiveTime, TimeDelta, Timelike, Utc};
use iced::keyboard::{self, Key, key::Named};
use iced::{Size, Subscription, Task};
use iced_core::Length;
use iced_widget::{Space, Stack, column, container, mouse_area, opaque, row, scrollable, text_editor};
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

const AGENDA_DAYS_AHEAD: i64 = 60;
const MONTH_WHEEL_DEBOUNCE: Duration = Duration::from_millis(120);
const UNDO_DELETE_TIMEOUT_SECS: u64 = 5;
const WINDOW_SIZE_SAVE_DEBOUNCE: Duration = Duration::from_millis(800);
const MIN_PERSIST_WIDTH: f32 = 360.0;
const MAX_PERSIST_WIDTH: f32 = 7680.0;
const MIN_PERSIST_HEIGHT: f32 = 600.0;
const MAX_PERSIST_HEIGHT: f32 = 4320.0;

pub struct CalendarApp {
    pub theme: AppTheme,
    theme_stamp: Option<ThemeStamp>,
    store: CalendarStore,

    window_size: Size,
    pending_window_size_save: Option<Instant>,
    tier: ViewportTier,
    view_mode: ViewMode,
    sidebar_open: bool,
    week_all_day_expanded: bool,
    week_scrolled_once: bool,

    active_date: NaiveDate,
    mini_calendar_month: NaiveDate,
    today: NaiveDate,
    now: DateTime<Local>,

    month_layout: MonthLayout,
    week_layout: WeekLayout,
    agenda_layout: AgendaLayout,
    mini_event_dots: [bool; 42],

    search_open: bool,
    search_query: String,
    search_results: Vec<EventOccurrence>,

    overlay: Option<Overlay>,
    day_overflow_cache: Vec<EventOccurrence>,
    drop_hovering: bool,

    pending_deletion: Option<PendingDeletion>,
    toast: Option<ToastNotice>,
    next_toast_id: u64,
    last_month_wheel: Option<Instant>,
    last_minute_bucket: i64,
    fired_reminders: HashSet<(String, i64, u32)>,
}

fn first_of_month(date: NaiveDate) -> NaiveDate {
    NaiveDate::from_ymd_opt(date.year(), date.month(), 1).unwrap_or(date)
}

fn parse_hm(raw: &str) -> Option<NaiveTime> {
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

fn format_editor_time(time: NaiveTime, use_24h: bool) -> String {
    format_clock_time(time, use_24h)
}

fn parse_ymd(raw: &str) -> Option<NaiveDate> {
    NaiveDate::parse_from_str(raw.trim(), "%Y-%m-%d").ok()
}

fn expand_user_path(raw: &str) -> PathBuf {
    let trimmed = raw.trim();
    if let Some(rest) = trimmed.strip_prefix("~/") {
        if let Some(home) = std::env::var_os("HOME").filter(|h| !h.is_empty()) {
            return PathBuf::from(home).join(rest);
        }
    }
    PathBuf::from(trimmed)
}

fn send_desktop_notification(summary: &str, body: &str) {
    let mut cmd = Command::new("notify-send");
    crate::gpu::inherit_session_environment(&mut cmd);
    let _ = cmd
        .args(["-a", "Dusky Calendar", "-i", "x-office-calendar", summary, body])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn();
}

fn open_external_url(url: &str) -> Result<(), String> {
    let trimmed = url.trim();
    if !is_safe_http_url(trimmed) {
        return Err("Refused to open unsupported or malformed URL.".to_string());
    }
    let mut cmd = Command::new("xdg-open");
    crate::gpu::inherit_session_environment(&mut cmd);
    cmd.arg(trimmed)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map(|_| ())
        .map_err(|err| format!("Could not launch URL via xdg-open: {err}"))
}

fn tick_stream() -> impl iced_futures::futures::Stream<Item = Message> {
    iced_futures::futures::stream::unfold((), |()| async {
        futures_timer::Delay::new(Duration::from_secs(2)).await;
        Some((Message::Tick, ()))
    })
}

fn ipc_stream() -> impl iced_futures::futures::Stream<Item = Message> {
    use iced_futures::futures::StreamExt;
    iced_futures::futures::stream::unfold(crate::take_ipc_receiver(), |maybe_rx| async move {
        match maybe_rx {
            Some(mut rx) => match rx.next().await {
                Some(cmd) => Some((Message::IpcReceived(cmd), Some(rx))),
                None => iced_futures::futures::future::pending().await,
            },
            None => iced_futures::futures::future::pending().await,
        }
    })
}

impl CalendarApp {
    pub fn boot(options: LaunchOptions) -> (Self, Task<Message>) {
        let (theme, theme_stamp) = AppTheme::load_with_stamp();
        let store = CalendarStore::load(options.data_dir.as_deref());
        let now = Local::now();
        let today = now.date_naive();
        let active_date = options.initial_date.unwrap_or(today);
        let mini_calendar_month = first_of_month(active_date);

        let (init_w, init_h) = options.resolved_window_size(store.prefs.valid_window_size());
        let window_size = Size::new(init_w, init_h);
        let tier = ViewportTier::from_width(window_size.width);
        let view_mode = options
            .initial_view
            .map(ViewMode::from)
            .unwrap_or_else(|| ViewMode::from(store.prefs.default_view));
        let sidebar_open = tier == ViewportTier::Wide;

        let dummy_month = build_month_layout(
            active_date.year(),
            active_date.month(),
            today,
            store.prefs.week_start_monday,
            MONTH_MAX_PACK_LANES,
            &[],
        );
        let dummy_week = build_week_layout(
            week_start_for(active_date, store.prefs.week_start_monday),
            today,
            &[],
        );
        let dummy_agenda = build_agenda_layout(active_date, AGENDA_DAYS_AHEAD, today, "", &[]);

        let mut app = Self {
            theme,
            theme_stamp,
            store,
            window_size,
            pending_window_size_save: None,
            tier,
            view_mode,
            sidebar_open,
            week_all_day_expanded: false,
            week_scrolled_once: false,
            active_date,
            mini_calendar_month,
            today,
            now,
            month_layout: dummy_month,
            week_layout: dummy_week,
            agenda_layout: dummy_agenda,
            mini_event_dots: [false; 42],
            search_open: false,
            search_query: String::new(),
            search_results: Vec::new(),
            overlay: None,
            day_overflow_cache: Vec::new(),
            drop_hovering: false,
            pending_deletion: None,
            toast: None,
            next_toast_id: 1,
            last_month_wheel: None,
            last_minute_bucket: now.timestamp() / 60,
            fired_reminders: HashSet::new(),
        };

        for path in options.import_files {
            app.import_ics_path(&path, None);
        }

        app.rebuild_projections();
        app.check_due_reminders();

        let initial_task = if app.effective_view() == ViewMode::Week {
            app.week_scrolled_once = true;
            app.scroll_week_to_initial_hour()
        } else {
            Task::none()
        };

        (app, initial_task)
    }

    fn effective_view(&self) -> ViewMode {
        if self.tier == ViewportTier::Narrow {
            ViewMode::Agenda
        } else {
            self.view_mode
        }
    }

    fn period_title(&self) -> String {
        match self.effective_view() {
            ViewMode::Month => self.active_date.format("%B %Y").to_string(),
            ViewMode::Week => {
                let s = self.week_layout.week_start;
                let e = self.week_layout.week_end;
                if s.year() != e.year() {
                    format!("{} – {}", s.format("%b %-d, %Y"), e.format("%b %-d, %Y"))
                } else if s.month() != e.month() {
                    format!("{} – {}, {}", s.format("%b %-d"), e.format("%b %-d"), e.year())
                } else {
                    format!("{} {} – {}, {}", s.format("%B"), s.day(), e.day(), e.year())
                }
            }
            ViewMode::Agenda => self.active_date.format("%B %Y").to_string(),
        }
    }

    fn today_enabled(&self) -> bool {
        self.effective_view() == ViewMode::Week
            || self.active_date != self.today
            || self.mini_calendar_month != first_of_month(self.today)
    }

    fn alloc_toast_id(&mut self) -> u64 {
        let id = self.next_toast_id;
        self.next_toast_id = self.next_toast_id.wrapping_add(1);
        id
    }

    /// Returns visible events with any active deferred deletion filtered out in memory.
    fn visible_events_filtered(&self) -> Vec<Event> {
        let mut events = self.store.visible_events();
        let Some(pending) = &self.pending_deletion else {
            return events;
        };

        match pending.scope {
            EditScope::All => {
                events.retain(|ev| {
                    !(ev.calendar_id == pending.calendar_id && ev.uid == pending.key.uid)
                });
            }
            EditScope::Single => {
                let target_occ = pending.key.recurrence_id.or(pending.occurrence_id);
                if let Some(occ) = target_occ {
                    events.retain(|ev| {
                        !(ev.calendar_id == pending.calendar_id
                            && ev.uid == pending.key.uid
                            && ev.recurrence_id == Some(occ))
                    });
                    let drop_non_recurring = events
                        .iter_mut()
                        .find(|ev| {
                            ev.calendar_id == pending.calendar_id
                                && ev.uid == pending.key.uid
                                && ev.recurrence_id.is_none()
                        })
                        .map(|master| {
                            if master.is_recurring() {
                                master.add_exdate(occ);
                                false
                            } else {
                                true
                            }
                        })
                        .unwrap_or(false);
                    if drop_non_recurring {
                        events.retain(|ev| {
                            !(ev.calendar_id == pending.calendar_id && ev.uid == pending.key.uid)
                        });
                    }
                } else {
                    events.retain(|ev| {
                        !(ev.calendar_id == pending.calendar_id && ev.uid == pending.key.uid)
                    });
                }
            }
        }

        events
    }

    /// Commits any active pending deletion permanently to the store.
    fn commit_pending_deletion(&mut self) -> bool {
        let Some(pending) = self.pending_deletion.take() else {
            return false;
        };
        if self
            .toast
            .as_ref()
            .is_some_and(|t| t.id == pending.toast_id)
        {
            self.toast = None;
        }
        self.store.delete_event(
            &pending.calendar_id,
            &pending.key,
            pending.scope,
            pending.occurrence_id,
        )
    }

    fn schedule_pending_deletion(
        &mut self,
        calendar_id: String,
        key: EventKey,
        scope: EditScope,
        occurrence_id: Option<DateTime<Utc>>,
        summary: String,
    ) -> Task<Message> {
        let had_previous = self.commit_pending_deletion();
        let toast_id = self.alloc_toast_id();
        let trimmed = summary.trim();
        let message = if had_previous {
            "Another event deleted".to_string()
        } else if trimmed.is_empty() {
            "Event deleted".to_string()
        } else {
            format!("Deleted \"{trimmed}\"")
        };

        self.pending_deletion = Some(PendingDeletion {
            toast_id,
            calendar_id,
            key,
            scope,
            occurrence_id,
            summary,
        });
        self.toast = Some(ToastNotice {
            id: toast_id,
            kind: ToastKind::Info,
            message,
            expires_at: Instant::now() + Duration::from_secs(UNDO_DELETE_TIMEOUT_SECS),
            action: Some(ToastAction::UndoDelete),
        });
        self.rebuild_projections();

        Task::perform(
            async move {
                futures_timer::Delay::new(Duration::from_secs(UNDO_DELETE_TIMEOUT_SECS)).await;
            },
            move |()| Message::CommitPendingDelete(toast_id),
        )
    }

    fn undo_pending_deletion(&mut self) {
        let Some(pending) = self.pending_deletion.take() else {
            return;
        };
        if self
            .toast
            .as_ref()
            .is_some_and(|t| t.id == pending.toast_id)
        {
            self.toast = None;
        }
        self.rebuild_projections();
        let trimmed = pending.summary.trim();
        let msg = if trimmed.is_empty() {
            "Event restored".to_string()
        } else {
            format!("Restored \"{trimmed}\"")
        };
        self.push_toast(ToastKind::Success, msg, 3, None);
    }

    fn persist_window_size_if_dirty(&mut self) {
        if self.pending_window_size_save.take().is_none() {
            return;
        }
        let w = self.window_size.width.round();
        let h = self.window_size.height.round();
        if (MIN_PERSIST_WIDTH..=MAX_PERSIST_WIDTH).contains(&w)
            && (MIN_PERSIST_HEIGHT..=MAX_PERSIST_HEIGHT).contains(&h)
        {
            self.store.prefs.window_width = Some(w);
            self.store.prefs.window_height = Some(h);
            self.store.save_prefs();
        }
    }

    fn push_toast(
        &mut self,
        kind: ToastKind,
        message: impl Into<String>,
        duration_secs: u64,
        action: Option<ToastAction>,
    ) {
        // Never leave a pending deletion dangling without an active Undo affordance.
        if self.pending_deletion.is_some() {
            self.commit_pending_deletion();
        }
        let id = self.alloc_toast_id();
        self.toast = Some(ToastNotice {
            id,
            kind,
            message: message.into(),
            expires_at: Instant::now() + Duration::from_secs(duration_secs),
            action,
        });
    }

    fn rebuild_projections(&mut self) {
        let visible = self.visible_events_filtered();
        let wsm = self.store.prefs.week_start_monday;

        let (m_start, m_end) =
            month_grid_bounds(self.active_date.year(), self.active_date.month(), wsm);
        let (mini_start, mini_end) = month_grid_bounds(
            self.mini_calendar_month.year(),
            self.mini_calendar_month.month(),
            wsm,
        );
        let w_start = week_start_for(self.active_date, wsm);
        let w_end = w_start + TimeDelta::days(6);
        let a_start = self.active_date;
        let a_end = self.active_date + TimeDelta::days(AGENDA_DAYS_AHEAD);

        let min_date = m_start.min(mini_start).min(w_start).min(a_start);
        let max_date = m_end.max(mini_end).max(w_end).max(a_end) + TimeDelta::days(1);

        let range_start =
            resolve_local_datetime(min_date.and_hms_opt(0, 0, 0).expect("midnight"));
        let range_end =
            resolve_local_datetime(max_date.and_hms_opt(0, 0, 0).expect("midnight"));

        let occurrences = expand_events_in_range(&visible, range_start, range_end);

        self.month_layout = build_month_layout(
            self.active_date.year(),
            self.active_date.month(),
            self.today,
            wsm,
            MONTH_MAX_PACK_LANES,
            &occurrences,
        );
        self.week_layout = build_week_layout(w_start, self.today, &occurrences);
        if self.week_layout.total_all_day_lanes <= 3 {
            self.week_all_day_expanded = false;
        }
        self.agenda_layout = build_agenda_layout(
            self.active_date,
            AGENDA_DAYS_AHEAD,
            self.today,
            &self.search_query,
            &occurrences,
        );
        self.mini_event_dots = month_days_with_events(&occurrences, mini_start);

        // Keep DayOverflow cache synchronized if open.
        if let Some(Overlay::DayOverflow { date }) = &self.overlay {
            self.day_overflow_cache = self
                .month_layout
                .day_cell(*date)
                .map(|c| c.all_occurrences.clone())
                .unwrap_or_default();
        }

        if self.search_open && !self.search_query.trim().is_empty() {
            self.rebuild_search_results();
        }
    }

    fn rebuild_search_results(&mut self) {
        let needle = self.search_query.trim().to_lowercase();
        if needle.is_empty() {
            self.search_results.clear();
            return;
        }

        let matching_events: Vec<Event> = self
            .visible_events_filtered()
            .into_iter()
            .filter(|ev| ev.matches_query(&needle))
            .collect();

        if matching_events.is_empty() {
            self.search_results.clear();
            return;
        }

        // Search across a practical 2-year window centered around the active date.
        let start_d = self.active_date - TimeDelta::days(365);
        let end_d = self.active_date + TimeDelta::days(366);
        let range_start =
            resolve_local_datetime(start_d.and_hms_opt(0, 0, 0).expect("midnight"));
        let range_end =
            resolve_local_datetime(end_d.and_hms_opt(0, 0, 0).expect("midnight"));

        let mut occs = expand_events_in_range(&matching_events, range_start, range_end);
        let anchor = self.today;
        occs.sort_by_key(|o| {
            let is_past = o.end_date < anchor;
            let dist = (o.start_date - anchor).num_days().abs();
            (is_past, dist, o.start_local)
        });
        occs.truncate(30);
        self.search_results = occs;
    }

    fn scroll_week_to_initial_hour(&self) -> Task<Message> {
        let target_hour = (self.now.hour().saturating_sub(1)).clamp(6, 18) as f32;
        let y = (target_hour * self.store.prefs.week_hour_height).max(0.0);
        iced::widget::operation::scroll_to(
            week_scroll_id(),
            scrollable::AbsoluteOffset {
                x: Some(0.0),
                y: Some(y),
            },
        )
    }

    fn import_ics_path(&mut self, path: &Path, target_cal: Option<&str>) {
        self.commit_pending_deletion();
        let cal_id = target_cal
            .map(str::to_string)
            .unwrap_or_else(|| self.store.default_calendar_id());
        match self.store.import_ics_file(path, &cal_id) {
            Ok(summary) => {
                self.rebuild_projections();
                let mut msg = format!("Imported {} event(s)", summary.imported);
                if summary.updated > 0 {
                    msg.push_str(&format!(", updated {}", summary.updated));
                }
                if summary.skipped > 0 {
                    msg.push_str(&format!(" ({} skipped)", summary.skipped));
                }
                self.push_toast(ToastKind::Success, msg, 5, None);
            }
            Err(err) => {
                self.push_toast(ToastKind::Error, err, 6, None);
            }
        }
    }

    fn check_due_reminders(&mut self) {
        let now = self.now;
        let visible = self.visible_events_filtered();
        let has_reminders = visible.iter().any(|ev| !ev.reminders.is_empty());
        if !has_reminders {
            return;
        }

        let window_start = now - TimeDelta::minutes(2);
        let window_end = now + TimeDelta::hours(25);
        let occs = expand_events_in_range(&visible, window_start, window_end);

        for occ in occs {
            if occ.reminders.is_empty() {
                continue;
            }
            for &mins in &occ.reminders {
                let trigger_at = occ.start_local - TimeDelta::minutes(i64::from(mins));
                let delta = now.signed_duration_since(trigger_at);
                if delta >= TimeDelta::zero() && delta <= TimeDelta::minutes(2) {
                    let key = (
                        occ.event_key.uid.clone(),
                        occ.occurrence_id.timestamp(),
                        mins,
                    );
                    if self.fired_reminders.insert(key) {
                        let subtitle = if mins == 0 {
                            format!("Starting now · {}", occ.format_time_range(self.store.prefs.use_24h_time))
                        } else {
                            format!(
                                "{} · {}",
                                format_reminder_minutes(mins),
                                occ.format_time_range(self.store.prefs.use_24h_time)
                            )
                        };
                        send_desktop_notification(&occ.summary, &subtitle);
                        self.push_toast(
                            ToastKind::Info,
                            format!("Reminder: {} ({subtitle})", occ.summary),
                            10,
                            Some(ToastAction::JumpTo {
                                date: occ.start_date,
                                occurrence: Some(occ.clone()),
                            }),
                        );
                    }
                }
            }
        }
    }

    fn open_quick_add_range(
        &mut self,
        start_date: NaiveDate,
        end_date: NaiveDate,
        time_range: Option<(u32, u32)>,
    ) -> Task<Message> {
        let (start_date, end_date) = if start_date <= end_date {
            (start_date, end_date)
        } else {
            (end_date, start_date)
        };
        let use_24h = self.store.prefs.use_24h_time;
        let (all_day, start_time, end_time) = match time_range {
            Some((start_mins, end_mins)) => {
                let s = start_mins.min(1438);
                let e = end_mins.max(s + 1).min(1439);
                let st = NaiveTime::from_hms_opt(s / 60, s % 60, 0).expect("valid start time");
                let et = NaiveTime::from_hms_opt(e / 60, e % 60, 0).expect("valid end time");
                (
                    false,
                    format_editor_time(st, use_24h),
                    format_editor_time(et, use_24h),
                )
            }
            None => (
                true,
                format_editor_time(NaiveTime::from_hms_opt(9, 0, 0).expect("time"), use_24h),
                format_editor_time(NaiveTime::from_hms_opt(10, 0, 0).expect("time"), use_24h),
            ),
        };

        self.overlay = Some(Overlay::QuickAdd(QuickAddDraft {
            title: String::new(),
            calendar_id: self.store.default_calendar_id(),
            start_date,
            end_date,
            all_day,
            start_time,
            end_time,
            error: None,
        }));
        iced::widget::operation::focus(quick_add_input_id())
    }

    fn open_quick_add(&mut self, date: NaiveDate, hour: Option<u32>) -> Task<Message> {
        let time_range = hour.map(|h| {
            let start_h = h.min(23);
            let start_mins = start_h * 60;
            let end_mins = if start_h == 23 {
                1439
            } else {
                (start_h + 1) * 60
            };
            (start_mins, end_mins)
        });
        self.open_quick_add_range(date, date, time_range)
    }

    fn open_new_editor(&mut self, date: NaiveDate, hour: Option<u32>) -> Task<Message> {
        let all_day = hour.is_none();
        let start_h = hour.unwrap_or(9).min(23);
        let end_h = (start_h + 1).min(23);
        let end_m = if start_h == 23 { 59 } else { 0 };
        let use_24h = self.store.prefs.use_24h_time;
        let start_t = NaiveTime::from_hms_opt(start_h, 0, 0).expect("valid start time");
        let end_t = NaiveTime::from_hms_opt(end_h, end_m, 0).expect("valid end time");

        let draft = EventEditorDraft {
            original_calendar_id: None,
            original_key: None,
            occurrence_id: None,
            clicked_occurrence: None,
            is_recurring_source: false,
            edit_scope: EditScope::All,
            summary: String::new(),
            calendar_id: self.store.default_calendar_id(),
            all_day,
            start_date: date,
            start_date_text: date.format("%Y-%m-%d").to_string(),
            end_date: date,
            end_date_text: date.format("%Y-%m-%d").to_string(),
            start_time_text: format_editor_time(start_t, use_24h),
            end_time_text: format_editor_time(end_t, use_24h),
            recurrence: RecurrenceRule::NONE,
            interval_text: "1".to_string(),
            until_date_text: (date + TimeDelta::days(30)).format("%Y-%m-%d").to_string(),
            count_text: "10".to_string(),
            reminder_minutes: Some(15),
            location: String::new(),
            notes: text_editor::Content::new(),
            active_date_picker: None,
            error: None,
            confirm_delete: false,
        };

        self.overlay = Some(Overlay::EventEditor(Box::new(draft)));
        iced::widget::operation::focus(editor_summary_input_id())
    }

    fn open_editor_for_occurrence(&mut self, occ: &EventOccurrence) -> Task<Message> {
        if self
            .pending_deletion
            .as_ref()
            .is_some_and(|p| p.calendar_id == occ.calendar_id && p.key.uid == occ.event_key.uid)
        {
            self.commit_pending_deletion();
        }
        let stored = self
            .store
            .find_event(&occ.calendar_id, &occ.event_key)
            .cloned();
        let master = if occ.event_key.recurrence_id.is_some() {
            self.store
                .find_event(&occ.calendar_id, &EventKey::master(occ.event_key.uid.clone()))
                .cloned()
        } else {
            stored.clone()
        };

        let is_recurring_source = occ.is_recurring_instance
            || master.as_ref().is_some_and(Event::is_recurring);
        let rec = RecurrenceRule::from_rrule_string(
            master.as_ref().and_then(|e| e.rrule.as_deref()),
        );
        let until_text = match rec.end {
            RecurrenceEnd::Until(d) => d.format("%Y-%m-%d").to_string(),
            _ => (occ.start_date + TimeDelta::days(30))
                .format("%Y-%m-%d")
                .to_string(),
        };
        let count_text = match rec.end {
            RecurrenceEnd::Count(c) => c.to_string(),
            _ => "10".to_string(),
        };

        let edit_scope = if occ.event_key.recurrence_id.is_some() {
            EditScope::Single
        } else {
            EditScope::All
        };

        let use_24h = self.store.prefs.use_24h_time;
        let (all_day, start_date, end_date, start_time_text, end_time_text) =
            if edit_scope == EditScope::All {
                if let Some(m) = master.as_ref() {
                    let (s_date, e_date) = m.time.inclusive_day_span_local();
                    (
                        m.time.is_all_day(),
                        s_date,
                        e_date,
                        format_editor_time(m.time.start_local().time(), use_24h),
                        format_editor_time(m.time.end_local().time(), use_24h),
                    )
                } else {
                    (
                        occ.is_all_day,
                        occ.start_date,
                        occ.end_date,
                        format_editor_time(occ.start_local.time(), use_24h),
                        format_editor_time(occ.end_local.time(), use_24h),
                    )
                }
            } else {
                (
                    occ.is_all_day,
                    occ.start_date,
                    occ.end_date,
                    format_editor_time(occ.start_local.time(), use_24h),
                    format_editor_time(occ.end_local.time(), use_24h),
                )
            };

        let draft = EventEditorDraft {
            original_calendar_id: Some(occ.calendar_id.clone()),
            original_key: Some(occ.event_key.clone()),
            occurrence_id: Some(occ.occurrence_id),
            clicked_occurrence: Some(occ.clone()),
            is_recurring_source,
            edit_scope,
            summary: occ.summary.clone(),
            calendar_id: occ.calendar_id.clone(),
            all_day,
            start_date,
            start_date_text: start_date.format("%Y-%m-%d").to_string(),
            end_date,
            end_date_text: end_date.format("%Y-%m-%d").to_string(),
            start_time_text,
            end_time_text,
            recurrence: rec,
            interval_text: rec.interval.max(1).to_string(),
            until_date_text: until_text,
            count_text,
            reminder_minutes: occ.reminders.first().copied(),
            location: occ.location.clone().unwrap_or_default(),
            notes: text_editor::Content::with_text(occ.description.as_deref().unwrap_or("")),
            active_date_picker: None,
            error: None,
            confirm_delete: false,
        };

        self.overlay = Some(Overlay::EventEditor(Box::new(draft)));
        iced::widget::operation::focus(editor_summary_input_id())
    }

    fn save_quick_add(&mut self) {
        let Some(Overlay::QuickAdd(draft)) = self.overlay.as_mut() else {
            return;
        };

        let title = draft.title.trim().to_string();
        if title.is_empty() {
            draft.error = Some("Please enter an event title.".to_string());
            return;
        }

        let end_date = draft.end_date.max(draft.start_date);
        let event = if draft.all_day {
            let end_excl = end_date.succ_opt().unwrap_or(end_date);
            Event::new_all_day(draft.calendar_id.clone(), title, draft.start_date, end_excl)
        } else {
            let time_hint = if self.store.prefs.use_24h_time {
                "HH:MM (e.g. 09:30)"
            } else {
                "H:MM AM/PM (e.g. 9:30 AM)"
            };
            let Some(start_t) = parse_hm(&draft.start_time) else {
                draft.error = Some(format!("Start time must be {time_hint}."));
                return;
            };
            let Some(end_t) = parse_hm(&draft.end_time) else {
                draft.error = Some(format!("End time must be {time_hint}."));
                return;
            };
            let start_dt = draft.start_date.and_time(start_t);
            let mut end_dt = end_date.and_time(end_t);
            if end_dt <= start_dt {
                end_dt = start_dt + TimeDelta::hours(1);
            }
            Event::new_timed_local(draft.calendar_id.clone(), title, start_dt, end_dt)
        };

        self.store.upsert_event(None, None, event);
        self.overlay = None;
        self.rebuild_projections();
        self.push_toast(ToastKind::Success, "Event saved", 3, None);
    }

    fn save_editor(&mut self) {
        let Some(Overlay::EventEditor(draft)) = self.overlay.as_mut() else {
            return;
        };

        let summary = draft.summary.trim().to_string();
        if summary.is_empty() {
            draft.error = Some("Event title cannot be empty.".to_string());
            return;
        }

        let Some(start_date) = parse_ymd(&draft.start_date_text) else {
            draft.error = Some("Start date must be YYYY-MM-DD.".to_string());
            return;
        };
        let Some(end_date) = parse_ymd(&draft.end_date_text) else {
            draft.error = Some("End date must be YYYY-MM-DD.".to_string());
            return;
        };

        if end_date < start_date {
            draft.error = Some("End date cannot be before start date.".to_string());
            return;
        }

        let event_time = if draft.all_day {
            EventTime::AllDay {
                start: start_date,
                end: end_date.succ_opt().unwrap_or(end_date),
            }
        } else {
            let time_hint = if self.store.prefs.use_24h_time {
                "HH:MM (e.g. 09:30)"
            } else {
                "H:MM AM/PM (e.g. 9:30 AM)"
            };
            let Some(start_t) = parse_hm(&draft.start_time_text) else {
                draft.error = Some(format!("Start time must be {time_hint}."));
                return;
            };
            let Some(end_t) = parse_hm(&draft.end_time_text) else {
                draft.error = Some(format!("End time must be {time_hint}."));
                return;
            };
            let start_naive = start_date.and_time(start_t);
            let end_naive = end_date.and_time(end_t);
            if end_naive < start_naive {
                draft.error = Some("End time cannot be before start time.".to_string());
                return;
            }
            if let Some(tz_name) = local_timezone_name() {
                let start_utc = resolve_local_datetime(start_naive).with_timezone(&Utc);
                let end_utc = resolve_local_datetime(end_naive).with_timezone(&Utc);
                EventTime::Zoned {
                    start: start_utc,
                    end: end_utc.max(start_utc),
                    tz: tz_name.to_string(),
                }
            } else {
                EventTime::Floating {
                    start: start_naive,
                    end: end_naive,
                }
            }
        };

        // Sync recurrence interval / end fields before serializing RRULE.
        let mut recurrence = draft.recurrence;
        if recurrence.freq != RecurrenceFreq::None {
            recurrence.interval = draft
                .interval_text
                .trim()
                .parse::<u16>()
                .ok()
                .filter(|&n| n >= 1)
                .unwrap_or(1);
            match recurrence.end {
                RecurrenceEnd::Never => {}
                RecurrenceEnd::Until(_) => {
                    let Some(u) = parse_ymd(&draft.until_date_text) else {
                        draft.error = Some("Repeat-until date must be YYYY-MM-DD.".to_string());
                        return;
                    };
                    recurrence.end = RecurrenceEnd::Until(u);
                }
                RecurrenceEnd::Count(_) => {
                    let Some(c) = draft
                        .count_text
                        .trim()
                        .parse::<u32>()
                        .ok()
                        .filter(|&n| n >= 1)
                    else {
                        draft.error = Some("Repeat count must be a positive integer.".to_string());
                        return;
                    };
                    recurrence.end = RecurrenceEnd::Count(c);
                }
            }
        }

        let location = {
            let trimmed = draft.location.trim();
            (!trimmed.is_empty()).then(|| trimmed.to_string())
        };
        let description = {
            let raw = draft.notes.text();
            (!raw.trim().is_empty()).then_some(raw)
        };
        let reminders = draft.reminder_minutes.into_iter().collect::<Vec<_>>();

        // Take draft values cleanly before mutating store & clearing overlay.
        let original_cal = draft.original_calendar_id.clone();
        let original_key = draft.original_key.clone();
        let occurrence_id = draft.occurrence_id;
        let is_recurring_source = draft.is_recurring_source;
        let edit_scope = draft.edit_scope;
        let target_calendar_id = draft.calendar_id.clone();

        self.commit_pending_deletion();

        if let (Some(orig_cal), Some(orig_key)) = (original_cal, original_key) {
            if is_recurring_source && edit_scope == EditScope::Single {
                let occ_id = orig_key
                    .recurrence_id
                    .or(occurrence_id)
                    .unwrap_or_else(|| event_time.start_anchor_utc());
                let mut override_ev = self
                    .store
                    .find_event(&orig_cal, &EventKey::with_recurrence_id(orig_key.uid.clone(), Some(occ_id)))
                    .cloned()
                    .unwrap_or_else(|| {
                        let mut base = Event::new_all_day(&orig_cal, &summary, start_date, end_date);
                        base.uid = orig_key.uid.clone();
                        base
                    });
                override_ev.summary = summary;
                override_ev.location = location;
                override_ev.description = description;
                override_ev.time = event_time;
                override_ev.reminders = reminders;
                self.store.upsert_single_occurrence_override(
                    &orig_cal,
                    &orig_key.uid,
                    occ_id,
                    override_ev,
                );
            } else {
                let master_key = EventKey::master(orig_key.uid.clone());
                let mut master = self
                    .store
                    .find_event(&orig_cal, &master_key)
                    .or_else(|| self.store.find_event(&orig_cal, &orig_key))
                    .cloned()
                    .unwrap_or_else(|| {
                        Event::new_all_day(&target_calendar_id, &summary, start_date, end_date)
                    });
                master.calendar_id = target_calendar_id;
                master.summary = summary;
                master.location = location;
                master.description = description;
                master.time = event_time;
                master.rrule = recurrence.to_rrule_string();
                master.reminders = reminders;
                self.store
                    .upsert_event(Some(&orig_cal), Some(&master_key), master);
            }
        } else {
            let mut ev = Event::new_all_day(&target_calendar_id, &summary, start_date, end_date);
            ev.time = event_time;
            ev.location = location;
            ev.description = description;
            ev.rrule = recurrence.to_rrule_string();
            ev.reminders = reminders;
            self.store.upsert_event(None, None, ev);
        }

        self.overlay = None;
        self.rebuild_projections();
        self.push_toast(ToastKind::Success, "Event saved", 3, None);
    }

    fn handle_system_event(&mut self, event: iced::Event) -> Task<Message> {
        match event {
            iced::Event::Window(win_ev) => match win_ev {
                iced::window::Event::Opened { size, .. } | iced::window::Event::Resized(size) => {
                    return self.update(Message::WindowResized(size));
                }
                iced::window::Event::CloseRequested => {
                    return self.update(Message::RequestClose);
                }
                iced::window::Event::FileHovered(path) => {
                    let is_ics = path
                        .extension()
                        .and_then(|e| e.to_str())
                        .is_some_and(|e| e.eq_ignore_ascii_case("ics"));
                    if is_ics || path.extension().is_none() {
                        self.drop_hovering = true;
                    }
                }
                iced::window::Event::FilesHoveredLeft => {
                    self.drop_hovering = false;
                }
                iced::window::Event::FileDropped(path) => {
                    self.drop_hovering = false;
                    self.import_ics_path(&path, None);
                }
                _ => {}
            },
            iced::Event::Mouse(iced::mouse::Event::ButtonPressed(btn)) => {
                if self.overlay.is_none() {
                    match btn {
                        iced::mouse::Button::Back | iced::mouse::Button::Other(8) => {
                            return self.update(Message::NavigatePrev);
                        }
                        iced::mouse::Button::Forward | iced::mouse::Button::Other(9) => {
                            return self.update(Message::NavigateNext);
                        }
                        _ => {}
                    }
                }
            }
            iced::Event::Keyboard(keyboard::Event::KeyPressed { key, modifiers, .. }) => {
                // Escape dismisses active overlay or search bar without quitting the app.
                if matches!(key, Key::Named(Named::Escape)) {
                    if self.overlay.is_some() {
                        return self.update(Message::CloseOverlay);
                    }
                    if self.search_open {
                        return self.update(Message::ClearSearch);
                    }
                    return Task::none();
                }

                // Ctrl+S saves inside the Event Editor.
                if modifiers.control()
                    && !modifiers.alt()
                    && matches!(&key, Key::Character(c) if c.eq_ignore_ascii_case("s"))
                    && matches!(self.overlay, Some(Overlay::EventEditor(_)))
                {
                    return self.update(Message::EditorSave);
                }

                // Ctrl+Q / Ctrl+W always cleanly exit.
                if modifiers.control()
                    && !modifiers.alt()
                    && matches!(&key, Key::Character(c) if c.eq_ignore_ascii_case("q") || c.eq_ignore_ascii_case("w"))
                {
                    return self.update(Message::RequestClose);
                }

                // Do not steal typing shortcuts while a modal form or search input is open.
                let text_captured = self
                    .overlay
                    .as_ref()
                    .is_some_and(Overlay::captures_text_shortcuts)
                    || self.search_open;
                if text_captured {
                    return Task::none();
                }

                // Global GNOME-Calendar-aligned keyboard shortcuts.
                if modifiers.alt() && !modifiers.control() {
                    match &key {
                        Key::Character(c) if c == "1" => {
                            return self.update(Message::SwitchView(ViewMode::Month));
                        }
                        Key::Character(c) if c == "2" => {
                            return self.update(Message::SwitchView(ViewMode::Week));
                        }
                        Key::Character(c) if c == "3" => {
                            return self.update(Message::SwitchView(ViewMode::Agenda));
                        }
                        Key::Named(Named::ArrowLeft) => {
                            return self.update(Message::NavigatePrev);
                        }
                        Key::Named(Named::ArrowRight) => {
                            return self.update(Message::NavigateNext);
                        }
                        Key::Named(Named::ArrowDown) => {
                            return self.update(Message::GoToday);
                        }
                        _ => {}
                    }
                }

                if modifiers.control() && modifiers.alt() {
                    if matches!(&key, Key::Character(c) if c.eq_ignore_ascii_case("m")) {
                        return self.update(Message::OpenCalendarManager);
                    }
                }

                if modifiers.control() && !modifiers.alt() {
                    match &key {
                        Key::Character(c)
                            if c.eq_ignore_ascii_case("z") && self.pending_deletion.is_some() =>
                        {
                            return self.update(Message::UndoDelete);
                        }
                        Key::Character(c) if c.eq_ignore_ascii_case("t") => {
                            return self.update(Message::GoToday);
                        }
                        Key::Character(c) if c.eq_ignore_ascii_case("n") => {
                            return self.update(Message::OpenQuickAdd {
                                date: None,
                                hour: None,
                            });
                        }
                        Key::Character(c) if c.eq_ignore_ascii_case("f") => {
                            return self.update(Message::ToggleSearch);
                        }
                        Key::Character(c) if c == "+" || c == "=" => {
                            return self.update(Message::ZoomWeek(10.0));
                        }
                        Key::Character(c) if c == "-" || c == "_" => {
                            return self.update(Message::ZoomWeek(-10.0));
                        }
                        Key::Named(Named::PageUp) => {
                            let next = match self.view_mode {
                                ViewMode::Month => ViewMode::Agenda,
                                ViewMode::Week => ViewMode::Month,
                                ViewMode::Agenda => ViewMode::Week,
                            };
                            return self.update(Message::SwitchView(next));
                        }
                        Key::Named(Named::PageDown) => {
                            let next = match self.view_mode {
                                ViewMode::Month => ViewMode::Week,
                                ViewMode::Week => ViewMode::Agenda,
                                ViewMode::Agenda => ViewMode::Month,
                            };
                            return self.update(Message::SwitchView(next));
                        }
                        _ => {}
                    }
                }

                if !modifiers.control() && !modifiers.alt() {
                    match key {
                        Key::Named(Named::Home) => return self.update(Message::GoToday),
                        Key::Named(Named::PageUp) => return self.update(Message::NavigatePrev),
                        Key::Named(Named::PageDown) => return self.update(Message::NavigateNext),
                        Key::Named(Named::F8) => {
                            return self.update(Message::OpenCalendarManager);
                        }
                        Key::Named(Named::F9) => return self.update(Message::ToggleSidebar),
                        _ => {}
                    }
                }
            }
            _ => {}
        }

        Task::none()
    }

    pub fn update(&mut self, message: Message) -> Task<Message> {
        match message {
            Message::SwitchView(mode) => {
                self.view_mode = mode;
                self.store.prefs.default_view = mode.into();
                self.store.save_prefs();
                self.rebuild_projections();
                if mode == ViewMode::Week && !self.week_scrolled_once {
                    self.week_scrolled_once = true;
                    return self.scroll_week_to_initial_hour();
                }
            }
            Message::GoToday => {
                self.now = Local::now();
                self.today = self.now.date_naive();
                self.active_date = self.today;
                self.mini_calendar_month = first_of_month(self.today);
                self.rebuild_projections();
                if self.effective_view() == ViewMode::Week {
                    return self.scroll_week_to_initial_hour();
                }
            }
            Message::NavigatePrev => {
                self.active_date = match self.effective_view() {
                    ViewMode::Month => shift_months(self.active_date, -1),
                    ViewMode::Week => self.active_date - TimeDelta::days(7),
                    ViewMode::Agenda => self.active_date - TimeDelta::days(14),
                };
                self.mini_calendar_month = first_of_month(self.active_date);
                self.rebuild_projections();
            }
            Message::NavigateNext => {
                self.active_date = match self.effective_view() {
                    ViewMode::Month => shift_months(self.active_date, 1),
                    ViewMode::Week => self.active_date + TimeDelta::days(7),
                    ViewMode::Agenda => self.active_date + TimeDelta::days(14),
                };
                self.mini_calendar_month = first_of_month(self.active_date);
                self.rebuild_projections();
            }
            Message::SelectDate(date) => {
                self.active_date = date;
                self.mini_calendar_month = first_of_month(date);
                if self.tier == ViewportTier::Medium && self.sidebar_open {
                    self.sidebar_open = false;
                }
                self.rebuild_projections();
            }
            Message::ActivateDate(date) => {
                self.active_date = date;
                self.mini_calendar_month = first_of_month(date);
                self.rebuild_projections();
                return self.open_quick_add(date, None);
            }
            Message::MonthWheelStep(delta) => {
                let now = Instant::now();
                if self
                    .last_month_wheel
                    .is_some_and(|prev| now.saturating_duration_since(prev) < MONTH_WHEEL_DEBOUNCE)
                {
                    return Task::none();
                }
                self.last_month_wheel = Some(now);
                self.active_date = shift_months(self.active_date, delta);
                self.mini_calendar_month = first_of_month(self.active_date);
                self.rebuild_projections();
            }
            Message::MiniCalendarShiftMonth(delta) => {
                self.mini_calendar_month = shift_months(self.mini_calendar_month, delta);
                self.rebuild_projections();
            }
            Message::MiniCalendarShiftYear(delta) => {
                self.mini_calendar_month = shift_months(self.mini_calendar_month, delta * 12);
                self.rebuild_projections();
            }
            Message::ToggleSidebar => {
                if self.tier != ViewportTier::Narrow {
                    self.sidebar_open = !self.sidebar_open;
                }
            }
            Message::ToggleWeekAllDayExpanded => {
                self.week_all_day_expanded = !self.week_all_day_expanded;
            }
            Message::ZoomWeek(delta) => {
                let next = (self.store.prefs.week_hour_height + delta).clamp(40.0, 120.0);
                if (next - self.store.prefs.week_hour_height).abs() > 0.1 {
                    self.store.prefs.week_hour_height = next;
                    self.store.save_prefs();
                }
            }
            Message::QuickAddDateRange {
                start_date,
                end_date,
            } => {
                self.active_date = start_date;
                self.mini_calendar_month = first_of_month(start_date);
                self.rebuild_projections();
                return self.open_quick_add_range(start_date, end_date, None);
            }
            Message::QuickAddTimeRange {
                date,
                start_minutes,
                end_minutes,
            } => {
                self.active_date = date;
                self.mini_calendar_month = first_of_month(date);
                self.rebuild_projections();
                return self.open_quick_add_range(date, date, Some((start_minutes, end_minutes)));
            }
            Message::DragWindow => {
                return iced::window::oldest()
                    .and_then(iced::window::drag::<()>)
                    .discard();
            }
            Message::RequestClose => {
                self.commit_pending_deletion();
                self.persist_window_size_if_dirty();
                self.store.flush_and_shutdown();
                return iced::exit();
            }
            Message::ToggleSearch => {
                self.search_open = !self.search_open;
                if self.search_open {
                    self.rebuild_search_results();
                    return iced::widget::operation::focus(search_input_id());
                } else {
                    self.search_query.clear();
                    self.search_results.clear();
                    self.rebuild_projections();
                }
            }
            Message::SearchChanged(query) => {
                self.search_query = query;
                self.rebuild_search_results();
                self.rebuild_projections();
            }
            Message::SearchSubmit => {
                if let Some(top) = self.search_results.first().cloned() {
                    return self.update(Message::SelectSearchResult(top));
                }
            }
            Message::SelectSearchResult(occ) => {
                self.active_date = occ.start_date;
                self.mini_calendar_month = first_of_month(occ.start_date);
                self.search_open = false;
                self.search_query.clear();
                self.search_results.clear();
                self.rebuild_projections();
                self.overlay = Some(Overlay::EventPreview {
                    occurrence: occ,
                    confirm_delete: false,
                });
            }
            Message::ClearSearch => {
                self.search_open = false;
                self.search_query.clear();
                self.search_results.clear();
                self.rebuild_projections();
            }
            Message::ToggleCalendarVisible(id) => {
                if self.store.toggle_calendar_visibility(&id) {
                    self.rebuild_projections();
                }
            }
            Message::OpenCalendarManager => {
                self.overlay = Some(Overlay::CalendarManager(CalendarManagerState {
                    form: None,
                    import_path_text: String::new(),
                    import_target_calendar_id: self.store.default_calendar_id(),
                    confirm_delete_id: None,
                    error: None,
                }));
            }
            Message::CalendarManagerStartCreate => {
                if let Some(Overlay::CalendarManager(state)) = self.overlay.as_mut() {
                    let next_token = SemanticToken::next_for_index(self.store.calendars.len());
                    state.form = Some(CalendarFormDraft {
                        editing_id: None,
                        name: String::new(),
                        color: CalendarColor::Token(next_token),
                        custom_hex_text: String::new(),
                        make_default: false,
                    });
                    state.error = None;
                }
            }
            Message::CalendarManagerStartEdit(id) => {
                if let Some(cal) = self.store.calendar(&id).cloned() {
                    if let Some(Overlay::CalendarManager(state)) = self.overlay.as_mut() {
                        state.form = Some(CalendarFormDraft {
                            editing_id: Some(cal.id),
                            name: cal.name,
                            color: cal.color,
                            custom_hex_text: cal.color.custom_hex().unwrap_or_default(),
                            make_default: cal.is_default,
                        });
                        state.error = None;
                    }
                }
            }
            Message::CalendarManagerCancelForm => {
                if let Some(Overlay::CalendarManager(state)) = self.overlay.as_mut() {
                    state.form = None;
                    state.error = None;
                }
            }
            Message::CalendarManagerNameChanged(name) => {
                if let Some(Overlay::CalendarManager(state)) = self.overlay.as_mut() {
                    if let Some(form) = state.form.as_mut() {
                        form.name = name;
                    }
                }
            }
            Message::CalendarManagerSelectToken(token) => {
                if let Some(Overlay::CalendarManager(state)) = self.overlay.as_mut() {
                    if let Some(form) = state.form.as_mut() {
                        form.color = CalendarColor::Token(token);
                        form.custom_hex_text.clear();
                    }
                }
            }
            Message::CalendarManagerHexChanged(raw) => {
                if let Some(Overlay::CalendarManager(state)) = self.overlay.as_mut() {
                    if let Some(form) = state.form.as_mut() {
                        form.custom_hex_text = raw.clone();
                        if let Some(custom) = CalendarColor::from_hex(&raw) {
                            form.color = custom;
                        }
                    }
                }
            }
            Message::CalendarManagerToggleDefault => {
                if let Some(Overlay::CalendarManager(state)) = self.overlay.as_mut() {
                    if let Some(form) = state.form.as_mut() {
                        form.make_default = !form.make_default;
                    }
                }
            }
            Message::CalendarManagerSaveForm => {
                if let Some(Overlay::CalendarManager(state)) = self.overlay.as_mut() {
                    let Some(form) = state.form.clone() else {
                        return Task::none();
                    };
                    if form.name.trim().is_empty() {
                        state.error = Some("Calendar name cannot be empty.".to_string());
                        return Task::none();
                    }
                    if !form.custom_hex_text.trim().is_empty()
                        && CalendarColor::from_hex(&form.custom_hex_text).is_none()
                    {
                        state.error =
                            Some("Custom hex must be 6 hex digits (e.g. #8ab4fa).".to_string());
                        return Task::none();
                    }

                    if let Some(edit_id) = form.editing_id {
                        self.store.update_calendar(
                            &edit_id,
                            form.name,
                            form.color,
                            form.make_default,
                        );
                    } else {
                        let created = self.store.create_calendar(form.name, form.color);
                        if form.make_default {
                            self.store.update_calendar(
                                &created.id,
                                created.name.clone(),
                                created.color,
                                true,
                            );
                        }
                    }
                    state.form = None;
                    state.error = None;
                    self.rebuild_projections();
                    self.push_toast(ToastKind::Success, "Calendar saved", 3, None);
                }
            }
            Message::CalendarManagerRequestDelete(id) => {
                if let Some(Overlay::CalendarManager(state)) = self.overlay.as_mut() {
                    state.confirm_delete_id = Some(id);
                    state.error = None;
                }
            }
            Message::CalendarManagerCancelDelete => {
                if let Some(Overlay::CalendarManager(state)) = self.overlay.as_mut() {
                    state.confirm_delete_id = None;
                }
            }
            Message::CalendarManagerConfirmDelete(id) => {
                self.commit_pending_deletion();
                match self.store.delete_calendar(&id) {
                    Ok(name) => {
                        if let Some(Overlay::CalendarManager(state)) = self.overlay.as_mut() {
                            state.confirm_delete_id = None;
                            state.import_target_calendar_id = self.store.default_calendar_id();
                        }
                        self.rebuild_projections();
                        self.push_toast(
                            ToastKind::Info,
                            format!("Deleted calendar \"{name}\""),
                            4,
                            None,
                        );
                    }
                    Err(err) => {
                        if let Some(Overlay::CalendarManager(state)) = self.overlay.as_mut() {
                            state.error = Some(err);
                            state.confirm_delete_id = None;
                        }
                    }
                }
            }
            Message::CalendarManagerExport(id) => {
                self.commit_pending_deletion();
                match self.store.export_calendar_to_downloads(&id) {
                    Ok(dest) => {
                        self.push_toast(
                            ToastKind::Success,
                            format!("Exported to {}", dest.display()),
                            5,
                            None,
                        );
                    }
                    Err(err) => {
                        self.push_toast(ToastKind::Error, err, 5, None);
                    }
                }
            }
            Message::CalendarManagerImportPathChanged(path) => {
                if let Some(Overlay::CalendarManager(state)) = self.overlay.as_mut() {
                    state.import_path_text = path;
                    state.error = None;
                }
            }
            Message::CalendarManagerImportTargetChanged(cal_id) => {
                if let Some(Overlay::CalendarManager(state)) = self.overlay.as_mut() {
                    state.import_target_calendar_id = cal_id;
                }
            }
            Message::CalendarManagerImportSubmit => {
                let Some(Overlay::CalendarManager(state)) = self.overlay.as_mut() else {
                    return Task::none();
                };
                if state.import_path_text.trim().is_empty() {
                    state.error = Some("Enter a path to a local .ics file.".to_string());
                    return Task::none();
                }
                let path = expand_user_path(&state.import_path_text);
                let target = state.import_target_calendar_id.clone();
                state.import_path_text.clear();
                self.import_ics_path(&path, Some(&target));
            }
            Message::TogglePref24h => {
                self.store.prefs.use_24h_time = !self.store.prefs.use_24h_time;
                self.store.save_prefs();
            }
            Message::TogglePrefWeekStartMonday => {
                self.store.prefs.week_start_monday = !self.store.prefs.week_start_monday;
                self.store.save_prefs();
                self.rebuild_projections();
            }
            Message::TogglePrefShowWeekNumbers => {
                self.store.prefs.show_week_numbers = !self.store.prefs.show_week_numbers;
                self.store.save_prefs();
            }
            Message::OpenQuickAdd { date, hour } => {
                let target_date = date.unwrap_or(self.active_date);
                return self.open_quick_add(target_date, hour);
            }
            Message::QuickAddTitleChanged(title) => {
                if let Some(Overlay::QuickAdd(draft)) = self.overlay.as_mut() {
                    draft.title = title;
                    draft.error = None;
                }
            }
            Message::QuickAddToggleAllDay => {
                if let Some(Overlay::QuickAdd(draft)) = self.overlay.as_mut() {
                    draft.all_day = !draft.all_day;
                    draft.error = None;
                }
            }
            Message::QuickAddStartTimeChanged(t) => {
                if let Some(Overlay::QuickAdd(draft)) = self.overlay.as_mut() {
                    draft.start_time = t;
                    draft.error = None;
                }
            }
            Message::QuickAddEndTimeChanged(t) => {
                if let Some(Overlay::QuickAdd(draft)) = self.overlay.as_mut() {
                    draft.end_time = t;
                    draft.error = None;
                }
            }
            Message::QuickAddSelectCalendar(cal_id) => {
                if let Some(Overlay::QuickAdd(draft)) = self.overlay.as_mut() {
                    draft.calendar_id = cal_id;
                }
            }
            Message::QuickAddSave => {
                self.save_quick_add();
            }
            Message::QuickAddSubmit => {
                let empty_title = matches!(
                    &self.overlay,
                    Some(Overlay::QuickAdd(draft)) if draft.title.trim().is_empty()
                );
                if empty_title {
                    return self.update(Message::QuickAddPromoteToEditor);
                }
                self.save_quick_add();
            }
            Message::QuickAddPromoteToEditor => {
                if let Some(Overlay::QuickAdd(q)) = self.overlay.take() {
                    let end_date = q.end_date.max(q.start_date);
                    let draft = EventEditorDraft {
                        original_calendar_id: None,
                        original_key: None,
                        occurrence_id: None,
                        clicked_occurrence: None,
                        is_recurring_source: false,
                        edit_scope: EditScope::All,
                        summary: q.title,
                        calendar_id: q.calendar_id,
                        all_day: q.all_day,
                        start_date: q.start_date,
                        start_date_text: q.start_date.format("%Y-%m-%d").to_string(),
                        end_date,
                        end_date_text: end_date.format("%Y-%m-%d").to_string(),
                        start_time_text: q.start_time,
                        end_time_text: q.end_time,
                        recurrence: RecurrenceRule::NONE,
                        interval_text: "1".to_string(),
                        until_date_text: (q.start_date + TimeDelta::days(30))
                            .format("%Y-%m-%d")
                            .to_string(),
                        count_text: "10".to_string(),
                        reminder_minutes: Some(15),
                        location: String::new(),
                        notes: text_editor::Content::new(),
                        active_date_picker: None,
                        error: None,
                        confirm_delete: false,
                    };
                    self.overlay = Some(Overlay::EventEditor(Box::new(draft)));
                    return iced::widget::operation::focus(editor_summary_input_id());
                }
            }
            Message::OpenDayOverflow(date) => {
                self.day_overflow_cache = self
                    .month_layout
                    .day_cell(date)
                    .map(|c| c.all_occurrences.clone())
                    .unwrap_or_default();
                self.overlay = Some(Overlay::DayOverflow { date });
            }
            Message::OpenEventPreview(occ) => {
                self.overlay = Some(Overlay::EventPreview {
                    occurrence: occ,
                    confirm_delete: false,
                });
            }
            Message::OpenExternalUrl(url) => {
                if let Err(err) = open_external_url(&url) {
                    self.push_toast(ToastKind::Error, err, 5, None);
                }
            }
            Message::PreviewExportEvent => {
                self.commit_pending_deletion();
                if let Some(Overlay::EventPreview { occurrence, .. }) = &self.overlay {
                    match self
                        .store
                        .export_event_to_downloads(&occurrence.calendar_id, &occurrence.event_key)
                    {
                        Ok(dest) => {
                            self.push_toast(
                                ToastKind::Success,
                                format!("Exported event to {}", dest.display()),
                                5,
                                None,
                            );
                        }
                        Err(err) => {
                            self.push_toast(ToastKind::Error, err, 5, None);
                        }
                    }
                }
            }
            Message::PreviewEditEvent => {
                if let Some(Overlay::EventPreview { occurrence, .. }) = self.overlay.take() {
                    return self.open_editor_for_occurrence(&occurrence);
                }
            }
            Message::PreviewRequestDelete => {
                if let Some(Overlay::EventPreview { confirm_delete, .. }) = self.overlay.as_mut() {
                    *confirm_delete = !*confirm_delete;
                }
            }
            Message::PreviewCancelDelete => {
                if let Some(Overlay::EventPreview { confirm_delete, .. }) = self.overlay.as_mut() {
                    *confirm_delete = false;
                }
            }
            Message::PreviewConfirmDelete(scope) => {
                if let Some(Overlay::EventPreview { occurrence, .. }) = self.overlay.take() {
                    return self.schedule_pending_deletion(
                        occurrence.calendar_id,
                        occurrence.event_key,
                        scope,
                        Some(occurrence.occurrence_id),
                        occurrence.summary,
                    );
                }
            }
            Message::OpenNewEventEditor { date, hour } => {
                return self.open_new_editor(date, hour);
            }
            Message::EditorScopeChanged(scope) => {
                let use_24h = self.store.prefs.use_24h_time;
                let master_schedule = if scope == EditScope::All {
                    if let Some(Overlay::EventEditor(draft)) = &self.overlay {
                        if let (Some(orig_cal), Some(orig_key)) =
                            (&draft.original_calendar_id, &draft.original_key)
                        {
                            self.store
                                .find_event(orig_cal, &EventKey::master(orig_key.uid.clone()))
                                .map(|m| {
                                    let (s_date, e_date) = m.time.inclusive_day_span_local();
                                    (
                                        m.time.is_all_day(),
                                        s_date,
                                        e_date,
                                        format_editor_time(m.time.start_local().time(), use_24h),
                                        format_editor_time(m.time.end_local().time(), use_24h),
                                    )
                                })
                        } else {
                            None
                        }
                    } else {
                        None
                    }
                } else {
                    None
                };

                if let Some(Overlay::EventEditor(draft)) = self.overlay.as_mut() {
                    draft.edit_scope = scope;
                    draft.active_date_picker = None;
                    draft.error = None;
                    match scope {
                        EditScope::All => {
                            if let Some((all_day, s_date, e_date, s_time, e_time)) = master_schedule
                            {
                                draft.all_day = all_day;
                                draft.start_date = s_date;
                                draft.start_date_text = s_date.format("%Y-%m-%d").to_string();
                                draft.end_date = e_date;
                                draft.end_date_text = e_date.format("%Y-%m-%d").to_string();
                                draft.start_time_text = s_time;
                                draft.end_time_text = e_time;
                            }
                        }
                        EditScope::Single => {
                            if let Some(orig_cal) = &draft.original_calendar_id {
                                draft.calendar_id = orig_cal.clone();
                            }
                            if let Some(occ) = &draft.clicked_occurrence {
                                draft.all_day = occ.is_all_day;
                                draft.start_date = occ.start_date;
                                draft.start_date_text =
                                    occ.start_date.format("%Y-%m-%d").to_string();
                                draft.end_date = occ.end_date;
                                draft.end_date_text = occ.end_date.format("%Y-%m-%d").to_string();
                                draft.start_time_text =
                                    format_editor_time(occ.start_local.time(), use_24h);
                                draft.end_time_text =
                                    format_editor_time(occ.end_local.time(), use_24h);
                            }
                        }
                    }
                }
            }
            Message::EditorSummaryChanged(s) => {
                if let Some(Overlay::EventEditor(draft)) = self.overlay.as_mut() {
                    draft.summary = s;
                    draft.error = None;
                }
            }
            Message::EditorSelectCalendar(id) => {
                if let Some(Overlay::EventEditor(draft)) = self.overlay.as_mut() {
                    if !(draft.original_key.is_some()
                        && draft.is_recurring_source
                        && draft.edit_scope == EditScope::Single)
                    {
                        draft.calendar_id = id;
                    }
                }
            }
            Message::EditorToggleAllDay => {
                if let Some(Overlay::EventEditor(draft)) = self.overlay.as_mut() {
                    draft.all_day = !draft.all_day;
                    draft.error = None;
                }
            }
            Message::EditorStartDateChanged(s) => {
                if let Some(Overlay::EventEditor(draft)) = self.overlay.as_mut() {
                    draft.start_date_text = s.clone();
                    if let Some(parsed) = parse_ymd(&s) {
                        let span = (draft.end_date - draft.start_date).max(TimeDelta::zero());
                        draft.start_date = parsed;
                        draft.end_date = parsed + span;
                        draft.end_date_text = draft.end_date.format("%Y-%m-%d").to_string();
                    }
                    draft.error = None;
                }
            }
            Message::EditorEndDateChanged(s) => {
                if let Some(Overlay::EventEditor(draft)) = self.overlay.as_mut() {
                    draft.end_date_text = s.clone();
                    if let Some(parsed) = parse_ymd(&s) {
                        draft.end_date = parsed;
                    }
                    draft.error = None;
                }
            }
            Message::EditorStepDate {
                is_start,
                delta_days,
            } => {
                if let Some(Overlay::EventEditor(draft)) = self.overlay.as_mut() {
                    if is_start {
                        let span = (draft.end_date - draft.start_date).max(TimeDelta::zero());
                        draft.start_date += TimeDelta::days(delta_days);
                        draft.start_date_text = draft.start_date.format("%Y-%m-%d").to_string();
                        draft.end_date = draft.start_date + span;
                        draft.end_date_text = draft.end_date.format("%Y-%m-%d").to_string();
                    } else {
                        draft.end_date = (draft.end_date + TimeDelta::days(delta_days)).max(draft.start_date);
                        draft.end_date_text = draft.end_date.format("%Y-%m-%d").to_string();
                    }
                    draft.error = None;
                }
            }
            Message::EditorStartTimeChanged(s) => {
                if let Some(Overlay::EventEditor(draft)) = self.overlay.as_mut() {
                    draft.start_time_text = s;
                    draft.error = None;
                }
            }
            Message::EditorEndTimeChanged(s) => {
                if let Some(Overlay::EventEditor(draft)) = self.overlay.as_mut() {
                    draft.end_time_text = s;
                    draft.error = None;
                }
            }
            Message::EditorStepTime {
                is_start,
                delta_minutes,
            } => {
                let use_24h = self.store.prefs.use_24h_time;
                if let Some(Overlay::EventEditor(draft)) = self.overlay.as_mut() {
                    let cur_start = parse_hm(&draft.start_time_text)
                        .unwrap_or_else(|| NaiveTime::from_hms_opt(9, 0, 0).expect("time"));
                    let cur_end = parse_hm(&draft.end_time_text)
                        .unwrap_or_else(|| NaiveTime::from_hms_opt(10, 0, 0).expect("time"));

                    if is_start {
                        let old_start_dt = draft.start_date.and_time(cur_start);
                        let old_end_dt = draft.end_date.and_time(cur_end);
                        let dur = (old_end_dt - old_start_dt).max(TimeDelta::minutes(15));
                        let new_start_dt = old_start_dt + TimeDelta::minutes(delta_minutes);
                        let new_end_dt = new_start_dt + dur;
                        draft.start_date = new_start_dt.date();
                        draft.start_date_text = draft.start_date.format("%Y-%m-%d").to_string();
                        draft.start_time_text = format_editor_time(new_start_dt.time(), use_24h);
                        draft.end_date = new_end_dt.date();
                        draft.end_date_text = draft.end_date.format("%Y-%m-%d").to_string();
                        draft.end_time_text = format_editor_time(new_end_dt.time(), use_24h);
                    } else {
                        let start_dt = draft.start_date.and_time(cur_start);
                        let new_end_dt = (draft.end_date.and_time(cur_end)
                            + TimeDelta::minutes(delta_minutes))
                        .max(start_dt + TimeDelta::minutes(15));
                        draft.end_date = new_end_dt.date();
                        draft.end_date_text = draft.end_date.format("%Y-%m-%d").to_string();
                        draft.end_time_text = format_editor_time(new_end_dt.time(), use_24h);
                    }
                    draft.error = None;
                }
            }
            Message::EditorToggleDatePicker(target) => {
                if let Some(Overlay::EventEditor(draft)) = self.overlay.as_mut() {
                    if draft.active_date_picker.is_some_and(|(t, _)| t == target) {
                        draft.active_date_picker = None;
                    } else {
                        let anchor = match target {
                            EditorDateTarget::Start => draft.start_date,
                            EditorDateTarget::End => draft.end_date,
                            EditorDateTarget::RecurrenceUntil => parse_ymd(&draft.until_date_text)
                                .unwrap_or(draft.end_date),
                        };
                        draft.active_date_picker = Some((target, first_of_month(anchor)));
                    }
                }
            }
            Message::EditorDatePickerShiftMonth(delta) => {
                if let Some(Overlay::EventEditor(draft)) = self.overlay.as_mut() {
                    if let Some((target, month)) = draft.active_date_picker {
                        draft.active_date_picker = Some((target, shift_months(month, delta)));
                    }
                }
            }
            Message::EditorDatePickerSelect(date) => {
                if let Some(Overlay::EventEditor(draft)) = self.overlay.as_mut() {
                    if let Some((target, _)) = draft.active_date_picker.take() {
                        match target {
                            EditorDateTarget::Start => {
                                let span = (draft.end_date - draft.start_date).max(TimeDelta::zero());
                                draft.start_date = date;
                                draft.start_date_text = date.format("%Y-%m-%d").to_string();
                                draft.end_date = date + span;
                                draft.end_date_text = draft.end_date.format("%Y-%m-%d").to_string();
                            }
                            EditorDateTarget::End => {
                                draft.end_date = date.max(draft.start_date);
                                draft.end_date_text = draft.end_date.format("%Y-%m-%d").to_string();
                            }
                            EditorDateTarget::RecurrenceUntil => {
                                draft.recurrence.end = RecurrenceEnd::Until(date);
                                draft.until_date_text = date.format("%Y-%m-%d").to_string();
                            }
                        }
                    }
                }
            }
            Message::EditorRecurrenceFreqChanged(freq) => {
                if let Some(Overlay::EventEditor(draft)) = self.overlay.as_mut() {
                    if freq == RecurrenceFreq::None {
                        draft.recurrence = RecurrenceRule::NONE;
                    } else {
                        let mut next = RecurrenceRule::default_for_date(freq, draft.start_date);
                        next.interval = draft.recurrence.interval.max(1);
                        next.end = draft.recurrence.end;
                        draft.recurrence = next;
                    }
                }
            }
            Message::EditorRecurrenceIntervalChanged(raw) => {
                if let Some(Overlay::EventEditor(draft)) = self.overlay.as_mut() {
                    draft.interval_text = raw.clone();
                    if let Ok(n) = raw.trim().parse::<u16>() {
                        draft.recurrence.interval = n.max(1);
                    }
                }
            }
            Message::EditorRecurrenceToggleWeekday(idx) => {
                if let Some(Overlay::EventEditor(draft)) = self.overlay.as_mut() {
                    if idx < 7 {
                        draft.recurrence.by_weekday[idx] = !draft.recurrence.by_weekday[idx];
                    }
                }
            }
            Message::EditorRecurrenceEndModeChanged(mode) => {
                if let Some(Overlay::EventEditor(draft)) = self.overlay.as_mut() {
                    draft.recurrence.end = match mode {
                        1 => {
                            let d = parse_ymd(&draft.until_date_text)
                                .unwrap_or(draft.start_date + TimeDelta::days(30));
                            draft.until_date_text = d.format("%Y-%m-%d").to_string();
                            RecurrenceEnd::Until(d)
                        }
                        2 => {
                            let c = draft
                                .count_text
                                .trim()
                                .parse::<u32>()
                                .ok()
                                .filter(|&n| n >= 1)
                                .unwrap_or(10);
                            draft.count_text = c.to_string();
                            RecurrenceEnd::Count(c)
                        }
                        _ => RecurrenceEnd::Never,
                    };
                }
            }
            Message::EditorRecurrenceUntilChanged(raw) => {
                if let Some(Overlay::EventEditor(draft)) = self.overlay.as_mut() {
                    draft.until_date_text = raw.clone();
                    if let Some(d) = parse_ymd(&raw) {
                        draft.recurrence.end = RecurrenceEnd::Until(d);
                    }
                }
            }
            Message::EditorRecurrenceCountChanged(raw) => {
                if let Some(Overlay::EventEditor(draft)) = self.overlay.as_mut() {
                    draft.count_text = raw.clone();
                    if let Ok(c) = raw.trim().parse::<u32>() {
                        if c >= 1 {
                            draft.recurrence.end = RecurrenceEnd::Count(c);
                        }
                    }
                }
            }
            Message::EditorReminderChanged(rem) => {
                if let Some(Overlay::EventEditor(draft)) = self.overlay.as_mut() {
                    draft.reminder_minutes = rem;
                }
            }
            Message::EditorLocationChanged(loc) => {
                if let Some(Overlay::EventEditor(draft)) = self.overlay.as_mut() {
                    draft.location = loc;
                }
            }
            Message::EditorNotesAction(action) => {
                if let Some(Overlay::EventEditor(draft)) = self.overlay.as_mut() {
                    draft.notes.perform(action);
                }
            }
            Message::EditorSave => {
                self.save_editor();
            }
            Message::EditorRequestDelete => {
                if let Some(Overlay::EventEditor(draft)) = self.overlay.as_mut() {
                    draft.confirm_delete = true;
                }
            }
            Message::EditorCancelDelete => {
                if let Some(Overlay::EventEditor(draft)) = self.overlay.as_mut() {
                    draft.confirm_delete = false;
                }
            }
            Message::EditorConfirmDelete(scope) => {
                if let Some(Overlay::EventEditor(draft)) = self.overlay.take() {
                    if let (Some(cal_id), Some(key)) =
                        (draft.original_calendar_id, draft.original_key)
                    {
                        return self.schedule_pending_deletion(
                            cal_id,
                            key,
                            scope,
                            draft.occurrence_id,
                            draft.summary,
                        );
                    }
                }
            }
            Message::CloseOverlay => {
                self.overlay = None;
            }
            Message::UndoDelete => {
                self.undo_pending_deletion();
            }
            Message::CommitPendingDelete(toast_id) => {
                if self
                    .pending_deletion
                    .as_ref()
                    .is_some_and(|p| p.toast_id == toast_id)
                    && self.commit_pending_deletion()
                {
                    self.rebuild_projections();
                }
            }
            Message::DismissToast(id) => {
                let committed = self
                    .pending_deletion
                    .as_ref()
                    .is_some_and(|p| p.toast_id == id)
                    && self.commit_pending_deletion();
                if self.toast.as_ref().is_some_and(|t| t.id == id) {
                    self.toast = None;
                }
                if committed {
                    self.rebuild_projections();
                }
            }
            Message::ToastJump(date, occurrence) => {
                self.toast = None;
                self.active_date = date;
                self.mini_calendar_month = first_of_month(date);
                self.rebuild_projections();
                if let Some(occ) = occurrence {
                    self.overlay = Some(Overlay::EventPreview {
                        occurrence: occ,
                        confirm_delete: false,
                    });
                }
            }
            Message::WindowResized(size) => {
                if !size.width.is_finite()
                    || !size.height.is_finite()
                    || size.width < 100.0
                    || size.height < 100.0
                {
                    return Task::none();
                }
                let prev_tier = self.tier;
                let size_changed = (size.width - self.window_size.width).abs() >= 1.0
                    || (size.height - self.window_size.height).abs() >= 1.0;
                self.window_size = size;
                self.tier = ViewportTier::from_width(size.width);

                if self.tier != prev_tier {
                    match self.tier {
                        ViewportTier::Wide => self.sidebar_open = true,
                        ViewportTier::Medium | ViewportTier::Narrow => self.sidebar_open = false,
                    }
                }

                if size_changed
                    && (MIN_PERSIST_WIDTH..=MAX_PERSIST_WIDTH).contains(&size.width)
                    && (MIN_PERSIST_HEIGHT..=MAX_PERSIST_HEIGHT).contains(&size.height)
                {
                    let differs_from_saved = self
                        .store
                        .prefs
                        .window_width
                        .is_none_or(|w| (w - size.width.round()).abs() >= 1.0)
                        || self
                            .store
                            .prefs
                            .window_height
                            .is_none_or(|h| (h - size.height.round()).abs() >= 1.0);
                    if differs_from_saved {
                        self.pending_window_size_save =
                            Some(Instant::now() + WINDOW_SIZE_SAVE_DEBOUNCE);
                    }
                }
            }
            Message::SystemEvent(ev) => {
                return self.handle_system_event(ev);
            }
            Message::IpcReceived(cmd) => match cmd {
                IpcCommand::Focus => {
                    crate::focus_hyprland_window();
                }
                IpcCommand::Toggle => {
                    return self.update(Message::RequestClose);
                }
                IpcCommand::Import(path) => {
                    self.import_ics_path(&path, None);
                    crate::focus_hyprland_window();
                }
                IpcCommand::Goto { view, date } => {
                    if let Some(v) = view {
                        self.view_mode = v.into();
                    }
                    if let Some(d) = date {
                        self.active_date = d;
                        self.mini_calendar_month = first_of_month(d);
                    }
                    self.rebuild_projections();
                    crate::focus_hyprland_window();
                }
            },
            Message::Tick => {
                // 1. Stat-gated Matugen live reload (single stat() syscall when unchanged).
                if let Some((new_theme, new_stamp)) =
                    AppTheme::load_if_changed(self.theme_stamp.as_ref())
                {
                    self.theme = new_theme;
                    self.theme_stamp = Some(new_stamp);
                }

                // 2. Auto-dismiss expired toasts and commit any expired pending deletion.
                if let Some(expired_id) = self
                    .toast
                    .as_ref()
                    .filter(|t| Instant::now() >= t.expires_at)
                    .map(|t| t.id)
                {
                    let committed = self
                        .pending_deletion
                        .as_ref()
                        .is_some_and(|p| p.toast_id == expired_id)
                        && self.commit_pending_deletion();
                    if self.toast.as_ref().is_some_and(|t| t.id == expired_id) {
                        self.toast = None;
                    }
                    if committed {
                        self.rebuild_projections();
                    }
                }

                // 3. Persist stabilized window dimensions to preferences.
                if self
                    .pending_window_size_save
                    .is_some_and(|due| Instant::now() >= due)
                {
                    self.persist_window_size_if_dirty();
                }

                // 4. Minute-gated clock refresh, midnight rollover, and reminder evaluation.
                let now = Local::now();
                let minute_bucket = now.timestamp() / 60;
                if minute_bucket != self.last_minute_bucket {
                    self.last_minute_bucket = minute_bucket;
                    let new_today = now.date_naive();
                    let day_changed = new_today != self.today;
                    self.now = now;
                    self.today = new_today;
                    if day_changed {
                        self.fired_reminders.clear();
                        self.rebuild_projections();
                    }
                    self.check_due_reminders();
                }
            }
            Message::NoOp => {}
        }

        Task::none()
    }

    pub fn subscription(&self) -> Subscription<Message> {
        let events = iced::event::listen_with(|event, status, _window| match &event {
            iced::Event::Window(
                iced::window::Event::Opened { .. }
                | iced::window::Event::Resized(_)
                | iced::window::Event::CloseRequested
                | iced::window::Event::FileHovered(_)
                | iced::window::Event::FileDropped(_)
                | iced::window::Event::FilesHoveredLeft,
            ) => Some(Message::SystemEvent(event)),
            iced::Event::Mouse(iced::mouse::Event::ButtonPressed(
                iced::mouse::Button::Back
                | iced::mouse::Button::Forward
                | iced::mouse::Button::Other(8 | 9),
            )) => Some(Message::SystemEvent(event)),
            iced::Event::Keyboard(keyboard::Event::KeyPressed { key, modifiers, .. }) => {
                let always_forward = matches!(key, Key::Named(Named::Escape))
                    || (modifiers.control()
                        && matches!(&key, Key::Character(c) if c.eq_ignore_ascii_case("s")
                            || c.eq_ignore_ascii_case("q")
                            || c.eq_ignore_ascii_case("w")));
                if always_forward || status == iced::event::Status::Ignored {
                    Some(Message::SystemEvent(event))
                } else {
                    None
                }
            }
            _ => None,
        });

        Subscription::batch([
            events,
            Subscription::run(tick_stream),
            Subscription::run(ipc_stream),
        ])
    }

    pub fn view(&self) -> Element<'_> {
        let active_view = self.effective_view();

        let header = view_header(
            self.theme,
            self.tier,
            active_view,
            self.period_title(),
            self.today_enabled(),
            self.sidebar_open,
            self.search_open,
            &self.search_query,
        );

        let is_empty_calendar = self.store.total_event_count() == 0;
        let main_view: Element<'_> = match active_view {
            ViewMode::Month => view_month(
                self.theme,
                &self.month_layout,
                self.active_date,
                self.now,
                self.store.prefs.week_start_monday,
                self.store.prefs.show_week_numbers,
                self.store.prefs.use_24h_time,
                is_empty_calendar,
                &self.store.calendars,
            ),
            ViewMode::Week => view_week(
                self.theme,
                &self.week_layout,
                self.active_date,
                self.now,
                self.store.prefs.week_hour_height,
                self.window_size.height,
                self.week_all_day_expanded,
                self.store.prefs.use_24h_time,
                is_empty_calendar,
                &self.store.calendars,
            ),
            ViewMode::Agenda => view_agenda(
                self.theme,
                &self.agenda_layout,
                self.active_date,
                self.mini_calendar_month,
                self.today,
                self.now,
                self.store.prefs.week_start_monday,
                self.store.prefs.show_week_numbers,
                self.store.prefs.use_24h_time,
                self.tier == ViewportTier::Narrow,
                &self.mini_event_dots,
                &self.store.calendars,
                &self.search_query,
            ),
        };

        // Wide tier pins the sidebar beside the main view; Medium tier opens it as a scrim drawer.
        let body: Element<'_> = if self.tier == ViewportTier::Wide && self.sidebar_open {
            let sidebar = view_sidebar(
                self.theme,
                self.mini_calendar_month,
                self.active_date,
                self.today,
                self.store.prefs.week_start_monday,
                self.store.prefs.show_week_numbers,
                &self.mini_event_dots,
                &self.store.calendars,
                false,
            );
            row![sidebar, main_view]
                .width(Length::Fill)
                .height(Length::Fill)
                .into()
        } else if self.tier == ViewportTier::Medium && self.sidebar_open {
            let drawer = view_sidebar(
                self.theme,
                self.mini_calendar_month,
                self.active_date,
                self.today,
                self.store.prefs.week_start_monday,
                self.store.prefs.show_week_numbers,
                &self.mini_event_dots,
                &self.store.calendars,
                true,
            );
            let dismiss_scrim = mouse_area(
                container(Space::new().width(Length::Fill).height(Length::Fill))
                    .width(Length::Fill)
                    .height(Length::Fill)
                    .style(style::backdrop_scrim(self.theme)),
            )
            .on_press(Message::ToggleSidebar);

            let drawer_layer = opaque(
                row![drawer, dismiss_scrim]
                    .width(Length::Fill)
                    .height(Length::Fill),
            );

            Stack::new()
                .push(main_view)
                .push(drawer_layer)
                .width(Length::Fill)
                .height(Length::Fill)
                .into()
        } else {
            main_view
        };

        let base_shell = container(
            column![header, body]
                .width(Length::Fill)
                .height(Length::Fill),
        )
        .width(Length::Fill)
        .height(Length::Fill)
        .style(style::root_shell(self.theme));

        let mut layers = Stack::new()
            .push(base_shell)
            .width(Length::Fill)
            .height(Length::Fill);

        if self.search_open && !self.search_query.trim().is_empty() && self.overlay.is_none() {
            layers = layers.push(view_search_dropdown(
                self.theme,
                &self.search_query,
                &self.search_results,
                &self.store.calendars,
                self.today,
                self.store.prefs.use_24h_time,
            ));
        }

        if let Some(active_overlay) = &self.overlay {
            layers = layers.push(view_active_overlay(
                self.theme,
                active_overlay,
                &self.store.calendars,
                &self.store.prefs,
                &self.day_overflow_cache,
                self.today,
                self.now,
            ));
        }

        if self.drop_hovering {
            layers = layers.push(view_drop_overlay(self.theme));
        }

        if let Some(toast) = &self.toast {
            layers = layers.push(view_toast(self.theme, toast));
        }

        layers.into()
    }
}
