use crate::hyprland::{self, Client, OverviewSnapshot, WorkspaceKey, WorkspaceView};
use crate::icons::{IconRegistry, IconVisual, ResolvedAppIcon};
use iced::alignment::{Horizontal, Vertical};
use iced::event::{self, Event};
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

const PREVIEW_BASE_HEIGHT: f32 = 260.0;
const MAX_COLUMNS: usize = 3;
const HEARTBEAT_INTERVAL: Duration = Duration::from_millis(25);
const HOLD_INITIAL_DELAY: Duration = Duration::from_millis(325);
const HOLD_REPEAT_INTERVAL: Duration = Duration::from_millis(150);
const IPC_STREAM_THRESHOLD: Duration = Duration::from_millis(95);

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

#[inline]
fn contrast_text_color(bg: Color, dark_text: Color, light_text: Color) -> Color {
    let luminance = 0.2126 * bg.r + 0.7152 * bg.g + 0.0722 * bg.b;
    if luminance > 0.52 {
        dark_text
    } else {
        light_text
    }
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

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum IpcCommand {
    NextWs,
    PrevWs,
    NextItem,
    PrevItem,
    Activate,
    Dismiss,
    SnapPickerMapped,
}

static POST_EXIT_ACTION: OnceLock<PostExitAction> = OnceLock::new();
static PENDING_IPC: Mutex<Vec<IpcCommand>> = Mutex::new(Vec::new());

pub fn take_post_exit_action() -> Option<&'static PostExitAction> {
    POST_EXIT_ACTION.get()
}

pub fn send_ipc_command(cmd: IpcCommand) {
    if let Ok(mut queue) = PENDING_IPC.lock() {
        queue.push(cmd);
    }
}

pub fn spawn_ipc_watchers(listener: UnixListener) {
    // 1. Single-instance IPC command watcher: continuously handles next/prev/activate/dismiss commands.
    thread::spawn(move || {
        for stream in listener.incoming().flatten() {
            let _ = stream.set_read_timeout(Some(Duration::from_millis(50)));
            let reader = BufReader::new(stream);
            if let Some(Ok(line)) = reader.lines().next() {
                let cmd = match line.trim() {
                    "next_ws" | "next" => IpcCommand::NextWs,
                    "prev_ws" | "prev" | "reverse" => IpcCommand::PrevWs,
                    "next_item" => IpcCommand::NextItem,
                    "prev_item" => IpcCommand::PrevItem,
                    "activate" => {
                        send_ipc_command(IpcCommand::Activate);
                        break;
                    }
                    "dismiss" => {
                        send_ipc_command(IpcCommand::Dismiss);
                        break;
                    }
                    _ => IpcCommand::NextWs,
                };
                send_ipc_command(cmd);
            }
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
                send_ipc_command(IpcCommand::SnapPickerMapped);
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
    Heartbeat(Instant),
    ModifiersChanged(keyboard::Modifiers),
    CtrlKeyChanged(bool),
    ShiftKeyChanged(bool),
    TabPressed { shift: bool, ctrl: bool, alt: bool },
    TabReleased { alt: bool },
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
    window_icons: Vec<Vec<ResolvedAppIcon>>,
    entrance_pos: f32,
    entrance_spring: SpringAnimation,
    card_select_positions: Vec<f32>,
    card_animations: Vec<SpringAnimation>,
    window_hover_positions: Vec<Vec<f32>>,
    window_hover_animations: Vec<Vec<SpringAnimation>>,
    animating: bool,
    alt_held: bool,
    tab_held: bool,
    tab_shift: bool,
    tab_ctrl: bool,
    next_repeat_deadline: Option<Instant>,
    last_ipc_at: Option<Instant>,
    last_step_at: Option<Instant>,
}

impl OverviewApp {
    pub fn new(
        snapshot: OverviewSnapshot,
        palette: ThemePalette,
        reverse: bool,
        detailed: bool,
    ) -> (Self, Task<Message>) {
        if let Ok(mut queue) = PENDING_IPC.lock() {
            queue.clear();
        }
        let selected = Self::initial_selection(&snapshot, reverse, detailed);
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
        let icons = IconRegistry::load();
        let mut window_icons = Vec::with_capacity(ws_count);
        let mut card_select_positions = Vec::with_capacity(ws_count);
        let mut card_animations = Vec::with_capacity(ws_count);
        let mut window_hover_positions = Vec::with_capacity(ws_count);
        let mut window_hover_animations = Vec::with_capacity(ws_count);

        for (idx, ws) in snapshot.workspaces.iter().enumerate() {
            let initial = if idx == initial_ws_idx { 1.0 } else { 0.0 };
            card_select_positions.push(initial);
            // omega = 60.0 settles 90% in 65ms and 98% in 97ms for crisp 150ms auto-repeat cycling.
            card_animations.push(SpringAnimation::new(initial, 60.0, 1.0, now));

            let resolved: Vec<ResolvedAppIcon> = ws
                .clients
                .iter()
                .map(|client| icons.resolve_client(client))
                .collect();
            window_icons.push(resolved);

            let win_count = ws.clients.len();
            let win_positions: Vec<f32> = (0..win_count)
                .map(|win_idx| {
                    if selected == (SelectionTarget::Window { ws_idx: idx, win_idx }) {
                        1.0
                    } else {
                        0.0
                    }
                })
                .collect();
            window_hover_animations.push(
                win_positions
                    .iter()
                    .map(|&pos| SpringAnimation::new(pos, 46.0, 0.82, now))
                    .collect(),
            );
            window_hover_positions.push(win_positions);
        }
        let entrance_spring = SpringAnimation::new(1.0, 36.0, 1.0, now);
        (
            Self {
                snapshot,
                selected,
                hovered_window: None,
                palette,
                custom_theme,
                window_icons,
                entrance_pos: 0.0,
                entrance_spring,
                card_select_positions,
                card_animations,
                window_hover_positions,
                window_hover_animations,
                animating: true,
                alt_held: true,
                tab_held: false,
                tab_shift: reverse,
                tab_ctrl: detailed,
                next_repeat_deadline: None,
                last_ipc_at: Some(now),
                last_step_at: Some(now),
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

    fn active_selection(snapshot: &OverviewSnapshot) -> SelectionTarget {
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

    fn collect_targets(snapshot: &OverviewSnapshot) -> Vec<SelectionTarget> {
        let mut targets = Vec::new();
        for (ws_idx, ws) in snapshot.workspaces.iter().enumerate() {
            if ws.clients.is_empty() {
                targets.push(SelectionTarget::Workspace(ws_idx));
            } else {
                let focused_win = snapshot
                    .focused_address
                    .as_deref()
                    .and_then(|addr| ws.clients.iter().position(|c| c.address == addr));

                if let Some(f_idx) = focused_win {
                    targets.push(SelectionTarget::Window {
                        ws_idx,
                        win_idx: f_idx,
                    });
                    for win_idx in (0..ws.clients.len()).rev() {
                        if win_idx != f_idx {
                            targets.push(SelectionTarget::Window { ws_idx, win_idx });
                        }
                    }
                } else {
                    for win_idx in (0..ws.clients.len()).rev() {
                        targets.push(SelectionTarget::Window { ws_idx, win_idx });
                    }
                }
            }
        }
        if targets.is_empty() {
            targets.push(SelectionTarget::Workspace(0));
        }
        targets
    }

    fn initial_selection(
        snapshot: &OverviewSnapshot,
        reverse: bool,
        detailed: bool,
    ) -> SelectionTarget {
        let current = Self::active_selection(snapshot);
        let ws_count = snapshot.workspaces.len();

        if detailed || ws_count <= 1 {
            let targets = Self::collect_targets(snapshot);
            return if let Some(pos) = targets.iter().position(|t| *t == current) {
                if reverse {
                    let prev = if pos == 0 { targets.len() - 1 } else { pos - 1 };
                    targets[prev].clone()
                } else {
                    targets[(pos + 1) % targets.len()].clone()
                }
            } else {
                targets.first().cloned().unwrap_or(current)
            };
        }

        // Default workspace-level mode: step directly to the next/previous workspace card.
        let current_ws_idx = match current {
            SelectionTarget::Workspace(ws_idx) => ws_idx,
            SelectionTarget::Window { ws_idx, .. } => ws_idx,
        };
        let target_ws_idx = if reverse {
            if current_ws_idx == 0 {
                ws_count - 1
            } else {
                current_ws_idx - 1
            }
        } else {
            (current_ws_idx + 1) % ws_count
        };

        if snapshot.workspaces.get(target_ws_idx).is_some() {
            SelectionTarget::Workspace(target_ws_idx)
        } else {
            current
        }
    }

    fn all_targets(&self) -> Vec<SelectionTarget> {
        Self::collect_targets(&self.snapshot)
    }

    fn current_ws_index(&self) -> usize {
        match self.selected {
            SelectionTarget::Workspace(ws_idx) => ws_idx,
            SelectionTarget::Window { ws_idx, .. } => ws_idx,
        }
    }

    fn select_workspace_by_index(&mut self, ws_idx: usize) {
        if self.snapshot.workspaces.get(ws_idx).is_some() {
            self.selected = SelectionTarget::Workspace(ws_idx);
        }
    }

    fn sync_selection_springs(&mut self) {
        let now = Instant::now();
        let cur_ws = self.current_ws_index();
        for (idx, anim) in self.card_animations.iter_mut().enumerate() {
            let target = if idx == cur_ws { 1.0 } else { 0.0 };
            anim.set_target(target, now);
        }
        self.sync_hover_springs();
    }

    fn sync_hover_springs(&mut self) {
        let now = Instant::now();
        for (ws_idx, ws_anims) in self.window_hover_animations.iter_mut().enumerate() {
            for (win_idx, anim) in ws_anims.iter_mut().enumerate() {
                let is_sel = self.selected == SelectionTarget::Window { ws_idx, win_idx };
                let target = if is_sel || self.hovered_window == Some((ws_idx, win_idx)) {
                    1.0
                } else {
                    0.0
                };
                anim.set_target(target, now);
            }
        }
        self.animating = true;
    }

    fn step_by_modifiers(&mut self, shift: bool, ctrl: bool) -> Task<Message> {
        let ws_count = self.snapshot.workspaces.len();
        match (ctrl || ws_count <= 1, shift) {
            (false, false) => self.update(Message::SelectNextWorkspace),
            (false, true) => self.update(Message::SelectPrevWorkspace),
            (true, false) => self.update(Message::SelectNextItem),
            (true, true) => self.update(Message::SelectPrevItem),
        }
    }

    fn step_workspace_vertical(&mut self, down: bool) -> Task<Message> {
        let count = self.snapshot.workspaces.len();
        let total_rows = count.div_ceil(MAX_COLUMNS);

        if total_rows > 1 {
            let cur = self.current_ws_index().min(count.saturating_sub(1));
            let cur_row = cur / MAX_COLUMNS;
            let cur_col = cur % MAX_COLUMNS;
            let cur_row_len = (count - cur_row * MAX_COLUMNS).min(MAX_COLUMNS);
            // Account for horizontal centering of shorter rows (< MAX_COLUMNS)
            let cur_visual_x =
                (cur_col as f32) + ((MAX_COLUMNS - cur_row_len) as f32) * 0.5;

            let target_row = if down {
                (cur_row + 1) % total_rows
            } else if cur_row == 0 {
                total_rows - 1
            } else {
                cur_row - 1
            };

            let target_row_start = target_row * MAX_COLUMNS;
            let target_row_len = (count - target_row_start).min(MAX_COLUMNS);

            let mut best_ws = target_row_start;
            let mut best_dist = f32::MAX;
            for col in 0..target_row_len {
                let cand_x =
                    (col as f32) + ((MAX_COLUMNS - target_row_len) as f32) * 0.5;
                let dist = (cand_x - cur_visual_x).abs();
                if dist < best_dist {
                    best_dist = dist;
                    best_ws = target_row_start + col;
                }
            }

            self.select_workspace_by_index(best_ws);
            self.sync_selection_springs();
            Task::none()
        } else {
            let ws_idx = self.current_ws_index();
            let win_len = self
                .snapshot
                .workspaces
                .get(ws_idx)
                .map_or(0, |ws| ws.clients.len());

            if win_len > 1 {
                let next_win = match self.selected {
                    SelectionTarget::Window { win_idx, .. } => {
                        if down {
                            (win_idx + 1) % win_len
                        } else if win_idx == 0 {
                            win_len - 1
                        } else {
                            win_idx - 1
                        }
                    }
                    SelectionTarget::Workspace(_) => {
                        if down { 0 } else { win_len - 1 }
                    }
                };
                self.selected = SelectionTarget::Window {
                    ws_idx,
                    win_idx: next_win,
                };
                self.sync_selection_springs();
                Task::none()
            } else if down {
                self.update(Message::SelectNextWorkspace)
            } else {
                self.update(Message::SelectPrevWorkspace)
            }
        }
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
            Message::Heartbeat(now) => {
                // 1. Drain pending IPC commands from Hyprland keybinds
                let ipc_commands = if let Ok(mut q) = PENDING_IPC.lock() {
                    std::mem::take(&mut *q)
                } else {
                    Vec::new()
                };

                for cmd in ipc_commands {
                    match cmd {
                        IpcCommand::Activate => {
                            return self.update(Message::ActivateSelected);
                        }
                        IpcCommand::Dismiss => {
                            return self.update(Message::Dismiss);
                        }
                        IpcCommand::SnapPickerMapped => {
                            return self.update(Message::SnapPickerMapped);
                        }
                        IpcCommand::NextWs
                        | IpcCommand::PrevWs
                        | IpcCommand::NextItem
                        | IpcCommand::PrevItem => {
                            let ipc_reverse = matches!(cmd, IpcCommand::PrevWs | IpcCommand::PrevItem);
                            let ipc_detailed = matches!(cmd, IpcCommand::NextItem | IpcCommand::PrevItem);
                            let reverse = ipc_reverse || self.tab_shift;
                            let detailed = ipc_detailed || self.tab_ctrl;

                            let dt_ipc = self
                                .last_ipc_at
                                .map(|t| now.saturating_duration_since(t))
                                .unwrap_or(Duration::from_secs(10));
                            self.last_ipc_at = Some(now);

                            let is_burst = dt_ipc <= IPC_STREAM_THRESHOLD;
                            let dt_step = self
                                .last_step_at
                                .map(|t| now.saturating_duration_since(t))
                                .unwrap_or(Duration::from_secs(10));

                            if is_burst {
                                if dt_step >= HOLD_REPEAT_INTERVAL {
                                    self.last_step_at = Some(now);
                                    let _ = self.step_by_modifiers(reverse, detailed);
                                }
                            } else if dt_step >= Duration::from_millis(90) {
                                self.last_step_at = Some(now);
                                let _ = self.step_by_modifiers(reverse, detailed);
                            }
                        }
                    }
                }

                // 2. In-window Tab hold auto-repeat evaluation
                if self.tab_held
                    && let Some(deadline) = self.next_repeat_deadline
                    && now >= deadline
                {
                    self.next_repeat_deadline = Some(now + HOLD_REPEAT_INTERVAL);
                    self.last_step_at = Some(now);
                    let _ = self.step_by_modifiers(self.tab_shift, self.tab_ctrl);
                }

                Task::none()
            }
            Message::ModifiersChanged(mods) => {
                self.tab_shift = mods.shift();
                self.tab_ctrl = mods.control();
                if mods.alt() {
                    self.alt_held = true;
                    Task::none()
                } else if self.alt_held {
                    self.alt_held = false;
                    self.tab_held = false;
                    self.next_repeat_deadline = None;
                    self.update(Message::ActivateSelected)
                } else {
                    Task::none()
                }
            }
            Message::CtrlKeyChanged(down) => {
                self.tab_ctrl = down;
                Task::none()
            }
            Message::ShiftKeyChanged(down) => {
                self.tab_shift = down;
                Task::none()
            }
            Message::TabPressed { shift, ctrl, alt } => {
                if shift {
                    self.tab_shift = true;
                }
                if ctrl {
                    self.tab_ctrl = true;
                }
                if alt {
                    self.alt_held = true;
                }
                if !self.tab_held {
                    self.tab_held = true;
                    self.next_repeat_deadline = Some(Instant::now() + HOLD_INITIAL_DELAY);
                    self.last_step_at = Some(Instant::now());
                    return self.step_by_modifiers(self.tab_shift, self.tab_ctrl);
                }
                Task::none()
            }
            Message::TabReleased { alt } => {
                if alt {
                    self.alt_held = true;
                }
                self.tab_held = false;
                self.next_repeat_deadline = None;
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
                self.tab_held = false;
                self.next_repeat_deadline = None;
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
                } else if let SelectionTarget::Workspace(ws_idx) = self.selected {
                    if let Some(first_in_ws) = targets.iter().find(|t| {
                        matches!(t, SelectionTarget::Window { ws_idx: w, .. } if *w == ws_idx)
                    }) {
                        self.selected = first_in_ws.clone();
                    } else if let Some(first) = targets.first() {
                        self.selected = first.clone();
                    }
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
                } else if let SelectionTarget::Workspace(ws_idx) = self.selected {
                    if let Some(last_in_ws) = targets.iter().rev().find(|t| {
                        matches!(t, SelectionTarget::Window { ws_idx: w, .. } if *w == ws_idx)
                    }) {
                        self.selected = last_in_ws.clone();
                    } else if let Some(last) = targets.last() {
                        self.selected = last.clone();
                    }
                } else if let Some(first) = targets.first() {
                    self.selected = first.clone();
                }
                self.sync_selection_springs();
                Task::none()
            }
            Message::SelectNextWorkspace => {
                let count = self.snapshot.workspaces.len();
                if count > 1 {
                    let next_ws = (self.current_ws_index() + 1) % count;
                    self.select_workspace_by_index(next_ws);
                    self.sync_selection_springs();
                    Task::none()
                } else {
                    self.update(Message::SelectNextItem)
                }
            }
            Message::SelectPrevWorkspace => {
                let count = self.snapshot.workspaces.len();
                if count > 1 {
                    let cur = self.current_ws_index();
                    let prev_ws = if cur == 0 { count - 1 } else { cur - 1 };
                    self.select_workspace_by_index(prev_ws);
                    self.sync_selection_springs();
                    Task::none()
                } else {
                    self.update(Message::SelectPrevItem)
                }
            }
            Message::SelectNextWindowInWorkspace => self.step_workspace_vertical(true),
            Message::SelectPrevWindowInWorkspace => self.step_workspace_vertical(false),
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
                                    send_ipc_command(IpcCommand::SnapPickerMapped);
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
            Message::Dismiss => {
                self.tab_held = false;
                self.next_repeat_deadline = None;
                iced::exit()
            }
        }
    }

    pub fn subscription(&self) -> Subscription<Message> {
        let keyboard_sub = event::listen_with(|event, _status, _window_id| match event {
            Event::Keyboard(keyboard::Event::KeyPressed {
                key,
                physical_key,
                modifiers,
                ..
            }) => {
                if matches!(key.as_ref(), Key::Named(Named::Control))
                    || matches!(
                        physical_key,
                        Physical::Code(Code::ControlLeft) | Physical::Code(Code::ControlRight)
                    )
                {
                    return Some(Message::CtrlKeyChanged(true));
                }
                if matches!(key.as_ref(), Key::Named(Named::Shift))
                    || matches!(
                        physical_key,
                        Physical::Code(Code::ShiftLeft) | Physical::Code(Code::ShiftRight)
                    )
                {
                    return Some(Message::ShiftKeyChanged(true));
                }

                match key.as_ref() {
                    Key::Named(Named::PrintScreen) => Some(Message::TriggerScreenshot {
                        full_screen: !modifiers.shift(),
                    }),
                    _ if matches!(physical_key, Physical::Code(Code::PrintScreen)) => {
                        Some(Message::TriggerScreenshot {
                            full_screen: !modifiers.shift(),
                        })
                    }
                    Key::Character("s" | "S") if modifiers.logo() => {
                        Some(Message::TriggerScreenshot {
                            full_screen: !modifiers.shift(),
                        })
                    }
                    Key::Named(Named::Escape) => Some(Message::Dismiss),
                    Key::Named(Named::Enter) | Key::Named(Named::Space) => {
                        Some(Message::ActivateSelected)
                    }
                    Key::Named(Named::Tab) => Some(Message::TabPressed {
                        shift: modifiers.shift(),
                        ctrl: modifiers.control(),
                        alt: modifiers.alt(),
                    }),
                    _ if matches!(physical_key, Physical::Code(Code::Tab)) => {
                        Some(Message::TabPressed {
                            shift: modifiers.shift(),
                            ctrl: modifiers.control(),
                            alt: modifiers.alt(),
                        })
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
                }
            }
            Event::Keyboard(keyboard::Event::KeyReleased {
                key,
                physical_key,
                modifiers,
                ..
            }) => {
                if matches!(
                    key.as_ref(),
                    Key::Named(Named::Alt) | Key::Named(Named::AltGraph)
                ) || matches!(
                    physical_key,
                    Physical::Code(Code::AltLeft) | Physical::Code(Code::AltRight)
                ) {
                    Some(Message::ActivateSelected)
                } else if matches!(key.as_ref(), Key::Named(Named::Tab))
                    || matches!(physical_key, Physical::Code(Code::Tab))
                {
                    Some(Message::TabReleased {
                        alt: modifiers.alt(),
                    })
                } else if matches!(key.as_ref(), Key::Named(Named::Control))
                    || matches!(
                        physical_key,
                        Physical::Code(Code::ControlLeft) | Physical::Code(Code::ControlRight)
                    )
                {
                    Some(Message::CtrlKeyChanged(false))
                } else if matches!(key.as_ref(), Key::Named(Named::Shift))
                    || matches!(
                        physical_key,
                        Physical::Code(Code::ShiftLeft) | Physical::Code(Code::ShiftRight)
                    )
                {
                    Some(Message::ShiftKeyChanged(false))
                } else {
                    None
                }
            }
            Event::Keyboard(keyboard::Event::ModifiersChanged(modifiers)) => {
                Some(Message::ModifiersChanged(modifiers))
            }
            _ => None,
        });

        let heartbeat_sub = iced::time::every(HEARTBEAT_INTERVAL).map(Message::Heartbeat);

        if self.animating {
            Subscription::batch([
                keyboard_sub,
                heartbeat_sub,
                iced::window::frames().map(Message::AnimationFrame),
            ])
        } else {
            Subscription::batch([keyboard_sub, heartbeat_sub])
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
            195.0
        } else if ws_count > 3 {
            230.0
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
                        .spacing(28)
                        .align_y(Vertical::Center)
                        .into(),
                );
            }
        }

        if !current_row.is_empty() {
            grid_rows.push(
                Row::with_children(current_row)
                    .spacing(28)
                    .align_y(Vertical::Center)
                    .into(),
            );
        }

        let cards_grid = Column::with_children(grid_rows)
            .spacing(28)
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
                    .size(15)
                    .color(with_alpha(p.muted, 0.85)),
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

                let target_border_color = if is_special {
                    p.tertiary
                } else {
                    p.accent
                };

                let (unselected_bg, selected_bg) = if is_special {
                    (
                        mix_color(p.bg, p.tertiary, 0.08),
                        mix_color(p.bg, p.tertiary, 0.16),
                    )
                } else {
                    (
                        mix_color(p.bg, p.muted, 0.14),
                        mix_color(p.bg, p.accent, 0.16),
                    )
                };
                let progress_bg = lerp_color(unselected_bg, selected_bg, sel_progress);
                let base_bg = if hovered {
                    mix_color(progress_bg, target_border_color, 0.06)
                } else {
                    progress_bg
                };

                let rest_border_color = if is_special {
                    with_alpha(p.tertiary, if is_active { 0.82 } else { 0.60 })
                } else if is_active {
                    with_alpha(p.secondary, 0.78)
                } else {
                    with_alpha(p.muted, 0.55)
                };

                let progress_border_color =
                    lerp_color(rest_border_color, target_border_color, sel_progress);
                let progress_border_width =
                    lerp(if is_active { 1.8 } else { 1.0 }, 2.5, sel_progress);

                let (border_color, border_width) = if hovered {
                    let hover_color = mix_color(
                        lerp_color(progress_border_color, target_border_color, 0.65),
                        Color::WHITE,
                        0.12,
                    );
                    (hover_color, progress_border_width.max(2.2) + 0.2)
                } else {
                    (progress_border_color, progress_border_width)
                };

                let shadow = Shadow {
                    color: with_alpha(
                        target_border_color,
                        (if is_special { 0.36 } else { 0.28 }) * sel_progress * entrance,
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

        // Partition windows deterministically into (1) unselected tiled, (2) unselected floating,
        // and (3) selected windows so floating windows always sit above tiled windows while
        // keeping `Stack` child indices invariant across hover frames.
        let mut tiled_layers: Vec<Element<'a, Message>> = Vec::new();
        let mut floating_layers: Vec<Element<'a, Message>> = Vec::new();
        let mut promoted_layers: Vec<Element<'a, Message>> = Vec::new();
        for (rep_win_idx, group_tabs) in &render_units {
            let client = &ws.clients[*rep_win_idx];
            let is_win_selected = group_tabs.iter().any(|t| t.is_selected);
            let is_unit_floating = group_tabs.iter().any(|t| t.client.floating);
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
            } else if is_unit_floating {
                floating_layers.push(layer);
            } else {
                tiled_layers.push(layer);
            }
        }
        card_layers.extend(tiled_layers);
        card_layers.extend(floating_layers);
        card_layers.extend(promoted_layers);

        // High-contrast workspace indicator badge overlaid in the bottom-right corner.
        let badge_text = if is_active {
            format!("● {}", ws.title)
        } else {
            ws.title.clone()
        };

        let badge_fg = if is_ws_selected { p.bg } else { p.fg };

        let badge_pill = button(text(badge_text).size(13).color(badge_fg))
            .padding([4, 11])
            .on_press(Message::ActivateWorkspace(ws.key.clone()))
            .style(move |_theme: &Theme, status: button::Status| {
                let hovered = matches!(status, button::Status::Hovered | button::Status::Pressed);
                let bg = if is_ws_selected {
                    let base = if is_special { p.tertiary } else { p.accent };
                    if hovered {
                        mix_color(base, Color::WHITE, 0.16)
                    } else {
                        base
                    }
                } else if is_special {
                    let mix_t = if hovered {
                        0.48
                    } else if is_active {
                        0.38
                    } else {
                        0.26
                    };
                    with_alpha(mix_color(p.bg, p.tertiary, mix_t), 0.94)
                } else if is_active {
                    let mix_t = if hovered { 0.48 } else { 0.36 };
                    with_alpha(mix_color(p.bg, p.secondary, mix_t), 0.94)
                } else if hovered {
                    mix_color(p.bg, p.accent, 0.30)
                } else {
                    with_alpha(mix_color(p.bg, p.muted, 0.28), 0.92)
                };

                let border_color = if is_ws_selected {
                    if is_special { p.tertiary } else { p.accent }
                } else if is_special {
                    with_alpha(p.tertiary, if is_active || hovered { 0.92 } else { 0.72 })
                } else if is_active {
                    if hovered {
                        p.accent
                    } else {
                        with_alpha(p.secondary, 0.90)
                    }
                } else if hovered {
                    p.accent
                } else {
                    with_alpha(p.muted, 0.85)
                };

                button::Style {
                    background: Some(Background::Color(bg)),
                    text_color: badge_fg,
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
            .padding([10, 12]);

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
        let is_floating = group_tabs.iter().any(|t| t.client.floating);
        let is_pinned = group_tabs.iter().any(|t| t.client.pinned);
        let is_occluded_under_fs = has_fullscreen_with_others && !is_fullscreen;

        let mon_w = ws.monitor_w.max(1.0);
        let mon_h = ws.monitor_h.max(1.0);

        let inset = 4.5_f32;
        let fs_banner_h = 44.0_f32;
        let sub_top = if is_occluded_under_fs {
            fs_banner_h + inset + 2.0
        } else {
            inset
        };
        let avail_h = (preview_h - sub_top - inset).max(32.0);
        let scale_x = preview_w / mon_w;
        let scale_y = avail_h / mon_h;

        let min_win_w = if is_floating { 56.0 } else { 46.0 };
        let min_win_h = if is_floating { 36.0 } else { 30.0 };

        // 1. Compute static resting geometry (100% independent of hover_progress).
        let (base_x, mut base_y, base_w, base_h) = if is_fullscreen && has_fullscreen_with_others {
            // Dock the fullscreen window as a prominent high-contrast top banner when other windows
            // share the workspace so background tiled/floating windows remain visible below it.
            (
                inset,
                inset,
                (preview_w - inset * 2.0).max(56.0),
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

            bw = bw.clamp(min_win_w, (preview_w - inset * 2.0).max(min_win_w));
            bh = bh.clamp(min_win_h, avail_h.max(min_win_h));

            if bx + bw > preview_w - inset {
                bx = (preview_w - inset - bw).max(inset);
            }
            if by + bh > preview_h - inset {
                by = (preview_h - inset - bh).max(sub_top);
            }
            (bx, by, bw, bh)
        };

        // Nudge small floating windows out of the bottom-right workspace badge zone.
        if is_floating
            && !is_fullscreen
            && base_w <= 116.0
            && base_h <= 56.0
            && base_x + base_w > preview_w - 64.0
            && base_y + base_h > preview_h - 40.0
        {
            base_y = (preview_h - 40.0 - base_h).max(sub_top);
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
            .clamp(min_win_w, (slot_w - inner_x).max(min_win_w));
        let h = lerp(base_h, slot_h, hover_t)
            .round()
            .clamp(min_win_h, (slot_h - inner_y).max(min_win_h));

        let resolved_icon = &self.window_icons[ws_idx][rep_win_idx];
        let raw_class = if !client.class.is_empty() {
            client.class.as_str()
        } else if !client.initial_class.is_empty() {
            client.initial_class.as_str()
        } else {
            "Window"
        };
        let display_class = format_display_class(raw_class, resolved_icon.display_badge.as_deref());
        let clean_subtitle = clean_window_subtitle(
            &client.title,
            raw_class,
            resolved_icon.display_badge.as_deref(),
        );

        let accent = resolved_icon.resolve_color(&p);
        let has_custom_color = resolved_icon.accent_slot.is_custom();
        let active_tint = accent;
        let title_fg = mix_color(p.fg, Color::WHITE, 0.18);

        // Anchor `tab_bar_h` to static `base_h` so it never jumps across height thresholds mid-hover.
        let tab_bar_h = if is_grouped {
            if base_h >= 54.0 {
                24.0
            } else {
                (base_h * 0.44).round().clamp(16.0, 20.0)
            }
        } else {
            0.0
        };
        let body_h = if is_grouped {
            (h - tab_bar_h - 2.0).max(16.0)
        } else {
            h
        };

        let active_tab_pos = group_tabs
            .iter()
            .position(|t| t.win_idx == rep_win_idx)
            .map(|idx| idx + 1)
            .unwrap_or(1);
        let tab_count = group_tabs.len();

        let icon_fg = mix_color(
            accent,
            p.fg,
            if has_custom_color { 0.14 } else { 0.24 },
        );

        let label_content: Element<'a, Message> = if is_fullscreen && has_fullscreen_with_others {
            // Tier 4: Docked Fullscreen Banner
            let banner_badge = if is_grouped {
                format!("⛶ FULLSCREEN · {active_tab_pos}/{tab_count}")
            } else if is_selected || is_hypr_focused {
                String::from("● ⛶ FULLSCREEN")
            } else {
                String::from("⛶ FULLSCREEN")
            };
            let banner_pill_bg = if is_selected || has_custom_color {
                active_tint
            } else {
                mix_color(active_tint, p.secondary, 0.35)
            };
            let banner_pill_fg = contrast_text_color(banner_pill_bg, p.bg, p.fg);
            let banner_class = truncate_str(
                &display_class,
                ((base_w * 0.36) / 8.2).max(6.0) as usize,
            );
            let banner_sub = clean_subtitle
                .map(|s| truncate_str(s, ((base_w * 0.44) / 7.5).max(8.0) as usize))
                .unwrap_or_default();
            let sub_fg = with_alpha(mix_color(p.fg, Color::WHITE, 0.12), 0.95);

            row![
                container(text(banner_badge).size(10.5).color(banner_pill_fg))
                    .padding([3, 7])
                    .style(move |_theme: &Theme| container::Style {
                        background: Some(Background::Color(banner_pill_bg)),
                        text_color: Some(banner_pill_fg),
                        border: Border {
                            color: Color::TRANSPARENT,
                            width: 0.0,
                            radius: 5.0.into(),
                        },
                        shadow: Shadow::default(),
                        ..Default::default()
                    }),
                Space::new().width(Length::Fixed(8.0)),
                render_app_icon(&resolved_icon.visual, 16.0, icon_fg),
                Space::new().width(Length::Fixed(6.0)),
                text(banner_class)
                    .size(15)
                    .font(bold_font())
                    .color(title_fg),
                Space::new().width(Length::Fixed(8.0)),
                text(banner_sub)
                    .size(13.5)
                    .font(semibold_font())
                    .color(sub_fg),
                Space::new().width(Length::Fill),
            ]
            .padding([0, 8])
            .align_y(Vertical::Center)
            .height(Length::Fill)
            .into()
        } else {
            // Standalone / Grouped Window Interior Canvas (No redundant top titlebar)
            let sub_fg = with_alpha(mix_color(p.fg, Color::WHITE, 0.12), 0.95);

            let main_canvas: Element<'a, Message> = if body_h >= 76.0 && w >= 96.0 {
                // Tier 1: Hero Vertical Stack Canvas (Compact fixed icon badge + Bold title + Framed Subtitle)
                let is_large_hero = w >= 170.0 && body_h >= 105.0;
                let primary_font_size = if is_large_hero { 20.5 } else { 19.0 };
                let sub_font_size = if is_large_hero { 16.0 } else { 15.0 };
                let (badge_box_sz, icon_sz, badge_radius) = if is_large_hero {
                    (46.0, 29.0, 12.0)
                } else {
                    (42.0, 26.0, 10.5)
                };

                let primary_chars =
                    ((base_w - 20.0) / (primary_font_size * 0.52)).max(5.0) as usize;
                let sub_chars = ((base_w - 26.0) / (sub_font_size * 0.54)).max(6.0) as usize;
                let primary_str = truncate_str(&display_class, primary_chars);

                let hero_icon_col = with_alpha(icon_fg, lerp(0.90, 1.0, hover_t));
                let icon_badge = container(render_app_icon(
                    &resolved_icon.visual,
                    icon_sz,
                    hero_icon_col,
                ))
                .width(Length::Fixed(badge_box_sz))
                .height(Length::Fixed(badge_box_sz))
                .align_x(Horizontal::Center)
                .align_y(Vertical::Center)
                .style(move |_theme: &Theme| container::Style {
                    background: Some(Background::Color(with_alpha(
                        mix_color(p.bg, accent, if is_selected { 0.34 } else { 0.22 }),
                        0.94,
                    ))),
                    text_color: None,
                    border: Border {
                        color: with_alpha(accent, if is_selected { 0.62 } else { 0.36 }),
                        width: 1.0,
                        radius: badge_radius.into(),
                    },
                    shadow: Shadow::default(),
                    ..Default::default()
                });

                let mut col_items: Vec<Element<'a, Message>> = Vec::with_capacity(5);
                col_items.push(icon_badge.into());
                col_items.push(Space::new().height(Length::Fixed(6.0)).into());
                col_items.push(
                    text(primary_str)
                        .size(primary_font_size)
                        .font(bold_font())
                        .color(title_fg)
                        .into(),
                );

                if let Some(sub) = clean_subtitle {
                    let sub_str = truncate_str(sub, sub_chars);
                    let sub_pill = container(
                        text(sub_str)
                            .size(sub_font_size)
                            .font(semibold_font())
                            .color(sub_fg),
                    )
                    .padding([3, 10])
                    .style(move |_theme: &Theme| container::Style {
                        background: Some(Background::Color(with_alpha(
                            mix_color(p.bg, accent, if is_selected { 0.24 } else { 0.15 }),
                            0.90,
                        ))),
                        text_color: None,
                        border: Border {
                            color: with_alpha(accent, if is_selected { 0.42 } else { 0.24 }),
                            width: 1.0,
                            radius: 6.5.into(),
                        },
                        shadow: Shadow::default(),
                        ..Default::default()
                    });

                    col_items.push(Space::new().height(Length::Fixed(4.0)).into());
                    col_items.push(sub_pill.into());
                }

                container(
                    Column::with_children(col_items)
                        .width(Length::Shrink)
                        .align_x(Horizontal::Center),
                )
                .width(Length::Fill)
                .height(Length::Fill)
                .padding([6, 8])
                .center_x(Length::Fill)
                .center_y(Length::Fill)
                .into()
            } else if body_h >= 44.0 {
                // Tier 2: Balanced Horizontal Icon + Two-Line Label Canvas
                let avail_text_w = (base_w - 58.0).max(28.0);
                let primary_chars = (avail_text_w / 8.6).max(4.0) as usize;
                let sub_chars = (avail_text_w / 7.5).max(5.0) as usize;
                let primary_str = truncate_str(&display_class, primary_chars);

                let icon_box = container(render_app_icon(&resolved_icon.visual, 22.0, icon_fg))
                    .width(Length::Fixed(34.0))
                    .height(Length::Fixed(34.0))
                    .align_x(Horizontal::Center)
                    .align_y(Vertical::Center)
                    .style(move |_theme: &Theme| container::Style {
                        background: Some(Background::Color(with_alpha(
                            mix_color(p.bg, accent, if is_selected { 0.32 } else { 0.20 }),
                            0.92,
                        ))),
                        text_color: None,
                        border: Border {
                            color: with_alpha(accent, if is_selected { 0.55 } else { 0.32 }),
                            width: 1.0,
                            radius: 8.5.into(),
                        },
                        shadow: Shadow::default(),
                        ..Default::default()
                    });

                let text_col: Element<'a, Message> = if let Some(sub) = clean_subtitle {
                    let sub_str = truncate_str(sub, sub_chars);
                    column![
                        text(primary_str)
                            .size(16.5)
                            .font(bold_font())
                            .color(title_fg),
                        Space::new().height(Length::Fixed(2.5)),
                        text(sub_str)
                            .size(14)
                            .font(semibold_font())
                            .color(sub_fg),
                    ]
                    .align_x(Horizontal::Left)
                    .into()
                } else {
                    text(primary_str)
                        .size(16.5)
                        .font(bold_font())
                        .color(title_fg)
                        .into()
                };

                container(
                    row![
                        icon_box,
                        Space::new().width(Length::Fixed(9.0)),
                        text_col,
                    ]
                    .align_y(Vertical::Center),
                )
                .width(Length::Fill)
                .height(Length::Fill)
                .padding([4, 8])
                .center_x(Length::Fill)
                .center_y(Length::Fill)
                .into()
            } else {
                // Tier 3: Compact Strip Canvas
                let primary_chars = ((base_w - 30.0) / 8.0).max(4.0) as usize;
                let primary_str = truncate_str(&display_class, primary_chars);

                let mut strip_row: Vec<Element<'a, Message>> = Vec::with_capacity(5);
                strip_row.push(render_app_icon(&resolved_icon.visual, 16.0, icon_fg));
                strip_row.push(Space::new().width(Length::Fixed(5.5)).into());
                strip_row.push(
                    text(primary_str)
                        .size(14.5)
                        .font(bold_font())
                        .color(title_fg)
                        .into(),
                );

                if w >= 160.0
                    && let Some(sub) = clean_subtitle
                {
                    let sub_chars = ((base_w - 108.0) / 7.4).max(5.0) as usize;
                    let sub_str = truncate_str(sub, sub_chars);
                    strip_row.push(Space::new().width(Length::Fixed(6.0)).into());
                    strip_row.push(
                        text(sub_str)
                            .size(13)
                            .font(semibold_font())
                            .color(sub_fg)
                            .into(),
                    );
                }


                container(Row::with_children(strip_row).align_y(Vertical::Center))
                    .width(Length::Fill)
                    .height(Length::Fill)
                    .padding([2, 6])
                    .center_x(Length::Fill)
                    .center_y(Length::Fill)
                    .into()
            };

            // Optional Top-Right Corner Status / Floating Pill Overlay
            let corner_badge_text: Option<String> = if is_fullscreen {
                if is_grouped {
                    Some(format!("⛶ FULL · {active_tab_pos}/{tab_count}"))
                } else if is_selected || is_hypr_focused {
                    Some(String::from("● ⛶ FULL"))
                } else {
                    Some(String::from("⛶ FULL"))
                }
            } else if is_floating {
                let flt_tag = if is_pinned { "📌 PIN" } else { "❐ FLOAT" };
                if is_grouped {
                    Some(format!("{flt_tag} · {active_tab_pos}/{tab_count}"))
                } else if is_selected || is_hypr_focused {
                    Some(format!("● {flt_tag}"))
                } else {
                    Some(String::from(flt_tag))
                }
            } else if is_grouped && body_h >= 48.0 {
                Some(format!("⇥ {active_tab_pos}/{tab_count}"))
            } else if is_selected || is_hypr_focused {
                Some(String::from("●"))
            } else {
                None
            };

            if let Some(pill_label) = corner_badge_text
                && body_h >= 34.0
                && w >= 64.0
            {
                let pill_tint = if is_floating {
                    if has_custom_color { accent } else { p.tertiary }
                } else {
                    active_tint
                };
                let pill_bg = with_alpha(
                    mix_color(p.bg, pill_tint, if is_selected || is_floating { 0.42 } else { 0.28 }),
                    0.94,
                );
                let pill_fg = mix_color(pill_tint, p.fg, 0.55);
                let pill_border = with_alpha(pill_tint, if is_selected || is_floating { 0.82 } else { 0.55 });

                let corner_pill = container(text(pill_label).size(10).color(pill_fg))
                    .padding([2, 6])
                    .style(move |_theme: &Theme| container::Style {
                        background: Some(Background::Color(pill_bg)),
                        text_color: Some(pill_fg),
                        border: Border {
                            color: pill_border,
                            width: 1.0,
                            radius: 6.0.into(),
                        },
                        shadow: Shadow::default(),
                        ..Default::default()
                    });

                let badge_layer = container(corner_pill)
                    .width(Length::Fill)
                    .height(Length::Fill)
                    .align_x(Horizontal::Right)
                    .align_y(Vertical::Top)
                    .padding([5, 6]);

                stack![main_canvas, badge_layer]
                    .width(Length::Fill)
                    .height(Length::Fill)
                    .into()
            } else {
                main_canvas
            }
        };

        let centered_box = container(label_content)
            .width(Length::Fill)
            .height(Length::Fill)
            .clip(true);

        let body_radius = if is_grouped {
            7.5
        } else if is_floating {
            10.5
        } else {
            8.5
        };

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

                let floating_tint = if has_custom_color { accent } else { p.tertiary };
                let rest_bg = if is_selected {
                    mix_color(p.bg, active_tint, 0.25)
                } else if is_floating {
                    mix_color(p.bg, floating_tint, 0.20)
                } else if has_custom_color {
                    mix_color(p.bg, accent, 0.14)
                } else {
                    mix_color(p.bg, mix_color(p.muted, active_tint, 0.18), 0.25)
                };
                let hover_bg = mix_color(
                    p.bg,
                    active_tint,
                    if is_selected && hovered { 0.36 } else { 0.30 },
                );
                let bg = lerp_color(rest_bg, hover_bg, eff_hover);

                let base_alpha = if is_floating {
                    lerp(0.97, 1.0, eff_hover)
                } else if is_occluded_under_fs && !is_selected {
                    lerp(0.78, 0.97, eff_hover)
                } else {
                    lerp(0.94, 0.99, eff_hover)
                };

                let rest_border_col = if is_selected {
                    mix_color(active_tint, Color::WHITE, 0.16)
                } else if is_floating {
                    with_alpha(
                        mix_color(p.secondary, floating_tint, 0.55),
                        if is_hypr_focused { 0.94 } else { 0.86 },
                    )
                } else if has_custom_color {
                    let muted_custom = mix_color(
                        p.muted,
                        accent,
                        if is_hypr_focused { 0.78 } else { 0.62 },
                    );
                    with_alpha(muted_custom, if is_hypr_focused { 0.90 } else { 0.72 })
                } else if is_hypr_focused {
                    with_alpha(mix_color(p.secondary, active_tint, 0.55), 0.88)
                } else if is_fullscreen {
                    with_alpha(mix_color(p.secondary, active_tint, 0.45), 0.74)
                } else {
                    with_alpha(
                        mix_color(p.muted, active_tint, 0.28),
                        if is_occluded_under_fs { 0.45 } else { 0.72 },
                    )
                };
                let hover_border_col = mix_color(
                    active_tint,
                    Color::WHITE,
                    if is_selected && hovered {
                        0.42
                    } else if is_selected {
                        0.32
                    } else {
                        0.24
                    },
                );
                let border_color = lerp_color(rest_border_col, hover_border_col, eff_hover);
                let rest_border_w = if is_selected {
                    2.4
                } else if is_floating {
                    1.6
                } else if is_hypr_focused {
                    1.5
                } else if is_fullscreen {
                    1.4
                } else {
                    1.0
                };
                let target_border_w = if is_selected && hovered {
                    3.0
                } else if is_selected {
                    2.8
                } else {
                    2.2
                };
                let border_width = lerp(rest_border_w, target_border_w, eff_hover);

                let shadow = if is_floating && !is_selected && eff_hover <= 0.05 {
                    Shadow {
                        color: with_alpha(Color::BLACK, 0.48),
                        offset: Vector::new(0.0, 4.0),
                        blur_radius: 12.0,
                    }
                } else {
                    let glow_alpha = 0.40 * eff_hover
                        + if is_selected { 0.28 } else { 0.0 }
                        + if is_floating { 0.14 } else { 0.0 };
                    Shadow {
                        color: with_alpha(active_tint, glow_alpha.clamp(0.0, 0.68)),
                        offset: Vector::new(
                            0.0,
                            4.5 * eff_hover
                                + if is_selected { 2.0 } else { 0.0 }
                                + if is_floating { 1.5 } else { 0.0 },
                        ),
                        blur_radius: 14.0 * eff_hover
                            + if is_selected { 6.0 } else { 0.0 }
                            + if is_floating { 4.0 } else { 0.0 },
                    }
                };

                button::Style {
                    background: Some(Background::Color(with_alpha(bg, base_alpha))),
                    text_color: p.fg,
                    border: Border {
                        color: border_color,
                        width: border_width,
                        radius: body_radius.into(),
                    },
                    shadow,
                    ..Default::default()
                }
            });

        let visual_unit: Element<'a, Message> = if is_grouped {
            let per_tab_chars =
                (((base_w / (tab_count as f32)) - 22.0) / 7.2).max(3.0) as usize;
            let mut tab_pills: Vec<Element<'a, Message>> = Vec::with_capacity(tab_count);

            for tab in group_tabs {
                let tab_resolved = &self.window_icons[ws_idx][tab.win_idx];
                let tab_class = if !tab.client.class.is_empty() {
                    tab.client.class.as_str()
                } else {
                    "win"
                };
                let tab_accent = tab_resolved.resolve_color(&p);
                let tab_has_custom_color = tab_resolved.accent_slot.is_custom();
                let tab_active_tint = tab_accent;

                let short_lead = tab_resolved
                    .display_badge
                    .as_deref()
                    .unwrap_or(tab_class);
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
                            render_app_icon(&tab_resolved.visual, 12.5, tab_icon_col),
                            Space::new().width(Length::Fixed(4.0)),
                            text(full_label).size(11),
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
                .padding([2, 5])
                .on_press(Message::ActivateWindow {
                    workspace: ws.key.clone(),
                    address: tab.client.address.clone(),
                    floating: tab.client.floating,
                })
                .style(move |_theme: &Theme, status: button::Status| {
                    let hovered =
                        matches!(status, button::Status::Hovered | button::Status::Pressed);
                    let bg = if is_tab_sel {
                        mix_color(p.bg, tab_active_tint, if hovered { 0.58 } else { 0.48 })
                    } else if is_tab_active {
                        let base = if tab_has_custom_color {
                            tab_accent
                        } else {
                            p.secondary
                        };
                        mix_color(p.bg, base, if hovered { 0.40 } else { 0.28 })
                    } else if hovered {
                        mix_color(p.bg, tab_active_tint, 0.32)
                    } else {
                        let base = if tab_has_custom_color {
                            mix_color(p.muted, tab_accent, 0.55)
                        } else {
                            p.muted
                        };
                        with_alpha(
                            mix_color(p.bg, base, lerp(0.22, 0.30, hover_t)),
                            lerp(0.88, 0.96, hover_t),
                        )
                    };

                    let border_col = if is_tab_sel {
                        mix_color(tab_active_tint, Color::WHITE, if hovered { 0.38 } else { 0.25 })
                    } else if hovered {
                        tab_active_tint
                    } else if is_tab_active {
                        let base = if tab_has_custom_color {
                            tab_accent
                        } else {
                            p.secondary
                        };
                        with_alpha(base, 0.85)
                    } else {
                        let base = if tab_has_custom_color {
                            mix_color(p.muted, tab_accent, 0.55)
                        } else {
                            p.muted
                        };
                        with_alpha(base, lerp(0.60, 0.80, hover_t))
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
                            width: if is_tab_sel || hovered {
                                1.5
                            } else if is_tab_active {
                                1.2
                            } else {
                                1.0
                            },
                            radius: 6.0.into(),
                        },
                        shadow: if is_tab_sel || hovered {
                            Shadow {
                                color: with_alpha(tab_active_tint, 0.28),
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

        let hover_win_idx = group_tabs.first().map_or(rep_win_idx, |tab| tab.win_idx);
        let unified_hover_zone: Element<'a, Message> = mouse_area(fixed_hit_slot)
            .on_enter(Message::WindowHoverEntered {
                ws_idx,
                win_idx: hover_win_idx,
            })
            .on_exit(Message::WindowHoverExited {
                ws_idx,
                win_idx: hover_win_idx,
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

#[inline]
fn bold_font() -> iced::Font {
    iced::Font {
        weight: iced::font::Weight::Bold,
        ..iced::Font::with_name(crate::fonts::DEFAULT_FAMILY)
    }
}

#[inline]
fn semibold_font() -> iced::Font {
    iced::Font {
        weight: iced::font::Weight::Semibold,
        ..iced::Font::with_name(crate::fonts::DEFAULT_FAMILY)
    }
}

#[inline]
fn medium_font() -> iced::Font {
    iced::Font {
        weight: iced::font::Weight::Medium,
        ..iced::Font::with_name(crate::fonts::DEFAULT_FAMILY)
    }
}

fn format_display_class(raw_class: &str, badge: Option<&str>) -> String {
    let base = raw_class
        .trim()
        .rsplit('.')
        .next()
        .unwrap_or(raw_class)
        .trim();
    let mut chars = base.chars();
    let pretty_base = match chars.next() {
        Some(first) if first.is_ascii_lowercase() => {
            let mut s = String::with_capacity(base.len());
            s.push(first.to_ascii_uppercase());
            s.push_str(chars.as_str());
            s
        }
        _ => base.to_owned(),
    };

    match badge {
        Some(b) if !b.is_empty() && !pretty_base.eq_ignore_ascii_case(b) => {
            format!("{pretty_base} · {b}")
        }
        _ => pretty_base,
    }
}

fn clean_window_subtitle<'a>(
    title: &'a str,
    raw_class: &str,
    badge: Option<&str>,
) -> Option<&'a str> {
    let mut trimmed = title.trim();
    for suffix in [
        " — Mozilla Firefox",
        " - Mozilla Firefox",
        " — Firefox",
        " - Firefox",
        " — Zen Browser",
        " - Zen Browser",
        " - Google Chrome",
        " - Chromium",
        " - Brave",
        " - Vivaldi",
        " - Visual Studio Code",
        " - VSCodium",
    ] {
        if let Some(stripped) = trimmed.strip_suffix(suffix) {
            let candidate = stripped.trim();
            if !candidate.is_empty() {
                trimmed = candidate;
            }
            break;
        }
    }

    if trimmed.is_empty() || matches!(trimmed, "~" | "-" | "zsh" | "bash" | "fish") {
        return None;
    }
    let short_class = raw_class.trim().rsplit('.').next().unwrap_or(raw_class);
    if trimmed.eq_ignore_ascii_case(raw_class.trim()) || trimmed.eq_ignore_ascii_case(short_class) {
        return None;
    }
    if let Some(b) = badge
        && trimmed.eq_ignore_ascii_case(b.trim())
    {
        return None;
    }
    Some(trimmed)
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

fn render_app_icon<'a>(visual: &'a IconVisual, size: f32, color: Color) -> Element<'a, Message> {
    match visual {
        IconVisual::Svg { handle, tint } => {
            let icon_color = (*tint).then_some(color);
            svg(handle.clone())
                .width(Length::Fixed(size))
                .height(Length::Fixed(size))
                .style(move |_theme: &Theme, _status: svg::Status| svg::Style {
                    color: icon_color,
                })
                .into()
        }
        IconVisual::Glyph(glyph) => container(
            text(glyph.as_str())
                .size((size * 0.95).round().max(9.0))
                .line_height(1.0)
                .color(color),
        )
        .width(Length::Fixed(size))
        .height(Length::Fixed(size))
        .center_x(Length::Fill)
        .center_y(Length::Fill)
        .into(),
    }
}
