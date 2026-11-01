use crate::hyprland::{self, Client, OverviewSnapshot, WorkspaceKey, WorkspaceView};
use iced::alignment::{Horizontal, Vertical};
use iced::event::{self, Event};
use iced::futures::channel::mpsc::{self, UnboundedReceiver, UnboundedSender};
use iced::keyboard::{
    self, Key,
    key::{Code, Named, Physical},
};
use iced::widget::{
    Column, Row, Space, button, column, container, row, scrollable, stack, text,
};
use iced::{Background, Border, Color, Element, Length, Shadow, Subscription, Task, Theme, Vector};
use serde_json::Value;
use std::env;
use std::fs;
use std::io::{BufRead, BufReader};
use std::os::unix::net::{UnixListener, UnixStream};
use std::os::unix::process::CommandExt;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::{Mutex, OnceLock};
use std::thread;
use std::time::{Duration, Instant};

const PREVIEW_BASE_HEIGHT: f32 = 220.0;
const MAX_COLUMNS: usize = 3;

#[derive(Debug, Clone, Copy)]
pub struct ThemePalette {
    pub bg: Color,
    pub fg: Color,
    pub accent: Color,
    pub muted: Color,
    pub secondary: Color,
    pub tertiary: Color,
}

impl Default for ThemePalette {
    fn default() -> Self {
        Self {
            bg: Color::from_rgb8(0x13, 0x13, 0x18),
            fg: Color::from_rgb8(0xe4, 0xe1, 0xe9),
            accent: Color::from_rgb8(0xbe, 0xc2, 0xff),
            muted: Color::from_rgb8(0x46, 0x46, 0x4f),
            secondary: Color::from_rgb8(0xc5, 0xc4, 0xdd),
            tertiary: Color::from_rgb8(0xe6, 0xba, 0xda),
        }
    }
}

impl ThemePalette {
    pub fn load() -> Self {
        let mut palette = Self::default();
        let Some(home) = env::var_os("HOME").map(PathBuf::from) else {
            return palette;
        };

        let candidates = [
            home.join(".config/matugen/generated/dusky_tui.json"),
            home.join(".config/matugen/generated/dusky_tray.json"),
        ];

        for path in candidates {
            let Ok(raw) = fs::read_to_string(&path) else {
                continue;
            };
            let Ok(json) = serde_json::from_str::<Value>(&raw) else {
                continue;
            };

            if let Some(c) = extract_color(&json, &["bg", "background", "surface"]) {
                palette.bg = c;
            }
            if let Some(c) = extract_color(&json, &["fg", "foreground", "on_surface", "text"]) {
                palette.fg = c;
            }
            if let Some(c) = extract_color(&json, &["accent", "primary"]) {
                palette.accent = c;
            }
            if let Some(c) = extract_color(&json, &["muted", "outline", "surface_variant"]) {
                palette.muted = c;
            }
            if let Some(c) = extract_color(&json, &["secondary", "accent_secondary"]) {
                palette.secondary = c;
            }
            if let Some(c) = extract_color(&json, &["tertiary", "special", "accent_tertiary"]) {
                palette.tertiary = c;
            }
            break;
        }

        palette
    }
}

fn extract_color(root: &Value, keys: &[&str]) -> Option<Color> {
    for &key in keys {
        if let Some(val) = root.get(key).and_then(Value::as_str)
            && let Some(color) = parse_hex_color(val)
        {
            return Some(color);
        }
        if let Some(colors) = root.get("colors") {
            if let Some(val) = colors.get(key).and_then(Value::as_str)
                && let Some(color) = parse_hex_color(val)
            {
                return Some(color);
            }
            if let Some(dark) = colors.get("dark")
                && let Some(val) = dark.get(key).and_then(Value::as_str)
                && let Some(color) = parse_hex_color(val)
            {
                return Some(color);
            }
        }
    }
    None
}

fn parse_hex_color(hex: &str) -> Option<Color> {
    let clean = hex.trim().strip_prefix('#').unwrap_or(hex.trim());
    match clean.len() {
        6 => {
            let r = u8::from_str_radix(&clean[0..2], 16).ok()?;
            let g = u8::from_str_radix(&clean[2..4], 16).ok()?;
            let b = u8::from_str_radix(&clean[4..6], 16).ok()?;
            Some(Color::from_rgb8(r, g, b))
        }
        8 => {
            let r = u8::from_str_radix(&clean[0..2], 16).ok()?;
            let g = u8::from_str_radix(&clean[2..4], 16).ok()?;
            let b = u8::from_str_radix(&clean[4..6], 16).ok()?;
            let a = u8::from_str_radix(&clean[6..8], 16).ok()?;
            Some(Color::from_rgba8(r, g, b, f32::from(a) / 255.0))
        }
        _ => None,
    }
}

fn with_alpha(mut color: Color, a: f32) -> Color {
    color.a = a;
    color
}

fn mix_color(a: Color, b: Color, t: f32) -> Color {
    let t = t.clamp(0.0, 1.0);
    Color::from_rgba(
        a.r + (b.r - a.r) * t,
        a.g + (b.g - a.g) * t,
        a.b + (b.b - a.b) * t,
        a.a + (b.a - a.a) * t,
    )
}

#[derive(Debug, Clone)]
pub enum PostExitAction {
    FocusWindow {
        workspace: WorkspaceKey,
        address: String,
        floating: bool,
    },
    FocusWorkspace(WorkspaceKey),
    FocusSnapPicker,
}

static POST_EXIT_ACTION: OnceLock<PostExitAction> = OnceLock::new();
static IPC_TX: OnceLock<UnboundedSender<Message>> = OnceLock::new();
static PENDING_IPC_MSG: Mutex<Option<Message>> = Mutex::new(None);

pub fn take_post_exit_action() -> Option<&'static PostExitAction> {
    POST_EXIT_ACTION.get()
}

fn notify_ui(msg: Message) {
    if let Some(tx) = IPC_TX.get() {
        let _ = tx.unbounded_send(msg);
    } else if let Ok(mut slot) = PENDING_IPC_MSG.lock() {
        *slot = Some(msg);
    }
}

fn external_signal_stream() -> UnboundedReceiver<Message> {
    let (tx, rx) = mpsc::unbounded();
    if let Ok(mut slot) = PENDING_IPC_MSG.lock()
        && let Some(msg) = slot.take()
    {
        let _ = tx.unbounded_send(msg);
    }
    let _ = IPC_TX.set(tx);
    rx
}

pub fn spawn_ipc_watchers(listener: UnixListener) {
    // 1. Single-instance toggle watcher: dismisses immediately when a second dusky_overview runs.
    thread::spawn(move || {
        if let Ok((_stream, _addr)) = listener.accept() {
            notify_ui(Message::Dismiss);
        }
    });

    // 2. Hyprland event2 socket watcher: if dusky_snap is launched externally and maps dusky-snap-pick,
    //    wait 25ms for frame capture completion and then uncover and focus dusky-snap-pick.
    thread::spawn(move || {
        let Some(sock2) = hyprland::event_socket_path() else {
            return;
        };
        let Ok(stream) = UnixStream::connect(sock2) else {
            return;
        };
        let reader = BufReader::new(stream);
        for line in reader.lines().map_while(Result::ok) {
            if line.starts_with("openwindow>>") && line.contains("dusky-snap-pick") {
                thread::sleep(Duration::from_millis(25));
                notify_ui(Message::SnapPickerMapped);
                break;
            }
        }
    });
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SelectionTarget {
    Workspace(usize),
    Window { ws_idx: usize, win_idx: usize },
}

#[derive(Debug, Clone)]
pub enum Message {
    ActivateWindow {
        workspace: WorkspaceKey,
        address: String,
        floating: bool,
    },
    ActivateWorkspace(WorkspaceKey),
    ActivateSelected,
    SelectNextItem,
    SelectPrevItem,
    SelectNextWorkspace,
    SelectPrevWorkspace,
    SelectNextWindowInWorkspace,
    SelectPrevWindowInWorkspace,
    QuickWorkspace(i32),
    TriggerScreenshot { full_screen: bool },
    SnapPickerMapped,
    Dismiss,
}

pub struct OverviewApp {
    snapshot: OverviewSnapshot,
    selected: SelectionTarget,
    palette: ThemePalette,
    custom_theme: Theme,
}

impl OverviewApp {
    pub fn new(snapshot: OverviewSnapshot, palette: ThemePalette) -> (Self, Task<Message>) {
        let selected = Self::initial_selection(&snapshot);
        let custom_theme = Theme::custom(
            String::from("DuskyMatugen"),
            iced::theme::Palette {
                background: palette.bg,
                text: palette.fg,
                primary: palette.accent,
                success: palette.secondary,
                warning: palette.tertiary,
                danger: palette.tertiary,
            },
        );
        (
            Self {
                snapshot,
                selected,
                palette,
                custom_theme,
            },
            Task::none(),
        )
    }

    pub fn namespace() -> String {
        String::from("dusky_overview")
    }

    pub fn theme(&self) -> Theme {
        self.custom_theme.clone()
    }

    pub fn palette(&self) -> ThemePalette {
        self.palette
    }

    fn initial_selection(snapshot: &OverviewSnapshot) -> SelectionTarget {
        // 1. Prioritize the focused window if it resides on an active visible workspace.
        if let Some(ref focused_addr) = snapshot.focused_address {
            for (ws_idx, ws) in snapshot.workspaces.iter().enumerate() {
                if !ws.is_active {
                    continue;
                }
                for (win_idx, client) in ws.clients.iter().enumerate() {
                    if &client.address == focused_addr {
                        return SelectionTarget::Window { ws_idx, win_idx };
                    }
                }
            }
        }

        // 2. Otherwise select the active visible workspace (or its top-most window).
        if let Some(ws_idx) = snapshot.workspaces.iter().position(|w| w.is_active) {
            if !snapshot.workspaces[ws_idx].clients.is_empty() {
                let win_idx = snapshot.workspaces[ws_idx].clients.len() - 1;
                return SelectionTarget::Window { ws_idx, win_idx };
            }
            return SelectionTarget::Workspace(ws_idx);
        }

        // 3. Fallback: match focused_address on any workspace, or default to Workspace(0).
        if let Some(ref focused_addr) = snapshot.focused_address {
            for (ws_idx, ws) in snapshot.workspaces.iter().enumerate() {
                for (win_idx, client) in ws.clients.iter().enumerate() {
                    if &client.address == focused_addr {
                        return SelectionTarget::Window { ws_idx, win_idx };
                    }
                }
            }
        }

        SelectionTarget::Workspace(0)
    }

    fn all_targets(&self) -> Vec<SelectionTarget> {
        let mut targets = Vec::new();
        for (ws_idx, ws) in self.snapshot.workspaces.iter().enumerate() {
            if ws.clients.is_empty() {
                targets.push(SelectionTarget::Workspace(ws_idx));
            } else {
                for win_idx in (0..ws.clients.len()).rev() {
                    targets.push(SelectionTarget::Window { ws_idx, win_idx });
                }
            }
        }
        if targets.is_empty() {
            targets.push(SelectionTarget::Workspace(0));
        }
        targets
    }

    fn current_ws_index(&self) -> usize {
        match self.selected {
            SelectionTarget::Workspace(ws_idx) => ws_idx,
            SelectionTarget::Window { ws_idx, .. } => ws_idx,
        }
    }

    fn select_workspace_by_index(&mut self, ws_idx: usize) {
        if let Some(ws) = self.snapshot.workspaces.get(ws_idx) {
            if ws.clients.is_empty() {
                self.selected = SelectionTarget::Workspace(ws_idx);
            } else {
                // Select the top-most / most recently focused window on that workspace.
                self.selected = SelectionTarget::Window {
                    ws_idx,
                    win_idx: ws.clients.len() - 1,
                };
            }
        }
    }

    pub fn update(&mut self, message: Message) -> Task<Message> {
        match message {
            Message::ActivateWindow {
                workspace,
                address,
                floating,
            } => {
                let _ = POST_EXIT_ACTION.set(PostExitAction::FocusWindow {
                    workspace,
                    address,
                    floating,
                });
                iced::exit()
            }
            Message::ActivateWorkspace(ws_key) => {
                let _ = POST_EXIT_ACTION.set(PostExitAction::FocusWorkspace(ws_key));
                iced::exit()
            }
            Message::ActivateSelected => {
                match &self.selected {
                    SelectionTarget::Workspace(ws_idx) => {
                        if let Some(ws) = self.snapshot.workspaces.get(*ws_idx) {
                            let _ = POST_EXIT_ACTION
                                .set(PostExitAction::FocusWorkspace(ws.key.clone()));
                        }
                    }
                    SelectionTarget::Window { ws_idx, win_idx } => {
                        if let Some(ws) = self.snapshot.workspaces.get(*ws_idx) {
                            if let Some(client) = ws.clients.get(*win_idx) {
                                let _ = POST_EXIT_ACTION.set(PostExitAction::FocusWindow {
                                    workspace: ws.key.clone(),
                                    address: client.address.clone(),
                                    floating: client.floating,
                                });
                            } else {
                                let _ = POST_EXIT_ACTION
                                    .set(PostExitAction::FocusWorkspace(ws.key.clone()));
                            }
                        }
                    }
                }
                iced::exit()
            }
            Message::SelectNextItem => {
                let targets = self.all_targets();
                if let Some(pos) = targets.iter().position(|t| *t == self.selected) {
                    self.selected = targets[(pos + 1) % targets.len()].clone();
                } else if let Some(first) = targets.first() {
                    self.selected = first.clone();
                }
                Task::none()
            }
            Message::SelectPrevItem => {
                let targets = self.all_targets();
                if let Some(pos) = targets.iter().position(|t| *t == self.selected) {
                    let prev = if pos == 0 { targets.len() - 1 } else { pos - 1 };
                    self.selected = targets[prev].clone();
                } else if let Some(first) = targets.first() {
                    self.selected = first.clone();
                }
                Task::none()
            }
            Message::SelectNextWorkspace => {
                let count = self.snapshot.workspaces.len();
                if count > 0 {
                    let next_ws = (self.current_ws_index() + 1) % count;
                    self.select_workspace_by_index(next_ws);
                }
                Task::none()
            }
            Message::SelectPrevWorkspace => {
                let count = self.snapshot.workspaces.len();
                if count > 0 {
                    let cur = self.current_ws_index();
                    let prev_ws = if cur == 0 { count - 1 } else { cur - 1 };
                    self.select_workspace_by_index(prev_ws);
                }
                Task::none()
            }
            Message::SelectNextWindowInWorkspace => {
                let ws_idx = self.current_ws_index();
                if let Some(ws) = self.snapshot.workspaces.get(ws_idx) {
                    let len = ws.clients.len();
                    if len == 0 {
                        self.selected = SelectionTarget::Workspace(ws_idx);
                    } else {
                        match self.selected {
                            SelectionTarget::Workspace(_) => {
                                self.selected = SelectionTarget::Window { ws_idx, win_idx: 0 };
                            }
                            SelectionTarget::Window { win_idx, .. } => {
                                if win_idx + 1 < len {
                                    self.selected = SelectionTarget::Window {
                                        ws_idx,
                                        win_idx: win_idx + 1,
                                    };
                                } else {
                                    self.selected = SelectionTarget::Workspace(ws_idx);
                                }
                            }
                        }
                    }
                }
                Task::none()
            }
            Message::SelectPrevWindowInWorkspace => {
                let ws_idx = self.current_ws_index();
                if let Some(ws) = self.snapshot.workspaces.get(ws_idx) {
                    let len = ws.clients.len();
                    if len == 0 {
                        self.selected = SelectionTarget::Workspace(ws_idx);
                    } else {
                        match self.selected {
                            SelectionTarget::Workspace(_) => {
                                self.selected = SelectionTarget::Window {
                                    ws_idx,
                                    win_idx: len - 1,
                                };
                            }
                            SelectionTarget::Window { win_idx, .. } => {
                                if win_idx > 0 {
                                    self.selected = SelectionTarget::Window {
                                        ws_idx,
                                        win_idx: win_idx - 1,
                                    };
                                } else {
                                    self.selected = SelectionTarget::Workspace(ws_idx);
                                }
                            }
                        }
                    }
                }
                Task::none()
            }
            Message::QuickWorkspace(id) => {
                let _ = POST_EXIT_ACTION
                    .set(PostExitAction::FocusWorkspace(WorkspaceKey::Normal(id)));
                iced::exit()
            }
            Message::TriggerScreenshot { full_screen } => {
                let mut cmd = Command::new("dusky_snap");
                if full_screen {
                    cmd.arg("--fullscreen");
                } else {
                    cmd.arg("--region");
                }
                cmd.stdin(Stdio::null())
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .process_group(0);

                if let Ok(mut child) = cmd.spawn() {
                    thread::spawn(move || {
                        if full_screen {
                            let _ = child.wait();
                        } else {
                            // Wait until dusky_snap captures the overview and maps dusky-snap-pick,
                            // then signal dusky_overview to unmap and focus dusky-snap-pick.
                            let deadline = Instant::now() + Duration::from_millis(1500);
                            while Instant::now() < deadline {
                                if hyprland::is_snap_picker_mapped() {
                                    thread::sleep(Duration::from_millis(20));
                                    notify_ui(Message::SnapPickerMapped);
                                    break;
                                }
                                thread::sleep(Duration::from_millis(12));
                            }
                            let _ = child.wait();
                        }
                    });
                }
                Task::none()
            }
            Message::SnapPickerMapped => {
                let _ = POST_EXIT_ACTION.set(PostExitAction::FocusSnapPicker);
                iced::exit()
            }
            Message::Dismiss => iced::exit(),
        }
    }

    pub fn subscription(&self) -> Subscription<Message> {
        let keyboard_sub = event::listen_with(|event, _status, _window_id| match event {
            Event::Keyboard(keyboard::Event::KeyPressed {
                key,
                physical_key,
                modifiers,
                ..
            }) => match key.as_ref() {
                Key::Named(Named::PrintScreen) => Some(Message::TriggerScreenshot {
                    full_screen: !modifiers.shift(),
                }),
                _ if matches!(physical_key, Physical::Code(Code::PrintScreen)) => {
                    Some(Message::TriggerScreenshot {
                        full_screen: !modifiers.shift(),
                    })
                }
                Key::Character("s" | "S") if modifiers.logo() => Some(Message::TriggerScreenshot {
                    full_screen: !modifiers.shift(),
                }),
                Key::Named(Named::Escape) => Some(Message::Dismiss),
                Key::Named(Named::Enter) | Key::Named(Named::Space) => {
                    Some(Message::ActivateSelected)
                }
                Key::Named(Named::Tab) => {
                    if modifiers.alt() {
                        Some(Message::Dismiss)
                    } else if modifiers.shift() {
                        Some(Message::SelectPrevItem)
                    } else {
                        Some(Message::SelectNextItem)
                    }
                }
                Key::Named(Named::ArrowRight) => Some(Message::SelectNextWorkspace),
                Key::Named(Named::ArrowLeft) => Some(Message::SelectPrevWorkspace),
                Key::Named(Named::ArrowDown) => Some(Message::SelectNextWindowInWorkspace),
                Key::Named(Named::ArrowUp) => Some(Message::SelectPrevWindowInWorkspace),
                Key::Character("l" | "L") => Some(Message::SelectNextWorkspace),
                Key::Character("h" | "H") => Some(Message::SelectPrevWorkspace),
                Key::Character("j" | "J") => Some(Message::SelectNextWindowInWorkspace),
                Key::Character("k" | "K") => Some(Message::SelectPrevWindowInWorkspace),
                Key::Character("q" | "Q") => Some(Message::Dismiss),
                Key::Character(ch) if ch.len() == 1 => match ch.as_bytes()[0] {
                    b @ b'1'..=b'9' => Some(Message::QuickWorkspace((b - b'0') as i32)),
                    b'0' => Some(Message::QuickWorkspace(10)),
                    _ => None,
                },
                _ => None,
            },
            _ => None,
        });

        let ipc_sub = Subscription::run(external_signal_stream);

        Subscription::batch([keyboard_sub, ipc_sub])
    }

    pub fn view(&self) -> Element<'_, Message> {
        let p = self.palette;

        let backdrop = button(Space::new().width(Length::Fill).height(Length::Fill))
            .width(Length::Fill)
            .height(Length::Fill)
            .on_press(Message::Dismiss)
            .style(move |_theme: &Theme, _status: button::Status| button::Style {
                background: Some(Background::Color(with_alpha(p.bg, 0.58))),
                text_color: p.fg,
                border: Border::default(),
                shadow: Shadow::default(),
                ..Default::default()
            });

        let ws_count = self.snapshot.workspaces.len();
        let preview_height = if ws_count > 6 {
            175.0
        } else if ws_count > 3 {
            195.0
        } else {
            PREVIEW_BASE_HEIGHT
        };

        let mut grid_rows: Vec<Element<'_, Message>> = Vec::new();
        let mut current_row: Vec<Element<'_, Message>> = Vec::with_capacity(MAX_COLUMNS);

        for (ws_idx, ws) in self.snapshot.workspaces.iter().enumerate() {
            current_row.push(self.render_workspace_card(ws_idx, ws, preview_height));
            if current_row.len() == MAX_COLUMNS {
                grid_rows.push(
                    Row::with_children(std::mem::take(&mut current_row))
                        .spacing(24)
                        .align_y(Vertical::Center)
                        .into(),
                );
            }
        }

        if !current_row.is_empty() {
            grid_rows.push(
                Row::with_children(current_row)
                    .spacing(24)
                    .align_y(Vertical::Center)
                    .into(),
            );
        }

        let cards_grid = Column::with_children(grid_rows)
            .spacing(24)
            .align_x(Horizontal::Center);

        let centered_grid = container(cards_grid)
            .width(Length::Fill)
            .center_x(Length::Fill)
            .padding([32, 40]);

        let scroll_view = scrollable(centered_grid)
            .width(Length::Fill)
            .height(Length::Shrink)
            .direction(scrollable::Direction::Vertical(
                scrollable::Scrollbar::new()
                    .width(6)
                    .margin(4)
                    .scroller_width(6),
            ))
            .style(move |theme: &Theme, status: scrollable::Status| {
                let mut s = scrollable::default(theme, status);
                s.container = container::Style::default();
                s.vertical_rail.background = Some(Background::Color(with_alpha(p.muted, 0.25)));
                s.vertical_rail.border = Border {
                    color: Color::TRANSPARENT,
                    width: 0.0,
                    radius: 999.0.into(),
                };
                let scroller_color = match status {
                    scrollable::Status::Hovered { is_vertical_scrollbar_hovered: true, .. }
                    | scrollable::Status::Dragged { .. } => p.accent,
                    scrollable::Status::Hovered { .. } => with_alpha(p.secondary, 0.75),
                    scrollable::Status::Active { .. } => with_alpha(p.muted, 0.50),
                };
                s.vertical_rail.scroller.background = Background::Color(scroller_color);
                s.vertical_rail.scroller.border = Border {
                    color: Color::TRANSPARENT,
                    width: 0.0,
                    radius: 999.0.into(),
                };
                s
            });

        let centered_overlay = container(scroll_view)
            .width(Length::Fill)
            .height(Length::Fill)
            .center_x(Length::Fill)
            .center_y(Length::Fill);

        stack![backdrop, centered_overlay]
            .width(Length::Fill)
            .height(Length::Fill)
            .into()
    }

    fn render_workspace_card<'a>(
        &'a self,
        ws_idx: usize,
        ws: &'a WorkspaceView,
        preview_h: f32,
    ) -> Element<'a, Message> {
        let p = self.palette;
        let aspect = (ws.monitor_w / ws.monitor_h).clamp(0.55, 3.6);
        let preview_w = (preview_h * aspect).round();

        let is_ws_selected = self.selected == SelectionTarget::Workspace(ws_idx);
        let has_selected_window = matches!(
            self.selected,
            SelectionTarget::Window {
                ws_idx: sel_ws,
                ..
            } if sel_ws == ws_idx
        );
        let is_highlighted = is_ws_selected || has_selected_window;
        let is_special = ws.is_special;
        let is_active = ws.is_active;

        let empty_label: Element<'a, Message> = if ws.clients.is_empty() {
            container(
                text("Empty")
                    .size(12)
                    .color(with_alpha(p.muted, 0.80)),
            )
            .width(Length::Fill)
            .height(Length::Fill)
            .center_x(Length::Fill)
            .center_y(Length::Fill)
            .into()
        } else {
            Space::new().width(Length::Fill).height(Length::Fill).into()
        };

        // Full-bleed proportional monitor viewport button (no top header bar).
        let card_base_btn = button(empty_label)
            .width(Length::Fixed(preview_w))
            .height(Length::Fixed(preview_h))
            .padding(0)
            .on_press(Message::ActivateWorkspace(ws.key.clone()))
            .style(move |_theme: &Theme, status: button::Status| {
                let hovered = matches!(status, button::Status::Hovered | button::Status::Pressed);

                let base_bg = if is_special {
                    mix_color(p.bg, p.tertiary, if hovered { 0.16 } else { 0.10 })
                } else if hovered {
                    mix_color(p.bg, p.accent, 0.12)
                } else {
                    mix_color(p.bg, p.muted, 0.14)
                };

                let (border_color, border_width) = if is_special {
                    if is_highlighted || hovered {
                        (p.tertiary, 2.5)
                    } else {
                        (with_alpha(p.tertiary, 0.78), 2.0)
                    }
                } else if is_highlighted {
                    (p.accent, 2.5)
                } else if hovered {
                    (with_alpha(p.accent, 0.85), 2.0)
                } else if is_active {
                    (with_alpha(p.secondary, 0.75), 1.8)
                } else {
                    (with_alpha(p.muted, 0.55), 1.0)
                };

                let shadow = if is_special {
                    Shadow {
                        color: with_alpha(p.tertiary, if is_highlighted { 0.38 } else { 0.22 }),
                        offset: Vector::new(0.0, 4.0),
                        blur_radius: 18.0,
                    }
                } else if is_highlighted {
                    Shadow {
                        color: with_alpha(p.accent, 0.28),
                        offset: Vector::new(0.0, 4.0),
                        blur_radius: 14.0,
                    }
                } else {
                    Shadow::default()
                };

                button::Style {
                    background: Some(Background::Color(with_alpha(base_bg, 0.96))),
                    text_color: p.fg,
                    border: Border {
                        color: border_color,
                        width: border_width,
                        radius: 12.0.into(),
                    },
                    shadow,
                    ..Default::default()
                }
            });

        let mut card_layers: Vec<Element<'a, Message>> =
            Vec::with_capacity(ws.clients.len() + 2);
        card_layers.push(card_base_btn.into());

        // Render unselected windows first, then promote the currently selected window to the top.
        let mut selected_layer: Option<Element<'a, Message>> = None;
        for (win_idx, client) in ws.clients.iter().enumerate() {
            let is_win_selected = self.selected == SelectionTarget::Window { ws_idx, win_idx };
            let is_hypr_focused =
                self.snapshot.focused_address.as_deref() == Some(client.address.as_str());

            let layer = self.render_window_miniature(
                ws,
                client,
                preview_w,
                preview_h,
                is_win_selected,
                is_hypr_focused,
            );
            if is_win_selected {
                selected_layer = Some(layer);
            } else {
                card_layers.push(layer);
            }
        }
        if let Some(top_layer) = selected_layer {
            card_layers.push(top_layer);
        }

        // High-contrast workspace indicator badge overlaid in the bottom-right corner.
        let badge_text = if is_active && !is_special {
            format!("● {}", ws.title)
        } else {
            ws.title.clone()
        };

        let badge_pill = button(
            text(badge_text)
                .size(11)
                .color(if is_special || is_active || is_ws_selected {
                    p.bg
                } else {
                    p.fg
                }),
        )
        .padding([3, 9])
        .on_press(Message::ActivateWorkspace(ws.key.clone()))
        .style(move |_theme: &Theme, status: button::Status| {
            let hovered = matches!(status, button::Status::Hovered | button::Status::Pressed);
            let bg = if is_special {
                if hovered {
                    mix_color(p.tertiary, Color::WHITE, 0.18)
                } else {
                    p.tertiary
                }
            } else if is_active || is_ws_selected {
                if hovered {
                    mix_color(p.accent, Color::WHITE, 0.15)
                } else {
                    p.accent
                }
            } else if hovered {
                mix_color(p.bg, p.accent, 0.30)
            } else {
                with_alpha(mix_color(p.bg, p.muted, 0.28), 0.92)
            };

            let border_color = if is_special {
                p.tertiary
            } else if is_active || is_ws_selected || hovered {
                p.accent
            } else {
                with_alpha(p.muted, 0.85)
            };

            button::Style {
                background: Some(Background::Color(bg)),
                text_color: if is_special || is_active || is_ws_selected {
                    p.bg
                } else {
                    p.fg
                },
                border: Border {
                    color: border_color,
                    width: 1.0,
                    radius: 999.0.into(),
                },
                shadow: Shadow {
                    color: with_alpha(Color::BLACK, 0.45),
                    offset: Vector::new(0.0, 2.0),
                    blur_radius: 6.0,
                },
                ..Default::default()
            }
        });

        let badge_overlay = container(badge_pill)
            .width(Length::Fixed(preview_w))
            .height(Length::Fixed(preview_h))
            .align_x(Horizontal::Right)
            .align_y(Vertical::Bottom)
            .padding([8, 10]);

        card_layers.push(badge_overlay.into());

        stack(card_layers).into()
    }

    fn render_window_miniature<'a>(
        &'a self,
        ws: &'a WorkspaceView,
        client: &'a Client,
        preview_w: f32,
        preview_h: f32,
        is_selected: bool,
        is_hypr_focused: bool,
    ) -> Element<'a, Message> {
        let p = self.palette;
        let mon_w = ws.monitor_w.max(1.0);
        let mon_h = ws.monitor_h.max(1.0);
        let scale_x = preview_w / mon_w;
        let scale_y = preview_h / mon_h;

        let raw_x = client.at[0] as f32 - ws.monitor_x;
        let raw_y = client.at[1] as f32 - ws.monitor_y;
        let mut raw_w = (client.size[0] as f32).clamp(32.0, mon_w);
        let mut raw_h = (client.size[1] as f32).clamp(24.0, mon_h);

        let x1 = raw_x.clamp(0.0, (mon_w - 32.0).max(0.0));
        let y1 = raw_y.clamp(0.0, (mon_h - 24.0).max(0.0));
        let x2 = (raw_x + raw_w).clamp(x1 + 32.0, mon_w);
        let y2 = (raw_y + raw_h).clamp(y1 + 24.0, mon_h);
        raw_w = (x2 - x1).max(32.0);
        raw_h = (y2 - y1).max(24.0);

        let inset = 3.0_f32;
        let mut x = (x1 * scale_x).round() + inset;
        let mut y = (y1 * scale_y).round() + inset;
        let mut w = (raw_w * scale_x).round() - (inset * 2.0);
        let mut h = (raw_h * scale_y).round() - (inset * 2.0);

        w = w.clamp(42.0, (preview_w - inset * 2.0).max(42.0));
        h = h.clamp(28.0, (preview_h - inset * 2.0).max(28.0));

        if x + w > preview_w - inset {
            x = (preview_w - inset - w).max(inset);
        }
        if y + h > preview_h - inset {
            y = (preview_h - inset - h).max(inset);
        }

        // If a small floating window sits in the bottom-right badge zone, nudge it above the badge pill
        // so the badge never covers its label or turns its border into a halo around the badge.
        if client.floating
            && w <= 96.0
            && h <= 48.0
            && x + w > preview_w - 48.0
            && y + h > preview_h - 34.0
        {
            y = (preview_h - 34.0 - h).max(inset);
        }

        let class_label = if client.class.is_empty() {
            "Window"
        } else {
            client.class.as_str()
        };
        let accent = class_accent_color(class_label, &p);
        let class_truncated = truncate_str(class_label, (w / 7.0).max(4.0) as usize);
        let title_truncated = truncate_str(&client.title, (w / 6.0).max(5.0) as usize);

        let label_content: Element<'a, Message> = if h >= 50.0 {
            let badge_str = if is_hypr_focused {
                "●"
            } else if client.floating {
                "FLT"
            } else {
                ""
            };

            let titlebar = container(
                row![
                    text(class_truncated).size(10).color(p.fg),
                    Space::new().width(Length::Fill),
                    text(badge_str).size(9).color(accent),
                ]
                .align_y(Vertical::Center),
            )
            .padding([2, 6])
            .width(Length::Fill)
            .style(move |_theme: &Theme| container::Style {
                background: Some(Background::Color(with_alpha(
                    mix_color(p.bg, accent, 0.28),
                    0.75,
                ))),
                text_color: None,
                border: Border {
                    color: Color::TRANSPARENT,
                    width: 0.0,
                    radius: 4.0.into(),
                },
                shadow: Shadow::default(),
                ..Default::default()
            });

            let body = container(
                text(title_truncated)
                    .size(10)
                    .color(with_alpha(p.fg, 0.86)),
            )
            .width(Length::Fill)
            .height(Length::Fill)
            .padding([2, 6])
            .center_x(Length::Fill)
            .center_y(Length::Fill);

            column![titlebar, body].into()
        } else {
            container(text(class_truncated).size(10).color(p.fg))
                .width(Length::Fill)
                .height(Length::Fill)
                .center_x(Length::Fill)
                .center_y(Length::Fill)
                .into()
        };

        let centered_box = container(label_content)
            .width(Length::Fill)
            .height(Length::Fill)
            .clip(true);

        let is_floating = client.floating;
        let win_button = button(centered_box)
            .width(Length::Fixed(w))
            .height(Length::Fixed(h))
            .padding(2)
            .on_press(Message::ActivateWindow {
                workspace: ws.key.clone(),
                address: client.address.clone(),
                floating: client.floating,
            })
            .style(move |_theme: &Theme, status: button::Status| {
                let hovered = matches!(status, button::Status::Hovered | button::Status::Pressed);

                let bg = if hovered {
                    mix_color(p.bg, p.accent, 0.30)
                } else if is_selected {
                    mix_color(p.bg, p.accent, 0.22)
                } else if is_floating {
                    mix_color(p.bg, p.secondary, 0.18)
                } else {
                    mix_color(p.bg, p.muted, 0.28)
                };

                let (border_color, border_width) = if hovered {
                    (mix_color(p.accent, Color::WHITE, 0.25), 2.0)
                } else if is_selected {
                    (p.accent, 2.0)
                } else if is_hypr_focused {
                    (with_alpha(p.secondary, 0.90), 1.5)
                } else {
                    (with_alpha(accent, 0.58), 1.0)
                };

                button::Style {
                    background: Some(Background::Color(with_alpha(bg, 0.95))),
                    text_color: p.fg,
                    border: Border {
                        color: border_color,
                        width: border_width,
                        radius: 8.5.into(),
                    },
                    shadow: Shadow::default(),
                    ..Default::default()
                }
            });

        column![
            Space::new().height(Length::Fixed(y)),
            row![Space::new().width(Length::Fixed(x)), win_button],
        ]
        .into()
    }
}

fn truncate_str(input: &str, max_chars: usize) -> String {
    if max_chars == 0 {
        return String::new();
    }
    let count = input.chars().count();
    if count <= max_chars {
        input.to_owned()
    } else if max_chars <= 2 {
        input.chars().take(max_chars).collect()
    } else {
        let mut s: String = input.chars().take(max_chars - 1).collect();
        s.push('…');
        s
    }
}

fn class_accent_color(class_name: &str, palette: &ThemePalette) -> Color {
    let mut hash: u32 = 0x811c_9dc5;
    for byte in class_name.bytes() {
        hash ^= u32::from(byte.to_ascii_lowercase());
        hash = hash.wrapping_mul(0x0100_0193);
    }
    let anchors = [
        palette.accent,
        palette.secondary,
        palette.tertiary,
        mix_color(palette.accent, palette.tertiary, 0.5),
        mix_color(palette.accent, palette.secondary, 0.5),
        mix_color(palette.secondary, palette.tertiary, 0.5),
    ];
    anchors[(hash as usize) % anchors.len()]
}
