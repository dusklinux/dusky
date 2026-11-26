//! Custom Iced 0.14 widgets for the unified 6-row Month grid (with exact per-row
//! height measurement, dual-budget lane visibility, direct-rendered `+N more` triggers,
//! and cross-row multi-day drag creation), Week all-day banner strip (with horizontal
//! drag creation), and 24-hour Week time grid (with 30-minute drag snapping, overlapping
//! column placement, and live current-time line).
//!
//! Uses `crate::renderer::Renderer` directly via `fill_quad` / `fill_text` + native child
//! `Element` layout, requiring zero `canvas`/`lyon` dependencies and preserving crisp
//! fractional-scale rendering.

use crate::model::format_clock_time;
use crate::renderer::Renderer;
use crate::theme::{AppTheme, mix};
use crate::ui::style;
use crate::ui::Element;
use chrono::{NaiveDate, NaiveTime};
use iced_core::alignment;
use iced_core::text::{self, Renderer as _};
use iced_core::widget::tree::{self, Tree};
use iced_core::{
    Background, Border, Clipboard, Color, Event, Layout, Length, Pixels, Point, Rectangle, Shell,
    Size, Theme, Vector, Widget, keyboard, layout, mouse, overlay, renderer, window,
};
use std::time::{Duration, Instant};

pub const MONTH_WEEKNUM_GUTTER: f32 = 26.0;
pub const MONTH_CELL_HEADER_H: f32 = 27.0;
pub const MONTH_PILL_H: f32 = 21.0;
pub const MONTH_PILL_GAP: f32 = 2.0;
pub const MONTH_OVERFLOW_H: f32 = 18.0;
/// Generous upper lane ceiling packed by `build_month_layout`; `MonthGrid::layout`
/// hides lanes that exceed each row's exact measured height dynamically.
pub const MONTH_MAX_PACK_LANES: usize = 12;

pub const WEEK_HOUR_GUTTER: f32 = 56.0;
pub const WEEK_ALL_DAY_LANE_H: f32 = 22.0;
pub const WEEK_ALL_DAY_GAP: f32 = 3.0;

const DOUBLE_CLICK_WINDOW: Duration = Duration::from_millis(360);

fn format_minutes_clock(total_minutes: u32, use_24h: bool) -> String {
    let clamped = total_minutes.min(1439);
    let h = clamped / 60;
    let m = clamped % 60;
    let time = NaiveTime::from_hms_opt(h, m, 0).expect("clamped minutes form a valid time");
    format_clock_time(time, use_24h)
}

fn lanes_fitting(usable_height: f32) -> usize {
    if usable_height < MONTH_PILL_H {
        0
    } else {
        ((usable_height + MONTH_PILL_GAP) / (MONTH_PILL_H + MONTH_PILL_GAP))
            .floor()
            .max(0.0) as usize
    }
}

// ---------------------------------------------------------------------------
// 1. MonthGrid (unified 6-row × 7-day grid with per-row height measurement)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy)]
pub struct MonthSpanPlacement {
    pub row: usize,
    pub start_col: usize,
    pub end_col: usize,
    pub lane: usize,
    pub continues_left: bool,
    pub continues_right: bool,
}

#[derive(Debug, Clone, Copy)]
pub struct MonthCellMeta {
    pub date: NaiveDate,
    pub is_weekend: bool,
    pub is_selected: bool,
    pub is_today: bool,
    pub in_current_month: bool,
    pub base_overflow: usize,
}

#[derive(Default)]
struct MonthGridState {
    hovered_cell: Option<(usize, usize)>,
    hovered_overflow: Option<(usize, usize)>,
    drag_anchor: Option<(usize, usize)>,
    drag_current: Option<(usize, usize)>,
    last_click: Option<(NaiveDate, Instant)>,
    row_tops: [f32; 7],
    child_visible: Vec<bool>,
    overflow_counts: [[usize; 7]; 6],
}

enum MonthChildSlot {
    WeekNum(usize),
    DayHeader(usize, usize),
    Span(MonthSpanPlacement),
}

pub struct MonthGrid<'a, Message> {
    theme: AppTheme,
    show_week_number: bool,
    cells: [[MonthCellMeta; 7]; 6],
    slots: Vec<MonthChildSlot>,
    children: Vec<Element<'a, Message>>,
    on_select: Box<dyn Fn(NaiveDate) -> Message + 'a>,
    on_activate: Box<dyn Fn(NaiveDate) -> Message + 'a>,
    on_range_select: Box<dyn Fn(NaiveDate, NaiveDate) -> Message + 'a>,
    on_overflow: Box<dyn Fn(NaiveDate) -> Message + 'a>,
    on_wheel: Option<Box<dyn Fn(i32) -> Message + 'a>>,
}

impl<'a, Message> MonthGrid<'a, Message> {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        theme: AppTheme,
        show_week_number: bool,
        cells: [[MonthCellMeta; 7]; 6],
        week_nums: Vec<(usize, Element<'a, Message>)>,
        day_headers: Vec<(usize, usize, Element<'a, Message>)>,
        spans: Vec<(MonthSpanPlacement, Element<'a, Message>)>,
        on_select: impl Fn(NaiveDate) -> Message + 'a,
        on_activate: impl Fn(NaiveDate) -> Message + 'a,
        on_range_select: impl Fn(NaiveDate, NaiveDate) -> Message + 'a,
        on_overflow: impl Fn(NaiveDate) -> Message + 'a,
    ) -> Self {
        let capacity = week_nums.len() + day_headers.len() + spans.len();
        let mut slots = Vec::with_capacity(capacity);
        let mut children = Vec::with_capacity(capacity);

        if show_week_number {
            for (row, el) in week_nums {
                slots.push(MonthChildSlot::WeekNum(row));
                children.push(el);
            }
        }

        for (row, col, el) in day_headers {
            slots.push(MonthChildSlot::DayHeader(row, col));
            children.push(el);
        }

        for (placement, pill) in spans {
            slots.push(MonthChildSlot::Span(placement));
            children.push(pill);
        }

        Self {
            theme,
            show_week_number,
            cells,
            slots,
            children,
            on_select: Box::new(on_select),
            on_activate: Box::new(on_activate),
            on_range_select: Box::new(on_range_select),
            on_overflow: Box::new(on_overflow),
            on_wheel: None,
        }
    }

    pub fn on_wheel(mut self, f: impl Fn(i32) -> Message + 'a) -> Self {
        self.on_wheel = Some(Box::new(f));
        self
    }

    fn gutter_width(&self) -> f32 {
        if self.show_week_number {
            MONTH_WEEKNUM_GUTTER
        } else {
            0.0
        }
    }

    fn row_at_rel_y(&self, bounds: Rectangle, row_tops: &[f32; 7], rel_y: f32) -> Option<usize> {
        if rel_y < 0.0 || rel_y >= bounds.height {
            return None;
        }
        if row_tops[6] > 0.0 {
            for r in 0..6 {
                if rel_y >= row_tops[r] && rel_y < row_tops[r + 1] {
                    return Some(r);
                }
            }
            return Some(5);
        }
        let r = ((rel_y / bounds.height.max(1.0)) * 6.0).floor() as isize;
        (0..6).contains(&r).then_some(r as usize)
    }

    fn cell_at_window_pos(
        &self,
        bounds: Rectangle,
        row_tops: &[f32; 7],
        window_pos: Point,
        clamp_to_bounds: bool,
    ) -> Option<(usize, usize)> {
        let gutter = self.gutter_width();
        let grid_w = (bounds.width - gutter).max(1.0);
        let grid_h = bounds.height.max(1.0);

        let (rel_x, rel_y) = if clamp_to_bounds {
            (
                (window_pos.x - bounds.x - gutter).clamp(0.0, (grid_w - 0.5).max(0.0)),
                (window_pos.y - bounds.y).clamp(0.0, (grid_h - 0.5).max(0.0)),
            )
        } else {
            let rx = window_pos.x - bounds.x - gutter;
            let ry = window_pos.y - bounds.y;
            if rx < 0.0 || rx >= grid_w || ry < 0.0 || ry >= grid_h {
                return None;
            }
            (rx, ry)
        };

        let col = ((rel_x / grid_w) * 7.0).floor().clamp(0.0, 6.0) as usize;
        let row = self.row_at_rel_y(bounds, row_tops, rel_y)?;
        Some((row, col))
    }

    fn overflow_rect(
        &self,
        bounds: Rectangle,
        row_tops: &[f32; 7],
        row: usize,
        col: usize,
    ) -> Rectangle {
        let gutter = self.gutter_width();
        let col_w = ((bounds.width - gutter) / 7.0).max(1.0);
        let row_top = bounds.y + row_tops[row];
        let row_bottom = bounds.y + row_tops[row + 1];
        let y = (row_bottom - MONTH_OVERFLOW_H - 2.0).max(row_top + MONTH_CELL_HEADER_H);
        Rectangle {
            x: bounds.x + gutter + (col as f32) * col_w + 4.0,
            y,
            width: (col_w - 8.0).max(1.0),
            height: MONTH_OVERFLOW_H,
        }
    }

    fn overflow_at_window_pos(
        &self,
        bounds: Rectangle,
        state: &MonthGridState,
        window_pos: Point,
    ) -> Option<(usize, usize)> {
        let (row, col) = self.cell_at_window_pos(bounds, &state.row_tops, window_pos, false)?;
        if state.overflow_counts[row][col] == 0 {
            return None;
        }
        let rect = self.overflow_rect(bounds, &state.row_tops, row, col);
        rect.contains(window_pos).then_some((row, col))
    }

    fn drag_range_indices(state: &MonthGridState) -> Option<(usize, usize)> {
        let (a, b) = (state.drag_anchor?, state.drag_current?);
        if a == b {
            return None;
        }
        let ia = a.0 * 7 + a.1;
        let ib = b.0 * 7 + b.1;
        Some((ia.min(ib), ia.max(ib)))
    }
}

impl<'a, Message> Widget<Message, Theme, Renderer> for MonthGrid<'a, Message> {
    fn tag(&self) -> tree::Tag {
        tree::Tag::of::<MonthGridState>()
    }

    fn state(&self) -> tree::State {
        tree::State::new(MonthGridState::default())
    }

    fn children(&self) -> Vec<Tree> {
        self.children.iter().map(Tree::new).collect()
    }

    fn diff(&self, tree: &mut Tree) {
        tree.diff_children(&self.children);
    }

    fn size(&self) -> Size<Length> {
        Size::new(Length::Fill, Length::Fill)
    }

    fn layout(
        &mut self,
        tree: &mut Tree,
        renderer: &Renderer,
        limits: &layout::Limits,
    ) -> layout::Node {
        let max = limits.max();
        let width = if max.width.is_finite() {
            max.width
        } else {
            700.0
        };
        let height = if max.height.is_finite() {
            max.height.max(300.0)
        } else {
            540.0
        };

        let gutter = self.gutter_width();
        let col_w = ((width - gutter) / 7.0).max(1.0);
        let state = tree.state.downcast_mut::<MonthGridState>();

        // Pixel-aligned cumulative row boundaries prevent accumulated rounding drift.
        for r in 0..=6 {
            state.row_tops[r] = ((height * (r as f32)) / 6.0).round();
        }
        state.child_visible.clear();
        state.child_visible.resize(self.children.len(), true);
        state.overflow_counts = [[0; 7]; 6];

        // Collect span child indices per row for dual-budget visibility evaluation.
        let mut row_spans: [Vec<(usize, MonthSpanPlacement)>; 6] = std::array::from_fn(|_| Vec::new());
        for (idx, slot) in self.slots.iter().enumerate() {
            if let MonthChildSlot::Span(p) = *slot {
                if p.row < 6 {
                    row_spans[p.row].push((idx, p));
                }
            }
        }

        // Dual-budget per-row lane evaluation:
        // Columns that fit within the full row height keep the bottom slot for an extra pill;
        // columns that overflow reserve space for `+N more`, propagated cleanly across spans.
        for row in 0..6 {
            let row_h = (state.row_tops[row + 1] - state.row_tops[row]).max(1.0);
            let full_lanes = lanes_fitting((row_h - MONTH_CELL_HEADER_H - 3.0).max(0.0));
            let reduced_lanes =
                lanes_fitting((row_h - MONTH_CELL_HEADER_H - MONTH_OVERFLOW_H - 4.0).max(0.0));

            let mut max_lane_needed = [0usize; 7];
            for &(_, p) in &row_spans[row] {
                let needed = p.lane + 1;
                for c in p.start_col..=p.end_col.min(6) {
                    max_lane_needed[c] = max_lane_needed[c].max(needed);
                }
            }

            let mut col_has_overflow = [false; 7];
            for c in 0..7 {
                col_has_overflow[c] =
                    self.cells[row][c].base_overflow > 0 || max_lane_needed[c] > full_lanes;
            }

            loop {
                let mut changed = false;
                for &(_, p) in &row_spans[row] {
                    let fits = (p.start_col..=p.end_col.min(6)).all(|c| {
                        let limit = if col_has_overflow[c] {
                            reduced_lanes
                        } else {
                            full_lanes
                        };
                        p.lane < limit
                    });
                    if !fits {
                        for c in p.start_col..=p.end_col.min(6) {
                            if !col_has_overflow[c] {
                                col_has_overflow[c] = true;
                                changed = true;
                            }
                        }
                    }
                }
                if !changed {
                    break;
                }
            }

            for c in 0..7 {
                state.overflow_counts[row][c] = self.cells[row][c].base_overflow;
            }

            for &(child_idx, p) in &row_spans[row] {
                let visible = (p.start_col..=p.end_col.min(6)).all(|c| {
                    let limit = if col_has_overflow[c] {
                        reduced_lanes
                    } else {
                        full_lanes
                    };
                    p.lane < limit
                });
                state.child_visible[child_idx] = visible;
                if !visible {
                    for c in p.start_col..=p.end_col.min(6) {
                        state.overflow_counts[row][c] += 1;
                    }
                }
            }
        }

        let mut nodes = Vec::with_capacity(self.children.len());
        for (idx, ((slot, child), child_tree)) in self
            .slots
            .iter()
            .zip(self.children.iter_mut())
            .zip(tree.children.iter_mut())
            .enumerate()
        {
            if !state.child_visible[idx] {
                nodes.push(layout::Node::new(Size::ZERO));
                continue;
            }

            let rect = match *slot {
                MonthChildSlot::WeekNum(row) => Rectangle {
                    x: 2.0,
                    y: state.row_tops[row] + 5.0,
                    width: (gutter - 4.0).max(1.0),
                    height: 20.0,
                },
                MonthChildSlot::DayHeader(row, col) => Rectangle {
                    x: gutter + (col as f32) * col_w + 5.0,
                    y: state.row_tops[row] + 4.0,
                    width: (col_w - 10.0).max(1.0),
                    height: MONTH_CELL_HEADER_H - 4.0,
                },
                MonthChildSlot::Span(p) => {
                    let left_pad = if p.continues_left { 0.0 } else { 3.0 };
                    let right_pad = if p.continues_right { 0.0 } else { 3.0 };
                    let span_cols = (p.end_col - p.start_col + 1) as f32;
                    let x = gutter + (p.start_col as f32) * col_w + left_pad;
                    let w = (span_cols * col_w - left_pad - right_pad).max(8.0);
                    let y = state.row_tops[p.row]
                        + MONTH_CELL_HEADER_H
                        + (p.lane as f32) * (MONTH_PILL_H + MONTH_PILL_GAP);
                    Rectangle {
                        x,
                        y,
                        width: w,
                        height: MONTH_PILL_H,
                    }
                }
            };

            let child_limits = layout::Limits::new(Size::ZERO, rect.size());
            let node = child
                .as_widget_mut()
                .layout(child_tree, renderer, &child_limits)
                .move_to(Point::new(rect.x, rect.y));
            nodes.push(node);
        }

        layout::Node::with_children(Size::new(width, height), nodes)
    }

    fn update(
        &mut self,
        tree: &mut Tree,
        event: &Event,
        layout: Layout<'_>,
        cursor: mouse::Cursor,
        renderer: &Renderer,
        clipboard: &mut dyn Clipboard,
        shell: &mut Shell<'_, Message>,
        viewport: &Rectangle,
    ) {
        let bounds = layout.bounds();

        // Handle active drag updates / cancellation / completion before child dispatch
        // so dragging over event pills never drops or steals the drag gesture.
        let is_dragging = tree.state.downcast_ref::<MonthGridState>().drag_anchor.is_some();
        if is_dragging {
            let state = tree.state.downcast_mut::<MonthGridState>();
            match event {
                Event::Mouse(mouse::Event::CursorMoved { .. }) => {
                    if let Some(win_pos) = cursor.position() {
                        if let Some(cell) =
                            self.cell_at_window_pos(bounds, &state.row_tops, win_pos, true)
                        {
                            if state.drag_current != Some(cell) {
                                state.drag_current = Some(cell);
                                shell.request_redraw();
                            }
                        }
                    }
                    return;
                }
                Event::Mouse(mouse::Event::ButtonReleased(mouse::Button::Left)) => {
                    if let Some(anchor) = state.drag_anchor.take() {
                        let end = state.drag_current.take().unwrap_or(anchor);
                        shell.request_redraw();
                        shell.capture_event();

                        if anchor != end {
                            let d1 = self.cells[anchor.0][anchor.1].date;
                            let d2 = self.cells[end.0][end.1].date;
                            let (start_date, end_date) = if d1 <= d2 { (d1, d2) } else { (d2, d1) };
                            state.last_click = None;
                            shell.publish((self.on_range_select)(start_date, end_date));
                        } else {
                            let clicked_date = self.cells[anchor.0][anchor.1].date;
                            let now = Instant::now();
                            let is_double = state.last_click.is_some_and(|(prev, at)| {
                                prev == clicked_date
                                    && now.saturating_duration_since(at) <= DOUBLE_CLICK_WINDOW
                            });
                            if is_double {
                                state.last_click = None;
                                shell.publish((self.on_activate)(clicked_date));
                            } else {
                                state.last_click = Some((clicked_date, now));
                                shell.publish((self.on_select)(clicked_date));
                            }
                        }
                    }
                    return;
                }
                Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Right))
                | Event::Keyboard(keyboard::Event::KeyPressed {
                    key: keyboard::Key::Named(keyboard::key::Named::Escape),
                    ..
                }) => {
                    state.drag_anchor = None;
                    state.drag_current = None;
                    shell.capture_event();
                    shell.request_redraw();
                    return;
                }
                Event::Window(window::Event::Unfocused) => {
                    state.drag_anchor = None;
                    state.drag_current = None;
                    shell.request_redraw();
                    return;
                }
                _ => {}
            }
        }

        // Dispatch to visible interactive children (spans before day headers).
        let layouts: Vec<Layout<'_>> = layout.children().collect();
        for idx in (0..self.children.len()).rev() {
            let visible = tree
                .state
                .downcast_ref::<MonthGridState>()
                .child_visible
                .get(idx)
                .copied()
                .unwrap_or(true);
            if !visible {
                continue;
            }
            self.children[idx].as_widget_mut().update(
                &mut tree.children[idx],
                event,
                layouts[idx],
                cursor,
                renderer,
                clipboard,
                shell,
                viewport,
            );
            if shell.is_event_captured() {
                return;
            }
        }

        let state = tree.state.downcast_mut::<MonthGridState>();

        match event {
            Event::Mouse(mouse::Event::CursorMoved { .. }) => {
                let next_cell = cursor
                    .position()
                    .and_then(|p| self.cell_at_window_pos(bounds, &state.row_tops, p, false));
                let next_overflow = cursor
                    .position()
                    .and_then(|p| self.overflow_at_window_pos(bounds, state, p));
                if state.hovered_cell != next_cell || state.hovered_overflow != next_overflow {
                    state.hovered_cell = next_cell;
                    state.hovered_overflow = next_overflow;
                    shell.request_redraw();
                }
            }
            Event::Mouse(mouse::Event::CursorLeft) => {
                if state.hovered_cell.take().is_some() || state.hovered_overflow.take().is_some() {
                    shell.request_redraw();
                }
            }
            Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Left)) => {
                if let Some(win_pos) = cursor.position() {
                    if let Some((row, col)) = self.overflow_at_window_pos(bounds, state, win_pos) {
                        state.last_click = None;
                        shell.publish((self.on_overflow)(self.cells[row][col].date));
                        shell.capture_event();
                        return;
                    }
                    if let Some(cell) =
                        self.cell_at_window_pos(bounds, &state.row_tops, win_pos, false)
                    {
                        state.drag_anchor = Some(cell);
                        state.drag_current = Some(cell);
                        shell.capture_event();
                        shell.request_redraw();
                    }
                }
            }
            Event::Mouse(mouse::Event::WheelScrolled { delta }) => {
                if cursor.is_over(bounds) {
                    if let Some(on_wheel) = &self.on_wheel {
                        let y = match delta {
                            mouse::ScrollDelta::Lines { y, .. } => *y,
                            mouse::ScrollDelta::Pixels { y, .. } => *y / 30.0,
                        };
                        if y.abs() >= 0.4 {
                            let step = if y < 0.0 { 1 } else { -1 };
                            shell.publish(on_wheel(step));
                            shell.capture_event();
                        }
                    }
                }
            }
            _ => {}
        }
    }

    fn mouse_interaction(
        &self,
        tree: &Tree,
        layout: Layout<'_>,
        cursor: mouse::Cursor,
        viewport: &Rectangle,
        renderer: &Renderer,
    ) -> mouse::Interaction {
        let state = tree.state.downcast_ref::<MonthGridState>();
        if state.drag_anchor.is_some() {
            return mouse::Interaction::Grabbing;
        }

        let layouts: Vec<Layout<'_>> = layout.children().collect();
        for idx in (0..self.children.len()).rev() {
            if !state.child_visible.get(idx).copied().unwrap_or(true) {
                continue;
            }
            let interaction = self.children[idx].as_widget().mouse_interaction(
                &tree.children[idx],
                layouts[idx],
                cursor,
                viewport,
                renderer,
            );
            if interaction != mouse::Interaction::None {
                return interaction;
            }
        }

        if let Some(win_pos) = cursor.position() {
            if self
                .overflow_at_window_pos(layout.bounds(), state, win_pos)
                .is_some()
            {
                return mouse::Interaction::Pointer;
            }
        }

        mouse::Interaction::None
    }

    fn draw(
        &self,
        tree: &Tree,
        renderer: &mut Renderer,
        theme: &Theme,
        style: &renderer::Style,
        layout: Layout<'_>,
        cursor: mouse::Cursor,
        viewport: &Rectangle,
    ) {
        use iced_core::Renderer as _;

        let bounds = layout.bounds();
        let gutter = self.gutter_width();
        let col_w = ((bounds.width - gutter) / 7.0).max(1.0);
        let state = tree.state.downcast_ref::<MonthGridState>();
        let line_color = self.theme.grid_line();
        let drag_range = Self::drag_range_indices(state);

        // Optional week-number gutter backdrop across all 6 rows.
        if self.show_week_number {
            renderer.fill_quad(
                renderer::Quad {
                    bounds: Rectangle {
                        x: bounds.x,
                        y: bounds.y,
                        width: gutter,
                        height: bounds.height,
                    },
                    ..Default::default()
                },
                Background::Color(self.theme.sidebar_bg),
            );
        }

        // Paint day cell backdrops, vertical dividers, and row hairlines.
        for row in 0..6 {
            let row_y = bounds.y + state.row_tops[row];
            let row_h = (state.row_tops[row + 1] - state.row_tops[row]).max(1.0);

            for col in 0..7 {
                let cell = self.cells[row][col];
                let x = bounds.x + gutter + (col as f32) * col_w;
                let cell_bounds = Rectangle {
                    x,
                    y: row_y,
                    width: col_w,
                    height: row_h,
                };

                let cell_idx = row * 7 + col;
                let in_drag = drag_range.is_some_and(|(lo, hi)| cell_idx >= lo && cell_idx <= hi);
                let hovered = drag_range.is_none() && state.hovered_cell == Some((row, col));

                let fill = if in_drag {
                    Some(mix(
                        self.theme.bg,
                        self.theme.accent,
                        if self.theme.is_light() { 0.24 } else { 0.22 },
                    ))
                } else if cell.is_selected {
                    Some(if hovered {
                        mix(self.theme.selected_tint(), self.theme.fg, 0.03)
                    } else {
                        self.theme.selected_tint()
                    })
                } else if hovered {
                    Some(self.theme.hover_tint())
                } else if cell.is_weekend {
                    Some(self.theme.weekend_tint())
                } else if !cell.in_current_month {
                    Some(mix(self.theme.bg, self.theme.sidebar_bg, 0.45))
                } else {
                    None
                };

                if let Some(bg_color) = fill {
                    renderer.fill_quad(
                        renderer::Quad {
                            bounds: cell_bounds,
                            ..Default::default()
                        },
                        Background::Color(bg_color),
                    );
                }

                if col > 0 || self.show_week_number {
                    renderer.fill_quad(
                        renderer::Quad {
                            bounds: Rectangle {
                                x,
                                y: row_y,
                                width: 1.0,
                                height: row_h,
                            },
                            ..Default::default()
                        },
                        Background::Color(line_color),
                    );
                }
            }

            // Bottom horizontal divider across the whole row.
            renderer.fill_quad(
                renderer::Quad {
                    bounds: Rectangle {
                        x: bounds.x,
                        y: row_y + row_h - 1.0,
                        width: bounds.width,
                        height: 1.0,
                    },
                    ..Default::default()
                },
                Background::Color(line_color),
            );
        }

        // Draw visible child widgets (week numbers, day headers, and visible spanning pills).
        for (idx, ((child, child_tree), child_layout)) in self
            .children
            .iter()
            .zip(tree.children.iter())
            .zip(layout.children())
            .enumerate()
        {
            if !state.child_visible.get(idx).copied().unwrap_or(true) {
                continue;
            }
            child.as_widget().draw(
                child_tree,
                renderer,
                theme,
                style,
                child_layout,
                cursor,
                viewport,
            );
        }

        // Draw direct-rendered `+N more` overflow triggers for each overflowing day cell.
        for row in 0..6 {
            for col in 0..7 {
                let count = state.overflow_counts[row][col];
                if count == 0 {
                    continue;
                }
                let rect = self.overflow_rect(bounds, &state.row_tops, row, col);
                let hovered =
                    drag_range.is_none() && state.hovered_overflow == Some((row, col));

                if hovered {
                    renderer.fill_quad(
                        renderer::Quad {
                            bounds: rect,
                            border: Border {
                                radius: 5.0.into(),
                                ..Default::default()
                            },
                            ..Default::default()
                        },
                        Background::Color(self.theme.card_hover),
                    );
                }

                let label = format!("+{count} more");
                let text_color = if hovered {
                    self.theme.accent
                } else {
                    self.theme.fg_muted
                };

                renderer.fill_text(
                    text::Text {
                        content: label,
                        bounds: Size::new((rect.width - 8.0).max(1.0), rect.height),
                        size: Pixels(10.5),
                        line_height: text::LineHeight::default(),
                        font: style::bold_font(),
                        align_x: text::Alignment::Default,
                        align_y: alignment::Vertical::Center,
                        shaping: text::Shaping::Basic,
                        wrapping: text::Wrapping::None,
                    },
                    Point::new(rect.x + 4.0, rect.y + rect.height * 0.5),
                    text_color,
                    rect,
                );
            }
        }

        // Draw crisp accent outline around each contiguous dragged week segment.
        if let Some((lo, hi)) = drag_range {
            for row in 0..6 {
                let row_start = row * 7;
                let row_end = row_start + 6;
                if hi < row_start || lo > row_end {
                    continue;
                }
                let seg_start_col = lo.saturating_sub(row_start).min(6);
                let seg_end_col = (hi - row_start).min(6);
                let seg_x = bounds.x + gutter + (seg_start_col as f32) * col_w + 1.0;
                let seg_w = ((seg_end_col - seg_start_col + 1) as f32 * col_w - 2.0).max(4.0);
                let seg_y = bounds.y + state.row_tops[row] + 1.0;
                let seg_h = (state.row_tops[row + 1] - state.row_tops[row] - 2.0).max(4.0);

                renderer.fill_quad(
                    renderer::Quad {
                        bounds: Rectangle {
                            x: seg_x,
                            y: seg_y,
                            width: seg_w,
                            height: seg_h,
                        },
                        border: Border {
                            color: self.theme.accent,
                            width: 1.5,
                            radius: 5.0.into(),
                        },
                        ..Default::default()
                    },
                    Background::Color(Color::TRANSPARENT),
                );
            }
        }
    }

    fn operate(
        &mut self,
        tree: &mut Tree,
        layout: Layout<'_>,
        renderer: &Renderer,
        operation: &mut dyn iced_core::widget::Operation,
    ) {
        operation.traverse(&mut |operation| {
            let visible_flags: Vec<bool> = tree
                .state
                .downcast_ref::<MonthGridState>()
                .child_visible
                .clone();
            for (idx, ((child, child_tree), child_layout)) in self
                .children
                .iter_mut()
                .zip(tree.children.iter_mut())
                .zip(layout.children())
                .enumerate()
            {
                if !visible_flags.get(idx).copied().unwrap_or(true) {
                    continue;
                }
                child
                    .as_widget_mut()
                    .operate(child_tree, child_layout, renderer, operation);
            }
        });
    }

    fn overlay<'b>(
        &'b mut self,
        tree: &'b mut Tree,
        layout: Layout<'b>,
        renderer: &Renderer,
        viewport: &Rectangle,
        translation: Vector,
    ) -> Option<overlay::Element<'b, Message, Theme, Renderer>> {
        overlay::from_children(
            &mut self.children,
            tree,
            layout,
            renderer,
            viewport,
            translation,
        )
    }
}

impl<'a, Message: 'a> From<MonthGrid<'a, Message>> for Element<'a, Message> {
    fn from(widget: MonthGrid<'a, Message>) -> Self {
        Element::new(widget)
    }
}

// ---------------------------------------------------------------------------
// 2. WeekAllDayGrid (multi-day banner row with horizontal drag creation)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy)]
pub struct WeekBannerPlacement {
    pub start_col: usize,
    pub end_col: usize,
    pub lane: usize,
    pub continues_left: bool,
    pub continues_right: bool,
}

#[derive(Default)]
struct WeekAllDayState {
    hovered_col: Option<usize>,
    drag_start_col: Option<usize>,
    drag_end_col: Option<usize>,
}

pub struct WeekAllDayGrid<'a, Message> {
    theme: AppTheme,
    visible_lanes: usize,
    placements: Vec<WeekBannerPlacement>,
    children: Vec<Element<'a, Message>>,
    on_range_select: Box<dyn Fn(usize, usize) -> Message + 'a>,
}

impl<'a, Message> WeekAllDayGrid<'a, Message> {
    pub fn new(
        theme: AppTheme,
        visible_lanes: usize,
        items: Vec<(WeekBannerPlacement, Element<'a, Message>)>,
        on_range_select: impl Fn(usize, usize) -> Message + 'a,
    ) -> Self {
        let mut placements = Vec::with_capacity(items.len());
        let mut children = Vec::with_capacity(items.len());
        for (p, el) in items {
            placements.push(p);
            children.push(el);
        }
        Self {
            theme,
            visible_lanes: visible_lanes.max(1),
            placements,
            children,
            on_range_select: Box::new(on_range_select),
        }
    }

    fn total_height(&self) -> f32 {
        (self.visible_lanes as f32) * (WEEK_ALL_DAY_LANE_H + WEEK_ALL_DAY_GAP) + 4.0
    }

    fn col_at_window_pos(
        bounds: Rectangle,
        window_pos: Point,
        clamp_to_bounds: bool,
    ) -> Option<usize> {
        let grid_w = bounds.width.max(1.0);
        let rel_x = if clamp_to_bounds {
            (window_pos.x - bounds.x).clamp(0.0, (grid_w - 0.5).max(0.0))
        } else {
            let rx = window_pos.x - bounds.x;
            let ry = window_pos.y - bounds.y;
            if rx < 0.0 || rx >= grid_w || ry < 0.0 || ry >= bounds.height {
                return None;
            }
            rx
        };
        Some(((rel_x / grid_w) * 7.0).floor().clamp(0.0, 6.0) as usize)
    }
}

impl<'a, Message> Widget<Message, Theme, Renderer> for WeekAllDayGrid<'a, Message> {
    fn tag(&self) -> tree::Tag {
        tree::Tag::of::<WeekAllDayState>()
    }

    fn state(&self) -> tree::State {
        tree::State::new(WeekAllDayState::default())
    }

    fn children(&self) -> Vec<Tree> {
        self.children.iter().map(Tree::new).collect()
    }

    fn diff(&self, tree: &mut Tree) {
        tree.diff_children(&self.children);
    }

    fn size(&self) -> Size<Length> {
        Size::new(Length::Fill, Length::Fixed(self.total_height()))
    }

    fn layout(
        &mut self,
        tree: &mut Tree,
        renderer: &Renderer,
        limits: &layout::Limits,
    ) -> layout::Node {
        let width = if limits.max().width.is_finite() {
            limits.max().width
        } else {
            700.0
        };
        let height = self.total_height();
        let col_w = (width / 7.0).max(1.0);

        let mut nodes = Vec::with_capacity(self.children.len());
        for ((p, child), child_tree) in self
            .placements
            .iter()
            .zip(self.children.iter_mut())
            .zip(tree.children.iter_mut())
        {
            let left_pad = if p.continues_left { 0.0 } else { 3.0 };
            let right_pad = if p.continues_right { 0.0 } else { 3.0 };
            let span_cols = (p.end_col - p.start_col + 1) as f32;
            let x = (p.start_col as f32) * col_w + left_pad;
            let w = (span_cols * col_w - left_pad - right_pad).max(8.0);
            let y = 2.0 + (p.lane as f32) * (WEEK_ALL_DAY_LANE_H + WEEK_ALL_DAY_GAP);

            let child_limits = layout::Limits::new(Size::ZERO, Size::new(w, WEEK_ALL_DAY_LANE_H));
            let node = child
                .as_widget_mut()
                .layout(child_tree, renderer, &child_limits)
                .move_to(Point::new(x, y));
            nodes.push(node);
        }

        layout::Node::with_children(Size::new(width, height), nodes)
    }

    fn update(
        &mut self,
        tree: &mut Tree,
        event: &Event,
        layout: Layout<'_>,
        cursor: mouse::Cursor,
        renderer: &Renderer,
        clipboard: &mut dyn Clipboard,
        shell: &mut Shell<'_, Message>,
        viewport: &Rectangle,
    ) {
        let bounds = layout.bounds();

        let is_dragging = tree
            .state
            .downcast_ref::<WeekAllDayState>()
            .drag_start_col
            .is_some();
        if is_dragging {
            let state = tree.state.downcast_mut::<WeekAllDayState>();
            match event {
                Event::Mouse(mouse::Event::CursorMoved { .. }) => {
                    if let Some(win_pos) = cursor.position() {
                        if let Some(col) = Self::col_at_window_pos(bounds, win_pos, true) {
                            if state.drag_end_col != Some(col) {
                                state.drag_end_col = Some(col);
                                shell.request_redraw();
                            }
                        }
                    }
                    return;
                }
                Event::Mouse(mouse::Event::ButtonReleased(mouse::Button::Left)) => {
                    if let Some(start) = state.drag_start_col.take() {
                        let end = state.drag_end_col.take().unwrap_or(start);
                        let (lo, hi) = if start <= end { (start, end) } else { (end, start) };
                        shell.request_redraw();
                        shell.capture_event();
                        shell.publish((self.on_range_select)(lo, hi));
                    }
                    return;
                }
                Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Right))
                | Event::Keyboard(keyboard::Event::KeyPressed {
                    key: keyboard::Key::Named(keyboard::key::Named::Escape),
                    ..
                }) => {
                    state.drag_start_col = None;
                    state.drag_end_col = None;
                    shell.capture_event();
                    shell.request_redraw();
                    return;
                }
                Event::Window(window::Event::Unfocused) => {
                    state.drag_start_col = None;
                    state.drag_end_col = None;
                    shell.request_redraw();
                    return;
                }
                _ => {}
            }
        }

        let layouts: Vec<Layout<'_>> = layout.children().collect();
        for idx in (0..self.children.len()).rev() {
            self.children[idx].as_widget_mut().update(
                &mut tree.children[idx],
                event,
                layouts[idx],
                cursor,
                renderer,
                clipboard,
                shell,
                viewport,
            );
            if shell.is_event_captured() {
                return;
            }
        }

        let state = tree.state.downcast_mut::<WeekAllDayState>();
        match event {
            Event::Mouse(mouse::Event::CursorMoved { .. }) => {
                let next_hover = cursor
                    .position()
                    .and_then(|p| Self::col_at_window_pos(bounds, p, false));
                if state.hovered_col != next_hover {
                    state.hovered_col = next_hover;
                    shell.request_redraw();
                }
            }
            Event::Mouse(mouse::Event::CursorLeft) => {
                if state.hovered_col.take().is_some() {
                    shell.request_redraw();
                }
            }
            Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Left)) => {
                if let Some(win_pos) = cursor.position() {
                    if let Some(col) = Self::col_at_window_pos(bounds, win_pos, false) {
                        state.drag_start_col = Some(col);
                        state.drag_end_col = Some(col);
                        shell.capture_event();
                        shell.request_redraw();
                    }
                }
            }
            _ => {}
        }
    }

    fn mouse_interaction(
        &self,
        tree: &Tree,
        layout: Layout<'_>,
        cursor: mouse::Cursor,
        viewport: &Rectangle,
        renderer: &Renderer,
    ) -> mouse::Interaction {
        let state = tree.state.downcast_ref::<WeekAllDayState>();
        if state.drag_start_col.is_some() {
            return mouse::Interaction::Grabbing;
        }
        let layouts: Vec<Layout<'_>> = layout.children().collect();
        for idx in (0..self.children.len()).rev() {
            let interaction = self.children[idx].as_widget().mouse_interaction(
                &tree.children[idx],
                layouts[idx],
                cursor,
                viewport,
                renderer,
            );
            if interaction != mouse::Interaction::None {
                return interaction;
            }
        }
        mouse::Interaction::None
    }

    fn draw(
        &self,
        tree: &Tree,
        renderer: &mut Renderer,
        theme: &Theme,
        style: &renderer::Style,
        layout: Layout<'_>,
        cursor: mouse::Cursor,
        viewport: &Rectangle,
    ) {
        use iced_core::Renderer as _;

        let bounds = layout.bounds();
        let col_w = (bounds.width / 7.0).max(1.0);
        let line_color = self.theme.grid_line();
        let state = tree.state.downcast_ref::<WeekAllDayState>();

        // Subtle empty-column hover highlight when not actively dragging.
        if state.drag_start_col.is_none() {
            if let Some(col) = state.hovered_col {
                let x = bounds.x + (col as f32) * col_w;
                renderer.fill_quad(
                    renderer::Quad {
                        bounds: Rectangle {
                            x: x + 1.0,
                            y: bounds.y,
                            width: (col_w - 1.0).max(1.0),
                            height: bounds.height,
                        },
                        ..Default::default()
                    },
                    Background::Color(self.theme.hover_tint()),
                );
            }
        }

        for col in 0..7 {
            let x = bounds.x + (col as f32) * col_w;
            renderer.fill_quad(
                renderer::Quad {
                    bounds: Rectangle {
                        x,
                        y: bounds.y,
                        width: 1.0,
                        height: bounds.height,
                    },
                    ..Default::default()
                },
                Background::Color(line_color),
            );
        }

        // Live horizontal drag-range highlight.
        if let (Some(s), Some(e)) = (state.drag_start_col, state.drag_end_col) {
            let lo = s.min(e);
            let hi = s.max(e);
            let x = bounds.x + (lo as f32) * col_w + 2.0;
            let w = (((hi - lo + 1) as f32) * col_w - 4.0).max(8.0);
            renderer.fill_quad(
                renderer::Quad {
                    bounds: Rectangle {
                        x,
                        y: bounds.y + 2.0,
                        width: w,
                        height: (bounds.height - 4.0).max(4.0),
                    },
                    border: Border {
                        color: self.theme.accent,
                        width: 1.5,
                        radius: 5.0.into(),
                    },
                    ..Default::default()
                },
                Background::Color(mix(
                    self.theme.sidebar_bg,
                    self.theme.accent,
                    if self.theme.is_light() { 0.26 } else { 0.24 },
                )),
            );
        }

        for ((child, child_tree), child_layout) in self
            .children
            .iter()
            .zip(tree.children.iter())
            .zip(layout.children())
        {
            child.as_widget().draw(
                child_tree,
                renderer,
                theme,
                style,
                child_layout,
                cursor,
                viewport,
            );
        }
    }

    fn overlay<'b>(
        &'b mut self,
        tree: &'b mut Tree,
        layout: Layout<'b>,
        renderer: &Renderer,
        viewport: &Rectangle,
        translation: Vector,
    ) -> Option<overlay::Element<'b, Message, Theme, Renderer>> {
        overlay::from_children(
            &mut self.children,
            tree,
            layout,
            renderer,
            viewport,
            translation,
        )
    }
}

impl<'a, Message: 'a> From<WeekAllDayGrid<'a, Message>> for Element<'a, Message> {
    fn from(widget: WeekAllDayGrid<'a, Message>) -> Self {
        Element::new(widget)
    }
}

// ---------------------------------------------------------------------------
// 3. WeekTimeGrid (24-hour time grid with 30-minute drag snapping & live label)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy)]
pub struct WeekTimedPlacement {
    pub day_col: usize,
    pub start_minutes: f32,
    pub end_minutes: f32,
    pub col: usize,
    pub col_span: usize,
    pub total_cols: usize,
}

#[derive(Default)]
struct WeekTimeState {
    /// `(day_col 0..7, half_hour_slot 0..48)`
    hovered_slot: Option<(usize, u32)>,
    /// `(locked_day_col 0..7, anchor_slot 0..48, current_slot 0..48)`
    drag: Option<(usize, u32, u32)>,
}

enum WeekChildSlot {
    HourLabel(u32),
    TimedBlock(WeekTimedPlacement),
}

pub struct WeekTimeGrid<'a, Message> {
    theme: AppTheme,
    hour_height: f32,
    use_24h: bool,
    weekend_cols: [bool; 7],
    today_col: Option<usize>,
    now_minutes: Option<f32>,
    slots: Vec<WeekChildSlot>,
    children: Vec<Element<'a, Message>>,
    on_range_select: Box<dyn Fn(usize, u32, u32) -> Message + 'a>,
}

impl<'a, Message> WeekTimeGrid<'a, Message> {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        theme: AppTheme,
        hour_height: f32,
        use_24h: bool,
        weekend_cols: [bool; 7],
        today_col: Option<usize>,
        now_minutes: Option<f32>,
        hour_labels: Vec<(u32, Element<'a, Message>)>,
        blocks: Vec<(WeekTimedPlacement, Element<'a, Message>)>,
        on_range_select: impl Fn(usize, u32, u32) -> Message + 'a,
    ) -> Self {
        let mut slots = Vec::with_capacity(hour_labels.len() + blocks.len());
        let mut children = Vec::with_capacity(hour_labels.len() + blocks.len());

        for (hour, label) in hour_labels {
            slots.push(WeekChildSlot::HourLabel(hour));
            children.push(label);
        }
        for (placement, block) in blocks {
            slots.push(WeekChildSlot::TimedBlock(placement));
            children.push(block);
        }

        Self {
            theme,
            hour_height: hour_height.clamp(40.0, 120.0),
            use_24h,
            weekend_cols,
            today_col,
            now_minutes,
            slots,
            children,
            on_range_select: Box::new(on_range_select),
        }
    }

    fn half_hour_height(&self) -> f32 {
        self.hour_height * 0.5
    }

    fn slot_at_rel(&self, bounds: Rectangle, pos: Point) -> Option<(usize, u32)> {
        let rel_x = pos.x - WEEK_HOUR_GUTTER;
        let grid_w = (bounds.width - WEEK_HOUR_GUTTER).max(1.0);
        if rel_x < 0.0 || rel_x >= grid_w || pos.y < 0.0 || pos.y >= bounds.height {
            return None;
        }
        let col = ((rel_x / grid_w) * 7.0).floor() as isize;
        let slot = (pos.y / self.half_hour_height()).floor() as isize;
        if (0..7).contains(&col) && (0..48).contains(&slot) {
            Some((col as usize, slot as u32))
        } else {
            None
        }
    }

    fn clamped_slot_for_window_y(&self, bounds: Rectangle, window_y: f32) -> u32 {
        let rel_y = (window_y - bounds.y).clamp(0.0, (bounds.height - 0.5).max(0.0));
        ((rel_y / self.half_hour_height()).floor() as isize).clamp(0, 47) as u32
    }
}

impl<'a, Message> Widget<Message, Theme, Renderer> for WeekTimeGrid<'a, Message> {
    fn tag(&self) -> tree::Tag {
        tree::Tag::of::<WeekTimeState>()
    }

    fn state(&self) -> tree::State {
        tree::State::new(WeekTimeState::default())
    }

    fn children(&self) -> Vec<Tree> {
        self.children.iter().map(Tree::new).collect()
    }

    fn diff(&self, tree: &mut Tree) {
        tree.diff_children(&self.children);
    }

    fn size(&self) -> Size<Length> {
        Size::new(Length::Fill, Length::Fixed(self.hour_height * 24.0))
    }

    fn layout(
        &mut self,
        tree: &mut Tree,
        renderer: &Renderer,
        limits: &layout::Limits,
    ) -> layout::Node {
        let width = if limits.max().width.is_finite() {
            limits.max().width
        } else {
            700.0
        };
        let total_height = self.hour_height * 24.0;
        let col_w = ((width - WEEK_HOUR_GUTTER) / 7.0).max(1.0);
        let mut nodes = Vec::with_capacity(self.children.len());

        for ((slot, child), child_tree) in self
            .slots
            .iter()
            .zip(self.children.iter_mut())
            .zip(tree.children.iter_mut())
        {
            let rect = match *slot {
                WeekChildSlot::HourLabel(hour) => {
                    let y = (hour as f32) * self.hour_height - 7.0;
                    Rectangle {
                        x: 4.0,
                        y: y.max(1.0),
                        width: WEEK_HOUR_GUTTER - 10.0,
                        height: 16.0,
                    }
                }
                WeekChildSlot::TimedBlock(b) => {
                    let total_cols = b.total_cols.max(1) as f32;
                    let slot_w = col_w / total_cols;
                    let x = WEEK_HOUR_GUTTER
                        + (b.day_col as f32) * col_w
                        + (b.col as f32) * slot_w
                        + 2.0;
                    let w = ((b.col_span as f32) * slot_w - 4.0).max(16.0);
                    let y = (b.start_minutes / 60.0) * self.hour_height + 1.0;
                    let h = (((b.end_minutes - b.start_minutes) / 60.0) * self.hour_height - 2.0)
                        .max(18.0);
                    Rectangle {
                        x,
                        y,
                        width: w,
                        height: h,
                    }
                }
            };

            let child_limits = layout::Limits::new(Size::ZERO, rect.size());
            let node = child
                .as_widget_mut()
                .layout(child_tree, renderer, &child_limits)
                .move_to(Point::new(rect.x, rect.y));
            nodes.push(node);
        }

        layout::Node::with_children(Size::new(width, total_height), nodes)
    }

    fn update(
        &mut self,
        tree: &mut Tree,
        event: &Event,
        layout: Layout<'_>,
        cursor: mouse::Cursor,
        renderer: &Renderer,
        clipboard: &mut dyn Clipboard,
        shell: &mut Shell<'_, Message>,
        viewport: &Rectangle,
    ) {
        let bounds = layout.bounds();

        let is_dragging = tree.state.downcast_ref::<WeekTimeState>().drag.is_some();
        if is_dragging {
            let state = tree.state.downcast_mut::<WeekTimeState>();
            match event {
                Event::Mouse(mouse::Event::CursorMoved { .. }) => {
                    if let (Some(win_pos), Some((_, _, current))) =
                        (cursor.position(), state.drag.as_mut())
                    {
                        let next_slot = self.clamped_slot_for_window_y(bounds, win_pos.y);
                        if *current != next_slot {
                            *current = next_slot;
                            shell.request_redraw();
                        }
                    }
                    return;
                }
                Event::Mouse(mouse::Event::ButtonReleased(mouse::Button::Left)) => {
                    if let Some((col, anchor, current)) = state.drag.take() {
                        let lo = anchor.min(current);
                        let hi = anchor.max(current);
                        let start_minutes = lo * 30;
                        let end_minutes = ((hi + 1) * 30).min(1439);
                        shell.request_redraw();
                        shell.capture_event();
                        shell.publish((self.on_range_select)(col, start_minutes, end_minutes));
                    }
                    return;
                }
                Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Right))
                | Event::Keyboard(keyboard::Event::KeyPressed {
                    key: keyboard::Key::Named(keyboard::key::Named::Escape),
                    ..
                }) => {
                    state.drag = None;
                    shell.capture_event();
                    shell.request_redraw();
                    return;
                }
                Event::Window(window::Event::Unfocused) => {
                    state.drag = None;
                    shell.request_redraw();
                    return;
                }
                _ => {}
            }
        }

        let layouts: Vec<Layout<'_>> = layout.children().collect();
        for idx in (0..self.children.len()).rev() {
            self.children[idx].as_widget_mut().update(
                &mut tree.children[idx],
                event,
                layouts[idx],
                cursor,
                renderer,
                clipboard,
                shell,
                viewport,
            );
            if shell.is_event_captured() {
                return;
            }
        }

        let state = tree.state.downcast_mut::<WeekTimeState>();

        match event {
            Event::Mouse(mouse::Event::CursorMoved { .. }) => {
                let next_hover = cursor
                    .position_in(bounds)
                    .and_then(|pos| self.slot_at_rel(bounds, pos));
                if state.hovered_slot != next_hover {
                    state.hovered_slot = next_hover;
                    shell.request_redraw();
                }
            }
            Event::Mouse(mouse::Event::CursorLeft) => {
                if state.hovered_slot.take().is_some() {
                    shell.request_redraw();
                }
            }
            Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Left)) => {
                if let Some(pos) = cursor.position_in(bounds) {
                    if let Some((col, slot)) = self.slot_at_rel(bounds, pos) {
                        state.drag = Some((col, slot, slot));
                        shell.capture_event();
                        shell.request_redraw();
                    }
                }
            }
            _ => {}
        }
    }

    fn mouse_interaction(
        &self,
        tree: &Tree,
        layout: Layout<'_>,
        cursor: mouse::Cursor,
        viewport: &Rectangle,
        renderer: &Renderer,
    ) -> mouse::Interaction {
        let state = tree.state.downcast_ref::<WeekTimeState>();
        if state.drag.is_some() {
            return mouse::Interaction::Grabbing;
        }
        let layouts: Vec<Layout<'_>> = layout.children().collect();
        for idx in (0..self.children.len()).rev() {
            let interaction = self.children[idx].as_widget().mouse_interaction(
                &tree.children[idx],
                layouts[idx],
                cursor,
                viewport,
                renderer,
            );
            if interaction != mouse::Interaction::None {
                return interaction;
            }
        }
        mouse::Interaction::None
    }

    fn draw(
        &self,
        tree: &Tree,
        renderer: &mut Renderer,
        theme: &Theme,
        style: &renderer::Style,
        layout: Layout<'_>,
        cursor: mouse::Cursor,
        viewport: &Rectangle,
    ) {
        use iced_core::Renderer as _;

        let bounds = layout.bounds();
        let col_w = ((bounds.width - WEEK_HOUR_GUTTER) / 7.0).max(1.0);
        let half_h = self.half_hour_height();
        let line = self.theme.grid_line();
        let subtle_line = self.theme.grid_line_subtle();
        let state = tree.state.downcast_ref::<WeekTimeState>();

        // Left hour gutter background.
        renderer.fill_quad(
            renderer::Quad {
                bounds: Rectangle {
                    x: bounds.x,
                    y: bounds.y,
                    width: WEEK_HOUR_GUTTER,
                    height: bounds.height,
                },
                ..Default::default()
            },
            Background::Color(self.theme.sidebar_bg),
        );

        // Day column backdrops (today subtle accent wash, weekend tint).
        for col in 0..7 {
            let x = bounds.x + WEEK_HOUR_GUTTER + (col as f32) * col_w;
            let fill = if self.today_col == Some(col) {
                Some(mix(
                    self.theme.bg,
                    self.theme.accent,
                    if self.theme.is_light() { 0.06 } else { 0.045 },
                ))
            } else if self.weekend_cols[col] {
                Some(self.theme.weekend_tint())
            } else {
                None
            };

            if let Some(color) = fill {
                renderer.fill_quad(
                    renderer::Quad {
                        bounds: Rectangle {
                            x,
                            y: bounds.y,
                            width: col_w,
                            height: bounds.height,
                        },
                        ..Default::default()
                    },
                    Background::Color(color),
                );
            }
        }

        // Hovered empty 30-minute slot preview highlight (when not actively dragging).
        if state.drag.is_none() {
            if let Some((col, slot)) = state.hovered_slot {
                let x = bounds.x + WEEK_HOUR_GUTTER + (col as f32) * col_w + 1.0;
                let y = bounds.y + (slot as f32) * half_h;
                renderer.fill_quad(
                    renderer::Quad {
                        bounds: Rectangle {
                            x,
                            y,
                            width: (col_w - 1.0).max(1.0),
                            height: half_h,
                        },
                        ..Default::default()
                    },
                    Background::Color(self.theme.hover_tint()),
                );
            }
        }

        // Horizontal hour and half-hour lines.
        let grid_x = bounds.x + WEEK_HOUR_GUTTER;
        let grid_w = (bounds.width - WEEK_HOUR_GUTTER).max(1.0);
        for hour in 0..24 {
            let y = bounds.y + (hour as f32) * self.hour_height;
            if hour > 0 {
                renderer.fill_quad(
                    renderer::Quad {
                        bounds: Rectangle {
                            x: grid_x,
                            y,
                            width: grid_w,
                            height: 1.0,
                        },
                        ..Default::default()
                    },
                    Background::Color(line),
                );
            }
            let half_y = y + half_h;
            renderer.fill_quad(
                renderer::Quad {
                    bounds: Rectangle {
                        x: grid_x,
                        y: half_y,
                        width: grid_w,
                        height: 1.0,
                    },
                    ..Default::default()
                },
                Background::Color(subtle_line),
            );
        }

        // Vertical day separators.
        for col in 0..7 {
            let x = bounds.x + WEEK_HOUR_GUTTER + (col as f32) * col_w;
            renderer.fill_quad(
                renderer::Quad {
                    bounds: Rectangle {
                        x,
                        y: bounds.y,
                        width: 1.0,
                        height: bounds.height,
                    },
                    ..Default::default()
                },
                Background::Color(line),
            );
        }

        // Draw hour labels & timed event blocks.
        for ((child, child_tree), child_layout) in self
            .children
            .iter()
            .zip(tree.children.iter())
            .zip(layout.children())
        {
            child.as_widget().draw(
                child_tree,
                renderer,
                theme,
                style,
                child_layout,
                cursor,
                viewport,
            );
        }

        // Live 30-minute drag selection highlight with inline time-range label.
        if let Some((col, anchor, current)) = state.drag {
            let lo = anchor.min(current);
            let hi = anchor.max(current);
            let start_mins = lo * 30;
            let end_mins = ((hi + 1) * 30).min(1439);

            let sel_rect = Rectangle {
                x: bounds.x + WEEK_HOUR_GUTTER + (col as f32) * col_w + 2.0,
                y: bounds.y + (lo as f32) * half_h + 1.0,
                width: (col_w - 4.0).max(16.0),
                height: (((hi - lo + 1) as f32) * half_h - 2.0).max(18.0),
            };

            renderer.fill_quad(
                renderer::Quad {
                    bounds: sel_rect,
                    border: Border {
                        color: self.theme.accent,
                        width: 1.5,
                        radius: 6.0.into(),
                    },
                    ..Default::default()
                },
                Background::Color(mix(
                    self.theme.bg,
                    self.theme.accent,
                    if self.theme.is_light() { 0.28 } else { 0.26 },
                )),
            );

            let range_label = format!(
                "{} – {}",
                format_minutes_clock(start_mins, self.use_24h),
                format_minutes_clock(end_mins, self.use_24h),
            );
            renderer.fill_text(
                text::Text {
                    content: range_label,
                    bounds: Size::new((sel_rect.width - 10.0).max(1.0), 16.0),
                    size: Pixels(10.5),
                    line_height: text::LineHeight::default(),
                    font: style::mono_font(),
                    align_x: text::Alignment::Default,
                    align_y: alignment::Vertical::Top,
                    shaping: text::Shaping::Basic,
                    wrapping: text::Wrapping::None,
                },
                Point::new(sel_rect.x + 5.0, sel_rect.y + 4.0),
                self.theme.fg,
                sel_rect,
            );
        }

        // Current-time indicator line + dot drawn on top of today's column.
        if let (Some(today_col), Some(now_mins)) = (self.today_col, self.now_minutes) {
            let y = bounds.y + (now_mins / 60.0) * self.hour_height;
            let x = bounds.x + WEEK_HOUR_GUTTER + (today_col as f32) * col_w;
            renderer.fill_quad(
                renderer::Quad {
                    bounds: Rectangle {
                        x: x + 1.0,
                        y: y - 1.0,
                        width: (col_w - 1.0).max(1.0),
                        height: 2.0,
                    },
                    ..Default::default()
                },
                Background::Color(self.theme.accent),
            );
            renderer.fill_quad(
                renderer::Quad {
                    bounds: Rectangle {
                        x: x - 3.5,
                        y: y - 4.0,
                        width: 8.0,
                        height: 8.0,
                    },
                    border: Border {
                        color: Color::TRANSPARENT,
                        width: 0.0,
                        radius: 4.0.into(),
                    },
                    ..Default::default()
                },
                Background::Color(self.theme.accent),
            );
        }
    }

    fn operate(
        &mut self,
        tree: &mut Tree,
        layout: Layout<'_>,
        renderer: &Renderer,
        operation: &mut dyn iced_core::widget::Operation,
    ) {
        operation.traverse(&mut |operation| {
            for ((child, child_tree), child_layout) in self
                .children
                .iter_mut()
                .zip(tree.children.iter_mut())
                .zip(layout.children())
            {
                child
                    .as_widget_mut()
                    .operate(child_tree, child_layout, renderer, operation);
            }
        });
    }

    fn overlay<'b>(
        &'b mut self,
        tree: &'b mut Tree,
        layout: Layout<'b>,
        renderer: &Renderer,
        viewport: &Rectangle,
        translation: Vector,
    ) -> Option<overlay::Element<'b, Message, Theme, Renderer>> {
        overlay::from_children(
            &mut self.children,
            tree,
            layout,
            renderer,
            viewport,
            translation,
        )
    }
}

impl<'a, Message: 'a> From<WeekTimeGrid<'a, Message>> for Element<'a, Message> {
    fn from(widget: WeekTimeGrid<'a, Message>) -> Self {
        Element::new(widget)
    }
}
