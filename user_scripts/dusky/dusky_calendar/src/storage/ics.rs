//! Pure-`chrono` RFC 5545 `.ics` (iCalendar) parser and serializer.
//!
//! Handles `VEVENT`, `VALUE=DATE` all-day events, floating times, UTC and IANA `TZID`
//! zoned times, `VTIMEZONE` & Windows timezone alias mapping, `DURATION` fallback,
//! `RRULE`, `EXDATE`, `RDATE`, `RECURRENCE-ID`, `STATUS:CANCELLED`, `VALARM` reminders,
//! and lossless round-tripping of unrecognized `VEVENT` properties.

use crate::model::{Event, EventTime, RawProperty};
use chrono::{DateTime, NaiveDate, NaiveDateTime, TimeDelta, TimeZone, Utc};
use icalendar::{
    Alarm, Calendar as IcsCalendar, CalendarComponent, CalendarDateTime, Component,
    DatePerhapsTime, Event as IcsEvent, EventLike, EventStatus, Parameter, Property,
};
use std::collections::{BTreeMap, HashSet};
use uuid::Uuid;

#[derive(Debug)]
pub struct ParsedIcs {
    pub events: Vec<Event>,
    pub skipped: usize,
}

const KNOWN_EVENT_PROPS: &[&str] = &[
    "UID",
    "SUMMARY",
    "DESCRIPTION",
    "LOCATION",
    "DTSTART",
    "DTEND",
    "DURATION",
    "DTSTAMP",
    "CREATED",
    "LAST-MODIFIED",
    "SEQUENCE",
    "RRULE",
    "EXDATE",
    "RDATE",
    "RECURRENCE-ID",
    "STATUS",
];

fn map_windows_timezone(name: &str) -> Option<&'static str> {
    match name.trim() {
        "UTC" | "Coordinated Universal Time" | "Greenwich Standard Time" => Some("UTC"),
        "GMT Standard Time" => Some("Europe/London"),
        "W. Europe Standard Time" => Some("Europe/Berlin"),
        "Romance Standard Time" => Some("Europe/Paris"),
        "Central European Standard Time" => Some("Europe/Warsaw"),
        "Central Europe Standard Time" => Some("Europe/Budapest"),
        "E. Europe Standard Time" => Some("Europe/Chisinau"),
        "FLE Standard Time" => Some("Europe/Kiev"),
        "GTB Standard Time" => Some("Europe/Bucharest"),
        "Russian Standard Time" => Some("Europe/Moscow"),
        "Turkey Standard Time" => Some("Europe/Istanbul"),
        "Eastern Standard Time" | "US Eastern Standard Time" => Some("America/New_York"),
        "Central Standard Time" => Some("America/Chicago"),
        "Mountain Standard Time" => Some("America/Denver"),
        "US Mountain Standard Time" => Some("America/Phoenix"),
        "Pacific Standard Time" => Some("America/Los_Angeles"),
        "Alaskan Standard Time" => Some("America/Anchorage"),
        "Hawaiian Standard Time" => Some("Pacific/Honolulu"),
        "Atlantic Standard Time" => Some("America/Halifax"),
        "Newfoundland Standard Time" => Some("America/St_Johns"),
        "SA Eastern Standard Time" => Some("America/Cayenne"),
        "E. South America Standard Time" => Some("America/Sao_Paulo"),
        "Argentina Standard Time" => Some("America/Buenos_Aires"),
        "India Standard Time" => Some("Asia/Kolkata"),
        "Pakistan Standard Time" => Some("Asia/Karachi"),
        "Bangladesh Standard Time" => Some("Asia/Dhaka"),
        "Nepal Standard Time" => Some("Asia/Kathmandu"),
        "Sri Lanka Standard Time" => Some("Asia/Colombo"),
        "Arabian Standard Time" => Some("Asia/Dubai"),
        "Arab Standard Time" => Some("Asia/Riyadh"),
        "Israel Standard Time" => Some("Asia/Jerusalem"),
        "China Standard Time" => Some("Asia/Shanghai"),
        "Taipei Standard Time" => Some("Asia/Taipei"),
        "Singapore Standard Time" => Some("Asia/Singapore"),
        "SE Asia Standard Time" => Some("Asia/Bangkok"),
        "Tokyo Standard Time" => Some("Asia/Tokyo"),
        "Korea Standard Time" => Some("Asia/Seoul"),
        "AUS Eastern Standard Time" => Some("Australia/Sydney"),
        "AUS Central Standard Time" => Some("Australia/Darwin"),
        "W. Australia Standard Time" => Some("Australia/Perth"),
        "New Zealand Standard Time" => Some("Pacific/Auckland"),
        "South Africa Standard Time" => Some("Africa/Johannesburg"),
        "Egypt Standard Time" => Some("Africa/Cairo"),
        _ => None,
    }
}

fn collect_vtimezone_aliases(components: &[CalendarComponent]) -> BTreeMap<String, chrono_tz::Tz> {
    let mut aliases = BTreeMap::new();
    for component in components {
        if let CalendarComponent::Other(comp) = component {
            if !comp.component_kind().eq_ignore_ascii_case("VTIMEZONE") {
                continue;
            }
            let Some(tzid) = comp.property_value("TZID") else {
                continue;
            };
            if let Ok(tz) = tzid.trim().parse::<chrono_tz::Tz>() {
                aliases.insert(tzid.to_string(), tz);
                continue;
            }
            if let Some(mapped) = map_windows_timezone(tzid) {
                if let Ok(tz) = mapped.parse::<chrono_tz::Tz>() {
                    aliases.insert(tzid.to_string(), tz);
                    continue;
                }
            }
            if let Some(lic) = comp.property_value("X-LIC-LOCATION") {
                let candidate = lic.trim().strip_prefix("SystemV/").unwrap_or(lic.trim());
                if let Ok(tz) = candidate.parse::<chrono_tz::Tz>() {
                    aliases.insert(tzid.to_string(), tz);
                }
            }
        }
    }
    aliases
}

fn resolve_tz(tzid: &str, aliases: &BTreeMap<String, chrono_tz::Tz>) -> Option<chrono_tz::Tz> {
    let trimmed = tzid.trim().trim_matches('"');
    if let Some(&tz) = aliases.get(trimmed) {
        return Some(tz);
    }
    if let Ok(tz) = trimmed.parse::<chrono_tz::Tz>() {
        return Some(tz);
    }
    if let Some(stripped) = trimmed.strip_prefix('/') {
        if let Ok(tz) = stripped.parse::<chrono_tz::Tz>() {
            return Some(tz);
        }
    }
    map_windows_timezone(trimmed).and_then(|iana| iana.parse::<chrono_tz::Tz>().ok())
}

fn resolve_in_tz(naive: NaiveDateTime, zone: chrono_tz::Tz) -> DateTime<Utc> {
    if let Some(dt) = zone.from_local_datetime(&naive).earliest() {
        return dt.with_timezone(&Utc);
    }
    for step_minutes in 1..=180 {
        let shifted = naive + TimeDelta::minutes(step_minutes);
        if let Some(dt) = zone.from_local_datetime(&shifted).earliest() {
            return dt.with_timezone(&Utc);
        }
    }
    naive.and_utc()
}

fn date_perhaps_time_to_utc(
    dt: &DatePerhapsTime,
    aliases: &BTreeMap<String, chrono_tz::Tz>,
) -> DateTime<Utc> {
    match dt {
        DatePerhapsTime::Date(date) => date
            .and_hms_opt(0, 0, 0)
            .expect("midnight")
            .and_utc(),
        DatePerhapsTime::DateTime(CalendarDateTime::Utc(utc)) => *utc,
        DatePerhapsTime::DateTime(CalendarDateTime::Floating(naive)) => naive.and_utc(),
        DatePerhapsTime::DateTime(CalendarDateTime::WithTimezone { date_time, tzid }) => {
            if let Some(zone) = resolve_tz(tzid, aliases) {
                resolve_in_tz(*date_time, zone)
            } else {
                date_time.and_utc()
            }
        }
    }
}

/// Parses an RFC 5545 duration string (e.g. `-PT15M`, `P1D`, `P2W`, `PT1H30M`) into signed seconds.
pub fn parse_ics_duration_seconds(raw: &str) -> Option<i64> {
    let mut s = raw.trim();
    if s.is_empty() {
        return None;
    }
    let sign: i64 = if let Some(rest) = s.strip_prefix('-') {
        s = rest;
        -1
    } else if let Some(rest) = s.strip_prefix('+') {
        s = rest;
        1
    } else {
        1
    };

    s = s.strip_prefix('P')?;
    if s.is_empty() {
        return None;
    }

    let mut total_secs: i64 = 0;
    let mut in_time = false;
    let mut num_buf = String::new();
    let mut saw_unit = false;

    for ch in s.chars() {
        if ch == 'T' {
            if in_time || !num_buf.is_empty() {
                return None;
            }
            in_time = true;
            continue;
        }
        if ch.is_ascii_digit() {
            num_buf.push(ch);
            continue;
        }
        let val: i64 = num_buf.parse().ok()?;
        num_buf.clear();
        let unit_secs = match (in_time, ch) {
            (false, 'W') => 7 * 86_400,
            (false, 'D') => 86_400,
            (true, 'H') => 3_600,
            (true, 'M') => 60,
            (true, 'S') => 1,
            _ => return None,
        };
        total_secs = total_secs.checked_add(val.checked_mul(unit_secs)?)?;
        saw_unit = true;
    }

    if !saw_unit || !num_buf.is_empty() {
        return None;
    }
    total_secs.checked_mul(sign)
}

fn parse_recurrence_dates(
    component: &impl Component,
    key: &str,
    aliases: &BTreeMap<String, chrono_tz::Tz>,
) -> Vec<DateTime<Utc>> {
    let mut out = Vec::new();
    if let Some(props) = component.multi_properties().get(key) {
        for property in props {
            for value in property.value().split(',') {
                let trimmed = value.trim();
                if trimmed.is_empty() {
                    continue;
                }
                let mut entry = Property::new(key, trimmed);
                for param in property.params().values() {
                    entry.append_parameter(param.clone());
                }
                if let Some(dpt) = DatePerhapsTime::from_property(&entry) {
                    out.push(date_perhaps_time_to_utc(&dpt, aliases));
                }
            }
        }
    }
    out.sort_unstable();
    out.dedup();
    out
}

fn parse_reminders(component: &impl Component) -> Vec<u32> {
    let mut reminders = Vec::new();
    for sub in component.components() {
        if !sub.component_kind().eq_ignore_ascii_case("VALARM") {
            continue;
        }
        let Some(trigger) = sub.properties().get("TRIGGER") else {
            continue;
        };
        if trigger
            .params()
            .get("RELATED")
            .is_some_and(|p| !p.value().eq_ignore_ascii_case("START"))
        {
            continue;
        }
        if let Some(secs) = parse_ics_duration_seconds(trigger.value()) {
            if secs <= 0 && secs % 60 == 0 {
                if let Ok(mins) = u32::try_from(-secs / 60) {
                    reminders.push(mins);
                }
            }
        }
    }
    reminders.sort_unstable();
    reminders.dedup();
    reminders
}

fn collect_preserved_props(component: &impl Component) -> Vec<RawProperty> {
    let known: HashSet<&str> = KNOWN_EVENT_PROPS.iter().copied().collect();
    let mut preserved = Vec::new();

    for (key, prop) in component.properties() {
        if known.contains(key.to_ascii_uppercase().as_str()) {
            continue;
        }
        preserved.push(RawProperty {
            key: prop.key().to_string(),
            value: prop.value().to_string(),
            params: prop
                .params()
                .values()
                .map(|p| (p.key().to_string(), p.value().to_string()))
                .collect(),
        });
    }

    for (key, props) in component.multi_properties() {
        if known.contains(key.to_ascii_uppercase().as_str()) {
            continue;
        }
        for prop in props {
            preserved.push(RawProperty {
                key: prop.key().to_string(),
                value: prop.value().to_string(),
                params: prop
                    .params()
                    .values()
                    .map(|p| (p.key().to_string(), p.value().to_string()))
                    .collect(),
            });
        }
    }

    preserved
}

fn parse_event_time(
    ics_event: &IcsEvent,
    aliases: &BTreeMap<String, chrono_tz::Tz>,
) -> Option<EventTime> {
    let start_prop = ics_event.get_start()?;
    let end_prop = ics_event.get_end();
    let duration_secs = ics_event
        .property_value("DURATION")
        .and_then(parse_ics_duration_seconds)
        .filter(|s| *s >= 0);

    match start_prop {
        DatePerhapsTime::Date(start_date) => {
            let end_date = match end_prop {
                Some(DatePerhapsTime::Date(end_d)) if end_d > start_date => end_d,
                Some(DatePerhapsTime::Date(_)) => start_date.succ_opt().unwrap_or(start_date),
                Some(DatePerhapsTime::DateTime(dt)) => {
                    let end_utc = date_perhaps_time_to_utc(&DatePerhapsTime::DateTime(dt), aliases);
                    let end_d = end_utc.date_naive();
                    if end_d > start_date {
                        end_d
                    } else {
                        start_date.succ_opt().unwrap_or(start_date)
                    }
                }
                None => {
                    let days = duration_secs
                        .map(|s| (s / 86_400).max(1))
                        .unwrap_or(1);
                    start_date + TimeDelta::days(days)
                }
            };
            Some(EventTime::AllDay {
                start: start_date,
                end: end_date,
            })
        }
        DatePerhapsTime::DateTime(CalendarDateTime::Floating(start_naive)) => {
            let end_naive = match end_prop {
                Some(DatePerhapsTime::DateTime(CalendarDateTime::Floating(end_n))) => {
                    end_n.max(start_naive)
                }
                Some(other) => {
                    let utc = date_perhaps_time_to_utc(&other, aliases);
                    utc.naive_utc().max(start_naive)
                }
                None => {
                    let delta = duration_secs
                        .map(TimeDelta::seconds)
                        .unwrap_or_else(TimeDelta::zero);
                    start_naive + delta
                }
            };
            Some(EventTime::Floating {
                start: start_naive,
                end: end_naive,
            })
        }
        DatePerhapsTime::DateTime(CalendarDateTime::Utc(start_utc)) => {
            let end_utc = match end_prop {
                Some(other) => date_perhaps_time_to_utc(&other, aliases).max(start_utc),
                None => {
                    let delta = duration_secs
                        .map(TimeDelta::seconds)
                        .unwrap_or_else(TimeDelta::zero);
                    start_utc + delta
                }
            };
            Some(EventTime::Zoned {
                start: start_utc,
                end: end_utc,
                tz: "UTC".to_string(),
            })
        }
        DatePerhapsTime::DateTime(CalendarDateTime::WithTimezone { date_time, tzid }) => {
            if let Some(zone) = resolve_tz(&tzid, aliases) {
                let start_utc = resolve_in_tz(date_time, zone);
                let end_utc = match end_prop {
                    Some(other) => date_perhaps_time_to_utc(&other, aliases).max(start_utc),
                    None => {
                        let delta = duration_secs
                            .map(TimeDelta::seconds)
                            .unwrap_or_else(TimeDelta::zero);
                        start_utc + delta
                    }
                };
                Some(EventTime::Zoned {
                    start: start_utc,
                    end: end_utc,
                    tz: zone.name().to_string(),
                })
            } else {
                // Unknown TZID: preserve wall-clock time gracefully as floating.
                let end_naive = match end_prop {
                    Some(DatePerhapsTime::DateTime(CalendarDateTime::WithTimezone {
                        date_time: end_dt,
                        ..
                    })) => end_dt.max(date_time),
                    Some(DatePerhapsTime::DateTime(CalendarDateTime::Floating(end_dt))) => {
                        end_dt.max(date_time)
                    }
                    _ => {
                        let delta = duration_secs
                            .map(TimeDelta::seconds)
                            .unwrap_or_else(TimeDelta::zero);
                        date_time + delta
                    }
                };
                Some(EventTime::Floating {
                    start: date_time,
                    end: end_naive,
                })
            }
        }
    }
}

/// Parses an RFC 5545 `.ics` document into local `Event` records belonging to `calendar_id`.
pub fn parse_ics(text: &str, calendar_id: &str) -> Result<ParsedIcs, String> {
    let calendar: IcsCalendar = text
        .parse()
        .map_err(|err: String| format!("Invalid .ics document: {err}"))?;

    let aliases = collect_vtimezone_aliases(&calendar.components);
    let now = Utc::now();
    let mut events = Vec::new();
    let mut cancellations: Vec<(String, DateTime<Utc>, u32)> = Vec::new();
    let mut skipped = 0usize;

    for component in &calendar.components {
        match component {
            CalendarComponent::Other(other)
                if other.component_kind().eq_ignore_ascii_case("VTIMEZONE") =>
            {
                continue;
            }
            CalendarComponent::Event(ics_event) => {
                if ics_event.get_status() == Some(EventStatus::Cancelled) {
                    if let (Some(uid), Some(rec_id)) = (
                        ics_event.get_uid(),
                        ics_event.get_recurrence_id(),
                    ) {
                        cancellations.push((
                            uid.to_string(),
                            date_perhaps_time_to_utc(&rec_id, &aliases),
                            ics_event.get_sequence().unwrap_or_default(),
                        ));
                    } else {
                        skipped += 1;
                    }
                    continue;
                }

                let Some(time) = parse_event_time(ics_event, &aliases) else {
                    skipped += 1;
                    continue;
                };

                let uid = ics_event
                    .get_uid()
                    .map(str::trim)
                    .filter(|s| !s.is_empty())
                    .map(str::to_string)
                    .unwrap_or_else(|| Uuid::new_v4().to_string());

                let summary = ics_event
                    .get_summary()
                    .map(str::trim)
                    .filter(|s| !s.is_empty())
                    .unwrap_or("Untitled Event")
                    .to_string();

                let description = ics_event
                    .get_description()
                    .map(str::to_string)
                    .filter(|s| !s.trim().is_empty());

                let location = ics_event
                    .get_location()
                    .map(str::trim)
                    .filter(|s| !s.is_empty())
                    .map(str::to_string);

                let recurrence_id = ics_event
                    .get_recurrence_id()
                    .map(|d| date_perhaps_time_to_utc(&d, &aliases));

                let rrule = ics_event
                    .property_value("RRULE")
                    .map(str::trim)
                    .filter(|s| !s.is_empty())
                    .map(str::to_string);

                let exdates = parse_recurrence_dates(ics_event, "EXDATE", &aliases);
                let rdates = parse_recurrence_dates(ics_event, "RDATE", &aliases);
                let reminders = parse_reminders(ics_event);
                let sequence = ics_event.get_sequence().unwrap_or_default();
                let created_at = ics_event.get_created().unwrap_or(now);
                let updated_at = ics_event.get_last_modified().unwrap_or(now);
                let preserved_props = collect_preserved_props(ics_event);

                events.push(Event {
                    uid,
                    calendar_id: calendar_id.to_string(),
                    summary,
                    description,
                    location,
                    time,
                    rrule,
                    exdates,
                    rdates,
                    recurrence_id,
                    reminders,
                    sequence,
                    created_at,
                    updated_at,
                    preserved_props,
                });
            }
            _ => {
                skipped += 1;
            }
        }
    }

    // Reconcile STATUS:CANCELLED recurrence instances into parent EXDATEs.
    for (uid, occ, seq) in cancellations {
        let superseded = events.iter().any(|ev| {
            ev.uid == uid && ev.recurrence_id == Some(occ) && ev.sequence > seq
        });
        if superseded {
            continue;
        }
        if let Some(master) = events
            .iter_mut()
            .find(|ev| ev.uid == uid && ev.recurrence_id.is_none() && ev.is_recurring())
        {
            master.add_exdate(occ);
            events.retain(|ev| !(ev.uid == uid && ev.recurrence_id == Some(occ)));
        } else {
            skipped += 1;
        }
    }

    // Ensure master series records precede their RECURRENCE-ID overrides.
    events.sort_by_key(|ev| ev.recurrence_id.is_some());

    Ok(ParsedIcs { events, skipped })
}

fn export_instant_for_event(dt: DateTime<Utc>, event: &Event) -> DatePerhapsTime {
    match &event.time {
        EventTime::AllDay { .. } => DatePerhapsTime::from(dt.date_naive()),
        EventTime::Floating { .. } => DatePerhapsTime::from(CalendarDateTime::Floating(dt.naive_utc())),
        EventTime::Zoned { tz, .. } => {
            if tz.eq_ignore_ascii_case("UTC") {
                DatePerhapsTime::from(CalendarDateTime::Utc(dt))
            } else if let Ok(zone) = tz.parse::<chrono_tz::Tz>() {
                DatePerhapsTime::from(CalendarDateTime::WithTimezone {
                    date_time: dt.with_timezone(&zone).naive_local(),
                    tzid: zone.name().to_string(),
                })
            } else {
                DatePerhapsTime::from(CalendarDateTime::Utc(dt))
            }
        }
    }
}

fn format_rrule_for_export(rule: &str, time: &EventTime) -> String {
    rule.split(';')
        .map(|field| {
            let Some(until) = field.strip_prefix("UNTIL=") else {
                return field.to_string();
            };
            match time {
                EventTime::AllDay { .. } => {
                    if let Some(date_part) = until.get(..8) {
                        format!("UNTIL={date_part}")
                    } else {
                        field.to_string()
                    }
                }
                EventTime::Floating { .. } => {
                    format!("UNTIL={}", until.trim_end_matches('Z'))
                }
                EventTime::Zoned { .. } => {
                    if until.len() == 8 {
                        format!("UNTIL={until}T235959Z")
                    } else if until.len() == 15 && !until.ends_with('Z') {
                        format!("UNTIL={until}Z")
                    } else {
                        field.to_string()
                    }
                }
            }
        })
        .collect::<Vec<_>>()
        .join(";")
}

/// Serializes a calendar's events into a clean, standards-compliant RFC 5545 `.ics` string.
pub fn serialize_calendar(calendar_name: &str, events: &[Event]) -> String {
    let mut cal = IcsCalendar::new();
    cal.name(calendar_name);

    for event in events {
        let mut ics_event = IcsEvent::new();
        ics_event.uid(&event.uid);
        ics_event.summary(&event.summary);
        if let Some(desc) = &event.description {
            if !desc.trim().is_empty() {
                ics_event.description(desc);
            }
        }
        if let Some(loc) = &event.location {
            if !loc.trim().is_empty() {
                ics_event.location(loc);
            }
        }
        ics_event.sequence(event.sequence);

        match &event.time {
            EventTime::AllDay { start, end } => {
                let effective_end = if *end > *start {
                    *end
                } else {
                    start.succ_opt().unwrap_or(*start)
                };
                ics_event.starts(*start);
                ics_event.ends(effective_end);
            }
            EventTime::Floating { start, end } => {
                ics_event.starts(CalendarDateTime::Floating(*start));
                if end > start {
                    ics_event.ends(CalendarDateTime::Floating(*end));
                }
            }
            EventTime::Zoned { start, end, tz } => {
                if tz.eq_ignore_ascii_case("UTC") {
                    ics_event.starts(CalendarDateTime::Utc(*start));
                    if end > start {
                        ics_event.ends(CalendarDateTime::Utc(*end));
                    }
                } else if let Ok(zone) = tz.parse::<chrono_tz::Tz>() {
                    ics_event.starts(CalendarDateTime::WithTimezone {
                        date_time: start.with_timezone(&zone).naive_local(),
                        tzid: zone.name().to_string(),
                    });
                    if end > start {
                        ics_event.ends(CalendarDateTime::WithTimezone {
                            date_time: end.with_timezone(&zone).naive_local(),
                            tzid: zone.name().to_string(),
                        });
                    }
                } else {
                    ics_event.starts(CalendarDateTime::Utc(*start));
                    if end > start {
                        ics_event.ends(CalendarDateTime::Utc(*end));
                    }
                }
            }
        }

        if let Some(rrule) = &event.rrule {
            if !rrule.trim().is_empty() {
                ics_event.add_property("RRULE", format_rrule_for_export(rrule, &event.time));
            }
        }

        for &exdate in &event.exdates {
            let prop = export_instant_for_event(exdate, event).to_property("EXDATE");
            ics_event.append_multi_property(prop);
        }

        for &rdate in &event.rdates {
            let prop = export_instant_for_event(rdate, event).to_property("RDATE");
            ics_event.append_multi_property(prop);
        }

        if let Some(rec_id) = event.recurrence_id {
            ics_event.recurrence_id(export_instant_for_event(rec_id, event));
        }

        for &minutes in &event.reminders {
            let neg_minutes = -i64::from(minutes);
            if let Some(delta) = TimeDelta::try_minutes(neg_minutes) {
                ics_event.alarm(Alarm::display(&event.summary, delta));
            }
        }

        for raw in &event.preserved_props {
            let mut prop = Property::new(&raw.key, &raw.value);
            for (pk, pv) in &raw.params {
                prop.append_parameter(Parameter::new(pk, pv));
            }
            ics_event.append_multi_property(prop);
        }

        cal.push(ics_event.done());
    }

    // Strip redundant UID/DTSTAMP lines that `icalendar` attaches inside VALARM blocks.
    let serialized = cal.done().to_string();
    let mut cleaned = String::with_capacity(serialized.len());
    let mut in_alarm = false;
    for line in serialized.split_inclusive("\r\n") {
        match line.trim_end() {
            "BEGIN:VALARM" => in_alarm = true,
            "END:VALARM" => in_alarm = false,
            _ if in_alarm && (line.starts_with("DTSTAMP:") || line.starts_with("UID:")) => {
                continue;
            }
            _ => {}
        }
        cleaned.push_str(line);
    }
    cleaned
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Timelike;

    #[test]
    fn parses_rfc5545_durations() {
        assert_eq!(parse_ics_duration_seconds("-PT15M"), Some(-900));
        assert_eq!(parse_ics_duration_seconds("PT1H30M"), Some(5400));
        assert_eq!(parse_ics_duration_seconds("-P1D"), Some(-86400));
        assert_eq!(parse_ics_duration_seconds("P2W"), Some(14 * 86400));
        assert_eq!(parse_ics_duration_seconds("PT0S"), Some(0));
        assert_eq!(parse_ics_duration_seconds("invalid"), None);
    }

    #[test]
    fn round_trips_zoned_event_with_rrule_exdate_alarm_and_x_prop() {
        let start = Utc.with_ymd_and_hms(2026, 11, 26, 14, 0, 0).unwrap();
        let end = Utc.with_ymd_and_hms(2026, 11, 26, 15, 30, 0).unwrap();
        let ex = Utc.with_ymd_and_hms(2026, 12, 3, 14, 0, 0).unwrap();

        let original = Event {
            uid: "demo-uid-123".into(),
            calendar_id: "cal-1".into(),
            summary: "Architecture Review".into(),
            description: Some("Discuss Wayland layout".into()),
            location: Some("Room 3B".into()),
            time: EventTime::Zoned {
                start,
                end,
                tz: "America/New_York".into(),
            },
            rrule: Some("FREQ=WEEKLY;COUNT=4".into()),
            exdates: vec![ex],
            rdates: Vec::new(),
            recurrence_id: None,
            reminders: vec![15, 60],
            sequence: 2,
            created_at: start,
            updated_at: start,
            preserved_props: vec![RawProperty {
                key: "X-DUSKY-TAG".into(),
                value: "engineering".into(),
                params: Vec::new(),
            }],
        };

        let ics_text = serialize_calendar("Work", std::slice::from_ref(&original));
        let parsed = parse_ics(&ics_text, "cal-1").unwrap();
        assert_eq!(parsed.skipped, 0);
        assert_eq!(parsed.events.len(), 1);

        let rt = &parsed.events[0];
        assert_eq!(rt.uid, original.uid);
        assert_eq!(rt.summary, original.summary);
        assert_eq!(rt.description, original.description);
        assert_eq!(rt.location, original.location);
        assert_eq!(rt.rrule, original.rrule);
        assert_eq!(rt.exdates, vec![ex]);
        assert_eq!(rt.reminders, vec![15, 60]);
        assert_eq!(rt.sequence, 2);
        assert!(rt
            .preserved_props
            .iter()
            .any(|p| p.key == "X-DUSKY-TAG" && p.value == "engineering"));

        match &rt.time {
            EventTime::Zoned {
                start: s,
                end: e,
                tz,
            } => {
                assert_eq!(*s, start);
                assert_eq!(*e, end);
                assert_eq!(tz, "America/New_York");
            }
            other => panic!("expected Zoned, got {other:?}"),
        }
    }

    #[test]
    fn imports_windows_timezone_and_duration_fallback() {
        let sample = "\
BEGIN:VCALENDAR\r\n\
VERSION:2.0\r\n\
PRODID:-//Test//Test//EN\r\n\
BEGIN:VEVENT\r\n\
UID:win-tz-1\r\n\
SUMMARY:IST Sync\r\n\
DTSTART;TZID=India Standard Time:20261126T100000\r\n\
DURATION:PT45M\r\n\
END:VEVENT\r\n\
END:VCALENDAR\r\n";

        let parsed = parse_ics(sample, "cal-1").unwrap();
        assert_eq!(parsed.events.len(), 1);
        match &parsed.events[0].time {
            EventTime::Zoned { start, end, tz } => {
                assert_eq!(tz, "Asia/Kolkata");
                assert_eq!(*end - *start, TimeDelta::minutes(45));
                assert_eq!(start.hour(), 4);
                assert_eq!(start.minute(), 30);
            }
            other => panic!("expected Zoned Asia/Kolkata, got {other:?}"),
        }
    }

    #[test]
    fn round_trips_all_day_event_and_until_date() {
        let mut ev = Event::new_all_day(
            "cal-1",
            "Holiday",
            NaiveDate::from_ymd_opt(2026, 12, 24).unwrap(),
            NaiveDate::from_ymd_opt(2026, 12, 27).unwrap(),
        );
        ev.rrule = Some("FREQ=YEARLY;UNTIL=20301231T235959Z".into());

        let ics_text = serialize_calendar("Personal", &[ev.clone()]);
        assert!(ics_text.contains("UNTIL=20301231"));

        let parsed = parse_ics(&ics_text, "cal-1").unwrap();
        assert_eq!(parsed.events.len(), 1);
        assert_eq!(parsed.events[0].time, ev.time);
    }
}
