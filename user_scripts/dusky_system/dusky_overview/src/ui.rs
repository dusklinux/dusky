use crate::hyprland::{self, Client, OverviewSnapshot, WorkspaceKey, WorkspaceView};
use crate::icons::{IconRegistry, ResolvedAppIcon};
use iced::alignment::{Horizontal, Vertical};
use iced::event::{self, Event};
use iced::futures::channel::mpsc::{self, UnboundedReceiver, UnboundedSender};
use iced::keyboard::{
    self, Key,
    key::{Code, Named, Physical},
};
use iced::widget::{
    Column, Row, Space, button, column, container, mouse_area, row, scrollable, stack, svg, text,
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
            home.join(".config/matugen/generated/dusky.json"),
            home.join(".config/matugen/generated/dusky_rust.json"),
            home.join(".config/matugen/generated/dusky_center.json"),
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
#[allow(clippy::enum_variant_names)]
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

#[derive(Debug, Clone, Copy)]
pub struct SpringAnimation {
    pub target: f32,
    pub velocity: f32,
    pub last_tick: Instant,
    pub omega: f32, // Natural frequency (rad/s)
    pub zeta: f32,  // Damping ratio (1.0 = critically damped)
}

impl SpringAnimation {
    pub fn new(initial_target: f32, omega: f32, zeta: f32, now: Instant) -> Self {
        Self {
            target: initial_target,
            velocity: 0.0,
            last_tick: now,
            omega,
            zeta,
        }
    }

    #[inline]
    pub fn set_target(&mut self, target: f32, now: Instant) {
        if (self.target - target).abs() > 0.0001 {
            if self.velocity == 0.0 {
                self.last_tick = now;
            }
            self.target = target;
        }
    }

    pub fn tick(&mut self, pos: &mut f32, now: Instant) -> bool {
        let raw_dt = now.saturating_duration_since(self.last_tick).as_secs_f32();
        self.last_tick = now;
        let dt = raw_dt.clamp(0.0, 0.050);
        if dt == 0.0 {
            return (*pos - self.target).abs() > 0.0005 || self.velocity.abs() > 0.005;
        }
        const SUBSTEPS: usize = 4;
        let sub_dt = dt / (SUBSTEPS as f32);
        for _ in 0..SUBSTEPS {
            let displacement = *pos - self.target;
            let spring_force = -self.omega * self.omega * displacement;
            let damping_force = -2.0 * self.zeta * self.omega * self.velocity;
            let accel = spring_force + damping_force;
            self.velocity += accel * sub_dt;
            *pos += self.velocity * sub_dt;
        }
        let remaining = (self.target - *pos).abs();
        if remaining <= 0.0005 && self.velocity.abs() <= 0.005 {
            *pos = self.target;
            self.velocity = 0.0;
            false
        } else {
            true
        }
    }
}

#[inline]
fn lerp(a: f32, b: f32, t: f32) -> f32 {
    a + (b - a) * t.clamp(0.0, 1.0)
}

#[inline]
fn lerp_color(a: Color, b: Color, t: f32) -> Color {
    let t = t.clamp(0.0, 1.0);
    Color {
        r: a.r + (b.r - a.r) * t,
        g: a.g + (b.g - a.g) * t,
        b: a.b + (b.b - a.b) * t,
        a: a.a + (b.a - a.a) * t,
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SelectionTarget {
    Workspace(usize),
    Window { ws_idx: usize, win_idx: usize },
}

#[derive(Debug, Clone)]
pub enum Message {
    AnimationFrame(Instant),
    WindowHoverEntered { ws_idx: usize, win_idx: usize },
    WindowHoverExited { ws_idx: usize, win_idx: usize },
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

#[derive(Debug, Clone, Copy)]
struct GroupTabInfo<'a> {
    win_idx: usize,
    client: &'a Client,
    is_selected: bool,
    is_active_tab: bool,
}

pub struct OverviewApp {
    snapshot: OverviewSnapshot,
    selected: SelectionTarget,
    hovered_window: Option<(usize, usize)>,
    palette: ThemePalette,
    custom_theme: Theme,
    icons: IconRegistry,
    entrance_pos: f32,
    entrance_spring: SpringAnimation,
    card_select_positions: Vec<f32>,
    card_animations: Vec<SpringAnimation>,
    window_hover_positions: Vec<Vec<f32>>,
    window_hover_animations: Vec<Vec<SpringAnimation>>,
    animating: bool,
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
        let now = Instant::now();
        let ws_count = snapshot.workspaces.len();
        let initial_ws_idx = match selected {
            SelectionTarget::Workspace(idx) => idx,
            SelectionTarget::Window { ws_idx, .. } => ws_idx,
        };
        let mut card_select_positions = Vec::with_capacity(ws_count);
        let mut card_animations = Vec::with_capacity(ws_count);
        let mut window_hover_positions = Vec::with_capacity(ws_count);
        let mut window_hover_animations = Vec::with_capacity(ws_count);

        for (idx, ws) in snapshot.workspaces.iter().enumerate() {
            let initial = if idx == initial_ws_idx { 1.0 } else { 0.0 };
            card_select_positions.push(initial);
            card_animations.push(SpringAnimation::new(initial, 38.0, 1.0, now));

            let win_count = ws.clients.len();
            window_hover_positions.push(vec![0.0; win_count]);
            window_hover_animations.push(
                (0..win_count)
                    .map(|_| SpringAnimation::new(0.0, 46.0, 0.82, now))
                    .collect(),
            );
        }
        let entrance_spring = SpringAnimation::new(1.0, 32.0, 1.0, now);
        let icons = IconRegistry::load();
        (
            Self {
                snapshot,
                selected,
                hovered_window: None,
                palette,
                custom_theme,
                icons,
                entrance_pos: 0.0,
                entrance_spring,
                card_select_positions,
                card_animations,
                window_hover_positions,
                window_hover_animations,
                animating: true,
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

    fn sync_selection_springs(&mut self) {
        let now = Instant::now();
        let cur_ws = self.current_ws_index();
        for (idx, anim) in self.card_animations.iter_mut().enumerate() {
            let target = if idx == cur_ws { 1.0 } else { 0.0 };
            anim.set_target(target, now);
        }
        self.animating = true;
    }

    fn sync_hover_springs(&mut self) {
        let now = Instant::now();
        for (ws_idx, ws_anims) in self.window_hover_animations.iter_mut().enumerate() {
            for (win_idx, anim) in ws_anims.iter_mut().enumerate() {
                let target = if self.hovered_window == Some((ws_idx, win_idx)) {
                    1.0
                } else {
                    0.0
                };
                anim.set_target(target, now);
            }
        }
        self.animating = true;
    }

    pub fn update(&mut self, message: Message) -> Task<Message> {
        match message {
            Message::AnimationFrame(now) => {
                let mut still_moving = self.entrance_spring.tick(&mut self.entrance_pos, now);
                for (i, anim) in self.card_animations.iter_mut().enumerate() {
                    if let Some(pos) = self.card_select_positions.get_mut(i)
                        && anim.tick(pos, now)
                    {
                        still_moving = true;
                    }
                }
                for (ws_idx, ws_anims) in self.window_hover_animations.iter_mut().enumerate() {
                    if let Some(ws_positions) = self.window_hover_positions.get_mut(ws_idx) {
                        for (win_idx, anim) in ws_anims.iter_mut().enumerate() {
                            if let Some(pos) = ws_positions.get_mut(win_idx)
                                && anim.tick(pos, now)
                            {
                                still_moving = true;
                            }
                        }
                    }
                }
                self.animating = still_moving;
                Task::none()
            }
            Message::WindowHoverEntered { ws_idx, win_idx } => {
                if self.hovered_window != Some((ws_idx, win_idx)) {
                    self.hovered_window = Some((ws_idx, win_idx));
                    self.sync_hover_springs();
                }
                Task::none()
            }
            Message::WindowHoverExited { ws_idx, win_idx } => {
                if self.hovered_window == Some((ws_idx, win_idx)) {
                    self.hovered_window = None;
                    self.sync_hover_springs();
                }
                Task::none()
            }
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
                self.sync_selection_springs();
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
                self.sync_selection_springs();
                Task::none()
            }
            Message::SelectNextWorkspace => {
                let count = self.snapshot.workspaces.len();
                if count > 0 {
                    let next_ws = (self.current_ws_index() + 1) % count;
                    self.select_workspace_by_index(next_ws);
                }
                self.sync_selection_springs();
                Task::none()
            }
            Message::SelectPrevWorkspace => {
                let count = self.snapshot.workspaces.len();
                if count > 0 {
                    let cur = self.current_ws_index();
                    let prev_ws = if cur == 0 { count - 1 } else { cur - 1 };
                    self.select_workspace_by_index(prev_ws);
                }
                self.sync_selection_springs();
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
                self.sync_selection_springs();
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
                self.sync_selection_springs();
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

        if self.animating {
            Subscription::batch([
                keyboard_sub,
                ipc_sub,
                iced::window::frames().map(Message::AnimationFrame),
            ])
        } else {
            Subscription::batch([keyboard_sub, ipc_sub])
        }
    }

    pub fn view(&self) -> Element<'_, Message> {
        let p = self.palette;
        let entrance = self.entrance_pos.clamp(0.0, 1.0);
        let entrance_offset = (16.5 * (1.0 - entrance)).round();

        let backdrop = button(Space::new().width(Length::Fill).height(Length::Fill))
            .width(Length::Fill)
            .height(Length::Fill)
            .on_press(Message::Dismiss)
            .style(move |_theme: &Theme, _status: button::Status| button::Style {
                background: Some(Background::Color(with_alpha(p.bg, 0.58 * entrance))),
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
            .padding(iced::Padding {
                top: 32.0 + entrance_offset,
                right: 40.0,
                bottom: (32.0 - entrance_offset).max(8.0),
                left: 40.0,
            });

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

        let sel_progress = self
            .card_select_positions
            .get(ws_idx)
            .copied()
            .unwrap_or(if is_highlighted { 1.0 } else { 0.0 });
        let entrance = self.entrance_pos.clamp(0.0, 1.0);

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
                    let unselected_bg = mix_color(p.bg, p.muted, 0.14);
                    let selected_bg = mix_color(p.bg, p.accent, 0.16);
                    lerp_color(unselected_bg, selected_bg, sel_progress)
                };

                let target_border_color = if is_special {
                    p.tertiary
                } else {
                    p.accent
                };
                let rest_border_color = if is_special {
                    with_alpha(p.tertiary, 0.78)
                } else if is_active {
                    with_alpha(p.secondary, 0.75)
                } else {
                    with_alpha(p.muted, 0.55)
                };

                let (border_color, border_width) = if hovered {
                    (if is_special { p.tertiary } else { with_alpha(p.accent, 0.90) }, 2.4)
                } else {
                    let col = lerp_color(rest_border_color, target_border_color, sel_progress);
                    let width = lerp(if is_active { 1.8 } else { 1.0 }, 2.5, sel_progress);
                    (col, width)
                };

                let shadow = Shadow {
                    color: with_alpha(
                        target_border_color,
                        (if is_special { 0.38 } else { 0.28 }) * sel_progress * entrance,
                    ),
                    offset: Vector::new(0.0, 4.0 * sel_progress),
                    blur_radius: 16.0 * sel_progress,
                };

                button::Style {
                    background: Some(Background::Color(with_alpha(base_bg, 0.96 * entrance))),
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
            Vec::with_capacity(ws.clients.len() + 3);
        card_layers.push(card_base_btn.into());

        // Consolidate grouped windows by their canonical group head address so each group renders
        // once as a multi-tab miniature rather than stacking opaque duplicates at identical coordinates.
        let mut rendered_group_heads: Vec<&str> = Vec::new();
        let mut render_units: Vec<(usize, Vec<GroupTabInfo<'a>>)> = Vec::new();

        for (win_idx, client) in ws.clients.iter().enumerate() {
            if client.grouped.len() > 1 {
                let head = client.grouped[0].as_str();
                if rendered_group_heads
                    .iter()
                    .any(|h| h.eq_ignore_ascii_case(head))
                {
                    continue;
                }
                rendered_group_heads.push(head);

                let mut tabs: Vec<GroupTabInfo<'a>> = client
                    .grouped
                    .iter()
                    .filter_map(|addr| {
                        ws.clients
                            .iter()
                            .enumerate()
                            .find(|(_, c)| c.address.eq_ignore_ascii_case(addr))
                            .map(|(idx, c)| GroupTabInfo {
                                win_idx: idx,
                                client: c,
                                is_selected: self.selected
                                    == SelectionTarget::Window {
                                        ws_idx,
                                        win_idx: idx,
                                    },
                                is_active_tab: c.visible,
                            })
                    })
                    .collect();

                if tabs.is_empty() {
                    tabs.push(GroupTabInfo {
                        win_idx,
                        client,
                        is_selected: self.selected == SelectionTarget::Window { ws_idx, win_idx },
                        is_active_tab: client.visible,
                    });
                }

                let rep_idx = tabs
                    .iter()
                    .find(|t| t.is_selected)
                    .or_else(|| tabs.iter().find(|t| t.is_active_tab))
                    .or_else(|| tabs.last())
                    .map(|t| t.win_idx)
                    .unwrap_or(win_idx);

                render_units.push((rep_idx, tabs));
            } else {
                render_units.push((
                    win_idx,
                    vec![GroupTabInfo {
                        win_idx,
                        client,
                        is_selected: self.selected == SelectionTarget::Window { ws_idx, win_idx },
                        is_active_tab: client.visible,
                    }],
                ));
            }
        }

        let ws_has_fullscreen = ws
            .clients
            .iter()
            .any(|c| c.fullscreen > 0 || c.fullscreen_client > 0);
        let has_fullscreen_with_others = ws_has_fullscreen && render_units.len() > 1;

        // When a fullscreen window coexists with background windows on this workspace,
        // add a full-bleed ambient fullscreen frame so the fullscreen state is unmistakable
        // while keeping underlying windows visible and interactive.
        if has_fullscreen_with_others {
            let fs_aura = container(Space::new().width(Length::Fill).height(Length::Fill))
                .width(Length::Fixed(preview_w - 6.0))
                .height(Length::Fixed(preview_h - 6.0))
                .style(move |_theme: &Theme| container::Style {
                    background: Some(Background::Color(with_alpha(
                        mix_color(p.bg, p.tertiary, 0.20),
                        0.18,
                    ))),
                    text_color: None,
                    border: Border {
                        color: with_alpha(p.tertiary, 0.55),
                        width: 1.5,
                        radius: 10.0.into(),
                    },
                    shadow: Shadow::default(),
                    ..Default::default()
                });
            card_layers.push(
                column![
                    Space::new().height(Length::Fixed(3.0)),
                    row![Space::new().width(Length::Fixed(3.0)), fs_aura],
                ]
                .into(),
            );
        }

        // Render unselected windows first, then promote the selected window to the top.
        // Do NOT reorder layers based on `hover_progress`, as changing `Stack` child indices
        // mid-animation swaps `MouseArea` hover states across sibling widgets in Iced.
        let mut promoted_layers: Vec<Element<'a, Message>> = Vec::new();
        for (rep_win_idx, group_tabs) in &render_units {
            let client = &ws.clients[*rep_win_idx];
            let is_win_selected = group_tabs.iter().any(|t| t.is_selected);
            let is_hypr_focused = group_tabs.iter().any(|t| {
                self.snapshot.focused_address.as_deref() == Some(t.client.address.as_str())
            });
            let hover_progress = group_tabs
                .iter()
                .filter_map(|t| {
                    self.window_hover_positions
                        .get(ws_idx)
                        .and_then(|v| v.get(t.win_idx))
                        .copied()
                })
                .fold(0.0_f32, f32::max);

            let layer = self.render_window_miniature(
                ws_idx,
                ws,
                *rep_win_idx,
                client,
                group_tabs,
                preview_w,
                preview_h,
                is_win_selected,
                is_hypr_focused,
                has_fullscreen_with_others,
                hover_progress,
            );
            if is_win_selected {
                promoted_layers.push(layer);
            } else {
                card_layers.push(layer);
            }
        }
        card_layers.extend(promoted_layers);

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

        let card_stack: Element<'a, Message> = stack(card_layers).into();
        let lift_y = (3.5 * sel_progress).round();
        column![
            Space::new().height(Length::Fixed((4.0 - lift_y).max(0.0))),
            card_stack,
            Space::new().height(Length::Fixed(lift_y)),
        ]
        .width(Length::Fixed(preview_w))
        .into()
    }

    #[allow(clippy::too_many_arguments)]
    fn render_window_miniature<'a>(
        &'a self,
        ws_idx: usize,
        ws: &'a WorkspaceView,
        rep_win_idx: usize,
        client: &'a Client,
        group_tabs: &[GroupTabInfo<'a>],
        preview_w: f32,
        preview_h: f32,
        is_selected: bool,
        is_hypr_focused: bool,
        has_fullscreen_with_others: bool,
        hover_progress: f32,
    ) -> Element<'a, Message> {
        let p = self.palette;
        let is_fullscreen = group_tabs
            .iter()
            .any(|t| t.client.fullscreen > 0 || t.client.fullscreen_client > 0);
        let is_grouped = group_tabs.len() > 1;
        let is_occluded_under_fs = has_fullscreen_with_others && !is_fullscreen;

        let mon_w = ws.monitor_w.max(1.0);
        let mon_h = ws.monitor_h.max(1.0);

        let inset = 4.5_f32;
        let fs_banner_h = 40.0_f32;
        let sub_top = if is_occluded_under_fs {
            fs_banner_h + inset + 2.0
        } else {
            inset
        };
        let avail_h = (preview_h - sub_top - inset).max(28.0);
        let scale_x = preview_w / mon_w;
        let scale_y = avail_h / mon_h;

        // 1. Compute static resting geometry (100% independent of hover_progress).
        let (base_x, mut base_y, base_w, base_h) = if is_fullscreen && has_fullscreen_with_others {
            // Dock the fullscreen window as a prominent high-contrast top banner when other windows
            // share the workspace so background tiled/floating windows remain visible below it.
            (
                inset,
                inset,
                (preview_w - inset * 2.0).max(48.0),
                fs_banner_h,
            )
        } else {
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

            let mut bx = (x1 * scale_x).round() + inset;
            let mut by = sub_top + (y1 * scale_y).round();
            let mut bw = (raw_w * scale_x).round() - (inset * 2.0);
            let mut bh = (raw_h * scale_y).round() - inset;

            bw = bw.clamp(42.0, (preview_w - inset * 2.0).max(42.0));
            bh = bh.clamp(28.0, avail_h.max(28.0));

            if bx + bw > preview_w - inset {
                bx = (preview_w - inset - bw).max(inset);
            }
            if by + bh > preview_h - inset {
                by = (preview_h - inset - bh).max(sub_top);
            }
            (bx, by, bw, bh)
        };

        // Nudge small floating windows out of the bottom-right workspace badge zone.
        if client.floating
            && !is_fullscreen
            && base_w <= 96.0
            && base_h <= 48.0
            && base_x + base_w > preview_w - 48.0
            && base_y + base_h > preview_h - 34.0
        {
            base_y = (preview_h - 34.0 - base_h).max(sub_top);
        }

        // 2. Compute the fixed, invariant outer hit-test slot (`slot_x, slot_y, slot_w, slot_h`).
        //    Because this slot never moves or resizes with `hover_progress`, the `mouse_area`
        //    never shifts out from under the cursor.
        const MAX_POP_X: f32 = 2.5;
        const MAX_POP_TOP: f32 = 3.0;
        const MAX_POP_BOT: f32 = 1.0;

        let slot_x = (base_x - MAX_POP_X).max(1.0).round();
        let slot_y = (base_y - MAX_POP_TOP).max(1.0).round();
        let slot_right = (base_x + base_w + MAX_POP_X).min(preview_w - 1.0).round();
        let slot_bottom = (base_y + base_h + MAX_POP_BOT).min(preview_h - 1.0).round();
        let slot_w = (slot_right - slot_x).max(base_w);
        let slot_h = (slot_bottom - slot_y).max(base_h);

        // 3. Compute the animated visual bounds inside the fixed slot.
        let hover_t = hover_progress.clamp(0.0, 1.0);
        let rest_pad_left = (base_x - slot_x).max(0.0);
        let rest_pad_top = (base_y - slot_y).max(0.0);
        let inner_x = (rest_pad_left * (1.0 - hover_t)).round();
        let inner_y = (rest_pad_top * (1.0 - hover_t)).round();
        let w = lerp(base_w, slot_w, hover_t)
            .round()
            .clamp(42.0, (slot_w - inner_x).max(42.0));
        let h = lerp(base_h, slot_h, hover_t)
            .round()
            .clamp(28.0, (slot_h - inner_y).max(28.0));

        let resolved_icon: ResolvedAppIcon = self.icons.resolve_client(client);
        let raw_class = if !client.class.is_empty() {
            client.class.as_str()
        } else if !client.initial_class.is_empty() {
            client.initial_class.as_str()
        } else {
            "Window"
        };
        let effective_class_label = match resolved_icon.display_badge.as_deref() {
            Some(tui_badge) => format!("{raw_class} · {tui_badge}"),
            None => raw_class.to_owned(),
        };
        let base_class_accent = class_accent_color(raw_class, &p);
        let accent = resolved_icon.resolve_color(base_class_accent, &p);

        // Anchor `tab_bar_h` to static `base_h` so it never jumps across height thresholds mid-hover.
        let tab_bar_h = if is_grouped {
            if base_h >= 48.0 {
                20.0
            } else {
                (base_h * 0.44).round().clamp(14.0, 18.0)
            }
        } else {
            0.0
        };
        let body_h = if is_grouped {
            (h - tab_bar_h - 2.0).max(14.0)
        } else {
            h
        };

        let active_tab_pos = group_tabs
            .iter()
            .position(|t| t.win_idx == rep_win_idx)
            .map(|idx| idx + 1)
            .unwrap_or(1);
        let tab_count = group_tabs.len();

        let class_truncated = truncate_str(
            &effective_class_label,
            ((base_w - 22.0) / 7.2).max(4.0) as usize,
        );
        let title_truncated = truncate_str(&client.title, (base_w / 5.8).max(6.0) as usize);

        let badge_str = if is_fullscreen {
            if is_grouped {
                format!("⛶ FULLSCREEN · {active_tab_pos}/{tab_count}")
            } else {
                String::from("⛶ FULLSCREEN")
            }
        } else if is_grouped {
            format!("⇥ {active_tab_pos}/{tab_count} TABS")
        } else if is_hypr_focused {
            String::from("●")
        } else if client.floating {
            String::from("FLT")
        } else {
            String::new()
        };

        let badge_color = if is_fullscreen {
            accent
        } else if is_grouped {
            p.accent
        } else {
            accent
        };

        let titlebar_icon_col = mix_color(accent, p.fg, 0.35);

        let label_content: Element<'a, Message> = if is_fullscreen && has_fullscreen_with_others {
            // Compact high-contrast layout for the docked fullscreen banner.
            row![
                container(text(badge_str).size(9).color(p.bg))
                    .padding([2, 6])
                    .style(move |_theme: &Theme| container::Style {
                        background: Some(Background::Color(accent)),
                        text_color: Some(p.bg),
                        border: Border {
                            color: Color::TRANSPARENT,
                            width: 0.0,
                            radius: 4.0.into(),
                        },
                        shadow: Shadow::default(),
                        ..Default::default()
                    }),
                Space::new().width(Length::Fixed(6.0)),
                render_svg_icon(resolved_icon.handle.clone(), 13.0, titlebar_icon_col),
                Space::new().width(Length::Fixed(5.0)),
                text(class_truncated).size(10).color(p.fg),
                Space::new().width(Length::Fixed(6.0)),
                text(title_truncated)
                    .size(10)
                    .color(with_alpha(p.fg, 0.85)),
                Space::new().width(Length::Fill),
            ]
            .padding([0, 6])
            .align_y(Vertical::Center)
            .height(Length::Fill)
            .into()
        } else if body_h >= 42.0 {
            let titlebar = container(
                row![
                    render_svg_icon(resolved_icon.handle.clone(), 12.5, titlebar_icon_col),
                    Space::new().width(Length::Fixed(5.0)),
                    text(class_truncated).size(10).color(p.fg),
                    Space::new().width(Length::Fill),
                    text(badge_str).size(9).color(badge_color),
                ]
                .align_y(Vertical::Center),
            )
            .padding([2, 6])
            .width(Length::Fill)
            .style(move |_theme: &Theme| container::Style {
                background: Some(Background::Color(with_alpha(
                    mix_color(p.bg, if is_fullscreen { p.tertiary } else { accent }, 0.30),
                    0.80,
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

            let body_inner: Element<'a, Message> = if body_h >= 68.0 && w >= 76.0 {
                let hero_icon_col = with_alpha(
                    mix_color(accent, p.fg, 0.25),
                    lerp(0.82, 0.98, hover_t),
                );
                column![
                    render_svg_icon(resolved_icon.handle.clone(), 20.0, hero_icon_col),
                    Space::new().height(Length::Fixed(4.0)),
                    text(title_truncated)
                        .size(10)
                        .color(with_alpha(p.fg, 0.90)),
                ]
                .align_x(Horizontal::Center)
                .into()
            } else {
                text(title_truncated)
                    .size(10)
                    .color(with_alpha(p.fg, 0.88))
                    .into()
            };

            let body = container(body_inner)
                .width(Length::Fill)
                .height(Length::Fill)
                .padding([2, 6])
                .center_x(Length::Fill)
                .center_y(Length::Fill);

            column![titlebar, body].into()
        } else {
            let compact_text = if is_fullscreen {
                format!("⛶ {class_truncated}")
            } else {
                class_truncated
            };
            container(
                row![
                    render_svg_icon(resolved_icon.handle.clone(), 11.5, titlebar_icon_col),
                    Space::new().width(Length::Fixed(4.0)),
                    text(compact_text).size(10).color(p.fg),
                ]
                .align_y(Vertical::Center),
            )
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
        let body_radius = if is_grouped { 6.5 } else { 8.5 };

        let win_button = button(centered_box)
            .width(Length::Fixed(w))
            .height(Length::Fixed(body_h))
            .padding(2)
            .on_press(Message::ActivateWindow {
                workspace: ws.key.clone(),
                address: client.address.clone(),
                floating: client.floating,
            })
            .style(move |_theme: &Theme, status: button::Status| {
                let hovered = matches!(status, button::Status::Hovered | button::Status::Pressed);
                let eff_hover = if hovered { 1.0_f32 } else { hover_t };

                let rest_bg = if is_fullscreen {
                    mix_color(p.bg, accent, 0.26)
                } else if is_selected {
                    mix_color(p.bg, p.accent, 0.24)
                } else if is_floating {
                    mix_color(p.bg, p.secondary, 0.18)
                } else {
                    mix_color(p.bg, p.muted, 0.28)
                };
                let hover_bg = if is_fullscreen {
                    mix_color(p.bg, accent, 0.38)
                } else {
                    mix_color(p.bg, p.accent, 0.32)
                };
                let bg = lerp_color(rest_bg, hover_bg, eff_hover);

                let base_alpha = if is_occluded_under_fs && !is_selected {
                    lerp(0.78, 0.97, eff_hover)
                } else {
                    lerp(0.94, 0.99, eff_hover)
                };

                let rest_border_col = if is_fullscreen {
                    accent
                } else if is_selected {
                    p.accent
                } else if is_hypr_focused {
                    with_alpha(p.secondary, 0.90)
                } else {
                    with_alpha(accent, if is_occluded_under_fs { 0.45 } else { 0.62 })
                };
                let hover_border_col = mix_color(
                    if is_fullscreen { accent } else { p.accent },
                    Color::WHITE,
                    0.28,
                );
                let border_color = lerp_color(rest_border_col, hover_border_col, eff_hover);
                let rest_border_w = if is_fullscreen || is_selected {
                    2.0
                } else if is_hypr_focused {
                    1.5
                } else {
                    1.0
                };
                let border_width = lerp(rest_border_w, 2.2, eff_hover);

                let shadow_color = if is_fullscreen {
                    p.tertiary
                } else {
                    mix_color(accent, p.accent, 0.5)
                };
                let shadow_alpha = 0.40 * eff_hover
                    + if is_fullscreen {
                        0.32
                    } else if is_selected {
                        0.24
                    } else {
                        0.0
                    };

                button::Style {
                    background: Some(Background::Color(with_alpha(bg, base_alpha))),
                    text_color: p.fg,
                    border: Border {
                        color: border_color,
                        width: border_width,
                        radius: body_radius.into(),
                    },
                    shadow: Shadow {
                        color: with_alpha(shadow_color, shadow_alpha.clamp(0.0, 0.65)),
                        offset: Vector::new(
                            0.0,
                            4.5 * eff_hover + if is_fullscreen || is_selected { 2.0 } else { 0.0 },
                        ),
                        blur_radius: 14.0 * eff_hover
                            + if is_fullscreen {
                                10.0
                            } else if is_selected {
                                6.0
                            } else {
                                0.0
                            },
                    },
                    ..Default::default()
                }
            });

        let visual_unit: Element<'a, Message> = if is_grouped {
            let per_tab_chars =
                (((base_w / (tab_count as f32)) - 18.0) / 6.6).max(3.0) as usize;
            let mut tab_pills: Vec<Element<'a, Message>> = Vec::with_capacity(tab_count);

            for tab in group_tabs {
                let tab_resolved = self.icons.resolve_client(tab.client);
                let tab_class = if !tab.client.class.is_empty() {
                    tab.client.class.as_str()
                } else {
                    "win"
                };
                let tab_base_accent = class_accent_color(tab_class, &p);
                let tab_accent = tab_resolved.resolve_color(tab_base_accent, &p);

                let short_lead = tab_resolved.display_badge.as_deref().unwrap_or(tab_class);
                let raw_tab_label = if !tab.client.title.is_empty() && per_tab_chars >= 8 {
                    if tab
                        .client
                        .title
                        .to_ascii_lowercase()
                        .starts_with(&short_lead.to_ascii_lowercase())
                    {
                        tab.client.title.clone()
                    } else {
                        format!("{short_lead}: {}", tab.client.title)
                    }
                } else {
                    short_lead.to_owned()
                };

                let prefix = if tab.is_active_tab { "● " } else { "" };
                let full_label = format!(
                    "{prefix}{}",
                    truncate_str(
                        &raw_tab_label,
                        per_tab_chars.saturating_sub(prefix.chars().count())
                    )
                );

                let is_tab_active = tab.is_active_tab;
                let is_tab_sel = tab.is_selected;
                let tab_icon_col = if is_tab_sel || is_tab_active {
                    mix_color(tab_accent, Color::WHITE, 0.32)
                } else {
                    with_alpha(mix_color(tab_accent, p.fg, 0.30), 0.88)
                };

                let pill_btn = button(
                    container(
                        row![
                            render_svg_icon(tab_resolved.handle, 10.5, tab_icon_col),
                            Space::new().width(Length::Fixed(3.5)),
                            text(full_label).size(9),
                        ]
                        .align_y(Vertical::Center),
                    )
                    .width(Length::Fill)
                    .height(Length::Fill)
                    .center_x(Length::Fill)
                    .center_y(Length::Fill)
                    .clip(true),
                )
                .width(Length::FillPortion(1))
                .height(Length::Fixed(tab_bar_h))
                .padding([1, 4])
                .on_press(Message::ActivateWindow {
                    workspace: ws.key.clone(),
                    address: tab.client.address.clone(),
                    floating: tab.client.floating,
                })
                .style(move |_theme: &Theme, status: button::Status| {
                    let hovered =
                        matches!(status, button::Status::Hovered | button::Status::Pressed);
                    let bg = if is_tab_sel {
                        mix_color(p.bg, p.accent, if hovered { 0.58 } else { 0.48 })
                    } else if is_tab_active {
                        mix_color(p.bg, tab_accent, if hovered { 0.50 } else { 0.38 })
                    } else if hovered {
                        mix_color(p.bg, tab_accent, 0.36)
                    } else {
                        with_alpha(
                            mix_color(p.bg, p.muted, lerp(0.22, 0.30, hover_t)),
                            lerp(0.88, 0.96, hover_t),
                        )
                    };

                    let border_col = if is_tab_sel {
                        mix_color(p.accent, Color::WHITE, 0.25)
                    } else if is_tab_active || hovered {
                        tab_accent
                    } else {
                        with_alpha(p.muted, lerp(0.60, 0.80, hover_t))
                    };

                    button::Style {
                        background: Some(Background::Color(bg)),
                        text_color: if is_tab_sel || is_tab_active || hovered {
                            p.fg
                        } else {
                            with_alpha(p.fg, lerp(0.72, 0.88, hover_t))
                        },
                        border: Border {
                            color: border_col,
                            width: if is_tab_sel || is_tab_active || hovered {
                                1.5
                            } else {
                                1.0
                            },
                            radius: 5.0.into(),
                        },
                        shadow: if is_tab_active || is_tab_sel || hovered {
                            Shadow {
                                color: with_alpha(tab_accent, 0.28),
                                offset: Vector::new(0.0, 1.0),
                                blur_radius: 4.0,
                            }
                        } else {
                            Shadow::default()
                        },
                        ..Default::default()
                    }
                });

                tab_pills.push(pill_btn.into());
            }

            let tab_bar = Row::with_children(tab_pills)
                .spacing(2)
                .width(Length::Fixed(w))
                .height(Length::Fixed(tab_bar_h));

            column![
                tab_bar,
                Space::new().height(Length::Fixed(2.0)),
                win_button
            ]
            .width(Length::Fixed(w))
            .into()
        } else {
            win_button.into()
        };

        // 4. Wrap the animated visual unit inside a fixed-size slot container and a single
        //    unified `mouse_area` so hover state is 100% stable across tabs, gaps, and edges.
        let positioned_visual = column![
            Space::new().height(Length::Fixed(inner_y)),
            row![Space::new().width(Length::Fixed(inner_x)), visual_unit],
        ];

        let fixed_hit_slot = container(positioned_visual)
            .width(Length::Fixed(slot_w))
            .height(Length::Fixed(slot_h));

        let unified_hover_zone: Element<'a, Message> = mouse_area(fixed_hit_slot)
            .on_enter(Message::WindowHoverEntered {
                ws_idx,
                win_idx: rep_win_idx,
            })
            .on_exit(Message::WindowHoverExited {
                ws_idx,
                win_idx: rep_win_idx,
            })
            .into();

        column![
            Space::new().height(Length::Fixed(slot_y)),
            row![
                Space::new().width(Length::Fixed(slot_x)),
                unified_hover_zone
            ],
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

fn render_svg_icon<'a>(handle: svg::Handle, size: f32, color: Color) -> Element<'a, Message> {
    svg(handle)
        .width(Length::Fixed(size))
        .height(Length::Fixed(size))
        .style(move |_theme: &Theme, _status: svg::Status| svg::Style {
            color: Some(color),
        })
        .into()
}
