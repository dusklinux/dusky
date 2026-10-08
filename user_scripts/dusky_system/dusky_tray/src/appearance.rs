//! Panel opacity and frame-driven height changes; no startup visibility barrier.
use crate::renderer::Renderer;
use iced_core::{
    Clipboard, Element, Event, Layout, Length, Rectangle, Shell, Size, Theme, Vector, Widget,
    layout, mouse, overlay, renderer,
    widget::{self, Tree, tree},
};

pub fn reveal<'a, Message: 'a>(
    content: Element<'a, Message, Theme, Renderer>,
    opacity: f32,
) -> Element<'a, Message, Theme, Renderer> {
    Element::new(Reveal {
        content,
        opacity,
        glow: 0.0,
        animate_height: false,
    })
}
/// Glyph-edge halo without changing layout or scheduling animation frames.
/// Zero strength returns the original widget, preserving its rendering exactly.
pub fn glow<'a, Message: 'a>(
    content: Element<'a, Message, Theme, Renderer>,
    strength: f32,
) -> Element<'a, Message, Theme, Renderer> {
    if strength <= 0.0 {
        return content;
    }
    Element::new(Reveal {
        content,
        opacity: 1.0,
        glow: strength.clamp(0.0, 1.0),
        animate_height: false,
    })
}

/// Grow/shrink the panel as asynchronous controls and notifications arrive.
/// First layout is immediate; only subsequent size changes animate.
pub fn smooth_height<'a, Message: 'a>(
    content: Element<'a, Message, Theme, Renderer>,
) -> Element<'a, Message, Theme, Renderer> {
    Element::new(Reveal {
        content,
        opacity: 1.0,
        glow: 0.0,
        animate_height: true,
    })
}

#[derive(Default)]
struct Height {
    value: Option<f32>,
    target: f32,
    velocity: f32,
    last_frame: Option<std::time::Instant>,
}

impl Height {
    fn retarget(&mut self, target: f32) {
        if self.value.is_none() {
            self.value = Some(target);
        }
        if self.target != target {
            self.target = target;
            self.last_frame = Some(std::time::Instant::now());
        }
    }

    fn moving(&self) -> bool {
        self.value.is_some_and(|value| value != self.target)
    }

    fn tick(&mut self, now: std::time::Instant) {
        let Some(last) = self.last_frame.replace(now) else {
            return;
        };
        let Some(value) = self.value else { return };
        let dt = now.saturating_duration_since(last).as_secs_f32();
        // Same critically damped, retargetable spring as skwd-wall-2.
        // Solve it analytically so slow frames catch up without integration loops.
        let omega = 6.64 / 0.18;
        let offset = value - self.target;
        let decay = (-omega * dt).exp();
        let impulse = self.velocity + omega * offset;
        let next = (offset + impulse * dt) * decay;
        self.velocity = (self.velocity - omega * impulse * dt) * decay;
        self.value = Some(self.target + next);
        if next.abs() < 0.25 && self.velocity.abs() < 2.5 {
            self.value = Some(self.target);
            self.velocity = 0.0;
        }
    }
}

struct Reveal<'a, Message> {
    content: Element<'a, Message, Theme, Renderer>,
    opacity: f32,
    glow: f32,
    animate_height: bool,
}
impl<Message> Widget<Message, Theme, Renderer> for Reveal<'_, Message> {
    fn tag(&self) -> tree::Tag {
        tree::Tag::of::<Height>()
    }
    fn state(&self) -> tree::State {
        tree::State::new(Height::default())
    }
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
        let mut size = node.size();
        if self.animate_height {
            let height = tree.state.downcast_mut::<Height>();
            height.retarget(size.height);
            size.height = height
                .value
                .unwrap()
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
        if self.animate_height {
            let height = tree.state.downcast_mut::<Height>();
            if height.moving() {
                if let Event::Window(iced_core::window::Event::RedrawRequested(now)) = event {
                    height.tick(*now);
                    shell.invalidate_layout();
                }
                shell.request_redraw();
            }
        }
        let visible = layout.bounds().intersection(viewport).unwrap_or_default();
        let cursor = if self.animate_height && !cursor.is_over(visible) {
            mouse::Cursor::Unavailable
        } else {
            cursor
        };
        self.content.as_widget_mut().update(
            &mut tree.children[0],
            event,
            layout.children().next().unwrap(),
            cursor,
            renderer,
            clipboard,
            shell,
            &visible,
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
        let visible = if self.animate_height {
            let Some(visible) = layout.bounds().intersection(viewport) else {
                return;
            };
            visible
        } else {
            *viewport
        };
        let draw = |renderer: &mut Renderer| {
            renderer.with_opacity(self.opacity, |renderer| {
                renderer.with_glow(self.glow, |renderer| {
                    self.content.as_widget().draw(
                        &tree.children[0],
                        renderer,
                        theme,
                        style,
                        layout.children().next().unwrap(),
                        cursor,
                        &visible,
                    );
                });
            });
        };
        if self.animate_height {
            use iced_core::Renderer as _;
            renderer.with_layer(visible, draw);
        } else {
            draw(renderer);
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
        if self.animate_height && !cursor.is_over(layout.bounds()) {
            return mouse::Interaction::default();
        }
        self.content.as_widget().mouse_interaction(
            &tree.children[0],
            layout.children().next().unwrap(),
            cursor,
            viewport,
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
    use std::time::{Duration, Instant};

    #[test]
    fn first_height_is_immediate_and_unchanged_layout_does_not_animate() {
        let mut height = Height::default();
        height.retarget(300.0);
        assert_eq!(height.value, Some(300.0));
        assert!(!height.moving());
        let last = height.last_frame;
        height.retarget(300.0);
        assert_eq!(height.last_frame, last);
        assert!(!height.moving());
    }

    #[test]
    fn resize_retargets_without_jumps_and_settles_after_a_slow_frame() {
        let mut height = Height::default();
        height.retarget(300.0);
        height.retarget(600.0);
        let start = Instant::now();
        height.last_frame = Some(start);
        let mut previous = 300.0;
        for frame in 1..=6 {
            height.tick(start + Duration::from_millis(frame * 16));
            let value = height.value.unwrap();
            assert!(value >= previous && value < 600.0);
            previous = value;
        }
        height.retarget(450.0);
        assert_eq!(height.value, Some(previous));
        height.tick(start + Duration::from_secs(2));
        assert_eq!(height.value, Some(450.0));
        assert!(!height.moving());
        assert_eq!(height.velocity, 0.0);
    }
}
