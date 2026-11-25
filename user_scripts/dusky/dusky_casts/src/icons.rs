use iced::{Color, Element, widget::svg};
use std::{collections::HashMap, sync::OnceLock};

#[derive(Clone, Copy, PartialEq, Eq, Hash)]
pub enum Icon {
    Audio,
    Library,
    Progress,
    Queue,
    Download,
    Star,
    History,
    Archive,
    Search,
    Folder,
    Account,
    Play,
    Pause,
    Next,
    Back,
    Forward,
    Settings,
    Refresh,
    Sync,
    Plus,
    More,
    ChevronLeft,
    ChevronRight,
    ChevronDown,
    Volume,
    Moon,
    Close,
    Up,
    Down,
    Check,
    Grid,
    List,
    Edit,
    Trash,
    Sort,
    External,
}

pub fn icon<'a, M: 'a>(name: Icon, size: f32, color: Color) -> Element<'a, M> {
    static HANDLES: OnceLock<HashMap<Icon, svg::Handle>> = OnceLock::new();
    let handles = HANDLES.get_or_init(|| {
        use Icon::*;
        [
            (
                Audio,
                "<path d='M9 18V5l12-2v13M9 8l12-2'/><ellipse cx='6' cy='18' rx='3' ry='3'/><ellipse cx='18' cy='16' rx='3' ry='3'/>",
            ),
            (Library, "<path d='M4 4v16M9 4v16M14 4v16M18 4l3 16'/>"),
            (Progress, "<circle cx='12' cy='12' r='8'/><path d='M12 7v5l3 2'/>"),
            (Queue, "<path d='M4 6h16M4 12h11M4 18h7M18 15v6m-3-3h6'/>"),
            (Download, "<path d='M12 3v12m-4-4 4 4 4-4M5 16v4h14v-4'/>"),
            (
                Star,
                "<path d='m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9Z'/>",
            ),
            (History, "<path d='M3 10a9 9 0 1 1 2 8M3 4v6h6M12 7v5l3 2'/>"),
            (
                Archive,
                "<rect x='3' y='4' width='18' height='4' rx='1'/><path d='M5 8v12h14V8m-10 4h6'/>",
            ),
            (Search, "<circle cx='10.5' cy='10.5' r='6.5'/><path d='m16 16 5 5'/>"),
            (Folder, "<path d='M3 7V5h7l2 3h9v12H3Z'/>"),
            (
                Account,
                "<circle cx='12' cy='8' r='3.5'/><path d='M5 21v-2a7 7 0 0 1 14 0v2'/>",
            ),
            (
                Play,
                "<path d='m9 5 11 7-11 7Z' fill='currentColor' stroke='none'/>",
            ),
            (Pause, "<path d='M8 5v14M16 5v14' stroke-width='3'/>"),
            (Next, "<path d='m5 5 10 7-10 7ZM19 5v14'/>"),
            (Back, "<path d='M3 9h6V3M4 9a8 8 0 1 1 0 7'/>"),
            (Forward, "<path d='M21 9h-6V3m5 6a8 8 0 1 0 0 7'/>"),
            (
                Settings,
                "<path d='M4 6h7m4 0h5M4 12h2m4 0h10M4 18h10m4 0h2'/><circle cx='13' cy='6' r='2'/><circle cx='8' cy='12' r='2'/><circle cx='16' cy='18' r='2'/>",
            ),
            (
                Refresh,
                "<path d='M20 9a8 8 0 0 0-14-4L3 8m0-5v5h5m-4 7a8 8 0 0 0 14 4l3-3m0 5v-5h-5'/>",
            ),
            (
                Sync,
                "<path d='M4 8h16m-4-4 4 4-4 4M20 16H4m4-4-4 4 4 4'/>",
            ),
            (
                Plus,
                "<path d='M11 5h2v6h6v2h-6v6h-2v-6H5v-2h6Z' fill='currentColor' stroke='none'/>",
            ),
            (
                More,
                "<circle cx='5' cy='12' r='1'/><circle cx='12' cy='12' r='1'/><circle cx='19' cy='12' r='1'/>",
            ),
            (ChevronLeft, "<path d='m14 6-6 6 6 6'/>"),
            (ChevronRight, "<path d='m10 6 6 6-6 6'/>"),
            (ChevronDown, "<path d='m6 9 6 6 6-6'/>"),
            (
                Volume,
                "<path d='M4 9h4l5-4v14l-5-4H4ZM17 8a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14'/>",
            ),
            (Moon, "<path d='M20 14a8 8 0 0 1-10-10 8 8 0 1 0 10 10Z'/>"),
            (Close, "<path d='m6 6 12 12M18 6 6 18'/>"),
            (Up, "<path d='m6 14 6-6 6 6'/>"),
            (Down, "<path d='m6 10 6 6 6-6'/>"),
            (Check, "<path d='m5 12 4 4L19 6'/>"),
            (
                Grid,
                "<rect x='3' y='3' width='7' height='7' rx='1.5'/><rect x='14' y='3' width='7' height='7' rx='1.5'/><rect x='3' y='14' width='7' height='7' rx='1.5'/><rect x='14' y='14' width='7' height='7' rx='1.5'/>",
            ),
            (
                List,
                "<path d='M8 6h13M8 12h13M8 18h13'/><circle cx='4' cy='6' r='1'/><circle cx='4' cy='12' r='1'/><circle cx='4' cy='18' r='1'/>",
            ),
            (
                Edit,
                "<path d='M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z'/>",
            ),
            (
                Trash,
                "<path d='M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14M10 11v5M14 11v5'/>",
            ),
            (Sort, "<path d='M4 6h16M7 12h10M10 18h4'/>"),
            (External, "<path d='M14 4h6v6M10 14 20 4M19 14v5H5V5h5'/>"),
        ]
        .into_iter()
        .map(|(name, paths)| {
            (
                name,
                svg::Handle::from_memory(
                    format!(
                        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.7' stroke-linecap='round' stroke-linejoin='round'>{paths}</svg>"
                    )
                    .into_bytes(),
                ),
            )
        })
        .collect()
    });
    svg(handles[&name].clone())
        .width(size)
        .height(size)
        .style(move |_, _| svg::Style { color: Some(color) })
        .into()
}
