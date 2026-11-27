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
        // Bluetooth & Adapter Controls
        "bluetooth" => IconSpec::Stroke("M6.5 6.5l11 11L12 22V2l5.5 4.5-11 11"),
        "bluetooth_off" => IconSpec::Stroke("M17 17l-5 5V12l-5 5M2 2l20 20M14.5 9.5L17.5 7 12 2v5"),
        "power" => IconSpec::Stroke("M12 2v10M18.36 6.64a9 9 0 11-12.73 0"),
        "scan" => IconSpec::Stroke("M21 12a9 9 0 11-2.64-6.36M21 3v6h-6"),
        "discoverable" => IconSpec::Stroke(
            "M12 12h.01M8.46 15.54a5 5 0 010-7.08M15.54 8.46a5 5 0 010 7.08M5.64 18.36a9 9 0 010-12.72M18.36 5.64a9 9 0 010 12.72",
        ),
        "filter" => IconSpec::Stroke("M4 5h16l-6 7v6l-4 2v-8L4 5z"),

        // Device Types
        "headphones" => IconSpec::Stroke(
            "M3 18v-6a9 9 0 0118 0v6M21 19a2 2 0 01-2 2h-1a2 2 0 01-2-2v-3a2 2 0 012-2h3v5zM3 19a2 2 0 002 2h1a2 2 0 002-2v-3a2 2 0 00-2-2H3v5z",
        ),
        "speaker" => IconSpec::Stroke(
            "M6 3h12a2 2 0 012 2v14a2 2 0 01-2 2H6a2 2 0 01-2-2V5a2 2 0 012-2zm6 4h.01M12 18a4 4 0 100-8 4 4 0 000 8z",
        ),
        "keyboard" => IconSpec::Stroke(
            "M4 6h16a2 2 0 012 2v8a2 2 0 01-2 2H4a2 2 0 01-2-2V8a2 2 0 012-2zm2 4h.01M10 10h.01M14 10h.01M18 10h.01M8 14h8",
        ),
        "mouse" => IconSpec::Stroke("M12 3a6 6 0 00-6 6v6a6 6 0 0012 0V9a6 6 0 00-6-6zm0 0v6"),
        "phone" => IconSpec::Stroke(
            "M7 2h10a2 2 0 012 2v16a2 2 0 01-2 2H7a2 2 0 01-2-2V4a2 2 0 012-2zm5 16h.01",
        ),
        "controller" => IconSpec::Stroke(
            "M6 9h12a4 4 0 013.9 4.8l-.8 3.6a2.5 2.5 0 01-4.2 1.2L14.5 16h-5l-2.4 2.6a2.5 2.5 0 01-4.2-1.2l-.8-3.6A4 4 0 016 9zm2 3v3m-1.5-1.5h3M15.5 12.5h.01M17.5 14.5h.01",
        ),
        "computer" => IconSpec::Stroke(
            "M4 5h16a2 2 0 012 2v9a2 2 0 01-2 2H4a2 2 0 01-2-2V7a2 2 0 012-2zm4 16h8m-4-3v3",
        ),

        // Battery & Signal Tiers
        "battery" => IconSpec::Stroke(
            "M4 7h14a2 2 0 012 2v6a2 2 0 01-2 2H4a2 2 0 01-2-2V9a2 2 0 012-2zm18 3v4M6 11h8v2H6z",
        ),
        "battery_low" => IconSpec::Stroke(
            "M4 7h14a2 2 0 012 2v6a2 2 0 01-2 2H4a2 2 0 01-2-2V9a2 2 0 012-2zm18 3v4M6 11h3v2H6z",
        ),
        "signal_1" => IconSpec::Stroke("M4 18v-2"),
        "signal_2" => IconSpec::Stroke("M4 18v-2M9 18v-5"),
        "signal_3" => IconSpec::Stroke("M4 18v-2M9 18v-5M14 18v-8"),
        "signal_4" => IconSpec::Stroke("M4 18v-2M9 18v-5M14 18v-8M19 18V6"),

        // Device Actions & Chrome
        "connect" => IconSpec::Stroke(
            "M10 13a5 5 0 007.54.54l2.83-2.83a5 5 0 00-7.07-7.07l-1.72 1.71M14 11a5 5 0 00-7.54-.54l-2.83 2.83a5 5 0 007.07 7.07l1.71-1.71",
        ),
        "disconnect" => IconSpec::Stroke(
            "M9 15l6-6M17 14l2.5-2.5a4 4 0 00-5.66-5.66L11.5 8M7 10l-2.5 2.5a4 4 0 005.66 5.66L12.5 16M4 4l16 16",
        ),
        "pair" => IconSpec::Stroke("M15 7a4 4 0 11-7.45 2H3v4h2v2h3v-2h.55A4 4 0 0115 7zm0 0h.01"),
        "trust" => {
            IconSpec::Stroke("M12 3l8 4v5c0 5-3.4 8.7-8 10-4.6-1.3-8-5-8-10V7l8-4zm-3 9l2 2 4-4")
        }
        "untrust" => IconSpec::Stroke(
            "M12 3l8 4v5c0 5-3.4 8.7-8 10-4.6-1.3-8-5-8-10V7l8-4zM9 9l6 6m0-6l-6 6",
        ),
        "block" => IconSpec::Stroke("M12 21a9 9 0 100-18 9 9 0 000 18zM5.64 5.64l12.72 12.72"),
        "edit" => IconSpec::Stroke(
            "M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7M18.5 2.5a2.12 2.12 0 013 3L12 15l-4 1 1-4 9.5-9.5z",
        ),
        "trash" => IconSpec::Stroke(
            "M3 6h18M8 6V4a1 1 0 011-1h6a1 1 0 011 1v2m2 0v14a2 2 0 01-2 2H8a2 2 0 01-2-2V6h12zM10 11v6M14 11v6",
        ),
        "copy" => IconSpec::Stroke(
            "M9 9h10a2 2 0 012 2v9a2 2 0 01-2 2H9a2 2 0 01-2-2v-9a2 2 0 012-2zm-4 6H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1",
        ),
        "audio" => IconSpec::Stroke(
            "M11 5L6 9H2v6h4l5 4V5zm4.54 3.46a5 5 0 010 7.08m2.83-9.91a9 9 0 010 12.74",
        ),
        "eye" => IconSpec::Stroke(
            "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12zm10 3a3 3 0 100-6 3 3 0 000 6z",
        ),
        "eye_off" => IconSpec::Stroke(
            "M17.94 17.94A10.07 10.07 0 0112 19c-6.5 0-10-7-10-7a18.45 18.45 0 015.06-5.94M9.9 4.24A9.12 9.12 0 0112 5c6.5 0 10 7 10 7a18.5 18.5 0 01-2.16 3.19M1 1l22 22",
        ),
        "chevron_down" => IconSpec::Stroke("M6 9l6 6 6-6"),
        "chevron_up" => IconSpec::Stroke("M18 15l-6-6-6 6"),
        "chevron_right" => IconSpec::Stroke("M9 5l7 7-7 7"),
        "close" => IconSpec::Stroke("M6 6l12 12M6 18L18 6"),
        "check" => IconSpec::Stroke("M5 13l4 4L19 7"),
        "warning" => IconSpec::Stroke(
            "M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0zM12 9v4m0 4h.01",
        ),
        "settings" => IconSpec::Stroke(
            "M12 15a3 3 0 100-6 3 3 0 000 6zm7.4-2.1a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 01-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.3 1.7 1.7 0 00-1 1.5V19a2 2 0 01-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.8.3l-.1.1a2 2 0 01-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.8 1.7 1.7 0 00-1.5-1H5a2 2 0 010-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.8l-.1-.1a2 2 0 012.8-2.8l.1.1a1.7 1.7 0 001.8.3h.1a1.7 1.7 0 001-1.5V5a2 2 0 014 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.8-.3l.1-.1a2 2 0 012.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.8v.1a1.7 1.7 0 001.5 1H19a2 2 0 010 4h-.1a1.7 1.7 0 00-1.5 1z",
        ),
        "info" => IconSpec::Stroke("M12 16v-4m0-4h.01M22 12a10 10 0 11-20 0 10 10 0 0120 0z"),
        "dot" => IconSpec::Filled("M12 12m-5 0a5 5 0 1010 0 5 5 0 10-10 0"),
        _ => IconSpec::Stroke("M6.5 6.5l11 11L12 22V2l5.5 4.5-11 11"),
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
    fn embedded_bluetooth_svgs_are_well_formed() {
        for name in [
            "bluetooth",
            "bluetooth_off",
            "power",
            "scan",
            "discoverable",
            "filter",
            "headphones",
            "speaker",
            "keyboard",
            "mouse",
            "phone",
            "controller",
            "computer",
            "battery",
            "battery_low",
            "signal_1",
            "signal_2",
            "signal_3",
            "signal_4",
            "connect",
            "disconnect",
            "pair",
            "trust",
            "untrust",
            "block",
            "edit",
            "trash",
            "copy",
            "audio",
            "eye",
            "eye_off",
            "chevron_down",
            "chevron_up",
            "chevron_right",
            "close",
            "check",
            "warning",
            "settings",
            "info",
            "dot",
            "generic_fallback",
        ] {
            let bytes = build_svg_bytes(name);
            let text = std::str::from_utf8(&bytes).unwrap();
            assert!(text.starts_with("<svg "), "invalid prefix for {name}");
            assert!(text.ends_with("</svg>"), "invalid suffix for {name}");
            assert!(
                text.contains("viewBox=\"0 0 24 24\""),
                "missing viewBox for {name}"
            );
        }
    }
}
