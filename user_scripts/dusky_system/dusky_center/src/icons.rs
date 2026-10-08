//! Embedded, font-independent vector icons on a 24 × 24 optical grid.
//!
//! Rendered directly via Iced's Rust SVG renderer (wgpu).
//! Guarantees crisp rendering without relying on system font glyphs.

use iced_core::Color;
use iced_widget::svg;
use std::collections::HashMap;
use std::sync::OnceLock;

type Element<'a, Message> = iced::Element<'a, Message>;

fn get_svg_path(name: &str) -> &'static str {
    match name {
        // Navigation & Core
        "home" | "user-home-symbolic" => {
            "M3 12l9-9 9 9M5 10v10a1 1 0 001 1h4v-6h4v6h4a1 1 0 001-1V10"
        }
        "system" | "computer-symbolic" | "cpu" => {
            "M9 3v2m6-2v2M9 19v2m6-2v2M3 9h2m-2 6h2m14-6h2m-2 6h2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z"
        }
        "memory" | "memory-symbolic" => {
            "M6 4h12a2 2 0 012 2v12a2 2 0 01-2 2H6a2 2 0 01-2-2V6a2 2 0 012-2zm2 5h8M8 12h8M8 15h8"
        }
        "disk" | "disk_and_files" | "drive-multidisk-symbolic" | "system-file-manager-symbolic" => {
            "M4 6h16a1 1 0 011 1v3a1 1 0 01-1 1H4a1 1 0 01-1-1V7a1 1 0 011-1zm0 8h16a1 1 0 011 1v3a1 1 0 01-1 1H4a1 1 0 01-1-1v-3a1 1 0 011-1zm13-5h.01M17 17h.01"
        }
        "network" | "network-wireless-symbolic" | "network-workgroup-symbolic" | "wifi" => {
            "M3 9a15 15 0 0118 0M6 12a10 10 0 0112 0M9 15a5 5 0 016 0M12 18h.01"
        }
        "bluetooth" | "bluetooth-active-symbolic" => {
            "M7 7l10 10-5 4V3l5 4L7 17"
        }
        "hardware" | "input-keyboard-symbolic" | "keyboard" | "keybinds" | "keylogger" => {
            "M4 6h16a2 2 0 012 2v8a2 2 0 01-2 2H4a2 2 0 01-2-2V8a2 2 0 012-2zm3 4h.01M10 10h.01M13 10h.01M16 10h.01M7 14h10"
        }
        "display" | "video-display-symbolic" | "monitor" => {
            "M4 5h16a1 1 0 011 1v10a1 1 0 01-1 1H4a1 1 0 01-1-1V6a1 1 0 011-1zm4 15h8m-4-4v4"
        }
        "audio" | "audio-volume-high-symbolic" | "volume" => {
            "M11 5L6 9H2v6h4l5 4V5zm4.5 3a5 5 0 010 8m2.5-11a9 9 0 010 14"
        }
        "visuals" | "applications-graphics-symbolic" | "palette" => {
            "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10c.83 0 1.5-.67 1.5-1.5 0-.39-.15-.74-.39-1.01-.23-.26-.38-.61-.38-1 0-.83.67-1.5 1.5-1.5H16c3.31 0 6-2.69 6-6 0-4.97-4.48-9-10-9z"
        }
        "components" | "emblem-system-symbolic" | "settings" => {
            "M12 8a4 4 0 100 8 4 4 0 000-8zm8 4c0-.34-.03-.68-.08-1.01l2.06-1.61-2-3.46-2.43.98a7.9 7.9 0 00-1.75-1.01L15.42 3h-4l-.38 2.89c-.62.26-1.21.6-1.75 1.01l-2.43-.98-2 3.46 2.06 1.61A8.2 8.2 0 006.84 12c0 .34.03.68.08 1.01l-2.06 1.61 2 3.46 2.43-.98c.54.41 1.13.75 1.75 1.01L11.42 21h4l.38-2.89c.62-.26 1.21-.6 1.75-1.01l2.43.98 2-3.46-2.06-1.61c.05-.33.08-.67.08-1.01z"
        }
        "services" | "system-run-symbolic" | "bolt" => {
            "M13 2L4 14h7l-1 8 10-13h-7z"
        }
        "configs" | "emblem-documents-symbolic" | "document" => {
            "M6 2h8l6 6v12a2 2 0 01-2 2H6a2 2 0 01-2-2V4a2 2 0 012-2zm7 1.5V8h4.5"
        }
        "tools_and_ai" | "utilities-terminal-symbolic" | "terminal" => {
            "M4 17l6-5-6-5m8 10h6"
        }
        "setup" | "setup_features" | "system-software-install-symbolic" | "download" => {
            "M12 3v12m-5-5l5 5 5-5M5 19h14"
        }
        "troubleshoot" | "tools-check-spelling-symbolic" | "wrench" => {
            "M14.7 6.3a1 1 0 000 1.4l1.6 1.6-5.4 5.4a2 2 0 01-1.4.6H7a1 1 0 01-1-1v-2.5a2 2 0 01.6-1.4l5.4-5.4 1.6 1.6a1 1 0 001.4 0l.7-.7a3 3 0 00-4.2-4.2l-.7.7"
        }
        "about" | "about_dusky" | "info" => {
            "M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
        }

        // Controls & Actions
        "search" | "system-search-symbolic" => {
            "M11 19a8 8 0 100-16 8 8 0 000 16zm10 2l-4.35-4.35"
        }
        "refresh" | "system-reboot-symbolic" => {
            "M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
        }
        "close" | "window-close-symbolic" => {
            "M6 6l12 12M6 18L18 6"
        }
        "power" | "system-log-out-symbolic" => {
            "M12 2v10M6 5a9 9 0 1012 0"
        }
        "dark_mode" | "weather-clear-night-symbolic" | "moon" => {
            "M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z"
        }
        "brightness" | "sun" => {
            "M12 3v2m0 14v2m9-9h-2M5 12H3m15.36-6.36l-1.41 1.41M7.05 16.95l-1.41 1.41m12.72 0l-1.41-1.41M7.05 7.05L5.64 5.64M12 8a4 4 0 100 8 4 4 0 000-8z"
        }
        "update" | "software-update-available-symbolic" => {
            "M12 4v12m-4-4l4 4 4-4M4 18h16"
        }
        "chevron_right" => {
            "M9 5l7 7-7 7"
        }
        "chevron_left" => {
            "M15 19l-7-7 7-7"
        }
        "chevron_down" => {
            "M6 9l6 6 6-6"
        }
        "check" => {
            "M5 13l4 4L19 7"
        }
        "sidebar" | "split" | "view-sidebar-symbolic" => {
            "M4 4h16a2 2 0 012 2v12a2 2 0 01-2 2H4a2 2 0 01-2-2V6a2 2 0 012-2zm0 2v12h5V6H4zm7 0v12h9V6h-9z"
        }
        "power-profile-balanced-symbolic" | "power-profile-performance-symbolic" | "power-profile-power-saver-symbolic" | "gauge" | "speedometer" => {
            "M12 2a10 10 0 100 20 10 10 0 000-20zm0 18a8 8 0 110-16 8 8 0 010 16zm-1-9.5V8a1 1 0 112 0v2.5l2.12 2.12a1 1 0 01-1.41 1.41L11 11.41z"
        }
        _ => "M12 12m-3 0a3 3 0 106 0 3 3 0 10-6 0",
    }
}

pub fn render_icon<'a, Message: 'static>(
    name: &str,
    size: f32,
    color: Color,
) -> Element<'a, Message> {
    static CACHE: OnceLock<std::sync::Mutex<HashMap<String, svg::Handle>>> = OnceLock::new();
    let cache = CACHE.get_or_init(|| std::sync::Mutex::new(HashMap::new()));

    let path_str = get_svg_path(name);
    let mut lock = cache.lock().unwrap();

    let handle = lock.entry(name.to_string()).or_insert_with(|| {
        let svg_bytes = format!(
            r#"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="{path_str}" fill="none" stroke="white" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/></svg>"#
        );
        svg::Handle::from_memory(svg_bytes.into_bytes())
    }).clone();

    svg(handle)
        .width(size)
        .height(size)
        .style(move |_, _| svg::Style {
            color: Some(color),
        })
        .into()
}
