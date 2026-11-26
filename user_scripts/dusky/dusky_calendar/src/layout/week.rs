//! Week view layout engine: sticky all-day/multi-day header lane packer and
//! sweep-line 24-hour timed-event overlap cluster & column expander.

use crate::layout::is_weekend;
use crate::model::{EventOccurrence, resolve_local_datetime};
use chrono::{NaiveDate, TimeDelta, Timelike};

/// Minimum visual height of a timed block in minutes so brief events remain clickable and legible.
const MIN_VISUAL_MINUTES: f32 = 25.0;

#[derive(Debug, Clone)]
pub struct WeekDayHeader {
    pub date: NaiveDate,
    pub is_today: bool,
    pub is_weekend: bool,
}

#[derive(Debug, Clone)]
pub struct WeekAllDaySpan {
    pub occurrence: EventOccurrence,
    pub start_col: usize,
    pub end_col: usize,
    pub lane: usize,
    pub continues_left: bool,
    pub continues_right: bool,
}

#[derive(Debug, Clone)]
pub struct WeekTimedBlock {
    pub occurrence: EventOccurrence,
    pub day_col: usize,
    pub start_minutes: f32,
    pub end_minutes: f32,
    pub col: usize,
    pub col_span: usize,
    pub total_cols: usize,
    pub continues_from_prev_day: bool,
    pub continues_to_next_day: bool,
}

#[derive(Debug, Clone)]
pub struct WeekLayout {
    pub week_start: NaiveDate,
    pub week_end: NaiveDate,
    pub days: [WeekDayHeader; 7],
    pub all_day_spans: Vec<WeekAllDaySpan>,
    pub total_all_day_lanes: usize,
    pub timed_blocks: Vec<WeekTimedBlock>,
}

fn is_header_banner_event(occ: &EventOccurrence) -> bool {
    if occ.is_all_day {
        return true;
    }
    (occ.end_local - occ.start_local) >= TimeDelta::hours(24)
}

#[derive(Clone)]
struct RawDaySlice {
    occurrence: EventOccurrence,
    start_minutes: f32,
    visual_end: f32,
    continues_from_prev_day: bool,
    continues_to_next_day: bool,
}

fn layout_day_timed_slices(day_col: usize, mut slices: Vec<RawDaySlice>) -> Vec<WeekTimedBlock> {
    if slices.is_empty() {
        return Vec::new();
    }

    slices.sort_by(|a, b| {
        a.start_minutes
            .total_cmp(&b.start_minutes)
            .then_with(|| b.visual_end.total_cmp(&a.visual_end))
            .then_with(|| a.occurrence.summary.cmp(&b.occurrence.summary))
    });

    // Partition transitively overlapping slices into independent collision clusters.
    let mut clusters: Vec<Vec<RawDaySlice>> = Vec::new();
    let mut current_cluster: Vec<RawDaySlice> = Vec::new();
    let mut cluster_max_end: f32 = 0.0;

    for slice in slices {
        if current_cluster.is_empty() {
            cluster_max_end = slice.visual_end;
            current_cluster.push(slice);
        } else if slice.start_minutes < cluster_max_end {
            cluster_max_end = cluster_max_end.max(slice.visual_end);
            current_cluster.push(slice);
        } else {
            clusters.push(std::mem::take(&mut current_cluster));
            cluster_max_end = slice.visual_end;
            current_cluster.push(slice);
        }
    }
    if !current_cluster.is_empty() {
        clusters.push(current_cluster);
    }

    let mut placed = Vec::new();

    for cluster in clusters {
        // Greedy leftmost column placement.
        let mut col_ends: Vec<f32> = Vec::new();
        let mut assigned: Vec<(RawDaySlice, usize)> = Vec::with_capacity(cluster.len());

        for slice in cluster {
            if let Some((col_idx, end)) = col_ends
                .iter_mut()
                .enumerate()
                .find(|(_, end)| **end <= slice.start_minutes)
            {
                *end = slice.visual_end;
                assigned.push((slice, col_idx));
            } else {
                let col_idx = col_ends.len();
                col_ends.push(slice.visual_end);
                assigned.push((slice, col_idx));
            }
        }

        let total_cols = col_ends.len().max(1);

        // Expand each block rightward across adjacent unused columns when free.
        for i in 0..assigned.len() {
            let (ref slice, col) = assigned[i];
            let mut span = 1usize;
            while col + span < total_cols {
                let target_col = col + span;
                let blocked = assigned.iter().any(|(other, other_col)| {
                    *other_col == target_col
                        && other.start_minutes < slice.visual_end
                        && other.visual_end > slice.start_minutes
                });
                if blocked {
                    break;
                }
                span += 1;
            }

            placed.push(WeekTimedBlock {
                occurrence: slice.occurrence.clone(),
                day_col,
                start_minutes: slice.start_minutes,
                end_minutes: slice.visual_end,
                col,
                col_span: span,
                total_cols,
                continues_from_prev_day: slice.continues_from_prev_day,
                continues_to_next_day: slice.continues_to_next_day,
            });
        }
    }

    placed
}

pub fn build_week_layout(
    week_start: NaiveDate,
    today: NaiveDate,
    occurrences: &[EventOccurrence],
) -> WeekLayout {
    let week_end = week_start + TimeDelta::days(6);

    let days: [WeekDayHeader; 7] = std::array::from_fn(|col| {
        let date = week_start + TimeDelta::days(col as i64);
        WeekDayHeader {
            date,
            is_today: date == today,
            is_weekend: is_weekend(date),
        }
    });

    let mut banner_candidates: Vec<(&EventOccurrence, usize, usize, bool, bool)> = Vec::new();
    let mut day_slices: [Vec<RawDaySlice>; 7] = std::array::from_fn(|_| Vec::new());

    for occ in occurrences {
        if occ.end_date < week_start || occ.start_date > week_end {
            continue;
        }

        if is_header_banner_event(occ) {
            let clipped_start = occ.start_date.max(week_start);
            let clipped_end = occ.end_date.min(week_end);
            let start_col = (clipped_start - week_start).num_days().clamp(0, 6) as usize;
            let end_col = (clipped_end - week_start).num_days().clamp(0, 6) as usize;
            banner_candidates.push((
                occ,
                start_col,
                end_col,
                occ.start_date < week_start,
                occ.end_date > week_end,
            ));
        } else {
            // Clip timed event cleanly onto each visible day column it touches.
            let first_col = (occ.start_date.max(week_start) - week_start)
                .num_days()
                .clamp(0, 6) as usize;
            let last_col = (occ.end_date.min(week_end) - week_start)
                .num_days()
                .clamp(0, 6) as usize;

            for col in first_col..=last_col {
                let day_date = week_start + TimeDelta::days(col as i64);
                let day_start_dt =
                    resolve_local_datetime(day_date.and_hms_opt(0, 0, 0).expect("midnight"));
                let next_day_dt = resolve_local_datetime(
                    (day_date + TimeDelta::days(1))
                        .and_hms_opt(0, 0, 0)
                        .expect("midnight"),
                );

                let continues_from_prev_day = occ.start_local < day_start_dt;
                let continues_to_next_day = occ.end_local > next_day_dt;

                let start_minutes = if continues_from_prev_day {
                    0.0
                } else {
                    (occ.start_local.hour() as f32) * 60.0 + (occ.start_local.minute() as f32)
                };

                let raw_end_minutes = if continues_to_next_day {
                    1440.0
                } else if occ.end_local == next_day_dt {
                    1440.0
                } else {
                    (occ.end_local.hour() as f32) * 60.0 + (occ.end_local.minute() as f32)
                };

                let visual_end = raw_end_minutes
                    .max(start_minutes + MIN_VISUAL_MINUTES)
                    .min(1440.0);
                let clamped_start = start_minutes.min(1440.0 - MIN_VISUAL_MINUTES).max(0.0);

                day_slices[col].push(RawDaySlice {
                    occurrence: occ.clone(),
                    start_minutes: clamped_start,
                    visual_end,
                    continues_from_prev_day,
                    continues_to_next_day,
                });
            }
        }
    }

    // Pack all-day header lanes greedily.
    banner_candidates.sort_by(|a, b| {
        let a_span = a.2 - a.1;
        let b_span = b.2 - b.1;
        a.1.cmp(&b.1)
            .then_with(|| b_span.cmp(&a_span))
            .then_with(|| a.0.summary.cmp(&b.0.summary))
    });

    let mut lane_masks: Vec<u8> = Vec::new();
    let mut all_day_spans = Vec::with_capacity(banner_candidates.len());

    for (occ, start_col, end_col, continues_left, continues_right) in banner_candidates {
        let mut span_mask = 0u8;
        for col in start_col..=end_col {
            span_mask |= 1u8 << col;
        }
        let lane = if let Some((idx, mask)) = lane_masks
            .iter_mut()
            .enumerate()
            .find(|(_, m)| (**m & span_mask) == 0)
        {
            *mask |= span_mask;
            idx
        } else {
            let idx = lane_masks.len();
            lane_masks.push(span_mask);
            idx
        };

        all_day_spans.push(WeekAllDaySpan {
            occurrence: occ.clone(),
            start_col,
            end_col,
            lane,
            continues_left,
            continues_right,
        });
    }

    let mut timed_blocks = Vec::new();
    for (col, slices) in day_slices.into_iter().enumerate() {
        timed_blocks.extend(layout_day_timed_slices(col, slices));
    }

    WeekLayout {
        week_start,
        week_end,
        days,
        all_day_spans,
        total_all_day_lanes: lane_masks.len(),
        timed_blocks,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{Event, expand_events_in_range};

    #[test]
    fn clusters_overlapping_timed_events_and_expands_free_columns() {
        let day = NaiveDate::from_ymd_opt(2026, 11, 26).unwrap();
        let week_start = NaiveDate::from_ymd_opt(2026, 11, 23).unwrap();

        let ev_a = Event::new_timed_local(
            "cal-1",
            "Long Meeting",
            day.and_hms_opt(9, 0, 0).unwrap(),
            day.and_hms_opt(12, 0, 0).unwrap(),
        );
        let ev_b = Event::new_timed_local(
            "cal-1",
            "Standup",
            day.and_hms_opt(9, 30, 0).unwrap(),
            day.and_hms_opt(10, 0, 0).unwrap(),
        );
        let ev_c = Event::new_timed_local(
            "cal-1",
            "Design Sync",
            day.and_hms_opt(9, 30, 0).unwrap(),
            day.and_hms_opt(10, 30, 0).unwrap(),
        );
        let ev_d = Event::new_timed_local(
            "cal-1",
            "Check-in",
            day.and_hms_opt(11, 0, 0).unwrap(),
            day.and_hms_opt(11, 30, 0).unwrap(),
        );

        let range_start = resolve_local_datetime(week_start.and_hms_opt(0, 0, 0).unwrap());
        let range_end = resolve_local_datetime(
            (week_start + TimeDelta::days(7))
                .and_hms_opt(0, 0, 0)
                .unwrap(),
        );
        let occs = expand_events_in_range(&[ev_a, ev_b, ev_c, ev_d], range_start, range_end);
        let layout = build_week_layout(week_start, day, &occs);

        assert_eq!(layout.timed_blocks.len(), 4);
        // Cluster has 3 simultaneous columns at 09:30 (Long Meeting, Standup, Design Sync).
        for block in &layout.timed_blocks {
            assert_eq!(block.total_cols, 3);
            assert_eq!(block.day_col, 3); // Thu
        }
        // Check-in at 11:00 only collides with Long Meeting (col 0), so it should span cols 1..3 (col_span == 2).
        let checkin = layout
            .timed_blocks
            .iter()
            .find(|b| b.occurrence.summary == "Check-in")
            .unwrap();
        assert_eq!(checkin.col, 1);
        assert_eq!(checkin.col_span, 2);
    }
}
