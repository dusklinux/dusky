//! Iced Wayland tray: layout mirrors the original compact panel.
//!
//! Sections (top → bottom): header (weather / clock / power), metrics,
//! quick-toggle grid (5), power row (wifi+bt switches + TLP radios),
//! sliders (volume / brightness / sunset), notifications.

use chrono::{Datelike, Days, Months, NaiveDate};
use iced_core::alignment::{Horizontal, Vertical};
use iced_core::font::Weight;
use iced_core::keyboard::Key;
use iced_core::keyboard::key::Named;
use iced_core::mouse::ScrollDelta;
use iced_core::{Border, Color, Event, Font, Length};
use iced_futures::Subscription;
use iced_runtime::Task;
use iced_widget::{
    Space, button, column, container, mouse_area, row, scrollable, slider, stack, text, toggler,
    tooltip,
};

use std::collections::{HashMap, HashSet};
use std::time::{Duration, Instant};

use crate::backend::cmd::execute_detached;
use crate::backend::notify::{self, Notification};
use crate::backend::system as sys;
use crate::backend::worker;
use crate::config::{AppConfig, Appearance, icon_glyph};
use crate::theme::{AppTheme, saturate, slider_icon_color, slider_label_color};

type Element<'a, Message> =
    iced_core::Element<'a, Message, iced_core::Theme, crate::renderer::Renderer>;

// ---------------------------------------------------------------------------
// messages
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub enum Message {
    Tick,
    PanelSize(iced_core::Size),
    PanelFitted(iced_core::Size, bool),
    WindowEvent(iced_core::window::Id, iced_core::window::Event),
    BlurConfigured,
    GlobalBlurLoaded(Option<bool>),
    WeatherLoaded(Option<sys::WeatherState>),
    MetricsLoaded(
        (
            sys::CpuState,
            sys::RamState,
            sys::NetState,
            sys::BatteryState,
        ),
    ),
    TogglesLoaded(u64, ToggleSnapshot),
    RadioLoaded(u64, u64, (Option<bool>, Option<bool>)),
    PowerLoaded(u64, Option<String>),
    VolumeLoaded(u64, Option<(f32, bool)>),
    SliderLoaded(SliderKind, u64, Option<f32>),
    NotifsLoaded(u64, (Vec<Notification>, Option<bool>)),
    NotifsRefreshed(u64, (Vec<Notification>, Option<bool>)),
    SliderChanged(SliderKind, f32),
    WifiToggled(bool),
    WifiSettled(Option<bool>),
    BtToggled(bool),
    BtSettled(Option<bool>),
    PowerSelected(String),
    PowerSettled((String, bool)),
    Powertop,
    PowertopDone,
    ToggleLeft(String),
    ToggleRight(String),
    ToggleMiddle(String),
    SliderApplied(SliderKind),
    PowerButton,
    ClockPressed,
    CalendarScrolled(ScrollDelta),
    CalendarHoverExited,
    WeatherPressed,
    WifiManager,
    BluetoothManager,
    BluetoothRecovery,
    VolumeControl,
    OutputSelector,
    MetricPressed(String),
    MetricRightPressed(String),
    DndToggled,
    DndSettled(Option<bool>),
    ClearNotifs,
    NotifsCleared,
    NotifDismiss(i32),
    NotifInvoke(i32),
    NotifInvoked(bool),
    NotifActionDone,
    StackToggled(String),
    StackClosed(String),
    NotificationHovered(String),
    NotificationLeft(String),
    BackdropPressed,
    EventOccurred(Event),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum SliderKind {
    Volume,
    Brightness,
    Sunset,
}

#[derive(Debug, Clone, Default)]
pub struct ToggleSnapshot {
    pub animations_on: Option<bool>,
    pub wifi_connected: Option<bool>,
    pub idle_inhibited: Option<bool>,
    pub blur_on: Option<bool>,
    pub audio_active: bool,
    pub updates_badge: String,
    pub updates_tooltip: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
enum RefreshKind {
    GlobalBlur,
    Weather,
    Metrics,
    Toggles,
    Radios,
    Power,
    Slider(SliderKind),
    Notifications,
}

impl RefreshKind {
    const ALL: [Self; 10] = [
        Self::GlobalBlur,
        Self::Weather,
        Self::Metrics,
        Self::Toggles,
        Self::Radios,
        Self::Power,
        Self::Slider(SliderKind::Volume),
        Self::Slider(SliderKind::Sunset),
        Self::Notifications,
        // DDC discovery can take seconds. Submit it after the quick controls.
        Self::Slider(SliderKind::Brightness),
    ];

    fn phase(self) -> &'static str {
        match self {
            Self::GlobalBlur => "global-blur-ready",
            Self::Weather => "weather-ready",
            Self::Metrics => "metrics-ready",
            Self::Toggles => "toggles-ready",
            Self::Radios => "radios-ready",
            Self::Power => "power-ready",
            Self::Slider(SliderKind::Volume) => "volume-ready",
            Self::Slider(SliderKind::Brightness) => "brightness-ready",
            Self::Slider(SliderKind::Sunset) => "sunset-ready",
            Self::Notifications => "notifications-ready",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct CalendarCell {
    day: u32,
    in_month: bool,
    is_today: bool,
    is_weekend: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct CalendarMonth {
    title: String,
    badge: String,
    is_offset: bool,
    cells: [CalendarCell; 42],
}

// ---------------------------------------------------------------------------
// app state
// ---------------------------------------------------------------------------

pub struct TrayApp {
    config: AppConfig,
    appearance: Appearance,
    global_blur_enabled: bool,
    theme: AppTheme,
    time_text: String,
    date_text: String,
    today: NaiveDate,
    calendar_month_offset: i32,
    calendar_scroll_accum: f32,
    weather: Option<sys::WeatherState>,
    cpu: sys::CpuState,
    ram: sys::RamState,
    net: sys::NetState,
    battery: sys::BatteryState,
    show_battery_metric: Option<bool>,
    toggles: ToggleSnapshot,
    toggle_revision: u64,
    wifi_on: Option<bool>,
    bt_on: Option<bool>,
    wifi_pending: bool,
    bt_pending: bool,
    wifi_revision: u64,
    bt_revision: u64,
    power_profile: Option<String>,
    power_pending: Option<String>,
    power_revision: u64,
    powertop_running: bool,
    volume: Option<f32>,
    volume_muted: bool,
    brightness: Option<f32>,
    sunset: Option<f32>,
    sunset_available: bool,
    notifications: Vec<Notification>,
    dismissed: HashSet<i32>,
    dnd: Option<bool>,
    dnd_pending: bool,
    dnd_revision: u64,
    expanded: HashSet<String>,
    hovered_notification: Option<String>,
    refresh_pending: HashSet<RefreshKind>,
    initial_pending: HashSet<RefreshKind>,
    initial_refresh: bool,
    notifications_loaded: bool,
    slider_busy: HashSet<SliderKind>,
    slider_pending: HashMap<SliderKind, f32>,
    slider_changed_at: HashMap<SliderKind, Instant>,
    slider_revision: HashMap<SliderKind, u64>,
    error: Option<String>,
    pub(crate) panel_monitor: Option<sys::PanelMonitor>,
    window_id: Option<iced_core::window::Id>,
    panel_size: Option<iced_core::Size>,
    panel_applied_size: Option<iced_core::Size>,
    panel_fit_busy: bool,
}

impl TrayApp {
    pub fn new(config: AppConfig) -> (Self, Task<Message>) {
        let mut app = Self::state(config);
        // Iced runs boot task streams after opening the window. Start read-only
        // queries eagerly so hardware waits overlap compositor/font startup.
        let refresh = app.refresh_all();
        app.initial_pending = app.refresh_pending.clone();
        if app.initial_pending.is_empty() {
            app.initial_refresh = false;
            crate::renderer::trace_phase("snapshot-ready");
        }
        (app, refresh)
    }

    fn state(config: AppConfig) -> Self {
        let (time_text, date_text, today) = sys::current_time_date();
        Self {
            config,
            appearance: Appearance::load().unwrap_or_default(),
            // Start opaque until the compositor answers; a failed refresh
            // retains the last observed state.
            global_blur_enabled: false,
            theme: AppTheme::load(),
            time_text,
            date_text,
            today,
            calendar_month_offset: 0,
            calendar_scroll_accum: 0.0,
            weather: None,
            cpu: Default::default(),
            ram: Default::default(),
            net: Default::default(),
            battery: Default::default(),
            show_battery_metric: None,
            toggles: Default::default(),
            toggle_revision: 0,
            wifi_on: None,
            bt_on: None,
            wifi_pending: false,
            bt_pending: false,
            wifi_revision: 0,
            bt_revision: 0,
            power_profile: None,
            power_pending: None,
            power_revision: 0,
            powertop_running: false,
            volume: None,
            volume_muted: false,
            brightness: None,
            sunset: None,
            sunset_available: false,
            notifications: Vec::new(),
            dismissed: HashSet::new(),
            dnd: None,
            dnd_pending: false,
            dnd_revision: 0,
            expanded: HashSet::new(),
            hovered_notification: None,
            refresh_pending: HashSet::new(),
            initial_pending: HashSet::new(),
            initial_refresh: true,
            notifications_loaded: false,
            slider_busy: HashSet::new(),
            slider_pending: HashMap::new(),
            slider_changed_at: HashMap::new(),
            slider_revision: HashMap::new(),
            error: None,
            panel_monitor: None,
            window_id: None,
            panel_size: None,
            panel_applied_size: None,
            panel_fit_busy: false,
        }
    }

    fn reserve_refresh(&mut self) -> Vec<RefreshKind> {
        let layout = &self.config.layout;
        RefreshKind::ALL
            .into_iter()
            .filter(|kind| {
                let enabled = match kind {
                    RefreshKind::GlobalBlur => true,
                    RefreshKind::Weather => layout.show_weather,
                    RefreshKind::Metrics => layout.show_metrics,
                    RefreshKind::Toggles => {
                        layout.show_quick_toggles && !self.config.toggles.is_empty()
                    }
                    RefreshKind::Radios | RefreshKind::Power => layout.show_power_profiles,
                    RefreshKind::Slider(_) => layout.show_sliders,
                    RefreshKind::Notifications => layout.show_notifications,
                };
                enabled && self.refresh_pending.insert(*kind)
            })
            .collect()
    }

    fn refresh_all(&mut self) -> Task<Message> {
        let kinds = self.reserve_refresh();
        Task::batch(kinds.into_iter().map(|kind| self.refresh_task(kind)))
    }

    fn refresh_task(&self, kind: RefreshKind) -> Task<Message> {
        match kind {
            RefreshKind::GlobalBlur => {
                worker::start(sys::global_blur_enabled).map(Message::GlobalBlurLoaded)
            }
            RefreshKind::Weather => worker::start(sys::weather_state).map(Message::WeatherLoaded),
            RefreshKind::Metrics => worker::start(|| {
                let (cpu, ram) = sys::cpu_ram_state();
                let net = sys::net_state();
                let battery = sys::battery_state();
                (cpu, ram, net, battery)
            })
            .map(Message::MetricsLoaded),
            RefreshKind::Toggles => {
                let revision = self.toggle_revision;
                let has = |id: &str| self.config.toggles.iter().any(|t| t.id == id);
                let (animations, wifi, idle, blur, audio, updates) = (
                    has("animations"),
                    has("wifi"),
                    has("idle"),
                    has("blur"),
                    has("audio"),
                    has("updates"),
                );
                worker::start(move || {
                    let updates = if updates {
                        sys::updates_state()
                    } else {
                        Default::default()
                    };
                    ToggleSnapshot {
                        animations_on: animations.then(sys::animations_enabled).flatten(),
                        wifi_connected: wifi.then(sys::wifi_connected).flatten(),
                        idle_inhibited: idle
                            .then(sys::is_idle_active)
                            .flatten()
                            .map(|active| !active),
                        blur_on: blur.then(sys::is_blur_on).flatten(),
                        audio_active: audio && sys::is_audio_active(),
                        updates_badge: updates.badge,
                        updates_tooltip: updates.tooltip,
                    }
                })
                .map(move |state| Message::TogglesLoaded(revision, state))
            }
            RefreshKind::Radios => {
                let (wifi, bt) = (self.wifi_revision, self.bt_revision);
                worker::start(|| (sys::get_wifi(), sys::get_bt()))
                    .map(move |state| Message::RadioLoaded(wifi, bt, state))
            }
            RefreshKind::Power => {
                let revision = self.power_revision;
                worker::start(sys::get_power_profile)
                    .map(move |state| Message::PowerLoaded(revision, state))
            }
            RefreshKind::Slider(SliderKind::Volume) => {
                let revision = self
                    .slider_revision
                    .get(&SliderKind::Volume)
                    .copied()
                    .unwrap_or(0);
                worker::start(sys::get_volume)
                    .map(move |value| Message::VolumeLoaded(revision, value))
            }
            RefreshKind::Slider(kind) => {
                let revision = self.slider_revision.get(&kind).copied().unwrap_or(0);
                worker::start(move || match kind {
                    SliderKind::Brightness => sys::get_brightness(),
                    SliderKind::Sunset => sys::get_sunset(),
                    SliderKind::Volume => unreachable!(),
                })
                .map(move |value| Message::SliderLoaded(kind, revision, value))
            }
            RefreshKind::Notifications => {
                let revision = self.dnd_revision;
                worker::start(|| (notify::fetch_notifications(), sys::mako_dnd()))
                    .map(move |state| Message::NotifsRefreshed(revision, state))
            }
        }
    }

    pub fn update(&mut self, message: Message) -> Task<Message> {
        let phase = match &message {
            Message::GlobalBlurLoaded(_) => Some(RefreshKind::GlobalBlur),
            Message::WeatherLoaded(_) => Some(RefreshKind::Weather),
            Message::MetricsLoaded(_) => Some(RefreshKind::Metrics),
            Message::TogglesLoaded(_, _) => Some(RefreshKind::Toggles),
            Message::RadioLoaded(_, _, _) => Some(RefreshKind::Radios),
            Message::PowerLoaded(_, _) => Some(RefreshKind::Power),
            Message::VolumeLoaded(_, _) => Some(RefreshKind::Slider(SliderKind::Volume)),
            Message::SliderLoaded(kind, _, _) => Some(RefreshKind::Slider(*kind)),
            _ => None,
        };
        if let Some(phase) = phase {
            self.finish_refresh(phase);
        }
        // Notification refreshes also occur after actions, independently of the
        // periodic batch; their completion must not release a batch slot.
        match message {
            Message::PanelSize(size) => {
                crate::renderer::trace_phase(&format!("panel-fit {}x{}", size.width, size.height));
                self.panel_size = Some(size);
                return self.fit_panel();
            }
            Message::PanelFitted(size, success) => {
                self.panel_fit_busy = false;
                let first_fit = success && self.panel_applied_size.is_none();
                self.panel_applied_size = success.then_some(size);
                // Follow an in-flight resize with only the latest measured size.
                // A failed current size retries on Tick, avoiding a busy loop.
                let refit = if self.panel_size != Some(size) {
                    self.fit_panel()
                } else {
                    Task::none()
                };
                if first_fit {
                    return Task::batch([Self::configure_blur(self.appearance.blur), refit]);
                }
                return refit;
            }
            Message::WindowEvent(id, event) => {
                self.window_id = Some(id);
                if matches!(event, iced_core::window::Event::Opened { .. }) {
                    return self.fit_panel();
                }
            }
            Message::BlurConfigured => {}
            Message::GlobalBlurLoaded(enabled) => {
                if let Some(enabled) = enabled {
                    self.global_blur_enabled = enabled;
                }
            }
            Message::Tick => {
                // Local time is cheap to read and must not wait for slow DDC
                // discovery or occupied hardware-query workers.
                (self.time_text, self.date_text, self.today) = sys::current_time_date();
                let panel_fit = self.fit_panel();
                let mut blur_change = Task::none();
                if let Some(appearance) = Appearance::load() {
                    if appearance.blur != self.appearance.blur && self.panel_applied_size.is_some()
                    {
                        blur_change = Self::configure_blur(appearance.blur);
                    }
                    self.appearance = appearance;
                }
                if let Some(theme) = AppTheme::load_generated() {
                    self.theme = theme;
                }
                // Only skip the still-running categories. Slow DDC must not
                // prevent the next radio, notification, or volume refresh.
                return Task::batch([blur_change, panel_fit, self.refresh_all()]);
            }
            Message::WeatherLoaded(w) => {
                self.weather = w;
            }
            Message::MetricsLoaded((cpu, ram, net, battery)) => {
                if !battery.present
                    || (self.battery.present
                        && self.battery.is_discharging() != battery.is_discharging())
                {
                    self.show_battery_metric = None;
                }
                self.cpu = cpu;
                self.ram = ram;
                self.net = net;
                self.battery = battery;
            }
            Message::TogglesLoaded(revision, s) => {
                if revision == self.toggle_revision {
                    self.toggles = s;
                }
            }
            Message::RadioLoaded(wifi_revision, bt_revision, (wifi, bt)) => {
                if !self.wifi_pending && wifi_revision == self.wifi_revision {
                    self.wifi_on = wifi;
                }
                if !self.bt_pending && bt_revision == self.bt_revision {
                    self.bt_on = bt;
                }
            }
            Message::PowerLoaded(revision, p) => {
                if self.power_pending.is_none() && revision == self.power_revision {
                    self.power_profile = p;
                }
            }
            Message::VolumeLoaded(revision, value) => {
                if revision
                    == self
                        .slider_revision
                        .get(&SliderKind::Volume)
                        .copied()
                        .unwrap_or(0)
                {
                    let recent = self
                        .slider_changed_at
                        .get(&SliderKind::Volume)
                        .is_some_and(|t| t.elapsed() < Duration::from_secs(3));
                    if !recent && !self.slider_busy.contains(&SliderKind::Volume) {
                        match value {
                            Some((level, muted)) => {
                                self.volume = Some(level);
                                self.volume_muted = muted;
                            }
                            None => {
                                self.volume = None;
                                self.volume_muted = false;
                            }
                        }
                    }
                }
            }
            Message::SliderLoaded(kind, revision, value) => {
                if revision == self.slider_revision.get(&kind).copied().unwrap_or(0) {
                    let recent = self
                        .slider_changed_at
                        .get(&kind)
                        .is_some_and(|t| t.elapsed() < Duration::from_secs(3));
                    if !recent && !self.slider_busy.contains(&kind) {
                        match kind {
                            SliderKind::Volume => self.volume = value,
                            SliderKind::Brightness => self.brightness = value,
                            SliderKind::Sunset => {
                                self.sunset = value;
                                self.sunset_available = value.is_some();
                            }
                        }
                    }
                }
            }
            Message::NotifsRefreshed(revision, result) => {
                self.finish_refresh(RefreshKind::Notifications);
                return self.update(Message::NotifsLoaded(revision, result));
            }
            Message::NotifsLoaded(revision, (notifs, dnd)) => {
                self.notifications_loaded = true;
                self.notifications = notifs
                    .into_iter()
                    .filter(|n| !self.dismissed.contains(&n.id))
                    .collect();
                if dnd.is_some() && !self.dnd_pending && revision == self.dnd_revision {
                    self.dnd = dnd;
                }
                self.prune_expanded();
            }
            Message::SliderChanged(kind, value) => {
                let value = match kind {
                    SliderKind::Volume => {
                        let clamped = sys::clamp(value, 0.0, 100.0);
                        self.volume = Some(clamped);
                        if clamped > 0.0 {
                            self.volume_muted = false;
                        }
                        clamped
                    }
                    SliderKind::Brightness => {
                        self.brightness = Some(sys::clamp(value, 1.0, 100.0));
                        self.brightness.unwrap()
                    }
                    SliderKind::Sunset => {
                        self.sunset = Some(sys::clamp(value, 0.0, 100.0));
                        self.sunset.unwrap()
                    }
                };
                *self.slider_revision.entry(kind).or_default() += 1;
                self.slider_changed_at.insert(kind, Instant::now());
                if self.slider_busy.contains(&kind) {
                    self.slider_pending.insert(kind, value);
                } else {
                    self.slider_busy.insert(kind);
                    return Self::apply_slider(kind, value);
                }
            }
            Message::SliderApplied(kind) => {
                if let Some(value) = self.slider_pending.remove(&kind) {
                    return Self::apply_slider(kind, value);
                }
                self.slider_busy.remove(&kind);
                self.slider_changed_at.insert(kind, Instant::now());
            }
            Message::WifiToggled(on) => {
                if self.wifi_pending {
                    return Task::none();
                }
                self.wifi_pending = true;
                self.wifi_revision += 1;
                self.wifi_on = Some(on);
                return worker::perform(move || sys::set_wifi(on)).map(Message::WifiSettled);
            }
            Message::WifiSettled(observed) => {
                self.wifi_pending = false;
                self.wifi_on = observed;
            }
            Message::BtToggled(on) => {
                if self.bt_pending {
                    return Task::none();
                }
                self.bt_pending = true;
                self.bt_revision += 1;
                self.bt_on = Some(on);
                return worker::perform(move || sys::set_bt(on)).map(Message::BtSettled);
            }
            Message::BtSettled(observed) => {
                self.bt_pending = false;
                self.bt_on = observed;
            }
            Message::PowerSelected(profile) => {
                if self.power_pending.is_some() {
                    return Task::none();
                }
                self.power_pending = Some(profile.clone());
                self.power_revision += 1;
                return worker::perform(move || {
                    let ok = sys::apply_power_profile(&profile);
                    (profile, ok)
                })
                .map(Message::PowerSettled);
            }
            Message::PowerSettled((profile, ok)) => {
                self.power_pending = None;
                if ok {
                    self.power_profile = Some(profile);
                    self.error = None;
                } else {
                    self.error = Some("Could not apply power profile".into());
                }
            }
            Message::Powertop => {
                if self.powertop_running {
                    return Task::none();
                }
                self.powertop_running = true;
                return worker::perform(sys::powertop_autotune).map(|_| Message::PowertopDone);
            }
            Message::PowertopDone => {
                self.powertop_running = false;
            }
            Message::ToggleLeft(id) => {
                if let Some(t) = self.config.toggles.iter().find(|t| t.id == id) {
                    let cmd = t.on_left.clone();
                    if !cmd.is_empty() {
                        self.toggle_revision += 1;
                        execute_detached(&cmd);
                        if matches!(id.as_str(), "wifi" | "updates" | "audio") {
                            return iced_runtime::exit();
                        }
                    }
                    // Immediate visual refresh for known stateful toggles.
                    if id == "animations" {
                        self.toggles.animations_on = self.toggles.animations_on.map(|on| !on);
                    }
                    if id == "blur"
                        && let Some(b) = self.toggles.blur_on
                    {
                        self.toggles.blur_on = Some(!b);
                    }
                }
            }
            Message::ToggleMiddle(id) => {
                if let Some(t) = self.config.toggles.iter().find(|t| t.id == id) {
                    if !t.on_middle.is_empty() {
                        self.toggle_revision += 1;
                    }
                    execute_detached(&t.on_middle);
                }
            }
            Message::ToggleRight(id) => {
                if let Some(t) = self.config.toggles.iter().find(|t| t.id == id)
                    && !t.on_right.is_empty()
                {
                    self.toggle_revision += 1;
                    execute_detached(&t.on_right);
                    if matches!(id.as_str(), "idle" | "blur" | "updates") {
                        return iced_runtime::exit();
                    }
                    if id == "audio" {
                        self.toggles.audio_active = !self.toggles.audio_active;
                    }
                }
            }
            Message::PowerButton => {
                execute_detached("~/user_scripts/wlogout/wlogout_scale.sh");
                return iced_runtime::exit();
            }
            Message::ClockPressed => {
                self.calendar_month_offset = 0;
                self.calendar_scroll_accum = 0.0;
                execute_detached("gnome-clocks");
                return iced_runtime::exit();
            }
            Message::CalendarScrolled(delta) => {
                let (x, y, is_lines) = match delta {
                    ScrollDelta::Lines { x, y } => (x, y, true),
                    ScrollDelta::Pixels { x, y } => (x, y, false),
                };
                if !x.is_finite() || !y.is_finite() || y.abs() <= f32::EPSILON || x.abs() > y.abs()
                {
                    return Task::none();
                }
                let normalized = if is_lines { y } else { y / 40.0 };
                if self.calendar_scroll_accum.abs() > f32::EPSILON
                    && self.calendar_scroll_accum.is_sign_positive()
                        != normalized.is_sign_positive()
                {
                    self.calendar_scroll_accum = 0.0;
                }
                self.calendar_scroll_accum += normalized;
                if self.calendar_scroll_accum.abs() >= 1.0 {
                    // Scroll UP (y > 0) -> previous month; scroll DOWN (y < 0) -> next month.
                    let step = if self.calendar_scroll_accum > 0.0 {
                        -1
                    } else {
                        1
                    };
                    self.calendar_month_offset =
                        (self.calendar_month_offset + step).clamp(-120, 120);
                    if is_lines {
                        self.calendar_scroll_accum = 0.0;
                    } else {
                        self.calendar_scroll_accum =
                            self.calendar_scroll_accum.fract().clamp(-0.99, 0.99);
                    }
                }
            }
            Message::CalendarHoverExited => {
                self.calendar_month_offset = 0;
                self.calendar_scroll_accum = 0.0;
            }
            Message::WeatherPressed => {
                execute_detached(
                    "foot --app-id=dusky_tui --hold zsh -fc 'source \"$HOME/.config/zshrc/wthr\"; wthr'",
                );
                return iced_runtime::exit();
            }
            Message::WifiManager => {
                execute_detached(
                    "foot --app-id=dusky_tui python3 ~/user_scripts/dusky_tui/python/main/main.py ~/user_scripts/network_manager/tui_dusky_network.py",
                );
                return iced_runtime::exit();
            }
            Message::BluetoothManager => {
                execute_detached("blueman-manager");
                return iced_runtime::exit();
            }
            Message::BluetoothRecovery => {
                execute_detached("python3 \"$HOME/user_scripts/bluetooth/bluetooth_recovery.py\"");
                return iced_runtime::exit();
            }
            Message::VolumeControl => {
                execute_detached("pavucontrol");
                return iced_runtime::exit();
            }
            Message::OutputSelector => {
                execute_detached("~/user_scripts/audio/dusky_in_out_source.sh --output");
                return iced_runtime::exit();
            }
            Message::MetricPressed(which) => {
                match which.as_str() {
                    "net" => {
                        if self.battery.present {
                            self.show_battery_metric = Some(!self.showing_battery());
                        } else {
                            self.show_battery_metric = None;
                        }
                        return Task::none();
                    }
                    "ram" => execute_detached("kitty --class zramctl --hold zramctl"),
                    "cpu" => execute_detached("kitty --class btop btop"),
                    _ => return Task::none(),
                }
                return iced_runtime::exit();
            }
            Message::MetricRightPressed(which) => {
                if which == "net" && self.net.warp.is_installed() {
                    execute_detached("python3 ~/user_scripts/networking/warp_toggle.py");
                }
                return Task::none();
            }
            Message::DndToggled => {
                if self.dnd_pending || self.dnd.is_none() {
                    return Task::none();
                }
                self.dnd_pending = true;
                self.dnd_revision += 1;
                self.dnd = self.dnd.map(|on| !on);
                return worker::perform(|| {
                    sys::toggle_dnd();
                    sys::mako_dnd()
                })
                .map(Message::DndSettled);
            }
            Message::DndSettled(observed) => {
                self.dnd_pending = false;
                self.dnd = observed;
            }
            Message::ClearNotifs => {
                self.dismissed
                    .extend(self.notifications.iter().map(|n| n.id));
                self.notifications.clear();
                self.expanded.clear();
                return worker::perform(notify::clear_all).map(|_| Message::NotifsCleared);
            }
            Message::NotifsCleared => {
                let revision = self.dnd_revision;
                return worker::perform(|| (notify::fetch_notifications(), sys::mako_dnd()))
                    .map(move |state| Message::NotifsLoaded(revision, state));
            }
            Message::NotifDismiss(id) => {
                let was_active = self
                    .notifications
                    .iter()
                    .find(|n| n.id == id)
                    .is_some_and(|n| n.source == "active");
                self.dismissed.insert(id);
                self.notifications.retain(|n| n.id != id);
                self.prune_expanded();
                notify::append_blacklist_ids(&[id]);
                if was_active {
                    return worker::perform(move || notify::dismiss_mako_ids(&[id]))
                        .map(|()| Message::NotifActionDone);
                }
            }
            Message::NotifInvoke(id) => {
                let notification = self.notifications.iter().find(|n| n.id == id).cloned();
                self.dismissed.insert(id);
                self.notifications.retain(|n| n.id != id);
                self.prune_expanded();
                notify::append_blacklist_ids(&[id]);
                if let Some(n) = notification {
                    return worker::perform(move || notify::invoke(n)).map(Message::NotifInvoked);
                }
            }
            Message::NotifInvoked(launched) => {
                if launched {
                    return iced_runtime::exit();
                }
            }
            Message::NotifActionDone => {}
            Message::NotificationHovered(key) => self.hovered_notification = Some(key),
            Message::NotificationLeft(key) => {
                if self.hovered_notification.as_ref() == Some(&key) {
                    self.hovered_notification = None;
                }
            }
            Message::StackToggled(app) => {
                if !self.expanded.remove(&app) {
                    self.expanded.insert(app);
                }
            }
            Message::StackClosed(app) => {
                let mut ids = Vec::new();
                let mut active_ids = Vec::new();
                for n in &self.notifications {
                    let a = if n.app.is_empty() { "Unknown" } else { &n.app };
                    if a == app {
                        ids.push(n.id);
                        if n.source == "active" {
                            active_ids.push(n.id);
                        }
                    }
                }
                self.dismissed.extend(ids.iter().copied());
                self.notifications.retain(|n| {
                    let a = if n.app.is_empty() { "Unknown" } else { &n.app };
                    a != app
                });
                self.expanded.remove(&app);
                notify::append_blacklist_ids(&ids);
                if !active_ids.is_empty() {
                    return worker::perform(move || notify::dismiss_mako_ids(&active_ids))
                        .map(|()| Message::NotifActionDone);
                }
            }
            Message::BackdropPressed => return iced_runtime::exit(),
            Message::EventOccurred(Event::Keyboard(iced_core::keyboard::Event::KeyPressed {
                key,
                ..
            })) => {
                if key == Key::Named(Named::Escape) {
                    return iced_runtime::exit();
                }
            }
            Message::EventOccurred(_) => {}
        }
        Task::none()
    }

    fn fit_panel(&mut self) -> Task<Message> {
        if self.panel_fit_busy
            || self.window_id.is_none()
            || self.panel_size == self.panel_applied_size
        {
            return Task::none();
        }
        let (Some(monitor), Some(size)) = (self.panel_monitor, self.panel_size) else {
            return Task::none();
        };
        self.panel_fit_busy = true;
        worker::perform(move || sys::fit_panel(monitor, size))
            .map(move |success| Message::PanelFitted(size, success))
    }

    fn prune_expanded(&mut self) {
        let mut counts: HashMap<&str, usize> = HashMap::new();
        for n in &self.notifications {
            let app = if n.app.is_empty() {
                "Unknown"
            } else {
                n.app.as_str()
            };
            *counts.entry(app).or_default() += 1;
        }
        self.expanded
            .retain(|app| counts.get(app.as_str()).copied().unwrap_or(0) > 1);
    }

    fn finish_refresh(&mut self, kind: RefreshKind) {
        self.refresh_pending.remove(&kind);
        if self.initial_refresh && self.initial_pending.remove(&kind) {
            crate::renderer::trace_phase(kind.phase());
            if self.initial_pending.is_empty() {
                self.initial_refresh = false;
                crate::renderer::trace_phase("snapshot-ready");
            }
        }
    }

    fn configure_blur(enabled: bool) -> Task<Message> {
        worker::perform(move || {
            sys::configure_panel_blur(enabled);
        })
        .map(|_| Message::BlurConfigured)
    }

    pub fn subscription(&self) -> Subscription<Message> {
        let events =
            iced_futures::event::listen_with(|event, status, id| match (&status, &event) {
                (
                    _,
                    Event::Window(
                        event @ (iced_core::window::Event::Opened { .. }
                        | iced_core::window::Event::Resized(_)),
                    ),
                ) => Some(Message::WindowEvent(id, event.clone())),
                (
                    iced_core::event::Status::Captured,
                    Event::Keyboard(iced_core::keyboard::Event::KeyPressed {
                        key: Key::Named(Named::Escape),
                        ..
                    }),
                ) => Some(Message::EventOccurred(event)),
                (iced_core::event::Status::Ignored, _) => Some(Message::EventOccurred(event)),
                _ => None,
            });
        Subscription::batch([
            events,
            Subscription::run(Self::ticks),
            Subscription::run(crate::click_away::events),
        ])
    }

    fn ticks() -> impl iced_futures::futures::Stream<Item = Message> {
        iced_futures::futures::stream::unfold((), |()| async {
            futures_timer::Delay::new(Duration::from_secs(2)).await;
            Some((Message::Tick, ()))
        })
    }

    fn apply_slider(kind: SliderKind, value: f32) -> Task<Message> {
        worker::perform(move || match kind {
            SliderKind::Volume => sys::apply_volume(value),
            SliderKind::Brightness => sys::apply_brightness(value),
            SliderKind::Sunset => sys::apply_sunset(value),
        })
        .map(move |_| Message::SliderApplied(kind))
    }

    fn icon_label(
        glyph: &'static str,
        size: f32,
        color: Option<Color>,
    ) -> Element<'static, Message> {
        crate::icons::icon(glyph, size, color)
    }

    fn card(theme: AppTheme, radius: f32) -> container::Style {
        container::Style {
            background: Some(Self::surface(theme, 0.085).into()),
            border: Border {
                radius: radius.into(),
                color: Color::TRANSPARENT,
                width: 0.0,
            },
            shadow: iced_core::Shadow {
                color: Color::from_rgba(0.0, 0.0, 0.0, 0.35),
                offset: iced_core::Vector::new(0.0, 2.0),
                blur_radius: 8.0,
            },
            ..Default::default()
        }
    }

    fn surface(theme: AppTheme, amount: f32) -> Color {
        Color {
            a: 0.52,
            ..Self::mix(theme.bg, theme.fg, amount)
        }
    }

    fn mix(a: Color, b: Color, amount: f32) -> Color {
        Color::from_rgb(
            a.r + (b.r - a.r) * amount,
            a.g + (b.g - a.g) * amount,
            a.b + (b.b - a.b) * amount,
        )
    }

    fn flat(theme: AppTheme, status: button::Status) -> button::Style {
        button::Style {
            background: Some(
                if status == button::Status::Hovered {
                    Self::surface(theme, 0.08)
                } else {
                    Color::TRANSPARENT
                }
                .into(),
            ),
            text_color: theme.muted,
            border: Border {
                radius: 8.0.into(),
                ..Default::default()
            },
            ..Default::default()
        }
    }

    fn parse_leading_f64(s: &str) -> Option<f64> {
        let trimmed = s.trim();
        let mut end = 0usize;
        let mut saw_digit = false;
        let mut saw_dot = false;
        for (i, ch) in trimmed.char_indices() {
            if i == 0 && ch == '-' {
                end = ch.len_utf8();
            } else if ch.is_ascii_digit() {
                saw_digit = true;
                end = i + ch.len_utf8();
            } else if ch == '.' && !saw_dot {
                saw_dot = true;
                end = i + ch.len_utf8();
            } else {
                break;
            }
        }
        if !saw_digit {
            return None;
        }
        trimmed[..end].parse::<f64>().ok()
    }

    fn parse_leading_u64(s: &str) -> Option<u64> {
        let trimmed = s.trim();
        let digits: String = trimmed.chars().take_while(|c| c.is_ascii_digit()).collect();
        if digits.is_empty() {
            return None;
        }
        digits.parse::<u64>().ok()
    }

    fn tooltip_value_style(key: &str, value: &str, theme: AppTheme) -> (Color, Weight) {
        if matches!(
            value,
            "N/A" | "Disabled" | "unknown" | "unavailable" | "off"
        ) {
            return (theme.muted, Weight::Normal);
        }

        match key {
            "Cloudflare WARP" => {
                if value.starts_with("Connected") {
                    (saturate(theme.profiles[0], 0.9), Weight::Semibold)
                } else {
                    (theme.muted, Weight::Normal)
                }
            }
            "Temp" => match Self::parse_leading_f64(value) {
                Some(t) if t >= 85.0 => (theme.danger, Weight::Bold),
                Some(t) if t >= 70.0 => (saturate(theme.profiles[2], 0.85), Weight::Semibold),
                Some(_) => (theme.fg, Weight::Normal),
                None => (theme.muted, Weight::Normal),
            },
            "Power" => match Self::parse_leading_f64(value) {
                Some(w) if w >= 45.0 => (saturate(theme.profiles[2], 0.85), Weight::Semibold),
                Some(_) => (theme.fg, Weight::Normal),
                None => (theme.muted, Weight::Normal),
            },
            "Battery" => {
                if value == "Discharging" {
                    (saturate(theme.profiles[2], 0.8), Weight::Semibold)
                } else if matches!(value, "Charging" | "Full" | "Not charging") {
                    (saturate(theme.profiles[0], 0.85), Weight::Semibold)
                } else if value.ends_with('%') {
                    match Self::parse_leading_u64(value) {
                        Some(pct) if pct <= 20 => (theme.danger, Weight::Bold),
                        _ => (theme.fg, Weight::Normal),
                    }
                } else {
                    (theme.fg, Weight::Normal)
                }
            }
            "Remaining" => {
                if matches!(value, "Full" | "Held") {
                    (saturate(theme.profiles[0], 0.85), Weight::Semibold)
                } else {
                    (theme.fg, Weight::Normal)
                }
            }
            "Wi-Fi" | "Bluetooth" => {
                if value == "on" {
                    (saturate(theme.accent, 1.2), Weight::Semibold)
                } else if value.starts_with("applying") {
                    (theme.secondary, Weight::Normal)
                } else {
                    (theme.muted, Weight::Normal)
                }
            }
            "Pacman" | "AUR" | "Dusky commits (main)" => match Self::parse_leading_u64(value) {
                Some(n) if n > 0 => (theme.accent, Weight::Semibold),
                _ => (theme.muted, Weight::Normal),
            },
            _ => (theme.fg, Weight::Normal),
        }
    }

    fn render_action_hint(trigger: &str, desc: &str, theme: AppTheme) -> Element<'static, Message> {
        let action_fg = saturate(theme.accent, 1.2);
        let action_bg = Color {
            a: 0.15,
            ..theme.accent
        };
        let action_desc_color = Self::mix(theme.fg, theme.muted, 0.12);

        let badge = container(
            text(trigger.to_owned())
                .size(11)
                .font(Font {
                    weight: Weight::Bold,
                    ..Font::DEFAULT
                })
                .color(action_fg),
        )
        .padding([1, 5])
        .style(move |_| container::Style {
            background: Some(action_bg.into()),
            border: Border {
                radius: 4.0.into(),
                ..Default::default()
            },
            ..Default::default()
        });

        let mut action_row = row![badge].spacing(6).align_y(Vertical::Center);
        if !desc.is_empty() {
            action_row = action_row.push(text(desc.to_owned()).size(12).color(action_desc_color));
        }
        action_row.into()
    }

    fn render_rich_tooltip(label: &str, theme: AppTheme) -> Element<'static, Message> {
        let lines: Vec<&str> = label.lines().collect();
        let total_lines = lines.len();
        let key_color = Self::mix(theme.accent, theme.fg, 0.10);

        let mut col = column![].spacing(3);
        let mut prev_was_action = false;
        let mut prev_was_blank = false;

        for (idx, raw_line) in lines.into_iter().enumerate() {
            let trimmed = raw_line.trim();
            if trimmed.is_empty() {
                col = col.push(Space::new().height(4.0));
                prev_was_blank = true;
                prev_was_action = false;
                continue;
            }

            let action_split = ["LMB:", "RMB:", "MMB:"].into_iter().find_map(|prefix| {
                trimmed
                    .strip_prefix(prefix)
                    .map(|rest| (prefix, rest.trim()))
            });

            if let Some((trigger, desc)) = action_split {
                if idx > 0 && !prev_was_action && !prev_was_blank {
                    col = col.push(Space::new().height(3.0));
                }
                col = col.push(Self::render_action_hint(trigger, desc, theme));
                prev_was_action = true;
                prev_was_blank = false;
                continue;
            }

            prev_was_action = false;
            prev_was_blank = false;

            if !trimmed.contains("://")
                && let Some((raw_key, raw_val)) = trimmed.split_once(':')
            {
                let key = raw_key.trim();
                let val = raw_val.trim();
                if !key.is_empty() && !val.is_empty() {
                    let (val_color, val_weight) = Self::tooltip_value_style(key, val, theme);
                    let kv_row = row![
                        text(format!("{key}:"))
                            .size(12)
                            .font(Font {
                                weight: Weight::Semibold,
                                ..Font::DEFAULT
                            })
                            .color(key_color),
                        text(val.to_owned())
                            .size(12)
                            .font(Font {
                                weight: val_weight,
                                ..Font::DEFAULT
                            })
                            .color(val_color),
                    ]
                    .spacing(6)
                    .align_y(Vertical::Center);
                    col = col.push(kv_row);
                    continue;
                }
            }

            let is_offline = matches!(
                trimmed,
                "Disconnected" | "Network reading unavailable" | "No battery present"
            );
            let (title_color, title_weight) = if is_offline {
                (theme.muted, Weight::Normal)
            } else if idx == 0 && total_lines > 1 {
                (theme.accent, Weight::Bold)
            } else {
                (Self::mix(theme.accent, theme.fg, 0.25), Weight::Semibold)
            };

            col = col.push(
                text(trimmed.to_owned())
                    .size(12)
                    .font(Font {
                        weight: title_weight,
                        ..Font::DEFAULT
                    })
                    .color(title_color),
            );
        }

        col.into()
    }

    fn render_weather_tooltip(w: &sys::WeatherState, theme: AppTheme) -> Element<'static, Message> {
        let mut col = column![].spacing(8).width(Length::Fixed(224.0));

        // Tier 1: Hero temperature + optional location chip.
        let mut hero = row![
            text(w.temperature.clone())
                .size(28)
                .font(Font {
                    weight: Weight::Bold,
                    ..Font::DEFAULT
                })
                .color(theme.fg)
        ]
        .spacing(8)
        .align_y(Vertical::Center)
        .width(Length::Fill);

        if let Some(location) = &w.location {
            let chip_bg = Color {
                a: 0.13,
                ..theme.accent
            };
            let chip_fg = Self::mix(theme.accent, theme.fg, 0.22);
            let chip = container(
                text(location.clone())
                    .size(11)
                    .font(Font {
                        weight: Weight::Semibold,
                        ..Font::DEFAULT
                    })
                    .color(chip_fg),
            )
            .padding([2, 7])
            .style(move |_| container::Style {
                background: Some(chip_bg.into()),
                border: Border {
                    radius: 6.0.into(),
                    ..Default::default()
                },
                ..Default::default()
            });
            hero = hero.push(Space::new().width(Length::Fill)).push(chip);
        }

        let mut head = column![hero].spacing(2).width(Length::Fill);

        // Tier 2: Condition description.
        if let Some(condition) = &w.condition {
            head = head.push(
                text(condition.clone())
                    .size(13)
                    .font(Font {
                        weight: Weight::Semibold,
                        ..Font::DEFAULT
                    })
                    .color(Self::mix(theme.fg, theme.muted, 0.10)),
            );
        }
        col = col.push(head);

        // Tier 3: High / Low / Precipitation strip.
        let mut metric_items: Vec<Element<'static, Message>> = Vec::with_capacity(3);
        if let Some(hi) = &w.temp_max {
            metric_items.push(
                row![
                    text("▲").size(10).color(saturate(theme.profiles[2], 0.85)),
                    text(hi.clone())
                        .size(12)
                        .font(Font::MONOSPACE)
                        .color(theme.fg),
                ]
                .spacing(4)
                .align_y(Vertical::Center)
                .into(),
            );
        }
        if let Some(lo) = &w.temp_min {
            metric_items.push(
                row![
                    text("▼")
                        .size(10)
                        .color(Self::mix(theme.secondary, theme.fg, 0.18)),
                    text(lo.clone())
                        .size(12)
                        .font(Font::MONOSPACE)
                        .color(theme.fg),
                ]
                .spacing(4)
                .align_y(Vertical::Center)
                .into(),
            );
        }
        if let Some(precip) = &w.precip_chance {
            let rain_active = Self::parse_leading_u64(precip).is_some_and(|p| p > 0);
            let (val_color, val_weight) = if rain_active {
                (theme.accent, Weight::Semibold)
            } else {
                (theme.muted, Weight::Normal)
            };
            metric_items.push(
                row![
                    text("Rain")
                        .size(11)
                        .font(Font {
                            weight: Weight::Semibold,
                            ..Font::DEFAULT
                        })
                        .color(saturate(theme.accent, 1.15)),
                    text(precip.clone())
                        .size(12)
                        .font(Font {
                            weight: val_weight,
                            ..Font::MONOSPACE
                        })
                        .color(val_color),
                ]
                .spacing(4)
                .align_y(Vertical::Center)
                .into(),
            );
        }
        if !metric_items.is_empty() {
            let mut strip = row![]
                .spacing(6)
                .align_y(Vertical::Center)
                .width(Length::Fill);
            let count = metric_items.len();
            for (idx, item) in metric_items.into_iter().enumerate() {
                if idx > 0 && count > 1 {
                    strip = strip.push(Space::new().width(Length::Fill));
                }
                strip = strip.push(item);
            }
            col = col.push(container(strip).padding([6, 10]).width(Length::Fill).style(
                move |_| container::Style {
                    background: Some(Self::surface(theme, 0.08).into()),
                    border: Border {
                        radius: 8.0.into(),
                        ..Default::default()
                    },
                    ..Default::default()
                },
            ));
        }

        // Tier 4: Conditional offline banner.
        if let Some(note) = &w.offline_note {
            col = col.push(
                container(
                    text(note.clone())
                        .size(11)
                        .font(Font {
                            weight: Weight::Semibold,
                            ..Font::DEFAULT
                        })
                        .color(theme.danger),
                )
                .padding([5, 8])
                .width(Length::Fill)
                .style(move |_| container::Style {
                    background: Some(
                        Color {
                            a: 0.14,
                            ..theme.danger
                        }
                        .into(),
                    ),
                    border: Border {
                        radius: 6.0.into(),
                        color: Color {
                            a: 0.35,
                            ..theme.danger
                        },
                        width: 1.0,
                    },
                    ..Default::default()
                }),
            );
        }

        // Tier 5: Hairline divider + action footer.
        col = col
            .push(
                container(Space::new())
                    .width(Length::Fill)
                    .height(Length::Fixed(1.0))
                    .style(move |_| container::Style {
                        background: Some(
                            Color {
                                a: 0.10,
                                ..theme.fg
                            }
                            .into(),
                        ),
                        ..Default::default()
                    }),
            )
            .push(Self::render_action_hint(
                "LMB:",
                "Open terminal forecast",
                theme,
            ));

        col.into()
    }

    fn format_month_offset(offset: i32) -> String {
        let sign = if offset < 0 { "−" } else { "+" };
        let abs = offset.unsigned_abs();
        if abs < 12 {
            format!("{sign}{abs} mo")
        } else {
            let years = abs / 12;
            let rem = abs % 12;
            if rem == 0 {
                format!("{sign}{years}y")
            } else {
                format!("{sign}{years}y {rem}m")
            }
        }
    }

    fn contrast_text_on(bg: Color, dark: Color) -> Color {
        let luminance = 0.2126 * bg.r + 0.7152 * bg.g + 0.0722 * bg.b;
        if luminance >= 0.40 {
            dark
        } else {
            Color::WHITE
        }
    }

    fn calendar_weekend_colors(theme: AppTheme) -> (Color, Color) {
        let base = Self::mix(theme.danger, Color::from_rgb8(232, 96, 96), 0.35);
        let header = Color { a: 0.90, ..base };
        let day = Self::mix(base, theme.fg, 0.22);
        (header, day)
    }

    fn build_calendar_month(today: NaiveDate, offset: i32) -> CalendarMonth {
        let base_first =
            NaiveDate::from_ymd_opt(today.year(), today.month(), 1).expect("valid first of month");
        let target_first = if offset > 0 {
            base_first
                .checked_add_months(Months::new(offset as u32))
                .unwrap_or(base_first)
        } else if offset < 0 {
            base_first
                .checked_sub_months(Months::new(offset.unsigned_abs()))
                .unwrap_or(base_first)
        } else {
            base_first
        };
        let leading = u64::from(target_first.weekday().num_days_from_sunday());
        let grid_start = target_first
            .checked_sub_days(Days::new(leading))
            .unwrap_or(target_first);
        let cells = std::array::from_fn(|idx| {
            let date = grid_start
                .checked_add_days(Days::new(idx as u64))
                .unwrap_or(target_first);
            let in_month =
                date.year() == target_first.year() && date.month() == target_first.month();
            let col = idx % 7;
            CalendarCell {
                day: date.day(),
                in_month,
                is_today: offset == 0 && in_month && date == today,
                is_weekend: col == 0 || col == 6,
            }
        });
        let badge = if offset == 0 {
            format!("W{:02}", today.iso_week().week())
        } else {
            Self::format_month_offset(offset)
        };
        CalendarMonth {
            title: target_first.format("%B %Y").to_string(),
            badge,
            is_offset: offset != 0,
            cells,
        }
    }

    fn render_calendar_tooltip(
        today: NaiveDate,
        offset: i32,
        theme: AppTheme,
    ) -> Element<'static, Message> {
        let cal = Self::build_calendar_month(today, offset);
        let badge_bg = if cal.is_offset {
            Color {
                a: 0.14,
                ..theme.accent
            }
        } else {
            Color {
                a: 0.07,
                ..theme.fg
            }
        };
        let badge_fg = if cal.is_offset {
            theme.accent
        } else {
            theme.muted
        };
        let badge = container(
            text(cal.badge)
                .size(10)
                .font(Font {
                    weight: Weight::Semibold,
                    ..Font::DEFAULT
                })
                .color(badge_fg),
        )
        .padding([2, 7])
        .style(move |_| container::Style {
            background: Some(badge_bg.into()),
            border: Border {
                radius: 6.0.into(),
                ..Default::default()
            },
            ..Default::default()
        });

        let header = row![
            text(cal.title)
                .size(14)
                .font(Font {
                    weight: Weight::Bold,
                    ..Font::DEFAULT
                })
                .color(theme.fg),
            Space::new().width(Length::Fill),
            badge,
        ]
        .align_y(Vertical::Center)
        .width(Length::Fill);

        let (weekend_header, weekend_fg) = Self::calendar_weekend_colors(theme);
        let mut weekdays = row![].spacing(2);
        for (idx, label) in ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"]
            .into_iter()
            .enumerate()
        {
            let color = if idx == 0 || idx == 6 {
                weekend_header
            } else {
                theme.muted
            };
            weekdays = weekdays.push(
                container(
                    text(label)
                        .size(10)
                        .font(Font {
                            weight: Weight::Bold,
                            ..Font::DEFAULT
                        })
                        .color(color),
                )
                .center_x(Length::Fixed(30.0)),
            );
        }

        let today_fg = Self::contrast_text_on(theme.accent, theme.bg);
        let outside_fg = Color {
            a: 0.28,
            ..theme.muted
        };

        let mut grid = column![].spacing(2);
        for week in cal.cells.as_chunks::<7>().0 {
            let mut week_row = row![].spacing(2);
            for cell in week {
                let (fg, font) = if cell.is_today {
                    (
                        today_fg,
                        Font {
                            weight: Weight::Bold,
                            ..Font::MONOSPACE
                        },
                    )
                } else if !cell.in_month {
                    (outside_fg, Font::MONOSPACE)
                } else if cell.is_weekend {
                    (weekend_fg, Font::MONOSPACE)
                } else {
                    (theme.fg, Font::MONOSPACE)
                };
                let day_cell = container(text(cell.day.to_string()).size(11).font(font).color(fg))
                    .center_x(Length::Fixed(30.0))
                    .center_y(Length::Fixed(24.0));
                let day_cell = if cell.is_today {
                    day_cell.style(move |_| container::Style {
                        background: Some(theme.accent.into()),
                        border: Border {
                            radius: 8.0.into(),
                            ..Default::default()
                        },
                        ..Default::default()
                    })
                } else {
                    day_cell
                };
                week_row = week_row.push(day_cell);
            }
            grid = grid.push(week_row);
        }

        let divider = container(Space::new())
            .width(Length::Fill)
            .height(Length::Fixed(1.0))
            .style(move |_| container::Style {
                background: Some(
                    Color {
                        a: 0.10,
                        ..theme.fg
                    }
                    .into(),
                ),
                ..Default::default()
            });

        let footer = row![
            Self::render_action_hint("LMB:", "Clocks", theme),
            Space::new().width(Length::Fill),
            Self::render_action_hint("Scroll:", "Month", theme),
        ]
        .align_y(Vertical::Center)
        .width(Length::Fill);

        column![header, weekdays, grid, divider, footer]
            .spacing(7)
            .width(Length::Fixed(222.0))
            .into()
    }

    const TOOLTIP_DELAY: Duration = Duration::from_millis(500);

    fn hint_with<'a>(
        content: impl Into<Element<'a, Message>>,
        tip: Element<'a, Message>,
        position: tooltip::Position,
        padding: f32,
        theme: AppTheme,
    ) -> Element<'a, Message> {
        tooltip(content, tip, position)
            .gap(8.0)
            .padding(padding)
            .delay(Self::TOOLTIP_DELAY)
            .snap_within_viewport(true)
            .style(move |_| {
                let mut style = Self::card(theme, 10.0);
                style.background = Some(
                    Color {
                        a: 1.0,
                        ..theme.card_bg
                    }
                    .into(),
                );
                style.border = Border {
                    radius: 10.0.into(),
                    color: Color {
                        a: 0.14,
                        ..Self::mix(theme.bg, theme.fg, 0.30)
                    },
                    width: 1.0,
                };
                style
            })
            .into()
    }

    fn hint<'a>(
        content: impl Into<Element<'a, Message>>,
        label: String,
        theme: AppTheme,
    ) -> Element<'a, Message> {
        Self::hint_with(
            content,
            Self::render_rich_tooltip(&label, theme),
            tooltip::Position::Top,
            10.0,
            theme,
        )
    }

    fn showing_battery(&self) -> bool {
        self.battery.present
            && self
                .show_battery_metric
                .unwrap_or_else(|| self.battery.is_discharging())
    }

    fn first_metric_display(&self) -> (String, Color, Option<Color>, String) {
        let warp_installed = self.net.warp.is_installed();
        if self.showing_battery() {
            let value = if self.battery.text.is_empty() {
                "--".into()
            } else {
                self.battery.text.clone()
            };
            let color = match self.battery.status {
                sys::BatteryStatus::Discharging => saturate(self.theme.profiles[2], 0.8),
                sys::BatteryStatus::Charging
                | sys::BatteryStatus::Full
                | sys::BatteryStatus::NotCharging => saturate(self.theme.profiles[0], 0.8),
                sys::BatteryStatus::Unknown => self.theme.fg,
            };
            let mut hint = if self.battery.tooltip.is_empty() {
                "Battery power".into()
            } else {
                self.battery.tooltip.clone()
            };
            sys::append_warp_status(&mut hint, self.net.warp);
            hint.push_str("\nLMB: Network traffic");
            if warp_installed {
                hint.push_str("\nRMB: Toggle WARP");
            }
            (value, color, None, hint)
        } else {
            let value = if self.net.text.is_empty() || self.net.disconnected {
                "--".into()
            } else {
                self.net.text.clone()
            };
            let ring = self
                .net
                .warp
                .is_connected()
                .then(|| saturate(self.theme.accent, 1.2));
            let mut hint = self.net.tooltip.clone();
            if self.battery.present {
                hint.push_str("\nLMB: Battery power");
            }
            if warp_installed {
                hint.push_str("\nRMB: Toggle WARP");
            }
            (value, self.theme.fg, ring, hint)
        }
    }

    fn metric(
        &self,
        glyph: &'static str,
        value: String,
        color: Color,
        ring_color: Option<Color>,
        kind: &'static str,
        hint: String,
    ) -> Element<'static, Message> {
        let theme = self.theme;
        let label = text(value)
            .size(if kind == "net" { 10 } else { 12 })
            .font(Font::MONOSPACE)
            .color(color);
        let contents: Element<'_, Message> = if kind == "net" {
            label.into()
        } else {
            row![Self::icon_label(glyph, 15.0, Some(theme.muted)), label]
                .spacing(6)
                .align_y(Vertical::Center)
                .into()
        };
        let content = container(contents)
            .center_x(Length::Fill)
            .center_y(Length::Fixed(34.0));
        let mut btn = button(content)
            .padding(0)
            .width(Length::Fill)
            .style(move |_, status| {
                let mut style = Self::flat(theme, status);
                style.background = Some(
                    if status == button::Status::Hovered {
                        Self::surface(theme, 0.14)
                    } else {
                        Self::surface(theme, 0.085)
                    }
                    .into(),
                );
                let border_color = match ring_color {
                    Some(ring) => Color {
                        a: if status == button::Status::Hovered {
                            0.72
                        } else {
                            0.50
                        },
                        ..ring
                    },
                    None => Color::TRANSPARENT,
                };
                style.border = Border {
                    radius: 12.0.into(),
                    color: border_color,
                    width: 1.25,
                };
                style.shadow = iced_core::Shadow {
                    color: Color::from_rgba(0.0, 0.0, 0.0, 0.28),
                    offset: iced_core::Vector::new(0.0, 1.5),
                    blur_radius: 6.0,
                };
                style
            });
        if kind != "net" || self.battery.present {
            btn = btn.on_press(Message::MetricPressed(kind.into()));
        }
        let target: Element<'static, Message> = if kind == "net" && self.net.warp.is_installed() {
            mouse_area(btn)
                .on_right_press(Message::MetricRightPressed(kind.into()))
                .into()
        } else {
            btn.into()
        };
        Self::hint(target, hint, theme)
    }

    fn toggle_button(&self, t: &crate::config::Toggle) -> Element<'static, Message> {
        let theme = self.theme;
        let (glyph, active) = match t.id.as_str() {
            "wifi" => (
                icon_glyph(&t.icon),
                self.toggles.wifi_connected == Some(true) && self.wifi_on != Some(false),
            ),
            "animations" => (
                icon_glyph(&t.icon),
                self.toggles.animations_on == Some(true),
            ),
            "idle" => (
                icon_glyph(if self.toggles.idle_inhibited.unwrap_or(false) {
                    "view-reveal-symbolic"
                } else {
                    &t.icon
                }),
                self.toggles.idle_inhibited.unwrap_or(false),
            ),
            "blur" => (icon_glyph(&t.icon), self.toggles.blur_on.unwrap_or(false)),
            "audio" => (
                icon_glyph(if self.toggles.audio_active {
                    "audio-input-microphone-symbolic"
                } else {
                    "audio-input-microphone-muted-symbolic"
                }),
                self.toggles.audio_active,
            ),
            _ => (icon_glyph(&t.icon), false),
        };
        let btn = button(
            container(Self::icon_label(glyph, 22.0, None))
                .center_x(44)
                .center_y(44),
        )
        .padding(0)
        .on_press(Message::ToggleLeft(t.id.clone()))
        .style(move |_, status| {
            let hovered = status == button::Status::Hovered;
            button::Style {
                background: Some(
                    if active {
                        Color {
                            a: if hovered { 0.28 } else { 0.18 },
                            ..theme.accent
                        }
                    } else if hovered {
                        Self::surface(theme, 0.14)
                    } else {
                        Self::surface(theme, 0.085)
                    }
                    .into(),
                ),
                text_color: if active { theme.accent } else { theme.fg },
                border: Border {
                    radius: 22.0.into(),
                    color: if active {
                        Color {
                            a: 0.45,
                            ..theme.accent
                        }
                    } else {
                        Color::TRANSPARENT
                    },
                    width: if active { 1.0 } else { 0.0 },
                },
                shadow: iced_core::Shadow {
                    color: Color::from_rgba(0.0, 0.0, 0.0, 0.22),
                    offset: iced_core::Vector::new(0.0, 1.5),
                    blur_radius: 5.0,
                },
                ..Default::default()
            }
        });
        let mut area = mouse_area(btn);
        if !t.on_right.is_empty() {
            area = area.on_right_press(Message::ToggleRight(t.id.clone()));
        }
        if !t.on_middle.is_empty() {
            area = area.on_middle_press(Message::ToggleMiddle(t.id.clone()));
        }
        let mut cell: Element<'static, Message> = area.into();
        if t.id == "updates" && !self.toggles.updates_badge.is_empty() {
            let badge = container(
                text(self.toggles.updates_badge.clone())
                    .size(9)
                    .color(theme.bg)
                    .font(Font {
                        weight: Weight::Bold,
                        ..Font::DEFAULT
                    }),
            )
            .padding([1, 5])
            .style(move |_| container::Style {
                background: Some(theme.accent.into()),
                border: Border {
                    radius: 7.0.into(),
                    ..Default::default()
                },
                ..Default::default()
            });
            cell = stack![
                cell,
                container(badge)
                    .align_right(Length::Fill)
                    .align_top(Length::Fill)
            ]
            .into();
        }
        let tip = if t.id == "updates" && !self.toggles.updates_tooltip.is_empty() {
            self.toggles.updates_tooltip.clone()
        } else if t.tooltip.is_empty() {
            t.label.clone()
        } else {
            t.tooltip.replace("\\n", "\n")
        };
        Self::hint(cell, tip, theme)
    }

    fn slider_row(
        &self,
        kind: SliderKind,
        glyph: &'static str,
        value: Option<f32>,
        range: (f32, f32),
        step: f32,
    ) -> Element<'static, Message> {
        let theme = self.theme;
        let tint = match kind {
            SliderKind::Volume => theme.accent,
            SliderKind::Brightness => theme.secondary,
            SliderKind::Sunset => theme.tertiary,
        };
        let Some(value) = value else {
            return Space::new().into();
        };
        let clamped = sys::clamp(value, range.0, range.1);
        let span = (range.1 - range.0).max(f32::EPSILON);
        let ratio = ((clamped - range.0) / span).clamp(0.0, 1.0);
        let is_muted_volume = kind == SliderKind::Volume && (value <= 0.0 || self.volume_muted);
        let label_tint = if is_muted_volume {
            theme.muted
        } else {
            slider_icon_color(tint, value)
        };
        let rail_start = Self::mix(saturate(tint, 1.40), theme.bg, 0.40);
        let rail_mid = saturate(tint, 1.25);
        let rail_end = Self::mix(tint, Color::WHITE, 0.42);
        let knob_color = Self::mix(saturate(tint, 1.30), Color::WHITE, 0.08);
        let glyph = match kind {
            SliderKind::Volume if is_muted_volume => "volume-muted",
            SliderKind::Brightness if value <= range.0 => "brightness-low",
            _ => glyph,
        };
        let track_bg = container(Space::new())
            .width(Length::Fill)
            .height(Length::Fixed(16.0))
            .style(move |_| {
                let recess = iced_core::gradient::Linear::new(iced_core::Radians::PI)
                    .add_stop(
                        0.0,
                        Color {
                            a: 0.72,
                            ..Self::mix(theme.bg, Color::BLACK, 0.62)
                        },
                    )
                    .add_stop(
                        1.0,
                        Color {
                            a: 0.52,
                            ..Self::mix(theme.bg, Color::BLACK, 0.35)
                        },
                    );
                container::Style {
                    background: Some(recess.into()),
                    border: Border {
                        radius: 8.0.into(),
                        color: Color::TRANSPARENT,
                        width: 0.0,
                    },
                    ..Default::default()
                }
            });
        let left_portion = (ratio * 1000.0).round() as u16;
        let right_portion = 1000_u16.saturating_sub(left_portion);
        let mut active_fill_row = row![]
            .width(Length::Fill)
            .height(Length::Fixed(24.0))
            .align_y(Vertical::Center);
        if clamped > range.0 {
            // The 20px knob center is at x = 10 + ratio * (W - 20).
            // For a ~169px track, (10 / 169) ≈ 0.059 and (149 / 169) ≈ 0.882.
            // Extending the active bar directly to the knob center ensures the
            // right edge stays completely submerged underneath the solid 20px
            // knob disc, preventing any visible gap or inward-curving crescent.
            let fill_portion = (59.0 + ratio * 882.0).round().clamp(1.0, 1000.0) as u16;
            let empty_portion = 1000_u16.saturating_sub(fill_portion);
            let active_pill = container(Space::new())
                .width(Length::FillPortion(fill_portion))
                .height(Length::Fixed(16.0))
                .style(move |_| {
                    let grad = iced_core::gradient::Linear::new(iced_core::Radians(
                        std::f32::consts::FRAC_PI_2,
                    ))
                    .add_stop(0.0, rail_start)
                    .add_stop(0.48, rail_mid)
                    .add_stop(1.0, rail_end);
                    container::Style {
                        background: Some(grad.into()),
                        border: Border {
                            radius: iced_core::border::Radius {
                                top_left: 8.0,
                                bottom_left: 8.0,
                                top_right: 0.0,
                                bottom_right: 0.0,
                            },
                            ..Default::default()
                        },
                        ..Default::default()
                    }
                });
            active_fill_row = active_fill_row.push(active_pill);
            if empty_portion > 0 {
                active_fill_row =
                    active_fill_row.push(Space::new().width(Length::FillPortion(empty_portion)));
            }
        }
        let knob_head = container(Space::new())
            .width(Length::Fixed(20.0))
            .height(Length::Fixed(20.0))
            .style(move |_| container::Style {
                background: Some(knob_color.into()),
                border: Border {
                    radius: 10.0.into(),
                    color: Color {
                        a: 0.32,
                        ..Self::mix(knob_color, Color::WHITE, 0.45)
                    },
                    width: 1.0,
                },
                shadow: iced_core::Shadow {
                    color: Color::from_rgba(0.0, 0.0, 0.0, 0.75),
                    offset: iced_core::Vector::new(-3.5, 0.5),
                    blur_radius: 5.5,
                },
                ..Default::default()
            });
        let mut knob_row = row![]
            .width(Length::Fill)
            .height(Length::Fixed(24.0))
            .align_y(Vertical::Center);
        if left_portion > 0 {
            knob_row = knob_row.push(Space::new().width(Length::FillPortion(left_portion)));
        }
        knob_row = knob_row.push(
            container(knob_head)
                .width(Length::Fixed(20.0))
                .height(Length::Fixed(24.0))
                .align_y(Vertical::Center),
        );
        if right_portion > 0 {
            knob_row = knob_row.push(Space::new().width(Length::FillPortion(right_portion)));
        }
        let s = slider(range.0..=range.1, clamped, move |v| {
            Message::SliderChanged(kind, sys::snap_to_step(v, range.0, range.1, step))
        })
        .step(step)
        .height(24)
        .style(move |_, _| slider::Style {
            rail: slider::Rail {
                backgrounds: (Color::TRANSPARENT.into(), Color::TRANSPARENT.into()),
                width: 16.0,
                border: Border {
                    radius: 8.0.into(),
                    ..Default::default()
                },
            },
            handle: slider::Handle {
                shape: slider::HandleShape::Circle { radius: 10.0 },
                background: Color::TRANSPARENT.into(),
                border_color: Color::TRANSPARENT,
                border_width: 0.0,
            },
        });
        // Keep `s` as the topmost child in `stack!` so it receives all pointer,
        // drag, and wheel events first while rendering the custom track and
        // 3D shadowed knob directly underneath.
        let slider_stack = container(stack![
            container(track_bg)
                .width(Length::Fill)
                .height(Length::Fixed(24.0))
                .align_y(Vertical::Center),
            active_fill_row,
            knob_row,
            s,
        ])
        .width(Length::Fill);
        // Decrease slider length by ~7% on each side (~14% total) for elegant
        // spacing and center alignment between icon and percentage value.
        let s = container(slider_stack)
            .padding(iced_core::Padding {
                top: 0.0,
                right: 14.0,
                bottom: 0.0,
                left: 14.0,
            })
            .width(Length::Fill);
        let name = match kind {
            SliderKind::Volume => "Volume",
            SliderKind::Brightness => "Brightness",
            SliderKind::Sunset => "Night light",
        };
        let val = text(format!("{value:.0}"))
            .size(12)
            .font(Font::MONOSPACE)
            .color(if is_muted_volume {
                theme.muted
            } else {
                slider_label_color(Self::mix(tint, theme.fg, 0.18), value)
            })
            .width(26)
            .align_x(Horizontal::Right);
        let icon = Self::icon_label(glyph, 17.0, Some(label_tint));
        let icon = if kind == SliderKind::Volume {
            Self::hint(
                mouse_area(
                    button(icon)
                        .padding(0)
                        .on_press(Message::VolumeControl)
                        .style(move |_, status| Self::flat(theme, status)),
                )
                .on_right_press(Message::OutputSelector),
                "Volume\nLMB: Open pavucontrol\nRMB: Select output".into(),
                theme,
            )
        } else {
            Self::hint(icon, name.into(), theme)
        };
        container(row![icon, s, val].spacing(10).align_y(Vertical::Center))
            .padding([6, 10])
            .into()
    }

    fn radio(&self, wifi: bool) -> Element<'static, Message> {
        let theme = self.theme;
        let state = if wifi { self.wifi_on } else { self.bt_on };
        let pending = if wifi {
            self.wifi_pending
        } else {
            self.bt_pending
        };
        let on = state.unwrap_or(false);
        let mut switch = toggler(on)
            .size(22)
            .spacing(0)
            .width(44)
            .style(move |_, status| {
                let disabled = matches!(status, toggler::Status::Disabled { .. });
                let (bg, bg_border_color, bg_border_width, fg, fg_border_color, fg_border_width) =
                    if on && !disabled {
                        (
                            Self::mix(theme.bg, saturate(theme.accent, 1.25), 0.28).into(),
                            Color {
                                a: 0.45,
                                ..saturate(theme.accent, 1.25)
                            },
                            1.5,
                            saturate(theme.accent, 1.35).into(),
                            Color {
                                a: 0.25,
                                ..Color::WHITE
                            },
                            1.0,
                        )
                    } else if disabled {
                        (
                            Color {
                                a: 0.06,
                                ..theme.fg
                            }
                            .into(),
                            Color::TRANSPARENT,
                            0.0,
                            theme.muted.into(),
                            Color::TRANSPARENT,
                            0.0,
                        )
                    } else {
                        (
                            Color {
                                a: 0.08,
                                ..Self::mix(theme.bg, theme.fg, 0.15)
                            }
                            .into(),
                            Color {
                                a: 0.09,
                                ..theme.fg
                            },
                            1.0,
                            Color {
                                a: 0.32,
                                ..theme.fg
                            }
                            .into(),
                            Color::TRANSPARENT,
                            0.0,
                        )
                    };
                toggler::Style {
                    background: bg,
                    foreground: fg,
                    border_radius: Some(11.0.into()),
                    padding_ratio: 0.12,
                    background_border_width: bg_border_width,
                    background_border_color: bg_border_color,
                    foreground_border_width: fg_border_width,
                    foreground_border_color: fg_border_color,
                    text_color: None,
                }
            });
        if state.is_some() && !pending {
            switch = switch.on_toggle(if wifi {
                Message::WifiToggled
            } else {
                Message::BtToggled
            });
        }
        let glyph = icon_glyph(if wifi {
            "network-wireless-symbolic"
        } else {
            "bluetooth-active-symbolic"
        });
        let label = format!(
            "{}: {}",
            if wifi { "Wi-Fi" } else { "Bluetooth" },
            if pending {
                "applying…"
            } else if state.is_none() {
                "unavailable"
            } else if on {
                "on"
            } else {
                "off"
            }
        );
        let icon_button = button(Self::icon_label(
            glyph,
            16.0,
            Some(if on {
                saturate(theme.accent, 1.25)
            } else {
                theme.muted
            }),
        ))
        .padding(0)
        .on_press(if wifi {
            Message::WifiManager
        } else {
            Message::BluetoothManager
        })
        .style(move |_, status| Self::flat(theme, status));
        let mut icon_area = mouse_area(icon_button);
        if !wifi {
            icon_area = icon_area.on_right_press(Message::BluetoothRecovery);
        }
        let icon = Self::hint(
            icon_area,
            if wifi {
                "Wi-Fi\nLMB: Open Network Manager"
            } else {
                "Bluetooth\nLMB: Open Blueman\nRMB: Recover Bluetooth"
            }
            .into(),
            theme,
        );
        row![icon, Self::hint(switch, label, theme)]
            .spacing(6)
            .align_y(Vertical::Center)
            .into()
    }

    pub fn view(&self) -> Element<'_, Message> {
        let size = self
            .panel_monitor
            .map(|m| m.size)
            .unwrap_or_else(|| iced_core::Size::new(1920.0, 1080.0));
        self.view_at(size)
    }

    fn view_at(&self, size: iced_core::Size) -> Element<'_, Message> {
        let theme = self.theme;
        // Shrink-wrap the clock target in the center so hovering side margins
        // never triggers the calendar or captures panel wheel scrolling.
        let clock_button = button(
            column![
                crate::appearance::glow(
                    text(self.time_text.clone())
                        .size(38)
                        .font(Font {
                            weight: Weight::ExtraBold,
                            ..Font::DEFAULT
                        })
                        .color(theme.fg)
                        .into(),
                    0.242
                ),
                crate::appearance::glow(
                    text(self.date_text.clone())
                        .size(12)
                        .color(theme.accent)
                        .into(),
                    0.242
                )
            ]
            .spacing(2)
            .align_x(Horizontal::Center),
        )
        .padding([0, 6])
        .on_press(Message::ClockPressed)
        .style(move |_, _| button::Style {
            text_color: theme.fg,
            ..Default::default()
        });
        let clock = container(Self::hint_with(
            mouse_area(clock_button)
                .on_scroll(Message::CalendarScrolled)
                .on_exit(Message::CalendarHoverExited),
            Self::render_calendar_tooltip(self.today, self.calendar_month_offset, theme),
            tooltip::Position::Bottom,
            12.0,
            theme,
        ))
        .center_x(Length::Fill);
        let weather: Element<'_, Message> = if self.config.layout.show_weather
            && let Some(w) = &self.weather
        {
            let pill_color = if w.is_offline() {
                Self::mix(theme.fg, theme.muted, 0.45)
            } else {
                theme.fg
            };
            Self::hint_with(
                button(crate::appearance::glow(
                    row![
                        Self::icon_label("☁︎", 16.0, Some(pill_color)),
                        text(w.temperature.clone()).size(11).color(pill_color)
                    ]
                    .spacing(5)
                    .align_y(Vertical::Center)
                    .into(),
                    0.242,
                ))
                .padding([2, 5])
                .on_press(Message::WeatherPressed)
                .style(move |_, status| Self::flat(theme, status)),
                Self::render_weather_tooltip(w, theme),
                tooltip::Position::Bottom,
                12.0,
                theme,
            )
        } else {
            Space::new().into()
        };
        let power = button(
            container(crate::appearance::glow(
                Self::icon_label("⏻︎", 17.0, None),
                0.242,
            ))
            .center_x(36)
            .center_y(36),
        )
        .padding(0)
        .on_press(Message::PowerButton)
        .style(move |_, status| button::Style {
            background: Some(
                if status == button::Status::Hovered {
                    Self::mix(Color::from_rgb8(129, 40, 36), Color::WHITE, 0.10)
                } else {
                    Color::from_rgb8(129, 40, 36)
                }
                .into(),
            ),
            text_color: Color::WHITE,
            shadow: iced_core::Shadow {
                color: Color::from_rgba(129.0 / 255.0, 40.0 / 255.0, 36.0 / 255.0, 0.198),
                blur_radius: 6.5,
                ..Default::default()
            },
            border: Border {
                radius: 18.0.into(),
                ..Default::default()
            },
            ..Default::default()
        });
        let side_controls = row![
            container(weather).width(Length::Fill).padding(0),
            Self::hint(power, "Power menu".into(), theme)
        ]
        .height(50)
        .align_y(Vertical::Center);
        let header = stack![clock, side_controls];
        let mut controls = column![header].spacing(12);
        if self.config.layout.show_metrics {
            let (first_value, first_color, first_ring, first_hint) = self.first_metric_display();
            controls = controls.push(
                row![
                    self.metric("⇅", first_value, first_color, first_ring, "net", first_hint),
                    self.metric(
                        "▤",
                        self.ram.text.clone(),
                        theme.fg,
                        None,
                        "ram",
                        self.ram.tooltip.clone()
                    ),
                    self.metric(
                        "▣",
                        self.cpu.text.clone(),
                        theme.fg,
                        None,
                        "cpu",
                        self.cpu.tooltip.clone()
                    )
                ]
                .spacing(10),
            );
        }
        if self.config.layout.show_quick_toggles && !self.config.toggles.is_empty() {
            let mut grid = column![].spacing(10);
            for toggles in self.config.toggles.chunks(5) {
                let mut r = row![].spacing(0);
                for t in toggles {
                    r = r.push(container(self.toggle_button(t)).center_x(Length::Fill));
                }
                for _ in toggles.len()..5 {
                    r = r.push(Space::new().width(Length::Fill));
                }
                grid = grid.push(r);
            }
            controls = controls.push(grid);
        }
        if self.config.layout.show_power_profiles {
            let mut profiles = row![].spacing(4);
            for ((profile, name, glyph), tint) in [
                ("power-saver", "Power saver\nRMB: Powertop auto-tune", ""),
                ("balanced", "Balanced", "󰀄"),
                ("performance", "Performance", ""),
            ]
            .into_iter()
            .zip(theme.profiles.map(|tint| saturate(tint, 0.8)))
            {
                let selected = self
                    .power_pending
                    .as_deref()
                    .or(self.power_profile.as_deref())
                    == Some(profile);
                let btn = button(
                    container(Self::icon_label(glyph, 16.0, None))
                        .center_x(30)
                        .center_y(30),
                )
                .padding(0)
                .on_press(Message::PowerSelected(profile.into()))
                .style(move |_, status| {
                    let mut style = Self::flat(theme, status);
                    style.text_color = tint;
                    if selected {
                        style.background = Some(
                            Color {
                                a: 0.16,
                                ..theme.accent
                            }
                            .into(),
                        );
                    }
                    style.border = Border {
                        radius: 15.0.into(),
                        color: if selected {
                            theme.accent
                        } else {
                            Color::TRANSPARENT
                        },
                        width: 1.5,
                    };
                    style
                });
                let cell: Element<'_, Message> = if profile == "power-saver" {
                    mouse_area(btn).on_right_press(Message::Powertop).into()
                } else {
                    btn.into()
                };
                profiles = profiles.push(Self::hint(cell, name.into(), theme));
            }
            controls = controls.push(
                container(
                    row![
                        self.radio(true),
                        Space::new().width(12),
                        self.radio(false),
                        Space::new().width(Length::Fill),
                        profiles
                    ]
                    .align_y(Vertical::Center),
                )
                .padding([7, 10])
                .style(move |_| Self::card(theme, 14.0)),
            );
        }
        if self.config.layout.show_sliders
            && (self.volume.is_some() || self.brightness.is_some() || self.sunset_available)
        {
            let mut sliders = column![].spacing(4);
            if self.volume.is_some() {
                sliders = sliders.push(self.slider_row(
                    SliderKind::Volume,
                    "󰕾",
                    self.volume,
                    (0.0, 100.0),
                    1.0,
                ));
            }
            if self.brightness.is_some() {
                sliders = sliders.push(self.slider_row(
                    SliderKind::Brightness,
                    "󰃠",
                    self.brightness,
                    (1.0, 100.0),
                    1.0,
                ));
            }
            if self.sunset_available {
                sliders = sliders.push(self.slider_row(
                    SliderKind::Sunset,
                    "󰡬",
                    self.sunset,
                    (0.0, 100.0),
                    1.0,
                ));
            }
            controls = controls.push(
                container(sliders)
                    .padding(6)
                    .style(move |_| Self::card(theme, 16.0)),
            );
        }
        let mut content = column![controls].spacing(18);
        if self.config.layout.show_notifications {
            let dnd = self.dnd.unwrap_or(false);
            let bell = button(
                container(Self::icon_label(
                    icon_glyph(if dnd {
                        "notifications-disabled-symbolic"
                    } else {
                        "notification-symbolic"
                    }),
                    16.0,
                    None,
                ))
                .center_x(28)
                .center_y(28),
            )
            .padding(0)
            .on_press(Message::DndToggled)
            .style(move |_, status| {
                let mut s = Self::flat(theme, status);
                if dnd {
                    s.text_color = theme.accent;
                    s.background = Some(
                        Color {
                            a: 0.12,
                            ..theme.accent
                        }
                        .into(),
                    );
                }
                s
            });
            let has_notifs = !self.notifications.is_empty();
            let mut clear = button(
                container(Self::icon_label(
                    icon_glyph("edit-clear-all-symbolic"),
                    15.0,
                    None,
                ))
                .center_x(28)
                .center_y(28),
            )
            .padding(0)
            .style(move |_, s| {
                let mut style = Self::flat(theme, s);
                if !has_notifs {
                    style.text_color = Color {
                        a: 0.35,
                        ..theme.muted
                    };
                }
                style
            });
            if has_notifs {
                clear = clear.on_press(Message::ClearNotifs);
            }
            let head = row![
                text("Notifications").size(14).color(theme.fg).font(Font {
                    weight: Weight::Bold,
                    ..Font::DEFAULT
                }),
                Space::new().width(Length::Fill),
                Self::hint(
                    bell,
                    if dnd {
                        "Disable Do Not Disturb"
                    } else {
                        "Enable Do Not Disturb"
                    }
                    .into(),
                    theme
                ),
                Self::hint(clear, "Clear notifications".into(), theme)
            ]
            .spacing(6)
            .align_y(Vertical::Center);
            let mut notifs = column![head].spacing(10);
            if self.notifications.is_empty() {
                notifs = notifs.push(
                    container(
                        text(if !self.notifications_loaded {
                            "Loading notifications…"
                        } else if dnd {
                            "Do Not Disturb is on"
                        } else {
                            "You're all caught up"
                        })
                        .size(12)
                        .color(theme.muted),
                    )
                    .height(56)
                    .align_y(Vertical::Center)
                    .center_x(Length::Fill),
                );
            } else {
                let mut list = column![].spacing(8);
                for (app, items) in notify::group_notifications(&self.notifications) {
                    if items.len() > 1 {
                        if self.expanded.contains(app) {
                            list = list.push(self.expanded_group_card(theme, app, &items));
                        } else {
                            list = list.push(self.collapsed_group_card(theme, app, &items));
                        }
                    } else {
                        for n in items {
                            list = list.push(self.notif_row(theme, n));
                        }
                    }
                }
                notifs = notifs.push(list);
            }
            content = content.push(notifs);
        }
        if let Some(error) = &self.error {
            content = content.push(text(error.clone()).size(11).color(theme.danger));
        }
        let bg_alpha = if self.global_blur_enabled { 0.72 } else { 1.0 };
        let panel_bg = Color {
            a: bg_alpha,
            ..Self::mix(theme.bg, theme.fg, 0.02)
        };
        // Vertical padding belongs to the content, so it scrolls away instead
        // of forming fixed strips at the viewport's top and bottom edges.
        let scroller = scrollable(container(content).padding(iced_core::Padding {
            top: 12.0,
            right: 0.0,
            bottom: 44.0,
            left: 0.0,
        }))
        .direction(scrollable::Direction::Vertical(
            scrollable::Scrollbar::new().width(0).scroller_width(0),
        ));
        let body: Element<'_, Message> =
            if self.config.layout.show_notifications && !self.notifications.is_empty() {
                let fade_target = Self::mix(panel_bg, Color::BLACK, 0.18);
                let fade_strip = container(Space::new())
                    .width(Length::Fill)
                    .height(Length::Fixed(44.0))
                    .style(move |_| {
                        let grad = iced_core::gradient::Linear::new(iced_core::Radians::PI)
                            .add_stop(0.0, Color::TRANSPARENT)
                            .add_stop(
                                0.35,
                                Color {
                                    a: bg_alpha * 0.35,
                                    ..Self::mix(panel_bg, Color::BLACK, 0.08)
                                },
                            )
                            .add_stop(
                                0.70,
                                Color {
                                    a: bg_alpha * 0.75,
                                    ..Self::mix(panel_bg, Color::BLACK, 0.14)
                                },
                            )
                            .add_stop(
                                1.0,
                                Color {
                                    a: bg_alpha * 0.95,
                                    ..fade_target
                                },
                            );
                        container::Style {
                            background: Some(grad.into()),
                            border: Border {
                                radius: iced_core::border::Radius {
                                    top_left: 0.0,
                                    top_right: 0.0,
                                    bottom_right: 20.0,
                                    bottom_left: 20.0,
                                },
                                ..Default::default()
                            },
                            ..Default::default()
                        }
                    });
                let bottom_fade = container(fade_strip)
                    .width(Length::Fill)
                    .align_bottom(Length::Fill);
                stack![container(scroller).padding([0, 12]), bottom_fade].into()
            } else {
                container(scroller).padding([0, 12]).into()
            };
        let limit = self.panel_monitor.map(|m| m.limit()).unwrap_or_else(|| {
            iced_core::Size::new(
                320.0_f32.min((size.width - 40.0).max(1.0)),
                (size.height * 0.85).min(size.height - 40.0).max(1.0),
            )
        });
        let panel = container(body)
            .width(Length::Fixed(limit.width))
            .max_height(limit.height)
            .style(move |_| {
                // Outside pixels must stay transparent: a rendered shadow also
                // extends the compositor's background blur beyond the border.
                container::Style {
                    background: Some(panel_bg.into()),
                    border: Border {
                        radius: 20.0.into(),
                        color: Color {
                            a: 0.08,
                            ..Self::mix(theme.bg, theme.fg, 0.20)
                        },
                        width: 1.0,
                    },
                    ..Default::default()
                }
            });
        crate::appearance::fit_window(
            crate::appearance::reveal(panel.into(), self.panel_opacity()),
            limit,
            Message::PanelSize,
        )
    }

    fn panel_opacity(&self) -> f32 {
        if self.global_blur_enabled {
            self.appearance.opacity
        } else {
            1.0
        }
    }

    fn notification_button(theme: AppTheme, status: button::Status) -> button::Style {
        button::Style {
            text_color: if status == button::Status::Hovered {
                theme.fg
            } else {
                theme.muted
            },
            ..Default::default()
        }
    }

    fn truncate_preview(input: &str, max_chars: usize) -> String {
        let mut chars = input.chars();
        let prefix: String = chars.by_ref().take(max_chars).collect();
        if chars.next().is_some() {
            format!("{}…", prefix.trim_end())
        } else {
            prefix
        }
    }

    fn group_count_pill(
        theme: AppTheme,
        count: usize,
        expanded: bool,
    ) -> Element<'static, Message> {
        let pill_bg = if expanded {
            Color {
                a: 0.14,
                ..theme.accent
            }
        } else {
            Color {
                a: 0.08,
                ..theme.fg
            }
        };
        let pill_fg = if expanded { theme.accent } else { theme.fg };

        container(
            text(count.to_string())
                .size(10)
                .font(Font {
                    weight: Weight::Bold,
                    ..Font::DEFAULT
                })
                .color(pill_fg),
        )
        .padding([1, 6])
        .style(move |_| container::Style {
            background: Some(pill_bg.into()),
            border: Border {
                radius: 9.0.into(),
                ..Default::default()
            },
            ..Default::default()
        })
        .into()
    }

    fn collapsed_group_card(
        &self,
        theme: AppTheme,
        app: &str,
        items: &[&Notification],
    ) -> Element<'static, Message> {
        let key = format!("stack:{app}");
        let hovered = self.hovered_notification.as_ref() == Some(&key);
        let top_row = row![
            Self::icon_label(icon_glyph("pan-end-symbolic"), 11.0, Some(theme.muted)),
            text(app.to_uppercase())
                .size(11)
                .color(theme.accent)
                .font(Font {
                    weight: Weight::Bold,
                    ..Font::DEFAULT
                })
                .width(Length::Fill),
            Self::group_count_pill(theme, items.len(), false),
        ]
        .spacing(6)
        .align_y(Vertical::Center);

        let preview = items
            .first()
            .map(|n| Self::truncate_preview(&n.summary, 42))
            .unwrap_or_default();
        let body: Element<'static, Message> = if preview.is_empty() {
            top_row.into()
        } else {
            column![top_row, text(preview).size(11).color(theme.muted)]
                .spacing(4)
                .into()
        };

        let expand = button(body)
            .width(Length::Fill)
            .padding(iced_core::Padding {
                top: 9.0,
                right: 4.0,
                bottom: 9.0,
                left: 12.0,
            })
            .on_press(Message::StackToggled(app.to_owned()))
            .style(move |_, s| Self::notification_button(theme, s));

        let close = Self::close_button(theme, Message::StackClosed(app.to_owned()));
        let card_content = row![
            expand,
            container(close).padding(iced_core::Padding {
                top: 6.0,
                right: 8.0,
                ..Default::default()
            }),
        ]
        .align_y(Vertical::Top);

        let base = Self::mix(theme.bg, theme.fg, 0.085);
        mouse_area(container(card_content).style(move |_| {
            container::Style {
                background: Some(
                    Color {
                        a: if hovered { 0.60 } else { 0.54 },
                        ..Self::mix(base, theme.accent, if hovered { 0.07 } else { 0.04 })
                    }
                    .into(),
                ),
                border: Border {
                    radius: 12.0.into(),
                    color: Color {
                        a: if hovered { 0.13 } else { 0.08 },
                        ..theme.fg
                    },
                    width: 1.0,
                },
                shadow: iced_core::Shadow {
                    color: Color::from_rgba(0.0, 0.0, 0.0, 0.32),
                    offset: iced_core::Vector::new(0.0, 2.0),
                    blur_radius: 8.0,
                },
                ..Default::default()
            }
        }))
        .on_enter(Message::NotificationHovered(key.clone()))
        .on_exit(Message::NotificationLeft(key))
        .into()
    }

    fn expanded_group_card(
        &self,
        theme: AppTheme,
        app: &str,
        items: &[&Notification],
    ) -> Element<'static, Message> {
        let key = format!("stack:{app}");
        let header_hovered = self.hovered_notification.as_ref() == Some(&key);
        let top_row = row![
            Self::icon_label(icon_glyph("pan-down-symbolic"), 11.0, Some(theme.accent)),
            text(app.to_uppercase())
                .size(11)
                .color(theme.accent)
                .font(Font {
                    weight: Weight::Bold,
                    ..Font::DEFAULT
                })
                .width(Length::Fill),
            Self::group_count_pill(theme, items.len(), true),
        ]
        .spacing(6)
        .align_y(Vertical::Center);

        let expand = button(top_row)
            .width(Length::Fill)
            .padding(iced_core::Padding {
                top: 9.0,
                right: 4.0,
                bottom: 9.0,
                left: 12.0,
            })
            .on_press(Message::StackToggled(app.to_owned()))
            .style(move |_, s| Self::notification_button(theme, s));

        let close = Self::close_button(theme, Message::StackClosed(app.to_owned()));
        let header_row = row![
            expand,
            container(close).padding(iced_core::Padding {
                right: 8.0,
                ..Default::default()
            }),
        ]
        .align_y(Vertical::Center);

        let base = Self::mix(theme.bg, theme.fg, 0.085);
        let header = mouse_area(container(header_row).style(move |_| {
            container::Style {
                background: Some(
                    Color {
                        a: if header_hovered { 0.66 } else { 0.60 },
                        ..Self::mix(base, theme.accent, if header_hovered { 0.10 } else { 0.07 })
                    }
                    .into(),
                ),
                border: Border {
                    radius: iced_core::border::Radius {
                        top_left: 11.0,
                        top_right: 11.0,
                        bottom_right: 0.0,
                        bottom_left: 0.0,
                    },
                    ..Default::default()
                },
                ..Default::default()
            }
        }))
        .on_enter(Message::NotificationHovered(key.clone()))
        .on_exit(Message::NotificationLeft(key));

        let header_sep = container(Space::new())
            .width(Length::Fill)
            .height(Length::Fixed(1.0))
            .style(move |_| container::Style {
                background: Some(
                    Color {
                        a: 0.07,
                        ..theme.fg
                    }
                    .into(),
                ),
                ..Default::default()
            });

        let drawer_bg = Color {
            a: 0.38,
            ..Self::mix(theme.bg, theme.fg, 0.03)
        };
        let mut accordion = column![header, header_sep];
        let last_idx = items.len().saturating_sub(1);
        for (idx, n) in items.iter().enumerate() {
            if idx > 0 {
                let row_sep = container(
                    container(Space::new())
                        .width(Length::Fill)
                        .height(Length::Fixed(1.0))
                        .style(move |_| container::Style {
                            background: Some(
                                Color {
                                    a: 0.05,
                                    ..theme.fg
                                }
                                .into(),
                            ),
                            ..Default::default()
                        }),
                )
                .padding([0, 10])
                .width(Length::Fill)
                .style(move |_| container::Style {
                    background: Some(drawer_bg.into()),
                    ..Default::default()
                });
                accordion = accordion.push(row_sep);
            }
            accordion = accordion.push(self.accordion_child_row(theme, n, idx == last_idx));
        }

        container(accordion)
            .padding(1)
            .width(Length::Fill)
            .style(move |_| container::Style {
                border: Border {
                    radius: 12.0.into(),
                    color: Color {
                        a: if header_hovered { 0.22 } else { 0.16 },
                        ..theme.accent
                    },
                    width: 1.0,
                },
                shadow: iced_core::Shadow {
                    color: Color::from_rgba(0.0, 0.0, 0.0, 0.30),
                    offset: iced_core::Vector::new(0.0, 2.0),
                    blur_radius: 8.0,
                },
                ..Default::default()
            })
            .into()
    }

    fn accordion_child_row(
        &self,
        theme: AppTheme,
        n: &Notification,
        is_last: bool,
    ) -> Element<'static, Message> {
        let key = format!("item:{}", n.id);
        let hovered = self.hovered_notification.as_ref() == Some(&key);
        let mut lines = column![text(n.summary.clone()).size(12).color(theme.fg)].spacing(3);
        if !n.body.is_empty() {
            lines = lines.push(
                text(n.body.chars().take(160).collect::<String>())
                    .size(11)
                    .color(theme.muted),
            );
        }
        let open = button(lines)
            .padding(iced_core::Padding {
                top: 9.0,
                right: 8.0,
                bottom: 9.0,
                left: 12.0,
            })
            .width(Length::Fill)
            .on_press(Message::NotifInvoke(n.id))
            .style(move |_, s| Self::notification_button(theme, s));
        let mut actions = column![].spacing(3).align_x(Horizontal::Right);
        if !n.time.is_empty() {
            actions = actions.push(text(n.time.clone()).size(10).color(theme.muted));
        }
        actions = actions.push(Self::close_button(theme, Message::NotifDismiss(n.id)));
        let content = row![
            open,
            container(actions).padding(iced_core::Padding {
                top: 6.0,
                right: 8.0,
                ..Default::default()
            })
        ]
        .align_y(Vertical::Top);

        let radius = if is_last {
            iced_core::border::Radius {
                top_left: 0.0,
                top_right: 0.0,
                bottom_right: 11.0,
                bottom_left: 11.0,
            }
        } else {
            0.0.into()
        };
        mouse_area(container(content).style(move |_| {
            container::Style {
                background: Some(
                    Color {
                        a: if hovered { 0.48 } else { 0.38 },
                        ..Self::mix(theme.bg, theme.fg, if hovered { 0.10 } else { 0.03 })
                    }
                    .into(),
                ),
                border: Border {
                    radius,
                    ..Default::default()
                },
                ..Default::default()
            }
        }))
        .on_enter(Message::NotificationHovered(key.clone()))
        .on_exit(Message::NotificationLeft(key))
        .into()
    }

    fn notification_card(
        &self,
        content: Element<'static, Message>,
        key: String,
    ) -> Element<'static, Message> {
        let theme = self.theme;
        let hovered = self.hovered_notification.as_ref() == Some(&key);
        mouse_area(container(content).style(move |_| {
            let mut style = Self::card(theme, 12.0);
            if hovered {
                style.background = Some(Self::surface(theme, 0.12).into());
            }
            style
        }))
        .on_enter(Message::NotificationHovered(key.clone()))
        .on_exit(Message::NotificationLeft(key))
        .into()
    }

    fn close_button(theme: AppTheme, message: Message) -> Element<'static, Message> {
        button(
            container(Self::icon_label(
                icon_glyph("window-close-symbolic"),
                12.0,
                None,
            ))
            .center_x(24)
            .center_y(24),
        )
        .padding(0)
        .on_press(message)
        .style(move |_, status| {
            let mut style = Self::notification_button(theme, status);
            if matches!(status, button::Status::Hovered | button::Status::Pressed) {
                style.background = Some(
                    Color {
                        a: 0.20,
                        ..theme.danger
                    }
                    .into(),
                );
                style.text_color = theme.danger;
                style.border.radius = 7.0.into();
            }
            style
        })
        .into()
    }

    fn notif_row(&self, theme: AppTheme, n: &Notification) -> Element<'static, Message> {
        let mut lines = column![
            text(n.app.to_uppercase()).size(10).color(theme.accent),
            text(n.summary.clone()).size(12).color(theme.fg),
        ]
        .spacing(3);
        if !n.body.is_empty() {
            lines = lines.push(
                text(n.body.chars().take(160).collect::<String>())
                    .size(11)
                    .color(theme.muted),
            );
        }
        let open = button(lines)
            .padding(12)
            .width(Length::Fill)
            .on_press(Message::NotifInvoke(n.id))
            .style(move |_, s| Self::notification_button(theme, s));
        let mut actions = column![].spacing(3).align_x(Horizontal::Right);
        if !n.time.is_empty() {
            actions = actions.push(text(n.time.clone()).size(10).color(theme.muted));
        }
        actions = actions.push(Self::close_button(theme, Message::NotifDismiss(n.id)));
        self.notification_card(
            row![
                open,
                container(actions).padding(iced_core::Padding {
                    top: 8.0,
                    right: 8.0,
                    ..Default::default()
                })
            ]
            .align_y(Vertical::Top)
            .into(),
            format!("item:{}", n.id),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn app() -> TrayApp {
        TrayApp::state(AppConfig::parse(AppConfig::default_toml()))
    }
    fn window_app() -> TrayApp {
        let mut app = app();
        app.panel_monitor = Some(sys::PanelMonitor {
            x: 0.0,
            y: 0.0,
            size: iced_core::Size::new(1280.0, 720.0),
        });
        app.window_id = Some(iced_core::window::Id::unique());
        app
    }

    #[test]
    fn global_blur_switch_restores_opacity_and_failed_queries_retain_state() {
        let mut app = app();
        app.appearance.opacity = 0.42;
        assert_eq!(app.panel_opacity(), 1.0);
        let _ = app.update(Message::GlobalBlurLoaded(Some(true)));
        assert_eq!(app.panel_opacity(), 0.42);
        let _ = app.update(Message::GlobalBlurLoaded(None));
        assert_eq!(app.panel_opacity(), 0.42);
        let _ = app.update(Message::GlobalBlurLoaded(Some(false)));
        assert_eq!(app.panel_opacity(), 1.0);
        app.appearance.opacity = 0.61;
        let _ = app.update(Message::GlobalBlurLoaded(None));
        assert_eq!(app.panel_opacity(), 1.0);
        let _ = app.update(Message::GlobalBlurLoaded(Some(true)));
        assert_eq!(app.panel_opacity(), 0.61);
    }

    #[test]
    #[ignore = "requires a working Vulkan or Wayland EGL adapter"]
    fn scroll_viewport_reaches_both_panel_edges_and_final_item_is_visible() {
        use iced_core::renderer::Headless as _;
        use iced_core::{Layout, Point, Rectangle, Size, Vector, layout, widget};
        use widget::operation::{Operation, Scrollable};
        let renderer = iced_futures::futures::executor::block_on(crate::renderer::Renderer::new(
            Font::DEFAULT,
            iced_core::Pixels(16.0),
            None,
        ))
        .expect("a render adapter is required for this layout test");
        let mut app = window_app();
        app.notifications = (0..20)
            .map(|id| Notification {
                id,
                app: format!("App {id}"),
                summary: format!("Notification {id}"),
                body: "Notification body".into(),
                source: "active".into(),
                desktop_entry: String::new(),
                time: String::new(),
            })
            .collect();
        let mut view = app.view_at(Size::new(1280.0, 720.0));
        let mut tree = widget::Tree::new(&view);
        let node = view.as_widget_mut().layout(
            &mut tree,
            &renderer,
            &layout::Limits::new(Size::ZERO, Size::new(320.0, 612.0)),
        );
        #[derive(Default)]
        struct Probe {
            viewport: Option<Rectangle>,
            translation: Vector,
            last_text: Option<Rectangle>,
        }
        impl Operation for Probe {
            fn traverse(&mut self, operate: &mut dyn FnMut(&mut dyn Operation)) {
                operate(self);
            }
            fn scrollable(
                &mut self,
                _: Option<&widget::Id>,
                bounds: Rectangle,
                content: Rectangle,
                translation: Vector,
                _: &mut dyn Scrollable,
            ) {
                assert!(content.height > bounds.height, "fixture must overflow");
                self.viewport = Some(bounds);
                self.translation = translation;
            }
            fn text(&mut self, _: Option<&widget::Id>, bounds: Rectangle, caption: &str) {
                if caption == "Notification body" {
                    self.last_text = Some(bounds);
                }
            }
        }
        let mut probe = Probe::default();
        view.as_widget_mut()
            .operate(&mut tree, Layout::new(&node), &renderer, &mut probe);
        assert_eq!(probe.translation.y, 0.0);
        let mut messages = Vec::new();
        let mut shell = iced_core::Shell::new(&mut messages);
        view.as_widget_mut().update(
            &mut tree,
            &Event::Mouse(iced_core::mouse::Event::WheelScrolled {
                delta: iced_core::mouse::ScrollDelta::Pixels {
                    x: 0.0,
                    y: -10000.0,
                },
            }),
            Layout::new(&node),
            iced_core::mouse::Cursor::Available(Point::new(160.0, 300.0)),
            &renderer,
            &mut iced_core::clipboard::Null,
            &mut shell,
            &Rectangle::with_size(node.size()),
        );
        view.as_widget_mut()
            .operate(&mut tree, Layout::new(&node), &renderer, &mut probe);
        assert!(
            probe.translation.y > 0.0,
            "wheel input must scroll the list"
        );
        let viewport = probe.viewport.unwrap();
        assert_eq!(viewport.y, 0.0);
        assert_eq!(viewport.height, node.size().height);
        assert_eq!(viewport.x, 12.0);
        assert_eq!(viewport.width, node.size().width - 24.0);
        let last = probe.last_text.unwrap();
        let visible_y = last.y - probe.translation.y;
        assert!(visible_y >= viewport.y);
        assert!(visible_y + last.height <= viewport.y + viewport.height);
        assert_eq!(node.bounds().position(), Point::ORIGIN);
    }

    #[test]
    #[ignore = "requires a working Vulkan or Wayland EGL adapter"]
    fn bluetooth_icon_mouse_buttons_remain_independent_under_repeated_clicks() {
        use iced_core::renderer::Headless as _;
        use iced_core::{Layout, Point, Rectangle, Size, layout, widget};
        let renderer = iced_futures::futures::executor::block_on(crate::renderer::Renderer::new(
            Font::DEFAULT,
            iced_core::Pixels(16.0),
            None,
        ))
        .expect("a render adapter is required");
        let app = app();
        for wifi in [false, true] {
            let mut view = app.radio(wifi);
            let mut tree = widget::Tree::new(&view);
            let node = view.as_widget_mut().layout(
                &mut tree,
                &renderer,
                &layout::Limits::new(Size::ZERO, Size::new(100.0, 40.0)),
            );
            let mut messages = Vec::new();
            for _ in 0..1000 {
                for button in [
                    iced_core::mouse::Button::Right,
                    iced_core::mouse::Button::Left,
                ] {
                    for event in [
                        iced_core::mouse::Event::ButtonPressed(button),
                        iced_core::mouse::Event::ButtonReleased(button),
                    ] {
                        let mut shell = iced_core::Shell::new(&mut messages);
                        view.as_widget_mut().update(
                            &mut tree,
                            &Event::Mouse(event),
                            Layout::new(&node),
                            iced_core::mouse::Cursor::Available(Point::new(
                                8.0,
                                node.size().height / 2.0,
                            )),
                            &renderer,
                            &mut iced_core::clipboard::Null,
                            &mut shell,
                            &Rectangle::with_size(node.size()),
                        );
                    }
                }
            }
            if wifi {
                assert_eq!(messages.len(), 1000);
                assert!(messages.iter().all(|m| matches!(m, Message::WifiManager)));
            } else {
                assert_eq!(messages.len(), 2000);
                assert_eq!(
                    messages
                        .iter()
                        .filter(|m| matches!(m, Message::BluetoothRecovery))
                        .count(),
                    1000
                );
                assert_eq!(
                    messages
                        .iter()
                        .filter(|m| matches!(m, Message::BluetoothManager))
                        .count(),
                    1000
                );
            }
        }
    }

    #[test]
    fn panel_resize_coalesces_changes_until_current_request_finishes() {
        let mut app = window_app();
        let first = iced_core::Size::new(320.0, 361.0);
        let latest = iced_core::Size::new(320.0, 612.0);
        assert!(iced_runtime::task::into_stream(app.update(Message::PanelSize(first))).is_some());
        for size in [iced_core::Size::new(320.0, 501.0), latest] {
            assert!(
                iced_runtime::task::into_stream(app.update(Message::PanelSize(size))).is_none()
            );
        }
        assert!(app.panel_fit_busy);
        assert!(
            iced_runtime::task::into_stream(app.update(Message::PanelFitted(first, true)))
                .is_some()
        );
        assert_eq!(app.panel_size, Some(latest));
        assert_eq!(app.panel_applied_size, Some(first));
        assert!(app.panel_fit_busy);
        assert!(
            iced_runtime::task::into_stream(app.update(Message::PanelFitted(latest, true)))
                .is_none()
        );
        assert_eq!(app.panel_applied_size, Some(latest));
        assert!(!app.panel_fit_busy);
        assert!(iced_runtime::task::into_stream(app.fit_panel()).is_none());
    }

    #[test]
    fn failed_panel_resize_retries_on_tick_even_during_refresh() {
        let mut app = window_app();
        let size = iced_core::Size::new(320.0, 612.0);
        let _ = app.update(Message::PanelSize(size));
        assert!(
            iced_runtime::task::into_stream(app.update(Message::PanelFitted(size, false)))
                .is_none()
        );
        assert!(!app.panel_fit_busy);
        assert_eq!(app.panel_applied_size, None);
        app.refresh_pending = RefreshKind::ALL.into_iter().collect();
        assert!(iced_runtime::task::into_stream(app.update(Message::Tick)).is_some());
        assert!(app.panel_fit_busy);
        assert_eq!(app.refresh_pending.len(), RefreshKind::ALL.len());
        let _ = app.update(Message::PanelFitted(size, true));
        let _ = app.update(Message::Tick);
        assert!(!app.panel_fit_busy);
        assert!(iced_runtime::task::into_stream(app.fit_panel()).is_none());
    }

    #[test]
    fn panel_resize_restores_previous_size_if_content_changes_back_in_flight() {
        let mut app = window_app();
        let small = iced_core::Size::new(320.0, 361.0);
        let large = iced_core::Size::new(320.0, 612.0);
        let _ = app.update(Message::PanelSize(small));
        let _ = app.update(Message::PanelFitted(small, true));
        let _ = app.update(Message::PanelSize(large));
        assert!(iced_runtime::task::into_stream(app.update(Message::PanelSize(small))).is_none());
        assert!(
            iced_runtime::task::into_stream(app.update(Message::PanelFitted(large, true)))
                .is_some()
        );
        assert!(app.panel_fit_busy);
        let _ = app.update(Message::PanelFitted(small, true));
        assert_eq!(app.panel_applied_size, Some(small));
    }

    #[test]
    fn panel_is_visible_while_initial_queries_are_pending() {
        let mut app = app();
        app.reserve_refresh();
        assert_ne!(app.time_text, "--:--");
        let _ = app.update(Message::Tick);
        assert_eq!(app.refresh_pending.len(), RefreshKind::ALL.len());
        assert!(!app.notifications_loaded);
        let view = app.view_at(iced_core::Size::new(1920.0, 1080.0));
        assert!(!view.as_widget().children().is_empty());
        drop(view);
        let _ = app.update(Message::NotifsRefreshed(0, (vec![], None)));
        assert!(app.notifications_loaded);
        assert_eq!(app.refresh_pending.len(), RefreshKind::ALL.len() - 1);
    }

    #[test]
    fn rapid_drag_keeps_only_latest_pending_value() {
        let mut app = app();
        for value in [20.0, 35.0, 70.0] {
            let _ = app.update(Message::SliderChanged(SliderKind::Volume, value));
        }
        assert_eq!(app.volume, Some(70.0));
        assert_eq!(app.slider_busy.len(), 1);
        assert_eq!(app.slider_pending.len(), 1);
        assert_eq!(app.slider_pending[&SliderKind::Volume], 70.0);
        let _ = app.update(Message::SliderApplied(SliderKind::Volume));
        assert!(app.slider_pending.is_empty());
        assert!(app.slider_busy.contains(&SliderKind::Volume));
        let _ = app.update(Message::SliderApplied(SliderKind::Volume));
        assert!(app.slider_busy.is_empty());
    }
    #[test]
    fn polling_does_not_roll_back_a_drag() {
        let mut app = app();
        let _ = app.update(Message::SliderChanged(SliderKind::Volume, 70.0));
        let _ = app.update(Message::VolumeLoaded(0, Some((20.0, false))));
        assert_eq!(app.volume, Some(70.0));
    }
    #[test]
    fn small_external_volume_changes_are_observed() {
        let mut app = app();
        app.volume = Some(70.0);
        let _ = app.update(Message::VolumeLoaded(0, Some((69.0, false))));
        assert_eq!(app.volume, Some(69.0));
    }
    #[test]
    fn clock_updates_even_when_hardware_refresh_is_pending() {
        let mut app = app();
        app.time_text = "stale".into();
        app.date_text = "stale".into();
        app.refresh_pending = RefreshKind::ALL.into_iter().collect();
        let _ = app.update(Message::Tick);
        assert_ne!(app.time_text, "stale");
        assert_ne!(app.date_text, "stale");
        assert_eq!(app.refresh_pending.len(), RefreshKind::ALL.len());
    }

    #[test]
    fn periodic_refresh_cannot_queue_overlapping_batches() {
        let mut app = app();
        app.reserve_refresh();
        let _ = app.update(Message::Tick);
        assert_eq!(app.refresh_pending.len(), RefreshKind::ALL.len());
        let _ = app.update(Message::Tick);
        assert_eq!(app.refresh_pending.len(), RefreshKind::ALL.len());
        let _ = app.update(Message::NotifsLoaded(0, (vec![], None)));
        assert_eq!(app.refresh_pending.len(), RefreshKind::ALL.len());
        let _ = app.update(Message::NotifsRefreshed(0, (vec![], None)));
        assert_eq!(app.refresh_pending.len(), RefreshKind::ALL.len() - 1);
    }

    #[test]
    fn slow_brightness_does_not_stop_other_refreshes_or_queue_duplicate_work() {
        let mut app = app();
        let first = app.reserve_refresh();
        assert_eq!(first.len(), RefreshKind::ALL.len());
        app.initial_pending = app.refresh_pending.clone();
        let slow = RefreshKind::Slider(SliderKind::Brightness);
        for kind in first.into_iter().rev().filter(|kind| *kind != slow) {
            app.finish_refresh(kind);
        }
        for _ in 0..1000 {
            let next = app.reserve_refresh();
            assert_eq!(next.len(), RefreshKind::ALL.len() - 1);
            assert!(!next.contains(&slow));
            assert!(app.reserve_refresh().is_empty());
            for kind in next {
                app.finish_refresh(kind);
            }
            assert_eq!(app.initial_pending, HashSet::from([slow]));
        }
        app.finish_refresh(slow);
        assert!(!app.initial_refresh);
        assert!(app.refresh_pending.is_empty());
        assert_eq!(app.reserve_refresh().len(), RefreshKind::ALL.len());
    }

    #[test]
    fn hidden_sections_do_not_schedule_hardware_queries() {
        let mut app = TrayApp::state(AppConfig::parse(concat!(
            "toggles=[]\n[layout]\nshow_weather=false\nshow_metrics=false\n",
            "show_quick_toggles=false\nshow_power_profiles=false\n",
            "show_sliders=false\nshow_notifications=false\n",
        )));
        assert_eq!(app.reserve_refresh(), vec![RefreshKind::GlobalBlur]);
        app.config.layout.show_sliders = true;
        let kinds = app.reserve_refresh();
        assert_eq!(kinds.len(), 3);
        assert!(
            kinds
                .iter()
                .all(|kind| matches!(kind, RefreshKind::Slider(_)))
        );
    }

    #[test]
    fn sliders_load_independently_and_drag_revisions_are_per_slider() {
        let mut app = app();
        for value in 0..=1000 {
            let _ = app.update(Message::SliderChanged(
                SliderKind::Volume,
                value as f32 / 10.0,
            ));
        }
        let _ = app.update(Message::SliderLoaded(SliderKind::Brightness, 0, Some(45.0)));
        let _ = app.update(Message::SliderLoaded(SliderKind::Sunset, 0, Some(30.0)));
        let _ = app.update(Message::VolumeLoaded(0, Some((20.0, false))));
        assert_eq!(app.volume, Some(100.0));
        assert_eq!(app.brightness, Some(45.0));
        assert_eq!(app.sunset, Some(30.0));
        assert!(app.sunset_available);
        assert_eq!(app.slider_busy.len(), 1);
        assert_eq!(app.slider_pending.len(), 1);
        let _ = app.update(Message::SliderLoaded(SliderKind::Brightness, 0, None));
        assert!(app.brightness.is_none());
        assert_eq!(app.sunset, Some(30.0));
        let _ = app.update(Message::SliderLoaded(SliderKind::Sunset, 0, None));
        assert!(!app.sunset_available);
        assert_eq!(app.volume, Some(100.0));
    }

    #[test]
    fn late_queries_cannot_undo_completed_radio_power_or_dnd_actions() {
        let mut app = app();
        let _ = app.update(Message::WifiToggled(false));
        let _ = app.update(Message::WifiSettled(Some(false)));
        let _ = app.update(Message::RadioLoaded(0, 0, (Some(true), Some(true))));
        assert_eq!(app.wifi_on, Some(false));
        assert_eq!(app.bt_on, Some(true));
        let _ = app.update(Message::BtToggled(false));
        let _ = app.update(Message::BtSettled(Some(false)));
        let _ = app.update(Message::RadioLoaded(1, 0, (Some(false), Some(true))));
        assert_eq!(app.bt_on, Some(false));
        let _ = app.update(Message::PowerSelected("balanced".into()));
        let _ = app.update(Message::PowerSettled(("balanced".into(), true)));
        let _ = app.update(Message::PowerLoaded(0, Some("performance".into())));
        assert_eq!(app.power_profile.as_deref(), Some("balanced"));
        app.dnd = Some(false);
        let _ = app.update(Message::DndToggled);
        let _ = app.update(Message::DndSettled(Some(true)));
        let _ = app.update(Message::NotifsLoaded(0, (vec![], Some(false))));
        assert_eq!(app.dnd, Some(true));
        assert!(app.notifications_loaded);
        let _ = app.update(Message::NotifsLoaded(1, (vec![], Some(false))));
        assert_eq!(
            app.dnd,
            Some(false),
            "new external changes remain observable"
        );
    }

    #[test]
    fn stale_toggle_results_cannot_undo_optimistic_feedback() {
        let mut app = app();
        app.toggle_revision = 1;
        app.toggles.animations_on = Some(false);
        let snapshot = ToggleSnapshot {
            animations_on: Some(true),
            ..Default::default()
        };
        let _ = app.update(Message::TogglesLoaded(0, snapshot.clone()));
        assert_eq!(app.toggles.animations_on, Some(false));
        let _ = app.update(Message::TogglesLoaded(1, snapshot));
        assert_eq!(app.toggles.animations_on, Some(true));
    }

    #[test]
    fn hidden_boot_only_queries_the_global_visual_state() {
        let config = AppConfig::parse(concat!(
            "toggles=[]\n[layout]\nshow_weather=false\nshow_metrics=false\n",
            "show_quick_toggles=false\nshow_power_profiles=false\n",
            "show_sliders=false\nshow_notifications=false\n",
        ));
        let (mut app, _) = TrayApp::new(config);
        assert_eq!(
            app.refresh_pending,
            HashSet::from([RefreshKind::GlobalBlur])
        );
        let _ = app.update(Message::GlobalBlurLoaded(Some(false)));
        assert!(app.refresh_pending.is_empty());
        assert!(!app.initial_refresh);
    }
    #[test]
    fn stale_notification_snapshot_does_not_restore_dismissed_items() {
        let mut app = app();
        let n = Notification {
            id: 42,
            app: "Test".into(),
            summary: "Example".into(),
            body: String::new(),
            source: "active".into(),
            desktop_entry: String::new(),
            time: String::new(),
        };
        let _ = app.update(Message::NotifsLoaded(0, (vec![n.clone()], None)));
        app.dismissed.insert(42);
        app.notifications.clear();
        let _ = app.update(Message::NotifsLoaded(0, (vec![n], None)));
        assert!(app.notifications.is_empty());
    }

    #[test]
    fn first_metric_defaults_by_power_state_and_toggles_on_click() {
        let mut app = app();
        let cpu = sys::CpuState {
            text: "10%".into(),
            power_watts: Some(14.2),
            temp_c: Some(48.0),
            tooltip: "Power: 14.2 W\nTemp: 48°C\nLMB: Open btop".into(),
        };
        let ram = sys::RamState {
            text: "4.0 GB".into(),
            tooltip: "Memory: 4.0 GiB / 16.0 GiB (25%)\nDirty cache: 8.0 MiB\nSwap: Disabled\nLMB: Open zramctl".into(),
        };
        let net = sys::NetState {
            text: "12 KB 34".into(),
            tooltip: "Upload: 12.0 MiB (session)\nDownload: 34.0 MiB (session)\nCloudflare WARP: Connected (ON)".into(),
            disconnected: false,
            warp: sys::WarpStatus::Connected,
            total_rx_bytes: 34 * 1_048_576,
            total_tx_bytes: 12 * 1_048_576,
        };

        // Desktop / no battery present: always shows network meter + WARP ring + RMB hint, even if clicked
        let no_battery = sys::BatteryState {
            present: false,
            status: sys::BatteryStatus::Unknown,
            watts: 0.0,
            text: "--".into(),
            tooltip: "No battery present".into(),
        };
        let _ = app.update(Message::MetricsLoaded((
            cpu.clone(),
            ram.clone(),
            net.clone(),
            no_battery.clone(),
        )));
        let (val, color, ring, hint) = app.first_metric_display();
        assert_eq!(val, "12 KB 34");
        assert_eq!(color, app.theme.fg);
        assert_eq!(ring, Some(saturate(app.theme.accent, 1.2)));
        assert_eq!(
            hint,
            "Upload: 12.0 MiB (session)\nDownload: 34.0 MiB (session)\nCloudflare WARP: Connected (ON)\nRMB: Toggle WARP"
        );

        let _ = app.update(Message::MetricPressed("net".into()));
        let (val, color, ring, hint) = app.first_metric_display();
        assert_eq!(val, "12 KB 34");
        assert_eq!(color, app.theme.fg);
        assert_eq!(ring, Some(saturate(app.theme.accent, 1.2)));
        assert_eq!(
            hint,
            "Upload: 12.0 MiB (session)\nDownload: 34.0 MiB (session)\nCloudflare WARP: Connected (ON)\nRMB: Toggle WARP"
        );
        assert_eq!(app.show_battery_metric, None);

        // Discharging defaults to battery wattage without WARP ring, keeping WARP status and RMB hint while installed
        let discharging = sys::BatteryState {
            present: true,
            status: sys::BatteryStatus::Discharging,
            watts: 24.6,
            text: "24.60W".into(),
            tooltip: "Battery: Discharging\nRemaining: 2h 21m".into(),
        };
        let _ = app.update(Message::MetricsLoaded((
            cpu.clone(),
            ram.clone(),
            net.clone(),
            discharging.clone(),
        )));
        let (val, color, ring, hint) = app.first_metric_display();
        assert_eq!(val, "24.60W");
        assert_eq!(color, saturate(app.theme.profiles[2], 0.8));
        assert_eq!(ring, None);
        assert_eq!(
            hint,
            "Battery: Discharging\nRemaining: 2h 21m\nCloudflare WARP: Connected (ON)\nLMB: Network traffic\nRMB: Toggle WARP"
        );

        // Clicking toggles to network traffic with WARP ring and both LMB/RMB hints
        let _ = app.update(Message::MetricPressed("net".into()));
        let (val, color, ring, hint) = app.first_metric_display();
        assert_eq!(val, "12 KB 34");
        assert_eq!(color, app.theme.fg);
        assert_eq!(ring, Some(saturate(app.theme.accent, 1.2)));
        assert_eq!(
            hint,
            "Upload: 12.0 MiB (session)\nDownload: 34.0 MiB (session)\nCloudflare WARP: Connected (ON)\nLMB: Battery power\nRMB: Toggle WARP"
        );

        // Plugging in / charging with WARP installed but disconnected -> no ring, keeps WARP status and RMB hint
        let net_warp_off = sys::NetState {
            warp: sys::WarpStatus::Disconnected,
            tooltip: "Upload: 12.0 MiB (session)\nDownload: 34.0 MiB (session)\nCloudflare WARP: Disconnected (OFF)".into(),
            ..net.clone()
        };
        let charging = sys::BatteryState {
            present: true,
            status: sys::BatteryStatus::Charging,
            watts: 6.42,
            text: "6.42W".into(),
            tooltip: "Battery: Charging\nRemaining: 10h 31m".into(),
        };
        let _ = app.update(Message::MetricsLoaded((
            cpu.clone(),
            ram.clone(),
            net_warp_off,
            charging.clone(),
        )));
        let (val, color, ring, hint) = app.first_metric_display();
        assert_eq!(val, "12 KB 34");
        assert_eq!(color, app.theme.fg);
        assert_eq!(ring, None);
        assert_eq!(
            hint,
            "Upload: 12.0 MiB (session)\nDownload: 34.0 MiB (session)\nCloudflare WARP: Disconnected (OFF)\nLMB: Battery power\nRMB: Toggle WARP"
        );

        let _ = app.update(Message::MetricPressed("net".into()));
        let (val, color, ring, hint) = app.first_metric_display();
        assert_eq!(val, "6.42W");
        assert_eq!(color, saturate(app.theme.profiles[0], 0.8));
        assert_eq!(ring, None);
        assert_eq!(
            hint,
            "Battery: Charging\nRemaining: 10h 31m\nCloudflare WARP: Disconnected (OFF)\nLMB: Network traffic\nRMB: Toggle WARP"
        );

        // WARP not installed -> no ring, no WARP tooltip line, no RMB hint
        let net_no_warp = sys::NetState {
            text: "12 KB 34".into(),
            tooltip: "Upload: 12.0 MiB (session)\nDownload: 34.0 MiB (session)".into(),
            disconnected: false,
            warp: sys::WarpStatus::NotInstalled,
            total_rx_bytes: 34 * 1_048_576,
            total_tx_bytes: 12 * 1_048_576,
        };
        let _ = app.update(Message::MetricsLoaded((
            cpu.clone(),
            ram.clone(),
            net_no_warp.clone(),
            no_battery,
        )));
        let (val, color, ring, hint) = app.first_metric_display();
        assert_eq!(val, "12 KB 34");
        assert_eq!(color, app.theme.fg);
        assert_eq!(ring, None);
        assert_eq!(
            hint,
            "Upload: 12.0 MiB (session)\nDownload: 34.0 MiB (session)"
        );

        let _ = app.update(Message::MetricsLoaded((cpu, ram, net_no_warp, discharging)));
        let (val, color, ring, hint) = app.first_metric_display();
        assert_eq!(val, "24.60W");
        assert_eq!(color, saturate(app.theme.profiles[2], 0.8));
        assert_eq!(ring, None);
        assert_eq!(
            hint,
            "Battery: Discharging\nRemaining: 2h 21m\nLMB: Network traffic"
        );

        let _ = app.update(Message::MetricPressed("net".into()));
        let (val, color, ring, hint) = app.first_metric_display();
        assert_eq!(val, "12 KB 34");
        assert_eq!(color, app.theme.fg);
        assert_eq!(ring, None);
        assert_eq!(
            hint,
            "Upload: 12.0 MiB (session)\nDownload: 34.0 MiB (session)\nLMB: Battery power"
        );
    }

    #[test]
    fn rich_tooltip_parses_semantic_colors_and_builds_widget_tree() {
        let theme = AppTheme::load();

        // Temperature thresholds
        assert_eq!(
            TrayApp::tooltip_value_style("Temp", "45°C", theme),
            (theme.fg, Weight::Normal)
        );
        assert_eq!(
            TrayApp::tooltip_value_style("Temp", "75°C", theme),
            (saturate(theme.profiles[2], 0.85), Weight::Semibold)
        );
        assert_eq!(
            TrayApp::tooltip_value_style("Temp", "90°C", theme),
            (theme.danger, Weight::Bold)
        );
        assert_eq!(
            TrayApp::tooltip_value_style("Temp", "N/A", theme),
            (theme.muted, Weight::Normal)
        );

        // WARP & update counts
        assert_eq!(
            TrayApp::tooltip_value_style("Cloudflare WARP", "Connected (ON)", theme),
            (saturate(theme.profiles[0], 0.9), Weight::Semibold)
        );
        assert_eq!(
            TrayApp::tooltip_value_style("Cloudflare WARP", "Disconnected (OFF)", theme),
            (theme.muted, Weight::Normal)
        );
        assert_eq!(
            TrayApp::tooltip_value_style("Pacman", "12", theme),
            (theme.accent, Weight::Semibold)
        );
        assert_eq!(
            TrayApp::tooltip_value_style("AUR", "0", theme),
            (theme.muted, Weight::Normal)
        );

        // Ensure multi-line, single-line, colon-ratio, blank-line, and empty inputs build cleanly
        for sample in [
            "",
            "Power menu",
            "Disconnected",
            "Power: 14.2 W\nTemp: 52°C\nLMB: Open btop",
            "Memory: 8.0 GiB / 16.0 GiB (50%)\nDirty cache: 16.0 MiB\nSwap: 2.0 GiB / 8.0 GiB\nZRAM: 160 MiB physical · 4.0:1 ratio\nLMB: Open zramctl",
            "Cached update counts\nPacman: 4\nAUR: 0\n\nLMB: System Update\nRMB: Dusky Update",
        ] {
            let elem = TrayApp::render_rich_tooltip(sample, theme);
            drop(elem);
        }
    }

    #[test]
    fn weather_tooltip_renders_full_offline_and_minimal_states() {
        let theme = AppTheme::load();
        let mut app = app();

        let full = sys::WeatherState {
            temperature: "11°C".into(),
            condition: Some("Partly cloudy".into()),
            temp_max: Some("12°C".into()),
            temp_min: Some("-1°C".into()),
            precip_chance: Some("0%".into()),
            location: Some("City".into()),
            offline_note: None,
        };
        let offline = sys::WeatherState {
            precip_chance: Some("65%".into()),
            offline_note: Some("Offline — cached 15m ago".into()),
            ..full.clone()
        };
        let minimal = sys::WeatherState {
            temperature: "72°F".into(),
            condition: None,
            temp_max: None,
            temp_min: None,
            precip_chance: None,
            location: None,
            offline_note: None,
        };

        for sample in [&full, &offline, &minimal] {
            let _ = app.update(Message::WeatherLoaded(Some(sample.clone())));
            assert_eq!(app.weather.as_ref(), Some(sample));
            let elem = TrayApp::render_weather_tooltip(sample, theme);
            drop(elem);
        }

        let _ = app.update(Message::WeatherLoaded(None));
        assert!(app.weather.is_none());
    }

    #[test]
    fn volume_muted_state_updates_and_clears_on_nonzero_drag() {
        let mut app = app();
        let _ = app.update(Message::VolumeLoaded(0, Some((55.0, true))));
        assert_eq!(app.volume, Some(55.0));
        assert!(app.volume_muted);

        // Dragging to 0 does not unmute.
        let _ = app.update(Message::SliderChanged(SliderKind::Volume, 0.0));
        assert_eq!(app.volume, Some(0.0));
        assert!(app.volume_muted);

        // Dragging above 0 optimistically clears mute.
        let _ = app.update(Message::SliderChanged(SliderKind::Volume, 35.0));
        assert_eq!(app.volume, Some(35.0));
        assert!(!app.volume_muted);

        // Unavailable volume resets muted flag.
        app.slider_busy.clear();
        app.slider_changed_at.clear();
        let _ = app.update(Message::VolumeLoaded(2, None));
        assert_eq!(app.volume, None);
        assert!(!app.volume_muted);
    }

    #[test]
    fn notification_actions_and_grouped_rendering_work_cleanly() {
        const CHILD: &str = "DUSKY_TRAY_TEST_NOTIF_UI";
        if std::env::var_os(CHILD).is_none() {
            let temp =
                crate::backend::runtime_dir().join(format!("notif-ui-test-{}", std::process::id()));
            let _ = std::fs::remove_dir_all(&temp);
            std::fs::create_dir_all(&temp).unwrap();
            let output = std::process::Command::new(std::env::current_exe().unwrap())
                .args([
                    "--exact",
                    "ui::tests::notification_actions_and_grouped_rendering_work_cleanly",
                ])
                .env(CHILD, "1")
                .env("XDG_RUNTIME_DIR", &temp)
                .output()
                .unwrap();
            let _ = std::fs::remove_dir_all(&temp);
            assert!(
                output.status.success(),
                "{}",
                String::from_utf8_lossy(&output.stderr)
            );
            return;
        }

        let mut app = window_app();
        let n = |id: i32, app_name: &str, source: &str| Notification {
            id,
            app: app_name.into(),
            summary: format!("Summary {id}"),
            body: "Body text".into(),
            source: source.into(),
            desktop_entry: String::new(),
            time: "1:05 PM".into(),
        };
        app.notifications = vec![
            n(12, "hypr-rotate", "history"),
            n(11, "hypr-rotate", "history"),
            n(10, "kitty", "history"),
            n(9, "kitty", "history"),
            n(8, "Signal", "active"),
        ];

        // Collapsed and expanded full-width accordion views build cleanly.
        drop(app.view());
        let _ = app.update(Message::StackToggled("hypr-rotate".into()));
        let _ = app.update(Message::StackToggled("kitty".into()));
        assert!(app.expanded.contains("hypr-rotate"));
        assert!(app.expanded.contains("kitty"));
        drop(app.view());

        // Dismissing an expanded 2-item group down to 1 item prunes its expanded state.
        let dismiss_task = app.update(Message::NotifDismiss(12));
        assert!(iced_runtime::task::into_stream(dismiss_task).is_none());
        assert!(!app.expanded.contains("hypr-rotate"));
        assert!(app.expanded.contains("kitty"));
        drop(app.view());

        // Closing a history-only stack blacklists synchronously and spawns no makoctl task.
        let task = app.update(Message::StackClosed("kitty".into()));
        assert!(iced_runtime::task::into_stream(task).is_none());
        assert!(!app.expanded.contains("kitty"));
        assert_eq!(app.notifications.len(), 2);
        assert!(app.dismissed.contains(&10) && app.dismissed.contains(&9));

        // Invoking an item schedules async invoke; NotifInvoked(false) keeps panel open,
        // NotifInvoked(true) emits exit task.
        let invoke_task = app.update(Message::NotifInvoke(8));
        assert!(iced_runtime::task::into_stream(invoke_task).is_some());
        assert_eq!(app.notifications.len(), 1);
        assert!(
            iced_runtime::task::into_stream(app.update(Message::NotifInvoked(false))).is_none()
        );
        assert!(iced_runtime::task::into_stream(app.update(Message::NotifInvoked(true))).is_some());

        // ClearNotifs clears expanded state too.
        app.expanded.insert("kitty".into());
        let clear_task = app.update(Message::ClearNotifs);
        assert!(iced_runtime::task::into_stream(clear_task).is_some());
        assert!(app.expanded.is_empty());
    }

    #[test]
    fn updates_right_click_triggers_exit_task() {
        let mut app = app();
        let toggle = app
            .config
            .toggles
            .iter_mut()
            .find(|t| t.id == "updates")
            .unwrap();
        toggle.on_right = "true".into();
        let task = app.update(Message::ToggleRight("updates".into()));
        assert!(iced_runtime::task::into_stream(task).is_some());
    }

    #[test]
    fn calendar_grid_handles_leap_years_year_rollover_and_six_row_invariant() {
        let theme = AppTheme::load();

        // Current month: Nov 2026 starts on Sunday (0 leading days), Nov 25 is W48.
        let nov_25 = NaiveDate::from_ymd_opt(2026, 11, 25).unwrap();
        let cal = TrayApp::build_calendar_month(nov_25, 0);
        assert_eq!(cal.title, "November 2026");
        assert_eq!(cal.badge, "W48");
        assert!(!cal.is_offset);
        assert_eq!(cal.cells.len(), 42);
        assert_eq!(cal.cells[0].day, 1);
        assert!(cal.cells[0].in_month);
        assert!(cal.cells[0].is_weekend);
        assert_eq!(cal.cells.iter().filter(|c| c.in_month).count(), 30);
        assert_eq!(cal.cells.iter().filter(|c| c.is_today).count(), 1);
        assert!(cal.cells[24].is_today && cal.cells[24].day == 25);
        assert!(!cal.cells[30].in_month && cal.cells[30].day == 1);

        // Day-31 anchor + leap-year Feb 2024 vs non-leap Feb 2026.
        let jan_31_2024 = NaiveDate::from_ymd_opt(2024, 1, 31).unwrap();
        let feb_2024 = TrayApp::build_calendar_month(jan_31_2024, 1);
        assert_eq!(feb_2024.title, "February 2024");
        assert_eq!(feb_2024.badge, "+1 mo");
        assert!(feb_2024.is_offset);
        assert_eq!(feb_2024.cells.iter().filter(|c| c.in_month).count(), 29);
        assert_eq!(feb_2024.cells.iter().filter(|c| c.is_today).count(), 0);

        let jan_31_2026 = NaiveDate::from_ymd_opt(2026, 1, 31).unwrap();
        let feb_2026 = TrayApp::build_calendar_month(jan_31_2026, 1);
        assert_eq!(feb_2026.title, "February 2026");
        assert_eq!(feb_2026.cells.iter().filter(|c| c.in_month).count(), 28);

        // Dec <-> Jan year rollover.
        let dec_2026 = NaiveDate::from_ymd_opt(2026, 12, 15).unwrap();
        assert_eq!(
            TrayApp::build_calendar_month(dec_2026, 1).title,
            "January 2027"
        );
        let jan_2026 = NaiveDate::from_ymd_opt(2026, 1, 15).unwrap();
        assert_eq!(
            TrayApp::build_calendar_month(jan_2026, -1).title,
            "December 2025"
        );

        // Relative offset badge formatting.
        assert_eq!(TrayApp::format_month_offset(-1), "−1 mo");
        assert_eq!(TrayApp::format_month_offset(3), "+3 mo");
        assert_eq!(TrayApp::format_month_offset(-12), "−1y");
        assert_eq!(TrayApp::format_month_offset(14), "+1y 2m");

        // Luminance-aware today contrast across light and dark accents.
        let dark_bg = Color::from_rgb8(12, 14, 19);
        assert_eq!(
            TrayApp::contrast_text_on(Color::from_rgb8(154, 180, 255), dark_bg),
            dark_bg
        );
        assert_eq!(
            TrayApp::contrast_text_on(Color::from_rgb8(30, 58, 138), dark_bg),
            Color::WHITE
        );

        // Weekend coral-red tint stays red-dominant and distinct from muted weekdays.
        let (weekend_header, weekend_day) = TrayApp::calendar_weekend_colors(theme);
        assert!((weekend_header.a - 0.90).abs() < 1e-6);
        assert!(weekend_header.r > weekend_header.g && weekend_header.r > weekend_header.b);
        assert_eq!(weekend_day.a, 1.0);
        assert!(weekend_day.r > weekend_day.g && weekend_day.r > weekend_day.b);

        // Weekend "today" still marks both flags so today-pill precedence applies.
        let sun_22 = NaiveDate::from_ymd_opt(2026, 11, 22).unwrap();
        let sun_cal = TrayApp::build_calendar_month(sun_22, 0);
        assert!(sun_cal.cells[21].is_today && sun_cal.cells[21].is_weekend);

        // Widget tree builds cleanly for current, weekend-today, and offset months.
        drop(TrayApp::render_calendar_tooltip(nov_25, 0, theme));
        drop(TrayApp::render_calendar_tooltip(sun_22, 0, theme));
        drop(TrayApp::render_calendar_tooltip(nov_25, -2, theme));
    }

    #[test]
    fn calendar_scroll_switches_months_accumulates_pixels_clamps_and_resets() {
        let mut app = app();

        // Discrete lines: UP (y > 0) -> previous month (-1); DOWN (y < 0) -> next month (+1).
        let _ = app.update(Message::CalendarScrolled(ScrollDelta::Lines {
            x: 0.0,
            y: 1.0,
        }));
        assert_eq!(app.calendar_month_offset, -1);
        let _ = app.update(Message::CalendarScrolled(ScrollDelta::Lines {
            x: 0.0,
            y: -1.0,
        }));
        assert_eq!(app.calendar_month_offset, 0);

        // Horizontal-dominant gestures and non-finite deltas are ignored.
        let _ = app.update(Message::CalendarScrolled(ScrollDelta::Lines {
            x: 5.0,
            y: 1.0,
        }));
        let _ = app.update(Message::CalendarScrolled(ScrollDelta::Pixels {
            x: 0.0,
            y: f32::NAN,
        }));
        assert_eq!(app.calendar_month_offset, 0);

        // Smooth touchpad pixel accumulation (40 px per month) + instant direction reversal.
        let _ = app.update(Message::CalendarScrolled(ScrollDelta::Pixels {
            x: 0.0,
            y: -25.0,
        }));
        assert_eq!(app.calendar_month_offset, 0);
        let _ = app.update(Message::CalendarScrolled(ScrollDelta::Pixels {
            x: 0.0,
            y: -25.0,
        }));
        assert_eq!(app.calendar_month_offset, 1);

        // Reversing direction clears leftover negative residue immediately.
        let _ = app.update(Message::CalendarScrolled(ScrollDelta::Pixels {
            x: 0.0,
            y: 25.0,
        }));
        assert_eq!(app.calendar_month_offset, 1);
        let _ = app.update(Message::CalendarScrolled(ScrollDelta::Pixels {
            x: 0.0,
            y: 25.0,
        }));
        assert_eq!(app.calendar_month_offset, 0);

        // Single fast flick caps to one month step per event.
        let _ = app.update(Message::CalendarScrolled(ScrollDelta::Pixels {
            x: 0.0,
            y: 500.0,
        }));
        assert_eq!(app.calendar_month_offset, -1);

        // Clamped to ±120 months.
        app.calendar_month_offset = 120;
        let _ = app.update(Message::CalendarScrolled(ScrollDelta::Lines {
            x: 0.0,
            y: -1.0,
        }));
        assert_eq!(app.calendar_month_offset, 120);
        app.calendar_month_offset = -120;
        let _ = app.update(Message::CalendarScrolled(ScrollDelta::Lines {
            x: 0.0,
            y: 1.0,
        }));
        assert_eq!(app.calendar_month_offset, -120);

        // Leaving clock hover resets offset and pixel accumulator.
        app.calendar_scroll_accum = 0.75;
        let _ = app.update(Message::CalendarHoverExited);
        assert_eq!(app.calendar_month_offset, 0);
        assert_eq!(app.calendar_scroll_accum, 0.0);
    }
}
