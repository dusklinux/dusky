//! Iced layer-shell tray: layout mirrors the original compact panel.
//!
//! Sections (top → bottom): header (weather / clock / power), metrics,
//! quick-toggle grid (5), power row (wifi+bt switches + TLP radios),
//! sliders (volume / brightness / sunset), notifications.

use iced_core::alignment::{Horizontal, Vertical};
use iced_core::font::Weight;
use iced_core::keyboard::Key;
use iced_core::keyboard::key::Named;
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
    BlurConfigured,
    BlurRegion(bool),
    ClockLoaded((String, String)),
    WeatherLoaded(Option<String>),
    MetricsLoaded((String, String, sys::NetState)),
    TogglesLoaded(ToggleSnapshot),
    RadioLoaded((Option<bool>, Option<bool>)),
    PowerLoaded(Option<String>),
    SlidersLoaded(u64, SliderSnapshot),
    NotifsLoaded((Vec<Notification>, Option<bool>)),
    NotifsRefreshed((Vec<Notification>, Option<bool>)),
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
    WeatherPressed,
    WifiManager,
    BluetoothManager,
    VolumeControl,
    OutputSelector,
    MetricPressed(String),
    DndToggled,
    DndSettled(Option<bool>),
    ClearNotifs,
    NotifsCleared,
    NotifDismiss(i32),
    NotifInvoke(i32),
    StackToggled(String),
    StackClosed(String),
    NotificationHovered(String),
    NotificationLeft(String),
    PanelScrolled(scrollable::Viewport),
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

#[derive(Debug, Clone, Default)]
pub struct SliderSnapshot {
    pub volume: Option<f32>,
    pub brightness: Option<f32>,
    pub sunset: Option<f32>,
    pub sunset_available: bool,
}

// ---------------------------------------------------------------------------
// app state
// ---------------------------------------------------------------------------

pub struct TrayApp {
    config: AppConfig,
    appearance: Appearance,
    theme: AppTheme,
    time_text: String,
    date_text: String,
    weather: Option<String>,
    cpu_text: String,
    ram_text: String,
    net: sys::NetState,
    toggles: ToggleSnapshot,
    wifi_on: Option<bool>,
    bt_on: Option<bool>,
    wifi_pending: bool,
    bt_pending: bool,
    power_profile: Option<String>,
    power_pending: Option<String>,
    powertop_running: bool,
    volume: Option<f32>,
    brightness: Option<f32>,
    sunset: Option<f32>,
    sunset_available: bool,
    notifications: Vec<Notification>,
    dismissed: HashSet<i32>,
    dnd: Option<bool>,
    dnd_pending: bool,
    expanded: HashSet<String>,
    hovered_notification: Option<String>,
    fade_bottom: bool,
    refresh_pending: usize,
    initial_refresh: bool,
    notifications_loaded: bool,
    slider_busy: HashSet<SliderKind>,
    slider_pending: HashMap<SliderKind, f32>,
    slider_changed_at: HashMap<SliderKind, Instant>,
    slider_revision: u64,
    error: Option<String>,
}

impl TrayApp {
    pub fn new(config: AppConfig) -> (Self, Task<Message>) {
        let (time_text, date_text) = sys::current_time_date();
        let app = Self {
            config,
            appearance: Appearance::load().unwrap_or_default(),
            theme: AppTheme::load(),
            time_text,
            date_text,
            weather: None,
            cpu_text: "--".into(),
            ram_text: "--".into(),
            net: Default::default(),
            toggles: Default::default(),
            wifi_on: None,
            bt_on: None,
            wifi_pending: false,
            bt_pending: false,
            power_profile: None,
            power_pending: None,
            powertop_running: false,
            volume: None,
            brightness: None,
            sunset: None,
            sunset_available: false,
            notifications: Vec::new(),
            dismissed: HashSet::new(),
            dnd: None,
            dnd_pending: false,
            expanded: HashSet::new(),
            hovered_notification: None,
            fade_bottom: false,
            refresh_pending: 0,
            initial_refresh: true,
            notifications_loaded: false,
            slider_busy: HashSet::new(),
            slider_pending: HashMap::new(),
            slider_changed_at: HashMap::new(),
            slider_revision: 0,
            error: None,
        };
        let boot = Task::batch([
            Task::done(Message::Tick),
            Self::configure_blur(app.appearance.blur),
        ]);
        (app, boot)
    }

    fn refresh_all(&self) -> Task<Message> {
        let revision = self.slider_revision;
        let has_wifi_button = self.config.toggles.iter().any(|t| t.id == "wifi");
        Task::batch(vec![
            Task::perform(
                async move { sys::current_time_date() },
                Message::ClockLoaded,
            ),
            Task::perform(async move { sys::weather_text() }, Message::WeatherLoaded),
            Task::perform(
                async move {
                    let (cpu, ram) = sys::cpu_ram();
                    let net = sys::net_state();
                    (cpu, ram, net)
                },
                Message::MetricsLoaded,
            ),
            Task::perform(
                async move {
                    let idle = sys::is_idle_active();
                    let updates = sys::updates_state();
                    ToggleSnapshot {
                        animations_on: sys::animations_enabled(),
                        wifi_connected: if has_wifi_button {
                            sys::wifi_connected()
                        } else {
                            None
                        },
                        idle_inhibited: idle.map(|active| !active),
                        blur_on: sys::is_blur_on(),
                        audio_active: sys::is_audio_active(),
                        updates_badge: updates.badge,
                        updates_tooltip: updates.tooltip,
                    }
                },
                Message::TogglesLoaded,
            ),
            Task::perform(
                async move { (sys::get_wifi(), sys::get_bt()) },
                Message::RadioLoaded,
            ),
            Task::perform(
                async move { sys::get_power_profile() },
                Message::PowerLoaded,
            ),
            Task::perform(
                async move {
                    let sunset_available = sys::has_sunset() && sys::is_sunset_service_enabled();
                    SliderSnapshot {
                        volume: if sys::has_volume() {
                            sys::get_volume()
                        } else {
                            None
                        },
                        brightness: if sys::has_brightness() {
                            sys::get_brightness()
                        } else {
                            None
                        },
                        sunset: if sunset_available {
                            sys::get_sunset()
                        } else {
                            None
                        },
                        sunset_available,
                    }
                },
                move |s| Message::SlidersLoaded(revision, s),
            ),
            Task::perform(
                async move { (notify::fetch_notifications(), sys::mako_dnd()) },
                Message::NotifsRefreshed,
            ),
        ])
    }

    pub fn update(&mut self, message: Message) -> Task<Message> {
        let phase = match &message {
            Message::ClockLoaded(_) => Some("clock-ready"),
            Message::WeatherLoaded(_) => Some("weather-ready"),
            Message::MetricsLoaded(_) => Some("metrics-ready"),
            Message::TogglesLoaded(_) => Some("toggles-ready"),
            Message::RadioLoaded(_) => Some("radios-ready"),
            Message::PowerLoaded(_) => Some("power-ready"),
            Message::SlidersLoaded(_, _) => Some("sliders-ready"),
            _ => None,
        };
        if let Some(phase) = phase {
            self.finish_refresh(phase);
        }
        // Notification refreshes also occur after actions, independently of the
        // periodic batch; their completion must not release a batch slot.
        match message {
            Message::BlurConfigured | Message::BlurRegion(_) => {}
            Message::Tick => {
                let mut blur_change = Task::none();
                if let Some(appearance) = Appearance::load() {
                    if appearance.blur != self.appearance.blur {
                        blur_change = Self::configure_blur(appearance.blur);
                    }
                    self.appearance = appearance;
                }
                if let Some(theme) = AppTheme::load_generated() {
                    if theme != self.theme {
                        blur_change = Self::configure_blur(self.appearance.blur);
                    }
                    self.theme = theme;
                }
                if self.refresh_pending != 0 {
                    return blur_change;
                }
                self.refresh_pending = 8;
                return Task::batch([blur_change, self.refresh_all()]);
            }
            Message::ClockLoaded((t, d)) => {
                self.time_text = t;
                self.date_text = d;
            }
            Message::WeatherLoaded(w) => {
                self.weather = w;
            }
            Message::MetricsLoaded((cpu, ram, net)) => {
                self.cpu_text = cpu;
                self.ram_text = ram;
                self.net = net;
            }
            Message::TogglesLoaded(s) => {
                self.toggles = s;
            }
            Message::RadioLoaded((wifi, bt)) => {
                if !self.wifi_pending {
                    self.wifi_on = wifi;
                }
                if !self.bt_pending {
                    self.bt_on = bt;
                }
            }
            Message::PowerLoaded(p) => {
                if self.power_pending.is_none() {
                    self.power_profile = p;
                }
            }
            Message::SlidersLoaded(revision, s) => {
                if revision == self.slider_revision {
                    for (kind, value) in [
                        (SliderKind::Volume, s.volume),
                        (SliderKind::Brightness, s.brightness),
                        (SliderKind::Sunset, s.sunset),
                    ] {
                        let recent = self
                            .slider_changed_at
                            .get(&kind)
                            .is_some_and(|t| t.elapsed() < Duration::from_secs(3));
                        if !recent && !self.slider_busy.contains(&kind) {
                            match kind {
                                SliderKind::Volume => self.volume = value,
                                SliderKind::Brightness => self.brightness = value,
                                SliderKind::Sunset => self.sunset = value,
                            }
                        }
                    }
                }
                self.sunset_available = s.sunset_available;
            }
            Message::NotifsRefreshed(result) => {
                self.finish_refresh("notifications-ready");
                return self.update(Message::NotifsLoaded(result));
            }
            Message::NotifsLoaded((notifs, dnd)) => {
                self.notifications_loaded = true;
                if !self.notifications.iter().map(|n| n.id).eq(notifs
                    .iter()
                    .filter(|n| !self.dismissed.contains(&n.id))
                    .map(|n| n.id))
                {
                    self.fade_bottom = false;
                }
                self.notifications = notifs
                    .into_iter()
                    .filter(|n| !self.dismissed.contains(&n.id))
                    .collect();
                if dnd.is_some() && !self.dnd_pending {
                    self.dnd = dnd;
                }
                // Drop expansions for apps that vanished.
                let apps: HashSet<String> = self
                    .notifications
                    .iter()
                    .map(|n| {
                        if n.app.is_empty() {
                            "Unknown".to_owned()
                        } else {
                            n.app.clone()
                        }
                    })
                    .collect();
                self.expanded.retain(|a| apps.contains(a));
            }
            Message::SliderChanged(kind, value) => {
                let value = match kind {
                    SliderKind::Volume => {
                        self.volume = Some(sys::clamp(value, 0.0, 100.0));
                        self.volume.unwrap()
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
                self.slider_revision += 1;
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
                self.wifi_on = Some(on);
                return Task::perform(async move { sys::set_wifi(on) }, Message::WifiSettled);
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
                self.bt_on = Some(on);
                return Task::perform(async move { sys::set_bt(on) }, Message::BtSettled);
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
                return Task::perform(
                    async move {
                        let ok = sys::apply_power_profile(&profile);
                        (profile, ok)
                    },
                    Message::PowerSettled,
                );
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
                return Task::perform(async move { sys::powertop_autotune() }, |_| {
                    Message::PowertopDone
                });
            }
            Message::PowertopDone => {
                self.powertop_running = false;
            }
            Message::ToggleLeft(id) => {
                if let Some(t) = self.config.toggles.iter().find(|t| t.id == id) {
                    let cmd = t.on_left.clone();
                    if !cmd.is_empty() {
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
                    execute_detached(&t.on_middle);
                }
            }
            Message::ToggleRight(id) => {
                if let Some(t) = self.config.toggles.iter().find(|t| t.id == id) {
                    let cmd = if id == "power-saver-fallback" {
                        String::new()
                    } else {
                        t.on_right.clone()
                    };
                    if id == "audio" && cmd.is_empty() {
                        // Fallback parity with Python --toggle when config lacks on_right.
                        execute_detached(
                            "python3 ~/user_scripts/audio/dusky_audio_studio/dusky_audio_studio.py --toggle",
                        );
                    } else if !cmd.is_empty() {
                        execute_detached(&cmd);
                        if matches!(id.as_str(), "idle" | "blur") {
                            return iced_runtime::exit();
                        }
                    }
                }
                if id == "audio" {
                    self.toggles.audio_active = !self.toggles.audio_active;
                }
            }
            Message::PowerButton => {
                execute_detached("~/user_scripts/wlogout/wlogout_scale.sh");
                return iced_runtime::exit();
            }
            Message::ClockPressed => {
                execute_detached("gnome-clocks");
                return iced_runtime::exit();
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
                    "ram" => execute_detached("kitty --class zramctl --hold zramctl"),
                    "cpu" => execute_detached("kitty --class btop btop"),
                    _ => return Task::none(),
                }
                return iced_runtime::exit();
            }
            Message::DndToggled => {
                if self.dnd_pending || self.dnd.is_none() {
                    return Task::none();
                }
                self.dnd_pending = true;
                self.dnd = self.dnd.map(|on| !on);
                return Task::perform(
                    async {
                        sys::toggle_dnd();
                        sys::mako_dnd()
                    },
                    Message::DndSettled,
                );
            }
            Message::DndSettled(observed) => {
                self.dnd_pending = false;
                self.dnd = observed;
            }
            Message::ClearNotifs => {
                self.fade_bottom = false;
                self.dismissed
                    .extend(self.notifications.iter().map(|n| n.id));
                self.notifications.clear();
                return Task::perform(
                    async move {
                        notify::clear_all();
                    },
                    |_| Message::NotifsCleared,
                );
            }
            Message::NotifsCleared => {
                return Task::perform(
                    async move { (notify::fetch_notifications(), sys::mako_dnd()) },
                    Message::NotifsLoaded,
                );
            }
            Message::NotifDismiss(id) => {
                self.fade_bottom = false;
                self.dismissed.insert(id);
                self.notifications.retain(|n| n.id != id);
                std::thread::Builder::new()
                    .name("notif-dismiss".into())
                    .spawn(move || notify::dismiss(id))
                    .ok();
            }
            Message::NotifInvoke(id) => {
                self.fade_bottom = false;
                let notification = self.notifications.iter().find(|n| n.id == id).cloned();
                self.dismissed.insert(id);
                self.notifications.retain(|n| n.id != id);
                if let Some(n) = notification {
                    std::thread::Builder::new()
                        .name("notif-invoke".into())
                        .spawn(move || notify::invoke(n))
                        .ok();
                }
            }
            Message::NotificationHovered(key) => self.hovered_notification = Some(key),
            Message::NotificationLeft(key) => {
                if self.hovered_notification.as_ref() == Some(&key) {
                    self.hovered_notification = None;
                }
            }
            Message::PanelScrolled(viewport) => {
                self.fade_bottom = viewport.absolute_offset().y + viewport.bounds().height
                    < viewport.content_bounds().height - 1.0;
            }
            Message::StackToggled(app) => {
                self.fade_bottom = false;
                if !self.expanded.remove(&app) {
                    self.expanded.insert(app);
                }
            }
            Message::StackClosed(app) => {
                self.fade_bottom = false;
                let ids: Vec<i32> = self
                    .notifications
                    .iter()
                    .filter(|n| {
                        let a = if n.app.is_empty() { "Unknown" } else { &n.app };
                        a == app
                    })
                    .map(|n| n.id)
                    .collect();
                self.dismissed.extend(ids.iter().copied());
                self.notifications.retain(|n| {
                    let a = if n.app.is_empty() { "Unknown" } else { &n.app };
                    a != app
                });
                self.expanded.remove(&app);
                std::thread::Builder::new()
                    .name("stack-close".into())
                    .spawn(move || {
                        for id in ids {
                            notify::dismiss(id);
                        }
                    })
                    .ok();
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

    fn finish_refresh(&mut self, phase: &str) {
        self.refresh_pending = self.refresh_pending.saturating_sub(1);
        if self.initial_refresh {
            crate::renderer::trace_phase(phase);
            if self.refresh_pending == 0 {
                self.initial_refresh = false;
                crate::renderer::trace_phase("snapshot-ready");
            }
        }
    }

    fn configure_blur(enabled: bool) -> Task<Message> {
        Task::batch([
            Task::done(Message::BlurRegion(enabled)),
            Task::perform(
                async move {
                    sys::configure_panel_blur(enabled);
                },
                |_| Message::BlurConfigured,
            ),
        ])
    }

    pub fn subscription(&self) -> Subscription<Message> {
        let events = iced_futures::event::listen_with(|event, status, _| match (&status, &event) {
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
        Subscription::batch([events, Subscription::run(Self::ticks)])
    }

    fn ticks() -> impl iced_futures::futures::Stream<Item = Message> {
        iced_futures::futures::stream::unfold((), |()| async {
            futures_timer::Delay::new(Duration::from_secs(2)).await;
            Some((Message::Tick, ()))
        })
    }

    fn apply_slider(kind: SliderKind, value: f32) -> Task<Message> {
        Task::perform(
            async move {
                match kind {
                    SliderKind::Volume => sys::apply_volume(value),
                    SliderKind::Brightness => sys::apply_brightness(value),
                    SliderKind::Sunset => sys::apply_sunset(value),
                }
            },
            move |_| Message::SliderApplied(kind),
        )
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
            background: Some(Self::surface(theme, 0.05).into()),
            border: Border {
                radius: radius.into(),
                color: Color {
                    a: 0.055,
                    ..theme.fg
                },
                width: 1.0,
            },
            ..Default::default()
        }
    }

    fn surface(theme: AppTheme, amount: f32) -> Color {
        Color {
            a: 0.75,
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

    fn hint<'a>(
        content: impl Into<Element<'a, Message>>,
        label: String,
        theme: AppTheme,
    ) -> Element<'a, Message> {
        tooltip(
            content,
            text(label).size(12).color(theme.fg),
            tooltip::Position::Top,
        )
        .gap(8.0)
        .padding(10)
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
            style
        })
        .into()
    }

    fn metric(
        &self,
        glyph: &'static str,
        value: String,
        kind: &'static str,
        hint: String,
    ) -> Element<'static, Message> {
        let theme = self.theme;
        let label = text(value)
            .size(if kind == "net" { 10 } else { 12 })
            .font(Font::MONOSPACE)
            .color(theme.fg);
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
                        Self::surface(theme, 0.13)
                    } else {
                        Self::surface(theme, 0.05)
                    }
                    .into(),
                );
                style.border = Self::card(theme, 12.0).border;
                style
            });
        if kind != "net" {
            btn = btn.on_press(Message::MetricPressed(kind.into()));
        }
        Self::hint(btn, hint, theme)
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
                            a: if hovered { 0.26 } else { 0.16 },
                            ..theme.accent
                        }
                    } else if hovered {
                        Self::surface(theme, 0.14)
                    } else {
                        Self::surface(theme, 0.05)
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
                        Color {
                            a: 0.06,
                            ..theme.fg
                        }
                    },
                    width: 1.0,
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
        let label_tint = slider_icon_color(tint, value);
        let inactive_rail = Self::mix(theme.card_bg, theme.fg, 0.09);
        // Iced draws an active half-handle segment even at the minimum.
        // Match the empty rail there so zero has no colored fill.
        let active_rail = if value <= range.0 {
            inactive_rail
        } else {
            saturate(tint, 1.3)
        };
        let glyph = match kind {
            SliderKind::Volume if value <= 0.0 => "volume-muted",
            SliderKind::Brightness if value <= range.0 => "brightness-low",
            _ => glyph,
        };
        let s = slider(
            range.0..=range.1,
            sys::clamp(value, range.0, range.1),
            move |v| Message::SliderChanged(kind, sys::snap_to_step(v, range.0, range.1, step)),
        )
        .step(step)
        .height(24)
        .style(move |_, _| slider::Style {
            rail: slider::Rail {
                backgrounds: (active_rail.into(), inactive_rail.into()),
                width: 12.0,
                border: Border {
                    radius: 6.0.into(),
                    ..Default::default()
                },
            },
            handle: slider::Handle {
                shape: slider::HandleShape::Circle { radius: 7.0 },
                background: Color::WHITE.into(),
                border_color: theme.bg,
                border_width: 0.0,
            },
        });
        // Leave explicit clearance around both endpoint handles, independently
        // of the neighboring icon and value cells.
        let s = container(s).padding([0, 3]).width(Length::Fill);
        let glow = sys::clamp(value, 0.0, 100.0) / 100.0;
        let name = match kind {
            SliderKind::Volume => "Volume",
            SliderKind::Brightness => "Brightness",
            SliderKind::Sunset => "Night light",
        };
        let val = text(format!("{value:.0}"))
            .size(13)
            .font(Font::MONOSPACE)
            .color(slider_label_color(Self::mix(tint, theme.fg, 0.18), value))
            .width(28)
            .align_x(Horizontal::Right);
        let icon = crate::appearance::glow(Self::icon_label(glyph, 18.0, Some(label_tint)), glow);
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
        container(
            row![icon, s, crate::appearance::glow(val.into(), glow)]
                .spacing(12)
                .align_y(Vertical::Center),
        )
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
                toggler::Style {
                    background: if on && !disabled {
                        theme.accent.into()
                    } else {
                        Color {
                            a: 0.12,
                            ..theme.fg
                        }
                        .into()
                    },
                    foreground: if disabled {
                        theme.muted.into()
                    } else {
                        Color::WHITE.into()
                    },
                    border_radius: Some(11.0.into()),
                    padding_ratio: 0.12,
                    background_border_width: 0.0,
                    background_border_color: Color::TRANSPARENT,
                    foreground_border_width: 0.0,
                    foreground_border_color: Color::TRANSPARENT,
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
        let icon = Self::hint(
            button(Self::icon_label(
                glyph,
                16.0,
                Some(if on { theme.accent } else { theme.muted }),
            ))
            .padding(0)
            .on_press(if wifi {
                Message::WifiManager
            } else {
                Message::BluetoothManager
            })
            .style(move |_, status| Self::flat(theme, status)),
            if wifi {
                "Wi-Fi\nLMB: Open Network Manager"
            } else {
                "Bluetooth\nLMB: Open Blueman"
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
        iced_widget::responsive(move |size| self.view_at(size)).into()
    }

    fn view_at(&self, size: iced_core::Size) -> Element<'_, Message> {
        let theme = self.theme;
        // The clock occupies the whole header width. Side controls occupy a
        // separate row over it; neither can push the clock off its true center.
        let clock = button(
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
            .align_x(Horizontal::Center)
            .width(Length::Fill),
        )
        .padding([0, 0])
        .on_press(Message::ClockPressed)
        .style(move |_, _| button::Style {
            text_color: theme.fg,
            ..Default::default()
        });
        let weather: Element<'_, Message> = if self.config.layout.show_weather
            && let Some(w) = &self.weather
        {
            // The weather cache already contains its own condition glyph. Keep
            // only its temperature, avoiding duplicated symbols and overflow.
            let temperature = w
                .split_whitespace()
                .filter(|s| s.chars().any(|c| c.is_ascii_digit()))
                .collect::<Vec<_>>()
                .join(" ");
            Self::hint(
                button(crate::appearance::glow(
                    row![
                        Self::icon_label("☁︎", 16.0, Some(theme.fg)),
                        text(temperature).size(11).color(theme.fg)
                    ]
                    .spacing(5)
                    .align_y(Vertical::Center)
                    .into(),
                    0.242,
                ))
                .padding(0)
                .on_press(Message::WeatherPressed)
                .style(move |_, status| Self::flat(theme, status)),
                "Weather\nLMB: Open terminal forecast".into(),
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
            controls = controls.push(
                row![
                    self.metric(
                        "⇅",
                        if self.net.text.is_empty() || self.net.disconnected {
                            "--".into()
                        } else {
                            self.net.text.clone()
                        },
                        "net",
                        self.net.tooltip.clone()
                    ),
                    self.metric(
                        "▤",
                        self.ram_text.clone(),
                        "ram",
                        "Memory · Open zramctl".into()
                    ),
                    self.metric("▣", self.cpu_text.clone(), "cpu", "CPU · Open btop".into())
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
                        style.background = Some(Color { a: 0.12, ..tint }.into());
                    }
                    style.border = Border {
                        radius: 15.0.into(),
                        color: if selected { tint } else { Color::TRANSPARENT },
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
            let clear = button(
                container(Self::icon_label(
                    icon_glyph("edit-clear-all-symbolic"),
                    15.0,
                    None,
                ))
                .center_x(28)
                .center_y(28),
            )
            .padding(0)
            .on_press(Message::ClearNotifs)
            .style(move |_, s| Self::flat(theme, s));
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
                let mut list = column![].spacing(6);
                for (app, items) in notify::group_notifications(&self.notifications) {
                    if items.len() > 1 {
                        let expanded = self.expanded.contains(&app);
                        let expand = button(
                            row![
                                column![
                                    text(app.to_uppercase()).size(11).color(theme.accent).font(
                                        Font {
                                            weight: Weight::Bold,
                                            ..Font::DEFAULT
                                        }
                                    ),
                                    text(format!("{} notifications", items.len()))
                                        .size(11)
                                        .color(theme.muted)
                                ]
                                .spacing(3)
                                .width(Length::Fill),
                                Self::icon_label(
                                    icon_glyph(if expanded {
                                        "pan-down-symbolic"
                                    } else {
                                        "pan-end-symbolic"
                                    }),
                                    14.0,
                                    Some(theme.fg)
                                )
                            ]
                            .spacing(8)
                            .align_y(Vertical::Center),
                        )
                        .width(Length::Fill)
                        .padding(iced_core::Padding {
                            top: 10.0,
                            right: 4.0,
                            bottom: 10.0,
                            left: 14.0,
                        })
                        .on_press(Message::StackToggled(app.clone()))
                        .style(move |_, s| Self::notification_button(theme, s));
                        let key = format!("stack:{app}");
                        let close = Self::close_button(theme, Message::StackClosed(app));
                        list = list.push(
                            self.notification_card(
                                row![
                                    expand,
                                    container(close).padding(iced_core::Padding {
                                        right: 8.0,
                                        ..Default::default()
                                    })
                                ]
                                .align_y(Vertical::Center)
                                .into(),
                                key,
                            ),
                        );
                        if expanded {
                            for n in items {
                                list = list.push(self.notif_row(theme, n));
                            }
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
        let scroller = scrollable(content)
            .on_scroll(Message::PanelScrolled)
            .direction(scrollable::Direction::Vertical(
                scrollable::Scrollbar::new().width(0).scroller_width(0),
            ));
        // The overlay draws only; pointer and wheel events reach the scroller.
        let mut body = stack![container(scroller).padding(12)];
        if self.fade_bottom {
            // 64 px over the scrolling content, then an 8 px feather into
            // the bottom inset. The tail returns to the panel's own opacity
            // instead of ending in a more opaque rectangular strip.
            let gradient = iced_core::gradient::Linear::new(std::f32::consts::PI)
                .add_stop(0.0, Color { a: 0.0, ..theme.bg })
                .add_stop(
                    0.30,
                    Color {
                        a: 0.22,
                        ..theme.bg
                    },
                )
                .add_stop(
                    0.60,
                    Color {
                        a: 0.70,
                        ..theme.bg
                    },
                )
                .add_stop(64.0 / 72.0, theme.bg)
                .add_stop(1.0, Color { a: 0.0, ..theme.bg });
            body = body.push(
                container(
                    container(Space::new().width(Length::Fill).height(72)).style(move |_| {
                        container::Style {
                            background: Some(gradient.into()),
                            ..Default::default()
                        }
                    }),
                )
                .width(Length::Fill)
                .height(Length::Fill)
                .padding(iced_core::Padding {
                    left: 12.0,
                    right: 12.0,
                    bottom: 4.0,
                    ..Default::default()
                })
                .align_y(Vertical::Bottom),
            );
        }
        let panel = container(crate::appearance::smooth_height(body.into()))
            .width(Length::Fixed(320.0_f32.min((size.width - 40.0).max(1.0))))
            .max_height((size.height * 0.85).min(size.height - 40.0).max(1.0))
            .style(move |_| {
                let mut s = Self::card(theme, 20.0);
                s.background = Some(theme.bg.into());
                // Outside pixels must stay transparent: a rendered shadow also
                // extends Hyprland's alpha-based background blur beyond the border.
                s
            });
        // Disjoint click catchers: controls never dismiss the backdrop.
        let catcher = || {
            mouse_area(Space::new().width(Length::Fill).height(Length::Fill))
                .on_press(Message::BackdropPressed)
                .on_right_press(Message::BackdropPressed)
        };
        column![
            catcher(),
            row![
                catcher(),
                container(crate::appearance::reveal(
                    panel.into(),
                    self.appearance.opacity
                ))
                .padding(iced_core::Padding {
                    right: 20.0,
                    bottom: 20.0,
                    ..Default::default()
                })
            ]
            .height(Length::Shrink)
        ]
        .width(Length::Fill)
        .height(Length::Fill)
        .into()
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

    fn notif_row(&self, theme: AppTheme, n: Notification) -> Element<'static, Message> {
        let mut lines = column![
            text(n.app.to_uppercase()).size(10).color(theme.accent),
            text(n.summary).size(12).color(theme.fg)
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
            actions = actions.push(text(n.time).size(10).color(theme.muted));
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
        TrayApp::new(AppConfig::parse(AppConfig::default_toml())).0
    }
    #[test]
    fn panel_is_visible_while_initial_queries_are_pending() {
        let mut app = app();
        assert_ne!(app.time_text, "--:--");
        let _ = app.update(Message::Tick);
        assert_eq!(app.refresh_pending, 8);
        assert!(!app.notifications_loaded);
        let view = app.view_at(iced_core::Size::new(1920.0, 1080.0));
        assert!(!view.as_widget().children().is_empty());
        drop(view);
        let _ = app.update(Message::NotifsRefreshed((vec![], None)));
        assert!(app.notifications_loaded);
        assert_eq!(app.refresh_pending, 7);
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
        let _ = app.update(Message::SlidersLoaded(
            0,
            SliderSnapshot {
                volume: Some(20.0),
                ..Default::default()
            },
        ));
        assert_eq!(app.volume, Some(70.0));
    }
    #[test]
    fn small_external_volume_changes_are_observed() {
        let mut app = app();
        app.volume = Some(70.0);
        let _ = app.update(Message::SlidersLoaded(
            0,
            SliderSnapshot {
                volume: Some(69.0),
                ..Default::default()
            },
        ));
        assert_eq!(app.volume, Some(69.0));
    }
    #[test]
    fn periodic_refresh_cannot_queue_overlapping_batches() {
        let mut app = app();
        let _ = app.update(Message::Tick);
        assert_eq!(app.refresh_pending, 8);
        let _ = app.update(Message::Tick);
        assert_eq!(app.refresh_pending, 8);
        let _ = app.update(Message::NotifsLoaded((vec![], None)));
        assert_eq!(app.refresh_pending, 8);
        let _ = app.update(Message::NotifsRefreshed((vec![], None)));
        assert_eq!(app.refresh_pending, 7);
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
        let _ = app.update(Message::NotifsLoaded((vec![n.clone()], None)));
        app.dismissed.insert(42);
        app.notifications.clear();
        let _ = app.update(Message::NotifsLoaded((vec![n], None)));
        assert!(app.notifications.is_empty());
    }
}
