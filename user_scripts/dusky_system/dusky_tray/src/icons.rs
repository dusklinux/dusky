//! Embedded, font-independent icons on a consistent 24 × 24 optical grid.
//! Paths are drawn by Iced's Rust SVG renderer, with inherited button colors.
use iced_core::{
    Color, Element, Layout, Length, Rectangle, Size, Theme, Widget, layout, mouse, renderer, svg,
    widget::Tree,
};
use std::sync::OnceLock;

fn paths(glyph: &str) -> &'static str {
    match glyph {
        "󰖩" | "󰖪" => {
            "M3 9a15 15 0 0 1 18 0M6 12a10 10 0 0 1 12 0M9 15a5 5 0 0 1 6 0M12 18h.01"
        }
        "󰓛" => "M9 3h6M12 3v3M18 7l2-2M12 10v4l3 2 M20 14a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
        "󰈉" => "M2 12s4-6 10-6 10 6 10 6-4 6-10 6S2 12 2 12 M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0",
        "◐" | "◑" => "M4 5h16v14H4zM7 9h3v6H7zM13 9h4M13 12h4M13 15h4",
        "󰇚" => "M12 3v12M7 10l5 5 5-5M5 18v3h14v-3",
        "\u{F02EC}" => {
            "M9 5a3 3 0 0 1 6 0v7a3 3 0 0 1-6 0zM5 10v2a7 7 0 0 0 14 0v-2M12 19v3M9 22h6"
        }
        "\u{F02ED}" => "M9 5a3 3 0 0 1 6 0v7M5 10v2a7 7 0 0 0 12 5M12 19v3M9 22h6M3 3l18 18",
        "󰂛" => "M5 17h14l-2-3V9a5 5 0 0 0-10 0v5zM10 21h4M12 2v2M3 3l18 18",
        "󰂚" => "M5 17h14l-2-3V9a5 5 0 0 0-10 0v5zM10 21h4M12 2v2",
        "󰂯" | "󰂲" => "M7 7l10 10-5 4V3l5 4L7 17",
        "" => "M5 19C-1 6 12 3 21 3c0 12-5 18-14 14M5 21l10-10",
        "󰀄" => "M4 17a9 9 0 1 1 16 0M12 14l4-5M5 20h14M12 5v1M5 10l1 1M18 11l1-1",
        "" => "M13 2L4 14h7l-1 8 10-13h-7z",
        "⏻︎" => "M12 2v10M6 5a9 9 0 1 0 12 0",
        "☁︎" => "M6 18a4 4 0 1 1 0-8 6 6 0 0 1 11-2 5 5 0 0 1 1 10z",
        "▤" => "M4 6h16v12H4zM7 9h.01M7 12h.01M7 15h.01M11 9h6M11 12h6M11 15h6",
        "▣" => "M6 6h12v12H6zM9 9h6v6H9zM9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3",
        "\u{F0156}" => "M6 6l12 12M6 18L18 6",
        "\u{F0142}" => "M8 4l10 8-10 8z",
        "\u{F0140}" => "M4 8l8 10 8-10z",
        "volume-muted" => "M3 9h4l5-4v14l-5-4H3zM3 3l18 18",
        "brightness-low" => "M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0M12 5v1M12 18v1M5 12h1M18 12h1",
        "󰕾" => "M3 9h4l5-4v14l-5-4H3zM16 8a6 6 0 0 1 0 8M19 5a10 10 0 0 1 0 14",
        "󰃠" => {
            "M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M5 19l1.5-1.5M17.5 6.5L19 5"
        }
        "󰡬" => "M3 17h18M5 13a7 7 0 0 1 14 0M12 3v2M3 6l2 2M21 6l-2 2M8 21h8",
        "⇅" => "M7 3v17M3 7l4-4 4 4M17 21V4M13 17l4 4 4-4",
        _ => "M12 5v8M12 18h.01",
    }
}

pub fn icon<Message: 'static>(
    glyph: &'static str,
    size: f32,
    color: Option<Color>,
) -> Element<'static, Message, Theme, crate::renderer::Renderer> {
    // A small map initialized once; handles and renderer caches survive redraws.
    static HANDLES: OnceLock<std::collections::HashMap<&'static str, svg::Handle>> =
        OnceLock::new();
    let handles = HANDLES.get_or_init(|| {
        ["󰖩", "󰖪", "󰓛", "󰈉", "◐", "◑", "󰇚", "\u{F02EC}", "\u{F02ED}", "󰂚", "󰂛", "󰂯", "󰂲", "", "󰀄", "", "⏻︎", "☁︎", "▤", "▣", "\u{F0156}", "\u{F0142}", "\u{F0140}", "󰕾", "volume-muted", "brightness-low", "󰃠", "󰡬", "⇅", "●", "…"]
            .into_iter().map(|g| {
                let fill = if matches!(g, "\u{F0142}" | "\u{F0140}") { "white" } else { "none" };
                let bytes = format!(r#"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="{}" fill="{fill}" stroke="white" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>"#, paths(g));
                (g, svg::Handle::from_memory(bytes.into_bytes()))
            }).collect()
    });
    Element::new(Icon {
        handle: handles.get(glyph).unwrap_or(&handles["●"]).clone(),
        size,
        color,
    })
}

struct Icon {
    handle: svg::Handle,
    size: f32,
    color: Option<Color>,
}
impl<Message> Widget<Message, Theme, crate::renderer::Renderer> for Icon {
    fn size(&self) -> Size<Length> {
        Size::new(Length::Fixed(self.size), Length::Fixed(self.size))
    }
    fn layout(
        &mut self,
        _: &mut Tree,
        _: &crate::renderer::Renderer,
        limits: &layout::Limits,
    ) -> layout::Node {
        layout::Node::new(limits.resolve(
            Length::Fixed(self.size),
            Length::Fixed(self.size),
            Size::new(self.size, self.size),
        ))
    }
    fn draw(
        &self,
        _: &Tree,
        renderer: &mut crate::renderer::Renderer,
        _: &Theme,
        style: &renderer::Style,
        layout: Layout<'_>,
        _: mouse::Cursor,
        viewport: &Rectangle,
    ) {
        use svg::Renderer;
        renderer.draw_svg(
            svg::Svg::new(self.handle.clone()).color(self.color.unwrap_or(style.text_color)),
            layout.bounds(),
            *viewport,
        );
    }
}
