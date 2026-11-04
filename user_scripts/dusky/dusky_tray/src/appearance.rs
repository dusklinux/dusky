//! Panel opacity, glyph halos, and natural window-size measurement.
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
        report_size: None,
        measure_limit: None,
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
        report_size: None,
        measure_limit: None,
    })
}

/// Measure natural content height independently of the current window height.
/// Publish only changed dimensions; the regular window follows its content.
pub fn fit_window<'a, Message: 'a>(
    content: Element<'a, Message, Theme, Renderer>,
    limit: Size,
    report: impl Fn(Size) -> Message + 'a,
) -> Element<'a, Message, Theme, Renderer> {
    Element::new(Reveal {
        content,
        opacity: 1.0,
        glow: 0.0,
        report_size: Some(Box::new(report)),
        measure_limit: Some(limit),
    })
}

#[derive(Default)]
struct MeasuredSize {
    reported: Option<Size>,
}

struct Reveal<'a, Message> {
    content: Element<'a, Message, Theme, Renderer>,
    opacity: f32,
    glow: f32,
    report_size: Option<Box<dyn Fn(Size) -> Message + 'a>>,
    measure_limit: Option<Size>,
}
impl<Message> Widget<Message, Theme, Renderer> for Reveal<'_, Message> {
    fn tag(&self) -> tree::Tag {
        tree::Tag::of::<MeasuredSize>()
    }
    fn state(&self) -> tree::State {
        tree::State::new(MeasuredSize::default())
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
        let measured_limits = self
            .measure_limit
            .map(|limit| layout::Limits::new(Size::ZERO, limit));
        let limits = measured_limits.as_ref().unwrap_or(limits);
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
        if let Some(report) = &self.report_size {
            let bounds = layout.bounds();
            let size = Size::new(bounds.width.ceil(), bounds.height.ceil());
            let state = tree.state.downcast_mut::<MeasuredSize>();
            if state.reported != Some(size) {
                state.reported = Some(size);
                shell.publish(report(size));
            }
        }
        let visible = layout.bounds().intersection(viewport).unwrap_or_default();
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
        let visible = *viewport;
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
        draw(renderer);
    }
    fn mouse_interaction(
        &self,
        tree: &Tree,
        layout: Layout<'_>,
        cursor: mouse::Cursor,
        viewport: &Rectangle,
        renderer: &Renderer,
    ) -> mouse::Interaction {
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
