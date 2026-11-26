//! Calendar event domain types, RFC 5545 time semantics, and materialized occurrences.

use crate::model::local_timezone_name;
use chrono::{
    DateTime, Datelike, Local, NaiveDate, NaiveDateTime, NaiveTime, TimeDelta, TimeZone, Timelike,
    Utc, Weekday,
};
use uuid::Uuid;

/// Standard reminder presets offered in the Event Editor (minutes before event start).
pub const REMINDER_PRESETS: &[(u32, &str)] = &[
    (0, "At time of event"),
    (5, "5 minutes before"),
    (15, "15 minutes before"),
    (30, "30 minutes before"),
    (60, "1 hour before"),
    (1440, "1 day before"),
];

/// Formats a reminder offset (in minutes before start) into a concise human label.
pub fn format_reminder_minutes(minutes: u32) -> String {
    match minutes {
        0 => "At time of event".to_string(),
        1 => "1 minute before".to_string(),
        m if m < 60 => format!("{m} minutes before"),
        60 => "1 hour before".to_string(),
        m if m % 1440 == 0 => {
            let days = m / 1440;
            if days == 1 {
                "1 day before".to_string()
            } else {
                format!("{days} days before")
            }
        }
        m if m % 60 == 0 => format!("{} hours before", m / 60),
        m => format!("{m} min before"),
    }
}

/// Formats a `NaiveTime` in 24-hour (`14:30`) or 12-hour (`2:30 PM`) format.
pub fn format_clock_time(time: NaiveTime, use_24h: bool) -> String {
    if use_24h {
        format!("{:02}:{:02}", time.hour(), time.minute())
    } else {
        let (is_pm, hour12) = time.hour12();
        let suffix = if is_pm { "PM" } else { "AM" };
        if time.minute() == 0 {
            format!("{hour12} {suffix}")
        } else {
            format!("{hour12}:{:02} {suffix}", time.minute())
        }
    }
}

/// Returns a short 3-letter English weekday label (`Mon`, `Tue`, ...).
pub fn weekday_short_name(day: Weekday) -> &'static str {
    match day {
        Weekday::Mon => "Mon",
        Weekday::Tue => "Tue",
        Weekday::Wed => "Wed",
        Weekday::Thu => "Thu",
        Weekday::Fri => "Fri",
        Weekday::Sat => "Sat",
        Weekday::Sun => "Sun",
    }
}

/// Returns the full English weekday name (`Monday`, `Tuesday`, ...).
pub fn weekday_full_name(day: Weekday) -> &'static str {
    match day {
        Weekday::Mon => "Monday",
        Weekday::Tue => "Tuesday",
        Weekday::Wed => "Wednesday",
        Weekday::Thu => "Thursday",
        Weekday::Fri => "Friday",
        Weekday::Sat => "Saturday",
        Weekday::Sun => "Sunday",
    }
}

/// Formats a calendar date concisely relative to `today`, omitting the current year.
pub fn format_relative_date_short(date: NaiveDate, today: NaiveDate) -> String {
    match (date - today).num_days() {
        0 => "Today".to_string(),
        1 => "Tomorrow".to_string(),
        -1 => "Yesterday".to_string(),
        _ if date.year() == today.year() => date.format("%a, %b %-d").to_string(),
        _ => date.format("%a, %b %-d, %Y").to_string(),
    }
}

/// Formats an Agenda day group heading relative to `today`, omitting the current year.
pub fn format_agenda_day_heading(date: NaiveDate, today: NaiveDate) -> String {
    match (date - today).num_days() {
        0 => format!("Today · {}", date.format("%A, %B %-d")),
        1 => format!("Tomorrow · {}", date.format("%A, %B %-d")),
        -1 => format!("Yesterday · {}", date.format("%A, %B %-d")),
        _ if date.year() == today.year() => date.format("%A, %B %-d").to_string(),
        _ => date.format("%A, %B %-d, %Y").to_string(),
    }
}

fn format_relative_long_date(date: NaiveDate, today: NaiveDate, force_year: bool) -> String {
    match (date - today).num_days() {
        0 => "Today".to_string(),
        1 => "Tomorrow".to_string(),
        -1 => "Yesterday".to_string(),
        _ if !force_year && date.year() == today.year() => date.format("%B %-d").to_string(),
        _ => date.format("%B %-d, %Y").to_string(),
    }
}

fn format_quick_add_endpoint(
    prefix: &str,
    date: NaiveDate,
    today: NaiveDate,
    force_year: bool,
) -> String {
    match (date - today).num_days() {
        0 => format!("{prefix} Today"),
        1 => format!("{prefix} Tomorrow"),
        -1 => format!("{prefix} Yesterday"),
        2..=7 => format!("{prefix} next {}", weekday_full_name(date.weekday())),
        _ if !force_year && date.year() == today.year() => {
            format!("{prefix} {}", date.format("%B %-d"))
        }
        _ => format!("{prefix} {}", date.format("%B %-d, %Y")),
    }
}

/// Formats a GNOME-Calendar-style relative heading for the Quick-Add modal.
pub fn format_quick_add_heading(
    start_date: NaiveDate,
    end_date: NaiveDate,
    all_day: bool,
    start_time: Option<NaiveTime>,
    end_time: Option<NaiveTime>,
    today: NaiveDate,
    use_24h: bool,
) -> String {
    let end_date = end_date.max(start_date);
    if end_date > start_date {
        let force_year = start_date.year() != end_date.year();
        let from_str = format_quick_add_endpoint("from", start_date, today, force_year);
        let to_str = format_quick_add_endpoint("to", end_date, today, force_year);
        return format!("New Event {from_str} {to_str}");
    }

    let day_part = match (start_date - today).num_days() {
        0 => "New Event Today".to_string(),
        1 => "New Event Tomorrow".to_string(),
        -1 => "New Event Yesterday".to_string(),
        2..=7 => format!("New Event next {}", weekday_full_name(start_date.weekday())),
        _ if start_date.year() == today.year() => {
            format!("New Event on {}", start_date.format("%B %-d"))
        }
        _ => format!("New Event on {}", start_date.format("%B %-d, %Y")),
    };

    if !all_day {
        if let (Some(st), Some(et)) = (start_time, end_time) {
            return format!(
                "{day_part}, {} – {}",
                format_clock_time(st, use_24h),
                format_clock_time(et, use_24h)
            );
        }
    }

    day_part
}

/// A launchable HTTP/HTTPS link detected in an event's location or description.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MeetingLink {
    pub service_name: Option<&'static str>,
    pub url: String,
}

impl MeetingLink {
    pub fn button_label(&self) -> String {
        match self.service_name {
            Some(name) => format!("Join {name}"),
            None => "Open Link".to_string(),
        }
    }
}

/// Validates that `raw` is a clean `http://` or `https://` URL with a valid host
/// and no whitespace or ASCII control characters.
pub fn is_safe_http_url(raw: &str) -> bool {
    let trimmed = raw.trim();
    if trimmed.is_empty() || trimmed.chars().any(|c| c.is_control() || c.is_whitespace()) {
        return false;
    }
    let lower = trimmed.to_ascii_lowercase();
    let rest = if let Some(r) = lower.strip_prefix("https://") {
        r
    } else if let Some(r) = lower.strip_prefix("http://") {
        r
    } else {
        return false;
    };
    let authority = rest.split(['/', '?', '#']).next().unwrap_or("");
    let host = authority
        .rsplit('@')
        .next()
        .unwrap_or("")
        .split(':')
        .next()
        .unwrap_or("");
    if host.is_empty() || host.starts_with('.') || host.ends_with('.') {
        return false;
    }
    host.contains('.') || host == "localhost"
}

fn host_matches(host: &str, domain: &str) -> bool {
    host == domain
        || host
            .strip_suffix(domain)
            .is_some_and(|prefix| prefix.ends_with('.'))
}

/// Recognizes common video-conferencing services from an HTTP/HTTPS URL.
pub fn detect_meeting_service(url: &str) -> Option<&'static str> {
    if !is_safe_http_url(url) {
        return None;
    }
    let (_, after_scheme) = url.trim().split_once("://")?;
    let (authority, path_and_query) = match after_scheme.find(['/', '?', '#']) {
        Some(idx) => (&after_scheme[..idx], &after_scheme[idx..]),
        None => (after_scheme, ""),
    };
    let host = authority
        .rsplit('@')
        .next()
        .unwrap_or(authority)
        .split(':')
        .next()
        .unwrap_or("")
        .to_ascii_lowercase();

    if host_matches(&host, "zoom.us")
        || host_matches(&host, "zoom.com")
        || host_matches(&host, "zoomgov.com")
    {
        Some("Zoom")
    } else if host == "meet.google.com" {
        Some("Google Meet")
    } else if host_matches(&host, "teams.microsoft.com") || host_matches(&host, "teams.live.com") {
        Some("Microsoft Teams")
    } else if host_matches(&host, "meet.jit.si")
        || host_matches(&host, "8x8.vc")
        || host == "meet.ffmuc.net"
        || host.starts_with("jitsi.")
    {
        Some("Jitsi Meet")
    } else if host_matches(&host, "webex.com") {
        Some("Webex")
    } else if host_matches(&host, "whereby.com") {
        Some("Whereby")
    } else if host == "discord.gg"
        || (host_matches(&host, "discord.com")
            && (path_and_query.starts_with("/channels/") || path_and_query.starts_with("/invite/")))
    {
        Some("Discord")
    } else if host_matches(&host, "gather.town") {
        Some("Gather")
    } else if host_matches(&host, "bigbluebutton.org") || host.starts_with("bbb.") {
        Some("BigBlueButton")
    } else if host_matches(&host, "bluejeans.com") {
        Some("BlueJeans")
    } else if host_matches(&host, "gotomeet.me")
        || host_matches(&host, "gotomeeting.com")
        || host == "meet.goto.com"
    {
        Some("GoTo Meeting")
    } else {
        None
    }
}

fn trim_url_trailing_punctuation(raw: &str) -> &str {
    let mut s = raw.trim();
    loop {
        if let Some(stripped) = s.strip_suffix(['.', ',', ';', ':', '!', '?']) {
            s = stripped;
            continue;
        }
        if s.ends_with(')') && s.matches(')').count() > s.matches('(').count() {
            s = &s[..s.len() - 1];
            continue;
        }
        if s.ends_with(']') && s.matches(']').count() > s.matches('[').count() {
            s = &s[..s.len() - 1];
            continue;
        }
        if s.ends_with('}') && s.matches('}').count() > s.matches('{').count() {
            s = &s[..s.len() - 1];
            continue;
        }
        break;
    }
    s
}

fn scan_http_urls(text: &str) -> Vec<String> {
    let lower = text.to_ascii_lowercase();
    let bytes = text.as_bytes();
    let mut urls = Vec::new();
    let mut cursor = 0usize;

    while cursor < lower.len() {
        let next_https = lower[cursor..].find("https://").map(|i| cursor + i);
        let next_http = lower[cursor..].find("http://").map(|i| cursor + i);
        let start = match (next_https, next_http) {
            (Some(a), Some(b)) => a.min(b),
            (Some(a), None) => a,
            (None, Some(b)) => b,
            (None, None) => break,
        };

        let mut end = start;
        while end < bytes.len() {
            let b = bytes[end];
            if b.is_ascii_whitespace()
                || b.is_ascii_control()
                || matches!(b, b'"' | b'\'' | b'<' | b'>')
            {
                break;
            }
            end += 1;
        }

        let candidate = trim_url_trailing_punctuation(&text[start..end]);
        if is_safe_http_url(candidate) {
            urls.push(candidate.to_string());
        }
        cursor = end.max(start + 7);
    }

    urls
}

/// Extracts deduplicated meeting/action links from `location` and `description`.
///
/// Returns `(links, location_is_url)`. Any valid HTTP/HTTPS URL placed directly as the
/// location is returned and marks `location_is_url = true` so the preview can hide the
/// raw URL text. Descriptions only contribute recognized conferencing URLs.
pub fn extract_meeting_links(
    location: Option<&str>,
    description: Option<&str>,
) -> (Vec<MeetingLink>, bool) {
    let mut links: Vec<MeetingLink> = Vec::new();
    let mut location_is_url = false;

    let mut push_unique = |url: String, service_name: Option<&'static str>| {
        if !links.iter().any(|existing| existing.url == url) {
            links.push(MeetingLink { service_name, url });
        }
    };

    if let Some(loc_raw) = location {
        let loc_trimmed = trim_url_trailing_punctuation(loc_raw.trim());
        if is_safe_http_url(loc_trimmed) {
            location_is_url = true;
            push_unique(loc_trimmed.to_string(), detect_meeting_service(loc_trimmed));
        } else {
            for url in scan_http_urls(loc_raw) {
                if let Some(service) = detect_meeting_service(&url) {
                    push_unique(url, Some(service));
                }
            }
        }
    }

    if let Some(desc_raw) = description {
        for url in scan_http_urls(desc_raw) {
            if let Some(service) = detect_meeting_service(&url) {
                push_unique(url, Some(service));
            }
        }
    }

    (links, location_is_url)
}

/// Percent-encodes a UTF-8 string according to RFC 3986 unreserved characters.
pub fn percent_encode_uri_component(input: &str) -> String {
    const HEX: &[u8; 16] = b"0123456789ABCDEF";
    let mut out = String::with_capacity(input.len() + 8);
    for &b in input.as_bytes() {
        if b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.' | b'~') {
            out.push(b as char);
        } else {
            out.push('%');
            out.push(HEX[(b >> 4) as usize] as char);
            out.push(HEX[(b & 0x0f) as usize] as char);
        }
    }
    out
}

/// Builds a browser-launchable OpenStreetMap search URL for a physical location string.
/// Returns `None` if `location` is blank or is already an HTTP/HTTPS URL.
pub fn build_location_map_url(location: &str) -> Option<String> {
    let trimmed = location.trim();
    if trimmed.is_empty() || is_safe_http_url(trim_url_trailing_punctuation(trimmed)) {
        return None;
    }
    Some(format!(
        "https://www.openstreetmap.org/search?query={}",
        percent_encode_uri_component(trimmed)
    ))
}

/// Resolves a local wall-clock `NaiveDateTime` into a `DateTime<Local>` without panicking
/// on DST ambiguities (chooses earliest) or DST spring-forward gaps (steps to next valid minute).
pub fn resolve_local_datetime(naive: NaiveDateTime) -> DateTime<Local> {
    if let Some(dt) = Local.from_local_datetime(&naive).earliest() {
        return dt;
    }
    for step_minutes in 1..=180 {
        let shifted = naive + TimeDelta::minutes(step_minutes);
        if let Some(dt) = Local.from_local_datetime(&shifted).earliest() {
            return dt;
        }
    }
    naive.and_utc().with_timezone(&Local)
}

/// Unrecognized RFC 5545 `VEVENT` property preserved verbatim across `.ics` load/save cycles.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RawProperty {
    pub key: String,
    pub value: String,
    pub params: Vec<(String, String)>,
}

/// Primary key of a stored `VEVENT` record within a calendar (RFC 5545 `UID` + optional `RECURRENCE-ID`).
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct EventKey {
    pub uid: String,
    pub recurrence_id: Option<DateTime<Utc>>,
}

impl EventKey {
    pub fn master(uid: impl Into<String>) -> Self {
        Self {
            uid: uid.into(),
            recurrence_id: None,
        }
    }

    pub fn with_recurrence_id(uid: impl Into<String>, recurrence_id: Option<DateTime<Utc>>) -> Self {
        Self {
            uid: uid.into(),
            recurrence_id,
        }
    }
}

/// Whether an edit/delete of a recurring event applies only to the selected instance or the whole series.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EditScope {
    Single,
    All,
}

/// Exact RFC 5545 temporal representation of an event.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EventTime {
    /// All-day event using RFC 5545 exclusive `end` date (single day: `end = start + 1 day`).
    AllDay { start: NaiveDate, end: NaiveDate },
    /// Floating wall-clock event interpreted in whatever local timezone the user is in.
    Floating {
        start: NaiveDateTime,
        end: NaiveDateTime,
    },
    /// Exact UTC instants paired with an IANA `TZID` (or `"UTC"`) for DST-aware recurrence.
    Zoned {
        start: DateTime<Utc>,
        end: DateTime<Utc>,
        tz: String,
    },
}

impl EventTime {
    pub fn is_all_day(&self) -> bool {
        matches!(self, Self::AllDay { .. })
    }

    pub fn duration(&self) -> TimeDelta {
        match self {
            Self::AllDay { start, end } => {
                let days = (*end - *start).num_days().max(1);
                TimeDelta::days(days)
            }
            Self::Floating { start, end } => (*end - *start).max(TimeDelta::zero()),
            Self::Zoned { start, end, .. } => (*end - *start).max(TimeDelta::zero()),
        }
    }

    pub fn all_day_span_days(&self) -> i64 {
        match self {
            Self::AllDay { start, end } => (*end - *start).num_days().max(1),
            _ => 0,
        }
    }

    /// Canonical UTC anchor used for `RECURRENCE-ID` and `EXDATE` matching.
    pub fn start_anchor_utc(&self) -> DateTime<Utc> {
        match self {
            Self::AllDay { start, .. } => start
                .and_hms_opt(0, 0, 0)
                .expect("midnight is always valid")
                .and_utc(),
            Self::Floating { start, .. } => start.and_utc(),
            Self::Zoned { start, .. } => *start,
        }
    }

    pub fn start_local(&self) -> DateTime<Local> {
        match self {
            Self::AllDay { start, .. } => {
                resolve_local_datetime(start.and_hms_opt(0, 0, 0).expect("midnight"))
            }
            Self::Floating { start, .. } => resolve_local_datetime(*start),
            Self::Zoned { start, .. } => start.with_timezone(&Local),
        }
    }

    pub fn end_local(&self) -> DateTime<Local> {
        match self {
            Self::AllDay { start, end } => {
                let effective_end = if *end > *start {
                    *end
                } else {
                    start.succ_opt().unwrap_or(*start)
                };
                resolve_local_datetime(effective_end.and_hms_opt(0, 0, 0).expect("midnight"))
            }
            Self::Floating { start, end } => resolve_local_datetime((*end).max(*start)),
            Self::Zoned { start, end, .. } => (*end).max(*start).with_timezone(&Local),
        }
    }

    /// Inclusive local `(start_date, end_date)` pair.
    /// Properly treats an exact `00:00:00` timed end as ending on the previous calendar day.
    pub fn inclusive_day_span_local(&self) -> (NaiveDate, NaiveDate) {
        match self {
            Self::AllDay { start, end } => {
                let last = if *end > *start {
                    end.pred_opt().unwrap_or(*start)
                } else {
                    *start
                };
                (*start, last.max(*start))
            }
            Self::Floating { .. } | Self::Zoned { .. } => {
                let start_dt = self.start_local();
                let end_dt = self.end_local();
                inclusive_dates_for_timed(start_dt, end_dt)
            }
        }
    }

    pub fn timezone_name(&self) -> Option<&str> {
        match self {
            Self::Zoned { tz, .. } => Some(tz.as_str()),
            _ => None,
        }
    }
}

/// Computes inclusive local `(start_date, end_date)` for a timed occurrence, ensuring an event
/// ending precisely at `00:00:00` does not spill onto the next day.
pub fn inclusive_dates_for_timed(
    start_local: DateTime<Local>,
    end_local: DateTime<Local>,
) -> (NaiveDate, NaiveDate) {
    let start_date = start_local.date_naive();
    if end_local <= start_local {
        return (start_date, start_date);
    }
    let adjusted_end = end_local - TimeDelta::nanoseconds(1);
    let end_date = adjusted_end.date_naive().max(start_date);
    (start_date, end_date)
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Event {
    pub uid: String,
    pub calendar_id: String,
    pub summary: String,
    pub description: Option<String>,
    pub location: Option<String>,
    pub time: EventTime,
    pub rrule: Option<String>,
    pub exdates: Vec<DateTime<Utc>>,
    pub rdates: Vec<DateTime<Utc>>,
    pub recurrence_id: Option<DateTime<Utc>>,
    pub reminders: Vec<u32>,
    pub sequence: u32,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub preserved_props: Vec<RawProperty>,
}

impl Event {
    pub fn new_all_day(
        calendar_id: impl Into<String>,
        summary: impl Into<String>,
        start: NaiveDate,
        end_exclusive: NaiveDate,
    ) -> Self {
        let now = Utc::now();
        let end = if end_exclusive > start {
            end_exclusive
        } else {
            start.succ_opt().unwrap_or(start)
        };
        Self {
            uid: Uuid::new_v4().to_string(),
            calendar_id: calendar_id.into(),
            summary: summary.into(),
            description: None,
            location: None,
            time: EventTime::AllDay { start, end },
            rrule: None,
            exdates: Vec::new(),
            rdates: Vec::new(),
            recurrence_id: None,
            reminders: Vec::new(),
            sequence: 0,
            created_at: now,
            updated_at: now,
            preserved_props: Vec::new(),
        }
    }

    pub fn new_timed_local(
        calendar_id: impl Into<String>,
        summary: impl Into<String>,
        start_local: NaiveDateTime,
        end_local: NaiveDateTime,
    ) -> Self {
        let now = Utc::now();
        let clamped_end = if end_local >= start_local {
            end_local
        } else {
            start_local + TimeDelta::hours(1)
        };

        let time = if let Some(tz_name) = local_timezone_name() {
            let start_utc = resolve_local_datetime(start_local).with_timezone(&Utc);
            let end_utc = resolve_local_datetime(clamped_end).with_timezone(&Utc);
            EventTime::Zoned {
                start: start_utc,
                end: end_utc.max(start_utc),
                tz: tz_name.to_string(),
            }
        } else {
            EventTime::Floating {
                start: start_local,
                end: clamped_end,
            }
        };

        Self {
            uid: Uuid::new_v4().to_string(),
            calendar_id: calendar_id.into(),
            summary: summary.into(),
            description: None,
            location: None,
            time,
            rrule: None,
            exdates: Vec::new(),
            rdates: Vec::new(),
            recurrence_id: None,
            reminders: Vec::new(),
            sequence: 0,
            created_at: now,
            updated_at: now,
            preserved_props: Vec::new(),
        }
    }

    pub fn key(&self) -> EventKey {
        EventKey::with_recurrence_id(self.uid.clone(), self.recurrence_id)
    }

    pub fn is_recurring(&self) -> bool {
        self.rrule.is_some() || !self.rdates.is_empty()
    }

    pub fn is_override(&self) -> bool {
        self.recurrence_id.is_some()
    }

    pub fn start_anchor_utc(&self) -> DateTime<Utc> {
        self.time.start_anchor_utc()
    }

    pub fn add_exdate(&mut self, occurrence_id: DateTime<Utc>) {
        let already_present = match self.time {
            EventTime::AllDay { .. } => self
                .exdates
                .iter()
                .any(|d| d.date_naive() == occurrence_id.date_naive()),
            _ => self
                .exdates
                .iter()
                .any(|d| d.timestamp() == occurrence_id.timestamp()),
        };
        if !already_present {
            self.exdates.push(occurrence_id);
            self.exdates.sort_unstable();
        }
    }

    pub fn normalize_reminders(&mut self) {
        self.reminders.sort_unstable();
        self.reminders.dedup();
    }

    pub fn matches_query(&self, needle_lower: &str) -> bool {
        if needle_lower.is_empty() {
            return true;
        }
        if self.summary.to_lowercase().contains(needle_lower) {
            return true;
        }
        if let Some(loc) = &self.location {
            if loc.to_lowercase().contains(needle_lower) {
                return true;
            }
        }
        if let Some(desc) = &self.description {
            if desc.to_lowercase().contains(needle_lower) {
                return true;
            }
        }
        false
    }
}

/// A single materialized event occurrence projected into local time for layout and rendering.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EventOccurrence {
    pub event_key: EventKey,
    pub calendar_id: String,
    pub occurrence_id: DateTime<Utc>,
    pub is_recurring_instance: bool,
    pub is_all_day: bool,
    pub start_local: DateTime<Local>,
    pub end_local: DateTime<Local>,
    pub start_date: NaiveDate,
    pub end_date: NaiveDate,
    pub summary: String,
    pub location: Option<String>,
    pub description: Option<String>,
    pub reminders: Vec<u32>,
    pub rrule: Option<String>,
}

impl EventOccurrence {
    pub fn spans_multiple_days(&self) -> bool {
        self.end_date > self.start_date
    }

    pub fn is_past(&self, now: DateTime<Local>) -> bool {
        if self.is_all_day {
            self.end_date < now.date_naive()
        } else {
            self.end_local <= now
        }
    }

    pub fn format_short_start(&self, use_24h: bool) -> String {
        if self.is_all_day {
            "All day".to_string()
        } else {
            format_clock_time(self.start_local.time(), use_24h)
        }
    }

    pub fn format_time_range(&self, use_24h: bool) -> String {
        if self.is_all_day {
            if self.spans_multiple_days() {
                format!(
                    "{} – {}",
                    self.start_date.format("%b %-d"),
                    self.end_date.format("%b %-d")
                )
            } else {
                "All day".to_string()
            }
        } else if self.spans_multiple_days() {
            format!(
                "{} {} – {} {}",
                self.start_date.format("%b %-d"),
                format_clock_time(self.start_local.time(), use_24h),
                self.end_date.format("%b %-d"),
                format_clock_time(self.end_local.time(), use_24h),
            )
        } else {
            format!(
                "{} – {}",
                format_clock_time(self.start_local.time(), use_24h),
                format_clock_time(self.end_local.time(), use_24h),
            )
        }
    }

    pub fn format_date_time_summary(&self, use_24h: bool) -> String {
        if self.is_all_day {
            if self.spans_multiple_days() {
                format!(
                    "{} – {} · All day",
                    self.start_date.format("%a, %b %-d, %Y"),
                    self.end_date.format("%a, %b %-d, %Y"),
                )
            } else {
                format!("{} · All day", self.start_date.format("%A, %B %-d, %Y"))
            }
        } else if self.spans_multiple_days() {
            format!(
                "{}, {} – {}, {}",
                self.start_date.format("%a, %b %-d"),
                format_clock_time(self.start_local.time(), use_24h),
                self.end_date.format("%a, %b %-d, %Y"),
                format_clock_time(self.end_local.time(), use_24h),
            )
        } else {
            format!(
                "{} · {} – {}",
                self.start_date.format("%A, %B %-d, %Y"),
                format_clock_time(self.start_local.time(), use_24h),
                format_clock_time(self.end_local.time(), use_24h),
            )
        }
    }

    /// Formats a GNOME-Calendar-inspired relative date/time summary for Event Preview,
    /// using Today/Tomorrow/Yesterday and omitting the current year when unambiguous.
    pub fn format_relative_date_time_summary(&self, today: NaiveDate, use_24h: bool) -> String {
        if self.is_all_day {
            if self.spans_multiple_days() {
                let force_year = self.start_date.year() != self.end_date.year();
                format!(
                    "{} – {} · All day",
                    format_relative_long_date(self.start_date, today, force_year),
                    format_relative_long_date(self.end_date, today, force_year),
                )
            } else {
                format!(
                    "{} · All day",
                    format_relative_long_date(self.start_date, today, false),
                )
            }
        } else if self.spans_multiple_days() {
            let force_year = self.start_date.year() != self.end_date.year();
            let fmt_endpoint = |date: NaiveDate, time: NaiveTime| {
                let date_part = format_relative_long_date(date, today, force_year);
                let time_part = format_clock_time(time, use_24h);
                if matches!((date - today).num_days(), -1..=1) {
                    format!("{date_part} {time_part}")
                } else {
                    format!("{date_part}, {time_part}")
                }
            };
            format!(
                "{} – {}",
                fmt_endpoint(self.start_date, self.start_local.time()),
                fmt_endpoint(self.end_date, self.end_local.time()),
            )
        } else {
            format!(
                "{}, {} – {}",
                format_relative_long_date(self.start_date, today, false),
                format_clock_time(self.start_local.time(), use_24h),
                format_clock_time(self.end_local.time(), use_24h),
            )
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exact_midnight_timed_end_stays_on_same_day() {
        let start = resolve_local_datetime(
            NaiveDate::from_ymd_opt(2026, 11, 26)
                .unwrap()
                .and_hms_opt(22, 0, 0)
                .unwrap(),
        );
        let end = resolve_local_datetime(
            NaiveDate::from_ymd_opt(2026, 11, 27)
                .unwrap()
                .and_hms_opt(0, 0, 0)
                .unwrap(),
        );
        let (s, e) = inclusive_dates_for_timed(start, end);
        assert_eq!(s, NaiveDate::from_ymd_opt(2026, 11, 26).unwrap());
        assert_eq!(e, NaiveDate::from_ymd_opt(2026, 11, 26).unwrap());
    }

    #[test]
    fn all_day_exclusive_end_maps_to_inclusive_days() {
        let single = EventTime::AllDay {
            start: NaiveDate::from_ymd_opt(2026, 11, 26).unwrap(),
            end: NaiveDate::from_ymd_opt(2026, 11, 27).unwrap(),
        };
        assert_eq!(
            single.inclusive_day_span_local(),
            (
                NaiveDate::from_ymd_opt(2026, 11, 26).unwrap(),
                NaiveDate::from_ymd_opt(2026, 11, 26).unwrap(),
            )
        );

        let multi = EventTime::AllDay {
            start: NaiveDate::from_ymd_opt(2026, 11, 26).unwrap(),
            end: NaiveDate::from_ymd_opt(2026, 11, 29).unwrap(),
        };
        assert_eq!(
            multi.inclusive_day_span_local(),
            (
                NaiveDate::from_ymd_opt(2026, 11, 26).unwrap(),
                NaiveDate::from_ymd_opt(2026, 11, 28).unwrap(),
            )
        );
    }

    #[test]
    fn clock_and_reminder_formatting_are_clean() {
        let t = NaiveTime::from_hms_opt(14, 30, 0).unwrap();
        assert_eq!(format_clock_time(t, true), "14:30");
        assert_eq!(format_clock_time(t, false), "2:30 PM");

        let sharp = NaiveTime::from_hms_opt(9, 0, 0).unwrap();
        assert_eq!(format_clock_time(sharp, false), "9 AM");

        assert_eq!(format_reminder_minutes(0), "At time of event");
        assert_eq!(format_reminder_minutes(15), "15 minutes before");
        assert_eq!(format_reminder_minutes(60), "1 hour before");
        assert_eq!(format_reminder_minutes(1440), "1 day before");
    }

    #[test]
    fn relative_date_and_quick_add_formatting_match_expected_phrasing() {
        let today = NaiveDate::from_ymd_opt(2026, 11, 26).unwrap(); // Thu
        let yesterday = NaiveDate::from_ymd_opt(2026, 11, 25).unwrap();
        let tomorrow = NaiveDate::from_ymd_opt(2026, 11, 27).unwrap();
        let next_tue = NaiveDate::from_ymd_opt(2026, 12, 1).unwrap();
        let next_year = NaiveDate::from_ymd_opt(2027, 1, 5).unwrap();

        assert_eq!(format_relative_date_short(today, today), "Today");
        assert_eq!(format_relative_date_short(tomorrow, today), "Tomorrow");
        assert_eq!(format_relative_date_short(yesterday, today), "Yesterday");
        assert_eq!(format_relative_date_short(next_tue, today), "Tue, Dec 1");
        assert_eq!(
            format_relative_date_short(next_year, today),
            "Tue, Jan 5, 2027"
        );

        assert_eq!(
            format_agenda_day_heading(yesterday, today),
            "Yesterday · Wednesday, November 25"
        );
        assert_eq!(
            format_agenda_day_heading(next_tue, today),
            "Tuesday, December 1"
        );
        assert_eq!(
            format_agenda_day_heading(next_year, today),
            "Tuesday, January 5, 2027"
        );

        assert_eq!(
            format_quick_add_heading(
                today,
                today,
                false,
                NaiveTime::from_hms_opt(14, 0, 0),
                NaiveTime::from_hms_opt(15, 30, 0),
                today,
                true,
            ),
            "New Event Today, 14:00 – 15:30"
        );
        assert_eq!(
            format_quick_add_heading(next_tue, next_tue, true, None, None, today, true),
            "New Event next Tuesday"
        );
        assert_eq!(
            format_quick_add_heading(today, next_tue, true, None, None, today, true),
            "New Event from Today to next Tuesday"
        );

        let start_dt = resolve_local_datetime(today.and_hms_opt(14, 0, 0).unwrap());
        let end_dt = resolve_local_datetime(today.and_hms_opt(15, 0, 0).unwrap());
        let occ = EventOccurrence {
            event_key: EventKey::master("uid-rel"),
            calendar_id: "cal-1".into(),
            occurrence_id: start_dt.with_timezone(&Utc),
            is_recurring_instance: false,
            is_all_day: false,
            start_local: start_dt,
            end_local: end_dt,
            start_date: today,
            end_date: today,
            summary: "Demo".into(),
            location: None,
            description: None,
            reminders: Vec::new(),
            rrule: None,
        };
        assert_eq!(
            occ.format_relative_date_time_summary(today, true),
            "Today, 14:00 – 15:00"
        );
    }

    #[test]
    fn extracts_meeting_links_and_encodes_map_urls_safely() {
        assert_eq!(
            detect_meeting_service("https://us02web.zoom.us/j/123456789?pwd=abc"),
            Some("Zoom")
        );
        assert_eq!(
            detect_meeting_service("https://meet.google.com/abc-defg-hij"),
            Some("Google Meet")
        );
        assert_eq!(
            detect_meeting_service("https://teams.microsoft.com/l/meetup-join/xyz"),
            Some("Microsoft Teams")
        );
        assert_eq!(
            detect_meeting_service("https://meet.jit.si/DuskySync"),
            Some("Jitsi Meet")
        );
        assert_eq!(detect_meeting_service("https://example.com/agenda"), None);
        assert!(!is_safe_http_url("javascript:alert(1)"));
        assert!(!is_safe_http_url("https://bad host/path"));

        let (links, loc_is_url) = extract_meeting_links(
            Some("https://meet.google.com/abc-defg-hij"),
            Some(
                "Agenda: https://example.com/doc\nJoin (https://meet.google.com/abc-defg-hij).\nBackup: https://us02web.zoom.us/j/999.",
            ),
        );
        assert!(loc_is_url);
        assert_eq!(links.len(), 2);
        assert_eq!(links[0].button_label(), "Join Google Meet");
        assert_eq!(links[1].button_label(), "Join Zoom");
        assert_eq!(links[1].url, "https://us02web.zoom.us/j/999");

        let (generic_links, generic_loc_is_url) =
            extract_meeting_links(Some("https://example.com/room"), None);
        assert!(generic_loc_is_url);
        assert_eq!(generic_links.len(), 1);
        assert_eq!(generic_links[0].button_label(), "Open Link");

        assert_eq!(
            percent_encode_uri_component("Dal Lake, Srinagar & Co."),
            "Dal%20Lake%2C%20Srinagar%20%26%20Co."
        );
        assert_eq!(
            build_location_map_url("Conference Hall 2B").as_deref(),
            Some("https://www.openstreetmap.org/search?query=Conference%20Hall%202B")
        );
        assert_eq!(
            build_location_map_url("https://meet.google.com/abc-defg-hij"),
            None
        );
    }
}
