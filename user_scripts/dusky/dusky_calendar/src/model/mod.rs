//! Domain models for Dusky Calendar: calendars, events, occurrences, recurrence,
//! and zero-dependency Linux IANA timezone discovery.

pub mod calendar;
pub mod event;
pub mod recurrence;

pub use calendar::{CalendarColor, CalendarMeta};
pub use event::{
    EditScope, Event, EventKey, EventOccurrence, EventTime, MeetingLink, RawProperty,
    build_location_map_url, detect_meeting_service, extract_meeting_links,
    format_agenda_day_heading, format_clock_time, format_quick_add_heading,
    format_relative_date_short, format_reminder_minutes, is_safe_http_url,
    percent_encode_uri_component, resolve_local_datetime, weekday_full_name, weekday_short_name,
};
pub use recurrence::{
    RecurrenceEnd, RecurrenceFreq, RecurrenceRule, expand_events_in_range,
};

use std::path::Path;
use std::sync::OnceLock;
use std::{env, fs};

/// Parse an IANA timezone name from a symlink target path pointing into `zoneinfo/`.
fn parse_zoneinfo_path(path: &Path) -> Option<String> {
    let raw = path.to_str()?;
    let (_, tail) = raw.split_once("zoneinfo/")?;
    let trimmed = tail.trim_start_matches('/');
    let candidate = trimmed
        .strip_prefix("posix/")
        .or_else(|| trimmed.strip_prefix("right/"))
        .unwrap_or(trimmed);
    candidate
        .parse::<chrono_tz::Tz>()
        .ok()
        .map(|tz| tz.name().to_string())
}

fn detect_local_timezone() -> Option<String> {
    if let Ok(tz_env) = env::var("TZ") {
        let candidate = tz_env.trim().trim_start_matches(':');
        if let Ok(tz) = candidate.parse::<chrono_tz::Tz>() {
            return Some(tz.name().to_string());
        }
        if let Some(parsed) = parse_zoneinfo_path(Path::new(candidate)) {
            return Some(parsed);
        }
    }

    if let Ok(target) = fs::read_link("/etc/localtime") {
        if let Some(parsed) = parse_zoneinfo_path(&target) {
            return Some(parsed);
        }
    }

    if let Ok(canonical) = fs::canonicalize("/etc/localtime") {
        if let Some(parsed) = parse_zoneinfo_path(&canonical) {
            return Some(parsed);
        }
    }

    if let Ok(raw) = fs::read_to_string("/etc/timezone") {
        let candidate = raw.trim();
        if let Ok(tz) = candidate.parse::<chrono_tz::Tz>() {
            return Some(tz.name().to_string());
        }
    }

    None
}

/// Returns the detected system IANA timezone name (e.g. `"Asia/Kolkata"`), cached once per process.
pub fn local_timezone_name() -> Option<&'static str> {
    static TZ_NAME: OnceLock<Option<String>> = OnceLock::new();
    TZ_NAME.get_or_init(detect_local_timezone).as_deref()
}

/// Returns the detected system IANA timezone parsed as `chrono_tz::Tz`, if available.
pub fn local_chrono_tz() -> Option<chrono_tz::Tz> {
    local_timezone_name().and_then(|name| name.parse::<chrono_tz::Tz>().ok())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_standard_and_relative_zoneinfo_links() {
        assert_eq!(
            parse_zoneinfo_path(Path::new("../usr/share/zoneinfo/Asia/Kolkata")).as_deref(),
            Some("Asia/Kolkata")
        );
        assert_eq!(
            parse_zoneinfo_path(Path::new("/usr/share/zoneinfo/posix/America/New_York")).as_deref(),
            Some("America/New_York")
        );
        assert_eq!(
            parse_zoneinfo_path(Path::new("/usr/share/zoneinfo/NotAReal/Zone")),
            None
        );
    }
}
