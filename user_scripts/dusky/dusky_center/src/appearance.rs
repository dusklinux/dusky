//! Deterministic easing curves and stateless presentation wrappers for subtle UI motion.
//!
//! All wrapper widgets remain permanently mounted in their layout slots so Iced's
//! widget `Tree` never churns when a transition starts or settles (preserving
//! `Scrollable` offsets, `TextInput` focus, and active pointer state). At rest,
//! each wrapper passes directly through to its child with zero extra GPU layers
//! or transformations.

use crate::renderer::Renderer;
use iced_core::{
    Clipboard, Element, Event, Layout, Length, Point, Rectangle, Renderer as _, Shell, Size,
    Theme, Transformation, Vector, Widget, layout, mouse, overlay, renderer,
    widget::{self, Tree},
};
use std::time::{Duration, Instant};

/// Monotonic ease-out quartic (`1 - (1 - t)^4`): instant response, smooth rest, zero bounce.
pub fn ease_out_quart(t: f32) -> f32 {
    let clamped = t.clamp(0.0, 1.0);
    let inv = 1.0 - clamped;
    1.0 - inv * inv * inv * inv
}

/// Retargetable scalar transition (used for sidebar width, search bar reveal, and expanders).
#[derive(Debug, Clone, Copy)]
pub struct FloatTransition {
    from: f32,
    to: f32,
    started: Instant,
    duration: Duration,
}

impl FloatTransition {
    pub fn settled(value: f32, now: Instant) -> Self {
        Self {
            from: value,
            to: value,
            started: now,
            duration: Duration::ZERO,
        }
    }

    pub fn retarget(&mut self, target: f32, now: Instant, base_duration: Duration) {
        let current = self.value(now);
        let distance = (target - current).abs();
        if distance <= f32::EPSILON {
            self.snap(target, now);
            return;
        }
        let scaled = base_duration
            .mul_f32(distance.clamp(0.0, 1.0))
            .max(Duration::from_millis(40));
        self.from = current;
        self.to = target;
        self.started = now;
        self.duration = scaled;
    }

    pub fn snap(&mut self, value: f32, now: Instant) {
        self.from = value;
        self.to = value;
        self.started = now;
        self.duration = Duration::ZERO;
    }

    pub fn target(&self) -> f32 {
        self.to
    }

    pub fn is_active(&self, now: Instant) -> bool {
        (self.from - self.to).abs() > f32::EPSILON
            && !self.duration.is_zero()
            && now.saturating_duration_since(self.started) < self.duration
    }

    pub fn value(&self, now: Instant) -> f32 {
        if !self.is_active(now) {
            return self.to;
        }
        let elapsed = now.saturating_duration_since(self.started).as_secs_f32();
        let total = self.duration.as_secs_f32().max(f32::EPSILON);
        let eased = ease_out_quart(elapsed / total);
        self.from + (self.to - self.from) * eased
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NavDirection {
    Push,
    Pop,
    PageDown,
    PageUp,
}

/// One-shot directional glide + soft fade for subpage and root page transitions.
#[derive(Debug, Clone, Copy)]
pub struct NavTransition {
    pub direction: NavDirection,
    started: Instant,
    duration: Duration,
}

impl NavTransition {
    pub fn start(direction: NavDirection, now: Instant) -> Self {
        let duration = match direction {
            NavDirection::Push | NavDirection::Pop => Duration::from_millis(180),
            NavDirection::PageDown | NavDirection::PageUp => Duration::from_millis(150),
        };
        Self {
            direction,
            started: now,
            duration,
        }
    }

    pub fn is_active(&self, now: Instant) -> bool {
        now.saturating_duration_since(self.started) < self.duration
    }

    /// Returns `(translation_offset, opacity)` at `now`.
    pub fn sample(&self, now: Instant) -> (Vector, f32) {
        if !self.is_active(now) {
            return (Vector::ZERO, 1.0);
        }
        let elapsed = now.saturating_duration_since(self.started).as_secs_f32();
        let total = self.duration.as_secs_f32().max(f32::EPSILON);
        let eased = ease_out_quart(elapsed / total);
        let remaining = 1.0 - eased;

        match self.direction {
            NavDirection::Push => (Vector::new(28.0 * remaining, 0.0), 0.15 + 0.85 * eased),
            NavDirection::Pop => (Vector::new(-28.0 * remaining, 0.0), 0.15 + 0.85 * eased),
            NavDirection::PageDown => (Vector::new(0.0, 12.0 * remaining), 0.25 + 0.75 * eased),
            NavDirection::PageUp => (Vector::new(0.0, -12.0 * remaining), 0.25 + 0.75 * eased),
        }
    }
}

/// Always-mounted directional slide + opacity wrapper.
/// Preserves exact resting layout bounds so scroll queries (`LocateSearchTarget`) stay exact.
pub fn slide_fade<'a, Message: 'a>(
    content: Element<'a, Message, Theme, Renderer>,
    offset: Vector,
    opacity: f32,
) -> Element<'a, Message, Theme, Renderer> {
    Element::new(SlideFade {
        content,
        offset,
        opacity: opacity.clamp(0.0, 1.0),
    })
}

/// Always-mounted vertical height reveal/collapse wrapper with subtle vertical settle and fade.
pub fn reveal_height<'a, Message: 'a>(
    content: Element<'a, Message, Theme, Renderer>,
    factor: f32,
    opacity: f32,
    interactive: bool,
) -> Element<'a, Message, Theme, Renderer> {
    Element::new(RevealHeight {
        content,
        factor: factor.clamp(0.0, 1.0),
        opacity: opacity.clamp(0.0, 1.0),
        interactive,
    })
}

/// Always-mounted horizontal width reveal/collapse wrapper (lays out child at full width
/// to prevent text wrapping/reflow while sliding smoothly into/out of view).
pub fn reveal_width<'a, Message: 'a>(
    content: Element<'a, Message, Theme, Renderer>,
    factor: f32,
    opacity: f32,
    interactive: bool,
) -> Element<'a, Message, Theme, Renderer> {
    Element::new(RevealWidth {
        content,
        factor: factor.clamp(0.0, 1.0),
        opacity: opacity.clamp(0.0, 1.0),
        interactive,
    })
}

fn shift_cursor(cursor: mouse::Cursor, delta: Vector) -> mouse::Cursor {
    match cursor {
        mouse::Cursor::Available(p) => {
            mouse::Cursor::Available(Point::new(p.x - delta.x, p.y - delta.y))
        }
        mouse::Cursor::Levitating(p) => {
            mouse::Cursor::Levitating(Point::new(p.x - delta.x, p.y - delta.y))
        }
        mouse::Cursor::Unavailable => mouse::Cursor::Unavailable,
    }
}

fn shift_viewport(visible: Rectangle, delta: Vector) -> Rectangle {
    Rectangle {
        x: visible.x - delta.x,
        y: visible.y - delta.y,
        width: visible.width,
        height: visible.height,
    }
}

fn non_empty_clip(bounds: Rectangle, viewport: &Rectangle) -> Option<Rectangle> {
    bounds
        .intersection(viewport)
        .filter(|clip| clip.width > 0.0 && clip.height > 0.0)
}

// ---------------------------------------------------------------------------
// SlideFade Widget
// ---------------------------------------------------------------------------

struct SlideFade<'a, Message> {
    content: Element<'a, Message, Theme, Renderer>,
    offset: Vector,
    opacity: f32,
}

impl<Message> SlideFade<'_, Message> {
    fn is_settled(&self) -> bool {
        self.offset.x.abs() <= 0.01 && self.offset.y.abs() <= 0.01 && self.opacity >= 0.999
    }
}

impl<Message> Widget<Message, Theme, Renderer> for SlideFade<'_, Message> {
    fn children(&self) -> Vec<Tree> {
        vec![Tree::new(&self.content)]
    }

    fn diff(&self, tree: &mut Tree) {
        tree.diff_children(std::slice::from_ref(&self.content));
    }

    fn size(&self) -> Size<Length> {
        self.content.as_widget().size()
    }

    fn size_hint(&self) -> Size<Length> {
        self.content.as_widget().size_hint()
    }

    fn layout(
        &mut self,
        tree: &mut Tree,
        renderer: &Renderer,
        limits: &layout::Limits,
    ) -> layout::Node {
        let node = self
            .content
            .as_widget_mut()
            .layout(&mut tree.children[0], renderer, limits);
        let size = node.size();
        layout::Node::with_children(size, vec![node])
    }

    fn operate(
        &mut self,
        tree: &mut Tree,
        layout: Layout<'_>,
        renderer: &Renderer,
        operation: &mut dyn widget::Operation,
    ) {
        self.content.as_widget_mut().operate(
            &mut tree.children[0],
            layout.children().next().unwrap(),
            renderer,
            operation,
        );
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
        let child_layout = layout.children().next().unwrap();
        if self.is_settled() {
            self.content.as_widget_mut().update(
                &mut tree.children[0],
                event,
                child_layout,
                cursor,
                renderer,
                clipboard,
                shell,
                viewport,
            );
            return;
        }
        let visible = layout.bounds().intersection(viewport).unwrap_or_default();
        let mapped_cursor = if cursor.is_over(visible) {
            shift_cursor(cursor, self.offset)
        } else {
            mouse::Cursor::Unavailable
        };
        let child_viewport = shift_viewport(visible, self.offset);
        self.content.as_widget_mut().update(
            &mut tree.children[0],
            event,
            child_layout,
            mapped_cursor,
            renderer,
            clipboard,
            shell,
            &child_viewport,
        );
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
        let child_layout = layout.children().next().unwrap();
        if self.is_settled() {
            self.content.as_widget().draw(
                &tree.children[0],
                renderer,
                theme,
                style,
                child_layout,
                cursor,
                viewport,
            );
            return;
        }
        if self.opacity <= 0.0 {
            return;
        }
        let Some(visible) = non_empty_clip(layout.bounds(), viewport) else {
            return;
        };
        let mapped_cursor = if cursor.is_over(visible) {
            shift_cursor(cursor, self.offset)
        } else {
            mouse::Cursor::Unavailable
        };
        let child_viewport = shift_viewport(visible, self.offset);
        let offset = self.offset;
        let opacity = self.opacity;
        renderer.with_layer(visible, |renderer| {
            renderer.start_transformation(Transformation::translate(offset.x, offset.y));
            renderer.with_opacity(opacity, |renderer| {
                self.content.as_widget().draw(
                    &tree.children[0],
                    renderer,
                    theme,
                    style,
                    child_layout,
                    mapped_cursor,
                    &child_viewport,
                );
            });
            renderer.end_transformation();
        });
    }

    fn mouse_interaction(
        &self,
        tree: &Tree,
        layout: Layout<'_>,
        cursor: mouse::Cursor,
        viewport: &Rectangle,
        renderer: &Renderer,
    ) -> mouse::Interaction {
        let child_layout = layout.children().next().unwrap();
        if self.is_settled() {
            return self.content.as_widget().mouse_interaction(
                &tree.children[0],
                child_layout,
                cursor,
                viewport,
                renderer,
            );
        }
        let Some(visible) = non_empty_clip(layout.bounds(), viewport) else {
            return mouse::Interaction::default();
        };
        if !cursor.is_over(visible) {
            return mouse::Interaction::default();
        }
        let mapped_cursor = shift_cursor(cursor, self.offset);
        let child_viewport = shift_viewport(visible, self.offset);
        self.content.as_widget().mouse_interaction(
            &tree.children[0],
            child_layout,
            mapped_cursor,
            &child_viewport,
            renderer,
        )
    }

    fn overlay<'a>(
        &'a mut self,
        tree: &'a mut Tree,
        layout: Layout<'a>,
        renderer: &Renderer,
        viewport: &Rectangle,
        offset: Vector,
    ) -> Option<overlay::Element<'a, Message, Theme, Renderer>> {
        let total_offset = if self.is_settled() {
            offset
        } else {
            Vector::new(offset.x + self.offset.x, offset.y + self.offset.y)
        };
        self.content.as_widget_mut().overlay(
            &mut tree.children[0],
            layout.children().next().unwrap(),
            renderer,
            viewport,
            total_offset,
        )
    }
}

// ---------------------------------------------------------------------------
// RevealHeight Widget
// ---------------------------------------------------------------------------

struct RevealHeight<'a, Message> {
    content: Element<'a, Message, Theme, Renderer>,
    factor: f32,
    opacity: f32,
    interactive: bool,
}

impl<Message> RevealHeight<'_, Message> {
    fn is_settled_open(&self) -> bool {
        self.factor >= 0.999 && self.opacity >= 0.999 && self.interactive
    }

    fn slide_offset(&self) -> Vector {
        Vector::new(0.0, -6.0 * (1.0 - self.factor))
    }
}

impl<Message> Widget<Message, Theme, Renderer> for RevealHeight<'_, Message> {
    fn children(&self) -> Vec<Tree> {
        vec![Tree::new(&self.content)]
    }

    fn diff(&self, tree: &mut Tree) {
        tree.diff_children(std::slice::from_ref(&self.content));
    }

    fn size(&self) -> Size<Length> {
        let s = self.content.as_widget().size();
        Size::new(s.width, Length::Shrink)
    }

    fn size_hint(&self) -> Size<Length> {
        let s = self.content.as_widget().size_hint();
        Size::new(s.width, Length::Shrink)
    }

    fn layout(
        &mut self,
        tree: &mut Tree,
        renderer: &Renderer,
        limits: &layout::Limits,
    ) -> layout::Node {
        let node = self
            .content
            .as_widget_mut()
            .layout(&mut tree.children[0], renderer, limits);
        let mut size = node.size();
        if self.factor < 0.999 {
            size.height = (size.height * self.factor)
                .clamp(limits.min().height, limits.max().height);
        }
        layout::Node::with_children(size, vec![node])
    }

    fn operate(
        &mut self,
        tree: &mut Tree,
        layout: Layout<'_>,
        renderer: &Renderer,
        operation: &mut dyn widget::Operation,
    ) {
        self.content.as_widget_mut().operate(
            &mut tree.children[0],
            layout.children().next().unwrap(),
            renderer,
            operation,
        );
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
        let child_layout = layout.children().next().unwrap();
        if self.is_settled_open() {
            self.content.as_widget_mut().update(
                &mut tree.children[0],
                event,
                child_layout,
                cursor,
                renderer,
                clipboard,
                shell,
                viewport,
            );
            return;
        }
        if !self.interactive
            && matches!(
                event,
                Event::Keyboard(_) | Event::Window(iced_core::window::Event::RedrawRequested(_))
            )
        {
            return;
        }
        let visible = layout.bounds().intersection(viewport).unwrap_or_default();
        let delta = self.slide_offset();
        let mapped_cursor = if self.interactive && cursor.is_over(visible) {
            shift_cursor(cursor, delta)
        } else {
            mouse::Cursor::Unavailable
        };
        let child_viewport = shift_viewport(visible, delta);
        self.content.as_widget_mut().update(
            &mut tree.children[0],
            event,
            child_layout,
            mapped_cursor,
            renderer,
            clipboard,
            shell,
            &child_viewport,
        );
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
        let child_layout = layout.children().next().unwrap();
        if self.is_settled_open() {
            self.content.as_widget().draw(
                &tree.children[0],
                renderer,
                theme,
                style,
                child_layout,
                cursor,
                viewport,
            );
            return;
        }
        if self.factor <= 0.0 || self.opacity <= 0.0 {
            return;
        }
        let Some(visible) = non_empty_clip(layout.bounds(), viewport) else {
            return;
        };
        let delta = self.slide_offset();
        let mapped_cursor = if self.interactive && cursor.is_over(visible) {
            shift_cursor(cursor, delta)
        } else {
            mouse::Cursor::Unavailable
        };
        let child_viewport = shift_viewport(visible, delta);
        let opacity = self.opacity;
        renderer.with_layer(visible, |renderer| {
            renderer.start_transformation(Transformation::translate(delta.x, delta.y));
            renderer.with_opacity(opacity, |renderer| {
                self.content.as_widget().draw(
                    &tree.children[0],
                    renderer,
                    theme,
                    style,
                    child_layout,
                    mapped_cursor,
                    &child_viewport,
                );
            });
            renderer.end_transformation();
        });
    }

    fn mouse_interaction(
        &self,
        tree: &Tree,
        layout: Layout<'_>,
        cursor: mouse::Cursor,
        viewport: &Rectangle,
        renderer: &Renderer,
    ) -> mouse::Interaction {
        let child_layout = layout.children().next().unwrap();
        if self.is_settled_open() {
            return self.content.as_widget().mouse_interaction(
                &tree.children[0],
                child_layout,
                cursor,
                viewport,
                renderer,
            );
        }
        if !self.interactive {
            return mouse::Interaction::default();
        }
        let Some(visible) = non_empty_clip(layout.bounds(), viewport) else {
            return mouse::Interaction::default();
        };
        if !cursor.is_over(visible) {
            return mouse::Interaction::default();
        }
        let delta = self.slide_offset();
        let mapped_cursor = shift_cursor(cursor, delta);
        let child_viewport = shift_viewport(visible, delta);
        self.content.as_widget().mouse_interaction(
            &tree.children[0],
            child_layout,
            mapped_cursor,
            &child_viewport,
            renderer,
        )
    }

    fn overlay<'a>(
        &'a mut self,
        tree: &'a mut Tree,
        layout: Layout<'a>,
        renderer: &Renderer,
        viewport: &Rectangle,
        offset: Vector,
    ) -> Option<overlay::Element<'a, Message, Theme, Renderer>> {
        if !self.interactive || self.factor < 0.999 {
            return None;
        }
        self.content.as_widget_mut().overlay(
            &mut tree.children[0],
            layout.children().next().unwrap(),
            renderer,
            viewport,
            offset,
        )
    }
}

// ---------------------------------------------------------------------------
// RevealWidth Widget
// ---------------------------------------------------------------------------

struct RevealWidth<'a, Message> {
    content: Element<'a, Message, Theme, Renderer>,
    factor: f32,
    opacity: f32,
    interactive: bool,
}

impl<Message> RevealWidth<'_, Message> {
    fn is_settled_open(&self) -> bool {
        self.factor >= 0.999 && self.opacity >= 0.999 && self.interactive
    }

    fn slide_offset(&self, natural_width: f32, visible_width: f32) -> Vector {
        Vector::new(-(natural_width - visible_width), 0.0)
    }
}

impl<Message> Widget<Message, Theme, Renderer> for RevealWidth<'_, Message> {
    fn children(&self) -> Vec<Tree> {
        vec![Tree::new(&self.content)]
    }

    fn diff(&self, tree: &mut Tree) {
        tree.diff_children(std::slice::from_ref(&self.content));
    }

    fn size(&self) -> Size<Length> {
        let s = self.content.as_widget().size();
        Size::new(Length::Shrink, s.height)
    }

    fn size_hint(&self) -> Size<Length> {
        let s = self.content.as_widget().size_hint();
        Size::new(Length::Shrink, s.height)
    }

    fn layout(
        &mut self,
        tree: &mut Tree,
        renderer: &Renderer,
        limits: &layout::Limits,
    ) -> layout::Node {
        let node = self
            .content
            .as_widget_mut()
            .layout(&mut tree.children[0], renderer, limits);
        let mut size = node.size();
        if self.factor < 0.999 {
            size.width = (size.width * self.factor)
                .clamp(limits.min().width, limits.max().width);
        }
        layout::Node::with_children(size, vec![node])
    }

    fn operate(
        &mut self,
        tree: &mut Tree,
        layout: Layout<'_>,
        renderer: &Renderer,
        operation: &mut dyn widget::Operation,
    ) {
        self.content.as_widget_mut().operate(
            &mut tree.children[0],
            layout.children().next().unwrap(),
            renderer,
            operation,
        );
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
        let child_layout = layout.children().next().unwrap();
        if self.is_settled_open() {
            self.content.as_widget_mut().update(
                &mut tree.children[0],
                event,
                child_layout,
                cursor,
                renderer,
                clipboard,
                shell,
                viewport,
            );
            return;
        }
        if !self.interactive
            && matches!(
                event,
                Event::Keyboard(_) | Event::Window(iced_core::window::Event::RedrawRequested(_))
            )
        {
            return;
        }
        let visible = layout.bounds().intersection(viewport).unwrap_or_default();
        let delta = self.slide_offset(child_layout.bounds().width, layout.bounds().width);
        let mapped_cursor = if self.interactive && cursor.is_over(visible) {
            shift_cursor(cursor, delta)
        } else {
            mouse::Cursor::Unavailable
        };
        let child_viewport = shift_viewport(visible, delta);
        self.content.as_widget_mut().update(
            &mut tree.children[0],
            event,
            child_layout,
            mapped_cursor,
            renderer,
            clipboard,
            shell,
            &child_viewport,
        );
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
        let child_layout = layout.children().next().unwrap();
        if self.is_settled_open() {
            self.content.as_widget().draw(
                &tree.children[0],
                renderer,
                theme,
                style,
                child_layout,
                cursor,
                viewport,
            );
            return;
        }
        if self.factor <= 0.0 || self.opacity <= 0.0 {
            return;
        }
        let Some(visible) = non_empty_clip(layout.bounds(), viewport) else {
            return;
        };
        let delta = self.slide_offset(child_layout.bounds().width, layout.bounds().width);
        let mapped_cursor = if self.interactive && cursor.is_over(visible) {
            shift_cursor(cursor, delta)
        } else {
            mouse::Cursor::Unavailable
        };
        let child_viewport = shift_viewport(visible, delta);
        let opacity = self.opacity;
        renderer.with_layer(visible, |renderer| {
            renderer.start_transformation(Transformation::translate(delta.x, delta.y));
            renderer.with_opacity(opacity, |renderer| {
                self.content.as_widget().draw(
                    &tree.children[0],
                    renderer,
                    theme,
                    style,
                    child_layout,
                    mapped_cursor,
                    &child_viewport,
                );
            });
            renderer.end_transformation();
        });
    }

    fn mouse_interaction(
        &self,
        tree: &Tree,
        layout: Layout<'_>,
        cursor: mouse::Cursor,
        viewport: &Rectangle,
        renderer: &Renderer,
    ) -> mouse::Interaction {
        let child_layout = layout.children().next().unwrap();
        if self.is_settled_open() {
            return self.content.as_widget().mouse_interaction(
                &tree.children[0],
                child_layout,
                cursor,
                viewport,
                renderer,
            );
        }
        if !self.interactive {
            return mouse::Interaction::default();
        }
        let Some(visible) = non_empty_clip(layout.bounds(), viewport) else {
            return mouse::Interaction::default();
        };
        if !cursor.is_over(visible) {
            return mouse::Interaction::default();
        }
        let delta = self.slide_offset(child_layout.bounds().width, layout.bounds().width);
        let mapped_cursor = shift_cursor(cursor, delta);
        let child_viewport = shift_viewport(visible, delta);
        self.content.as_widget().mouse_interaction(
            &tree.children[0],
            child_layout,
            mapped_cursor,
            &child_viewport,
            renderer,
        )
    }

    fn overlay<'a>(
        &'a mut self,
        tree: &'a mut Tree,
        layout: Layout<'a>,
        renderer: &Renderer,
        viewport: &Rectangle,
        offset: Vector,
    ) -> Option<overlay::Element<'a, Message, Theme, Renderer>> {
        if !self.interactive || self.factor < 0.999 {
            return None;
        }
        self.content.as_widget_mut().overlay(
            &mut tree.children[0],
            layout.children().next().unwrap(),
            renderer,
            viewport,
            offset,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ease_out_quart_is_monotonic_bounded_and_exact_at_endpoints() {
        assert_eq!(ease_out_quart(-0.5), 0.0);
        assert_eq!(ease_out_quart(0.0), 0.0);
        assert_eq!(ease_out_quart(1.0), 1.0);
        assert_eq!(ease_out_quart(2.0), 1.0);
        let mut prev = 0.0;
        for step in 1..=20 {
            let v = ease_out_quart(step as f32 / 20.0);
            assert!(v > prev && v <= 1.0);
            prev = v;
        }
    }

    #[test]
    fn float_transition_retargets_continuously_and_scales_duration() {
        let start = Instant::now();
        let mut anim = FloatTransition::settled(0.0, start);
        assert!(!anim.is_active(start));
        assert_eq!(anim.value(start), 0.0);

        anim.retarget(1.0, start, Duration::from_millis(200));
        assert!(anim.is_active(start));
        let mid_time = start + Duration::from_millis(50);
        let mid = anim.value(mid_time);
        assert!(mid > 0.5 && mid < 1.0, "ease-out should progress briskly: {mid}");

        // Mid-flight reversal starts at exact current value without jump.
        anim.retarget(0.0, mid_time, Duration::from_millis(200));
        assert!((anim.value(mid_time) - mid).abs() < 1e-5);
        assert!(anim.is_active(mid_time));
        let after = mid_time + Duration::from_millis(250);
        assert!(!anim.is_active(after));
        assert_eq!(anim.value(after), 0.0);

        // Snap settles immediately.
        anim.retarget(1.0, after, Duration::from_millis(200));
        assert!(anim.is_active(after));
        anim.snap(1.0, after);
        assert!(!anim.is_active(after));
        assert_eq!(anim.value(after), 1.0);
    }

    #[test]
    fn nav_transition_samples_directional_offsets_and_settles_to_identity() {
        let start = Instant::now();
        let push = NavTransition::start(NavDirection::Push, start);
        let (off0, op0) = push.sample(start);
        assert!((off0.x - 28.0).abs() < 1e-4 && off0.y == 0.0);
        assert!((op0 - 0.15).abs() < 1e-4);

        let (off_mid, op_mid) = push.sample(start + Duration::from_millis(60));
        assert!(off_mid.x > 0.0 && off_mid.x < 28.0);
        assert!(op_mid > 0.15 && op_mid < 1.0);

        let done = start + Duration::from_millis(200);
        assert!(!push.is_active(done));
        assert_eq!(push.sample(done), (Vector::ZERO, 1.0));

        let pop = NavTransition::start(NavDirection::Pop, start);
        assert!(pop.sample(start).0.x < 0.0);

        let down = NavTransition::start(NavDirection::PageDown, start);
        assert!(down.sample(start).0.y > 0.0);

        let up = NavTransition::start(NavDirection::PageUp, start);
        assert!(up.sample(start).0.y < 0.0);
    }
}
