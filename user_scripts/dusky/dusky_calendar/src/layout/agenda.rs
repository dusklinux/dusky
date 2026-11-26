//! Chronological Agenda view layout builder with day grouping and live search filtering.

use crate::model::EventOccurrence;
use chrono::{NaiveDate, TimeDelta};
use std::collections::BTreeMap;

#[derive(Debug, Clone)]
pub struct AgendaDayGroup {
    pub date: NaiveDate,
    pub is_today: bool,
    pub is_tomorrow: bool,
    pub occurrences: Vec<EventOccurrence>,
}

#[derive(Debug, Clone)]
pub struct AgendaLayout {
    pub range_start: NaiveDate,
    pub range_end: NaiveDate,
    pub groups: Vec<AgendaDayGroup>,
    pub total_events: usize,
}

fn occurrence_matches_query(occ: &EventOccurrence, needle_lower: &str) -> bool {
    if needle_lower.is_empty() {
        return true;
    }
    if occ.summary.to_lowercase().contains(needle_lower) {
        return true;
    }
    if let Some(loc) = &occ.location {
        if loc.to_lowercase().contains(needle_lower) {
            return true;
        }
    }
    if let Some(desc) = &occ.description {
        if desc.to_lowercase().contains(needle_lower) {
            return true;
        }
    }
    false
}

pub fn build_agenda_layout(
    anchor_date: NaiveDate,
    days_ahead: i64,
    today: NaiveDate,
    query: &str,
    occurrences: &[EventOccurrence],
) -> AgendaLayout {
    let span = days_ahead.max(1);
    let range_start = anchor_date;
    let range_end = anchor_date + TimeDelta::days(span - 1);
    let tomorrow = today + TimeDelta::days(1);
    let needle = query.trim().to_lowercase();

    let mut by_day: BTreeMap<NaiveDate, Vec<EventOccurrence>> = BTreeMap::new();

    for occ in occurrences {
        if occ.end_date < range_start || occ.start_date > range_end {
            continue;
        }
        if !occurrence_matches_query(occ, &needle) {
            continue;
        }

        let first = occ.start_date.max(range_start);
        let last = occ.end_date.min(range_end);
        let mut cursor = first;
        while cursor <= last {
            by_day.entry(cursor).or_default().push(occ.clone());
            let Some(next) = cursor.succ_opt() else {
                break;
            };
            cursor = next;
        }
    }

    let mut total_events = 0usize;
    let mut groups = Vec::with_capacity(by_day.len());

    for (date, mut items) in by_day {
        items.sort_by(|a, b| {
            b.is_all_day
                .cmp(&a.is_all_day)
                .then_with(|| a.start_local.cmp(&b.start_local))
                .then_with(|| a.summary.cmp(&b.summary))
        });
        total_events += items.len();
        groups.push(AgendaDayGroup {
            date,
            is_today: date == today,
            is_tomorrow: date == tomorrow,
            occurrences: items,
        });
    }

    AgendaLayout {
        range_start,
        range_end,
        groups,
        total_events,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{Event, expand_events_in_range, resolve_local_datetime};

    #[test]
    fn groups_upcoming_events_by_day_and_filters_by_query() {
        let today = NaiveDate::from_ymd_opt(2026, 11, 26).unwrap();
        let tomorrow = NaiveDate::from_ymd_opt(2026, 11, 27).unwrap();

        let ev1 = Event::new_all_day("cal-1", "Arch Upgrade", today, tomorrow);
        let ev2 = Event::new_timed_local(
            "cal-1",
            "Dentist Appointment",
            tomorrow.and_hms_opt(15, 0, 0).unwrap(),
            tomorrow.and_hms_opt(16, 0, 0).unwrap(),
        );

        let range_start = resolve_local_datetime(today.and_hms_opt(0, 0, 0).unwrap());
        let range_end = resolve_local_datetime(
            (today + TimeDelta::days(14))
                .and_hms_opt(0, 0, 0)
                .unwrap(),
        );
        let occs = expand_events_in_range(&[ev1, ev2], range_start, range_end);

        let full = build_agenda_layout(today, 14, today, "", &occs);
        assert_eq!(full.groups.len(), 2);
        assert!(full.groups[0].is_today);
        assert!(full.groups[1].is_tomorrow);
        assert_eq!(full.total_events, 2);

        let filtered = build_agenda_layout(today, 14, today, "dentist", &occs);
        assert_eq!(filtered.groups.len(), 1);
        assert_eq!(filtered.groups[0].date, tomorrow);
        assert_eq!(filtered.total_events, 1);
    }
}
