use super::{App, ConfirmAction, Msg, RowEntry, format_clock};
use crate::{
    icons::{Icon, icon},
    theme::{self, Button as Kind, Colors},
};
use dusky_casts::{
    db::{Episode, Podcast, UpNextSort, n, s},
    player::TRIMS,
};
use iced::{
    Alignment, Border, Color, ContentFit, Element, Font, Length, Padding, Theme,
    font::Weight,
    widget::{
        self, Space, button, column, container, mouse_area, pick_list, row, scrollable, slider,
        stack, text, text_input, tooltip,
    },
};

type Action<'a> = widget::Button<'a, Msg>;

#[derive(Clone, Copy, PartialEq, Eq)]
struct SortKey(&'static str);
impl std::fmt::Display for SortKey {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(match self.0 {
            "filename" => "Filename",
            "path" => "Path",
            "modified" => "Modified",
            "date" => "Date",
            "duration" => "Duration",
            _ => "Title",
        })
    }
}
const SORT_OPTIONS: &[SortKey] = &[
    SortKey("filename"),
    SortKey("path"),
    SortKey("title"),
    SortKey("modified"),
    SortKey("date"),
    SortKey("duration"),
];

#[derive(Clone, Copy, PartialEq, Eq)]
struct DisplayKey(&'static str);
impl std::fmt::Display for DisplayKey {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(match self.0 {
            "file_title" => "File + Title",
            "title_file" => "Title + File",
            "path" => "Title + Path",
            _ => "Title only",
        })
    }
}
const DISPLAY_OPTIONS: &[DisplayKey] = &[
    DisplayKey("file_title"),
    DisplayKey("title_file"),
    DisplayKey("path"),
    DisplayKey("title"),
];

#[derive(Clone, Copy, PartialEq, Eq)]
struct PodcastsSortKey(&'static str);
impl std::fmt::Display for PodcastsSortKey {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(match self.0 {
            "title" => "Title A–Z",
            "latest" => "Latest episode",
            "added" => "Recently added",
            _ => "Sync order",
        })
    }
}
const PODCASTS_SORT_OPTIONS: &[PodcastsSortKey] = &[
    PodcastsSortKey("sort_order"),
    PodcastsSortKey("latest"),
    PodcastsSortKey("title"),
    PodcastsSortKey("added"),
];

#[derive(Clone, PartialEq, Eq)]
struct FolderOption {
    uuid: String,
    name: String,
}
impl std::fmt::Display for FolderOption {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.name)
    }
}

fn sort_key(value: &str) -> SortKey {
    match value {
        "filename" => SortKey("filename"),
        "path" => SortKey("path"),
        "modified" => SortKey("modified"),
        "date" => SortKey("date"),
        "duration" => SortKey("duration"),
        _ => SortKey("title"),
    }
}

fn display_key(value: &str) -> DisplayKey {
    match value {
        "title_file" => DisplayKey("title_file"),
        "path" => DisplayKey("path"),
        "title" => DisplayKey("title"),
        _ => DisplayKey("file_title"),
    }
}

fn podcasts_sort_key(value: &str) -> PodcastsSortKey {
    match value {
        "title" => PodcastsSortKey("title"),
        "latest" => PodcastsSortKey("latest"),
        "added" => PodcastsSortKey("added"),
        _ => PodcastsSortKey("sort_order"),
    }
}

fn format_rate(rate: f64) -> String {
    let rounded = (rate * 100.0).round() as i64;
    if rounded % 10 == 0 {
        format!("{:.1}×", rate)
    } else {
        format!("{:.2}×", rate)
    }
}

fn rate_control<'a>(c: Colors, rate: f64) -> Element<'a, Msg> {
    let minus = button(label("−", 13, c.fg))
        .padding([4, 8])
        .on_press(Msg::StepSpeed(-0.1))
        .style(move |_, s| theme::button_style(c, Kind::Quiet, s));
    let reset = tooltip(
        button(strong(format_rate(rate), 12, c.accent))
            .padding([4, 7])
            .on_press(Msg::StepSpeed(0.0))
            .style(move |_, s| theme::button_style(c, Kind::Soft, s)),
        container(label("Reset to 1.0×", 11, c.fg))
            .padding([5, 8])
            .style(move |_| theme::panel(c, c.hover, 7.0)),
        tooltip::Position::Top,
    )
    .gap(5);
    let plus = button(label("+", 13, c.fg))
        .padding([4, 8])
        .on_press(Msg::StepSpeed(0.1))
        .style(move |_, s| theme::button_style(c, Kind::Quiet, s));
    let dial = slider(0.5..=3.0, rate.clamp(0.5, 3.0), Msg::Speed)
        .step(0.05)
        .on_release(Msg::SaveSpeed)
        .width(80)
        .height(14)
        .style(move |_, s| theme::slider_style(c, s));
    container(
        row![minus, dial, reset, plus]
            .spacing(4)
            .align_y(Alignment::Center),
    )
    .padding([2, 6])
    .style(move |_| theme::panel(c, c.card, 8.0))
    .into()
}

fn chip<'a>(c: Colors, caption: impl Into<String>, msg: Option<Msg>, kind: Kind) -> Action<'a> {
    button(text(caption.into()).size(11))
        .padding([5, 9])
        .on_press_maybe(msg)
        .style(move |_, s| theme::button_style(c, kind, s))
}

fn label<'a>(value: impl Into<String>, size: u32, color: Color) -> widget::Text<'a> {
    text(value.into()).size(size).color(color)
}

fn strong<'a>(value: impl Into<String>, size: u32, color: Color) -> widget::Text<'a> {
    label(value, size, color).font(Font {
        weight: Weight::Semibold,
        ..Font::with_name("Atkinson Hyperlegible")
    })
}

fn short_tail(value: &str, limit: usize) -> String {
    let count = value.chars().count();
    if count <= limit {
        return value.to_owned();
    }
    let keep = limit.saturating_sub(1);
    format!("…{}", value.chars().skip(count - keep).collect::<String>())
}

fn file_name(path: &str) -> &str {
    path.rsplit('/').next().unwrap_or(path)
}

fn action<'a>(c: Colors, value: impl Into<String>, msg: Option<Msg>, kind: Kind) -> Action<'a> {
    button(text(value.into()).size(13))
        .padding([8, 12])
        .on_press_maybe(msg)
        .style(move |_, s| theme::button_style(c, kind, s))
}

fn confirm_chip<'a>(
    c: Colors,
    caption: &'static str,
    confirm_caption: &'static str,
    target: ConfirmAction,
    current: &Option<ConfirmAction>,
    idle_kind: Kind,
) -> Element<'a, Msg> {
    if current.as_ref() == Some(&target) {
        row![
            chip(c, confirm_caption, Some(Msg::Confirm), Kind::Danger),
            chip(c, "Cancel", Some(Msg::CancelConfirm), Kind::Quiet),
        ]
        .spacing(4)
        .align_y(Alignment::Center)
        .into()
    } else {
        chip(c, caption, Some(Msg::RequestConfirm(target)), idle_kind).into()
    }
}

fn control<'a>(
    c: Colors,
    name: Icon,
    tip: &'static str,
    msg: Option<Msg>,
    kind: Kind,
    size: f32,
) -> Element<'a, Msg> {
    let color = if matches!(kind, Kind::Accent) {
        c.on_accent
    } else if matches!(kind, Kind::Selected) {
        c.accent
    } else {
        c.muted
    };
    let color = if msg.is_some() {
        color
    } else {
        theme::mix(c.bg, color, 0.35)
    };
    let b = button(
        container(icon(name, if size > 36.0 { 22.0 } else { 17.0 }, color))
            .center_x(size)
            .center_y(size),
    )
    .padding(0)
    .on_press_maybe(msg)
    .style(move |_, s| {
        let mut style = theme::button_style(c, kind, s);
        if size > 36.0 {
            style.border.radius = (size / 2.0).into();
        }
        style
    });
    tooltip(
        b,
        container(label(tip, 12, c.fg))
            .padding([6, 9])
            .style(move |_| theme::panel(c, c.hover, 7.0)),
        tooltip::Position::Top,
    )
    .gap(6)
    .into()
}

fn row_control<'a>(
    c: Colors,
    name: Icon,
    msg: Option<Msg>,
    kind: Kind,
    size: f32,
) -> Element<'a, Msg> {
    let color = if matches!(kind, Kind::Accent) {
        c.on_accent
    } else if matches!(kind, Kind::Selected) {
        c.accent
    } else {
        c.muted
    };
    let color = if msg.is_some() {
        color
    } else {
        theme::mix(c.bg, color, 0.35)
    };
    button(
        container(icon(name, if size > 36.0 { 22.0 } else { 16.0 }, color))
            .center_x(size)
            .center_y(size),
    )
    .padding(0)
    .on_press_maybe(msg)
    .style(move |_, s| theme::button_style(c, kind, s))
    .into()
}

fn scroll_with_gutter<'a>(
    c: Colors,
    gutter: f32,
    content: impl Into<Element<'a, Msg>>,
) -> widget::Scrollable<'a, Msg> {
    let wrapped: Element<'a, Msg> = if gutter > 0.0 {
        container(content)
            .width(Length::Fill)
            .padding(Padding {
                top: 0.0,
                right: gutter,
                bottom: 0.0,
                left: 0.0,
            })
            .into()
    } else {
        content.into()
    };
    scrollable(wrapped)
        .direction(scrollable::Direction::Vertical(theme::scrollbar()))
        .style(move |t: &Theme, s| theme::scroll(t, c, s))
}

fn scroll<'a>(c: Colors, content: impl Into<Element<'a, Msg>>) -> widget::Scrollable<'a, Msg> {
    scroll_with_gutter(c, 12.0, content)
}

fn short(value: &str, limit: usize) -> String {
    let mut chars = value.chars();
    let prefix: String = chars.by_ref().take(limit).collect();
    if chars.next().is_some() {
        format!("{prefix}…")
    } else {
        prefix
    }
}

fn human_duration(value: f64) -> String {
    let s = value.max(0.0).round() as u64;
    let d = s / 86_400;
    let h = (s % 86_400) / 3600;
    let m = (s % 3600) / 60;
    let sec = s % 60;
    if d > 0 {
        format!("{d}d {h}h {m}m")
    } else if h > 0 {
        format!("{h}h {m}m")
    } else if m > 0 {
        format!("{m}m {sec}s")
    } else {
        format!("{sec}s")
    }
}

fn plain(html: &str) -> String {
    let mut output = String::new();
    let mut tag = false;
    for ch in html.chars() {
        match ch {
            '<' => {
                tag = true;
                output.push(' ');
            }
            '>' => tag = false,
            _ if !tag => output.push(ch),
            _ => {}
        }
    }
    let decoded = output
        .replace("&amp;", "&")
        .replace("&nbsp;", " ")
        .replace("&quot;", "\"")
        .replace("&#39;", "'")
        .replace("&lt;", "<")
        .replace("&gt;", ">");
    decoded.split_whitespace().collect::<Vec<_>>().join(" ")
}

impl App {
    pub(super) fn cover<'a>(&self, c: Colors, uuid: Option<&str>, size: f32) -> Element<'a, Msg> {
        if let Some(handle) = uuid.and_then(|u| self.artwork_handles.get(u)) {
            widget::image(handle.clone())
                .width(size)
                .height(size)
                .content_fit(ContentFit::Cover)
                .border_radius(9.0)
                .into()
        } else {
            container(icon(Icon::Audio, size * 0.43, c.accent))
                .center_x(size)
                .center_y(size)
                .style(move |_| theme::panel(c, theme::mix(c.card, c.accent, 0.06), 9.0))
                .into()
        }
    }

    pub(super) fn view(&self) -> Element<'_, Msg> {
        let c = self.appearance.colors;
        let compact = self.is_compact();
        let dense = self.is_dense();
        let content: Element<'_, Msg> = if let Some(e) = &self.info {
            self.info_view(e)
        } else if let Some(pod) = self.selected_podcast() {
            self.podcast_detail_view(pod, compact, dense)
        } else {
            match self.nav.as_str() {
                "Podcasts" => self.podcasts_view(compact, dense),
                "Account" => self.account_view(compact),
                "Discover" => self.discover_view(),
                "Organize" => self.organize_view(),
                "Local files" => {
                    let folder: Element<'_, Msg> =
                        if !self.local_path.is_empty() && !self.editing_local_path {
                            column![
                                strong(
                                    format!(
                                        "Local files  ·  {}",
                                        short_tail(&self.local_path, if compact { 30 } else { 70 })
                                    ),
                                    12,
                                    c.fg
                                ),
                                row![
                                    icon(Icon::Folder, 15.0, c.muted),
                                    Space::new().width(Length::Fill),
                                    control(
                                        c,
                                        Icon::Edit,
                                        "Change local folder",
                                        Some(Msg::ToggleLocalPathEdit),
                                        Kind::Quiet,
                                        26.0
                                    ),
                                    control(
                                        c,
                                        Icon::Refresh,
                                        "Rescan local folder",
                                        (!self.busy).then_some(Msg::ScanLocal),
                                        Kind::Soft,
                                        26.0
                                    )
                                ]
                                .spacing(2)
                                .align_y(Alignment::Center)
                            ]
                            .spacing(1)
                            .width(Length::Fill)
                            .into()
                        } else {
                            let mut buttons = row![action(
                                c,
                                "Save & scan",
                                (!self.busy).then_some(Msg::ScanLocal),
                                Kind::Accent
                            )]
                            .spacing(6);
                            if !self.local_path.is_empty() {
                                buttons = buttons.push(action(
                                    c,
                                    "Cancel",
                                    Some(Msg::CancelLocalPathEdit),
                                    Kind::Quiet,
                                ));
                            }
                            column![
                            strong("Local files", if dense { 15 } else { 18 }, c.fg),
                            label(
                                "Choose a folder. Subfolders are included; files stay in place.",
                                11,
                                c.muted
                            ),
                            text_input("/path/to/audio or ~/Podcasts", &self.local_path)
                                .on_input(Msg::LocalPath)
                                .on_submit(Msg::ScanLocal)
                                .size(13)
                                .padding([7, 10])
                                .style(move |_, s| theme::input(c, s)),
                            buttons
                        ]
                        .spacing(5)
                        .into()
                        };
                    column![folder, self.episode_view(compact, dense, false)]
                        .spacing(if dense { 6 } else { 8 })
                        .width(Length::Fill)
                        .height(Length::Fill)
                        .into()
                }
                _ => self.episode_view(compact, dense, false),
            }
        };
        let is_auxiliary_view = self.info.is_some()
            || matches!(
                self.nav.as_str(),
                "Podcasts" | "Account" | "Discover" | "Organize"
            );
        let mut main_col = column![content]
            .spacing(if dense { 5 } else { 8 })
            .height(Length::Fill);
        if is_auxiliary_view && (self.busy || self.failures > 0 || self.status_is_error) {
            main_col = main_col.push(self.status_bar(compact));
        }
        let main = container(main_col)
            .padding(if dense {
                [8, 14]
            } else if compact {
                [12, 14]
            } else {
                [12, 20]
            })
            .width(Length::Fill)
            .height(Length::Fill);
        let divider = container(Space::new())
            .width(1)
            .height(Length::Fill)
            .style(move |_| theme::panel(c, theme::mix(c.bg, c.border, 0.32), 0.0));
        let workspace: Element<'_, Msg> = row![self.sidebar(compact), divider, main]
            .height(Length::Fill)
            .width(Length::Fill)
            .into();
        let top_region: Element<'_, Msg> = if self.queue_drawer_open {
            stack![workspace, self.queue_drawer(compact)]
                .width(Length::Fill)
                .height(Length::Fill)
                .into()
        } else {
            workspace
        };
        container(column![top_region, self.player_view(compact, dense)].spacing(0))
            .style(move |_| theme::panel(c, c.bg, 0.0))
            .width(Length::Fill)
            .height(Length::Fill)
            .into()
    }

    fn sidebar(&self, compact: bool) -> Element<'_, Msg> {
        let c = self.appearance.colors;
        let mark = container(icon(Icon::Audio, 22.0, c.accent))
            .center_x(38)
            .center_y(38)
            .style(move |_| theme::panel(c, theme::mix(c.bg, c.accent, 0.09), 12.0));
        let brand: Element<'_, Msg> = if compact {
            mark.into()
        } else {
            row![
                mark,
                column![
                    strong("Dusky Casts", 18, c.fg),
                    label("Your listening library", 10, c.muted)
                ]
                .spacing(3)
            ]
            .spacing(10)
            .align_y(Alignment::Center)
            .into()
        };
        let mut nav = column![].spacing(2);
        if !compact {
            nav = nav.push(container(label("LIBRARY", 10, c.muted)).padding([8, 10]));
        }
        for (name, ico, badge) in [
            (
                "Podcasts",
                Icon::Grid,
                (!self.podcasts.is_empty()).then_some(self.podcasts.len()),
            ),
            ("All episodes", Icon::Library, None),
            ("In progress", Icon::Progress, None),
            (
                "Up Next",
                Icon::Queue,
                (self.cloud_queue_count > 0).then_some(self.cloud_queue_count),
            ),
            ("Local files", Icon::Folder, None),
            (
                "Local queue",
                Icon::Audio,
                (self.local_queue_count > 0).then_some(self.local_queue_count),
            ),
            ("Downloads", Icon::Download, None),
            ("Starred", Icon::Star, None),
            ("History", Icon::History, None),
            ("Archived", Icon::Archive, None),
        ] {
            nav = nav.push(self.nav_item(
                c,
                name,
                ico,
                badge,
                Msg::Nav(name.into()),
                self.nav == name && self.podcast == 0,
                compact,
            ));
        }
        for (heading, items) in [("FOLDERS", &self.folders), ("PLAYLISTS", &self.playlists)] {
            if !compact && !items.is_empty() {
                nav = nav.push(container(label(heading, 10, c.muted)).padding([12, 10]));
            }
            for p in items {
                let (name, target) = if heading == "FOLDERS" {
                    (s(p, "name"), format!("folder:{}", s(p, "uuid")))
                } else {
                    (s(p, "title"), format!("playlist:{}", n(p, "id")))
                };
                nav = nav.push(self.nav_item(
                    c,
                    name,
                    if heading == "FOLDERS" {
                        Icon::Folder
                    } else {
                        Icon::Queue
                    },
                    None,
                    Msg::Nav(target.clone()),
                    self.nav == target && self.podcast == 0,
                    compact,
                ));
            }
        }
        if !compact && !self.podcasts.is_empty() {
            nav = nav.push(container(label("PODCASTS", 10, c.muted)).padding([12, 10]));
        }
        for p in &self.podcasts {
            nav = nav.push(self.nav_item(
                c,
                &p.title,
                Icon::Audio,
                (p.unplayed_count > 0).then_some(p.unplayed_count as usize),
                Msg::Podcast(p.id),
                self.podcast == p.id,
                compact,
            ));
        }
        let footer = column![
            self.nav_item(
                c,
                "Discover",
                Icon::Search,
                None,
                Msg::Nav("Discover".into()),
                self.nav == "Discover" && self.podcast == 0,
                compact
            ),
            self.nav_item(
                c,
                "Organize",
                Icon::Folder,
                None,
                Msg::Nav("Organize".into()),
                self.nav == "Organize" && self.podcast == 0,
                compact
            ),
            self.nav_item(
                c,
                "Account",
                Icon::Account,
                None,
                Msg::Nav("Account".into()),
                self.nav == "Account" && self.podcast == 0,
                compact
            )
        ]
        .spacing(2);
        container(
            column![
                container(brand).padding([6, 4]),
                scroll_with_gutter(c, if compact { 0.0 } else { 12.0 }, nav).height(Length::Fill),
                footer
            ]
            .spacing(12),
        )
        .padding(if compact { 10 } else { 14 })
        .width(if compact { 64 } else { 228 })
        .height(Length::Fill)
        .style(move |_| theme::panel(c, c.sidebar, 0.0))
        .into()
    }

    #[allow(clippy::too_many_arguments)]
    fn nav_item<'a>(
        &self,
        c: Colors,
        name: &str,
        ico: Icon,
        badge: Option<usize>,
        msg: Msg,
        selected: bool,
        compact: bool,
    ) -> Element<'a, Msg> {
        let color = if selected { c.accent } else { c.muted };
        let mut contents = row![icon(ico, 16.0, color)]
            .spacing(10)
            .align_y(Alignment::Center);
        if !compact {
            contents = contents.push(
                label(
                    short(name, if badge.is_some() { 18 } else { 22 }),
                    13,
                    if selected { c.fg } else { c.muted },
                )
                .width(Length::Fill),
            );
            if let Some(count) = badge {
                contents = contents.push(
                    container(label(
                        count.to_string(),
                        10,
                        if selected { c.accent } else { c.muted },
                    ))
                    .padding([1, 6])
                    .style(move |_| theme::panel(c, theme::mix(c.card, c.hover, 0.5), 9.0)),
                );
            }
        }
        let btn = button(contents)
            .padding([8, 10])
            .width(Length::Fill)
            .on_press(msg)
            .style(move |_, s| {
                theme::button_style(
                    c,
                    if selected {
                        Kind::Selected
                    } else {
                        Kind::Quiet
                    },
                    s,
                )
            });
        if compact {
            tooltip(
                btn,
                container(label(name, 12, c.fg))
                    .padding([5, 8])
                    .style(move |_| theme::panel(c, c.hover, 7.0)),
                tooltip::Position::Right,
            )
            .into()
        } else {
            btn.into()
        }
    }

    pub(super) fn status_bar(&self, compact: bool) -> Element<'_, Msg> {
        let c = self.appearance.colors;
        let has_error = self.failures > 0 || self.status_is_error;
        let status = if self.busy {
            "Updating library"
        } else if self.failures > 0 {
            "Sync needs attention"
        } else if self.status_is_error {
            "Attention"
        } else if self.email.is_empty() {
            "Local library"
        } else {
            "Library synced"
        };
        let dot = container(Space::new()).width(6).height(6).style(move |_| {
            theme::panel(
                c,
                if has_error {
                    c.danger
                } else if self.busy {
                    c.accent
                } else {
                    c.muted
                },
                3.0,
            )
        });
        let mut bar = row![dot, label(status, 11, c.muted)]
            .spacing(6)
            .align_y(Alignment::Center);
        if has_error {
            bar = bar.push(label(
                short(&self.status, if compact { 24 } else { 55 }),
                11,
                c.danger,
            ));
        }
        bar.into()
    }

    fn podcasts_view(&self, compact: bool, dense: bool) -> Element<'_, Msg> {
        let c = self.appearance.colors;
        let q = self.podcasts_query.trim().to_ascii_lowercase();
        let mut filtered: Vec<&Podcast> = self
            .podcasts
            .iter()
            .filter(|p| match self.podcasts_folder.as_str() {
                "all" => true,
                "home" => p.folder_uuid.is_empty(),
                uuid => p.folder_uuid == uuid,
            })
            .filter(|p| {
                q.is_empty()
                    || p.title.to_ascii_lowercase().contains(&q)
                    || p.author.to_ascii_lowercase().contains(&q)
            })
            .collect();

        match self.podcasts_sort.as_str() {
            "title" => filtered.sort_by(|a, b| {
                a.title
                    .to_ascii_lowercase()
                    .cmp(&b.title.to_ascii_lowercase())
            }),
            "latest" => filtered.sort_by(|a, b| {
                b.latest_published_at
                    .cmp(&a.latest_published_at)
                    .then_with(|| a.title.cmp(&b.title))
            }),
            "added" => filtered.sort_by(|a, b| {
                b.added_at
                    .cmp(&a.added_at)
                    .then_with(|| a.title.cmp(&b.title))
            }),
            _ => filtered.sort_by(|a, b| {
                a.sort_order
                    .cmp(&b.sort_order)
                    .then_with(|| a.title.cmp(&b.title))
            }),
        }

        let search_box = container(
            row![
                icon(Icon::Search, 16.0, c.muted),
                text_input("Filter podcasts", &self.podcasts_query)
                    .on_input(Msg::PodcastsQuery)
                    .size(13)
                    .padding([if dense { 5 } else { 7 }, 0])
                    .style(move |_, s| {
                        let mut v = theme::input(c, s);
                        v.background = Color::TRANSPARENT.into();
                        v.border = Border::default();
                        v
                    })
            ]
            .spacing(8)
            .align_y(Alignment::Center),
        )
        .padding([0, 10])
        .width(Length::Fixed(if compact { 170.0 } else { 240.0 }))
        .style(move |_| theme::panel(c, c.card, 10.0));

        let sort_picker = pick_list(
            PODCASTS_SORT_OPTIONS,
            Some(podcasts_sort_key(&self.podcasts_sort)),
            |k| Msg::PodcastsSort(k.0.to_owned()),
        )
        .text_size(12)
        .padding([6, 9])
        .style(move |_, status| theme::pick(c, status));

        let layout_toggle = row![
            control(
                c,
                Icon::Grid,
                "Artwork grid",
                Some(Msg::PodcastsGridToggle(true)),
                if self.podcasts_grid {
                    Kind::Selected
                } else {
                    Kind::Quiet
                },
                28.0
            ),
            control(
                c,
                Icon::List,
                "Compact list",
                Some(Msg::PodcastsGridToggle(false)),
                if !self.podcasts_grid {
                    Kind::Selected
                } else {
                    Kind::Quiet
                },
                28.0
            ),
        ]
        .spacing(2);

        let header = row![
            strong("Podcasts", if dense { 18 } else { 21 }, c.fg),
            label(format!("{} shows", filtered.len()), 12, c.muted),
            Space::new().width(Length::Fill),
            search_box,
            sort_picker,
            layout_toggle,
            control(
                c,
                Icon::Refresh,
                "Refresh all podcasts",
                (!self.busy).then_some(Msg::Refresh),
                Kind::Quiet,
                28.0
            ),
            control(
                c,
                Icon::Sync,
                "Sync with your account",
                (!self.busy).then_some(Msg::Sync),
                Kind::Quiet,
                28.0
            ),
        ]
        .spacing(8)
        .align_y(Alignment::Center);

        let mut top = column![header].spacing(8);
        if !self.folders.is_empty() {
            let mut folder_bar = row![
                chip(
                    c,
                    "All",
                    Some(Msg::PodcastsFolderFilter("all".into())),
                    if self.podcasts_folder == "all" {
                        Kind::Selected
                    } else {
                        Kind::Quiet
                    }
                ),
                chip(
                    c,
                    "Home",
                    Some(Msg::PodcastsFolderFilter("home".into())),
                    if self.podcasts_folder == "home" {
                        Kind::Selected
                    } else {
                        Kind::Quiet
                    }
                ),
            ]
            .spacing(5)
            .align_y(Alignment::Center);
            for f in &self.folders {
                let uuid = s(f, "uuid").to_owned();
                let active = self.podcasts_folder == uuid;
                folder_bar = folder_bar.push(chip(
                    c,
                    s(f, "name"),
                    Some(Msg::PodcastsFolderFilter(uuid)),
                    if active { Kind::Selected } else { Kind::Quiet },
                ));
            }
            top = top.push(folder_bar.wrap());
        }

        let body: Element<'_, Msg> = if self.podcasts.is_empty() {
            container(
                column![
                    icon(Icon::Audio, 38.0, c.accent),
                    strong("No subscribed podcasts yet", 20, c.fg),
                    label(
                        "Discover a show, add an RSS feed, or sign in to sync your Pocket Casts library.",
                        13,
                        c.muted
                    ),
                    row![
                        action(
                            c,
                            "Discover podcasts",
                            Some(Msg::Nav("Discover".into())),
                            Kind::Accent
                        ),
                        action(
                            c,
                            "Sign in",
                            Some(Msg::Nav("Account".into())),
                            Kind::Soft
                        ),
                    ]
                    .spacing(10)
                ]
                .spacing(14)
                .align_x(Alignment::Center),
            )
            .center_x(Length::Fill)
            .padding([52, 12])
            .into()
        } else if filtered.is_empty() {
            container(label("No podcasts match the current filter.", 13, c.muted))
                .center_x(Length::Fill)
                .padding([36, 12])
                .into()
        } else if self.podcasts_grid {
            let avail_w = (self.window_size.width - if compact { 104.0 } else { 280.0 }).max(320.0);
            let cols = ((avail_w / 190.0).floor() as usize).clamp(2, 6);
            let art_size = ((avail_w / cols as f32) - 36.0).clamp(112.0, 148.0);
            let card_height = art_size + 68.0;
            let mut rows_col = column![].spacing(12);
            for chunk in filtered.chunks(cols) {
                let mut r = row![].spacing(12).width(Length::Fill);
                for p in chunk {
                    let badge_text = if p.unplayed_count > 0 {
                        format!("{} unplayed", p.unplayed_count)
                    } else {
                        format!("{} eps", p.episode_count)
                    };
                    let badge_color = if p.unplayed_count > 0 {
                        c.accent
                    } else {
                        c.muted
                    };
                    let card_body = column![
                        container(self.cover(c, Some(&p.uuid), art_size)).center_x(Length::Fill),
                        strong(short(&p.title, 22), 13, c.fg).wrapping(text::Wrapping::None),
                        row![
                            label(short(&p.author, 13), 11, c.muted)
                                .wrapping(text::Wrapping::None)
                                .width(Length::Fill),
                            label(badge_text, 10, badge_color)
                        ]
                        .spacing(6)
                        .align_y(Alignment::Center)
                    ]
                    .spacing(5)
                    .width(Length::Fill);
                    r = r.push(
                        button(card_body)
                            .padding(10)
                            .width(Length::FillPortion(1))
                            .height(Length::Fixed(card_height))
                            .on_press(Msg::Podcast(p.id))
                            .style(move |_, s| theme::button_style(c, Kind::Soft, s)),
                    );
                }
                for _ in chunk.len()..cols {
                    r = r.push(Space::new().width(Length::FillPortion(1)));
                }
                rows_col = rows_col.push(r);
            }
            scroll(c, rows_col).height(Length::Fill).into()
        } else {
            let mut list_col = column![].spacing(4);
            for p in filtered {
                let latest = chrono::DateTime::from_timestamp(p.latest_published_at, 0)
                    .filter(|_| p.latest_published_at > 0)
                    .map(|d| d.format("%b %-d, %Y").to_string())
                    .unwrap_or_default();
                let meta = if p.folder_name.is_empty() {
                    format!("{}  ·  {} episodes", p.author, p.episode_count)
                } else {
                    format!(
                        "{}  ·  {}  ·  {} episodes",
                        p.author, p.folder_name, p.episode_count
                    )
                };
                let badge: Element<'_, Msg> = if p.unplayed_count > 0 {
                    container(strong(
                        format!("{} unplayed", p.unplayed_count),
                        11,
                        c.accent,
                    ))
                    .padding([3, 8])
                    .style(move |_| theme::panel(c, theme::mix(c.card, c.accent, 0.14), 8.0))
                    .into()
                } else {
                    label("All caught up", 11, c.muted).into()
                };
                let row_inner = row![
                    self.cover(c, Some(&p.uuid), 38.0),
                    column![
                        strong(short(&p.title, 70), 14, c.fg).wrapping(text::Wrapping::None),
                        label(short(&meta, 85), 11, c.muted).wrapping(text::Wrapping::None)
                    ]
                    .spacing(2)
                    .width(Length::Fill),
                    label(latest, 11, c.muted),
                    badge,
                    icon(Icon::ChevronRight, 15.0, c.muted)
                ]
                .spacing(12)
                .align_y(Alignment::Center);
                list_col = list_col.push(
                    button(row_inner)
                        .padding([7, 12])
                        .width(Length::Fill)
                        .on_press(Msg::Podcast(p.id))
                        .style(move |_, s| theme::button_style(c, Kind::Soft, s)),
                );
            }
            scroll(c, list_col).height(Length::Fill).into()
        };

        column![top, body]
            .spacing(10)
            .width(Length::Fill)
            .height(Length::Fill)
            .into()
    }

    fn podcast_detail_view<'a>(
        &'a self,
        podcast: &'a Podcast,
        compact: bool,
        dense: bool,
    ) -> Element<'a, Msg> {
        let c = self.appearance.colors;
        let pid = podcast.id;

        let mut folder_options = vec![FolderOption {
            uuid: String::new(),
            name: "Home (No folder)".into(),
        }];
        for f in &self.folders {
            folder_options.push(FolderOption {
                uuid: s(f, "uuid").to_owned(),
                name: s(f, "name").to_owned(),
            });
        }
        let selected_folder = folder_options
            .iter()
            .find(|opt| opt.uuid == podcast.folder_uuid)
            .cloned()
            .or_else(|| folder_options.first().cloned());

        let folder_picker = pick_list(folder_options, selected_folder, move |opt| {
            Msg::Assign(pid, opt.uuid)
        })
        .text_size(12)
        .padding([5, 9])
        .style(move |_, status| theme::pick(c, status));

        let summary_line = format!(
            "{} unplayed  ·  {} total  ·  {} archived",
            podcast.unplayed_count, podcast.episode_count, podcast.archived_count
        );
        let desc_clean = plain(&podcast.description);
        let has_long_desc = desc_clean.chars().count() > 140;
        let desc_display = if self.podcast_desc_expanded {
            desc_clean
        } else {
            short(&desc_clean, 140)
        };

        let mut title_block = column![
            strong(
                short(&podcast.title, if compact { 36 } else { 80 }),
                if dense { 17 } else { 20 },
                c.fg
            ),
            label(
                if podcast.author.is_empty() {
                    summary_line
                } else {
                    format!("{}  ·  {summary_line}", podcast.author)
                },
                11,
                c.muted
            )
        ]
        .spacing(3)
        .width(Length::Fill);

        if !desc_display.is_empty() {
            let mut desc_row = row![label(desc_display, 12, c.muted).width(Length::Fill)]
                .spacing(6)
                .align_y(Alignment::Center);
            if has_long_desc {
                desc_row = desc_row.push(chip(
                    c,
                    if self.podcast_desc_expanded {
                        "Show less"
                    } else {
                        "Show more"
                    },
                    Some(Msg::TogglePodcastDesc),
                    Kind::Quiet,
                ));
            }
            title_block = title_block.push(desc_row);
        }

        let back_chip = chip(c, "← Podcasts", Some(Msg::BackFromPodcast), Kind::Quiet);
        let right_controls = row![
            folder_picker,
            chip(
                c,
                "Refresh",
                (!self.busy).then_some(Msg::RefreshPodcast(pid)),
                Kind::Soft
            ),
            chip(
                c,
                "Mark all played",
                (podcast.unplayed_count > 0).then_some(Msg::PodcastMarkAllPlayed(pid)),
                Kind::Quiet
            ),
            chip(
                c,
                "Archive played",
                (podcast.episode_count > podcast.archived_count)
                    .then_some(Msg::PodcastArchivePlayed(pid)),
                Kind::Quiet
            ),
            confirm_chip(
                c,
                "Unfollow",
                "Confirm unfollow",
                ConfirmAction::Unsubscribe(pid),
                &self.confirm_action,
                Kind::Danger
            )
        ]
        .spacing(6)
        .align_y(Alignment::Center);

        let header_actions: Element<'_, Msg> = if compact {
            column![back_chip, right_controls.wrap()].spacing(6).into()
        } else {
            row![back_chip, Space::new().width(Length::Fill), right_controls]
                .spacing(6)
                .align_y(Alignment::Center)
                .width(Length::Fill)
                .into()
        };

        let header_card = container(
            column![
                header_actions,
                row![
                    self.cover(c, Some(&podcast.uuid), if dense { 68.0 } else { 84.0 }),
                    title_block
                ]
                .spacing(14)
                .align_y(Alignment::Center)
            ]
            .spacing(if dense { 6 } else { 8 }),
        )
        .padding(if dense { [8, 12] } else { [12, 14] })
        .width(Length::Fill)
        .style(move |_| theme::panel(c, c.card, 12.0));

        let mut filters_group = row![].spacing(4).align_y(Alignment::Center);
        for f in [
            "Active",
            "Unplayed",
            "In Progress",
            "Downloaded",
            "Archived",
            "All",
        ] {
            filters_group = filters_group.push(chip(
                c,
                f,
                Some(Msg::PodcastFilter(f.into())),
                if self.podcast_filter == f {
                    Kind::Selected
                } else {
                    Kind::Quiet
                },
            ));
        }
        let mut sort_group = row![label("Sort:", 11, c.muted)]
            .spacing(4)
            .align_y(Alignment::Center);
        for (code, caption) in [
            (0, "Newest"),
            (1, "Oldest"),
            (2, "Shortest"),
            (3, "Longest"),
        ] {
            sort_group = sort_group.push(chip(
                c,
                caption,
                Some(Msg::PodcastSort(code)),
                if podcast.episodes_sort == code {
                    Kind::Selected
                } else {
                    Kind::Quiet
                },
            ));
        }
        let filter_bar: Element<'_, Msg> = if compact {
            column![filters_group.wrap(), sort_group.wrap()]
                .spacing(4)
                .into()
        } else {
            row![filters_group, Space::new().width(Length::Fill), sort_group]
                .spacing(8)
                .align_y(Alignment::Center)
                .width(Length::Fill)
                .into()
        };

        column![
            header_card,
            filter_bar,
            self.episode_view(compact, dense, true)
        ]
        .spacing(if dense { 6 } else { 8 })
        .width(Length::Fill)
        .height(Length::Fill)
        .into()
    }

    fn episode_view(
        &self,
        compact: bool,
        dense: bool,
        hide_podcast_meta: bool,
    ) -> Element<'_, Msg> {
        let c = self.appearance.colors;
        let is_local_view = matches!(self.nav.as_str(), "Local files" | "Local queue");
        let is_queue_view = matches!(self.nav.as_str(), "Up Next" | "Local queue");
        let manual_playlist_id = self
            .nav
            .strip_prefix("playlist:")
            .and_then(|s| s.parse::<i64>().ok())
            .filter(|id| {
                self.playlists
                    .iter()
                    .any(|p| n(p, "id") == *id && n(p, "manual") != 0)
            });
        let is_reorderable = is_queue_view || manual_playlist_id.is_some();
        let search = container(
            row![
                icon(Icon::Search, 16.0, c.muted),
                text_input(
                    if is_local_view {
                        "Search local audio"
                    } else if hide_podcast_meta {
                        "Search this podcast"
                    } else {
                        "Search your episodes"
                    },
                    &self.query
                )
                .on_input(Msg::Filter)
                .size(13)
                .padding([if dense { 5 } else { 7 }, 0])
                .style(move |_, s| {
                    let mut v = theme::input(c, s);
                    v.background = Color::TRANSPARENT.into();
                    v.border = Border::default();
                    v
                })
            ]
            .spacing(8)
            .align_y(Alignment::Center),
        )
        .padding([0, if dense { 9 } else { 12 }])
        .style(move |_| theme::panel(c, c.card, 10.0));

        let player = self.player.as_ref().map(|p| p.state()).unwrap_or_default();
        let (total_count, total_remaining, active_db_remaining) = self.view_stats;
        let speed = self.speed.max(0.25);
        let speed_tag = if (speed - 1.0).abs() > 0.01 {
            format!(" ({})", format_rate(speed))
        } else {
            String::new()
        };
        let remaining = if let (Some(current), Some(active_db)) =
            (&self.current, active_db_remaining)
            && player.loaded
        {
            let duration = if player.duration > 0.0 {
                player.duration
            } else {
                current.duration as f64
            };
            (total_remaining - active_db + (duration - player.position.max(0.0)).max(0.0)).max(0.0)
        } else {
            total_remaining
        };
        let count_text = if total_count == 0 {
            if is_local_view {
                "No files".into()
            } else {
                "No episodes".into()
            }
        } else if is_local_view {
            format!(
                "{total_count} {} · {}{} left",
                if self.nav == "Local queue" {
                    "queued"
                } else {
                    "files"
                },
                human_duration(remaining / speed),
                speed_tag
            )
        } else {
            format!(
                "{total_count} {} · {}{}",
                if is_queue_view { "queued" } else { "episodes" },
                human_duration(remaining / speed),
                speed_tag
            )
        };
        let mut meta_bar = row![label(count_text, 11, c.muted)]
            .spacing(if compact { 8 } else { 12 })
            .align_y(Alignment::Center);
        if !is_local_view || self.busy || self.failures > 0 || self.status_is_error {
            meta_bar = meta_bar.push(self.status_bar(compact));
        }

        let search_and_controls: Element<'_, Msg> = if is_local_view {
            let sort = pick_list(SORT_OPTIONS, Some(sort_key(&self.local_sort)), |key| {
                Msg::LocalSort(key.0.to_owned())
            })
            .text_size(12)
            .padding([6, 8])
            .style(move |_, status| theme::pick(c, status));
            let display = pick_list(
                DISPLAY_OPTIONS,
                Some(display_key(&self.local_display)),
                |key| Msg::LocalDisplay(key.0.to_owned()),
            )
            .text_size(12)
            .padding([6, 8])
            .style(move |_, status| theme::pick(c, status));
            let direction_icon = if self.local_sort_asc {
                Icon::Up
            } else {
                Icon::Down
            };
            let mut controls = row![
                chip(
                    c,
                    if self.selected.is_empty() {
                        "Select all"
                    } else {
                        "Clear sel"
                    },
                    (!self.view_ids.is_empty()).then_some(if self.selected.is_empty() {
                        Msg::SelectAll
                    } else {
                        Msg::ClearSelection
                    }),
                    Kind::Quiet,
                ),
                control(
                    c,
                    Icon::Queue,
                    if self.local_autoqueue {
                        "Autoqueue on"
                    } else {
                        "Autoqueue off"
                    },
                    Some(Msg::ToggleAutoQueue),
                    if self.local_autoqueue {
                        Kind::Selected
                    } else {
                        Kind::Quiet
                    },
                    if dense { 25.0 } else { 28.0 }
                ),
            ]
            .spacing(if dense { 3 } else { 6 })
            .align_y(Alignment::Center);

            if self.nav == "Local queue" {
                if !self.view_ids.is_empty() {
                    controls = controls.push(confirm_chip(
                        c,
                        "Clear local queue",
                        "Confirm clear",
                        ConfirmAction::ClearLocalQueue,
                        &self.confirm_action,
                        Kind::Danger,
                    ));
                }
            } else {
                controls = controls.push(control(
                    c,
                    Icon::Plus,
                    "Queue all local files",
                    Some(Msg::QueueAllLocal),
                    Kind::Soft,
                    if dense { 25.0 } else { 28.0 },
                ));
            }

            controls = controls
                .push(sort)
                .push(control(
                    c,
                    direction_icon,
                    if self.local_sort_asc {
                        "Sort descending"
                    } else {
                        "Sort ascending"
                    },
                    Some(Msg::ToggleLocalSortDir),
                    Kind::Quiet,
                    if dense { 25.0 } else { 28.0 },
                ))
                .push(display);

            if compact {
                column![
                    row![
                        search.width(Length::Fixed(180.0)),
                        meta_bar,
                        Space::new().width(Length::Fill)
                    ]
                    .spacing(8)
                    .align_y(Alignment::Center)
                    .width(Length::Fill),
                    controls
                ]
                .spacing(if dense { 4 } else { 6 })
                .width(Length::Fill)
                .into()
            } else {
                row![
                    search.width(Length::Fixed(if dense { 200.0 } else { 240.0 })),
                    meta_bar,
                    Space::new().width(Length::Fill),
                    controls
                ]
                .spacing(if dense { 6 } else { 10 })
                .align_y(Alignment::Center)
                .width(Length::Fill)
                .into()
            }
        } else {
            let search_width = if compact {
                180.0
            } else if dense {
                235.0
            } else {
                280.0
            };
            let mut right_actions = row![chip(
                c,
                if self.selected.is_empty() {
                    "Select all"
                } else {
                    "Clear sel"
                },
                (!self.view_ids.is_empty()).then_some(if self.selected.is_empty() {
                    Msg::SelectAll
                } else {
                    Msg::ClearSelection
                }),
                Kind::Quiet,
            )]
            .spacing(if dense { 4 } else { 6 })
            .align_y(Alignment::Center);
            if self.nav == "Up Next" && !self.view_ids.is_empty() {
                right_actions = right_actions.push(confirm_chip(
                    c,
                    "Clear Up Next",
                    "Confirm clear",
                    ConfirmAction::ClearUpNext,
                    &self.confirm_action,
                    Kind::Danger,
                ));
            } else if let Some(pid) = manual_playlist_id
                && !self.view_ids.is_empty()
            {
                right_actions = right_actions.push(confirm_chip(
                    c,
                    "Clear playlist",
                    "Confirm clear",
                    ConfirmAction::ClearPlaylist(pid),
                    &self.confirm_action,
                    Kind::Danger,
                ));
            }
            if !hide_podcast_meta {
                right_actions = right_actions
                    .push(control(
                        c,
                        Icon::Refresh,
                        "Refresh feeds",
                        (!self.busy).then_some(Msg::Refresh),
                        Kind::Quiet,
                        if dense { 28.0 } else { 30.0 },
                    ))
                    .push(control(
                        c,
                        Icon::Sync,
                        "Sync with your account",
                        (!self.busy).then_some(Msg::Sync),
                        Kind::Quiet,
                        if dense { 28.0 } else { 30.0 },
                    ));
            }
            row![
                search.width(Length::Fixed(search_width)),
                meta_bar,
                Space::new().width(Length::Fill),
                right_actions
            ]
            .spacing(if compact { 8 } else { 12 })
            .align_y(Alignment::Center)
            .width(Length::Fill)
            .into()
        };

        let mut top_bars = column![search_and_controls].spacing(if dense { 4 } else { 6 });
        if is_local_view && self.local_drive_locked {
            top_bars = top_bars.push(
                container(
                    row![
                        icon(Icon::Folder, 18.0, c.danger),
                        column![
                            strong("Encrypted media drive is locked or unmounted", 13, c.fg),
                            label(
                                "Your indexed local files, queue order, and last playback position are safely retained.",
                                11,
                                c.muted
                            )
                        ]
                        .spacing(2)
                        .width(Length::Fill),
                        chip(c, "Check drive", Some(Msg::CheckLocalDrive), Kind::Accent),
                        chip(
                            c,
                            "Rescan",
                            (!self.busy).then_some(Msg::ScanLocal),
                            Kind::Soft
                        )
                    ]
                    .spacing(10)
                    .align_y(Alignment::Center),
                )
                .padding([8, 12])
                .width(Length::Fill)
                .style(move |_| theme::panel(c, theme::mix(c.card, c.danger, 0.12), 10.0)),
            );
        }

        // Up Next one-off sort bar (matches Pocket Casts UpNextSortOption).
        if self.nav == "Up Next" && self.view_ids.len() > 1 {
            let sort_bar = container(
                row![
                    icon(Icon::Sort, 14.0, c.muted),
                    label("Sort Up Next:", 11, c.muted),
                    chip(
                        c,
                        "Newest",
                        Some(Msg::SortUpNext(UpNextSort::Newest)),
                        Kind::Soft
                    ),
                    chip(
                        c,
                        "Oldest",
                        Some(Msg::SortUpNext(UpNextSort::Oldest)),
                        Kind::Soft
                    ),
                    chip(
                        c,
                        "Shortest left",
                        Some(Msg::SortUpNext(UpNextSort::Shortest)),
                        Kind::Soft
                    ),
                    chip(
                        c,
                        "Longest left",
                        Some(Msg::SortUpNext(UpNextSort::Longest)),
                        Kind::Soft
                    ),
                    Space::new().width(Length::Fill),
                ]
                .spacing(6)
                .align_y(Alignment::Center)
                .wrap(),
            )
            .padding([5, 10])
            .width(Length::Fill)
            .style(move |_| theme::panel(c, c.card, 9.0));
            top_bars = top_bars.push(sort_bar);
        }

        // Separated Now Playing section for full Up Next / Local queue views.
        let mut skip_head_active = false;
        if is_queue_view
            && let Some(cur) = &self.current
            && cur.is_local() == (self.nav == "Local queue")
        {
            skip_head_active = self.view_ids.first().copied() == Some(cur.id);
            let cur_dur = if player.loaded && player.duration > 0.0 {
                player.duration
            } else {
                cur.duration as f64
            };
            let cur_pos = if player.loaded {
                player.position
            } else {
                let raw = (cur.position as f64 / 1000.0).max(0.0);
                if cur_dur > 0.0 { raw.min(cur_dur) } else { raw }
            };
            let cur_rem = (cur_dur - cur_pos).max(0.0) / speed;
            let cur_title = if cur.is_local() && self.local_display == "file_title" {
                file_name(&cur.description)
            } else {
                &cur.title
            };
            let now_playing_card = container(
                row![
                    self.cover(
                        c,
                        (!cur.is_local()).then_some(cur.podcast_uuid.as_str()),
                        if dense { 38.0 } else { 44.0 }
                    ),
                    column![
                        label("NOW PLAYING", 10, c.accent),
                        button(
                            strong(short(cur_title, if compact { 36 } else { 80 }), 14, c.fg)
                                .wrapping(text::Wrapping::None)
                        )
                        .padding(0)
                        .on_press(Msg::Info(cur.id))
                        .style(move |_, _| theme::button_style(
                            c,
                            Kind::Quiet,
                            widget::button::Status::Active
                        )),
                        label(
                            format!(
                                "{}  ·  {} / {}  ·  {} left",
                                if cur.is_local() {
                                    file_name(&cur.description)
                                } else {
                                    &cur.podcast
                                },
                                format_clock(cur_pos),
                                format_clock(cur_dur),
                                human_duration(cur_rem)
                            ),
                            11,
                            c.muted
                        )
                    ]
                    .spacing(2)
                    .width(Length::Fill),
                    control(
                        c,
                        if player.paused {
                            Icon::Play
                        } else {
                            Icon::Pause
                        },
                        "Play / pause",
                        Some(Msg::Pause),
                        Kind::Accent,
                        32.0
                    ),
                    control(
                        c,
                        Icon::Next,
                        "Play next in queue",
                        Some(Msg::Next),
                        Kind::Soft,
                        30.0
                    )
                ]
                .spacing(10)
                .align_y(Alignment::Center),
            )
            .padding([8, 12])
            .width(Length::Fill)
            .style(move |_| {
                let mut style = theme::panel(c, theme::mix(c.card, c.accent, 0.06), 11.0);
                style.border = Border {
                    width: 1.0,
                    color: theme::mix(c.card, c.accent, 0.35),
                    radius: 11.0.into(),
                };
                style
            });
            top_bars = top_bars.push(now_playing_card);
        }

        if let Some(pid) = self
            .nav
            .strip_prefix("playlist:")
            .and_then(|s| s.parse::<i64>().ok())
            && let Some(pl) = self.playlists.iter().find(|p| n(p, "id") == pid)
        {
            let cur_sort = n(pl, "sort_type");
            let mut pl_bar = row![
                strong(s(pl, "title"), 13, c.fg),
                chip(
                    c,
                    "Play All",
                    (!self.view_ids.is_empty()).then_some(Msg::PlayAll),
                    Kind::Accent
                ),
                chip(
                    c,
                    "Queue All",
                    (!self.view_ids.is_empty()).then_some(Msg::QueueAllView),
                    Kind::Soft
                ),
                Space::new().width(Length::Fill),
                label("Sort:", 11, c.muted),
            ]
            .spacing(6)
            .align_y(Alignment::Center);
            for (code, caption) in [
                (4, "Manual"),
                (0, "Newest"),
                (1, "Oldest"),
                (2, "Shortest"),
                (3, "Longest"),
            ] {
                pl_bar = pl_bar.push(chip(
                    c,
                    caption,
                    Some(Msg::PlaylistSort(pid, code)),
                    if cur_sort == code {
                        Kind::Selected
                    } else {
                        Kind::Quiet
                    },
                ));
            }
            top_bars = top_bars.push(
                container(pl_bar.wrap())
                    .padding([6, 10])
                    .width(Length::Fill)
                    .style(move |_| theme::panel(c, c.card, 9.0)),
            );
        }

        if let Some(target_ep_id) = self.playlist_picker {
            let target_title = self
                .row_cache
                .get(&target_ep_id)
                .map(|r| short(&r.episode.title, 42))
                .unwrap_or_else(|| "Episode".into());
            let mut picker_row = row![
                strong(format!("Add \"{target_title}\" to playlist:"), 12, c.accent),
                Space::new().width(Length::Fill),
            ]
            .spacing(6)
            .align_y(Alignment::Center);
            for p in self.playlists.iter().filter(|p| n(p, "manual") != 0) {
                let pid = n(p, "id");
                picker_row = picker_row.push(chip(
                    c,
                    format!("+ {}", s(p, "title")),
                    Some(Msg::Membership(pid, target_ep_id, true)),
                    Kind::Soft,
                ));
            }
            picker_row = picker_row
                .push(
                    text_input("New playlist…", &self.name)
                        .on_input(Msg::Name)
                        .on_submit(Msg::CreatePlaylist)
                        .size(12)
                        .padding([4, 8])
                        .width(Length::Fixed(140.0))
                        .style(move |_, s| theme::input(c, s)),
                )
                .push(chip(c, "Create", Some(Msg::CreatePlaylist), Kind::Accent))
                .push(chip(
                    c,
                    "Done",
                    Some(Msg::TogglePlaylistPicker(None)),
                    Kind::Quiet,
                ));
            top_bars = top_bars.push(
                container(picker_row.wrap())
                    .padding([6, 10])
                    .width(Length::Fill)
                    .style(move |_| theme::panel(c, theme::mix(c.card, c.accent, 0.1), 9.0)),
            );
        }

        if !self.selected.is_empty() {
            let mut sel_bar = row![
                strong(format!("{} selected", self.selected.len()), 12, c.accent),
                chip(c, "All", Some(Msg::SelectAll), Kind::Quiet),
                chip(c, "Above", Some(Msg::SelectAbove), Kind::Quiet),
                chip(c, "Below", Some(Msg::SelectBelow), Kind::Quiet),
                chip(c, "None", Some(Msg::ClearSelection), Kind::Quiet),
                Space::new().width(Length::Fill),
            ]
            .spacing(5)
            .align_y(Alignment::Center);
            if is_reorderable {
                sel_bar = sel_bar
                    .push(chip(c, "Top", Some(Msg::BulkMoveTop), Kind::Soft))
                    .push(chip(c, "↑ Up", Some(Msg::BulkMove(-1)), Kind::Soft))
                    .push(chip(c, "↓ Down", Some(Msg::BulkMove(1)), Kind::Soft))
                    .push(chip(c, "Bottom", Some(Msg::BulkMoveBottom), Kind::Soft))
                    .push(chip(c, "Remove", Some(Msg::BulkRemove), Kind::Danger));
            }
            if !is_queue_view {
                sel_bar = sel_bar
                    .push(chip(c, "Queue Top", Some(Msg::BulkQueue(2)), Kind::Soft))
                    .push(chip(c, "Queue End", Some(Msg::BulkQueue(3)), Kind::Soft));
            }
            sel_bar = sel_bar
                .push(chip(c, "Played", Some(Msg::BulkPlayed), Kind::Quiet))
                .push(chip(c, "Archive", Some(Msg::BulkArchive), Kind::Quiet));
            top_bars = top_bars.push(
                container(sel_bar.wrap())
                    .padding([6, 10])
                    .width(Length::Fill)
                    .style(move |_| theme::panel(c, theme::mix(c.card, c.accent, 0.08), 9.0)),
            );
        }

        let (row_height, row_gap) = self.main_row_layout();
        let row_stride = row_height + row_gap;
        let list_ids: &[i64] = if skip_head_active && !self.view_ids.is_empty() {
            &self.view_ids[1..]
        } else {
            &self.view_ids
        };
        let total_rows = list_ids.len();
        let visible_capacity = ((self.viewport_height / row_stride).ceil() as usize)
            .max(6)
            .saturating_add(super::VIRTUAL_OVERSCAN * 2);
        let start_idx = self.window_start.min(total_rows.saturating_sub(1));
        let end_idx = (start_idx + visible_capacity).min(total_rows);
        let top_spacer_px = start_idx as f32 * row_stride;
        let bottom_spacer_px = total_rows.saturating_sub(end_idx) as f32 * row_stride;

        let mut slice_col = column![].spacing(row_gap);
        for &ep_id in &list_ids[start_idx..end_idx] {
            let Some(entry) = self.row_cache.get(&ep_id) else {
                slice_col = slice_col.push(
                    container(Space::new())
                        .width(Length::Fill)
                        .height(Length::Fixed(row_height))
                        .style(move |_| theme::panel(c, c.card, 11.0)),
                );
                continue;
            };
            slice_col = slice_col.push(self.episode_row(
                entry,
                &player,
                compact,
                dense,
                row_height,
                is_reorderable,
                manual_playlist_id,
                hide_podcast_meta,
            ));
        }

        let mut virtual_list = column![].spacing(0);
        if total_rows == 0 {
            let (empty_title, empty_body, show_discover) = if skip_head_active {
                (
                    "Nothing else queued after this episode",
                    "Queue more episodes from Podcasts, All episodes, or Local files.",
                    false,
                )
            } else if is_queue_view {
                (
                    if self.nav == "Local queue" {
                        "Your local queue is empty"
                    } else {
                        "Your Up Next queue is empty"
                    },
                    if self.nav == "Local queue" {
                        "Pick audio files from Local files or click Queue All to start listening."
                    } else {
                        "Add episodes to Up Next from any podcast or episode list."
                    },
                    false,
                )
            } else if is_local_view {
                (
                    "No local audio indexed yet",
                    "Choose an audio folder and scan it, then add files to your local queue.",
                    false,
                )
            } else if !self.active_query.is_empty() {
                (
                    "No matching episodes",
                    "Try a shorter search term or switch filters.",
                    false,
                )
            } else if hide_podcast_meta {
                (
                    "No episodes in this filter",
                    "Try switching to All or Archived above.",
                    false,
                )
            } else {
                (
                    "Make room for a good listen",
                    "Discover a podcast, or sign in to bring your library along.",
                    true,
                )
            };
            let mut empty_col = column![
                icon(Icon::Audio, 36.0, c.accent),
                strong(empty_title, 19, c.fg),
                label(empty_body, 13, c.muted),
            ]
            .spacing(12)
            .align_x(Alignment::Center);
            if show_discover {
                empty_col = empty_col.push(action(
                    c,
                    "Discover podcasts",
                    Some(Msg::Nav("Discover".into())),
                    Kind::Soft,
                ));
            }
            virtual_list = virtual_list.push(
                container(empty_col)
                    .center_x(Length::Fill)
                    .padding([46, 12]),
            );
        } else {
            if top_spacer_px > 0.0 {
                virtual_list = virtual_list.push(Space::new().height(Length::Fixed(top_spacer_px)));
            }
            virtual_list = virtual_list.push(slice_col);
            if bottom_spacer_px > 0.0 {
                virtual_list =
                    virtual_list.push(Space::new().height(Length::Fixed(bottom_spacer_px)));
            }
        }
        let scroller = scroll(c, virtual_list)
            .id(self.main_scroll_id.clone())
            .on_scroll(move |vp| {
                Msg::Scroll(vp.absolute_offset().y, vp.bounds().height, row_stride)
            })
            .height(Length::Fill);
        column![top_bars, scroller]
            .spacing(if dense { 5 } else { 8 })
            .width(Length::Fill)
            .height(Length::Fill)
            .into()
    }

    #[allow(clippy::too_many_arguments)]
    fn episode_row<'a>(
        &'a self,
        entry: &'a RowEntry,
        player: &dusky_casts::player::State,
        compact: bool,
        dense: bool,
        row_height: f32,
        is_reorderable: bool,
        manual_playlist_id: Option<i64>,
        hide_podcast_meta: bool,
    ) -> Element<'a, Msg> {
        let c = self.appearance.colors;
        let e = &entry.episode;
        let playing = self
            .current
            .as_ref()
            .is_some_and(|active| active.id == e.id);
        let is_selected = self.selected.contains(&e.id);
        let is_queued = self.queued_ids.contains(&e.id);
        let paused = player.paused;
        let position = if playing {
            player.position
        } else {
            e.position as f64 / 1000.0
        };
        let duration_text = if playing && player.duration > 0.0 {
            format_clock(player.duration)
        } else {
            entry.duration_label.clone()
        };
        let is_local = e.is_local();
        let primary = if is_local && self.local_display == "file_title" {
            short(&entry.local_filename, if compact { 44 } else { 92 })
        } else {
            short(&e.title, if compact { 50 } else { 98 })
        };
        let secondary = if is_local {
            match self.local_display.as_str() {
                "file_title" if !e.title.eq_ignore_ascii_case(&entry.local_stem) => e.title.clone(),
                "title_file" => entry.local_filename.clone(),
                "path" => e.description.clone(),
                "title" => String::new(),
                _ => entry.local_parent.clone(),
            }
        } else if hide_podcast_meta {
            String::new()
        } else {
            short(&e.podcast, if compact { 28 } else { 46 })
        };
        let secondary = short(&secondary, if compact { 58 } else { 112 });
        let meta = if (playing || e.playing_status == 1) && position > 0.0 {
            if secondary.is_empty() {
                if entry.date_label.is_empty() {
                    format!("{} listened", format_clock(position))
                } else {
                    format!(
                        "{}  ·  {} listened",
                        entry.date_label,
                        format_clock(position)
                    )
                }
            } else {
                format!("{secondary}  ·  {} listened", format_clock(position))
            }
        } else if secondary.is_empty() {
            if is_local && self.local_display == "title" {
                String::new()
            } else if e.playing_status == 2 {
                if entry.date_label.is_empty() {
                    "Played".into()
                } else {
                    format!("{}  ·  Played", entry.date_label)
                }
            } else {
                entry.date_label.clone()
            }
        } else if entry.date_label.is_empty() {
            secondary
        } else {
            format!("{secondary}  ·  {}", entry.date_label)
        };
        let btn_sz = if dense { 24.0 } else { 27.0 };
        let select_control = row_control(
            c,
            Icon::Check,
            Some(Msg::ToggleSelect(e.id)),
            if is_selected {
                Kind::Selected
            } else {
                Kind::Quiet
            },
            btn_sz,
        );
        let title_color = if playing {
            c.accent
        } else if e.playing_status == 2 {
            theme::mix(c.fg, c.muted, 0.55)
        } else {
            c.fg
        };
        let title = button(
            strong(primary, if dense { 13 } else { 14 }, title_color)
                .wrapping(text::Wrapping::None),
        )
        .padding(0)
        .on_press(Msg::Info(e.id))
        .width(Length::Fill)
        .style(move |_, _| theme::button_style(c, Kind::Quiet, widget::button::Status::Active));
        let words = column![
            title,
            label(meta, if dense { 10 } else { 11 }, c.muted).wrapping(text::Wrapping::None)
        ]
        .spacing(if dense { 1 } else { 2 })
        .width(Length::Fill);
        let play_control = row_control(
            c,
            if playing && !paused {
                Icon::Pause
            } else {
                Icon::Play
            },
            Some(if playing { Msg::Pause } else { Msg::Play(e.id) }),
            if playing { Kind::Selected } else { Kind::Soft },
            if dense { 26.0 } else { 29.0 },
        );
        let star_control = row_control(
            c,
            Icon::Star,
            Some(Msg::Star(e.id)),
            if e.starred {
                Kind::Selected
            } else {
                Kind::Quiet
            },
            btn_sz,
        );
        let (leading, mut actions): (Element<'_, Msg>, widget::Row<'_, Msg>) =
            if is_local || hide_podcast_meta {
                (
                    row![select_control, play_control]
                        .spacing(6)
                        .align_y(Alignment::Center)
                        .into(),
                    row![star_control],
                )
            } else {
                (
                    row![
                        select_control,
                        self.cover(c, Some(&e.podcast_uuid), if dense { 34.0 } else { 40.0 })
                    ]
                    .spacing(8)
                    .align_y(Alignment::Center)
                    .into(),
                    row![play_control, star_control],
                )
            };
        actions = actions.spacing(2).align_y(Alignment::Center);
        if is_reorderable {
            actions = actions
                .push(chip(c, "Top", Some(Msg::MoveEdge(e.id, true)), Kind::Quiet))
                .push(row_control(
                    c,
                    Icon::Up,
                    Some(Msg::Move(e.id, -1)),
                    Kind::Quiet,
                    btn_sz,
                ))
                .push(row_control(
                    c,
                    Icon::Down,
                    Some(Msg::Move(e.id, 1)),
                    Kind::Quiet,
                    btn_sz,
                ))
                .push(chip(
                    c,
                    "End",
                    Some(Msg::MoveEdge(e.id, false)),
                    Kind::Quiet,
                ))
                .push(row_control(
                    c,
                    Icon::Close,
                    Some(if let Some(pid) = manual_playlist_id {
                        Msg::Membership(pid, e.id, false)
                    } else {
                        Msg::Queue(e.id, 4)
                    }),
                    Kind::Quiet,
                    btn_sz,
                ));
        } else {
            actions = actions
                .push(row_control(
                    c,
                    Icon::Up,
                    Some(Msg::Queue(e.id, 2)),
                    Kind::Quiet,
                    btn_sz,
                ))
                .push(row_control(
                    c,
                    if is_queued { Icon::Check } else { Icon::Plus },
                    Some(Msg::Queue(e.id, if is_queued { 4 } else { 3 })),
                    if is_queued {
                        Kind::Selected
                    } else {
                        Kind::Quiet
                    },
                    btn_sz,
                ))
                .push(row_control(
                    c,
                    Icon::Queue,
                    Some(Msg::TogglePlaylistPicker(Some(e.id))),
                    if self.playlist_picker == Some(e.id) {
                        Kind::Selected
                    } else {
                        Kind::Quiet
                    },
                    btn_sz,
                ));
        }
        actions = actions.push(row_control(
            c,
            Icon::More,
            Some(Msg::Info(e.id)),
            Kind::Quiet,
            btn_sz,
        ));
        let duration: Element<'_, Msg> = if compact {
            Space::new().width(0).into()
        } else {
            container(label(duration_text, 11, c.muted))
                .width(62)
                .align_right(62)
                .into()
        };
        container(
            row![leading, words, duration, actions]
                .spacing(if dense { 7 } else { 12 })
                .align_y(Alignment::Center),
        )
        .padding([0, if dense { 10 } else { 12 }])
        .width(Length::Fill)
        .height(Length::Fixed(row_height))
        .align_y(Alignment::Center)
        .style(move |_| {
            let mut style = theme::panel(
                c,
                if is_selected {
                    theme::mix(c.card, c.accent, 0.08)
                } else if playing {
                    theme::mix(c.card, c.accent, 0.035)
                } else {
                    c.card
                },
                11.0,
            );
            if is_selected || playing {
                style.border = Border {
                    width: 1.0,
                    color: theme::mix(c.card, c.accent, if is_selected { 0.45 } else { 0.3 }),
                    radius: 11.0.into(),
                };
            }
            style
        })
        .into()
    }
}

impl App {
    fn queue_drawer(&self, compact: bool) -> Element<'_, Msg> {
        let c = self.appearance.colors;
        let player = self.player.as_ref().map(|p| p.state()).unwrap_or_default();
        let speed = self.speed.max(0.25);
        let drawer_width = if compact {
            (self.window_size.width - 28.0).clamp(300.0, 420.0)
        } else {
            430.0
        };
        let drawer_height = (self.window_size.height * 0.66).clamp(260.0, 560.0);

        let header = row![
            chip(
                c,
                format!("Up Next ({})", self.cloud_queue_count),
                Some(Msg::QueueDrawerContext(false)),
                if !self.queue_drawer_local {
                    Kind::Selected
                } else {
                    Kind::Quiet
                }
            ),
            chip(
                c,
                format!("Local Queue ({})", self.local_queue_count),
                Some(Msg::QueueDrawerContext(true)),
                if self.queue_drawer_local {
                    Kind::Selected
                } else {
                    Kind::Quiet
                }
            ),
            Space::new().width(Length::Fill),
            button(
                row![
                    icon(Icon::External, 13.0, c.muted),
                    label("Full view", 11, c.fg)
                ]
                .spacing(5)
                .align_y(Alignment::Center)
            )
            .padding([5, 8])
            .on_press(Msg::OpenQueueFullView)
            .style(move |_, s| theme::button_style(c, Kind::Soft, s)),
            row_control(
                c,
                Icon::Close,
                Some(Msg::CloseQueueDrawer),
                Kind::Quiet,
                26.0
            ),
        ]
        .spacing(5)
        .align_y(Alignment::Center);

        let is_active_local_context = self
            .current
            .as_ref()
            .map(Episode::is_local)
            .unwrap_or(matches!(self.nav.as_str(), "Local files" | "Local queue"));
        let total_in_context = if self.queue_drawer_local {
            self.local_queue_count
        } else {
            self.cloud_queue_count
        };
        let summary_text = if self.queue_drawer_local == is_active_local_context {
            let (_, q_total_rem, q_active_rem, _) = &self.queue_summary;
            let queue_rem = if let (Some(current), Some(active_db)) = (&self.current, *q_active_rem)
                && player.loaded
            {
                let dur = if player.duration > 0.0 {
                    player.duration
                } else {
                    current.duration as f64
                };
                (q_total_rem - active_db + (dur - player.position.max(0.0)).max(0.0)).max(0.0)
            } else {
                *q_total_rem
            };
            if total_in_context == 0 {
                "Queue is empty".into()
            } else {
                format!(
                    "{total_in_context} queued · {} left{}",
                    human_duration(queue_rem / speed),
                    if (speed - 1.0).abs() > 0.01 {
                        format!(" at {}", format_rate(speed))
                    } else {
                        String::new()
                    }
                )
            }
        } else if total_in_context == 0 {
            "Queue is empty".into()
        } else {
            format!("{total_in_context} queued")
        };

        let mut subheader = row![
            label(summary_text, 11, c.muted),
            Space::new().width(Length::Fill)
        ]
        .spacing(6)
        .align_y(Alignment::Center);
        if total_in_context > 0 {
            let target = if self.queue_drawer_local {
                ConfirmAction::ClearLocalQueue
            } else {
                ConfirmAction::ClearUpNext
            };
            subheader = subheader.push(confirm_chip(
                c,
                "Clear",
                "Confirm clear",
                target,
                &self.confirm_action,
                Kind::Danger,
            ));
        }

        let mut body = column![header, subheader].spacing(8).height(Length::Fill);

        if let Some(cur) = &self.current
            && cur.is_local() == self.queue_drawer_local
        {
            let cur_dur = if player.loaded && player.duration > 0.0 {
                player.duration
            } else {
                cur.duration as f64
            };
            let cur_pos = if player.loaded {
                player.position
            } else {
                let raw = (cur.position as f64 / 1000.0).max(0.0);
                if cur_dur > 0.0 { raw.min(cur_dur) } else { raw }
            };
            let cur_title = if cur.is_local() && self.local_display == "file_title" {
                file_name(&cur.description)
            } else {
                &cur.title
            };
            let now_playing = container(
                row![
                    self.cover(
                        c,
                        (!cur.is_local()).then_some(cur.podcast_uuid.as_str()),
                        34.0
                    ),
                    column![
                        label("NOW PLAYING", 9, c.accent),
                        button(
                            strong(short(cur_title, 36), 12, c.fg).wrapping(text::Wrapping::None)
                        )
                        .padding(0)
                        .on_press(Msg::Info(cur.id))
                        .style(move |_, _| theme::button_style(
                            c,
                            Kind::Quiet,
                            widget::button::Status::Active
                        )),
                        label(
                            format!("{} / {}", format_clock(cur_pos), format_clock(cur_dur)),
                            10,
                            c.muted
                        )
                    ]
                    .spacing(1)
                    .width(Length::Fill),
                    row_control(
                        c,
                        if player.paused {
                            Icon::Play
                        } else {
                            Icon::Pause
                        },
                        Some(Msg::Pause),
                        Kind::Accent,
                        28.0
                    ),
                ]
                .spacing(8)
                .align_y(Alignment::Center),
            )
            .padding([7, 10])
            .width(Length::Fill)
            .style(move |_| {
                let mut style = theme::panel(c, theme::mix(c.card, c.accent, 0.08), 10.0);
                style.border = Border {
                    width: 1.0,
                    color: theme::mix(c.card, c.accent, 0.35),
                    radius: 10.0.into(),
                };
                style
            });
            body = body.push(now_playing);
        }

        let (row_h, row_gap) = App::drawer_row_layout();
        let row_stride = row_h + row_gap;
        let total_rows = self.drawer_ids.len();
        let visible_capacity = ((self.drawer_viewport_height / row_stride).ceil() as usize)
            .max(5)
            .saturating_add(super::DRAWER_OVERSCAN * 2);
        let start_idx = self.drawer_window_start.min(total_rows.saturating_sub(1));
        let end_idx = (start_idx + visible_capacity).min(total_rows);
        let top_spacer = start_idx as f32 * row_stride;
        let bottom_spacer = total_rows.saturating_sub(end_idx) as f32 * row_stride;

        let mut list_col = column![].spacing(0);
        if total_rows == 0 {
            list_col = list_col.push(
                container(
                    column![
                        icon(Icon::Queue, 24.0, c.muted),
                        label(
                            if self.queue_drawer_local {
                                "No upcoming local files queued."
                            } else {
                                "No upcoming episodes queued."
                            },
                            12,
                            c.muted
                        )
                    ]
                    .spacing(8)
                    .align_x(Alignment::Center),
                )
                .center_x(Length::Fill)
                .padding([28, 10]),
            );
        } else {
            if top_spacer > 0.0 {
                list_col = list_col.push(Space::new().height(Length::Fixed(top_spacer)));
            }
            let mut slice_col = column![].spacing(row_gap);
            for &ep_id in &self.drawer_ids[start_idx..end_idx] {
                let Some(entry) = self.drawer_cache.get(&ep_id) else {
                    slice_col = slice_col.push(
                        container(Space::new())
                            .width(Length::Fill)
                            .height(Length::Fixed(row_h))
                            .style(move |_| theme::panel(c, c.card, 9.0)),
                    );
                    continue;
                };
                let e = &entry.episode;
                let title_text = if e.is_local() && self.local_display == "file_title" {
                    short(&entry.local_filename, 34)
                } else {
                    short(&e.title, 34)
                };
                let sub_text = if e.is_local() {
                    format!(
                        "{}  ·  {}",
                        short(&entry.local_parent, 20),
                        entry.duration_label
                    )
                } else {
                    format!("{}  ·  {}", short(&e.podcast, 20), entry.duration_label)
                };
                let sub_text = sub_text.trim_start_matches("  ·  ").to_owned();
                slice_col = slice_col.push(
                    container(
                        row![
                            row_control(c, Icon::Play, Some(Msg::Play(e.id)), Kind::Soft, 24.0),
                            column![
                                button(strong(title_text, 12, c.fg).wrapping(text::Wrapping::None))
                                    .padding(0)
                                    .on_press(Msg::Info(e.id))
                                    .width(Length::Fill)
                                    .style(move |_, _| theme::button_style(
                                        c,
                                        Kind::Quiet,
                                        widget::button::Status::Active
                                    )),
                                label(sub_text, 10, c.muted).wrapping(text::Wrapping::None)
                            ]
                            .spacing(1)
                            .width(Length::Fill),
                            row_control(
                                c,
                                Icon::Up,
                                Some(Msg::DrawerMove(e.id, -1)),
                                Kind::Quiet,
                                22.0,
                            ),
                            row_control(
                                c,
                                Icon::Down,
                                Some(Msg::DrawerMove(e.id, 1)),
                                Kind::Quiet,
                                22.0,
                            ),
                            row_control(
                                c,
                                Icon::Close,
                                Some(Msg::Queue(e.id, 4)),
                                Kind::Quiet,
                                22.0
                            ),
                        ]
                        .spacing(5)
                        .align_y(Alignment::Center),
                    )
                    .padding([0, 8])
                    .width(Length::Fill)
                    .height(Length::Fixed(row_h))
                    .align_y(Alignment::Center)
                    .style(move |_| theme::panel(c, c.card, 9.0)),
                );
            }
            list_col = list_col.push(slice_col);
            if bottom_spacer > 0.0 {
                list_col = list_col.push(Space::new().height(Length::Fixed(bottom_spacer)));
            }
        }

        let scroller = scroll(c, list_col)
            .id(self.drawer_scroll_id.clone())
            .on_scroll(move |vp| {
                Msg::DrawerScroll(vp.absolute_offset().y, vp.bounds().height, row_stride)
            })
            .height(Length::Fill);

        body = body.push(scroller);

        let card = container(body)
            .padding(12)
            .width(Length::Fixed(drawer_width))
            .height(Length::Fixed(drawer_height))
            .style(move |_| theme::drawer_box(c));

        let scrim = mouse_area(
            container(Space::new())
                .width(Length::Fill)
                .height(Length::Fill)
                .style(|_| container::Style {
                    background: Some(iced::Background::Color(Color::from_rgba(
                        0.0, 0.0, 0.0, 0.34,
                    ))),
                    ..Default::default()
                }),
        )
        .on_press(Msg::CloseQueueDrawer);

        stack![
            scrim,
            container(mouse_area(card).on_press(Msg::Noop))
                .width(Length::Fill)
                .height(Length::Fill)
                .align_x(Alignment::End)
                .align_y(Alignment::End)
                .padding([10, 14])
        ]
        .width(Length::Fill)
        .height(Length::Fill)
        .into()
    }

    fn account_view(&self, compact: bool) -> Element<'_, Msg> {
        let c = self.appearance.colors;
        let acct = &self.account_stats;
        let signed_in = acct.signed_in;
        let today = chrono::Local::now().format("%Y-%m-%d").to_string();
        let pending = self.listening.pending();
        let pending_today = pending.get(&today).copied().unwrap_or(0);
        let pending_total: i64 = pending.values().sum();

        let pc_today = acct.today_ms.saturating_add(pending_today);
        let ios_today = acct.ios_today_ms;
        let pc_week = acct.week_pc_ms.saturating_add(pending_total);
        let ios_week = acct.week_ios_ms;
        let pc_month = acct.month_pc_ms.saturating_add(pending_total);
        let ios_month = acct.month_ios_ms;
        let pc_all = acct.total_ms.saturating_add(pending_total);
        let ios_all = acct.ios_total_ms;

        let last_sync_label = chrono::DateTime::from_timestamp(acct.last_sync_time, 0)
            .filter(|_| acct.last_sync_time > 0)
            .map(|d| {
                d.with_timezone(&chrono::Local)
                    .format("%b %-d, %H:%M")
                    .to_string()
            })
            .unwrap_or_else(|| "Not synced yet".into());

        // 1. Top Profile & KPI Hero Bar
        let avatar = container(icon(Icon::Account, 24.0, c.accent))
            .center_x(46)
            .center_y(46)
            .style(move |_| theme::panel(c, theme::mix(c.hover, c.accent, 0.14), 23.0));

        let identity_title = if signed_in && !self.email.is_empty() {
            self.email.clone()
        } else {
            "Local-Only Library".into()
        };
        let identity_sub = if signed_in {
            if acct.pending_sync_count > 0 {
                format!(
                    "Pocket Casts connected  ·  Last sync {last_sync_label}  ·  {} pending",
                    acct.pending_sync_count
                )
            } else {
                format!("Pocket Casts connected  ·  Last sync {last_sync_label}")
            }
        } else {
            "Sign in to sync subscriptions, Up Next, and playback progress with Pocket Casts".into()
        };

        let mut hero_actions = row![action(
            c,
            "Sync Local Stats",
            Some(Msg::SyncLocalStats),
            Kind::Soft
        ),]
        .spacing(8)
        .align_y(Alignment::Center);
        if signed_in {
            hero_actions = hero_actions
                .push(action(
                    c,
                    if self.busy { "Syncing…" } else { "Sync Now" },
                    (!self.busy).then_some(Msg::Sync),
                    Kind::Accent,
                ))
                .push(action(
                    c,
                    "Sign out",
                    (!self.busy).then_some(Msg::Logout),
                    Kind::Quiet,
                ));
        }

        let kpi_tile = |title: &'static str, value: String, sub: String| -> Element<'_, Msg> {
            container(
                column![
                    label(title, 10, c.muted),
                    strong(value, 18, c.accent),
                    label(sub, 10, c.muted)
                ]
                .spacing(3),
            )
            .padding([10, 12])
            .width(Length::FillPortion(1))
            .style(move |_| theme::panel(c, c.hover, 10.0))
            .into()
        };

        let fourth_kpi = if signed_in {
            if acct.stats_summary_at > 0 {
                kpi_tile(
                    "ACCOUNT (ALL DEVICES)",
                    human_duration(
                        acct.own_ms
                            .saturating_add(acct.other_ms)
                            .saturating_add(pending_total) as f64
                            / 1000.0,
                    ),
                    "Pocket Casts cloud".into(),
                )
            } else {
                kpi_tile(
                    "ACCOUNT (ALL DEVICES)",
                    "Pending sync".into(),
                    "Press Sync Now".into(),
                )
            }
        } else {
            kpi_tile(
                "COMBINED RECORDED",
                human_duration(pc_all.saturating_add(ios_all) as f64 / 1000.0),
                format!(
                    "PC {} · iPhone {}",
                    human_duration(pc_all as f64 / 1000.0),
                    human_duration(ios_all as f64 / 1000.0)
                ),
            )
        };

        let kpi_row = row![
            kpi_tile(
                "PODCASTS",
                acct.podcast_count.to_string(),
                "Subscribed shows".into()
            ),
            kpi_tile(
                "TODAY (PC + IPHONE)",
                human_duration(pc_today.saturating_add(ios_today) as f64 / 1000.0),
                format!(
                    "PC {} · iPhone {}",
                    human_duration(pc_today as f64 / 1000.0),
                    human_duration(ios_today as f64 / 1000.0)
                )
            ),
            kpi_tile(
                "THIS PC (ALL-TIME)",
                human_duration(pc_all as f64 / 1000.0),
                format!(
                    "{} active days",
                    acct.history.iter().filter(|(_, pc, _)| *pc > 0).count()
                )
            ),
            fourth_kpi,
        ]
        .spacing(10);

        let hero_top: Element<'_, Msg> = if compact {
            column![
                row![
                    avatar,
                    column![
                        strong(identity_title, 18, c.fg),
                        label(identity_sub, 12, c.muted)
                    ]
                    .spacing(3)
                    .width(Length::Fill)
                ]
                .spacing(12)
                .align_y(Alignment::Center),
                hero_actions
            ]
            .spacing(10)
            .into()
        } else {
            row![
                avatar,
                column![
                    strong(identity_title, 18, c.fg),
                    label(identity_sub, 12, c.muted)
                ]
                .spacing(3)
                .width(Length::Fill),
                hero_actions
            ]
            .spacing(12)
            .align_y(Alignment::Center)
            .width(Length::Fill)
            .into()
        };

        let hero_card = container(column![hero_top, kpi_row].spacing(14))
            .padding(18)
            .width(Length::Fill)
            .style(move |_| theme::panel(c, c.card, 14.0));

        // 2. Left Column: Cloud Sync Card + iPhone FTP Local Stats Card
        let mut cloud_col = column![
            strong("Pocket Casts Cloud Sync", 15, c.fg),
            label(
                "Syncs subscribed shows, folders, playlists, Up Next, and PC listening seconds.",
                12,
                c.muted
            )
        ]
        .spacing(10);

        if signed_in {
            cloud_col = cloud_col.push(
                container(
                    column![
                        row![
                            icon(Icon::Check, 16.0, c.accent),
                            strong("Connected", 14, c.fg),
                            Space::new().width(Length::Fill),
                            label(format!("Last sync · {last_sync_label}"), 11, c.muted)
                        ]
                        .spacing(8)
                        .align_y(Alignment::Center),
                        label(self.email.clone(), 13, c.muted),
                        label(
                            format!(
                                "Pending local changes: {}  ·  PC reported: {}",
                                acct.pending_sync_count,
                                human_duration(acct.own_ms as f64 / 1000.0)
                            ),
                            11,
                            c.muted
                        )
                    ]
                    .spacing(6),
                )
                .padding(12)
                .width(Length::Fill)
                .style(move |_| theme::panel(c, c.hover, 10.0)),
            );
            if !acct.stats_error.is_empty() {
                cloud_col = cloud_col.push(label(
                    format!("Cloud stats summary: {}", acct.stats_error),
                    11,
                    c.danger,
                ));
            }
            if !acct.last_sync_error.is_empty() {
                cloud_col = cloud_col.push(label(
                    format!("Last sync error: {}", acct.last_sync_error),
                    11,
                    c.danger,
                ));
            }
        } else {
            cloud_col = cloud_col
                .push(label("EMAIL ADDRESS", 10, c.muted))
                .push(
                    text_input("you@example.com", &self.email)
                        .on_input(Msg::Email)
                        .padding(10)
                        .size(13)
                        .style(move |_, s| theme::input(c, s)),
                )
                .push(label("PASSWORD", 10, c.muted))
                .push(
                    text_input("Your password", &self.password)
                        .secure(true)
                        .on_input(Msg::Password)
                        .on_submit(Msg::Login)
                        .padding(10)
                        .size(13)
                        .style(move |_, s| theme::input(c, s)),
                )
                .push(action(
                    c,
                    if self.busy {
                        "Connecting…"
                    } else {
                        "Sign in & sync library"
                    },
                    (!self.busy).then_some(Msg::Login),
                    Kind::Accent,
                ))
                .push(label(
                    "Your password is used for sign-in and isn't saved.",
                    11,
                    c.muted,
                ));
        }

        let cloud_card = container(cloud_col)
            .padding(18)
            .width(Length::Fill)
            .style(move |_| theme::panel(c, c.card, 14.0));

        // FTP Local Stats Link Card
        let ios_status_line = if !self.stats_report.ios_sources.is_empty() {
            let src = self.stats_report.ios_sources[0].display().to_string();
            let synced_when = chrono::DateTime::from_timestamp(acct.ios_synced_at, 0)
                .filter(|_| acct.ios_synced_at > 0)
                .map(|d| {
                    d.with_timezone(&chrono::Local)
                        .format("%b %-d, %H:%M")
                        .to_string()
                })
                .unwrap_or_else(|| "just now".into());
            format!(
                "Found iPhone manifest ({} days, synced {synced_when}): {}",
                self.stats_report.imported_days,
                short_tail(&src, 48)
            )
        } else if acct.ios_synced_at > 0 {
            let synced_when = chrono::DateTime::from_timestamp(acct.ios_synced_at, 0)
                .map(|d| {
                    d.with_timezone(&chrono::Local)
                        .format("%b %-d, %H:%M")
                        .to_string()
                })
                .unwrap_or_default();
            format!("Last imported iPhone stats · {synced_when} (file currently not mounted)")
        } else {
            "Waiting for iPhone FTP sync — tap Sync with Laptop in iPhone Stats to upload .dusky_casts-stats-ios.json".into()
        };

        let mut export_lines = column![label("EXPORTED PC STATS FOLDERS", 10, c.muted)].spacing(3);
        if self.stats_report.export_dirs.is_empty() {
            export_lines = export_lines.push(label(
                "Press Sync Local Stats to scan and export .dusky_casts-stats-pc.json",
                11,
                c.muted,
            ));
        } else {
            for dir in &self.stats_report.export_dirs {
                export_lines =
                    export_lines.push(label(short_tail(&dir.display().to_string(), 52), 11, c.fg));
            }
        }

        let ftp_card = container(
            column![
                strong("iPhone ↔ PC Local FTP Stats", 15, c.fg),
                container(
                    row![
                        icon(
                            if !self.stats_report.ios_sources.is_empty() {
                                Icon::Check
                            } else {
                                Icon::Folder
                            },
                            15.0,
                            if !self.stats_report.ios_sources.is_empty() {
                                c.accent
                            } else {
                                c.muted
                            }
                        ),
                        label(ios_status_line, 11, c.fg).width(Length::Fill)
                    ]
                    .spacing(8)
                    .align_y(Alignment::Center)
                )
                .padding(10)
                .width(Length::Fill)
                .style(move |_| theme::panel(c, c.hover, 9.0)),
                export_lines,
                label("FTP STATS FOLDER OVERRIDE (OPTIONAL)", 10, c.muted),
                row![
                    text_input(
                        "Auto-detected from local library & ancestors",
                        &self.ftp_stats_dir_input
                    )
                    .on_input(Msg::FtpStatsDir)
                    .on_submit(Msg::SaveFtpStatsDir)
                    .size(12)
                    .padding([8, 10])
                    .style(move |_, s| theme::input(c, s)),
                    chip(c, "Save", Some(Msg::SaveFtpStatsDir), Kind::Soft),
                    chip(c, "Auto", Some(Msg::ClearFtpStatsDir), Kind::Quiet),
                ]
                .spacing(6)
                .align_y(Alignment::Center),
                label(
                    "Local iPhone FTP stats stay strictly local (never uploaded to Pocket Casts cloud) so signing in on iPhone later never double-counts.",
                    11,
                    c.muted
                )
            ]
            .spacing(10),
        )
        .padding(18)
        .width(Length::Fill)
        .style(move |_| theme::panel(c, c.card, 14.0));

        let left_col = column![cloud_card, ftp_card].spacing(14);

        // 3. Right Column: 4-Period Summary + Daily Breakdown Chart
        let stat_card = |period: &'static str, pc_ms: i64, ios_ms: i64| -> Element<'_, Msg> {
            let combined = pc_ms.saturating_add(ios_ms);
            container(
                column![
                    label(period, 10, c.muted),
                    strong(human_duration(combined as f64 / 1000.0), 17, c.accent),
                    label(
                        format!("PC: {}", human_duration(pc_ms as f64 / 1000.0)),
                        10,
                        c.muted
                    ),
                    label(
                        format!("iPhone: {}", human_duration(ios_ms as f64 / 1000.0)),
                        10,
                        c.muted
                    )
                ]
                .spacing(2),
            )
            .padding(11)
            .width(Length::FillPortion(1))
            .style(move |_| theme::panel(c, c.hover, 10.0))
            .into()
        };

        let period_grid = row![
            stat_card("TODAY", pc_today, ios_today),
            stat_card("LAST 7 DAYS", pc_week, ios_week),
            stat_card("LAST 30 DAYS", pc_month, ios_month),
            stat_card("ALL-TIME", pc_all, ios_all),
        ]
        .spacing(10);

        let ios_color = theme::mix(c.accent, c.fg, 0.48);
        let displayed_days = if self.account_show_all_days {
            acct.history.len()
        } else {
            acct.history.len().min(30)
        };
        let visible_history = &acct.history[..displayed_days];
        let max_day_ms = visible_history
            .iter()
            .map(|(d, pc, ios)| {
                let add = if d == &today { pending_today } else { 0 };
                pc.saturating_add(add).saturating_add(*ios)
            })
            .max()
            .unwrap_or(1)
            .max(1);

        let mut chart_header = row![
            strong(
                if self.account_show_all_days {
                    format!("Daily Breakdown (All {} Active Days)", acct.history.len())
                } else {
                    "Daily Breakdown (Last 30 Active Days)".into()
                },
                15,
                c.fg
            ),
            Space::new().width(Length::Fill),
            row![
                container(Space::new())
                    .width(8)
                    .height(8)
                    .style(move |_| theme::panel(c, c.accent, 4.0)),
                label("PC", 11, c.muted),
                container(Space::new())
                    .width(8)
                    .height(8)
                    .style(move |_| theme::panel(c, ios_color, 4.0)),
                label("iPhone", 11, c.muted),
            ]
            .spacing(6)
            .align_y(Alignment::Center)
        ]
        .spacing(10)
        .align_y(Alignment::Center);

        if acct.history.len() > 30 {
            chart_header = chart_header.push(
                button(
                    row![
                        icon(Icon::ChevronDown, 13.0, c.muted),
                        label(
                            if self.account_show_all_days {
                                "Show 30d"
                            } else {
                                "Show all"
                            },
                            11,
                            c.fg
                        )
                    ]
                    .spacing(4)
                    .align_y(Alignment::Center),
                )
                .padding([4, 8])
                .on_press(Msg::ToggleAccountAllDays)
                .style(move |_, s| theme::button_style(c, Kind::Soft, s)),
            );
        }

        let mut history_col = column![chart_header].spacing(8);
        if visible_history.is_empty() {
            history_col = history_col.push(
                container(label(
                    "No listening history recorded yet. Start playing an episode to track daily listening.",
                    12,
                    c.muted,
                ))
                .padding([20, 4]),
            );
        } else {
            for (day, raw_pc_ms, ios_ms) in visible_history {
                let pc_ms = if day == &today {
                    raw_pc_ms.saturating_add(pending_today)
                } else {
                    *raw_pc_ms
                };
                let combined_ms = pc_ms.saturating_add(*ios_ms);
                let mut pc_part = ((pc_ms as f64 / max_day_ms as f64) * 1000.0).round() as u16;
                let mut ios_part = ((*ios_ms as f64 / max_day_ms as f64) * 1000.0).round() as u16;
                if pc_ms > 0 && pc_part < 16 {
                    pc_part = 16;
                }
                if *ios_ms > 0 && ios_part < 16 {
                    ios_part = 16;
                }
                let total_part = pc_part.saturating_add(ios_part);
                if total_part > 1000 {
                    let scale = 1000.0 / total_part as f64;
                    pc_part = (pc_part as f64 * scale).floor() as u16;
                    ios_part = (ios_part as f64 * scale).floor() as u16;
                    if pc_ms > 0 && pc_part == 0 {
                        pc_part = 1;
                    }
                    if *ios_ms > 0 && ios_part == 0 {
                        ios_part = 1;
                    }
                }
                let rest_part = 1000u16.saturating_sub(pc_part.saturating_add(ios_part));

                let mut bar = row![].spacing(2).align_y(Alignment::Center);
                if pc_part > 0 {
                    bar = bar.push(
                        container(Space::new())
                            .width(Length::FillPortion(pc_part))
                            .height(10)
                            .style(move |_| theme::panel(c, c.accent, 4.0)),
                    );
                }
                if ios_part > 0 {
                    bar = bar.push(
                        container(Space::new())
                            .width(Length::FillPortion(ios_part))
                            .height(10)
                            .style(move |_| theme::panel(c, ios_color, 4.0)),
                    );
                }
                if rest_part > 0 {
                    bar = bar.push(Space::new().width(Length::FillPortion(rest_part)));
                }

                history_col = history_col.push(
                    container(
                        row![
                            strong(day.clone(), 12, c.fg).width(Length::Fixed(92.0)),
                            container(bar).width(Length::FillPortion(3)),
                            label(
                                format!(
                                    "PC {} · iOS {}",
                                    human_duration(pc_ms as f64 / 1000.0),
                                    human_duration(*ios_ms as f64 / 1000.0)
                                ),
                                11,
                                c.muted
                            )
                            .width(Length::FillPortion(2)),
                            container(strong(
                                human_duration(combined_ms as f64 / 1000.0),
                                12,
                                c.accent
                            ))
                            .width(Length::Fixed(74.0))
                            .align_right(74.0)
                        ]
                        .spacing(10)
                        .align_y(Alignment::Center),
                    )
                    .padding([7, 10])
                    .width(Length::Fill)
                    .style(move |_| theme::panel(c, c.hover, 9.0)),
                );
            }
        }

        let right_card = container(column![period_grid, history_col].spacing(16))
            .padding(18)
            .width(Length::Fill)
            .style(move |_| theme::panel(c, c.card, 14.0));

        let body: Element<'_, Msg> = if compact {
            column![hero_card, left_col, right_card]
                .spacing(14)
                .width(Length::Fill)
                .into()
        } else {
            column![
                hero_card,
                row![
                    left_col.width(Length::FillPortion(2)),
                    right_card.width(Length::FillPortion(3)),
                ]
                .spacing(14)
                .width(Length::Fill)
            ]
            .spacing(14)
            .width(Length::Fill)
            .into()
        };

        scroll(c, container(body).max_width(1260).width(Length::Fill))
            .height(Length::Fill)
            .into()
    }

    fn discover_view(&self) -> Element<'_, Msg> {
        let c = self.appearance.colors;
        let mut results = column![].spacing(8);
        if self.results.is_empty() {
            results = results.push(
                container(
                    column![
                        strong("Find your next favorite", 22, c.fg),
                        label(
                            "Search for a show, an idea, or a voice you love.",
                            14,
                            c.muted
                        )
                    ]
                    .spacing(10),
                )
                .padding([24, 0]),
            );
        }
        for (index, p) in self.results.iter().enumerate() {
            let feed_url = s(p, "feedUrl");
            let title = s(p, "collectionName");
            let existing = self.podcasts.iter().find(|pod| {
                (!feed_url.is_empty() && pod.feed_url == feed_url)
                    || (!title.is_empty() && pod.title.eq_ignore_ascii_case(title))
            });
            let right_btn = if let Some(pod) = existing {
                action(c, "Open", Some(Msg::Podcast(pod.id)), Kind::Selected)
            } else {
                action(
                    c,
                    "Follow",
                    (!self.busy).then_some(Msg::Subscribe(index)),
                    Kind::Soft,
                )
            };
            results = results.push(
                container(
                    row![
                        column![
                            strong(title, 16, c.fg),
                            label(s(p, "artistName"), 12, c.muted)
                        ]
                        .spacing(6)
                        .width(Length::Fill),
                        right_btn
                    ]
                    .spacing(12)
                    .align_y(Alignment::Center),
                )
                .padding(14)
                .style(move |_| theme::panel(c, c.card, 12.0)),
            );
        }
        let feed = container(
            column![
                strong("Follow a feed", 15, c.fg),
                label("Add a podcast with its RSS or Atom address.", 12, c.muted),
                row![
                    text_input("https://…", &self.feed)
                        .on_input(Msg::Feed)
                        .on_submit(Msg::AddFeed)
                        .size(13)
                        .padding(11)
                        .style(move |_, s| theme::input(c, s)),
                    action(
                        c,
                        "Add feed",
                        (!self.busy).then_some(Msg::AddFeed),
                        Kind::Soft
                    )
                ]
                .spacing(10)
            ]
            .spacing(10),
        )
        .padding(18)
        .style(move |_| theme::panel(c, c.card, 14.0));
        column![
            row![
                text_input("Search podcasts", &self.discover)
                    .on_input(Msg::Discover)
                    .on_submit(Msg::Search)
                    .size(15)
                    .padding(12)
                    .style(move |_, s| theme::input(c, s)),
                action(
                    c,
                    "Search",
                    (!self.busy).then_some(Msg::Search),
                    Kind::Accent
                )
            ]
            .spacing(10),
            scroll(c, results).height(Length::Fill),
            feed
        ]
        .spacing(16)
        .into()
    }

    fn info_view<'a>(&'a self, e: &'a Episode) -> Element<'a, Msg> {
        let c = self.appearance.colors;
        let mut lists = column![].spacing(8);
        lists = lists.push(label("ADD TO A PLAYLIST", 10, c.muted));
        for p in self.playlists.iter().filter(|p| n(p, "manual") != 0) {
            let id = n(p, "id");
            let exists = self.info_playlists.contains(&id);
            lists = lists.push(action(
                c,
                format!(
                    "{} {}",
                    if exists { "Remove from" } else { "Add to" },
                    s(p, "title")
                ),
                Some(Msg::Membership(id, e.id, !exists)),
                Kind::Soft,
            ));
        }

        let parent_pod = (!e.is_local())
            .then(|| self.podcasts.iter().find(|p| p.uuid == e.podcast_uuid))
            .flatten();
        let podcast_header: Element<'_, Msg> = if let Some(pod) = parent_pod {
            button(label(&e.podcast, 13, c.accent))
                .padding(0)
                .on_press(Msg::Podcast(pod.id))
                .style(move |_, s| theme::button_style(c, Kind::Quiet, s))
                .into()
        } else {
            label(&e.podcast, 13, c.accent).into()
        };

        let hero = row![
            self.cover(c, (!e.is_local()).then_some(e.podcast_uuid.as_str()), 88.0),
            column![
                podcast_header,
                strong(&e.title, 22, c.fg),
                label(format_clock(e.duration as f64), 12, c.muted)
            ]
            .spacing(6)
        ]
        .spacing(18)
        .align_y(Alignment::Center);

        let controls = row![
            action(c, "Play episode", Some(Msg::Play(e.id)), Kind::Accent),
            action(c, "Play next (Top)", Some(Msg::Queue(e.id, 2)), Kind::Soft),
            action(
                c,
                if e.is_local() {
                    "Add to local queue (Bottom)"
                } else {
                    "Add to Up Next (Bottom)"
                },
                Some(Msg::Queue(e.id, 3)),
                Kind::Soft
            ),
            action(
                c,
                "Move to top",
                (matches!(self.nav.as_str(), "Up Next" | "Local queue")
                    || self.nav.starts_with("playlist:"))
                .then_some(Msg::MoveEdge(e.id, true)),
                Kind::Quiet
            ),
            action(
                c,
                "Move to bottom",
                (matches!(self.nav.as_str(), "Up Next" | "Local queue")
                    || self.nav.starts_with("playlist:"))
                .then_some(Msg::MoveEdge(e.id, false)),
                Kind::Quiet
            ),
            action(
                c,
                if e.archived { "Unarchive" } else { "Archive" },
                Some(Msg::Archive(e.id)),
                Kind::Quiet
            ),
            action(
                c,
                if e.playing_status == 2 {
                    "Mark unplayed"
                } else {
                    "Mark played"
                },
                Some(Msg::Played(e.id)),
                Kind::Quiet
            ),
            action(
                c,
                if e.is_local() {
                    "Local file"
                } else if self.downloads.contains_key(&e.id) {
                    "Cancel download"
                } else {
                    "Download"
                },
                (!e.is_local()).then_some(if self.downloads.contains_key(&e.id) {
                    Msg::CancelDownload(e.id)
                } else {
                    Msg::Download(e.id)
                }),
                Kind::Soft
            )
        ]
        .spacing(8)
        .wrap();
        let progress = self
            .downloads
            .get(&e.id)
            .map(|d| {
                format!(
                    "{} / {} KiB downloaded",
                    d.bytes.load(std::sync::atomic::Ordering::Relaxed) / 1024,
                    d.total.load(std::sync::atomic::Ordering::Relaxed) / 1024
                )
            })
            .unwrap_or_default();

        let back_btn = button(
            row![
                icon(Icon::ChevronLeft, 15.0, c.muted),
                label("Back", 13, c.fg)
            ]
            .spacing(6)
            .align_y(Alignment::Center),
        )
        .padding([6, 10])
        .on_press(Msg::CloseInfo)
        .style(move |_, s| theme::button_style(c, Kind::Quiet, s));

        scroll(
            c,
            column![
                back_btn,
                hero,
                controls,
                label(progress, 12, c.accent),
                container(
                    label(
                        if e.is_local() {
                            e.description.clone()
                        } else {
                            plain(&e.description)
                        },
                        14,
                        c.muted
                    )
                    .line_height(1.6)
                )
                .padding(18)
                .width(Length::Fill)
                .style(move |_| theme::panel(c, c.card, 14.0)),
                lists
            ]
            .spacing(16),
        )
        .height(Length::Fill)
        .into()
    }

    fn organize_view(&self) -> Element<'_, Msg> {
        let c = self.appearance.colors;
        let mut items = column![
            strong("A place for every listen", 22, c.fg),
            label(
                "Click any folder, playlist, or podcast to open it. Rename inline or organize shows into folders.",
                13,
                c.muted
            ),
            row![
                text_input("Name a new folder or playlist", &self.name)
                    .on_input(Msg::Name)
                    .padding(11)
                    .size(14)
                    .style(move |_, s| theme::input(c, s)),
                action(c, "New folder", Some(Msg::CreateFolder), Kind::Soft),
                action(c, "New playlist", Some(Msg::CreatePlaylist), Kind::Soft)
            ]
            .spacing(8)
            .align_y(Alignment::Center)
        ]
        .spacing(12);

        for (heading, values) in [("FOLDERS", &self.folders), ("PLAYLISTS", &self.playlists)] {
            if !values.is_empty() {
                items = items.push(container(label(heading, 10, c.muted)).padding([10, 0]));
            }
            for p in values {
                let id = n(p, "id");
                let is_folder = heading == "FOLDERS";
                let current_name = if is_folder {
                    s(p, "name")
                } else {
                    s(p, "title")
                };
                let target_nav = if is_folder {
                    format!("folder:{}", s(p, "uuid"))
                } else {
                    format!("playlist:{id}")
                };

                let row_el: Element<'_, Msg> = if let Some((ed_folder, ed_id, draft)) =
                    &self.editing_item
                    && *ed_folder == is_folder
                    && *ed_id == id
                {
                    row![
                        text_input("New name", draft)
                            .on_input(Msg::EditRename)
                            .on_submit(Msg::CommitRename)
                            .size(13)
                            .padding([7, 10])
                            .style(move |_, s| theme::input(c, s)),
                        chip(c, "Save", Some(Msg::CommitRename), Kind::Accent),
                        chip(c, "Cancel", Some(Msg::CancelRename), Kind::Quiet),
                    ]
                    .spacing(8)
                    .align_y(Alignment::Center)
                    .into()
                } else {
                    let del_action = if is_folder {
                        ConfirmAction::DeleteFolder(id)
                    } else {
                        ConfirmAction::DeletePlaylist(id)
                    };
                    let delete_control: Element<'_, Msg> =
                        if self.confirm_action.as_ref() == Some(&del_action) {
                            row![
                                chip(c, "Confirm delete", Some(Msg::Confirm), Kind::Danger),
                                chip(c, "Cancel", Some(Msg::CancelConfirm), Kind::Quiet),
                            ]
                            .spacing(4)
                            .align_y(Alignment::Center)
                            .into()
                        } else {
                            button(
                                row![
                                    icon(Icon::Trash, 14.0, c.danger),
                                    label("Delete", 12, c.danger)
                                ]
                                .spacing(5)
                                .align_y(Alignment::Center),
                            )
                            .padding([6, 10])
                            .on_press(Msg::RequestConfirm(del_action))
                            .style(move |_, s| theme::button_style(c, Kind::Danger, s))
                            .into()
                        };
                    row![
                        button(
                            row![
                                icon(
                                    if is_folder { Icon::Folder } else { Icon::Queue },
                                    16.0,
                                    c.accent
                                ),
                                strong(current_name, 14, c.fg).width(Length::Fill),
                                icon(Icon::ChevronRight, 14.0, c.muted),
                            ]
                            .spacing(10)
                            .align_y(Alignment::Center)
                        )
                        .padding([4, 6])
                        .width(Length::Fill)
                        .on_press(Msg::Nav(target_nav))
                        .style(move |_, s| theme::button_style(c, Kind::Quiet, s)),
                        button(
                            row![icon(Icon::Edit, 14.0, c.muted), label("Rename", 12, c.fg)]
                                .spacing(5)
                                .align_y(Alignment::Center)
                        )
                        .padding([6, 10])
                        .on_press(Msg::StartRename(is_folder, id, current_name.to_owned()))
                        .style(move |_, s| theme::button_style(c, Kind::Quiet, s)),
                        delete_control
                    ]
                    .spacing(8)
                    .align_y(Alignment::Center)
                    .into()
                };

                items = items.push(
                    container(row_el)
                        .padding([10, 12])
                        .width(Length::Fill)
                        .style(move |_| theme::panel(c, c.card, 12.0)),
                );
            }
        }

        if !self.podcasts.is_empty() {
            items = items.push(container(label("YOUR PODCASTS", 10, c.muted)).padding([10, 0]));
        }
        for p in &self.podcasts {
            let id = p.id;
            let mut folders = row![chip(
                c,
                "Home",
                Some(Msg::Assign(id, String::new())),
                if p.folder_uuid.is_empty() {
                    Kind::Selected
                } else {
                    Kind::Quiet
                }
            )]
            .spacing(6);
            for f in &self.folders {
                let fuuid = s(f, "uuid");
                folders = folders.push(chip(
                    c,
                    s(f, "name"),
                    Some(Msg::Assign(id, fuuid.into())),
                    if p.folder_uuid == fuuid {
                        Kind::Selected
                    } else {
                        Kind::Quiet
                    },
                ));
            }
            let open_podcast = button(
                row![
                    self.cover(c, Some(&p.uuid), 38.0),
                    column![
                        strong(&p.title, 15, c.fg),
                        label(
                            format!(
                                "{}  ·  {} unplayed / {} episodes",
                                p.author, p.unplayed_count, p.episode_count
                            ),
                            11,
                            c.muted
                        )
                    ]
                    .spacing(2)
                    .width(Length::Fill),
                    icon(Icon::ChevronRight, 15.0, c.muted)
                ]
                .spacing(10)
                .align_y(Alignment::Center),
            )
            .padding([4, 6])
            .width(Length::Fill)
            .on_press(Msg::Podcast(id))
            .style(move |_, s| theme::button_style(c, Kind::Quiet, s));

            items = items.push(
                container(
                    column![
                        row![
                            open_podcast,
                            confirm_chip(
                                c,
                                "Unfollow",
                                "Confirm unfollow",
                                ConfirmAction::Unsubscribe(id),
                                &self.confirm_action,
                                Kind::Danger
                            )
                        ]
                        .spacing(8)
                        .align_y(Alignment::Center),
                        folders.wrap()
                    ]
                    .spacing(8),
                )
                .padding([10, 14])
                .width(Length::Fill)
                .style(move |_| theme::panel(c, c.card, 12.0)),
            );
        }
        scroll(c, items).height(Length::Fill).into()
    }

    fn player_view(&self, compact: bool, dense: bool) -> Element<'_, Msg> {
        let c = self.appearance.colors;
        let mut state = self.player.as_ref().map(|p| p.state()).unwrap_or_default();
        if !state.loaded
            && let Some(e) = &self.current
        {
            state.duration = (e.duration as f64).max(0.0);
            let raw_pos = (e.position as f64 / 1000.0).max(0.0);
            state.position = if state.duration > 0.0 {
                raw_pos.min(state.duration)
            } else {
                raw_pos
            };
        }
        let title_str = self
            .current
            .as_ref()
            .map(|e| {
                let raw = if e.is_local() && self.local_display == "file_title" {
                    file_name(&e.description)
                } else {
                    &e.title
                };
                short(raw, if compact { 26 } else { 52 })
            })
            .unwrap_or_else(|| "Ready when you are".into());
        let subtitle_str = self
            .current
            .as_ref()
            .map(|e| {
                if e.is_local() {
                    match self.local_display.as_str() {
                        "file_title" => {
                            let filename = file_name(&e.description);
                            let stem = std::path::Path::new(filename)
                                .file_stem()
                                .and_then(|value| value.to_str())
                                .unwrap_or(filename);
                            if e.title.eq_ignore_ascii_case(stem) {
                                String::new()
                            } else {
                                short_tail(&e.title, if compact { 28 } else { 48 })
                            }
                        }
                        "path" => short_tail(&e.description, if compact { 28 } else { 48 }),
                        "title" => String::new(),
                        _ => short_tail(file_name(&e.description), if compact { 28 } else { 48 }),
                    }
                } else {
                    short(&e.podcast, if compact { 28 } else { 48 })
                }
            })
            .unwrap_or_else(|| {
                self.first_episode()
                    .map(|ep| format!("Press Play for {}", short(&ep.title, 34)))
                    .unwrap_or_else(|| "Choose an episode to start listening".into())
            });

        let cur_podcast_id = self
            .current
            .as_ref()
            .filter(|e| !e.is_local())
            .and_then(|e| self.podcasts.iter().find(|p| p.uuid == e.podcast_uuid))
            .map(|p| p.id);

        let title_el: Element<'_, Msg> = if let Some(cur) = &self.current {
            button(
                strong(title_str, if dense { 12 } else { 13 }, c.fg).wrapping(text::Wrapping::None),
            )
            .padding(0)
            .on_press(Msg::Info(cur.id))
            .style(move |_, _| theme::button_style(c, Kind::Quiet, widget::button::Status::Active))
            .into()
        } else {
            strong(title_str, if dense { 12 } else { 13 }, c.fg)
                .wrapping(text::Wrapping::None)
                .into()
        };

        let subtitle_el: Option<Element<'_, Msg>> = if subtitle_str.is_empty() {
            None
        } else if let Some(pid) = cur_podcast_id {
            Some(
                button(label(subtitle_str, 10, c.muted).wrapping(text::Wrapping::None))
                    .padding(0)
                    .on_press(Msg::Podcast(pid))
                    .style(move |_, s| theme::button_style(c, Kind::Quiet, s))
                    .into(),
            )
        } else {
            Some(
                label(subtitle_str, 10, c.muted)
                    .wrapping(text::Wrapping::None)
                    .into(),
            )
        };

        let words: Element<'_, Msg> = if let Some(sub) = subtitle_el {
            column![title_el, sub]
                .spacing(if dense { 2 } else { 4 })
                .into()
        } else {
            title_el
        };

        let cover_el = self.cover(
            c,
            self.current
                .as_ref()
                .filter(|e| !e.is_local())
                .map(|e| e.podcast_uuid.as_str()),
            if dense { 32.0 } else { 42.0 },
        );
        let clickable_cover: Element<'_, Msg> = if let Some(cur) = &self.current {
            button(cover_el)
                .padding(0)
                .on_press(Msg::Info(cur.id))
                .style(move |_, _| {
                    theme::button_style(c, Kind::Quiet, widget::button::Status::Active)
                })
                .into()
        } else {
            cover_el
        };

        let info = row![clickable_cover, words]
            .spacing(12)
            .align_y(Alignment::Center);

        let transport = row![
            control(
                c,
                Icon::Back,
                "Back 15 seconds",
                self.current.as_ref().map(|_| Msg::Skip(-15.0)),
                Kind::Quiet,
                if dense { 27.0 } else { 32.0 }
            ),
            control(
                c,
                if state.paused {
                    Icon::Play
                } else {
                    Icon::Pause
                },
                "Play / pause",
                (self.current.is_some() || !self.view_ids.is_empty()).then_some(Msg::Pause),
                Kind::Accent,
                if dense { 36.0 } else { 42.0 }
            ),
            control(
                c,
                Icon::Forward,
                "Forward 30 seconds",
                self.current.as_ref().map(|_| Msg::Skip(30.0)),
                Kind::Quiet,
                if dense { 27.0 } else { 32.0 }
            ),
            control(
                c,
                Icon::Next,
                "Next episode",
                self.current.as_ref().map(|_| Msg::Next),
                Kind::Quiet,
                if dense { 26.0 } else { 30.0 }
            )
        ]
        .spacing(if dense { 4 } else { 9 })
        .align_y(Alignment::Center);

        let (q_count, q_total_rem, q_active_rem, q_next) = &self.queue_summary;
        let queue_rem = if let (Some(current), Some(active_db)) = (&self.current, *q_active_rem)
            && state.loaded
        {
            let dur = if state.duration > 0.0 {
                state.duration
            } else {
                current.duration as f64
            };
            (q_total_rem - active_db + (dur - state.position.max(0.0)).max(0.0)).max(0.0)
        } else {
            *q_total_rem
        };
        let speed = self.speed.max(0.25);
        let adj_queue_rem = queue_rem / speed;
        let total_badge = if *q_count > 0 {
            *q_count
        } else {
            self.cloud_queue_count.max(self.local_queue_count)
        };

        let mut right = row![].spacing(8).align_y(Alignment::Center);
        if !compact && *q_count > 0 {
            let next_title = q_next
                .as_ref()
                .map(|e| {
                    let raw = if e.is_local() && self.local_display == "file_title" {
                        file_name(&e.description)
                    } else {
                        &e.title
                    };
                    format!("Next: {}", short(raw, 42))
                })
                .unwrap_or_else(|| "End of queue".into());
            let queue_meta = format!(
                "{} queued · {} left{}",
                q_count,
                human_duration(adj_queue_rem),
                if (speed - 1.0).abs() > 0.01 {
                    format!(" at {}", format_rate(speed))
                } else {
                    String::new()
                }
            );
            let up_next_pill = button(
                column![
                    strong(next_title, 11, c.fg).wrapping(text::Wrapping::None),
                    label(queue_meta, 10, c.muted).wrapping(text::Wrapping::None)
                ]
                .spacing(1),
            )
            .padding([4, 8])
            .on_press(Msg::ToggleQueueDrawer)
            .style(move |_, s| {
                theme::button_style(
                    c,
                    if self.queue_drawer_open {
                        Kind::Selected
                    } else {
                        Kind::Quiet
                    },
                    s,
                )
            });
            right = right
                .push(up_next_pill)
                .push(Space::new().width(Length::Fill));
        } else if !compact {
            right = right.push(Space::new().width(Length::Fill));
        }
        if !compact {
            right = right.push(rate_control(c, self.speed));
        }

        // Always-visible bottom-bar Up Next drawer toggle button with live count badge.
        let mut queue_btn_inner = row![icon(
            Icon::Queue,
            16.0,
            if self.queue_drawer_open {
                c.accent
            } else {
                c.muted
            }
        )]
        .spacing(5)
        .align_y(Alignment::Center);
        if total_badge > 0 {
            queue_btn_inner = queue_btn_inner.push(strong(
                total_badge.to_string(),
                11,
                if self.queue_drawer_open {
                    c.accent
                } else {
                    c.fg
                },
            ));
        }
        let queue_trigger = tooltip(
            button(queue_btn_inner)
                .padding([6, 9])
                .on_press(Msg::ToggleQueueDrawer)
                .style(move |_, s| {
                    theme::button_style(
                        c,
                        if self.queue_drawer_open {
                            Kind::Selected
                        } else {
                            Kind::Soft
                        },
                        s,
                    )
                }),
            container(label("Toggle Up Next drawer", 12, c.fg))
                .padding([6, 9])
                .style(move |_| theme::panel(c, c.hover, 7.0)),
            tooltip::Position::Top,
        )
        .gap(6);

        right = right.push(queue_trigger).push(control(
            c,
            Icon::Settings,
            "Playback settings",
            Some(Msg::TogglePlaybackSettings),
            if self.playback_settings || self.sleep.is_some() || self.sleep_eof {
                Kind::Selected
            } else {
                Kind::Quiet
            },
            32.0,
        ));

        let top = row![
            container(info).width(Length::FillPortion(3)),
            container(transport).center_x(Length::FillPortion(if compact { 3 } else { 2 })),
            container(right).width(Length::FillPortion(if compact { 2 } else { 5 }))
        ]
        .spacing(if dense { 6 } else { 12 })
        .align_y(Alignment::Center);

        let raw_rem = (state.duration - state.position.max(0.0)).max(0.0);
        let adj_rem = raw_rem / speed;
        let end_time_text = if self.show_remaining && state.duration > 0.0 {
            format!("−{}", format_clock(adj_rem))
        } else {
            format_clock(state.duration)
        };
        let end_time_tip = if self.show_remaining {
            format!(
                "Remaining at {} (click for full duration {})",
                format_rate(speed),
                format_clock(state.duration)
            )
        } else {
            format!(
                "Full duration (click for remaining at {})",
                format_rate(speed)
            )
        };
        let end_time_btn = tooltip(
            button(label(
                end_time_text,
                10,
                if self.show_remaining && state.duration > 0.0 {
                    c.accent
                } else {
                    c.muted
                },
            ))
            .padding([2, 5])
            .on_press(Msg::ToggleRemainingTime)
            .style(move |_, s| theme::button_style(c, Kind::Quiet, s)),
            container(label(end_time_tip, 11, c.fg))
                .padding([5, 8])
                .style(move |_| theme::panel(c, c.hover, 7.0)),
            tooltip::Position::Top,
        )
        .gap(5);
        let progress = row![
            label(format_clock(state.position), 10, c.muted),
            slider(
                0.0..=state.duration.max(1.0),
                state.position.min(state.duration.max(1.0)),
                Msg::Seek
            )
            .height(12)
            .style(move |_, s| theme::slider_style(c, s)),
            end_time_btn
        ]
        .spacing(12)
        .align_y(Alignment::Center);

        let mut contents = column![].spacing(if dense { 5 } else { 8 });
        if self.playback_settings {
            let options: Vec<String> = TRIMS.iter().map(|s| s.to_string()).collect();
            let trim = row![
                label("Trim silence", 12, c.muted),
                pick_list(options, Some(self.trim.clone()), Msg::Trim)
                    .text_size(12)
                    .padding([7, 10])
                    .style(move |_, s| theme::pick(c, s))
            ]
            .spacing(8)
            .align_y(Alignment::Center);
            let volume = row![
                icon(Icon::Volume, 17.0, c.muted),
                slider(0.0..=150.0, self.volume, Msg::Volume)
                    .on_release(Msg::SaveVolume)
                    .width(105)
                    .height(16)
                    .style(move |_, s| theme::slider_style(c, s))
            ]
            .spacing(8)
            .align_y(Alignment::Center);
            let timer = row![
                icon(Icon::Moon, 16.0, c.muted),
                label(
                    if self.sleep_eof {
                        "End of episode".into()
                    } else if let Some(deadline) = self.sleep {
                        format!(
                            "{}m left",
                            deadline
                                .saturating_duration_since(std::time::Instant::now())
                                .as_secs()
                                .div_ceil(60)
                        )
                    } else {
                        "Sleep".into()
                    },
                    12,
                    c.muted
                ),
                action(
                    c,
                    "Off",
                    Some(Msg::Sleep(0)),
                    if self.sleep.is_none() && !self.sleep_eof {
                        Kind::Selected
                    } else {
                        Kind::Quiet
                    }
                ),
                action(c, "15m", Some(Msg::Sleep(15)), Kind::Quiet),
                action(c, "30m", Some(Msg::Sleep(30)), Kind::Quiet),
                action(
                    c,
                    "End",
                    Some(Msg::Sleep(1)),
                    if self.sleep_eof {
                        Kind::Selected
                    } else {
                        Kind::Quiet
                    }
                )
            ]
            .spacing(3)
            .align_y(Alignment::Center);
            let mut media = row![label("Media keys", 12, c.muted)]
                .spacing(3)
                .align_y(Alignment::Center);
            for (secs, caption) in [
                (0, "Track"),
                (15, "±15s"),
                (30, "±30s"),
                (45, "±45s"),
                (60, "±60s"),
            ] {
                media = media.push(action(
                    c,
                    caption,
                    Some(Msg::MediaSkip(secs)),
                    if self.media_skip == secs {
                        Kind::Selected
                    } else {
                        Kind::Quiet
                    },
                ));
            }
            let speed_el: Element<'_, Msg> = if compact {
                rate_control(c, self.speed)
            } else {
                Space::new().width(0).into()
            };
            contents = contents.push(
                container(
                    row![trim, volume, timer, media, speed_el]
                        .spacing(20)
                        .wrap(),
                )
                .padding([10, 12])
                .style(move |_| theme::panel(c, c.card, 12.0)),
            );
        }
        contents = contents.push(top).push(progress);
        container(contents)
            .padding(if dense {
                [7, 14]
            } else if compact {
                [12, 16]
            } else {
                [12, 20]
            })
            .style(move |_| {
                let mut style = theme::panel(c, c.surface, 0.0);
                style.border = Border {
                    width: 1.0,
                    color: theme::mix(c.surface, c.border, 0.35),
                    radius: 0.0.into(),
                };
                style
            })
            .into()
    }
}
