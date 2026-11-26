//! RFC 5545 recurrence rule builder and bounded DST-aware occurrence expansion.

use crate::model::event::{
    Event, EventOccurrence, EventTime, inclusive_dates_for_timed, resolve_local_datetime,
};
use chrono::{DateTime, Datelike, Local, NaiveDate, TimeDelta, Utc};
use rrule::{Frequency, NWeekday, RRule, RRuleSet, Tz, Unvalidated};
use std::borrow::Cow;
use std::collections::HashSet;

/// Upper bound per event query to guard against pathological/unbounded RRULEs.
const MAX_OCCURRENCES_PER_EVENT: u16 = 512;

/// Maximum lookback buffer for multi-day recurring events starting before the visible window.
const MAX_LOOKBACK_DAYS: i64 = 366;

const RRULE_WEEKDAY_CODES: [&str; 7] = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"];
const WEEKDAY_LABELS: [&str; 7] = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RecurrenceFreq {
    None,
    Daily,
    Weekly,
    Monthly,
    Yearly,
}

impl RecurrenceFreq {
    pub const ALL: [Self; 5] = [
        Self::None,
        Self::Daily,
        Self::Weekly,
        Self::Monthly,
        Self::Yearly,
    ];

    pub fn label(self) -> &'static str {
        match self {
            Self::None => "Does not repeat",
            Self::Daily => "Daily",
            Self::Weekly => "Weekly",
            Self::Monthly => "Monthly",
            Self::Yearly => "Yearly",
        }
    }

    pub fn rrule_code(self) -> Option<&'static str> {
        match self {
            Self::None => None,
            Self::Daily => Some("DAILY"),
            Self::Weekly => Some("WEEKLY"),
            Self::Monthly => Some("MONTHLY"),
            Self::Yearly => Some("YEARLY"),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RecurrenceEnd {
    Never,
    Until(NaiveDate),
    Count(u32),
}

/// Editable structured representation of an RFC 5545 `RRULE`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RecurrenceRule {
    pub freq: RecurrenceFreq,
    pub interval: u16,
    /// Monday-first (`by_weekday[0]` = Monday). Only emitted for `Weekly`.
    pub by_weekday: [bool; 7],
    pub end: RecurrenceEnd,
}

impl Default for RecurrenceRule {
    fn default() -> Self {
        Self::NONE
    }
}

impl RecurrenceRule {
    pub const NONE: Self = Self {
        freq: RecurrenceFreq::None,
        interval: 1,
        by_weekday: [false; 7],
        end: RecurrenceEnd::Never,
    };

    pub fn default_for_date(freq: RecurrenceFreq, start_date: NaiveDate) -> Self {
        let mut by_weekday = [false; 7];
        if freq == RecurrenceFreq::Weekly {
            by_weekday[start_date.weekday().num_days_from_monday() as usize] = true;
        }
        Self {
            freq,
            interval: 1,
            by_weekday,
            end: RecurrenceEnd::Never,
        }
    }

    pub fn to_rrule_string(&self) -> Option<String> {
        let freq_code = self.freq.rrule_code()?;
        let mut parts = vec![format!("FREQ={freq_code}")];

        let interval = self.interval.max(1);
        if interval > 1 {
            parts.push(format!("INTERVAL={interval}"));
        }

        if self.freq == RecurrenceFreq::Weekly {
            let days: Vec<&str> = RRULE_WEEKDAY_CODES
                .iter()
                .zip(self.by_weekday.iter())
                .filter_map(|(code, on)| on.then_some(*code))
                .collect();
            if !days.is_empty() {
                parts.push(format!("BYDAY={}", days.join(",")));
            }
        }

        match self.end {
            RecurrenceEnd::Never => {}
            RecurrenceEnd::Count(count) if count > 0 => {
                parts.push(format!("COUNT={count}"));
            }
            RecurrenceEnd::Count(_) => {}
            RecurrenceEnd::Until(date) => {
                parts.push(format!(
                    "UNTIL={:04}{:02}{:02}T235959Z",
                    date.year(),
                    date.month(),
                    date.day()
                ));
            }
        }

        Some(parts.join(";"))
    }

    pub fn from_rrule_string(rrule: Option<&str>) -> Self {
        let Some(raw) = rrule else {
            return Self::NONE;
        };
        let normalized = normalize_until_for_rrule(raw);
        let Ok(rule) = normalized.parse::<RRule<Unvalidated>>() else {
            return Self::NONE;
        };

        let freq = match rule.get_freq() {
            Frequency::Daily => RecurrenceFreq::Daily,
            Frequency::Weekly => RecurrenceFreq::Weekly,
            Frequency::Monthly => RecurrenceFreq::Monthly,
            Frequency::Yearly => RecurrenceFreq::Yearly,
            _ => RecurrenceFreq::None,
        };

        let mut by_weekday = [false; 7];
        for nweekday in rule.get_by_weekday() {
            if let NWeekday::Every(weekday) = nweekday {
                by_weekday[weekday.num_days_from_monday() as usize] = true;
            }
        }

        let end = if let Some(count) = rule.get_count() {
            RecurrenceEnd::Count(count)
        } else if let Some(until) = rule.get_until() {
            RecurrenceEnd::Until(until.naive_utc().date())
        } else {
            RecurrenceEnd::Never
        };

        Self {
            freq,
            interval: rule.get_interval().max(1),
            by_weekday,
            end,
        }
    }

    pub fn human_summary(&self) -> String {
        if self.freq == RecurrenceFreq::None {
            return "Does not repeat".to_string();
        }

        let interval = self.interval.max(1);
        let mut out = match (self.freq, interval) {
            (RecurrenceFreq::Daily, 1) => "Daily".to_string(),
            (RecurrenceFreq::Daily, n) => format!("Every {n} days"),
            (RecurrenceFreq::Weekly, 1) => "Weekly".to_string(),
            (RecurrenceFreq::Weekly, n) => format!("Every {n} weeks"),
            (RecurrenceFreq::Monthly, 1) => "Monthly".to_string(),
            (RecurrenceFreq::Monthly, n) => format!("Every {n} months"),
            (RecurrenceFreq::Yearly, 1) => "Yearly".to_string(),
            (RecurrenceFreq::Yearly, n) => format!("Every {n} years"),
            (RecurrenceFreq::None, _) => unreachable!(),
        };

        if self.freq == RecurrenceFreq::Weekly {
            let active_days: Vec<&str> = WEEKDAY_LABELS
                .iter()
                .zip(self.by_weekday.iter())
                .filter_map(|(label, on)| on.then_some(*label))
                .collect();
            if !active_days.is_empty() && active_days.len() < 7 {
                out.push_str(" on ");
                out.push_str(&active_days.join(", "));
            }
        }

        match self.end {
            RecurrenceEnd::Never => {}
            RecurrenceEnd::Count(1) => out.push_str(", 1 time"),
            RecurrenceEnd::Count(n) => out.push_str(&format!(", {n} times")),
            RecurrenceEnd::Until(date) => {
                out.push_str(&format!(", until {}", date.format("%b %-d, %Y")));
            }
        }

        out
    }
}

/// Normalize RFC 5545 `UNTIL=YYYYMMDD` or floating `UNTIL=YYYYMMDDTHHMMSS` into UTC form
/// accepted cleanly by `rrule` 0.14.
fn normalize_until_for_rrule(rule: &str) -> Cow<'_, str> {
    let needs_fix = rule.split(';').any(|field| {
        matches!(
            field.strip_prefix("UNTIL="),
            Some(v) if v.len() == 8 || (v.len() == 15 && !v.ends_with('Z'))
        )
    });
    if !needs_fix {
        return Cow::Borrowed(rule);
    }

    let fixed = rule
        .split(';')
        .map(|field| match field.strip_prefix("UNTIL=") {
            Some(v) if v.len() == 8 => format!("UNTIL={v}T235959Z"),
            Some(v) if v.len() == 15 && !v.ends_with('Z') => format!("UNTIL={v}Z"),
            _ => field.to_string(),
        })
        .collect::<Vec<_>>()
        .join(";");
    Cow::Owned(fixed)
}

fn overlaps_window(
    occ_start: DateTime<Local>,
    occ_end: DateTime<Local>,
    range_start: DateTime<Local>,
    range_end: DateTime<Local>,
) -> bool {
    if occ_end > occ_start {
        occ_start < range_end && occ_end > range_start
    } else {
        occ_start >= range_start && occ_start < range_end
    }
}

fn is_exdate(event: &Event, anchor: DateTime<Utc>) -> bool {
    match event.time {
        EventTime::AllDay { .. } => {
            let target = anchor.date_naive();
            event.exdates.iter().any(|d| d.date_naive() == target)
        }
        EventTime::Floating { .. } | EventTime::Zoned { .. } => {
            let ts = anchor.timestamp();
            event.exdates.iter().any(|d| d.timestamp() == ts)
        }
    }
}

fn materialize_single(event: &Event, occurrence_id: DateTime<Utc>, recurring: bool) -> EventOccurrence {
    let start_local = event.time.start_local();
    let end_local = event.time.end_local();
    let (start_date, end_date) = event.time.inclusive_day_span_local();
    EventOccurrence {
        event_key: event.key(),
        calendar_id: event.calendar_id.clone(),
        occurrence_id,
        is_recurring_instance: recurring,
        is_all_day: event.time.is_all_day(),
        start_local,
        end_local,
        start_date,
        end_date,
        summary: event.summary.clone(),
        location: event.location.clone(),
        description: event.description.clone(),
        reminders: event.reminders.clone(),
        rrule: event.rrule.clone(),
    }
}

fn materialize_shifted(event: &Event, anchor_utc: DateTime<Utc>) -> EventOccurrence {
    match &event.time {
        EventTime::AllDay { start, end } => {
            let span_days = (*end - *start).num_days().max(1);
            let occ_start_date = anchor_utc.date_naive();
            let occ_end_exclusive = occ_start_date + TimeDelta::days(span_days);
            let occ_end_inclusive = occ_end_exclusive.pred_opt().unwrap_or(occ_start_date);
            let start_local = resolve_local_datetime(
                occ_start_date.and_hms_opt(0, 0, 0).expect("midnight"),
            );
            let end_local = resolve_local_datetime(
                occ_end_exclusive.and_hms_opt(0, 0, 0).expect("midnight"),
            );
            EventOccurrence {
                event_key: event.key(),
                calendar_id: event.calendar_id.clone(),
                occurrence_id: occ_start_date
                    .and_hms_opt(0, 0, 0)
                    .expect("midnight")
                    .and_utc(),
                is_recurring_instance: true,
                is_all_day: true,
                start_local,
                end_local,
                start_date: occ_start_date,
                end_date: occ_end_inclusive.max(occ_start_date),
                summary: event.summary.clone(),
                location: event.location.clone(),
                description: event.description.clone(),
                reminders: event.reminders.clone(),
                rrule: event.rrule.clone(),
            }
        }
        EventTime::Floating { start, end } => {
            let duration = (*end - *start).max(TimeDelta::zero());
            let occ_start_naive = anchor_utc.naive_utc();
            let occ_end_naive = occ_start_naive + duration;
            let start_local = resolve_local_datetime(occ_start_naive);
            let end_local = resolve_local_datetime(occ_end_naive).max(start_local);
            let (start_date, end_date) = inclusive_dates_for_timed(start_local, end_local);
            EventOccurrence {
                event_key: event.key(),
                calendar_id: event.calendar_id.clone(),
                occurrence_id: anchor_utc,
                is_recurring_instance: true,
                is_all_day: false,
                start_local,
                end_local,
                start_date,
                end_date,
                summary: event.summary.clone(),
                location: event.location.clone(),
                description: event.description.clone(),
                reminders: event.reminders.clone(),
                rrule: event.rrule.clone(),
            }
        }
        EventTime::Zoned { start, end, .. } => {
            let duration = (*end - *start).max(TimeDelta::zero());
            let occ_end_utc = anchor_utc + duration;
            let start_local = anchor_utc.with_timezone(&Local);
            let end_local = occ_end_utc.with_timezone(&Local);
            let (start_date, end_date) = inclusive_dates_for_timed(start_local, end_local);
            EventOccurrence {
                event_key: event.key(),
                calendar_id: event.calendar_id.clone(),
                occurrence_id: anchor_utc,
                is_recurring_instance: true,
                is_all_day: false,
                start_local,
                end_local,
                start_date,
                end_date,
                summary: event.summary.clone(),
                location: event.location.clone(),
                description: event.description.clone(),
                reminders: event.reminders.clone(),
                rrule: event.rrule.clone(),
            }
        }
    }
}

fn expand_recurring_anchors(
    event: &Event,
    expand_start_utc: DateTime<Utc>,
    expand_end_utc: DateTime<Utc>,
) -> Vec<DateTime<Utc>> {
    if expand_end_utc <= expand_start_utc {
        return Vec::new();
    }

    let rrule_tz = match &event.time {
        EventTime::Zoned { tz, .. } => tz
            .parse::<chrono_tz::Tz>()
            .ok()
            .map(Tz::from)
            .unwrap_or(Tz::UTC),
        EventTime::AllDay { .. } | EventTime::Floating { .. } => Tz::UTC,
    };

    let dt_start_utc = event.start_anchor_utc();
    let dt_start = dt_start_utc.with_timezone(&rrule_tz);
    let after = expand_start_utc.with_timezone(&rrule_tz);
    let before = expand_end_utc.with_timezone(&rrule_tz);

    let mut anchors: Vec<DateTime<Utc>> = event
        .rdates
        .iter()
        .copied()
        .filter(|d| *d >= expand_start_utc && *d <= expand_end_utc)
        .collect();

    if (event.rrule.is_none() || !event.rdates.is_empty())
        && dt_start_utc >= expand_start_utc
        && dt_start_utc <= expand_end_utc
    {
        anchors.push(dt_start_utc);
    }

    if let Some(rule_str) = event.rrule.as_deref() {
        let normalized = normalize_until_for_rrule(rule_str);
        if let Ok(validated) = normalized
            .parse::<RRule<Unvalidated>>()
            .and_then(|rule| rule.validate(dt_start))
        {
            let set = event
                .exdates
                .iter()
                .fold(RRuleSet::new(dt_start).rrule(validated), |set, ex| {
                    set.exdate(ex.with_timezone(&rrule_tz))
                });
            let result = set
                .after(after)
                .before(before)
                .all(MAX_OCCURRENCES_PER_EVENT);
            anchors.extend(result.dates.into_iter().map(|d| d.with_timezone(&Utc)));
        } else if dt_start_utc >= expand_start_utc && dt_start_utc <= expand_end_utc {
            // Fallback to the event's base start if an imported RRULE is malformed.
            anchors.push(dt_start_utc);
        }
    }

    anchors.retain(|a| !is_exdate(event, *a));
    anchors.sort_unstable();
    anchors.dedup();
    anchors
}

/// Expands all provided events (including recurring series, `EXDATE`s, `RDATE`s, and
/// `RECURRENCE-ID` single-occurrence overrides) into sorted local occurrences overlapping
/// `[range_start, range_end)`.
pub fn expand_events_in_range(
    events: &[Event],
    range_start: DateTime<Local>,
    range_end: DateTime<Local>,
) -> Vec<EventOccurrence> {
    if range_end <= range_start || events.is_empty() {
        return Vec::new();
    }

    let mut overridden_timestamps: HashSet<(&str, i64)> = HashSet::new();
    let mut overridden_dates: HashSet<(&str, NaiveDate)> = HashSet::new();
    for event in events {
        if let Some(rec_id) = event.recurrence_id {
            overridden_timestamps.insert((event.uid.as_str(), rec_id.timestamp()));
            overridden_dates.insert((event.uid.as_str(), rec_id.date_naive()));
        }
    }

    let mut occurrences = Vec::new();

    for event in events {
        if let Some(rec_id) = event.recurrence_id {
            let occ = materialize_single(event, rec_id, true);
            if overlaps_window(occ.start_local, occ.end_local, range_start, range_end) {
                occurrences.push(occ);
            }
            continue;
        }

        if !event.is_recurring() {
            let occ = materialize_single(event, event.start_anchor_utc(), false);
            if overlaps_window(occ.start_local, occ.end_local, range_start, range_end) {
                occurrences.push(occ);
            }
            continue;
        }

        let lookback = event
            .time
            .duration()
            .clamp(TimeDelta::zero(), TimeDelta::days(MAX_LOOKBACK_DAYS));
        // Add 1 day of timezone slack on both ends so floating/all-day wall-clock anchors never clip.
        let slack = TimeDelta::days(1);
        let expand_start_utc = (range_start - lookback - slack).with_timezone(&Utc);
        let expand_end_utc = (range_end + slack).with_timezone(&Utc);

        let anchors = expand_recurring_anchors(event, expand_start_utc, expand_end_utc);
        for anchor in anchors {
            let overridden = match event.time {
                EventTime::AllDay { .. } => {
                    overridden_dates.contains(&(event.uid.as_str(), anchor.date_naive()))
                }
                EventTime::Floating { .. } | EventTime::Zoned { .. } => {
                    overridden_timestamps.contains(&(event.uid.as_str(), anchor.timestamp()))
                }
            };
            if overridden {
                continue;
            }

            let occ = materialize_shifted(event, anchor);
            if overlaps_window(occ.start_local, occ.end_local, range_start, range_end) {
                occurrences.push(occ);
            }
        }
    }

    occurrences.sort_by(|a, b| {
        a.start_local
            .cmp(&b.start_local)
            .then_with(|| b.is_all_day.cmp(&a.is_all_day))
            .then_with(|| a.end_local.cmp(&b.end_local))
            .then_with(|| a.summary.cmp(&b.summary))
            .then_with(|| a.event_key.uid.cmp(&b.event_key.uid))
    });

    occurrences
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;

    #[test]
    fn recurrence_rule_round_trips_and_summarizes() {
        let mut by_weekday = [false; 7];
        by_weekday[1] = true; // Tue
        by_weekday[3] = true; // Thu

        let rule = RecurrenceRule {
            freq: RecurrenceFreq::Weekly,
            interval: 2,
            by_weekday,
            end: RecurrenceEnd::Count(6),
        };
        let encoded = rule.to_rrule_string().unwrap();
        assert_eq!(encoded, "FREQ=WEEKLY;INTERVAL=2;BYDAY=TU,TH;COUNT=6");

        let parsed = RecurrenceRule::from_rrule_string(Some(&encoded));
        assert_eq!(parsed, rule);
        assert_eq!(parsed.human_summary(), "Every 2 weeks on Tue, Thu, 6 times");

        let until_date = NaiveDate::from_ymd_opt(2026, 12, 31).unwrap();
        let monthly = RecurrenceRule {
            freq: RecurrenceFreq::Monthly,
            interval: 1,
            by_weekday: [false; 7],
            end: RecurrenceEnd::Until(until_date),
        };
        let encoded_m = monthly.to_rrule_string().unwrap();
        assert_eq!(encoded_m, "FREQ=MONTHLY;UNTIL=20261231T235959Z");
        assert_eq!(RecurrenceRule::from_rrule_string(Some(&encoded_m)), monthly);
    }

    #[test]
    fn expands_recurring_series_with_exdate_and_override() {
        let start_date = NaiveDate::from_ymd_opt(2026, 11, 2).unwrap();
        let mut master = Event::new_all_day(
            "cal-1",
            "Standup",
            start_date,
            NaiveDate::from_ymd_opt(2026, 11, 3).unwrap(),
        );
        master.uid = "series-1".to_string();
        master.rrule = Some("FREQ=DAILY;COUNT=5".to_string());
        // Exclude Nov 4
        master.add_exdate(
            NaiveDate::from_ymd_opt(2026, 11, 4)
                .unwrap()
                .and_hms_opt(0, 0, 0)
                .unwrap()
                .and_utc(),
        );

        // Override Nov 5 with a renamed event moved to Nov 5
        let mut override_ev = Event::new_all_day(
            "cal-1",
            "Standup (Retro)",
            NaiveDate::from_ymd_opt(2026, 11, 5).unwrap(),
            NaiveDate::from_ymd_opt(2026, 11, 6).unwrap(),
        );
        override_ev.uid = "series-1".to_string();
        override_ev.recurrence_id = Some(
            NaiveDate::from_ymd_opt(2026, 11, 5)
                .unwrap()
                .and_hms_opt(0, 0, 0)
                .unwrap()
                .and_utc(),
        );

        let range_start = resolve_local_datetime(
            NaiveDate::from_ymd_opt(2026, 11, 1)
                .unwrap()
                .and_hms_opt(0, 0, 0)
                .unwrap(),
        );
        let range_end = resolve_local_datetime(
            NaiveDate::from_ymd_opt(2026, 11, 10)
                .unwrap()
                .and_hms_opt(0, 0, 0)
                .unwrap(),
        );

        let occurrences = expand_events_in_range(&[master, override_ev], range_start, range_end);
        let dates_and_titles: Vec<(NaiveDate, String)> = occurrences
            .iter()
            .map(|o| (o.start_date, o.summary.clone()))
            .collect();

        assert_eq!(
            dates_and_titles,
            vec![
                (NaiveDate::from_ymd_opt(2026, 11, 2).unwrap(), "Standup".into()),
                (NaiveDate::from_ymd_opt(2026, 11, 3).unwrap(), "Standup".into()),
                (
                    NaiveDate::from_ymd_opt(2026, 11, 5).unwrap(),
                    "Standup (Retro)".into()
                ),
                (NaiveDate::from_ymd_opt(2026, 11, 6).unwrap(), "Standup".into()),
            ]
        );
    }

    #[test]
    fn preserves_zoned_wall_clock_across_dst_transition() {
        // America/New_York springs forward on 2026-03-08 (EST -05:00 -> EDT -04:00).
        let ny: chrono_tz::Tz = "America/New_York".parse().unwrap();
        let start_ny = ny.with_ymd_and_hms(2026, 3, 7, 9, 0, 0).unwrap();
        let end_ny = ny.with_ymd_and_hms(2026, 3, 7, 10, 0, 0).unwrap();

        let mut ev = Event::new_all_day(
            "cal-1",
            "Morning Sync",
            NaiveDate::from_ymd_opt(2026, 3, 7).unwrap(),
            NaiveDate::from_ymd_opt(2026, 3, 8).unwrap(),
        );
        ev.time = EventTime::Zoned {
            start: start_ny.with_timezone(&Utc),
            end: end_ny.with_timezone(&Utc),
            tz: "America/New_York".to_string(),
        };
        ev.rrule = Some("FREQ=DAILY;COUNT=3".to_string());

        let range_start = resolve_local_datetime(
            NaiveDate::from_ymd_opt(2026, 3, 6)
                .unwrap()
                .and_hms_opt(0, 0, 0)
                .unwrap(),
        );
        let range_end = resolve_local_datetime(
            NaiveDate::from_ymd_opt(2026, 3, 12)
                .unwrap()
                .and_hms_opt(0, 0, 0)
                .unwrap(),
        );

        let occurrences = expand_events_in_range(&[ev], range_start, range_end);
        assert_eq!(occurrences.len(), 3);
        for occ in occurrences {
            let in_ny = occ.start_local.with_timezone(&ny);
            assert_eq!(in_ny.format("%H:%M").to_string(), "09:00");
        }
    }

    #[test]
    fn multi_day_recurring_event_starting_before_window_is_included() {
        let mut ev = Event::new_all_day(
            "cal-1",
            "Sprint",
            NaiveDate::from_ymd_opt(2026, 10, 29).unwrap(),
            NaiveDate::from_ymd_opt(2026, 11, 3).unwrap(), // 5-day span: Oct 29 .. Nov 2 inclusive
        );
        ev.rrule = Some("FREQ=WEEKLY;COUNT=2".to_string());

        let range_start = resolve_local_datetime(
            NaiveDate::from_ymd_opt(2026, 11, 1)
                .unwrap()
                .and_hms_opt(0, 0, 0)
                .unwrap(),
        );
        let range_end = resolve_local_datetime(
            NaiveDate::from_ymd_opt(2026, 11, 4)
                .unwrap()
                .and_hms_opt(0, 0, 0)
                .unwrap(),
        );

        let occurrences = expand_events_in_range(&[ev], range_start, range_end);
        assert_eq!(occurrences.len(), 1);
        assert_eq!(
            occurrences[0].start_date,
            NaiveDate::from_ymd_opt(2026, 10, 29).unwrap()
        );
        assert_eq!(
            occurrences[0].end_date,
            NaiveDate::from_ymd_opt(2026, 11, 2).unwrap()
        );
    }
}
