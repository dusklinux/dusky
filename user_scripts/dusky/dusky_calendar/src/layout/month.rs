//! 6-row Month grid matrix builder with greedy multi-day spanning lane packing
//! and per-cell `+N more` overflow computation.

use crate::layout::{is_weekend, month_grid_bounds};
use crate::model::EventOccurrence;
use chrono::{Datelike, NaiveDate, TimeDelta};

#[derive(Debug, Clone)]
pub struct MonthSpanItem {
    pub occurrence: EventOccurrence,
    pub start_col: usize,
    pub end_col: usize,
    pub lane: usize,
    pub continues_left: bool,
    pub continues_right: bool,
}

#[derive(Debug, Clone)]
pub struct MonthDayCell {
    pub date: NaiveDate,
    pub in_current_month: bool,
    pub is_today: bool,
    pub is_weekend: bool,
    pub is_first_of_month: bool,
    pub overflow_count: usize,
    pub all_occurrences: Vec<EventOccurrence>,
}

#[derive(Debug, Clone)]
pub struct MonthWeekRow {
    pub week_start: NaiveDate,
    pub iso_week: u32,
    pub days: [MonthDayCell; 7],
    pub spans: Vec<MonthSpanItem>,
    pub max_lanes: usize,
}

#[derive(Debug, Clone)]
pub struct MonthLayout {
    pub year: i32,
    pub month: u32,
    pub grid_start: NaiveDate,
    pub grid_end: NaiveDate,
    pub weeks: Vec<MonthWeekRow>,
}

impl MonthLayout {
    pub fn day_cell(&self, date: NaiveDate) -> Option<&MonthDayCell> {
        self.weeks
            .iter()
            .flat_map(|w| w.days.iter())
            .find(|c| c.date == date)
    }
}

pub fn build_month_layout(
    year: i32,
    month: u32,
    today: NaiveDate,
    week_start_monday: bool,
    max_lanes: usize,
    occurrences: &[EventOccurrence],
) -> MonthLayout {
    let (grid_start, grid_end) = month_grid_bounds(year, month, week_start_monday);
    let max_lanes = max_lanes.max(1);
    let mut weeks = Vec::with_capacity(6);

    for row in 0..6 {
        let week_start = grid_start + TimeDelta::days(row * 7);
        let week_end = week_start + TimeDelta::days(6);

        let mut days: [MonthDayCell; 7] = std::array::from_fn(|col| {
            let date = week_start + TimeDelta::days(col as i64);
            MonthDayCell {
                date,
                in_current_month: date.year() == year && date.month() == month,
                is_today: date == today,
                is_weekend: is_weekend(date),
                is_first_of_month: date.day() == 1,
                overflow_count: 0,
                all_occurrences: Vec::new(),
            }
        });

        // Gather every occurrence overlapping this 7-day week.
        let mut candidates: Vec<(&EventOccurrence, usize, usize, bool, bool)> = Vec::new();
        for occ in occurrences {
            if occ.end_date < week_start || occ.start_date > week_end {
                continue;
            }
            let clipped_start = occ.start_date.max(week_start);
            let clipped_end = occ.end_date.min(week_end);
            let start_col = (clipped_start - week_start).num_days().clamp(0, 6) as usize;
            let end_col = (clipped_end - week_start).num_days().clamp(0, 6) as usize;
            let continues_left = occ.start_date < week_start;
            let continues_right = occ.end_date > week_end;

            for day in &mut days[start_col..=end_col] {
                day.all_occurrences.push(occ.clone());
            }

            candidates.push((occ, start_col, end_col, continues_left, continues_right));
        }

        // Keep each day's full list cleanly sorted for the Day Overflow popover.
        for day in &mut days {
            day.all_occurrences.sort_by(|a, b| {
                b.is_all_day
                    .cmp(&a.is_all_day)
                    .then_with(|| b.spans_multiple_days().cmp(&a.spans_multiple_days()))
                    .then_with(|| a.start_local.cmp(&b.start_local))
                    .then_with(|| a.summary.cmp(&b.summary))
            });
        }

        // Pack priority: multi-day/all-day first (longest span first), then earlier start time.
        candidates.sort_by(|a, b| {
            let a_banner = a.0.is_all_day || a.0.spans_multiple_days();
            let b_banner = b.0.is_all_day || b.0.spans_multiple_days();
            let a_span = a.2 - a.1;
            let b_span = b.2 - b.1;
            b_banner
                .cmp(&a_banner)
                .then_with(|| a.1.cmp(&b.1))
                .then_with(|| b_span.cmp(&a_span))
                .then_with(|| a.0.start_local.cmp(&b.0.start_local))
                .then_with(|| a.0.summary.cmp(&b.0.summary))
        });

        let mut lane_masks = vec![0u8; max_lanes];
        let mut spans = Vec::new();

        for (occ, start_col, end_col, continues_left, continues_right) in candidates {
            let mut span_mask = 0u8;
            for col in start_col..=end_col {
                span_mask |= 1u8 << col;
            }

            if let Some((lane, mask)) = lane_masks
                .iter_mut()
                .enumerate()
                .find(|(_, m)| (**m & span_mask) == 0)
            {
                *mask |= span_mask;
                spans.push(MonthSpanItem {
                    occurrence: occ.clone(),
                    start_col,
                    end_col,
                    lane,
                    continues_left,
                    continues_right,
                });
            } else {
                for day in &mut days[start_col..=end_col] {
                    day.overflow_count += 1;
                }
            }
        }

        weeks.push(MonthWeekRow {
            week_start,
            iso_week: week_start.iso_week().week(),
            days,
            spans,
            max_lanes,
        });
    }

    MonthLayout {
        year,
        month,
        grid_start,
        grid_end,
        weeks,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{Event, expand_events_in_range, resolve_local_datetime};

    #[test]
    fn packs_multi_day_spans_and_counts_overflow() {
        let cal = "cal-1";
        let events = vec![
            Event::new_all_day(
                cal,
                "Conference",
                NaiveDate::from_ymd_opt(2026, 11, 24).unwrap(),
                NaiveDate::from_ymd_opt(2026, 11, 28).unwrap(), // Tue..Fri
            ),
            Event::new_all_day(
                cal,
                "Design Jam",
                NaiveDate::from_ymd_opt(2026, 11, 25).unwrap(),
                NaiveDate::from_ymd_opt(2026, 11, 27).unwrap(), // Wed..Thu
            ),
            Event::new_all_day(
                cal,
                "Overflow Event",
                NaiveDate::from_ymd_opt(2026, 11, 25).unwrap(),
                NaiveDate::from_ymd_opt(2026, 11, 26).unwrap(), // Wed only
            ),
        ];

        let range_start = resolve_local_datetime(
            NaiveDate::from_ymd_opt(2026, 10, 25)
                .unwrap()
                .and_hms_opt(0, 0, 0)
                .unwrap(),
        );
        let range_end = resolve_local_datetime(
            NaiveDate::from_ymd_opt(2026, 12, 10)
                .unwrap()
                .and_hms_opt(0, 0, 0)
                .unwrap(),
        );
        let occs = expand_events_in_range(&events, range_start, range_end);
        let layout = build_month_layout(
            2026,
            11,
            NaiveDate::from_ymd_opt(2026, 11, 26).unwrap(),
            true,
            2, // Allow only 2 lanes to force overflow on Wed Nov 25
            &occs,
        );

        assert_eq!(layout.weeks.len(), 6);
        let wed_cell = layout
            .day_cell(NaiveDate::from_ymd_opt(2026, 11, 25).unwrap())
            .unwrap();
        assert_eq!(wed_cell.all_occurrences.len(), 3);
        assert_eq!(wed_cell.overflow_count, 1);

        let thu_cell = layout
            .day_cell(NaiveDate::from_ymd_opt(2026, 11, 26).unwrap())
            .unwrap();
        assert!(thu_cell.is_today);
        assert_eq!(thu_cell.overflow_count, 0);
    }
}
