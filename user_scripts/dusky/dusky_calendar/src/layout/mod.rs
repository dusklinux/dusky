//! Pure calendar geometry & projection layout modules for Month, Week, and Agenda views.

pub mod agenda;
pub mod month;
pub mod week;

pub use agenda::{AgendaDayGroup, AgendaLayout, build_agenda_layout};
pub use month::{MonthDayCell, MonthLayout, MonthSpanItem, MonthWeekRow, build_month_layout};
pub use week::{WeekAllDaySpan, WeekDayHeader, WeekLayout, WeekTimedBlock, build_week_layout};

use crate::model::EventOccurrence;
use chrono::{Datelike, NaiveDate, TimeDelta, Weekday};

/// Returns the ordered 7-day weekday header sequence for Monday-first or Sunday-first calendars.
pub fn ordered_weekdays(week_start_monday: bool) -> [Weekday; 7] {
    if week_start_monday {
        [
            Weekday::Mon,
            Weekday::Tue,
            Weekday::Wed,
            Weekday::Thu,
            Weekday::Fri,
            Weekday::Sat,
            Weekday::Sun,
        ]
    } else {
        [
            Weekday::Sun,
            Weekday::Mon,
            Weekday::Tue,
            Weekday::Wed,
            Weekday::Thu,
            Weekday::Fri,
            Weekday::Sat,
        ]
    }
}

/// Returns the start date of the week containing `date`.
pub fn week_start_for(date: NaiveDate, week_start_monday: bool) -> NaiveDate {
    let offset = if week_start_monday {
        i64::from(date.weekday().num_days_from_monday())
    } else {
        i64::from(date.weekday().num_days_from_sunday())
    };
    date - TimeDelta::days(offset)
}

/// Returns the number of days in `(year, month)`.
pub fn days_in_month(year: i32, month: u32) -> u32 {
    let (next_year, next_month) = if month == 12 {
        (year + 1, 1)
    } else {
        (year, month + 1)
    };
    let first_of_next = NaiveDate::from_ymd_opt(next_year, next_month, 1)
        .expect("valid next month");
    first_of_next
        .pred_opt()
        .expect("valid last day of month")
        .day()
}

/// Shifts `date` by `delta_months` while clamping the day-of-month to the target month's length.
pub fn shift_months(date: NaiveDate, delta_months: i32) -> NaiveDate {
    let total_months = (date.year() * 12 + (date.month() as i32 - 1)) + delta_months;
    let target_year = total_months.div_euclid(12).clamp(1, 9999);
    let target_month = (total_months.rem_euclid(12) + 1) as u32;
    let max_day = days_in_month(target_year, target_month);
    let target_day = date.day().min(max_day);
    NaiveDate::from_ymd_opt(target_year, target_month, target_day).unwrap_or(date)
}

/// Returns inclusive `(grid_start, grid_end)` for a 6-row (42-day) month matrix.
pub fn month_grid_bounds(year: i32, month: u32, week_start_monday: bool) -> (NaiveDate, NaiveDate) {
    let first_of_month =
        NaiveDate::from_ymd_opt(year, month, 1).expect("valid first of month");
    let grid_start = week_start_for(first_of_month, week_start_monday);
    let grid_end = grid_start + TimeDelta::days(41);
    (grid_start, grid_end)
}

/// Computes a 42-slot boolean array indicating which cells in a mini month grid contain at least one event.
pub fn month_days_with_events(occurrences: &[EventOccurrence], grid_start: NaiveDate) -> [bool; 42] {
    let mut dots = [false; 42];
    let grid_end = grid_start + TimeDelta::days(41);
    for occ in occurrences {
        if occ.end_date < grid_start || occ.start_date > grid_end {
            continue;
        }
        let s = occ.start_date.max(grid_start);
        let e = occ.end_date.min(grid_end);
        let start_idx = (s - grid_start).num_days().clamp(0, 41) as usize;
        let end_idx = (e - grid_start).num_days().clamp(0, 41) as usize;
        for slot in &mut dots[start_idx..=end_idx] {
            *slot = true;
        }
    }
    dots
}

pub fn is_weekend(date: NaiveDate) -> bool {
    matches!(date.weekday(), Weekday::Sat | Weekday::Sun)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn month_shifting_clamps_end_of_month() {
        let jan31 = NaiveDate::from_ymd_opt(2026, 1, 31).unwrap();
        assert_eq!(
            shift_months(jan31, 1),
            NaiveDate::from_ymd_opt(2026, 2, 28).unwrap()
        );
        assert_eq!(
            shift_months(jan31, -1),
            NaiveDate::from_ymd_opt(2025, 12, 31).unwrap()
        );
    }

    #[test]
    fn week_start_respects_monday_or_sunday() {
        // 2026-11-26 is Thursday
        let thu = NaiveDate::from_ymd_opt(2026, 11, 26).unwrap();
        assert_eq!(
            week_start_for(thu, true),
            NaiveDate::from_ymd_opt(2026, 11, 23).unwrap()
        );
        assert_eq!(
            week_start_for(thu, false),
            NaiveDate::from_ymd_opt(2026, 11, 22).unwrap()
        );
    }
}
