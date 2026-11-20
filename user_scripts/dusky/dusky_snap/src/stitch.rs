use crate::Result;
use image::RgbaImage;
use std::sync::{
    Condvar, Mutex,
    atomic::{AtomicBool, Ordering},
};

pub const DOWNSAMPLE_CROSS: usize = 4;
pub const MIN_OVERLAP_DEN: usize = 4;
pub const MIN_OVERLAP_PIXELS: usize = 16;
pub const MIN_MOTION_PIXELS: usize = 3;
pub const MAX_STITCHED_BYTES: usize = 512 * 1024 * 1024;
pub const MAX_STITCHED_PIXELS: usize = MAX_STITCHED_BYTES / 4;

pub const STATIONARY_ERROR: f64 = 1.0;
pub const MAX_STATIONARY_ERROR: f64 = 8.0;
pub const MAX_AMBIGUOUS_ERROR: f64 = 2.0;
pub const MAX_MATCH_ERROR: f64 = 24.0;
pub const MIN_CONFIDENCE: f64 = 1.10;
pub const MIN_ERROR_MARGIN: f64 = 0.75;
pub const MAX_FORWARD_CANDIDATES: usize = 64;
pub const PATH_DELTA_TOLERANCE: isize = 2;
pub const MAX_STATIONARY_EDGE: usize = 384;
pub const STATIONARY_EDGE_ERROR: f64 = 1.0;
pub const BORDER_TOLERANCE_PX: usize = 2;
pub const BORDER_TOLERANCE_ERROR: f64 = 4.0;
pub const BORDER_CONFIRM_LINES: usize = 6;
pub const MAX_STATIONARY_SCORING_EDGE_DEN: usize = 3;
pub const MAX_NEAR_STATIONARY_HALO: usize = 24;
pub const NEAR_STATIONARY_ERROR: f64 = 4.0;

pub const AUTO_END_CONFIRMATION_PROBES: usize = 2;
pub const NOTCHES_PER_TICK: i32 = 3;
pub const PROBE_NOTCHES: i32 = 1;
pub const MAX_BLANK_FIRST_FRAMES: usize = 8;

const SIG_BUCKETS: usize = 16;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Axis {
    Vertical,
    Horizontal,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SearchDirection {
    ForwardOnly,
    Bidirectional,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MotionKind {
    Stationary,
    Forward,
    Reverse,
    Ambiguous,
    Unmatchable,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Motion {
    pub kind: MotionKind,
    pub delta: i32,
}

#[derive(Clone, Copy, Debug)]
pub struct MotionEstimate {
    pub motion: Motion,
    pub error: f64,
    pub confidence: f64,
}

impl Default for MotionEstimate {
    fn default() -> Self {
        Self {
            motion: Motion {
                kind: MotionKind::Unmatchable,
                delta: 0,
            },
            error: f64::INFINITY,
            confidence: 0.0,
        }
    }
}

#[derive(Clone, Debug, Default)]
pub struct GrayView {
    pub pixels: Vec<u8>,
    pub width: usize,
    pub height: usize,
}

impl GrayView {
    pub fn axis_len(&self, axis: Axis) -> usize {
        match axis {
            Axis::Vertical => self.height,
            Axis::Horizontal => self.width,
        }
    }

    pub fn cross_len(&self, axis: Axis) -> usize {
        match axis {
            Axis::Vertical => self.width,
            Axis::Horizontal => self.height,
        }
    }

    pub fn crop_axis(&self, axis: Axis, start: usize, end: usize) -> Self {
        let len = self.axis_len(axis);
        let s = start.min(len);
        let e = end.max(s).min(len);
        match axis {
            Axis::Vertical => Self {
                width: self.width,
                height: e - s,
                pixels: self.pixels[s * self.width..e * self.width].to_vec(),
            },
            Axis::Horizontal => {
                let cw = e - s;
                let mut pixels = Vec::with_capacity(cw * self.height);
                for row in 0..self.height {
                    let base = row * self.width;
                    pixels.extend_from_slice(&self.pixels[base + s..base + e]);
                }
                Self {
                    width: cw,
                    height: self.height,
                    pixels,
                }
            }
        }
    }
}

pub fn downsample_to_gray(image: &RgbaImage, axis: Axis) -> GrayView {
    let src_w = image.width() as usize;
    let src_h = image.height() as usize;
    let dst_w = if axis == Axis::Vertical {
        (src_w / DOWNSAMPLE_CROSS).max(1).min(src_w.max(1))
    } else {
        src_w
    };
    let dst_h = if axis == Axis::Horizontal {
        (src_h / DOWNSAMPLE_CROSS).max(1).min(src_h.max(1))
    } else {
        src_h
    };
    let raw = image.as_raw();
    let mut pixels = vec![0u8; dst_w * dst_h];
    for gy in 0..dst_h {
        let sy = if axis == Axis::Vertical {
            gy
        } else {
            (gy * DOWNSAMPLE_CROSS).min(src_h.saturating_sub(1))
        };
        let row_off = sy * src_w * 4;
        let dst_row = &mut pixels[gy * dst_w..(gy + 1) * dst_w];
        for (gx, dst) in dst_row.iter_mut().enumerate() {
            let sx = if axis == Axis::Vertical {
                (gx * DOWNSAMPLE_CROSS).min(src_w.saturating_sub(1))
            } else {
                gx
            };
            let p = &raw[row_off + sx * 4..row_off + sx * 4 + 4];
            *dst = ((p[0] as u32 * 77 + p[1] as u32 * 150 + p[2] as u32 * 29) >> 8) as u8;
        }
    }
    GrayView {
        pixels,
        width: dst_w,
        height: dst_h,
    }
}

fn cross_margin(cross_len: usize) -> usize {
    (cross_len / 12).min(cross_len.saturating_sub(1) / 2)
}

/// Fast zero-shift MAD across the non-margin interior for settlement and idle checks.
pub fn frame_diff_at_zero(prev: &GrayView, cur: &GrayView, axis: Axis) -> f64 {
    if prev.width != cur.width
        || prev.height != cur.height
        || prev.pixels.len() != prev.width * prev.height
        || cur.pixels.len() != cur.width * cur.height
    {
        return f64::INFINITY;
    }
    let axis_len = prev.axis_len(axis);
    let cross_len = prev.cross_len(axis);
    if axis_len == 0 || cross_len == 0 {
        return f64::INFINITY;
    }
    let margin = cross_margin(cross_len);
    let cross_end = cross_len.saturating_sub(margin).max(margin + 1).min(cross_len);
    let a_step = (axis_len / 128).max(1);
    let c_step = ((cross_end - margin) / 64).max(1);
    let mut total = 0u64;
    let mut count = 0u64;
    match axis {
        Axis::Vertical => {
            for along in (0..axis_len).step_by(a_step) {
                let row = along * prev.width;
                for cross in (margin..cross_end).step_by(c_step) {
                    total += prev.pixels[row + cross].abs_diff(cur.pixels[row + cross]) as u64;
                    count += 1;
                }
            }
        }
        Axis::Horizontal => {
            for along in (0..axis_len).step_by(a_step) {
                for cross in (margin..cross_end).step_by(c_step) {
                    let idx = cross * prev.width + along;
                    total += prev.pixels[idx].abs_diff(cur.pixels[idx]) as u64;
                    count += 1;
                }
            }
        }
    }
    if count == 0 {
        f64::INFINITY
    } else {
        total as f64 / count as f64
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct StationaryEdges {
    pub lead: usize,
    pub trail: usize,
}

fn edge_line_error(prev: &GrayView, cur: &GrayView, axis: Axis, along: usize) -> f64 {
    let cross_len = prev.cross_len(axis);
    let margin = cross_margin(cross_len);
    let cross_end = cross_len.saturating_sub(margin).max(margin + 1).min(cross_len);
    let step = ((cross_end - margin) / 128).max(1);
    let mut total = 0u64;
    let mut count = 0u64;
    let mut cross = margin;
    while cross < cross_end {
        let idx = match axis {
            Axis::Vertical => along * prev.width + cross,
            Axis::Horizontal => cross * prev.width + along,
        };
        total += prev.pixels[idx].abs_diff(cur.pixels[idx]) as u64;
        count += 1;
        cross += step;
    }
    if count == 0 {
        f64::INFINITY
    } else {
        total as f64 / count as f64
    }
}

fn scan_stationary_depth(max_edge: usize, err_at: impl Fn(usize) -> f64) -> usize {
    let mut depth = 0;
    while depth < max_edge {
        let err = err_at(depth);
        if err <= STATIONARY_EDGE_ERROR {
            depth += 1;
            continue;
        }
        if depth < BORDER_TOLERANCE_PX
            && err <= BORDER_TOLERANCE_ERROR
            && depth + BORDER_CONFIRM_LINES < max_edge
            && (1..=BORDER_CONFIRM_LINES).all(|k| err_at(depth + k) <= STATIONARY_EDGE_ERROR)
        {
            depth += 1;
            continue;
        }
        break;
    }
    depth
}

pub fn stationary_scoring_edges(prev: &GrayView, cur: &GrayView, axis: Axis) -> StationaryEdges {
    let axis_len = prev.axis_len(axis);
    let min_keep = MIN_OVERLAP_PIXELS + MIN_MOTION_PIXELS;
    if axis_len <= min_keep {
        return StationaryEdges::default();
    }
    let max_edge = (axis_len / MAX_STATIONARY_SCORING_EDGE_DEN)
        .min(MAX_STATIONARY_EDGE)
        .min((axis_len - min_keep) / 2);
    let lead = scan_stationary_depth(max_edge, |d| edge_line_error(prev, cur, axis, d));
    let trail = scan_stationary_depth(max_edge, |d| {
        edge_line_error(prev, cur, axis, axis_len - 1 - d)
    });
    StationaryEdges { lead, trail }
}

/// Compact 16-bucket cross-axis signature per scroll-axis line for fast full-overlap search.
struct AxisSignature {
    lines: Vec<[u8; SIG_BUCKETS]>,
    buckets: usize,
}

impl AxisSignature {
    fn build(view: &GrayView, axis: Axis) -> Self {
        let axis_len = view.axis_len(axis);
        let cross_len = view.cross_len(axis);
        let margin = cross_margin(cross_len);
        let cross_end = cross_len.saturating_sub(margin).max(margin + 1).min(cross_len);
        let inner = cross_end - margin;
        let buckets = inner.min(SIG_BUCKETS).max(1);
        let mut lines = vec![[0u8; SIG_BUCKETS]; axis_len];

        match axis {
            Axis::Vertical => {
                for (along, sig) in lines.iter_mut().enumerate() {
                    let row = &view.pixels[along * view.width..(along + 1) * view.width];
                    for (b, slot) in sig.iter_mut().take(buckets).enumerate() {
                        let start = margin + (b * inner) / buckets;
                        let end = (margin + ((b + 1) * inner) / buckets).max(start + 1);
                        let mut sum = 0u32;
                        for &px in &row[start..end] {
                            sum += px as u32;
                        }
                        *slot = (sum / (end - start) as u32) as u8;
                    }
                }
            }
            Axis::Horizontal => {
                for (along, sig) in lines.iter_mut().enumerate() {
                    for (b, slot) in sig.iter_mut().take(buckets).enumerate() {
                        let start = margin + (b * inner) / buckets;
                        let end = (margin + ((b + 1) * inner) / buckets).max(start + 1);
                        let mut sum = 0u32;
                        for cross in start..end {
                            sum += view.pixels[cross * view.width + along] as u32;
                        }
                        *slot = (sum / (end - start) as u32) as u8;
                    }
                }
            }
        }
        Self { lines, buckets }
    }

    fn score_shift(&self, other: &Self, shift: isize, max_shift: usize) -> f64 {
        let axis_len = self.lines.len();
        let mag = shift.unsigned_abs();
        if mag > max_shift || mag >= axis_len {
            return f64::INFINITY;
        }
        let overlap = axis_len - mag;
        let (prev_start, cur_start) = if shift >= 0 { (mag, 0) } else { (0, mag) };
        let step = (overlap / 64).max(1);
        let buckets = self.buckets;
        let mut total = 0u64;
        let mut count = 0u64;
        for along in (0..overlap).step_by(step) {
            let a = &self.lines[prev_start + along];
            let b = &other.lines[cur_start + along];
            for k in 0..buckets {
                total += a[k].abs_diff(b[k]) as u64;
            }
            count += buckets as u64;
        }
        if count == 0 {
            f64::INFINITY
        } else {
            total as f64 / count as f64
        }
    }
}

/// Full-overlap 2D grayscale SAD over the actual overlapping region of a candidate shift.
pub fn score_shift(
    prev: &GrayView,
    cur: &GrayView,
    axis: Axis,
    shift: isize,
    max_shift: usize,
    axis_step: usize,
    cross_step: usize,
) -> f64 {
    let axis_len = prev.axis_len(axis);
    let cross_len = prev.cross_len(axis);
    let magnitude = shift.unsigned_abs();
    if magnitude > max_shift || magnitude >= axis_len {
        return f64::INFINITY;
    }
    let overlap = axis_len - magnitude;
    if overlap == 0 {
        return f64::INFINITY;
    }
    let (prev_start, cur_start) = if shift >= 0 {
        (magnitude, 0)
    } else {
        (0, magnitude)
    };
    let margin = cross_margin(cross_len);
    let cross_end = cross_len.saturating_sub(margin).max(margin + 1).min(cross_len);
    let a_step = axis_step.max(1);
    let c_step = cross_step.max(1);
    let mut total = 0u64;
    let mut count = 0u64;
    match axis {
        Axis::Vertical => {
            for along in (0..overlap).step_by(a_step) {
                let pr = (prev_start + along) * prev.width;
                let cr = (cur_start + along) * cur.width;
                for cross in (margin..cross_end).step_by(c_step) {
                    total += prev.pixels[pr + cross].abs_diff(cur.pixels[cr + cross]) as u64;
                    count += 1;
                }
            }
        }
        Axis::Horizontal => {
            for along in (0..overlap).step_by(a_step) {
                let pc = prev_start + along;
                let cc = cur_start + along;
                for cross in (margin..cross_end).step_by(c_step) {
                    total += prev.pixels[cross * prev.width + pc]
                        .abs_diff(cur.pixels[cross * cur.width + cc])
                        as u64;
                    count += 1;
                }
            }
        }
    }
    if count == 0 {
        f64::INFINITY
    } else {
        total as f64 / count as f64
    }
}

pub fn classify_motion(prev: &GrayView, cur: &GrayView, axis: Axis) -> MotionEstimate {
    classify_motion_directed(prev, cur, axis, SearchDirection::Bidirectional, None)
}

pub fn classify_motion_bounded(
    prev: &GrayView,
    cur: &GrayView,
    axis: Axis,
    max_source_delta: usize,
) -> MotionEstimate {
    classify_motion_directed(
        prev,
        cur,
        axis,
        SearchDirection::Bidirectional,
        Some(max_source_delta),
    )
}

pub fn classify_motion_directed(
    prev: &GrayView,
    cur: &GrayView,
    axis: Axis,
    direction: SearchDirection,
    max_source_delta: Option<usize>,
) -> MotionEstimate {
    if prev.width != cur.width
        || prev.height != cur.height
        || prev.pixels.len() != prev.width * prev.height
        || cur.pixels.len() != cur.width * cur.height
    {
        return MotionEstimate::default();
    }
    let axis_len = prev.axis_len(axis);
    let cross_len = prev.cross_len(axis);
    if axis_len < MIN_OVERLAP_PIXELS + MIN_MOTION_PIXELS || cross_len < 2 {
        return MotionEstimate::default();
    }
    let edges = stationary_scoring_edges(prev, cur, axis);
    if edges.lead + edges.trail > 0 {
        let pc = prev.crop_axis(axis, edges.lead, axis_len - edges.trail);
        let cc = cur.crop_axis(axis, edges.lead, axis_len - edges.trail);
        return classify_motion_cropped(&pc, &cc, axis, direction, max_source_delta);
    }
    classify_motion_cropped(prev, cur, axis, direction, max_source_delta)
}

fn classify_motion_cropped(
    prev: &GrayView,
    cur: &GrayView,
    axis: Axis,
    direction: SearchDirection,
    max_source_delta: Option<usize>,
) -> MotionEstimate {
    let axis_len = prev.axis_len(axis);
    let cross_len = prev.cross_len(axis);
    if axis_len < MIN_OVERLAP_PIXELS + MIN_MOTION_PIXELS || cross_len < 2 {
        return MotionEstimate::default();
    }
    let min_overlap = (axis_len / MIN_OVERLAP_DEN)
        .max(MIN_OVERLAP_PIXELS)
        .min(axis_len.saturating_sub(1));
    let safe_max_shift = axis_len.saturating_sub(min_overlap);
    let max_shift = max_source_delta
        .map(|d| d.min(safe_max_shift))
        .unwrap_or(safe_max_shift);
    if max_shift < MIN_MOTION_PIXELS {
        return MotionEstimate::default();
    }
    let fine_axis = (axis_len / 160).max(1);
    let fine_cross = (cross_len / 128).max(1);

    let zero_error = score_shift(prev, cur, axis, 0, max_shift, fine_axis, fine_cross);
    if zero_error <= STATIONARY_ERROR {
        return MotionEstimate {
            motion: Motion {
                kind: MotionKind::Stationary,
                delta: 0,
            },
            error: zero_error,
            confidence: f64::INFINITY,
        };
    }

    let prev_sig = AxisSignature::build(prev, axis);
    let cur_sig = AxisSignature::build(cur, axis);
    let max_s = max_shift as isize;
    let min_s = match direction {
        SearchDirection::ForwardOnly => 0,
        SearchDirection::Bidirectional => -max_s,
    };

    let mut coarse: Vec<(isize, f64)> = (min_s..=max_s)
        .map(|s| (s, prev_sig.score_shift(&cur_sig, s, max_shift)))
        .collect();
    coarse.sort_by(|a, b| a.1.total_cmp(&b.1));

    let mut fine: Vec<(isize, f64)> = Vec::with_capacity(48);
    for &(s, _) in coarse.iter().take(16) {
        for cand in (s - 1).max(min_s)..=(s + 1).min(max_s) {
            if !fine.iter().any(|&(existing, _)| existing == cand) {
                fine.push((
                    cand,
                    score_shift(prev, cur, axis, cand, max_shift, fine_axis, fine_cross),
                ));
            }
        }
    }
    fine.sort_by(|a, b| a.1.total_cmp(&b.1));
    let Some(&(best_shift, best_error)) = fine.first() else {
        return MotionEstimate::default();
    };

    let runner_neighborhood = (axis_len as isize / 256).clamp(6, 16);
    let mut runner_error = f64::INFINITY;
    for &(s, err) in &fine {
        if (s - best_shift).abs() > runner_neighborhood {
            runner_error = runner_error.min(err);
        }
    }
    let mut taken = 0;
    for &(s, _) in &coarse {
        if (s - best_shift).abs() <= runner_neighborhood {
            continue;
        }
        runner_error = runner_error.min(score_shift(
            prev, cur, axis, s, max_shift, fine_axis, fine_cross,
        ));
        taken += 1;
        if taken >= 8 {
            break;
        }
    }

    let confidence = if best_error <= f64::EPSILON {
        if runner_error <= f64::EPSILON {
            1.0
        } else {
            f64::INFINITY
        }
    } else {
        runner_error / best_error
    };

    let delta_abs = best_shift.unsigned_abs();
    if delta_abs <= MIN_MOTION_PIXELS && best_error <= MAX_STATIONARY_ERROR {
        return MotionEstimate {
            motion: Motion {
                kind: MotionKind::Stationary,
                delta: 0,
            },
            error: best_error,
            confidence,
        };
    }
    if !best_error.is_finite() || best_error > MAX_MATCH_ERROR {
        return MotionEstimate {
            motion: Motion {
                kind: MotionKind::Unmatchable,
                delta: 0,
            },
            error: best_error,
            confidence,
        };
    }
    let margin = runner_error - best_error;
    if confidence < MIN_CONFIDENCE || margin < MIN_ERROR_MARGIN {
        if best_error <= MAX_AMBIGUOUS_ERROR {
            return MotionEstimate {
                motion: Motion {
                    kind: MotionKind::Ambiguous,
                    delta: best_shift as i32,
                },
                error: best_error,
                confidence,
            };
        }
        return MotionEstimate {
            motion: Motion {
                kind: MotionKind::Unmatchable,
                delta: 0,
            },
            error: best_error,
            confidence,
        };
    }
    MotionEstimate {
        motion: Motion {
            kind: if best_shift > 0 {
                MotionKind::Forward
            } else {
                MotionKind::Reverse
            },
            delta: delta_abs as i32,
        },
        error: best_error,
        confidence,
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ForwardMatchCandidate {
    pub delta: i32,
    pub error: f64,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ForwardMatchPath {
    pub first_delta: i32,
    pub second_delta: i32,
    pub total_error: f64,
}

#[derive(Clone, Debug, Default)]
pub struct ForwardCandidateSet {
    pub candidates: Vec<ForwardMatchCandidate>,
    pub truncated: bool,
    pub search_max_delta: Option<i32>,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum StationaryProbeTag {
    Unique,
    Ambiguous,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct StationaryProbeFirstMatch {
    pub tag: StationaryProbeTag,
    pub candidate: Option<ForwardMatchCandidate>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LookaheadTag {
    Resolved,
    LowErrorPeriodic,
    StationaryProbe,
    Unresolved,
}

#[derive(Clone, Debug)]
pub struct ForwardLookaheadResolution {
    pub tag: LookaheadTag,
    pub path: Option<ForwardMatchPath>,
    pub first_match: Option<StationaryProbeFirstMatch>,
}

fn errors_are_distinct(best: f64, runner: f64) -> bool {
    if !best.is_finite() || !runner.is_finite() {
        return runner.is_infinite() && best.is_finite();
    }
    let conf = if best <= f64::EPSILON {
        if runner <= f64::EPSILON {
            1.0
        } else {
            f64::INFINITY
        }
    } else {
        runner / best
    };
    conf >= MIN_CONFIDENCE && (runner - best) >= MIN_ERROR_MARGIN
}

pub fn forward_candidate_set(prev: &GrayView, cur: &GrayView, axis: Axis) -> ForwardCandidateSet {
    let mut result = ForwardCandidateSet::default();
    if prev.width != cur.width || prev.height != cur.height {
        return result;
    }
    let raw_axis_len = prev.axis_len(axis);
    let cross_len = prev.cross_len(axis);
    if raw_axis_len < MIN_OVERLAP_PIXELS + MIN_MOTION_PIXELS || cross_len < 2 {
        return result;
    }
    let edges = stationary_scoring_edges(prev, cur, axis);
    let (prev_cropped, cur_cropped);
    let (prev, cur) = if edges.lead + edges.trail > 0 {
        prev_cropped = prev.crop_axis(axis, edges.lead, raw_axis_len - edges.trail);
        cur_cropped = cur.crop_axis(axis, edges.lead, raw_axis_len - edges.trail);
        (&prev_cropped, &cur_cropped)
    } else {
        (prev, cur)
    };
    let axis_len = prev.axis_len(axis);
    if axis_len < MIN_OVERLAP_PIXELS + MIN_MOTION_PIXELS {
        return result;
    }
    let min_overlap = (axis_len / MIN_OVERLAP_DEN)
        .max(MIN_OVERLAP_PIXELS)
        .min(axis_len.saturating_sub(1));
    let max_shift = axis_len.saturating_sub(min_overlap);
    result.search_max_delta = Some(max_shift as i32);
    let min_shift = MIN_MOTION_PIXELS + 1;
    if min_shift > max_shift {
        return result;
    }
    let fine_axis = (axis_len / 200).max(1);
    let fine_cross = (cross_len / 160).max(1);

    let prev_sig = AxisSignature::build(prev, axis);
    let cur_sig = AxisSignature::build(cur, axis);
    let mut coarse: Vec<(isize, f64)> = (min_shift as isize..=max_shift as isize)
        .map(|s| (s, prev_sig.score_shift(&cur_sig, s, max_shift)))
        .collect();
    coarse.sort_by(|a, b| a.1.total_cmp(&b.1));
    let Some(&(_, coarse_best)) = coarse.first() else {
        return result;
    };
    if !coarse_best.is_finite() || coarse_best > MAX_MATCH_ERROR {
        return result;
    }
    let coarse_limit = (coarse_best * MIN_CONFIDENCE)
        .max(coarse_best + MIN_ERROR_MARGIN)
        .min(MAX_MATCH_ERROR);
    let neighborhood = (axis_len as isize / 256).clamp(6, 16);
    let mut peaks: Vec<(isize, f64)> = Vec::new();
    for &(s, err) in &coarse {
        if err > coarse_limit {
            break;
        }
        if peaks.iter().any(|&(ps, _)| (ps - s).abs() <= neighborhood) {
            continue;
        }
        if peaks.len() == MAX_FORWARD_CANDIDATES {
            result.truncated = true;
            break;
        }
        peaks.push((s, err));
    }

    let mut refined: Vec<ForwardMatchCandidate> = Vec::with_capacity(peaks.len());
    for (peak_s, _) in peaks {
        let mut best = (peak_s, f64::INFINITY);
        for s in (peak_s - 1).max(min_shift as isize)..=(peak_s + 1).min(max_shift as isize) {
            let e = score_shift(prev, cur, axis, s, max_shift, fine_axis, fine_cross);
            if e < best.1 {
                best = (s, e);
            }
        }
        let delta = best.0.unsigned_abs() as i32;
        if (delta as usize) <= MIN_MOTION_PIXELS || best.1 > MAX_MATCH_ERROR {
            continue;
        }
        if let Some(ex) = refined
            .iter_mut()
            .find(|c| (c.delta as isize - delta as isize).abs() <= neighborhood)
        {
            if best.1 < ex.error {
                *ex = ForwardMatchCandidate {
                    delta,
                    error: best.1,
                };
            }
        } else {
            refined.push(ForwardMatchCandidate {
                delta,
                error: best.1,
            });
        }
    }
    refined.sort_by(|a, b| a.error.total_cmp(&b.error).then(a.delta.cmp(&b.delta)));
    if let Some(&best) = refined.first() {
        let keep_limit = (best.error * 2.5)
            .max(best.error + 4.0)
            .min(MAX_MATCH_ERROR);
        refined.retain(|&c| {
            c == best || c.error <= keep_limit || !errors_are_distinct(best.error, c.error)
        });
    }
    result.candidates = refined;
    result
}

#[derive(Clone, Debug)]
pub struct ForwardLookahead {
    origin: GrayView,
    pending: GrayView,
    axis: Axis,
    first: ForwardCandidateSet,
    estimate: MotionEstimate,
}

impl ForwardLookahead {
    pub fn estimate(&self) -> MotionEstimate {
        self.estimate
    }

    pub fn unique_candidate_at_most(&self, max_source_delta: i32) -> Option<ForwardMatchCandidate> {
        if self.first.truncated {
            return None;
        }
        let mut found = None;
        for &c in &self.first.candidates {
            if c.delta <= max_source_delta {
                if found.is_some() {
                    return None;
                }
                found = Some(c);
            }
        }
        found
    }

    pub fn resolve_auto(
        &self,
        lookahead: &GrayView,
        expected_ratio: f64,
    ) -> ForwardLookaheadResolution {
        let probe = classify_motion_directed(
            &self.pending,
            lookahead,
            self.axis,
            SearchDirection::ForwardOnly,
            None,
        );
        if probe.motion.kind == MotionKind::Stationary {
            let best = self.first.candidates.first().copied();
            let tag = if !self.first.truncated && self.first.candidates.len() == 1 {
                StationaryProbeTag::Unique
            } else {
                StationaryProbeTag::Ambiguous
            };
            return ForwardLookaheadResolution {
                tag: LookaheadTag::StationaryProbe,
                path: None,
                first_match: Some(StationaryProbeFirstMatch {
                    tag,
                    candidate: best,
                }),
            };
        }
        let second = forward_candidate_set(&self.pending, lookahead, self.axis);
        let cumulative = forward_candidate_set(&self.origin, lookahead, self.axis);
        resolve_forward_candidate_sets(&self.first, &second, &cumulative, Some(expected_ratio))
    }
}

pub fn resolve_forward_candidate_sets(
    first: &ForwardCandidateSet,
    second: &ForwardCandidateSet,
    cumulative: &ForwardCandidateSet,
    expected_ratio: Option<f64>,
) -> ForwardLookaheadResolution {
    let consistent = |f: &ForwardCandidateSet,
                      s: &ForwardCandidateSet,
                      c_set: &ForwardCandidateSet|
     -> Vec<ForwardMatchPath> {
        let mut paths = Vec::new();
        for &a in &f.candidates {
            for &b in &s.candidates {
                let sum = a.delta as isize + b.delta as isize;
                let mut best_c: Option<ForwardMatchCandidate> = None;
                for &c in &c_set.candidates {
                    if (c.delta as isize - sum).abs() <= PATH_DELTA_TOLERANCE
                        && best_c.is_none_or(|prev| c.error < prev.error)
                    {
                        best_c = Some(c);
                    }
                }
                if let Some(tot) = best_c {
                    let consistency_err = (tot.delta as isize - sum).abs() as f64;
                    paths.push(ForwardMatchPath {
                        first_delta: a.delta,
                        second_delta: b.delta,
                        total_error: a.error + b.error + tot.error + consistency_err,
                    });
                }
            }
        }
        paths
    };

    let mut paths = consistent(first, second, cumulative);
    paths.sort_by(|a, b| {
        a.total_error
            .total_cmp(&b.total_error)
            .then(a.first_delta.cmp(&b.first_delta))
            .then(a.second_delta.cmp(&b.second_delta))
    });
    let best_consistent = paths.first().copied();
    let fallback = best_consistent.or_else(|| {
        let a = first.candidates.first()?;
        let b = second.candidates.first()?;
        Some(ForwardMatchPath {
            first_delta: a.delta,
            second_delta: b.delta,
            total_error: a.error + b.error,
        })
    });

    let target_ratio = expected_ratio.unwrap_or(NOTCHES_PER_TICK as f64 / PROBE_NOTCHES as f64);
    let select_by_cadence = |mut candidates: Vec<ForwardMatchPath>| -> Option<ForwardMatchPath> {
        candidates.sort_by(|a, b| {
            a.total_error
                .total_cmp(&b.total_error)
                .then(a.first_delta.cmp(&b.first_delta))
                .then(a.second_delta.cmp(&b.second_delta))
        });
        let best_err = candidates.first()?.total_error;
        candidates.retain(|p| !errors_are_distinct(best_err, p.total_error));
        let ratio_err = |p: &ForwardMatchPath| {
            let r = p.first_delta as f64 / (p.second_delta.max(1) as f64);
            (r - target_ratio).abs()
        };
        candidates.sort_by(|a, b| {
            ratio_err(a)
                .total_cmp(&ratio_err(b))
                .then_with(|| a.total_error.total_cmp(&b.total_error))
                .then(a.first_delta.cmp(&b.first_delta))
        });
        candidates.first().copied()
    };

    let Some(best) = best_consistent else {
        if expected_ratio.is_some()
            && let Some(max_d) = cumulative.search_max_delta
        {
            let mut unverifiable = Vec::new();
            for &a in &first.candidates {
                if a.error <= MAX_MATCH_ERROR {
                    for &b in &second.candidates {
                        if b.error <= MAX_MATCH_ERROR && a.delta + b.delta > max_d {
                            unverifiable.push(ForwardMatchPath {
                                first_delta: a.delta,
                                second_delta: b.delta,
                                total_error: a.error + b.error,
                            });
                        }
                    }
                }
            }
            if let Some(p) = select_by_cadence(unverifiable) {
                return ForwardLookaheadResolution {
                    tag: LookaheadTag::LowErrorPeriodic,
                    path: Some(p),
                    first_match: None,
                };
            }
        }
        return ForwardLookaheadResolution {
            tag: LookaheadTag::Unresolved,
            path: fallback,
            first_match: None,
        };
    };

    let unique = paths.len() < 2 || errors_are_distinct(best.total_error, paths[1].total_error);
    if unique && !first.truncated && !second.truncated && !cumulative.truncated {
        return ForwardLookaheadResolution {
            tag: LookaheadTag::Resolved,
            path: Some(best),
            first_match: None,
        };
    }
    if expected_ratio.is_some()
        && let Some(p) = select_by_cadence(paths)
    {
        return ForwardLookaheadResolution {
            tag: LookaheadTag::LowErrorPeriodic,
            path: Some(p),
            first_match: None,
        };
    }
    ForwardLookaheadResolution {
        tag: LookaheadTag::Unresolved,
        path: Some(best),
        first_match: None,
    }
}

pub enum ForwardMatch {
    Classified(MotionEstimate),
    Ambiguous(ForwardLookahead),
}

pub fn classify_forward_with_lookahead(
    prev: &GrayView,
    cur: &GrayView,
    axis: Axis,
) -> ForwardMatch {
    let estimate =
        classify_motion_directed(prev, cur, axis, SearchDirection::ForwardOnly, None);
    let matcher_grade_ambiguity = estimate.motion.kind == MotionKind::Unmatchable
        && estimate.error.is_finite()
        && estimate.error <= MAX_MATCH_ERROR;
    let needs_lookahead =
        matches!(estimate.motion.kind, MotionKind::Ambiguous) || matcher_grade_ambiguity;
    if !needs_lookahead {
        return ForwardMatch::Classified(estimate);
    }
    let first = forward_candidate_set(prev, cur, axis);
    if first
        .candidates
        .first()
        .is_none_or(|c| c.error > MAX_MATCH_ERROR)
    {
        return ForwardMatch::Classified(estimate);
    }
    ForwardMatch::Ambiguous(ForwardLookahead {
        origin: prev.clone(),
        pending: cur.clone(),
        axis,
        first,
        estimate,
    })
}

#[derive(Clone)]
struct TailBand {
    delta: usize,
    source_axis_start: usize,
    rgba: Vec<u8>,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Direction {
    None,
    Forward,
    Reverse,
}

#[derive(Clone)]
pub struct StitchAccumulator {
    axis: Axis,
    direction: Direction,
    width: usize,
    height: usize,
    axis_len: usize,
    cross_len: usize,
    max_edge: usize,
    first_rgba: Vec<u8>,
    bands: Vec<TailBand>,
    total_delta: usize,
    lead_edge_sums: Vec<u64>,
    lead_edge_counts: Vec<u64>,
    trail_edge_sums: Vec<u64>,
    trail_edge_counts: Vec<u64>,
    aligned_sums: Vec<u64>,
    aligned_counts: Vec<u64>,
}

fn reflect_rgba_in_place(pixels: &mut [u8], width: usize, height: usize, axis: Axis) {
    let row_bytes = width * 4;
    match axis {
        Axis::Vertical => {
            for top in 0..height / 2 {
                let bottom = height - 1 - top;
                let (head, tail) = pixels.split_at_mut(bottom * row_bytes);
                head[top * row_bytes..(top + 1) * row_bytes]
                    .swap_with_slice(&mut tail[..row_bytes]);
            }
        }
        Axis::Horizontal => {
            for row in pixels.chunks_exact_mut(row_bytes) {
                for left in 0..width / 2 {
                    let right = width - 1 - left;
                    for c in 0..4 {
                        row.swap(left * 4 + c, right * 4 + c);
                    }
                }
            }
        }
    }
}

impl StitchAccumulator {
    pub fn new(first: RgbaImage, axis: Axis) -> Result<Self> {
        let width = first.width() as usize;
        let height = first.height() as usize;
        if width == 0 || height == 0 {
            return Err("Scroll frame has invalid dimensions".into());
        }
        let axis_len = if axis == Axis::Vertical {
            height
        } else {
            width
        };
        let cross_len = if axis == Axis::Vertical {
            width
        } else {
            height
        };
        let max_edge = (axis_len / MAX_STATIONARY_SCORING_EDGE_DEN).min(MAX_STATIONARY_EDGE);
        Ok(Self {
            axis,
            direction: Direction::None,
            width,
            height,
            axis_len,
            cross_len,
            max_edge,
            first_rgba: first.into_raw(),
            bands: Vec::new(),
            total_delta: 0,
            lead_edge_sums: vec![0; max_edge],
            lead_edge_counts: vec![0; max_edge],
            trail_edge_sums: vec![0; max_edge],
            trail_edge_counts: vec![0; max_edge],
            aligned_sums: vec![0; max_edge],
            aligned_counts: vec![0; max_edge],
        })
    }

    pub fn direction(&self) -> Direction {
        self.direction
    }

    pub fn frame_count(&self) -> usize {
        self.bands.len() + 1
    }

    pub fn current_extent(&self) -> usize {
        self.axis_len + self.total_delta
    }

    pub fn would_exceed_budget(&self, delta: usize) -> bool {
        self.cross_len.saturating_mul(self.axis_len + self.total_delta + delta)
            > MAX_STITCHED_PIXELS
    }

    pub fn push_forward(&mut self, frame: &RgbaImage, delta: usize) -> Result<()> {
        if self.direction == Direction::Reverse {
            return Err("Cannot mix forward and reverse frames".into());
        }
        self.push_oriented(
            frame.as_raw(),
            frame.width() as usize,
            frame.height() as usize,
            delta,
        )?;
        self.direction = Direction::Forward;
        Ok(())
    }

    pub fn push_reverse(&mut self, frame: &RgbaImage, delta: usize) -> Result<()> {
        if self.direction == Direction::Forward {
            return Err("Cannot mix forward and reverse frames".into());
        }
        let mut reflected = frame.as_raw().clone();
        reflect_rgba_in_place(
            &mut reflected,
            frame.width() as usize,
            frame.height() as usize,
            self.axis,
        );
        let first_rev = self.direction == Direction::None;
        if first_rev {
            reflect_rgba_in_place(&mut self.first_rgba, self.width, self.height, self.axis);
        }
        if let Err(e) = self.push_oriented(
            &reflected,
            frame.width() as usize,
            frame.height() as usize,
            delta,
        ) {
            if first_rev {
                reflect_rgba_in_place(&mut self.first_rgba, self.width, self.height, self.axis);
            }
            return Err(e);
        }
        self.direction = Direction::Reverse;
        Ok(())
    }

    fn push_oriented(&mut self, bits: &[u8], w: usize, h: usize, delta: usize) -> Result<()> {
        if w != self.width || h != self.height {
            return Err("Scroll frame dimensions changed".into());
        }
        if delta == 0 || delta >= self.axis_len {
            return Err(format!("Invalid stitch delta {delta}").into());
        }
        if self.would_exceed_budget(delta) {
            return Err("Scroll capture reached its memory budget".into());
        }

        let retained_extent = delta + self.max_edge;
        let source_axis_start = self.axis_len.saturating_sub(retained_extent);

        // Exclude outer cross margins (scrollbars / window borders) from stationary strip detection.
        let margin = cross_margin(self.cross_len);
        let cross_end = self
            .cross_len
            .saturating_sub(margin)
            .max(margin + 1)
            .min(self.cross_len);
        let cross_step = ((cross_end - margin) / 256).max(1);

        let rgba = match self.axis {
            Axis::Vertical => bits[source_axis_start * self.width * 4..].to_vec(),
            Axis::Horizontal => {
                let bw = self.width - source_axis_start;
                let mut v = Vec::with_capacity(bw * 4 * self.height);
                for y in 0..self.height {
                    let start = (y * self.width + source_axis_start) * 4;
                    v.extend_from_slice(&bits[start..start + bw * 4]);
                }
                v
            }
        };

        for depth in 0..self.max_edge {
            let lead_pos = depth;
            let trail_pos = self.axis_len - depth - 1;
            for cross in (margin..cross_end).step_by(cross_step) {
                let (lx, ly, tx, ty) = if self.axis == Axis::Vertical {
                    (cross, lead_pos, cross, trail_pos)
                } else {
                    (lead_pos, cross, trail_pos, cross)
                };
                let loff = (ly * self.width + lx) * 4;
                let toff = (ty * self.width + tx) * 4;
                for c in 0..3 {
                    self.lead_edge_sums[depth] +=
                        self.first_rgba[loff + c].abs_diff(bits[loff + c]) as u64;
                    self.lead_edge_counts[depth] += 1;
                    self.trail_edge_sums[depth] +=
                        self.first_rgba[toff + c].abs_diff(bits[toff + c]) as u64;
                    self.trail_edge_counts[depth] += 1;
                }
            }
        }

        let (prev_slice, prev_start, prev_row_bytes) = if let Some(last) = self.bands.last() {
            let rb = if self.axis == Axis::Vertical {
                self.width * 4
            } else {
                (self.axis_len - last.source_axis_start) * 4
            };
            (&last.rgba[..], last.source_axis_start, rb)
        } else {
            (&self.first_rgba[..], 0, self.width * 4)
        };

        for depth in 0..self.max_edge {
            let pos = self.axis_len - depth - 1;
            if pos < delta || pos < prev_start {
                continue;
            }
            let aligned = pos - delta;
            for cross in (margin..cross_end).step_by(cross_step) {
                let (prev_off, other_off) = if self.axis == Axis::Vertical {
                    (
                        (pos - prev_start) * prev_row_bytes + cross * 4,
                        (aligned * self.width + cross) * 4,
                    )
                } else {
                    (
                        cross * prev_row_bytes + (pos - prev_start) * 4,
                        (cross * self.width + aligned) * 4,
                    )
                };
                for c in 0..3 {
                    self.aligned_sums[depth] +=
                        prev_slice[prev_off + c].abs_diff(bits[other_off + c]) as u64;
                    self.aligned_counts[depth] += 1;
                }
            }
        }

        self.bands.push(TailBand {
            delta,
            source_axis_start,
            rgba,
        });
        self.total_delta += delta;
        Ok(())
    }

    pub fn stationary_leading_strip(&self) -> usize {
        if self.bands.is_empty() {
            return 0;
        }
        scan_stationary_depth(self.max_edge, |depth| {
            if self.lead_edge_counts[depth] == 0 {
                f64::INFINITY
            } else {
                self.lead_edge_sums[depth] as f64 / self.lead_edge_counts[depth] as f64
            }
        })
    }

    pub fn stationary_trailing_strip(&self) -> usize {
        if self.bands.is_empty() {
            return 0;
        }
        scan_stationary_depth(self.max_edge, |depth| {
            if self.trail_edge_counts[depth] == 0 {
                f64::INFINITY
            } else {
                self.trail_edge_sums[depth] as f64 / self.trail_edge_counts[depth] as f64
            }
        })
    }

    pub fn near_stationary_trailing_zone(&self, strip: usize) -> usize {
        if strip == 0 || self.bands.is_empty() {
            return 0;
        }
        let limit = (strip + MAX_NEAR_STATIONARY_HALO).min(self.max_edge);
        let mut zone = 0;
        for depth in strip..limit {
            let err = if self.aligned_counts[depth] == 0 {
                0.0
            } else {
                self.aligned_sums[depth] as f64 / self.aligned_counts[depth] as f64
            };
            if err <= NEAR_STATIONARY_ERROR {
                break;
            }
            zone += 1;
        }
        zone
    }

    pub fn finish(&self) -> Result<RgbaImage> {
        let lead = self.stationary_leading_strip();
        let strip = self.stationary_trailing_strip();
        let trailing = (strip + self.near_stationary_trailing_zone(strip)).min(self.max_edge);
        let content_extent = self.axis_len.saturating_sub(trailing);
        if content_extent <= lead {
            return Err("Stationary edges cover the entire capture".into());
        }
        for band in &self.bands {
            if band.delta > content_extent.saturating_sub(lead) {
                return Err("Scroll delta exceeds non-stationary content area".into());
            }
        }

        match self.axis {
            Axis::Vertical => {
                let total_h = self.height + self.total_delta;
                let row_bytes = self.width * 4;
                let mut out = vec![0u8; total_h * row_bytes];
                let mut row_cursor = 0usize;
                let reverse = self.direction == Direction::Reverse;
                let mut emit_rows = |src: &[u8], byte_start: usize, rows: usize| {
                    for i in 0..rows {
                        let dst_row = if reverse {
                            total_h - 1 - row_cursor
                        } else {
                            row_cursor
                        };
                        let s_off = byte_start + i * row_bytes;
                        let d_off = dst_row * row_bytes;
                        out[d_off..d_off + row_bytes]
                            .copy_from_slice(&src[s_off..s_off + row_bytes]);
                        row_cursor += 1;
                    }
                };
                emit_rows(&self.first_rgba, 0, content_extent);
                for band in &self.bands {
                    let local_start = (content_extent - band.delta) - band.source_axis_start;
                    emit_rows(&band.rgba, local_start * row_bytes, band.delta);
                }
                if trailing > 0
                    && let Some(last) = self.bands.last()
                {
                    let local_start = content_extent - last.source_axis_start;
                    emit_rows(&last.rgba, local_start * row_bytes, trailing);
                }
                RgbaImage::from_raw(self.width as u32, total_h as u32, out)
                    .ok_or_else(|| "Failed to allocate stitched image".into())
            }
            Axis::Horizontal => {
                let total_w = self.width + self.total_delta;
                let mut out = vec![0u8; total_w * self.height * 4];
                for y in 0..self.height {
                    let dst_row = &mut out[y * total_w * 4..(y + 1) * total_w * 4];
                    let mut cursor = content_extent * 4;
                    let first_off = y * self.width * 4;
                    dst_row[..cursor]
                        .copy_from_slice(&self.first_rgba[first_off..first_off + cursor]);
                    for band in &self.bands {
                        let bw = self.width - band.source_axis_start;
                        let local_start = (content_extent - band.delta) - band.source_axis_start;
                        let b_off = (y * bw + local_start) * 4;
                        let len = band.delta * 4;
                        dst_row[cursor..cursor + len]
                            .copy_from_slice(&band.rgba[b_off..b_off + len]);
                        cursor += len;
                    }
                    if trailing > 0
                        && let Some(last) = self.bands.last()
                    {
                        let bw = self.width - last.source_axis_start;
                        let local_start = content_extent - last.source_axis_start;
                        let b_off = (y * bw + local_start) * 4;
                        let len = trailing * 4;
                        dst_row[cursor..cursor + len]
                            .copy_from_slice(&last.rgba[b_off..b_off + len]);
                    }
                }
                if self.direction == Direction::Reverse {
                    reflect_rgba_in_place(&mut out, total_w, self.height, Axis::Horizontal);
                }
                RgbaImage::from_raw(total_w as u32, self.height as u32, out)
                    .ok_or_else(|| "Failed to allocate stitched image".into())
            }
        }
    }
}

pub fn image_is_uniform(image: &RgbaImage) -> bool {
    let raw = image.as_raw();
    if raw.len() < 4 {
        return true;
    }
    let first = &raw[..3];
    let step = (raw.len() / 4 / 4096).max(1);
    raw.as_chunks::<4>()
        .0
        .iter()
        .step_by(step)
        .filter(|p| p[..3] != *first)
        .count()
        < 8
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ManualEvent {
    Blank,
    Seeded,
    Kept,
    Pending,
    Still,
    ReSeeded,
    Ambiguous,
    Unmatchable,
    WrongDirection,
    Error,
    Full,
}

#[derive(Clone)]
pub struct ManualCapture {
    pub(crate) axis: Axis,
    pub(crate) accumulator: Option<StitchAccumulator>,
    pub(crate) last_gray: GrayView,
    previous_gray: Option<GrayView>,
    pending: Option<(RgbaImage, usize, Direction)>,
    direction: Direction,
    blank_first_frames: usize,
    pub(crate) kept: usize,
}

impl ManualCapture {
    pub fn new(axis: Axis) -> Self {
        Self {
            axis,
            accumulator: None,
            last_gray: GrayView::default(),
            previous_gray: None,
            pending: None,
            direction: Direction::None,
            blank_first_frames: 0,
            kept: 0,
        }
    }

    pub fn from_auto(auto: AutoCapture) -> Self {
        let direction = auto
            .accumulator
            .as_ref()
            .map_or(Direction::None, |a| a.direction());
        Self {
            axis: auto.axis,
            accumulator: auto.accumulator,
            last_gray: auto.last_gray,
            previous_gray: None,
            pending: None,
            direction,
            blank_first_frames: 0,
            kept: auto.kept,
        }
    }

    pub fn is_reverse(&self) -> bool {
        self.direction == Direction::Reverse
            || self
                .accumulator
                .as_ref()
                .is_some_and(|a| a.direction() == Direction::Reverse)
    }

    pub fn has_pending(&self) -> bool {
        self.pending.is_some()
    }

    pub fn coalesce_threshold(axis_len: usize) -> usize {
        (axis_len / 20)
            .clamp(8, 32)
            .min(axis_len.saturating_sub(1).max(1))
    }

    pub fn manual_search_bound(axis_len: usize) -> usize {
        (axis_len / 8)
            .clamp(128, 512)
            .min(axis_len.saturating_sub(1).max(1))
    }

    pub fn current_extent(&self) -> usize {
        self.accumulator.as_ref().map_or(0, |a| a.current_extent())
            + self.pending.as_ref().map_or(0, |(_, d, _)| *d)
    }

    pub fn flush_pending(&mut self) -> bool {
        if let Some((frame, delta, dir)) = self.pending.take()
            && let Some(acc) = &mut self.accumulator
        {
            let res = if dir == Direction::Forward {
                acc.push_forward(&frame, delta)
            } else {
                acc.push_reverse(&frame, delta)
            };
            if res.is_ok() {
                self.last_gray = downsample_to_gray(&frame, self.axis);
                self.kept += 1;
                return true;
            }
        }
        false
    }

    #[allow(dead_code)]
    pub fn feed(&mut self, cropped: RgbaImage) -> (ManualEvent, String) {
        let gray = downsample_to_gray(&cropped, self.axis);
        self.feed_with_gray(cropped, gray)
    }

    pub fn feed_with_gray(&mut self, cropped: RgbaImage, gray: GrayView) -> (ManualEvent, String) {
        let axis_len = gray.axis_len(self.axis);
        if self.accumulator.is_none() {
            if image_is_uniform(&cropped) {
                self.blank_first_frames += 1;
                if self.blank_first_frames >= MAX_BLANK_FIRST_FRAMES {
                    return (
                        ManualEvent::Error,
                        "Capture shows only a solid color · press Esc to cancel".into(),
                    );
                }
                return (ManualEvent::Blank, "Waiting for detailed content…".into());
            }
            match StitchAccumulator::new(cropped, self.axis) {
                Ok(acc) => {
                    self.accumulator = Some(acc);
                    self.last_gray = gray.clone();
                    self.previous_gray = Some(gray);
                    self.kept = 1;
                    return (
                        ManualEvent::Seeded,
                        format!(
                            "Manual capture · 1 frame ({} px) · scroll the page or press Done (Enter)",
                            self.current_extent()
                        ),
                    );
                }
                Err(e) => return (ManualEvent::Error, e.to_string()),
            }
        }

        let at_rest = self.previous_gray.as_ref().is_some_and(|prev| {
            frame_diff_at_zero(prev, &gray, self.axis) <= STATIONARY_ERROR
        });
        self.previous_gray = Some(gray.clone());

        if at_rest && self.pending.is_some() {
            self.flush_pending();
            return (
                ManualEvent::Kept,
                format!(
                    "Capturing · {} frames ({} px) · press Done (Enter) when finished",
                    self.kept,
                    self.current_extent()
                ),
            );
        }

        let axis = self.axis;
        let classify_pair = move |prev_g: &GrayView, cur_g: &GrayView| -> MotionEstimate {
            let mut est = classify_motion(prev_g, cur_g, axis);
            if matches!(
                est.motion.kind,
                MotionKind::Ambiguous | MotionKind::Unmatchable
            ) {
                let bound = Self::manual_search_bound(axis_len);
                let bounded = classify_motion_bounded(prev_g, cur_g, axis, bound);
                let delta_abs = bounded.motion.delta.unsigned_abs() as usize;
                if delta_abs > 0
                    && delta_abs <= bound
                    && bounded.error <= MAX_STATIONARY_ERROR
                    && bounded.confidence >= 1.02
                {
                    let kind = match bounded.motion.kind {
                        MotionKind::Forward | MotionKind::Reverse => bounded.motion.kind,
                        _ if bounded.motion.delta > 0 => MotionKind::Forward,
                        _ => MotionKind::Reverse,
                    };
                    est = MotionEstimate {
                        motion: Motion {
                            kind,
                            delta: delta_abs as i32,
                        },
                        ..bounded
                    };
                }
            }
            est
        };

        let mut est = classify_pair(&self.last_gray, &gray);
        if matches!(
            est.motion.kind,
            MotionKind::Ambiguous | MotionKind::Unmatchable
        ) && self.pending.is_some()
        {
            self.flush_pending();
            est = classify_pair(&self.last_gray, &gray);
        }

        match est.motion.kind {
            MotionKind::Forward => {
                self.record_motion(cropped, gray, Direction::Forward, est.motion.delta as usize)
            }
            MotionKind::Reverse => {
                self.record_motion(cropped, gray, Direction::Reverse, est.motion.delta as usize)
            }
            MotionKind::Stationary => {
                if self.pending.is_some() {
                    self.flush_pending();
                    (
                        ManualEvent::Kept,
                        format!(
                            "Capturing · {} frames ({} px)",
                            self.kept,
                            self.current_extent()
                        ),
                    )
                } else {
                    (
                        ManualEvent::Still,
                        format!(
                            "Capturing · {} frames ({} px) · scroll the page or press Done (Enter)",
                            self.kept,
                            self.current_extent()
                        ),
                    )
                }
            }
            MotionKind::Ambiguous => {
                let delta = est.motion.delta.unsigned_abs() as usize;
                let dir = if est.motion.delta > 0 {
                    Direction::Forward
                } else {
                    Direction::Reverse
                };
                if delta > 0
                    && delta <= Self::manual_search_bound(axis_len)
                    && est.error <= MAX_AMBIGUOUS_ERROR
                    && est.confidence >= 1.02
                {
                    self.record_motion(cropped, gray, dir, delta)
                } else {
                    (
                        ManualEvent::Ambiguous,
                        "Repeated content · keep scrolling to a distinctive section".into(),
                    )
                }
            }
            MotionKind::Unmatchable => {
                if self.accumulator.as_ref().is_some_and(|a| a.frame_count() == 1)
                    && self.pending.is_none()
                    && at_rest
                    && let Ok(acc) = StitchAccumulator::new(cropped, self.axis)
                {
                    self.accumulator = Some(acc);
                    self.last_gray = gray;
                    self.direction = Direction::None;
                    return (
                        ManualEvent::ReSeeded,
                        "Restarted from current view · scroll the page".into(),
                    );
                }
                (
                    ManualEvent::Unmatchable,
                    "Cannot align frames · scroll back slightly to resume".into(),
                )
            }
        }
    }

    fn record_motion(
        &mut self,
        cropped: RgbaImage,
        gray: GrayView,
        dir: Direction,
        delta: usize,
    ) -> (ManualEvent, String) {
        let Some(acc) = &mut self.accumulator else {
            return (ManualEvent::Error, "Not initialized".into());
        };
        if self.direction != Direction::None && self.direction != dir {
            if acc.frame_count() > 1 {
                return (
                    ManualEvent::WrongDirection,
                    "Scroll the other way to continue this capture".into(),
                );
            }
            self.pending = None;
            self.direction = dir;
        } else if self.direction == Direction::None {
            self.direction = dir;
        }
        let axis_len = gray.axis_len(self.axis);
        if delta < Self::coalesce_threshold(axis_len) {
            self.pending = Some((cropped, delta, dir));
            return (
                ManualEvent::Pending,
                format!(
                    "Capturing · {} frames ({} px)",
                    self.kept,
                    self.current_extent()
                ),
            );
        }
        if acc.would_exceed_budget(delta) {
            return (
                ManualEvent::Full,
                "Reached maximum capture size · press Done (Enter)".into(),
            );
        }
        let res = if dir == Direction::Forward {
            acc.push_forward(&cropped, delta)
        } else {
            acc.push_reverse(&cropped, delta)
        };
        match res {
            Ok(()) => {
                self.last_gray = gray;
                self.pending = None;
                self.kept += 1;
                (
                    ManualEvent::Kept,
                    format!(
                        "Capturing · {} frames ({} px)",
                        self.kept,
                        self.current_extent()
                    ),
                )
            }
            Err(e) => (ManualEvent::Error, e.to_string()),
        }
    }

    pub fn finish(&mut self) -> Result<RgbaImage> {
        self.flush_pending();
        self.accumulator
            .as_ref()
            .ok_or_else(|| -> Box<dyn std::error::Error + Send + Sync> {
                "No frames were captured".into()
            })?
            .finish()
    }
}

struct HandshakeState {
    ready_cycle: u64,
    ack_cycle: u64,
    notches: i32,
    consumed: bool,
}

pub struct CaptureHandshake {
    state: Mutex<HandshakeState>,
    cv: Condvar,
}

impl Default for CaptureHandshake {
    fn default() -> Self {
        Self::new()
    }
}

impl CaptureHandshake {
    pub fn new() -> Self {
        Self {
            state: Mutex::new(HandshakeState {
                ready_cycle: 0,
                ack_cycle: 0,
                notches: NOTCHES_PER_TICK,
                consumed: true,
            }),
            cv: Condvar::new(),
        }
    }

    pub fn publish_ready(&self) -> u64 {
        let mut guard = self.state.lock().unwrap();
        guard.ready_cycle += 1;
        let cycle = guard.ready_cycle;
        self.cv.notify_all();
        cycle
    }

    pub fn wait_for_ready(&self, last_cycle: u64, stop: &AtomicBool) -> Option<u64> {
        let mut guard = self.state.lock().unwrap();
        while !stop.load(Ordering::Acquire) {
            if guard.ready_cycle > last_cycle {
                return Some(guard.ready_cycle);
            }
            let (next_guard, _) = self
                .cv
                .wait_timeout(guard, std::time::Duration::from_millis(25))
                .unwrap();
            guard = next_guard;
        }
        None
    }

    pub fn acknowledge_with_notches(&self, cycle: u64, notches: i32) {
        let mut guard = self.state.lock().unwrap();
        if guard.ack_cycle < cycle {
            guard.ack_cycle = cycle;
            guard.notches = notches;
            guard.consumed = false;
            self.cv.notify_all();
        }
    }

    pub fn notify_stop(&self) {
        let _guard = self.state.lock().unwrap();
        self.cv.notify_all();
    }

    pub fn wait_for_capture(&self, cycle: u64, stop: &AtomicBool) -> Option<i32> {
        let mut guard = self.state.lock().unwrap();
        while !stop.load(Ordering::Acquire) {
            if guard.ack_cycle == cycle && !guard.consumed {
                guard.consumed = true;
                return Some(guard.notches);
            }
            let (next_guard, _) = self
                .cv
                .wait_timeout(guard, std::time::Duration::from_millis(25))
                .unwrap();
            guard = next_guard;
        }
        None
    }
}

#[derive(Clone, Default)]
pub struct AutoStepCalibration {
    recent: Vec<i32>,
}

impl AutoStepCalibration {
    pub fn record_verified_normal_step(&mut self, delta: i32) {
        if delta > 0 {
            self.recent.push(delta);
            if self.recent.len() > 8 {
                self.recent.remove(0);
            }
        }
    }

    pub fn endpoint_upper_bound(&self) -> Option<i32> {
        if self.recent.len() < 2 {
            return None;
        }
        let min = *self.recent.iter().min()?;
        let max = *self.recent.iter().max()?;
        if max <= min * 3 / 2 + 12 {
            Some(max + (max / 5).max(8))
        } else {
            None
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum AutoAck {
    Normal,
    Probe,
    Hold,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum AutoEvent {
    Blank,
    Seeded,
    Appended,
    StillOnce,
    ReachedEnd,
    ProbeStarted,
    ProbeAgain,
    Committed,
    ReachedEndAtSeam,
    Paused,
    Halted,
}

#[derive(Clone)]
pub struct AutoOutcome {
    pub event: AutoEvent,
    pub ack: AutoAck,
    pub estimate: MotionEstimate,
    pub first_delta: i32,
    pub second_delta: i32,
    pub status: String,
}

#[derive(Clone)]
pub struct AutoCapture {
    pub(crate) axis: Axis,
    pub(crate) accumulator: Option<StitchAccumulator>,
    pub(crate) last_gray: GrayView,
    lookahead: Option<(ForwardLookahead, RgbaImage, GrayView)>,
    stationary_probes: usize,
    consecutive_no_scroll: usize,
    calibration: AutoStepCalibration,
    expected_ratio: f64,
    blank_first_frames: usize,
    pub(crate) kept: usize,
    ended: bool,
    halted: bool,
}

impl AutoCapture {
    pub fn new(axis: Axis) -> Self {
        Self {
            axis,
            accumulator: None,
            last_gray: GrayView::default(),
            lookahead: None,
            stationary_probes: 0,
            consecutive_no_scroll: 0,
            calibration: AutoStepCalibration::default(),
            expected_ratio: NOTCHES_PER_TICK as f64 / PROBE_NOTCHES as f64,
            blank_first_frames: 0,
            kept: 0,
            ended: false,
            halted: false,
        }
    }

    pub fn from_manual(mut manual: ManualCapture) -> std::result::Result<Self, ManualCapture> {
        manual.flush_pending();
        if manual.is_reverse() {
            return Err(manual);
        }
        Ok(Self {
            axis: manual.axis,
            accumulator: manual.accumulator,
            last_gray: manual.last_gray,
            lookahead: None,
            stationary_probes: 0,
            consecutive_no_scroll: 0,
            calibration: AutoStepCalibration::default(),
            expected_ratio: NOTCHES_PER_TICK as f64 / PROBE_NOTCHES as f64,
            blank_first_frames: 0,
            kept: manual.kept,
            ended: false,
            halted: false,
        })
    }

    pub fn set_expected_ratio(&mut self, ratio: f64) {
        if ratio.is_finite() && ratio > 0.0 {
            self.expected_ratio = ratio;
        }
    }

    pub fn last_gray(&self) -> Option<&GrayView> {
        self.accumulator.is_some().then_some(&self.last_gray)
    }

    pub fn current_extent(&self) -> usize {
        self.accumulator.as_ref().map_or(0, |a| a.current_extent())
    }

    pub fn is_finished_or_paused(&self) -> bool {
        self.ended || self.halted
    }

    pub fn resume(&mut self) {
        self.ended = false;
        self.halted = false;
        self.lookahead = None;
        self.stationary_probes = 0;
        self.consecutive_no_scroll = 0;
    }

    pub fn finish(&self) -> Result<RgbaImage> {
        self.accumulator
            .as_ref()
            .ok_or_else(|| -> Box<dyn std::error::Error + Send + Sync> {
                "No frames were captured".into()
            })?
            .finish()
    }

    #[allow(dead_code)]
    pub fn feed(&mut self, cropped: RgbaImage) -> AutoOutcome {
        let gray = downsample_to_gray(&cropped, self.axis);
        self.feed_with_gray(cropped, gray)
    }

    pub fn feed_with_gray(&mut self, cropped: RgbaImage, gray: GrayView) -> AutoOutcome {
        if self.accumulator.is_none() {
            if image_is_uniform(&cropped) {
                self.blank_first_frames += 1;
                if self.blank_first_frames >= MAX_BLANK_FIRST_FRAMES {
                    self.halted = true;
                    return AutoOutcome {
                        event: AutoEvent::Halted,
                        ack: AutoAck::Hold,
                        estimate: MotionEstimate::default(),
                        first_delta: 0,
                        second_delta: 0,
                        status: "Capture shows only a solid color · press Esc to cancel".into(),
                    };
                }
                return AutoOutcome {
                    event: AutoEvent::Blank,
                    ack: AutoAck::Hold,
                    estimate: MotionEstimate::default(),
                    first_delta: 0,
                    second_delta: 0,
                    status: "Waiting for content…".into(),
                };
            }
            match StitchAccumulator::new(cropped, self.axis) {
                Ok(acc) => {
                    self.accumulator = Some(acc);
                    self.last_gray = gray;
                    self.kept = 1;
                    return AutoOutcome {
                        event: AutoEvent::Seeded,
                        ack: AutoAck::Normal,
                        estimate: MotionEstimate::default(),
                        first_delta: 0,
                        second_delta: 0,
                        status: format!("Auto-scrolling · 1 frame ({} px)", self.current_extent()),
                    };
                }
                Err(e) => {
                    self.halted = true;
                    return AutoOutcome {
                        event: AutoEvent::Halted,
                        ack: AutoAck::Hold,
                        estimate: MotionEstimate::default(),
                        first_delta: 0,
                        second_delta: 0,
                        status: e.to_string(),
                    };
                }
            }
        }

        if let Some((la, f1_img, f1_gray)) = self.lookahead.take() {
            let res = la.resolve_auto(&gray, self.expected_ratio);
            match res.tag {
                LookaheadTag::Resolved | LookaheadTag::LowErrorPeriodic => {
                    let path = res.path.unwrap();
                    self.stationary_probes = 0;
                    let Some(acc) = &mut self.accumulator else {
                        unreachable!()
                    };
                    if acc.push_forward(&f1_img, path.first_delta as usize).is_ok() {
                        self.last_gray = f1_gray;
                        self.kept += 1;
                        self.calibration
                            .record_verified_normal_step(path.first_delta);
                        if acc
                            .push_forward(&cropped, path.second_delta as usize)
                            .is_ok()
                        {
                            self.last_gray = gray;
                            self.kept += 1;
                            return AutoOutcome {
                                event: AutoEvent::Committed,
                                ack: AutoAck::Normal,
                                estimate: la.estimate(),
                                first_delta: path.first_delta,
                                second_delta: path.second_delta,
                                status: format!(
                                    "Auto-scrolling · {} frames ({} px)",
                                    self.kept,
                                    self.current_extent()
                                ),
                            };
                        }
                    }
                    self.halted = true;
                    return AutoOutcome {
                        event: AutoEvent::Halted,
                        ack: AutoAck::Hold,
                        estimate: la.estimate(),
                        first_delta: 0,
                        second_delta: 0,
                        status: "Reached capture limit · press Done (Enter)".into(),
                    };
                }
                LookaheadTag::StationaryProbe => {
                    self.stationary_probes += 1;
                    if self.stationary_probes < AUTO_END_CONFIRMATION_PROBES {
                        let est = la.estimate();
                        self.lookahead = Some((la, f1_img, f1_gray));
                        return AutoOutcome {
                            event: AutoEvent::ProbeAgain,
                            ack: AutoAck::Probe,
                            estimate: est,
                            first_delta: 0,
                            second_delta: 0,
                            status: "Confirming alignment at end of page…".into(),
                        };
                    }
                    let calibrated = self
                        .calibration
                        .endpoint_upper_bound()
                        .and_then(|b| la.unique_candidate_at_most(b));
                    let cand = res
                        .first_match
                        .and_then(|m| {
                            (m.tag == StationaryProbeTag::Unique)
                                .then_some(m.candidate)
                                .flatten()
                        })
                        .or(calibrated);
                    if let Some(c) = cand
                        && let Some(acc) = &mut self.accumulator
                        && acc.push_forward(&f1_img, c.delta as usize).is_ok()
                    {
                        self.last_gray = f1_gray;
                        self.kept += 1;
                    }
                    self.ended = true;
                    return AutoOutcome {
                        event: AutoEvent::ReachedEndAtSeam,
                        ack: AutoAck::Hold,
                        estimate: la.estimate(),
                        first_delta: cand.map_or(0, |c| c.delta),
                        second_delta: 0,
                        status: format!(
                            "End reached · {} frames ({} px) · press Done (Enter)",
                            self.kept,
                            self.current_extent()
                        ),
                    };
                }
                LookaheadTag::Unresolved => {
                    if let Some(path) = res.path
                        && path.first_delta > 0
                        && path.second_delta > 0
                        && path.total_error <= MAX_MATCH_ERROR
                        && let Some(acc) = &mut self.accumulator
                        && acc.push_forward(&f1_img, path.first_delta as usize).is_ok()
                    {
                        self.last_gray = f1_gray;
                        self.kept += 1;
                        self.calibration
                            .record_verified_normal_step(path.first_delta);
                        if acc
                            .push_forward(&cropped, path.second_delta as usize)
                            .is_ok()
                        {
                            self.last_gray = gray;
                            self.kept += 1;
                            return AutoOutcome {
                                event: AutoEvent::Committed,
                                ack: AutoAck::Normal,
                                estimate: la.estimate(),
                                first_delta: path.first_delta,
                                second_delta: path.second_delta,
                                status: format!(
                                    "Auto-scrolling · {} frames ({} px)",
                                    self.kept,
                                    self.current_extent()
                                ),
                            };
                        }
                    }
                    self.ended = true;
                    return AutoOutcome {
                        event: AutoEvent::Paused,
                        ack: AutoAck::Hold,
                        estimate: la.estimate(),
                        first_delta: 0,
                        second_delta: 0,
                        status: "Paused on repeated pattern · Resume Auto, Manual, or Done (Enter)"
                            .into(),
                    };
                }
            }
        }

        match classify_forward_with_lookahead(&self.last_gray, &gray, self.axis) {
            ForwardMatch::Classified(est) => match est.motion.kind {
                MotionKind::Stationary => {
                    self.consecutive_no_scroll += 1;
                    if self.consecutive_no_scroll < AUTO_END_CONFIRMATION_PROBES {
                        AutoOutcome {
                            event: AutoEvent::StillOnce,
                            ack: AutoAck::Normal,
                            estimate: est,
                            first_delta: 0,
                            second_delta: 0,
                            status: "Confirming end of content…".into(),
                        }
                    } else {
                        self.ended = true;
                        AutoOutcome {
                            event: AutoEvent::ReachedEnd,
                            ack: AutoAck::Hold,
                            estimate: est,
                            first_delta: 0,
                            second_delta: 0,
                            status: format!(
                                "End reached · {} frames ({} px) · press Done (Enter)",
                                self.kept,
                                self.current_extent()
                            ),
                        }
                    }
                }
                MotionKind::Forward => {
                    self.consecutive_no_scroll = 0;
                    let delta = est.motion.delta as usize;
                    let Some(acc) = &mut self.accumulator else {
                        unreachable!()
                    };
                    if acc.push_forward(&cropped, delta).is_ok() {
                        self.last_gray = gray;
                        self.kept += 1;
                        self.calibration
                            .record_verified_normal_step(est.motion.delta);
                        AutoOutcome {
                            event: AutoEvent::Appended,
                            ack: AutoAck::Normal,
                            estimate: est,
                            first_delta: est.motion.delta,
                            second_delta: 0,
                            status: format!(
                                "Auto-scrolling · {} frames ({} px)",
                                self.kept,
                                self.current_extent()
                            ),
                        }
                    } else {
                        self.halted = true;
                        AutoOutcome {
                            event: AutoEvent::Halted,
                            ack: AutoAck::Hold,
                            estimate: est,
                            first_delta: 0,
                            second_delta: 0,
                            status: "Reached capture limit · press Done (Enter)".into(),
                        }
                    }
                }
                _ => {
                    self.ended = true;
                    AutoOutcome {
                        event: AutoEvent::Paused,
                        ack: AutoAck::Hold,
                        estimate: est,
                        first_delta: 0,
                        second_delta: 0,
                        status: "Auto-scroll paused: lost alignment · Manual or Done (Enter)".into(),
                    }
                }
            },
            ForwardMatch::Ambiguous(la) => {
                self.consecutive_no_scroll = 0;
                self.stationary_probes = 0;
                let est = la.estimate();
                self.lookahead = Some((la, cropped, gray));
                AutoOutcome {
                    event: AutoEvent::ProbeStarted,
                    ack: AutoAck::Probe,
                    estimate: est,
                    first_delta: 0,
                    second_delta: 0,
                    status: "Verifying scroll alignment…".into(),
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn synthetic_page(w: u32, h: u32) -> RgbaImage {
        let mut page = RgbaImage::new(w, h);
        for (x, y, p) in page.enumerate_pixels_mut() {
            *p = image::Rgba([
                ((x * 31 + y * 17) % 251) as u8,
                ((x * 13 + y * 7 + (y / 9) * 29) % 253) as u8,
                ((x * 5 + y * 11) % 255) as u8,
                255,
            ]);
        }
        page
    }

    #[test]
    fn periodic_pattern_resolves_via_three_frame_lookahead_and_dynamic_ratio() {
        let f1 = ForwardCandidateSet {
            candidates: vec![
                ForwardMatchCandidate { delta: 40, error: 0.5 },
                ForwardMatchCandidate { delta: 80, error: 0.5 },
                ForwardMatchCandidate { delta: 120, error: 0.5 },
            ],
            truncated: false,
            search_max_delta: Some(200),
        };
        let f2 = ForwardCandidateSet {
            candidates: vec![
                ForwardMatchCandidate { delta: 40, error: 0.4 },
                ForwardMatchCandidate { delta: 80, error: 0.5 },
            ],
            truncated: false,
            search_max_delta: Some(200),
        };
        let cum = ForwardCandidateSet {
            candidates: vec![ForwardMatchCandidate { delta: 160, error: 0.4 }],
            truncated: false,
            search_max_delta: Some(200),
        };
        let res = resolve_forward_candidate_sets(&f1, &f2, &cum, Some(3.0));
        assert_eq!(res.tag, LookaheadTag::LowErrorPeriodic);
        assert_eq!(res.path.unwrap().first_delta, 120);
        assert_eq!(res.path.unwrap().second_delta, 40);

        let res_one = resolve_forward_candidate_sets(&f1, &f2, &cum, Some(1.0));
        assert_eq!(res_one.path.unwrap().first_delta, 80);
        assert_eq!(res_one.path.unwrap().second_delta, 80);
    }

    #[test]
    fn horizontal_and_reverse_stitch_accumulator_roundtrip() {
        let page = synthetic_page(220, 80);
        let a = image::imageops::crop_imm(&page, 0, 0, 120, 80).to_image();
        let b = image::imageops::crop_imm(&page, 50, 0, 120, 80).to_image();
        let c = image::imageops::crop_imm(&page, 100, 0, 120, 80).to_image();

        let mut acc = StitchAccumulator::new(a.clone(), Axis::Horizontal).unwrap();
        acc.push_forward(&b, 50).unwrap();
        acc.push_forward(&c, 50).unwrap();
        assert_eq!(acc.finish().unwrap(), page);

        let mut rev = StitchAccumulator::new(c, Axis::Horizontal).unwrap();
        rev.push_reverse(&b, 50).unwrap();
        rev.push_reverse(&a, 50).unwrap();
        assert_eq!(rev.finish().unwrap(), page);
    }

    #[test]
    fn hidpi_sticky_header_footer_and_moving_scrollbar_stitch_cleanly() {
        let page = synthetic_page(320, 900);
        let header_h = 160u32;
        let footer_h = 160u32;
        let view_h = 720u32;
        let content_h = view_h - header_h - footer_h; // 400

        let viewport = |scroll_y: u32| {
            let mut frame = RgbaImage::from_pixel(320, view_h, image::Rgba([28, 32, 42, 255]));
            let content = image::imageops::crop_imm(&page, 0, scroll_y, 320, content_h).to_image();
            image::imageops::replace(&mut frame, &content, 0, header_h as i64);
            // Moving vertical scrollbar on the right edge (inside outer 1/12 cross margin).
            let thumb_y = (scroll_y / 2).min(view_h - 60);
            for y in thumb_y..thumb_y + 60 {
                for x in 312..320 {
                    frame.put_pixel(x, y, image::Rgba([220, 220, 230, 255]));
                }
            }
            frame
        };

        let mut auto = AutoCapture::new(Axis::Vertical);
        for sy in [0, 200, 400] {
            let out = auto.feed(viewport(sy));
            assert!(
                matches!(out.event, AutoEvent::Seeded | AutoEvent::Appended),
                "unexpected event {:?}: {}",
                out.event,
                out.status
            );
        }
        let stitched = auto.finish().unwrap();
        assert_eq!(stitched.dimensions(), (320, view_h + 400));
        // Inner body (excluding scrollbar column) matches ground truth page exactly.
        let body = image::imageops::crop_imm(&stitched, 0, header_h, 300, 800).to_image();
        let expected = image::imageops::crop_imm(&page, 0, 0, 300, 800).to_image();
        assert_eq!(body, expected);
    }

    #[test]
    fn subpixel_noisy_frames_do_not_trigger_runaway_trailing_halo() {
        let page = synthetic_page(240, 600);
        let a = image::imageops::crop_imm(&page, 0, 0, 240, 300).to_image();
        let mut b = image::imageops::crop_imm(&page, 0, 120, 240, 300).to_image();
        // Add +/-2 subpixel noise across the whole frame (exceeding old NEAR_STATIONARY_ERROR = 1.0).
        for p in b.pixels_mut() {
            p[0] = p[0].saturating_add(2);
        }
        let mut acc = StitchAccumulator::new(a, Axis::Vertical).unwrap();
        acc.push_forward(&b, 120).unwrap();
        assert_eq!(acc.stationary_trailing_strip(), 0);
        assert_eq!(acc.near_stationary_trailing_zone(0), 0);
        assert_eq!(acc.finish().unwrap().dimensions(), (240, 420));
    }
}
