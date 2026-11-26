//! Self-contained embedded vector icons on a 24 × 24 optical grid.
//!
//! Serves cached `iced_widget::svg::Handle` instances through a zero-allocation
//! `RwLock` read fast path with zero external icon-theme runtime dependencies.

use iced_core::Color;
use iced_widget::svg;
use std::collections::HashMap;
use std::sync::{OnceLock, RwLock};

type Element<'a, Message> =
    iced_core::Element<'a, Message, iced_core::Theme, crate::renderer::Renderer>;

enum IconSpec {
    Stroke(&'static str),
    Filled(&'static str),
}

fn icon_spec(name: &str) -> IconSpec {
    match name {
        // Window & Layout Chrome
        "sidebar" => IconSpec::Stroke(
            "M4 4h16a2 2 0 012 2v12a2 2 0 01-2 2H4a2 2 0 01-2-2V6a2 2 0 012-2zm5 0v16",
        ),
        "search" => IconSpec::Stroke("M11 19a8 8 0 100-16 8 8 0 000 16zm10 2l-4.35-4.35"),
        "close" => IconSpec::Stroke("M6 6l12 12M6 18L18 6"),
        "plus" => IconSpec::Stroke("M12 5v14M5 12h14"),
        "minus" => IconSpec::Stroke("M5 12h14"),
        "check" => IconSpec::Stroke("M5 13l4 4L19 7"),
        "menu" => IconSpec::Stroke("M4 7h16M4 12h16M4 17h16"),
        "chevron_left" => IconSpec::Stroke("M15 19l-7-7 7-7"),
        "chevron_right" => IconSpec::Stroke("M9 5l7 7-7 7"),
        "chevron_down" => IconSpec::Stroke("M6 9l6 6 6-6"),
        "chevron_up" => IconSpec::Stroke("M18 15l-6-6-6 6"),

        // Views & Time
        "month" => IconSpec::Stroke(
            "M4 6a2 2 0 012-2h12a2 2 0 012 2v12a2 2 0 01-2 2H6a2 2 0 01-2-2V6zm0 4h16M9 2v4M15 2v4M9 14h.01M12 14h.01M15 14h.01M9 17h.01M12 17h.01",
        ),
        "week" => IconSpec::Stroke(
            "M4 6a2 2 0 012-2h12a2 2 0 012 2v12a2 2 0 01-2 2H6a2 2 0 01-2-2V6zm0 4h16M9 2v4M15 2v4M10 10v10M15 10v10",
        ),
        "agenda" => IconSpec::Stroke(
            "M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01",
        ),
        "today" => IconSpec::Stroke(
            "M4 6a2 2 0 012-2h12a2 2 0 012 2v12a2 2 0 01-2 2H6a2 2 0 01-2-2V6zm0 4h16M9 2v4M15 2v4M12 15a1.5 1.5 0 100 .01z",
        ),
        "calendar" => IconSpec::Stroke(
            "M5 5h14a2 2 0 012 2v12a2 2 0 01-2 2H5a2 2 0 01-2-2V7a2 2 0 012-2zm0 5h14M8 3v4M16 3v4",
        ),
        "clock" => IconSpec::Stroke("M12 22a10 10 0 100-20 10 10 0 000 20zm0-14v4l3 2"),
        "repeat" => IconSpec::Stroke(
            "M17 2l4 4-4 4M3 11V9a4 4 0 014-4h14M7 22l-4-4 4-4M21 13v2a4 4 0 01-4 4H3",
        ),
        "bell" => IconSpec::Stroke(
            "M18 8A6 6 0 006 8c0 7-3 9-3 9h18s-3-2-3-9m-4.27 13a2 2 0 01-3.46 0",
        ),

        // Event & Calendar Actions
        "edit" => IconSpec::Stroke(
            "M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7M18.5 2.5a2.12 2.12 0 013 3L12 15l-4 1 1-4 9.5-9.5z",
        ),
        "trash" => IconSpec::Stroke(
            "M3 6h18M8 6V4a1 1 0 011-1h6a1 1 0 011 1v2m2 0v14a2 2 0 01-2 2H8a2 2 0 01-2-2V6h12zM10 11v6M14 11v6",
        ),
        "location" => IconSpec::Stroke(
            "M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0118 0zm-9 3a3 3 0 100-6 3 3 0 000 6z",
        ),
        "notes" => IconSpec::Stroke("M4 6h16M4 11h16M4 16h10"),
        "import" => IconSpec::Stroke(
            "M12 3v12m-4-4l4 4 4-4M4 17v2a2 2 0 002 2h12a2 2 0 002-2v-2",
        ),
        "export" => IconSpec::Stroke(
            "M12 15V3m-4 4l4-4 4 4M4 17v2a2 2 0 002 2h12a2 2 0 002-2v-2",
        ),
        "eye" => IconSpec::Stroke(
            "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12zm10 3a3 3 0 100-6 3 3 0 000 6z",
        ),
        "eye_off" => IconSpec::Stroke(
            "M17.94 17.94A10.07 10.07 0 0112 19c-6.5 0-10-7-10-7a18.45 18.45 0 015.06-5.94M9.9 4.24A9.12 9.12 0 0112 5c6.5 0 10 7 10 7a18.5 18.5 0 01-2.16 3.19M1 1l22 22",
        ),
        "info" => IconSpec::Stroke("M12 16v-4m0-4h.01M22 12a10 10 0 11-20 0 10 10 0 0120 0z"),
        "warning" => IconSpec::Stroke(
            "M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0zM12 9v4m0 4h.01",
        ),
        "dot" => IconSpec::Filled("M12 12m-5 0a5 5 0 1010 0 5 5 0 10-10 0"),
        _ => IconSpec::Stroke(
            "M4 6a2 2 0 012-2h12a2 2 0 012 2v12a2 2 0 01-2 2H6a2 2 0 01-2-2V6zm4 6h8m-8-3h8m-8 6h5",
        ),
    }
}

fn build_svg_bytes(name: &str) -> Vec<u8> {
    let body = match icon_spec(name) {
        IconSpec::Stroke(path) => format!(
            r#"<path d="{path}" fill="none" stroke="white" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/>"#
        ),
        IconSpec::Filled(path) => format!(r#"<path d="{path}" fill="white"/>"#),
    };
    format!(r#"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">{body}</svg>"#)
        .into_bytes()
}

pub fn render_icon<'a, Message: 'static>(
    name: &str,
    size: f32,
    color: Color,
) -> Element<'a, Message> {
    static CACHE: OnceLock<RwLock<HashMap<Box<str>, svg::Handle>>> = OnceLock::new();
    let cache = CACHE.get_or_init(|| RwLock::new(HashMap::new()));

    let handle = if let Some(existing) = cache.read().unwrap().get(name) {
        existing.clone()
    } else {
        let created = svg::Handle::from_memory(build_svg_bytes(name));
        cache
            .write()
            .unwrap()
            .entry(Box::from(name))
            .or_insert(created)
            .clone()
    };

    svg(handle)
        .width(size)
        .height(size)
        .style(move |_, _| svg::Style { color: Some(color) })
        .into()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn embedded_svgs_are_well_formed() {
        for name in [
            "sidebar",
            "search",
            "close",
            "plus",
            "minus",
            "check",
            "chevron_left",
            "chevron_right",
            "chevron_down",
            "chevron_up",
            "month",
            "week",
            "agenda",
            "today",
            "calendar",
            "clock",
            "repeat",
            "bell",
            "edit",
            "trash",
            "location",
            "notes",
            "import",
            "export",
            "eye",
            "eye_off",
            "dot",
        ] {
            let bytes = build_svg_bytes(name);
            let text = std::str::from_utf8(&bytes).unwrap();
            assert!(text.starts_with("<svg "));
            assert!(text.ends_with("</svg>"));
            assert!(text.contains("viewBox=\"0 0 24 24\""));
        }
    }
}
