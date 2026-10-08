//! Dusky Center UI: Pure Rust native control center for Arch Linux / Hyprland.
//!
//! Visual parity with Dusky Control Center (GTK) and Dusky Tray using Iced and wgpu.
//! Features:
//! - 3-column quick hero cards with uniform geometry and solid accent highlights.
//! - Lazy initialization of control defaults for background pages.
//! - Native Wayland xdg-toplevel windowing (movable, resizable, tileable).
//! - Dynamic wallpaper theme sync via Matugen.
//! - Shared Matugen slider styling with thicker pill rails.
//! - PickList dropdown for performance profile selection.
//! - Seamless top header bar and auto-hiding slim scrollbars.

use iced::alignment::{Horizontal, Vertical};
use iced::font::Weight;
use iced::keyboard::Key;
use iced::keyboard::key::Named;
use iced::overlay::menu;
use iced::widget::operation;
use iced::widget::{
    Space, button, column, container, mouse_area, pick_list, row, scrollable, slider, text,
    text_input, toggler,
};
use iced::{Border, Color, Event, Length, Padding, Subscription, Task, mouse};

use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::time::Duration;

use crate::backend::controls::{self, ServiceStatus, Snapshot};
use crate::backend::system as sys;
use crate::config::{
    ActionConfig, AppConfig, ChangeAction, ItemConfig, PageConfig, SectionConfig, ToggleActionPair,
};
use crate::icons::render_icon;
use crate::theme::{AppTheme, mix};

pub type Element<'a, Message> = iced::Element<'a, Message>;

const PROFILE_OPTIONS: [&str; 3] = ["Balanced", "Performance", "Power Saver"];

// ---------------------------------------------------------------------------
// Navigation Stack
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub enum NavView {
    Root(usize),
    SubPage {
        parent_page: usize,
        title: String,
        sections: Vec<SectionConfig>,
    },
}

// ---------------------------------------------------------------------------
// Messages
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub enum Message {
    SelectPage(usize),
    PushSubPage {
        title: String,
        sections: Vec<SectionConfig>,
    },
    PopSubPage,
    ToggleSidebar,
    ToggleSearch,
    SearchChanged(String),
    ClearSearch,
    ExecuteAction(ActionConfig),
    ToggleItem {
        key: String,
        is_enabled: bool,
        on_toggle: Option<ToggleActionPair>,
    },
    SliderChanged {
        key: String,
        value: f32,
        on_change: Option<ChangeAction>,
    },
    SliderSettled {
        key: String,
        value: f32,
        on_change: Option<ChangeAction>,
    },
    SliderReleased {
        key: String,
        value: f32,
        on_change: Option<ChangeAction>,
    },
    ProfileSelected(String),
    SelectOption {
        key: String,
        option: String,
        action: Option<ActionConfig>,
    },
    ToggleService {
        unit: String,
        scope: String,
    },
    ControlsLoaded { generation: u64, snapshot: Snapshot },
    ToggleFinished { key: String, previous: bool, result: Result<(), String> },
    ServiceFinished { key: String, previous: bool, result: Result<(), String> },
    EntryChanged { key: String, value: String },
    SubmitEntry { key: String, action: ActionConfig },
    EntryFinished { key: String, result: Result<(), String> },
    DragWindow,
    Tick,
    SunsetFinished,
    CoreLoaded { values: Vec<(String, Option<f32>, Option<f32>)>, sunset_active: bool },
    ReloadConfig,
    CloseApp,
    ItemEntered(String),
    ItemExited(String),
    EventOccurred(Event),
}

// ---------------------------------------------------------------------------
// App State
// ---------------------------------------------------------------------------

pub struct CenterApp {
    pub config: AppConfig,
    pub theme: AppTheme,
    pub nav_stack: Vec<NavView>,
    pub sidebar_open: bool,
    pub search_open: bool,
    pub search_query: String,
    pub loaded_pages: HashSet<usize>,
    pub toggle_states: HashMap<String, bool>,
    pub slider_values: HashMap<String, f32>,
    pub selected_options: HashMap<String, String>,
    pub service_states: HashMap<String, bool>,
    pub live_labels: HashMap<String, String>,
    pub cpu_text: String,
    pub ram_text: String,
    pub active_profile: String,
    pub hovered_item: Option<String>,
    pub active_slider: Option<String>,
    pub sunset_active: bool,
    pub core_loading: bool,
    pub sunset_applying: bool,
    pub sunset_pending: Option<f32>,
    pub closing: bool,
    pub service_statuses: HashMap<String, ServiceStatus>,
    pub busy_controls: HashSet<String>,
    pub unavailable_controls: HashSet<String>,
    pub control_generation: u64,
    pub controls_loading: Option<u64>,
    pub action_error: Option<String>,
    pub entry_values: HashMap<String, String>,
    pub editing_entries: HashSet<String>,
}

impl CenterApp {
    pub fn new(
        mut config: AppConfig,
        initial_page: Option<String>,
        initial_hover: Option<String>,
    ) -> (Self, Task<Message>) {
        expand_app_config_generators(&mut config);
        let (cpu, ram) = sys::cpu_ram();
        let start_page = if let Some(ref target) = initial_page {
            let target_lower = target.to_lowercase();
            config
                .pages
                .iter()
                .position(|p| {
                    p.id.to_lowercase() == target_lower
                        || p.title.to_lowercase() == target_lower
                })
                .unwrap_or(0)
        } else {
            0
        };

        let mut app = Self {
            config,
            theme: AppTheme::load(),
            nav_stack: vec![NavView::Root(start_page)],
            sidebar_open: true,
            search_open: false,
            search_query: String::new(),
            loaded_pages: HashSet::new(),
            toggle_states: HashMap::new(),
            slider_values: HashMap::new(),
            selected_options: HashMap::new(),
            service_states: HashMap::new(),
            live_labels: HashMap::new(),
            cpu_text: cpu,
            ram_text: ram,
            active_profile: "Balanced".to_string(),
            hovered_item: initial_hover,
            active_slider: None,
            sunset_active: false,
            core_loading: false,
            sunset_applying: false,
            sunset_pending: None,
            closing: false,
            service_statuses: HashMap::new(),
            busy_controls: HashSet::new(),
            unavailable_controls: HashSet::new(),
            control_generation: 0,
            controls_loading: None,
            action_error: None,
            entry_values: HashMap::new(),
            editing_entries: HashSet::new(),
        };

        // LAZY LOADING: Only initialize active page on cold start!
        app.lazy_load_page(start_page);
        let task = Task::batch([app.refresh_core(), app.refresh_controls()]);
        (app, task)
    }

    pub fn active_page_idx(&self) -> usize {
        match self.nav_stack.last() {
            Some(NavView::Root(idx)) => *idx,
            Some(NavView::SubPage { parent_page, .. }) => *parent_page,
            None => 0,
        }
    }

    /// Lazy-load defaults and initial states for a specific page.
    fn lazy_load_page(&mut self, page_idx: usize) {
        if self.loaded_pages.contains(&page_idx) {
            return;
        }
        self.loaded_pages.insert(page_idx);

        if let Some(page) = self.config.pages.get(page_idx) {
            for section in &page.layout {
                for item in &section.items {
                    collect_item_defaults(
                        item,
                        &mut self.slider_values,
                        &mut self.toggle_states,
                        &mut self.service_states,
                        &mut self.selected_options,
                    );
                }
            }
        }
    }

    /// Keep blocking hardware IPC off initialization and the UI event loop.
    fn refresh_core(&mut self) -> Task<Message> {
        if self.core_loading { return Task::none(); }
        self.core_loading = true;
        let previous: Vec<_> = ["Volume", "Microphone", "Brightness", "Night Light"]
            .into_iter().map(|key| (key.to_string(), self.slider_values.get(key).copied())).collect();
        Task::perform(async move {
            let volume = sys::get_volume();
            let microphone = sys::get_microphone_volume();
            let brightness = sys::get_brightness();
            let sunset_active = sys::is_sunset_active();
            let sunset = if sunset_active { sys::get_sunset() } else { None };
            let values = previous.into_iter().zip([volume, microphone, brightness, sunset])
                .map(|((key, previous), value)| (key, previous, value)).collect();
            Message::CoreLoaded { values, sunset_active }
        }, |message| message)
    }

    fn apply_pending_sunset(&mut self) -> Task<Message> {
        if self.sunset_applying { return Task::none(); }
        let Some(value) = self.sunset_pending.take() else { return Task::none(); };
        self.sunset_applying = true;
        Task::perform(async move { sys::apply_sunset(value); }, |_| Message::SunsetFinished)
    }

    fn sync_slider(&mut self, key: &str, value: f32) {
        if self.active_slider.as_deref() != Some(key) {
            self.slider_values.insert(key.into(), value);
        }
    }

    // ---------------------------------------------------------------------------
    // Update Loop
    // ---------------------------------------------------------------------------

    pub fn update(&mut self, message: Message) -> Task<Message> {
        match message {
            Message::SelectPage(idx) => {
                if idx < self.config.pages.len() {
                    self.lazy_load_page(idx);
                    self.nav_stack = vec![NavView::Root(idx)];
                    self.search_query.clear();
                    self.control_generation += 1;
                    return self.refresh_controls();
                }
                Task::none()
            }

            Message::PushSubPage { title, sections } => {
                let parent = self.active_page_idx();
                for section in &sections {
                    for item in &section.items {
                        collect_item_defaults(item, &mut self.slider_values, &mut self.toggle_states,
                            &mut self.service_states, &mut self.selected_options);
                    }
                }
                self.nav_stack.push(NavView::SubPage {
                    parent_page: parent,
                    title,
                    sections,
                });
                self.search_query.clear();
                self.control_generation += 1;
                self.refresh_controls()
            }

            Message::PopSubPage => {
                if self.nav_stack.len() > 1 {
                    self.nav_stack.pop();
                }
                self.control_generation += 1;
                self.refresh_controls()
            }

            Message::ToggleSidebar => {
                self.sidebar_open = !self.sidebar_open;
                Task::none()
            }

            Message::ToggleSearch => {
                self.search_open = !self.search_open;
                if self.search_open {
                    operation::focus("search_input")
                } else {
                    self.search_query.clear();
                    Task::none()
                }
            }

            Message::SearchChanged(q) => {
                self.search_query = q;
                Task::none()
            }

            Message::ClearSearch => {
                self.search_query.clear();
                self.search_open = false;
                Task::none()
            }

            Message::ExecuteAction(ActionConfig::Reload) => self.update(Message::ReloadConfig),

            Message::ExecuteAction(action) => {
                self.dispatch_action(&action);
                if matches!(action, ActionConfig::Redirect { .. }) { self.refresh_controls() } else { Task::none() }
            }

            Message::ToggleItem { key, is_enabled, on_toggle } => {
                if self.busy_controls.contains(&key) { return Task::none(); }
                let previous = self.toggle_states.get(&key).copied().unwrap_or(false);
                self.toggle_states.insert(key.clone(), is_enabled);
                let Some(item) = self.visible_items().into_iter().find(|i| controls::item_key(i) == key) else {
                    return Task::none(); // Local expander state.
                };
                self.busy_controls.insert(key.clone());
                self.control_generation += 1;
                self.action_error = None;
                let action = on_toggle.map(|pair| if is_enabled { pair.enabled } else { pair.disabled });
                Task::perform(async move {
                    controls::set_toggle(&item, is_enabled, action.as_ref())
                }, move |result| Message::ToggleFinished { key: key.clone(), previous, result })
            }

            Message::ToggleFinished { key, previous, result } => {
                self.busy_controls.remove(&key);
                if let Err(error) = result {
                    self.toggle_states.insert(key.clone(), previous);
                    self.action_error = Some(format!("{key}: {error}"));
                }
                self.control_generation += 1;
                self.refresh_controls()
            }

            Message::ServiceFinished { key, previous, result } => {
                self.busy_controls.remove(&key);
                if let Err(error) = result {
                    self.service_states.insert(key.clone(), previous);
                    self.action_error = Some(format!("{key}: {error}"));
                }
                self.control_generation += 1;
                self.refresh_controls()
            }

            Message::ControlsLoaded { generation, snapshot } => {
                if self.controls_loading == Some(generation) { self.controls_loading = None; }
                if self.control_generation != generation { return self.refresh_controls(); }
                for (key, value) in snapshot.toggles {
                    if self.busy_controls.contains(&key) { continue; }
                    if let Some(value) = value {
                        self.toggle_states.insert(key.clone(), value);
                        self.unavailable_controls.remove(&key);
                    } else { self.unavailable_controls.insert(key); }
                }
                for (key, status) in snapshot.services {
                    if self.busy_controls.contains(&key) { continue; }
                    if status.load != "unavailable" {
                        self.service_states.insert(key.clone(), status.enabled());
                    }
                    self.service_statuses.insert(key, status);
                }
                for (key, value) in snapshot.selections {
                    if key == "Active Profile" { self.active_profile = value.clone(); }
                    self.selected_options.insert(key, value);
                }
                for (key, value) in snapshot.sliders { self.sync_slider(&key, value); }
                self.live_labels.extend(snapshot.labels);
                for (key, value) in snapshot.entries {
                    if !self.editing_entries.contains(&key) && !self.busy_controls.contains(&key) {
                        self.entry_values.insert(key, value);
                    }
                }
                Task::none()
            }

            Message::EntryChanged { key, value } => {
                self.editing_entries.insert(key.clone());
                self.entry_values.insert(key, value);
                Task::none()
            }

            Message::SubmitEntry { key, action } => {
                if self.busy_controls.contains(&key) { return Task::none(); }
                let value = self.entry_values.get(&key).cloned().unwrap_or_default();
                let action = controls::with_value(&action, &value);
                self.busy_controls.insert(key.clone());
                self.control_generation += 1;
                self.action_error = None;
                Task::perform(async move { controls::run_action(&action) }, move |result| {
                    Message::EntryFinished { key: key.clone(), result }
                })
            }

            Message::EntryFinished { key, result } => {
                self.busy_controls.remove(&key);
                match result {
                    Ok(()) => { self.editing_entries.remove(&key); }
                    Err(error) => { self.action_error = Some(format!("{key}: {error}")); }
                }
                self.control_generation += 1;
                self.refresh_controls()
            }

            Message::SliderChanged { key, value, on_change } => {
                if self.closing { return Task::none(); }
                self.active_slider = Some(key.clone());
                self.slider_values.insert(key.clone(), value);
                if key == "Night Light" {
                    // Coalesce IPC while retaining immediate visual feedback.
                    self.sunset_pending = Some(value);
                }
                // Iced emits on_release for pointer drags, but not arrow keys or
                // Ctrl+wheel. Commit a settled value for those interactions too.
                let settle = Task::perform(async move {
                    futures_timer::Delay::new(Duration::from_millis(100)).await;
                    Message::SliderSettled { key, value, on_change }
                }, |message| message);
                Task::batch([settle, self.apply_pending_sunset()])
            }

            Message::SliderSettled { key, value, on_change } => {
                if self.active_slider.as_deref() == Some(&key)
                    && self.slider_values.get(&key) == Some(&value)
                {
                    return self.update(Message::SliderReleased { key, value, on_change });
                }
                Task::none()
            }

            Message::SliderReleased {
                key,
                value,
                on_change,
            } => {
                if self.closing { return Task::none(); }
                let value = self.slider_values.get(&key).copied().unwrap_or(value);
                self.active_slider = None;
                self.slider_values.insert(key.clone(), value);
                if key == "Night Light" {
                    self.sunset_pending = Some(value);
                    return self.apply_pending_sunset();
                } else if let Some(ChangeAction::Direct(action)) = on_change {
                    self.dispatch_action_with_value(&action, value);
                }
                Task::none()
            }

            Message::ProfileSelected(profile) => {
                self.active_profile = profile.clone();
                let action = self.visible_items().into_iter().find(|item| controls::item_key(item) == "Active Profile")
                    .and_then(|item| match item.on_change {
                        Some(ChangeAction::Map(map)) => map.get(&profile).cloned(),
                        Some(ChangeAction::Direct(action)) => Some(action),
                        None => None,
                    });
                if let Some(action) = action { self.dispatch_action(&action); }
                Task::none()
            }

            Message::SelectOption {
                key,
                option,
                action,
            } => {
                self.selected_options.insert(key, option.clone());
                if let Some(act) = action {
                    self.dispatch_action(&controls::with_value(&act, &option));
                }
                Task::none()
            }

            Message::ToggleService { unit, scope } => {
                let key = controls::service_key(&scope, &unit);
                if self.busy_controls.contains(&key) { return Task::none(); }
                let previous = self.service_states.get(&key).copied().unwrap_or(false);
                self.service_states.insert(key.clone(), !previous);
                self.busy_controls.insert(key.clone());
                self.control_generation += 1;
                self.action_error = None;
                Task::perform(async move {
                    controls::set_service(&scope, &unit, !previous)
                }, move |result| Message::ServiceFinished { key: key.clone(), previous, result })
            }

            Message::DragWindow => iced::window::latest().and_then(iced::window::drag),

            Message::Tick => {
                let (cpu, ram) = sys::cpu_ram();
                self.cpu_text = cpu;
                self.ram_text = ram;

                // Dynamically sync theme palette with wallpaper
                if let Some(theme) = AppTheme::load_generated() { self.theme = theme; }
                Task::batch([self.refresh_core(), self.refresh_controls()])
            }

            Message::SunsetFinished => {
                self.sunset_applying = false;
                if self.closing && self.sunset_pending.is_none() { return iced::exit(); }
                self.apply_pending_sunset()
            }

            Message::CoreLoaded { values, sunset_active } => {
                self.core_loading = false;
                self.sunset_active = sunset_active;
                for (key, previous, value) in values {
                    // A read begun before a slider edit must not undo that edit,
                    // even if the user has already released the handle.
                    if !(key == "Night Light" && (self.sunset_applying || self.sunset_pending.is_some()))
                        && self.slider_values.get(&key).copied() == previous
                        && let Some(value) = value
                    { self.sync_slider(&key, value); }
                }
                Task::none()
            }

            Message::CloseApp | Message::EventOccurred(Event::Window(iced::window::Event::CloseRequested)) => {
                // Finish the latest queued Night Light value before releasing
                // workers; ordinary close remains immediate.
                self.closing = true;
                if self.sunset_applying { Task::none() }
                else if self.sunset_pending.is_some() { self.apply_pending_sunset() }
                else { iced::exit() }
            },

            Message::ItemEntered(key) => {
                self.hovered_item = Some(key);
                Task::none()
            }

            Message::ItemExited(key) => {
                if self.hovered_item.as_deref() == Some(&key) {
                    self.hovered_item = None;
                }
                Task::none()
            }

            Message::ReloadConfig => {
                if let Some(theme) = AppTheme::load_generated() { self.theme = theme; }
                match AppConfig::load() {
                    Ok(mut cfg) => {
                        expand_app_config_generators(&mut cfg);
                        self.config = cfg;
                        self.loaded_pages.clear();
                        self.nav_stack = vec![NavView::Root(self.active_page_idx().min(self.config.pages.len().saturating_sub(1)))];
                        self.lazy_load_page(self.active_page_idx());
                        self.control_generation += 1;
                        self.action_error = None;
                    }
                    Err(error) => { self.action_error = Some(format!("Reload: {error}")); }
                }
                self.refresh_controls()
            }

            Message::EventOccurred(Event::Keyboard(iced::keyboard::Event::KeyPressed {
                key,
                modifiers,
                ..
            })) => {
                if key == Key::Named(Named::Escape) {
                    if !self.search_query.is_empty() {
                        self.search_query.clear();
                        self.search_open = false;
                        return Task::none();
                    }
                    if self.nav_stack.len() > 1 {
                        return self.update(Message::PopSubPage);
                    }
                    return self.update(Message::CloseApp);
                }
                if modifiers.control()
                    && matches!(key, Key::Character(ref c) if c == "r" || c == "R")
                {
                    return self.update(Message::ReloadConfig);
                }
                if modifiers.control()
                    && matches!(key, Key::Character(ref c) if c == "f" || c == "F")
                {
                    self.search_open = true;
                    return operation::focus("search_input");
                }
                Task::none()
            }

            Message::EventOccurred(_) => Task::none(),
        }
    }

    fn dispatch_action(&mut self, action: &ActionConfig) {
        if let ActionConfig::Redirect { page } = action {
            if let Some(index) = self.config.pages.iter().position(|p| &p.id == page) {
                self.lazy_load_page(index);
                self.nav_stack = vec![NavView::Root(index)];
                self.control_generation += 1;
            }
        } else if let Err(error) = controls::launch(action) {
            self.action_error = Some(error);
        }
    }

    fn dispatch_action_with_value(&mut self, action: &ActionConfig, value: f32) {
        let mut action = action.clone();
        let value = format!("{}", value.round() as i64);
        match &mut action {
            ActionConfig::Exec { command, argv, .. } => {
                *command = command.replace("{value}", &value).replace("$VALUE", &value);
                for arg in argv { *arg = arg.replace("{value}", &value).replace("$VALUE", &value); }
            }
            ActionConfig::Argv { argv, .. } => {
                for arg in argv { *arg = arg.replace("{value}", &value).replace("$VALUE", &value); }
            }
            _ => (),
        }
        self.dispatch_action(&action);
    }

    fn visible_items(&self) -> Vec<ItemConfig> {
        let sections = match self.nav_stack.last() {
            Some(NavView::SubPage { sections, .. }) => sections.as_slice(),
            _ => self.config.pages.get(self.active_page_idx()).map(|p| p.layout.as_slice()).unwrap_or(&[]),
        };
        fn collect(items: &[ItemConfig], output: &mut Vec<ItemConfig>) {
            for item in items {
                output.push(item.clone());
                collect(&item.items, output);
            }
        }
        let mut items = vec![];
        for section in sections { collect(&section.items, &mut items); }
        items
    }

    fn refresh_controls(&mut self) -> Task<Message> {
        if self.controls_loading.is_some() { return Task::none(); }
        let generation = self.control_generation;
        self.controls_loading = Some(generation);
        let items = self.visible_items();
        Task::perform(async move { controls::query(&items) }, move |snapshot| {
            Message::ControlsLoaded { generation, snapshot }
        })
    }

    pub fn subscription(&self) -> Subscription<Message> {
        let events = iced::event::listen_with(|event, status, _| match (&status, &event) {
            (
                iced::event::Status::Captured,
                Event::Keyboard(iced::keyboard::Event::KeyPressed {
                    key: Key::Named(Named::Escape),
                    ..
                }),
            ) => Some(Message::EventOccurred(event)),
            (iced::event::Status::Ignored, _) => Some(Message::EventOccurred(event)),
            _ => None,
        });
        Subscription::batch([events, Subscription::run(Self::ticks)])
    }

    fn ticks() -> impl iced_futures::futures::Stream<Item = Message> {
        iced_futures::futures::stream::unfold((), |()| async {
            futures_timer::Delay::new(Duration::from_secs(3)).await;
            Some((Message::Tick, ()))
        })
    }

    // ---------------------------------------------------------------------------
    // View Root: Unified, Seamless Window (No Disjoint Header Bar)
    // ---------------------------------------------------------------------------

    pub fn view<'a>(&'a self) -> Element<'a, Message> {
        let palette = self.theme;

        row![
            if self.sidebar_open {
                self.view_sidebar()
            } else {
                container(Space::new().width(0).height(Length::Fill)).into()
            },
            if self.sidebar_open {
                container(Space::new().width(1).height(Length::Fill)).style(move |_| {
                    container::Style {
                        background: Some(
                            Color::from_rgba(
                                palette.border.r,
                                palette.border.g,
                                palette.border.b,
                                0.25,
                            )
                            .into(),
                        ),
                        ..Default::default()
                    }
                })
            } else {
                container(Space::new().width(0).height(0))
            },
            self.view_main_column(),
        ]
        .width(Length::Fill)
        .height(Length::Fill)
        .into()
    }

    // ---------------------------------------------------------------------------
    // Sidebar: Hidden/Hover-Only Slim Scrollbar
    // ---------------------------------------------------------------------------

    fn view_sidebar<'a>(&'a self) -> Element<'a, Message> {
        let palette = self.theme;
        let active_idx = self.active_page_idx();

        let search_btn = button(
            container(render_icon("search", 15.0, palette.fg))
                .align_x(Horizontal::Center)
                .align_y(Vertical::Center),
        )
        .on_press(Message::ToggleSearch)
        .width(26)
        .height(26)
        .padding([4, 6])
        .style(move |_, status| button::Style {
            background: Some(
                if status == button::Status::Hovered || self.search_open {
                    palette.card_hover
                } else {
                    palette.card_bg
                }
                .into(),
            ),
            border: Border {
                color: Color::from_rgba(palette.border.r, palette.border.g, palette.border.b, 0.30),
                width: 1.0,
                radius: 8.0.into(),
            },
            ..Default::default()
        });

        let sidebar_header = row![
            Space::new().width(26),
            text("Dusky")
                .size(15)
                .font(iced::Font {
                    weight: Weight::Bold,
                    ..Default::default()
                })
                .color(palette.fg)
                .width(Length::Fill)
                .align_x(Horizontal::Center),
            search_btn,
        ]
        .padding([8, 6])
        .align_y(Vertical::Center);

        let mut page_list = column![].spacing(2);

        for (idx, page) in self.config.pages.iter().enumerate() {
            let is_active = idx == active_idx && self.search_query.is_empty();
            let icon_name = if !page.icon.is_empty() {
                &page.icon
            } else {
                &page.id
            };

            let fg_color = palette.fg;
            let icon_color = palette.accent;

            let row_content = row![
                container(Space::new().width(3).height(16)).style(move |_| container::Style {
                    background: Some(if is_active { palette.accent } else { Color::TRANSPARENT }.into()),
                    border: Border { radius: 1.5.into(), ..Default::default() },
                    ..Default::default()
                }),
                render_icon(icon_name, 18.0, icon_color),
                text(&page.title)
                    .size(13)
                    .font(iced::Font {
                        weight: if is_active {
                            Weight::Semibold
                        } else {
                            Weight::Normal
                        },
                        ..Default::default()
                    })
                    .color(fg_color),
            ]
            .spacing(6)
            .align_y(Vertical::Center);

            let btn = button(container(row_content).padding([8, 6]).width(Length::Fill))
                .padding(0)
                .on_press(Message::SelectPage(idx))
                .style(move |_, status| {
                    let hovered = matches!(status, button::Status::Hovered | button::Status::Pressed);
                    let bg = if is_active {
                        mix(palette.sidebar_bg, palette.accent, if hovered { 0.12 } else { 0.07 })
                    } else if hovered {
                        mix(palette.sidebar_bg, palette.fg, 0.05)
                    } else {
                        Color::TRANSPARENT
                    };
                    button::Style {
                        background: Some(bg.into()),
                        border: Border {
                            radius: 8.0.into(),
                            ..Default::default()
                        },
                        ..Default::default()
                    }
                });

            page_list = page_list.push(btn);
        }

        let scroll_pages = make_slim_scrollable(page_list, Padding::from([2, 6]), palette.accent);

        let sidebar_col = column![
            mouse_area(sidebar_header).on_press(Message::DragWindow),
            scroll_pages
        ]
        .width(158)
        .height(Length::Fill);

        container(sidebar_col)
            .style(move |_| container::Style {
                background: Some(palette.sidebar_bg.into()),
                ..Default::default()
            })
            .into()
    }

    // ---------------------------------------------------------------------------
    // Main Area: Seamless, Unified Header & Content
    // ---------------------------------------------------------------------------

    fn view_main_column<'a>(&'a self) -> Element<'a, Message> {
        let palette = self.theme;
        let active_title = match self.nav_stack.last() {
            Some(NavView::SubPage { title, .. }) => title.as_str(),
            Some(NavView::Root(idx)) => {
                if let Some(page) = self.config.pages.get(*idx) {
                    page.title.as_str()
                } else {
                    "Home"
                }
            }
            None => "Home",
        };

        // Sidebar split toggle button (Sleek circle, perfectly centered)
        let sidebar_toggle_btn = button(
            container(render_icon("sidebar", 14.0, palette.fg))
                .width(Length::Fill)
                .height(Length::Fill)
                .align_x(Horizontal::Center)
                .align_y(Vertical::Center),
        )
        .on_press(Message::ToggleSidebar)
        .padding(0)
        .width(26)
        .height(26)
        .style(move |_, status| button::Style {
            background: Some(
                if status == button::Status::Hovered {
                    palette.card_hover
                } else {
                    palette.card_bg
                }
                .into(),
            ),
            border: Border {
                color: palette.border,
                width: 1.0,
                radius: 13.0.into(),
            },
            ..Default::default()
        });

        // Main Title (Seamless directly over the content background)
        let title_label = text(active_title)
            .size(15)
            .font(iced::Font {
                weight: Weight::Bold,
                ..Default::default()
            })
            .color(palette.fg);

        // Red hover feedback without a permanent background.
        let close_btn = button(
            container(render_icon("close", 12.0, palette.fg))
                .width(Length::Fill).height(Length::Fill)
                .align_x(Horizontal::Center).align_y(Vertical::Center),
        )
        .on_press(Message::CloseApp).padding(0).width(26).height(26)
        .style(move |_, status| button::Style {
            background: if matches!(status, button::Status::Hovered | button::Status::Pressed) {
                Some(palette.danger_bg.into())
            } else { None },
            border: Border { radius: 13.0.into(), ..Default::default() },
            ..Default::default()
        });

        // Completely seamless header: same background, no bottom border cutting across!
        let main_header = row![
            sidebar_toggle_btn,
            Space::new().width(Length::Fill),
            title_label,
            Space::new().width(Length::Fill),
            close_btn,
        ]
        .spacing(10)
        .align_y(Vertical::Center)
        .padding([8, 18]);

        let mut main_col = column![mouse_area(main_header).on_press(Message::DragWindow)];
        if let Some(error) = &self.action_error {
            main_col = main_col.push(container(text(error).size(12).color(palette.danger))
                .padding([6, 18]).width(Length::Fill));
        }

        // Search bar with auto-focus support and elegant capsule styling
        if self.search_open || !self.search_query.is_empty() {
            let search_icon = render_icon(
                "search",
                15.0,
                if self.search_query.is_empty() {
                    palette.fg_muted
                } else {
                    palette.accent
                },
            );

            let search_input = text_input("Search controls, services, tools...", &self.search_query)
                .id("search_input")
                .on_input(Message::SearchChanged)
                .padding([6, 6])
                .size(13)
                .style(move |_, _| text_input::Style {
                    background: Color::TRANSPARENT.into(),
                    border: Border::default(),
                    icon: palette.fg_muted,
                    placeholder: palette.fg_muted,
                    value: palette.fg,
                    selection: palette.accent,
                });

            let clear_btn = if !self.search_query.is_empty() {
                button(render_icon("close", 13.0, palette.fg_muted))
                    .on_press(Message::ClearSearch)
                    .padding([4, 6])
                    .style(move |_, status| button::Style {
                        background: Some(
                            if status == button::Status::Hovered {
                                palette.card_hover.into()
                            } else {
                                Color::TRANSPARENT.into()
                            },
                        ),
                        border: Border {
                            radius: 6.0.into(),
                            ..Default::default()
                        },
                        ..Default::default()
                    })
            } else {
                button(Space::new().width(0))
                    .padding([0, 0])
                    .style(|_, _| button::Style::default())
            };

            let inner_row = row![
                search_icon,
                search_input.width(Length::Fill),
                clear_btn,
            ]
            .spacing(10)
            .align_y(Vertical::Center)
            .padding([4, 12]);

            let search_bar = container(
                container(inner_row)
                    .style(move |_| container::Style {
                        background: Some(palette.card_bg.into()),
                        border: Border {
                            color: if self.search_query.is_empty() {
                                palette.border
                            } else {
                                palette.accent
                            },
                            width: 1.0,
                            radius: 10.0.into(),
                        },
                        ..Default::default()
                    })
                    .width(Length::Fill)
                    .max_width(560.0),
            )
            .width(Length::Fill)
            .align_x(Horizontal::Center)
            .padding(Padding {
                top: 0.0,
                right: 18.0,
                bottom: 12.0,
                left: 18.0,
            });

            main_col = main_col.push(search_bar);
        }

        let content = if !self.search_query.is_empty() {
            self.view_search_results()
        } else {
            match self.nav_stack.last() {
                Some(NavView::SubPage { title, sections, .. }) => {
                    self.view_subpage_content(title, sections)
                }
                Some(NavView::Root(idx)) => {
                    if let Some(page) = self.config.pages.get(*idx) {
                        self.view_page_content(page)
                    } else {
                        self.view_empty()
                    }
                }
                None => self.view_empty(),
            }
        };

        main_col = main_col.push(content);

        container(main_col.width(Length::Fill).height(Length::Fill))
            .style(move |_| container::Style {
                background: Some(palette.bg.into()),
                ..Default::default()
            })
            .width(Length::Fill)
            .height(Length::Fill)
            .into()
    }

    fn view_empty<'a>(&'a self) -> Element<'a, Message> {
        container(text("No controls available").color(self.theme.fg_muted))
            .width(Length::Fill)
            .height(Length::Fill)
            .align_x(Horizontal::Center)
            .align_y(Vertical::Center)
            .into()
    }

    // ---------------------------------------------------------------------------
    // Search Results
    // ---------------------------------------------------------------------------

    fn view_search_results<'a>(&'a self) -> Element<'a, Message> {
        let palette = self.theme;
        let query = self.search_query.to_lowercase();
        let total_hits = self.config.pages.iter()
            .flat_map(|page| &page.layout)
            .flat_map(|section| &section.items)
            .filter(|item| item.matches_query(&query))
            .count();
        let mut results_box = column![].spacing(0);
        let mut hit_count = 0;
        for (page_idx, page) in self.config.pages.iter().enumerate() {
            for section in &page.layout {
                for item in &section.items {
                    if item.matches_query(&query) {
                        if hit_count > 0 {
                            let divider = container(Space::new().width(Length::Fill).height(1))
                                .style(move |_| container::Style {
                                    background: Some(
                                        Color::from_rgba(
                                            palette.border.r,
                                            palette.border.g,
                                            palette.border.b,
                                            0.20,
                                        )
                                        .into(),
                                    ),
                                    ..Default::default()
                                });
                            results_box = results_box.push(divider);
                        }
                        hit_count += 1;
                        let item_view = self.view_item_row_rounded(
                            item, &page.title, Some(page_idx), row_radius(hit_count - 1, total_hits),
                        );
                        results_box = results_box.push(item_view);
                    }
                }
            }
        }

        let header = container(
            row![text(format!("Found {hit_count} results for \"{}\"", self.search_query))
                .size(13)
                .font(iced::Font {
                    weight: Weight::Semibold,
                    ..Default::default()
                })
                .color(palette.accent)]
            .padding([8, 16]),
        );

        if hit_count == 0 {
            return container(
                column![
                    render_icon("search", 32.0, palette.fg_muted),
                    text(format!("No controls matching \"{}\"", self.search_query))
                        .size(14)
                        .color(palette.fg_muted),
                ]
                .spacing(12)
                .align_x(Horizontal::Center),
            )
            .width(Length::Fill)
            .height(Length::Fill)
            .align_x(Horizontal::Center)
            .align_y(Vertical::Center)
            .into();
        }

        let results_card = container(results_box)
            .padding(1)
            .style(move |_| container::Style {
                background: Some(palette.card_bg.into()),
                border: Border {
                    color: Color::from_rgba(
                        palette.border.r,
                        palette.border.g,
                        palette.border.b,
                        0.30,
                    ),
                    width: 1.0,
                    radius: 14.0.into(),
                },
                ..Default::default()
            });

        let clamped_results = container(column![header, results_card].width(Length::Fill).max_width(560.0))
            .width(Length::Fill)
            .align_x(Horizontal::Center);

        column![
            make_slim_scrollable(
                clamped_results,
                Padding {
                    top: 0.0,
                    right: 18.0,
                    bottom: 20.0,
                    left: 18.0,
                },
                palette.accent,
            ),
        ]
        .into()
    }

    // ---------------------------------------------------------------------------
    // Page Content & Sections
    // ---------------------------------------------------------------------------

    fn view_page_content<'a>(&'a self, page: &'a PageConfig) -> Element<'a, Message> {
        let palette = self.theme;
        let mut sections_col = column![].spacing(16);

        for section in &page.layout {
            sections_col = sections_col.push(self.view_section(section));
        }

        let clamped_content = container(sections_col.width(Length::Fill).max_width(560.0))
            .width(Length::Fill)
            .align_x(Horizontal::Center);

        make_slim_scrollable(
            clamped_content,
            Padding {
                top: 4.0,
                right: 18.0,
                bottom: 24.0,
                left: 18.0,
            },
            palette.accent,
        )
        .into()
    }

    fn view_subpage_content<'a>(
        &'a self,
        title: &'a str,
        sections: &'a [SectionConfig],
    ) -> Element<'a, Message> {
        let palette = self.theme;

        let back_btn = button(
            row![
                render_icon("chevron_left", 16.0, palette.accent),
                text("Back").size(13).color(palette.accent),
            ]
            .spacing(6)
            .align_y(Vertical::Center),
        )
        .on_press(Message::PopSubPage)
        .padding([6, 10])
        .style(|_, _| button::Style {
            background: Some(Color::TRANSPARENT.into()),
            ..Default::default()
        });

        let header = container(
            row![
                back_btn,
                text(title)
                    .size(16)
                    .font(iced::Font {
                        weight: Weight::Bold,
                        ..Default::default()
                    })
                    .color(palette.fg),
            ]
            .spacing(10)
            .align_y(Vertical::Center),
        )
        .padding(Padding {
            top: 0.0,
            right: 0.0,
            bottom: 8.0,
            left: 0.0,
        });

        let mut sections_col = column![header].spacing(16);

        for section in sections {
            sections_col = sections_col.push(self.view_section(section));
        }

        let clamped_content = container(sections_col.width(Length::Fill).max_width(560.0))
            .width(Length::Fill)
            .align_x(Horizontal::Center);

        make_slim_scrollable(
            clamped_content,
            Padding {
                top: 4.0,
                right: 18.0,
                bottom: 24.0,
                left: 18.0,
            },
            palette.accent,
        )
        .into()
    }

    // ---------------------------------------------------------------------------
    // Section Views
    // ---------------------------------------------------------------------------

    fn view_section<'a>(&'a self, section: &'a SectionConfig) -> Element<'a, Message> {
        let palette = self.theme;

        // 1. Grid section: Strict 3-column top hero grid with identical tile heights
        if section.section_type == "grid_section" {
            let mut grid_rows = column![].spacing(10);
            let mut current_row = row![].spacing(10);
            let mut in_row = 0;

            for item in &section.items {
                let card = self.view_hero_card(item);
                current_row = current_row.push(card);
                in_row += 1;
                // Exactly 3 columns per row!
                if in_row == 3 {
                    grid_rows = grid_rows.push(current_row);
                    current_row = row![].spacing(10);
                    in_row = 0;
                }
            }
            if in_row > 0 {
                while in_row < 3 {
                    current_row = current_row.push(Space::new().width(Length::FillPortion(1)));
                    in_row += 1;
                }
                grid_rows = grid_rows.push(current_row);
            }

            return grid_rows.width(Length::Fill).into();
        }

        // Warning banner / compact informational banner
        if section.section_type == "warning_banner" {
            let msg = &section.properties.message;
            let title = &section.properties.title;
            let display_text = if !msg.is_empty() { msg } else { title };
            if display_text.is_empty() {
                return column![].into();
            }

            let icon_elem = render_icon("dialog-information-symbolic", 15.0, palette.accent);
            let banner_content = row![
                icon_elem,
                text(display_text)
                    .size(12)
                    .color(palette.fg_muted),
            ]
            .spacing(8)
            .align_y(Vertical::Center);

            return container(banner_content)
                .padding([8, 12])
                .width(Length::Fill)
                .style(move |_| container::Style {
                    background: Some(Color::from_rgba(palette.accent.r, palette.accent.g, palette.accent.b, 0.08).into()),
                    border: Border {
                        color: Color::from_rgba(palette.accent.r, palette.accent.g, palette.accent.b, 0.22),
                        width: 1.0,
                        radius: 10.0.into(),
                    },
                    ..Default::default()
                })
                .into();
        }

        // Avoid rendering an empty container card if a section has 0 items (e.g. unprivileged / empty generator)
        if section.items.is_empty() {
            return column![].into();
        }

        // 2. Standard section with title and boxed-list container
        let mut sec_col = column![].spacing(8);

        if !section.properties.title.is_empty() {
            sec_col = sec_col.push(
                text(&section.properties.title)
                    .size(14)
                    .font(iced::Font {
                        weight: Weight::Bold,
                        ..Default::default()
                    })
                    .color(palette.fg),
            );
        }

        if !section.properties.description.is_empty() {
            sec_col = sec_col.push(text(&section.properties.description).size(12)
                .color(mix(palette.bg, palette.fg, 0.46)));
        }

        // Special handling for Power Management (with PickList dropdown)
        if section.properties.title == "Power Management" {
            sec_col = sec_col.push(self.view_power_management_card(section));
            return sec_col.into();
        }

        // Slider groups use an explicit section type so titles remain editable.
        if section.section_type == "controls" || section.properties.title == "Quick Controls" {
            sec_col = sec_col.push(self.view_quick_controls_card(section));
            return sec_col.into();
        }

        // Boxed List Container for standard section items (matching GTK Adw.PreferencesGroup)
        let mut items_box = column![].spacing(0);
        for (idx, item) in section.items.iter().enumerate() {
            if idx > 0 {
                let divider = container(Space::new().width(Length::Fill).height(1))
                    .style(move |_| container::Style {
                        background: Some(
                            Color::from_rgba(
                                palette.border.r,
                                palette.border.g,
                                palette.border.b,
                                0.20,
                            )
                            .into(),
                        ),
                        ..Default::default()
                    });
                items_box = items_box.push(divider);
            }
            items_box = items_box.push(self.view_item_row_rounded(
                item, "", None, row_radius(idx, section.items.len()),
            ));
        }

        let container_card = container(items_box)
            .padding(1)
            .style(move |_| container::Style {
                background: Some(palette.card_bg.into()),
                border: Border {
                    color: Color::from_rgba(
                        palette.border.r,
                        palette.border.g,
                        palette.border.b,
                        0.30,
                    ),
                    width: 1.0,
                    radius: 14.0.into(),
                },
                ..Default::default()
            });

        sec_col = sec_col.push(container_card);
        sec_col.into()
    }

    // ---------------------------------------------------------------------------
    // Hero Card (3-Column Layout, Uniform Fixed Height for All Rows)
    // ---------------------------------------------------------------------------

    fn view_hero_card<'a>(&'a self, item: &'a ItemConfig) -> Element<'a, Message> {
        let palette = self.theme;
        let is_toggle = item.item_type == "toggle_card";
        let is_service = item.item_type == "service_card";

        let key = if !item.properties.key.is_empty() {
            item.properties.key.clone()
        } else {
            item.properties.title.clone()
        };

        let service_key = controls::service_key(&item.properties.scope, &item.properties.service);
        let is_enabled = if is_service { self.service_states.get(&service_key).copied().unwrap_or(false) }
            else { self.toggle_states.get(&key).copied().unwrap_or(false) };
        let ready = if is_service {
            self.service_statuses.get(&service_key).is_some_and(ServiceStatus::actionable)
                && !self.busy_controls.contains(&service_key)
        } else { !is_toggle || (self.toggle_states.contains_key(&key)
            && !self.busy_controls.contains(&key) && !self.unavailable_controls.contains(&key)) };

        // Resolve title & button text mappings (e.g. Dusky "0" -> "Updated")
        let mut display_title = item.properties.title.clone();
        let mut effective_style = item.properties.style.clone();
        if !item.properties.button_text_file.is_empty() {
            let expanded = expand_path(&item.properties.button_text_file);
            if let Ok(content) = std::fs::read_to_string(expanded) {
                let trimmed = content.trim();
                if let Some(style) = item.properties.style_map.get(trimmed).or_else(|| item.properties.style_map.get("default")) {
                    effective_style = style.clone();
                }
                if let Some(mapped) = item.properties.button_text_map.get(trimmed) {
                    display_title = mapped.clone();
                } else if let Some(def) = item.properties.button_text_map.get("default") {
                    display_title = def.clone();
                }
            }
        }

        let icon_name = if !item.properties.icon.is_empty() {
            &item.properties.icon
        } else {
            "dot"
        };

        let is_destructive = effective_style == "destructive";
        let is_suggested = effective_style == "suggested";
        // Keep the Matugen hue and saturation; lower active tile brightness 12%.
        let active_bg = mix(Color::BLACK, palette.accent, 0.88);
        let active_hover_bg = mix(Color::BLACK, palette.accent_hover, 0.88);

        // Hero card styling: Active -> solid accent; Destructive -> subtle reddish tint; Standard -> card_bg
        let (card_bg, text_fg, icon_fg, border_color) = if is_enabled {
            (
                active_bg,
                palette.accent_fg,
                palette.accent_fg,
                active_bg,
            )
        } else if is_destructive {
            (
                Color::from_rgb(
                    palette.card_bg.r + (palette.danger.r - palette.card_bg.r) * 0.02,
                    palette.card_bg.g + (palette.danger.g - palette.card_bg.g) * 0.005,
                    palette.card_bg.b + (palette.danger.b - palette.card_bg.b) * 0.005,
                ),
                palette.fg,
                palette.danger,
                Color::from_rgba(palette.danger.r, palette.danger.g, palette.danger.b, 0.08),
            )
        } else if is_suggested {
            (
                Color::from_rgb(
                    palette.card_bg.r + (palette.accent.r - palette.card_bg.r) * 0.02,
                    palette.card_bg.g + (palette.accent.g - palette.card_bg.g) * 0.02,
                    palette.card_bg.b + (palette.accent.b - palette.card_bg.b) * 0.02,
                ),
                palette.fg,
                palette.accent,
                mix(palette.card_bg, palette.accent, 0.18),
            )
        } else {
            (
                palette.card_bg,
                palette.fg,
                palette.accent,
                Color::from_rgba(palette.border.r, palette.border.g, palette.border.b, 0.30),
            )
        };

        let mut card_content = column![
            render_icon(icon_name, 22.0, icon_fg),
            text(display_title)
                .size(12)
                .font(iced::Font {
                    weight: Weight::Bold,
                    ..Default::default()
                })
                .color(text_fg),
        ]
        .spacing(5)
        .align_x(Horizontal::Center);

        if is_service {
            let detail = self.service_statuses.get(&service_key).map(|s| format!("{} • {}", if s.enabled() { "On" } else { "Off" }, s.startup))
                .unwrap_or_else(|| "Checking…".into());
            card_content = card_content.push(text(detail).size(9)
                .color(if is_enabled { palette.accent_fg } else { mix(palette.card_bg, palette.fg, 0.46) }));
        }

        let on_press_msg = if is_service {
            Message::ToggleService { unit: item.properties.service.clone(), scope: item.properties.scope.clone() }
        } else if is_toggle {
            Message::ToggleItem {
                key,
                is_enabled: !is_enabled,
                on_toggle: item.on_toggle.clone(),
            }
        } else if let Some(action) = &item.on_press {
            Message::ExecuteAction(action.clone())
        } else {
            Message::SelectPage(self.active_page_idx())
        };

        let shadow = if is_enabled {
            iced::Shadow {
                color: Color::from_rgba(
                    palette.accent.r,
                    palette.accent.g,
                    palette.accent.b,
                    0.031,
                ),
                offset: iced::Vector::new(0.0, 1.5),
                blur_radius: 6.5,
            }
        } else {
            iced::Shadow::default()
        };

        // Uniform 62px fixed height guarantees all rows have the exact same size!
        let mut card = button(
            container(card_content)
                .padding([6, 6])
                .width(Length::Fill)
                .height(Length::Fixed(62.0))
                .align_x(Horizontal::Center)
                .align_y(Vertical::Center),
        )
        .style(move |_, status| {
            let (bg, b_color) = if is_enabled {
                if status == button::Status::Hovered {
                    (active_hover_bg, active_hover_bg)
                } else {
                    (active_bg, active_bg)
                }
            } else if is_destructive {
                if status == button::Status::Hovered {
                    (
                        Color::from_rgb(
                            palette.card_hover.r + (palette.danger.r - palette.card_hover.r) * 0.04,
                            palette.card_hover.g,
                            palette.card_hover.b,
                        ),
                        Color::from_rgba(palette.danger.r, palette.danger.g, palette.danger.b, 0.18),
                    )
                } else {
                    (card_bg, border_color)
                }
            } else if is_suggested {
                if status == button::Status::Hovered {
                    (
                        Color::from_rgb(
                            palette.card_hover.r + (palette.accent.r - palette.card_hover.r) * 0.04,
                            palette.card_hover.g + (palette.accent.g - palette.card_hover.g) * 0.04,
                            palette.card_hover.b + (palette.accent.b - palette.card_hover.b) * 0.04,
                        ),
                        Color::from_rgba(palette.accent.r, palette.accent.g, palette.accent.b, 0.30),
                    )
                } else {
                    (card_bg, border_color)
                }
            } else if status == button::Status::Hovered {
                (
                    palette.card_hover,
                    Color::from_rgba(palette.accent.r, palette.accent.g, palette.accent.b, 0.35),
                )
            } else {
                (card_bg, border_color)
            };

            button::Style {
                background: Some(bg.into()),
                border: Border {
                    color: b_color,
                    width: 1.0,
                    radius: 12.0.into(),
                },
                shadow,
                ..Default::default()
            }
        })
        .width(Length::FillPortion(1))
        .height(Length::Fixed(62.0));
        if ready { card = card.on_press(on_press_msg); }
        card.into()
    }

    // ---------------------------------------------------------------------------
    // Power Management Card (Icon Badge + Interactive PickList Dropdown)
    // ---------------------------------------------------------------------------

    fn view_power_management_card<'a>(&'a self, _section: &'a SectionConfig) -> Element<'a, Message> {
        let palette = self.theme;
        let is_hovered = self.hovered_item.as_deref() == Some("Active Profile");

        let (icon_badge_bg, icon_badge_border, icon_color) = if is_hovered {
            (
                mix(palette.card_hover, palette.accent, 0.12),
                mix(palette.card_bg, palette.accent, 0.18),
                palette.accent,
            )
        } else {
            (
                mix(palette.card_bg, palette.accent, 0.08),
                Color::TRANSPARENT,
                palette.accent,
            )
        };

        // Left Icon Badge Box
        let icon_badge = container(render_icon("power-profile-balanced-symbolic", 20.0, icon_color))
            .width(36)
            .height(36)
            .align_x(Horizontal::Center)
            .align_y(Vertical::Center)
            .style(move |_| container::Style {
                background: Some(icon_badge_bg.into()),
                border: Border {
                    color: icon_badge_border,
                    width: 1.0,
                    radius: 10.0.into(),
                },
                ..Default::default()
            });

        let text_info = column![
            text("Active Profile")
                .size(13)
                .font(iced::Font {
                    weight: Weight::Normal,
                    ..iced::Font::with_name("Atkinson Hyperlegible")
                })
                .color(palette.fg),
            text("Select performance mode")
                .size(12)
                .color(mix(palette.card_bg, palette.fg, if is_hovered { 0.55 } else { 0.46 })),
        ]
        .spacing(2)
        .width(Length::Fill);

        // Interactive PickList Dropdown
        let dropdown = pick_list(
            &PROFILE_OPTIONS[..],
            Some(self.active_profile.as_str()),
            |selected| Message::ProfileSelected(selected.to_string()),
        )
        .padding([6, 12])
        .text_size(12)
        .style(move |_, status| pick_list::Style {
            text_color: palette.fg,
            placeholder_color: palette.fg_muted,
            handle_color: palette.accent,
            background: if matches!(
                status,
                pick_list::Status::Hovered | pick_list::Status::Opened { .. }
            ) {
                palette.card_hover.into()
            } else {
                palette.surface.into()
            },
            border: Border {
                color: Color::from_rgba(palette.border.r, palette.border.g, palette.border.b, 0.30),
                width: 1.0,
                radius: 8.0.into(),
            },
        })
        .menu_style(move |_| menu::Style {
            background: palette.card_bg.into(),
            border: Border {
                color: Color::from_rgba(palette.border.r, palette.border.g, palette.border.b, 0.30),
                width: 1.0,
                radius: 8.0.into(),
            },
            selected_background: palette.accent.into(),
            selected_text_color: palette.accent_fg,
            text_color: palette.fg,
            shadow: iced::Shadow::default(),
        });

        let row_content = row![icon_badge, text_info, dropdown]
            .spacing(14)
            .align_y(Vertical::Center);

        let card_container = container(row_content)
            .padding([12, 16])
            .width(Length::Fill)
            .style(move |_| container::Style {
                background: Some(
                    if is_hovered {
                        palette.card_hover
                    } else {
                        palette.card_bg
                    }
                    .into(),
                ),
                border: Border {
                    color: if is_hovered {
                        Color::from_rgba(palette.accent.r, palette.accent.g, palette.accent.b, 0.35)
                    } else {
                        Color::from_rgba(palette.border.r, palette.border.g, palette.border.b, 0.30)
                    },
                    width: 1.0,
                    radius: 14.0.into(),
                },
                ..Default::default()
            });

        mouse_area(card_container)
            .on_enter(Message::ItemEntered("Active Profile".to_string()))
            .on_exit(Message::ItemExited("Active Profile".to_string()))
            .into()
    }

    // ---------------------------------------------------------------------------
    // TOML Controls Card (Audio and Display on Home)
    // ---------------------------------------------------------------------------

    fn view_quick_controls_card<'a>(&'a self, section: &'a SectionConfig) -> Element<'a, Message> {
        let palette = self.theme;
        let mut controls_col = column![].spacing(14);

        for item in &section.items {
            let key = if !item.properties.key.is_empty() {
                item.properties.key.clone()
            } else {
                item.properties.title.clone()
            };

            if key == "Night Light" && !self.sunset_active {
                continue;
            }
            let icon_name = item.properties.icon.as_str();

            let min = item.properties.min.unwrap_or(0.0) as f32;
            let max = item.properties.max.unwrap_or(100.0) as f32;
            let step = item.properties.step.unwrap_or(1.0) as f32;
            let val = self.slider_values.get(&key).copied()
                .unwrap_or(item.properties.default.unwrap_or(min as f64) as f32);

            let key_c1 = key.clone();
            let key_c2 = key.clone();
            let on_change = item.on_change.clone();

            let is_row_hovered = self.hovered_item.as_deref() == Some(&key);
            let tint = slider_tint(palette, &key);

            let (icon_badge_bg, icon_badge_border, icon_color) = if is_row_hovered {
                (
                    mix(palette.card_hover, tint, 0.12),
                    mix(palette.card_bg, tint, 0.18),
                    tint,
                )
            } else {
                (
                    mix(palette.card_bg, tint, 0.08),
                    Color::TRANSPARENT,
                    tint,
                )
            };

            // Left Icon Badge Box
            let icon_badge = container(render_icon(icon_name, 18.0, icon_color))
                .width(36)
                .height(36)
                .align_x(Horizontal::Center)
                .align_y(Vertical::Center)
                .style(move |_| container::Style {
                    background: Some(icon_badge_bg.into()),
                    border: Border {
                        color: icon_badge_border,
                        width: 1.0,
                        radius: 10.0.into(),
                    },
                    ..Default::default()
                });

            let change_action = on_change.clone();
            // Shared Matugen rail and handle styling.
            let s = slider(min..=max, val, move |v| Message::SliderChanged {
                key: key_c1.clone(),
                value: v,
                on_change: change_action.clone(),
            })
            .step(step)
            .height(24)
            .width(Length::Fill)
            .on_release(Message::SliderReleased {
                key: key_c2,
                value: val,
                on_change,
            })
            .style(move |_, status| slider_style(palette, tint, val <= min, status));

            // Monospace numeric value on right
            let val_label = text(format!("{:.0}", val.round()))
                .size(13)
                .font(iced::Font::MONOSPACE)
                .color(mix(palette.card_bg, palette.fg, if is_row_hovered { 0.72 } else { 0.60 }))
                .width(28)
                .align_x(Horizontal::Right);

            let ctrl_row = row![
                icon_badge,
                container(s).width(Length::Fill).padding([0, 4]),
                val_label,
            ]
            .spacing(12)
            .align_y(Vertical::Center);

            let key_enter = key.clone();
            let key_exit = key.clone();
            let row_area = mouse_area(ctrl_row)
                .on_enter(Message::ItemEntered(key_enter))
                .on_exit(Message::ItemExited(key_exit));

            controls_col = controls_col.push(row_area);
        }

        container(controls_col)
            .padding([14, 16])
            .width(Length::Fill)
            .style(move |_| container::Style {
                background: Some(palette.card_bg.into()),
                border: Border {
                    color: Color::from_rgba(palette.border.r, palette.border.g, palette.border.b, 0.30),
                    width: 1.0,
                    radius: 14.0.into(),
                },
                ..Default::default()
            })
            .into()
    }

    // ---------------------------------------------------------------------------
    // Standard Item Row (Badge Container on Left, Control on Right)
    // ---------------------------------------------------------------------------

    fn view_item_row<'a>(
        &'a self,
        item: &'a ItemConfig,
        badge_tag: &'a str,
        _from_page: Option<usize>,
    ) -> Element<'a, Message> {
        self.view_item_row_rounded(item, badge_tag, _from_page, iced::border::Radius::default())
    }

    fn view_item_row_rounded<'a>(
        &'a self,
        item: &'a ItemConfig,
        badge_tag: &'a str,
        from_page: Option<usize>,
        radius: iced::border::Radius,
    ) -> Element<'a, Message> {
        let palette = self.theme;
        let title = &item.properties.title;
        let service_key = controls::service_key(&item.properties.scope, &item.properties.service);
        let desc = if item.item_type == "service" {
            let detail = if self.busy_controls.contains(&service_key) { "Applying…".into() }
                else { self.service_statuses.get(&service_key).map(ServiceStatus::description).unwrap_or_else(|| "Checking…".into()) };
            format!("{} • {detail}", item.properties.description)
        } else { item.properties.description.clone() };

        let key = if !item.properties.key.is_empty() {
            item.properties.key.clone()
        } else {
            title.clone()
        };

        let is_row_hovered = self.hovered_item.as_deref() == Some(&key);

        let icon_name = if !item.properties.icon.is_empty() {
            &item.properties.icon
        } else {
            "dot"
        };

        // Title and description colors brighten subtly on hover
        let title_color = palette.fg;
        let desc_color = if is_row_hovered {
            mix(palette.card_hover, palette.fg, 0.55)
        } else {
            mix(palette.card_bg, palette.fg, 0.46)
        };

        // Left info container with title and description
        let mut info_col = column![row![
            text(title)
                .size(13)
                .font(iced::Font {
                    weight: Weight::Normal,
                    ..iced::Font::with_name("Atkinson Hyperlegible")
                })
                .color(title_color),
            if !badge_tag.is_empty() {
                container(text(badge_tag).size(10).color(palette.accent))
                    .padding([2, 8])
                    .style(move |_| container::Style {
                        background: Some(
                            mix(palette.card_hover, palette.accent, 0.12).into(),
                        ),
                        border: Border {
                            radius: 4.0.into(),
                            ..Default::default()
                        },
                        ..Default::default()
                    })
            } else {
                container(Space::new())
            }
        ]
        .spacing(8)
        .align_y(Vertical::Center)];

        if !desc.is_empty() {
            info_col = info_col.push(
                text(desc)
                    .size(12)
                    .font(iced::Font::with_name("Atkinson Hyperlegible"))
                    .color(desc_color),
            );
        }

        // Icon badge lights up subtly with gentle glow on hover
        let (icon_badge_bg, icon_badge_border, icon_color) = if is_row_hovered {
            (
                mix(palette.card_hover, palette.accent, 0.12),
                mix(palette.card_bg, palette.accent, 0.18),
                palette.accent,
            )
        } else {
            (
                mix(palette.card_bg, palette.accent, 0.08),
                Color::TRANSPARENT,
                palette.accent,
            )
        };

        let left_part = row![
            container(render_icon(icon_name, 18.0, icon_color))
                .width(34)
                .height(34)
                .align_x(Horizontal::Center)
                .align_y(Vertical::Center)
                .style(move |_| container::Style {
                    background: Some(icon_badge_bg.into()),
                    border: Border {
                        color: icon_badge_border,
                        width: 1.0,
                        radius: 8.0.into(),
                    },
                    ..Default::default()
                }),
            info_col.width(Length::Fill),
        ]
        .spacing(12)
        .align_y(Vertical::Center)
        .width(Length::Fill);

        let mut buttons_row = row![].spacing(0).align_y(Vertical::Center);
        for (index, b) in item.properties.buttons.iter().enumerate().filter(|_| from_page.is_none()) {
            let style = b.style.clone();
            let is_suggested = style == "suggested";
            let icon_color = if is_suggested { palette.accent_fg }
                else if style == "destructive" { palette.danger } else { palette.fg };
            let content: Element<'a, Message> = if b.icon.is_empty() {
                text(&b.title).size(12).into()
            } else if b.title.is_empty() {
                render_icon(&b.icon, 16.0, icon_color)
            } else {
                row![render_icon(&b.icon, 16.0, icon_color), text(&b.title).size(12)]
                    .spacing(6).align_y(Vertical::Center).into()
            };
            let count = item.properties.buttons.len();
            let mut btn = button(container(content).center_y(34))
                .height(34).padding([0, 10])
                .style(move |_, status| action_button_style(palette, &style, true, index, count, status));
            if let Some(action) = &b.on_press { btn = btn.on_press(Message::ExecuteAction(action.clone())); }
            buttons_row = buttons_row.push(btn);
        }

        // Multi-option selections for Voice Character Preset
        // Render title/description on top and flow wrapped pills underneath indented by 46px
        if from_page.is_none() && item.item_type == "selection" && item.properties.title == "Voice Character Preset" {
            let current = self.selected_options.get(&key).cloned().unwrap_or_default();
            let mut options_row = row![].spacing(6);

            for opt in &item.properties.options {
                let is_sel = current == *opt;
                let opt_clone = opt.clone();
                let key_clone = key.clone();

                let act = match &item.on_change {
                    Some(ChangeAction::Map(map)) => map.get(opt).cloned(),
                    Some(ChangeAction::Direct(d)) => Some(d.clone()),
                    None => None,
                };

                let pill = button(text(opt).size(11).color(if is_sel {
                    palette.accent_fg
                } else {
                    palette.fg_muted
                }))
                .on_press(Message::SelectOption {
                    key: key_clone,
                    option: opt_clone,
                    action: act,
                })
                .padding([4, 10])
                .style(move |_, status| {
                    let bg = if is_sel {
                        palette.accent
                    } else if status == button::Status::Hovered {
                        palette.card_hover
                    } else {
                        palette.card_bg
                    };
                    button::Style {
                        background: Some(bg.into()),
                        border: Border {
                            radius: 6.0.into(),
                            ..Default::default()
                        },
                        ..Default::default()
                    }
                });

                options_row = options_row.push(pill);
            }

            let top_row = if !item.properties.buttons.is_empty() {
                row![left_part, buttons_row]
                    .spacing(8)
                    .align_y(Vertical::Center)
            } else {
                row![left_part]
            };

            let row_content = column![
                top_row,
                container(options_row.wrap().vertical_spacing(6))
                    .padding(Padding {
                        top: 4.0,
                        right: 0.0,
                        bottom: 4.0,
                        left: 46.0,
                    })
                    .width(Length::Fill),
            ]
            .spacing(4);

            let row_container = container(row_content)
                .padding([8, 15])
                .width(Length::Fill)
                .style(move |_| container::Style {
                    background: Some(if is_row_hovered { palette.card_hover } else { Color::TRANSPARENT }.into()),
                    border: Border { radius, ..Default::default() },
                    ..Default::default()
                });

            let key_enter = key.clone();
            let key_exit = key.clone();
            return mouse_area(row_container)
                .on_enter(Message::ItemEntered(key_enter))
                .on_exit(Message::ItemExited(key_exit))
                .into();
        }

        // Right interactive widget
        let right_widget: Element<'a, Message> = match if from_page.is_some() { "navigation" } else { item.item_type.as_str() } {
            "toggle" => {
                let is_active = self.toggle_states.get(&key).copied().unwrap_or(false);
                let toggle_pair = item.on_toggle.clone();
                let key_clone = key.clone();
                let mut control = toggler(is_active).size(24)
                    .style(move |_, status| toggle_style(palette, status));
                if self.toggle_states.contains_key(&key) && !self.busy_controls.contains(&key) && !self.unavailable_controls.contains(&key) {
                    control = control.on_toggle(move |val| Message::ToggleItem {
                        key: key_clone.clone(), is_enabled: val, on_toggle: toggle_pair.clone(),
                    });
                }
                control.into()
            }

            "slider" | "spin" => {
                let min = item.properties.min.unwrap_or(0.0) as f32;
                let max = item.properties.max.unwrap_or(100.0) as f32;
                let step = item.properties.step.unwrap_or(1.0) as f32;
                let val = self.slider_values.get(&key).copied().unwrap_or(min);
                let tint = slider_tint(palette, &key);

                let key_c1 = key.clone();
                let key_c2 = key.clone();
                let on_change = item.on_change.clone();

                let change_action = on_change.clone();
                let s = slider(min..=max, val, move |v| Message::SliderChanged {
                    key: key_c1.clone(),
                    value: v,
                    on_change: change_action.clone(),
                })
                .step(step)
                .height(24)
                .width(130)
                .on_release(Message::SliderReleased {
                    key: key_c2,
                    value: val,
                    on_change,
                })
                .style(move |_, status| slider_style(palette, tint, val <= min, status));

                let val_label = text(format!("{:.0}", val.round()))
                    .size(12)
                    .font(iced::Font::MONOSPACE)
                    .color(palette.fg_muted)
                    .width(26)
                    .align_x(Horizontal::Right);

                row![s, val_label]
                    .spacing(8)
                    .align_y(Vertical::Center)
                    .into()
            }

            "entry" | "secret" => {
                let value = self.entry_values.get(&key).map(String::as_str).unwrap_or("");
                let change_key = key.clone();
                let action = item.on_action.as_ref().or(item.on_press.as_ref());
                let submit = action.map(|action| Message::SubmitEntry { key: key.clone(), action: action.clone() });
                let ready = !self.busy_controls.contains(&key);
                let mut input = text_input("Value", value).size(12).padding([6, 8]).width(110)
                    .secure(item.item_type == "secret")
                    .style(move |_, _| text_input::Style {
                        background: palette.surface.into(),
                        border: Border { color: mix(palette.card_bg, palette.border, 0.55), width: 1.0, radius: 6.0.into() },
                        icon: palette.fg_muted, placeholder: mix(palette.surface, palette.fg, 0.40),
                        value: palette.fg, selection: palette.accent,
                    });
                if ready {
                    input = input.on_input(move |value| Message::EntryChanged { key: change_key.clone(), value });
                    if let Some(message) = &submit { input = input.on_submit(message.clone()); }
                }
                let label = if item.properties.button_text.is_empty() { "Apply" } else { &item.properties.button_text };
                let style = item.properties.style.clone();
                let mut apply = button(text(label).size(12)).padding([6, 12])
                    .style(move |_, status| action_button_style(palette, &style, false, 0, 1, status));
                if ready && let Some(message) = submit { apply = apply.on_press(message); }
                row![input, apply].spacing(6).align_y(Vertical::Center).into()
            }

            "selection" => {
                let current = self
                    .selected_options
                    .get(&key)
                    .cloned()
                    .or_else(|| item.properties.options.first().cloned())
                    .unwrap_or_default();

                let act_map = match &item.on_change {
                    Some(ChangeAction::Map(map)) => map.clone(),
                    _ => HashMap::new(),
                };
                let direct_act = match &item.on_change {
                    Some(ChangeAction::Direct(d)) => Some(d.clone()),
                    _ => None,
                };

                let key_clone = key.clone();
                let options = item.properties.options.clone();

                if options.is_empty() {
                    container(Space::new().width(0)).into()
                } else {
                    let dropdown = pick_list(
                        options,
                        Some(current),
                        move |selected| {
                            let action = act_map
                                .get(&selected)
                                .cloned()
                                .or_else(|| direct_act.clone());
                            Message::SelectOption {
                                key: key_clone.clone(),
                                option: selected,
                                action,
                            }
                        },
                    )
                    .padding([5, 10])
                    .text_size(12)
                    .style(move |_, status| pick_list::Style {
                        text_color: palette.fg,
                        placeholder_color: palette.fg_muted,
                        handle_color: palette.accent,
                        background: if matches!(
                            status,
                            pick_list::Status::Hovered | pick_list::Status::Opened { .. }
                        ) {
                            palette.card_hover.into()
                        } else {
                            palette.surface.into()
                        },
                        border: Border {
                            color: palette.border,
                            width: 1.0,
                            radius: 8.0.into(),
                        },
                    })
                    .menu_style(move |_| menu::Style {
                        background: palette.card_bg.into(),
                        border: Border {
                            color: palette.border,
                            width: 1.0,
                            radius: 8.0.into(),
                        },
                        selected_background: palette.accent.into(),
                        selected_text_color: palette.accent_fg,
                        text_color: palette.fg,
                        shadow: iced::Shadow::default(),
                    });

                    dropdown.into()
                }
            }

            "navigation" => {
                let icon_color = if is_row_hovered {
                    palette.fg
                } else {
                    Color::from_rgba(palette.fg.r, palette.fg.g, palette.fg.b, 0.45)
                };
                container(render_icon("chevron_right", 16.0, icon_color))
                    .padding([4, 6])
                    .align_y(Vertical::Center)
                    .into()
            }

            "expander" => {
                let expander_key = format!("expander:{}", key);
                let is_expanded = self.toggle_states.get(&expander_key).copied().unwrap_or(false);
                let chevron_icon = if is_expanded { "chevron_down" } else { "chevron_right" };
                let icon_color = if is_row_hovered {
                    palette.fg
                } else {
                    Color::from_rgba(palette.fg.r, palette.fg.g, palette.fg.b, 0.45)
                };
                container(render_icon(chevron_icon, 16.0, icon_color))
                    .padding([4, 6])
                    .align_y(Vertical::Center)
                    .into()
            }

            "service" => {
                let unit = item.properties.service.clone();
                let scope = if item.properties.scope == "user" { "user" } else { "system" }.to_string();
                let service_key = controls::service_key(&scope, &unit);
                let active = self.service_states.get(&service_key).copied().unwrap_or(false);
                let ready = self.service_statuses.get(&service_key).is_some_and(ServiceStatus::actionable)
                    && !self.busy_controls.contains(&service_key);
                let mut control = toggler(active).size(24).style(move |_, status| toggle_style(palette, status));
                if ready { control = control.on_toggle(move |_| Message::ToggleService { unit: unit.clone(), scope: scope.clone() }); }
                control.into()
            }

            "label" => {
                let label_val = self
                    .live_labels
                    .get(&key)
                    .cloned()
                    .unwrap_or_else(|| "…".into());

                text(label_val)
                    .size(12)
                    .color(palette.fg_muted)
                    .align_x(Horizontal::Right)
                    .into()
            }

            _ => {
                let opt_action = item.on_press.as_ref().or(item.on_action.as_ref());
                if let Some(action) = opt_action {
                    let act = action.clone();

                    let mut dyn_btn_text = String::new();
                    let mut dyn_style = String::new();

                    if !item.properties.button_text_file.is_empty() {
                        let expanded = expand_path(&item.properties.button_text_file);
                        if let Ok(content) = std::fs::read_to_string(expanded) {
                            let trimmed = content.trim();
                            if let Some(mapped) = item.properties.button_text_map.get(trimmed) {
                                dyn_btn_text = mapped.clone();
                            } else if let Some(def) = item.properties.button_text_map.get("default") {
                                dyn_btn_text = def.clone();
                            }
                            if let Some(s_mapped) = item.properties.style_map.get(trimmed).or_else(|| item.properties.style_map.get("default")) {
                                dyn_style = s_mapped.clone();
                            }
                        }
                    }

                    let btn_label = if !dyn_btn_text.is_empty() {
                        dyn_btn_text
                    } else if !item.properties.button_text.is_empty() {
                        item.properties.button_text.clone()
                    } else {
                        "Run".to_string()
                    };

                    let effective_style = if !dyn_style.is_empty() {
                        dyn_style
                    } else {
                        item.properties.style.clone()
                    };

                    button(text(btn_label).size(12).font(iced::Font {
                        weight: Weight::Semibold, ..iced::Font::with_name("Atkinson Hyperlegible")
                    }))
                    .on_press(Message::ExecuteAction(act))
                    .padding([6, 14])
                    .style(move |_, status| action_button_style(palette, &effective_style, false, 0, 1, status))
                    .into()
                } else {
                    container(Space::new().width(0)).into()
                }
            }
        };

        let right_container = if !item.properties.buttons.is_empty() {
            row![buttons_row, right_widget]
                .spacing(8)
                .align_y(Vertical::Center)
                .into()
        } else {
            right_widget
        };

        let row_content = row![left_part, right_container]
            .spacing(12)
            .align_y(Vertical::Center);

        // Paired buttons own their actions; the row only invokes an explicit row action.
        let primary_action = item.on_press.as_ref().or(item.on_action.as_ref()).cloned();

        let row_container = container(row_content)
            .padding([8, 15])
            .width(Length::Fill)
            .style(move |_| container::Style {
                background: Some(
                    if is_row_hovered {
                        palette.card_hover
                    } else {
                        Color::TRANSPARENT
                    }
                    .into(),
                ),
                border: Border {
                    radius,
                    ..Default::default()
                },
                ..Default::default()
            });

        let key_enter = key.clone();
        let key_exit = key.clone();
        let mut area = mouse_area(row_container)
            .on_enter(Message::ItemEntered(key_enter))
            .on_exit(Message::ItemExited(key_exit));

        if let Some(page) = from_page {
            area = area.on_press(Message::SelectPage(page)).interaction(mouse::Interaction::Pointer);
        } else if item.item_type == "navigation" {
            let sub_sections = item.layout.clone();
            let sub_title = title.clone();
            area = area
                .on_press(Message::PushSubPage {
                    title: sub_title,
                    sections: sub_sections,
                })
                .interaction(mouse::Interaction::Pointer);
        } else if item.item_type == "expander" {
            let expander_key = format!("expander:{}", key);
            let is_expanded = self.toggle_states.get(&expander_key).copied().unwrap_or(false);
            area = area
                .on_press(Message::ToggleItem {
                    key: expander_key,
                    is_enabled: !is_expanded,
                    on_toggle: None,
                })
                .interaction(mouse::Interaction::Pointer);
        } else if item.item_type == "toggle" && self.toggle_states.contains_key(&key) && !self.busy_controls.contains(&key)
            && !self.unavailable_controls.contains(&key) {
            let is_active = self.toggle_states.get(&key).copied().unwrap_or(false);
            let toggle_pair = item.on_toggle.clone();
            let key_clone = key.clone();
            area = area
                .on_press(Message::ToggleItem {
                    key: key_clone,
                    is_enabled: !is_active,
                    on_toggle: toggle_pair,
                })
                .interaction(mouse::Interaction::Pointer);
        } else if item.item_type == "service" {
            let key = controls::service_key(&item.properties.scope, &item.properties.service);
            if self.service_statuses.get(&key).is_some_and(ServiceStatus::actionable) && !self.busy_controls.contains(&key) {
                area = area.on_press(Message::ToggleService {
                    unit: item.properties.service.clone(), scope: item.properties.scope.clone(),
                }).interaction(mouse::Interaction::Pointer);
            }
        } else if (item.item_type == "button" || item.item_type.is_empty())
            && let Some(act) = primary_action
        {
            area = area
                .on_press(Message::ExecuteAction(act))
                .interaction(mouse::Interaction::Pointer);
        }

        let expander_key = format!("expander:{}", key);
        let is_expanded = self.toggle_states.get(&expander_key).copied().unwrap_or(false);

        if from_page.is_none() && item.item_type == "expander" && is_expanded && !item.items.is_empty() {
            let mut sub_col = column![].spacing(0);
            for sub_item in &item.items {
                let divider = container(Space::new().width(Length::Fill).height(1))
                    .style(move |_| container::Style {
                        background: Some(
                            Color::from_rgba(
                                palette.border.r,
                                palette.border.g,
                                palette.border.b,
                                0.15,
                            )
                            .into(),
                        ),
                        ..Default::default()
                    });
                sub_col = sub_col.push(divider);
                sub_col = sub_col.push(
                    container(self.view_item_row(sub_item, "", None)).padding(Padding {
                        top: 0.0,
                        right: 0.0,
                        bottom: 0.0,
                        left: 16.0,
                    }),
                );
            }
            column![area, sub_col].into()
        } else {
            area.into()
        }
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn row_radius(index: usize, count: usize) -> iced::border::Radius {
    let top = if index == 0 { 13.0 } else { 0.0 };
    let bottom = if index + 1 == count { 13.0 } else { 0.0 };
    iced::border::Radius {
        top_left: top,
        top_right: top,
        bottom_left: bottom,
        bottom_right: bottom,
    }
}

fn action_button_style(
    palette: AppTheme, kind: &str, joined: bool, index: usize, count: usize, status: button::Status,
) -> button::Style {
    let hovered = matches!(status, button::Status::Hovered | button::Status::Pressed);
    let (bg, fg, border) = match kind {
        "destructive" => (
            soften_red(if hovered { palette.danger_bg } else { mix(palette.card_bg, palette.danger_bg, 0.25) }),
            if hovered { palette.danger_fg } else { palette.danger },
            mix(palette.card_bg, palette.danger, 0.25),
        ),
        "suggested" => (
            if joined {
                // A quiet darkening keeps bright joined actions distinct on hover.
                mix(palette.accent, palette.card_bg, if status == button::Status::Pressed { 0.14 } else if hovered { 0.08 } else { 0.0 })
            } else if hovered { palette.accent } else { mix(palette.card_bg, palette.accent, 0.08) },
            if hovered || joined { palette.accent_fg } else { palette.accent },
            mix(palette.card_bg, palette.accent, 0.25),
        ),
        _ => (
            mix(palette.card_bg, palette.fg, if hovered { 0.10 } else { 0.05 }),
            palette.fg, mix(palette.card_bg, palette.border, 0.55),
        ),
    };
    button::Style {
        background: Some(bg.into()), text_color: fg,
        border: Border {
            color: border, width: if joined { 0.0 } else { 1.0 },
            radius: iced::border::Radius {
                top_left: if index == 0 { 10.0 } else { 0.0 },
                bottom_left: if index == 0 { 10.0 } else { 0.0 },
                top_right: if index + 1 == count { 10.0 } else { 0.0 },
                bottom_right: if index + 1 == count { 10.0 } else { 0.0 },
            },
        },
        ..Default::default()
    }
}

/// Reduce HSV saturation by 20% while retaining the original brightness and hue.
fn soften_red(color: Color) -> Color {
    let peak = color.r.max(color.g).max(color.b);
    mix(Color::from_rgb(peak, peak, peak), color, 0.8)
}

fn slider_tint(palette: AppTheme, key: &str) -> Color {
    match key {
        "Brightness" => palette.secondary,
        "Night Light" => palette.tertiary,
        _ => palette.accent,
    }
}

fn slider_style(palette: AppTheme, tint: Color, at_minimum: bool, status: slider::Status) -> slider::Style {
    let active = matches!(status, slider::Status::Hovered | slider::Status::Dragged);
    let empty = mix(palette.card_bg, palette.fg, 0.09);
    slider::Style {
        rail: slider::Rail {
            backgrounds: (if at_minimum { empty } else { mix(palette.card_bg, tint, 0.62) }.into(), empty.into()),
            width: 10.0,
            border: Border { radius: 5.0.into(), ..Default::default() },
        },
        handle: slider::Handle {
            shape: slider::HandleShape::Circle { radius: 7.0 },
            background: mix(tint, palette.fg, if active { 0.95 } else { 0.80 }).into(),
            border_width: 0.0,
            border_color: Color::TRANSPARENT,
        },
    }
}

fn toggle_style(palette: AppTheme, status: toggler::Status) -> toggler::Style {
    let (enabled, hovered) = match status {
        toggler::Status::Active { is_toggled } | toggler::Status::Disabled { is_toggled } =>
            (is_toggled, false),
        toggler::Status::Hovered { is_toggled } => (is_toggled, true),
    };
    toggler::Style {
        background: if enabled {
            if hovered { palette.accent_hover } else { palette.accent }
        } else {
            palette.card_hover
        }.into(),
        background_border_width: if enabled { 0.0 } else { 1.0 },
        background_border_color: if hovered { palette.accent } else { palette.border },
        foreground: if enabled { palette.fg } else { mix(palette.card_bg, palette.fg, 0.58) }.into(),
        foreground_border_width: 0.0,
        foreground_border_color: Color::TRANSPARENT,
        text_color: None,
        border_radius: None,
        padding_ratio: 0.1,
    }
}

fn collect_item_defaults(
    item: &ItemConfig,
    sliders: &mut HashMap<String, f32>,
    toggles: &mut HashMap<String, bool>,
    services: &mut HashMap<String, bool>,
    options: &mut HashMap<String, String>,
) {
    let key = if !item.properties.key.is_empty() {
        item.properties.key.clone()
    } else {
        item.properties.title.clone()
    };

    if item.item_type == "slider" || item.item_type == "spin" {
        let def = item
            .properties
            .default
            .unwrap_or(item.properties.min.unwrap_or(0.0)) as f32;
        sliders.entry(key.clone()).or_insert(def);
    }

    if matches!(item.item_type.as_str(), "toggle" | "toggle_card") && item.properties.state_command.is_empty() {
        let value = std::fs::read_to_string(controls::settings_dir().join(&key)).ok()
            .and_then(|raw| controls::parse_bool(&raw)).unwrap_or(false) ^ item.properties.key_inverse;
        toggles.entry(key.clone()).or_insert(value);
    }

    if !item.properties.service.is_empty() {
        services
            .entry(controls::service_key(&item.properties.scope, &item.properties.service))
            .or_insert(false);
    }

    if item.item_type == "selection" && !item.properties.options.is_empty() {
        let mut initial = None;

        // 1. Check if state file exists in settings
        let settings_file = dirs_fallback().join("settings").join(&key);
        if settings_file.is_file()
            && let Ok(content) = std::fs::read_to_string(&settings_file)
        {
            let trimmed = content.trim();
            let lower = trimmed.to_lowercase();
            if let Some(mapped) = item
                .properties
                .options_map
                .get(trimmed)
                .or_else(|| item.properties.options_map.get(&lower))
            {
                initial = Some(mapped.clone());
            } else if let Some(found) = item
                .properties
                .options
                .iter()
                .find(|o| o.eq_ignore_ascii_case(trimmed))
            {
                initial = Some(found.clone());
            }
        }

        let val = initial.unwrap_or_else(|| item.properties.options[0].clone());
        options.entry(key.clone()).or_insert(val);
    }

    for sub in &item.items {
        collect_item_defaults(sub, sliders, toggles, services, options);
    }
}

impl ItemConfig {
    pub fn matches_query(&self, query: &str) -> bool {
        self.properties.title.to_lowercase().contains(query)
            || self.properties.description.to_lowercase().contains(query)
            || self.properties.service.to_lowercase().contains(query)
            || self.properties.key.to_lowercase().contains(query)
            || self
                .properties
                .options
                .iter()
                .any(|o| o.to_lowercase().contains(query))
    }
}

fn expand_path(p: &str) -> std::path::PathBuf {
    if let Some(stripped) = p.strip_prefix("~/") {
        dirs_home().join(stripped)
    } else if let Some(stripped) = p.strip_prefix("$HOME/") {
        dirs_home().join(stripped)
    } else {
        std::path::PathBuf::from(p)
    }
}

fn dirs_home() -> std::path::PathBuf {
    if let Ok(home) = std::env::var("HOME") {
        std::path::PathBuf::from(home)
    } else {
        std::path::PathBuf::new()
    }
}

fn dirs_fallback() -> std::path::PathBuf {
    if let Ok(config_home) = std::env::var("XDG_CONFIG_HOME")
        && !config_home.is_empty()
    {
        return std::path::PathBuf::from(config_home).join("dusky");
    }
    dirs_home().join(".config/dusky")
}

fn make_slim_scrollable<'a>(
    content: impl Into<Element<'a, Message>>,
    padding: Padding,
    accent: Color,
) -> iced::widget::Scrollable<'a, Message> {
    scrollable(container(content).padding(padding))
        .direction(scrollable::Direction::Vertical(
            scrollable::Scrollbar::new()
                .width(4)
                .scroller_width(4)
                .margin(2),
        ))
        .style(move |theme, status| {
            let is_hovered = matches!(
                status,
                scrollable::Status::Hovered {
                    is_vertical_scrollbar_hovered: true,
                    ..
                } | scrollable::Status::Dragged {
                    is_vertical_scrollbar_dragged: true,
                    ..
                }
            );
            let mut s = scrollable::default(theme, status);
            s.vertical_rail.background = if is_hovered {
                Some(Color::from_rgba(accent.r, accent.g, accent.b, 0.04).into())
            } else {
                None
            };
            s.vertical_rail.scroller.background = if is_hovered {
                accent.into()
            } else {
                Color::TRANSPARENT.into()
            };
            s.horizontal_rail.background = None;
            s.horizontal_rail.scroller.background = Color::TRANSPARENT.into();
            s
        })
        .height(Length::Fill)
}

pub fn expand_app_config_generators(config: &mut AppConfig) {
    for page in &mut config.pages {
        for section in &mut page.layout {
            expand_section_generators(section);
        }
    }
}

fn expand_section_generators(section: &mut SectionConfig) {
    section.items = expand_generators(std::mem::take(&mut section.items));
    for item in &mut section.items {
        expand_item_sub_generators(item);
    }
}

fn expand_item_sub_generators(item: &mut ItemConfig) {
    for sub_sec in &mut item.layout {
        expand_section_generators(sub_sec);
    }
    item.items = expand_generators(std::mem::take(&mut item.items));
    for sub in &mut item.items {
        expand_item_sub_generators(sub);
    }
}

fn expand_generators(items: Vec<ItemConfig>) -> Vec<ItemConfig> {
    let mut out = Vec::with_capacity(items.len());
    for item in items {
        if item.item_type == "file_generator" {
            out.extend(expand_file_generator(&item));
        } else if item.item_type == "directory_generator" {
            out.extend(expand_directory_generator(&item));
        } else {
            out.push(item);
        }
    }
    out
}

fn expand_file_generator(generator: &ItemConfig) -> Vec<ItemConfig> {
    let Some(template) = &generator.item_template else {
        return Vec::new();
    };

    let path_str = &generator.properties.path;
    if path_str.is_empty() {
        return Vec::new();
    }

    let base_path = expand_path(path_str);
    if !base_path.exists() {
        return Vec::new();
    }

    let glob_ext = if generator.properties.glob.starts_with("*.") {
        &generator.properties.glob[2..]
    } else {
        "conf"
    };

    let mut found_files: Vec<(PathBuf, String)> = Vec::new();

    if let Ok(entries) = std::fs::read_dir(&base_path) {
        let mut subdirs = Vec::new();
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_file() || path.is_symlink() {
                if let Some(ext) = path.extension()
                    && ext == glob_ext
                {
                    found_files.push((path, String::new()));
                }
            } else if generator.properties.recursive && path.is_dir() && !path.is_symlink() {
                subdirs.push(path);
            }
        }

        if generator.properties.recursive {
            subdirs.sort_by_key(|p| {
                p.file_name()
                    .unwrap_or_default()
                    .to_string_lossy()
                    .to_lowercase()
            });
            for subdir in subdirs {
                let subdir_name = subdir
                    .file_name()
                    .unwrap_or_default()
                    .to_string_lossy()
                    .to_string();
                if let Ok(sub_entries) = std::fs::read_dir(&subdir) {
                    for sub_entry in sub_entries.flatten() {
                        let path = sub_entry.path();
                        if (path.is_file() || path.is_symlink())
                            && path.extension().is_some_and(|ext| ext == glob_ext)
                        {
                            found_files.push((path, subdir_name.clone()));
                        }
                    }
                }
            }
        }
    }

    if found_files.is_empty() {
        return Vec::new();
    }

    found_files.sort_by(|a, b| {
        let a_is_sub = !a.1.is_empty();
        let b_is_sub = !b.1.is_empty();
        a_is_sub
            .cmp(&b_is_sub)
            .then_with(|| a.1.to_lowercase().cmp(&b.1.to_lowercase()))
            .then_with(|| {
                a.0.file_name()
                    .unwrap_or_default()
                    .to_string_lossy()
                    .to_lowercase()
                    .cmp(
                        &b.0.file_name()
                            .unwrap_or_default()
                            .to_string_lossy()
                            .to_lowercase(),
                    )
            })
    });

    let mut result = Vec::new();
    for (file_path, subdir) in found_files {
        let stem = file_path
            .file_stem()
            .unwrap_or_default()
            .to_string_lossy()
            .to_string();
        let filename = file_path
            .file_name()
            .unwrap_or_default()
            .to_string_lossy()
            .to_string();
        let path_str = file_path.to_string_lossy().to_string();
        let name_pretty = to_pretty_title(&stem);
        let relpath = if !subdir.is_empty() {
            format!("{}/{}", subdir, filename)
        } else {
            filename.clone()
        };

        let mut vars = HashMap::new();
        vars.insert("name".to_string(), stem);
        vars.insert("filename".to_string(), filename);
        vars.insert("path".to_string(), path_str);
        vars.insert("name_pretty".to_string(), name_pretty);
        vars.insert("relpath".to_string(), relpath);
        vars.insert("subdir".to_string(), subdir);

        let mut item = (**template).clone();
        substitute_item_vars(&mut item, &vars);
        result.push(item);
    }

    result
}

fn expand_directory_generator(generator: &ItemConfig) -> Vec<ItemConfig> {
    let Some(template) = &generator.item_template else {
        return Vec::new();
    };

    let path_str = &generator.properties.path;
    if path_str.is_empty() {
        return Vec::new();
    }

    let base_path = expand_path(path_str);
    if !base_path.is_dir() {
        return Vec::new();
    }

    let mut dirs: Vec<PathBuf> = Vec::new();
    if let Ok(entries) = std::fs::read_dir(&base_path) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() && !path.is_symlink() {
                dirs.push(path);
            }
        }
    }

    if dirs.is_empty() {
        return Vec::new();
    }

    dirs.sort_by_key(|p| {
        p.file_name()
            .unwrap_or_default()
            .to_string_lossy()
            .to_lowercase()
    });

    let mut result = Vec::new();
    for dir in dirs {
        let dirname = dir
            .file_name()
            .unwrap_or_default()
            .to_string_lossy()
            .to_string();
        let path_str = dir.to_string_lossy().to_string();
        let name_pretty = to_pretty_title(&dirname);

        let mut vars = HashMap::new();
        vars.insert("name".to_string(), dirname);
        vars.insert("path".to_string(), path_str);
        vars.insert("name_pretty".to_string(), name_pretty);

        let mut item = (**template).clone();
        substitute_item_vars(&mut item, &vars);
        result.push(item);
    }

    result
}

fn to_pretty_title(s: &str) -> String {
    let replaced = s.replace(['_', '-'], " ");
    replaced
        .split_whitespace()
        .map(|word| {
            let mut chars = word.chars();
            match chars.next() {
                None => String::new(),
                Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

fn substitute_item_vars(item: &mut ItemConfig, vars: &HashMap<String, String>) {
    let sub = |s: &str| -> String {
        let mut out = s.to_string();
        for (k, v) in vars {
            let pattern = format!("{{{}}}", k);
            out = out.replace(&pattern, v);
        }
        out
    };

    item.properties.title = sub(&item.properties.title);
    item.properties.description = sub(&item.properties.description);
    item.properties.state_command = sub(&item.properties.state_command);
    item.properties.value_command = sub(&item.properties.value_command);
    item.properties.key = sub(&item.properties.key);

    if let Some(on_press) = &mut item.on_press {
        substitute_action_vars(on_press, vars);
    }
    if let Some(on_action) = &mut item.on_action {
        substitute_action_vars(on_action, vars);
    }
    if let Some(on_toggle) = &mut item.on_toggle {
        substitute_action_vars(&mut on_toggle.enabled, vars);
        substitute_action_vars(&mut on_toggle.disabled, vars);
    }

    for sub_item in &mut item.items {
        substitute_item_vars(sub_item, vars);
    }
}

fn substitute_action_vars(action: &mut ActionConfig, vars: &HashMap<String, String>) {
    let sub = |s: &str| -> String {
        let mut out = s.to_string();
        for (k, v) in vars {
            let pattern = format!("{{{}}}", k);
            out = out.replace(&pattern, v);
        }
        out
    };

    match action {
        ActionConfig::Exec { command, argv, .. } => {
            *command = sub(command);
            for arg in argv {
                *arg = sub(arg);
            }
        }
        ActionConfig::Argv { argv, .. } => {
            for arg in argv {
                *arg = sub(arg);
            }
        }
        ActionConfig::Redirect { page } => {
            *page = sub(page);
        }
        ActionConfig::Reload => (),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn control_state_keeps_pending_values_and_rejects_stale_results() {
        // No hardware queries or action launches: exercise the actual update loop.
        let mut app = CenterApp {
            config: AppConfig { pages: vec![] },
            theme: AppTheme::default(),
            nav_stack: vec![],
            sidebar_open: true,
            search_open: false,
            search_query: String::new(),
            loaded_pages: HashSet::new(),
            toggle_states: HashMap::new(),
            slider_values: HashMap::from([("Microphone".into(), 80.0)]),
            selected_options: HashMap::new(),
            service_states: HashMap::new(),
            live_labels: HashMap::new(),
            cpu_text: String::new(),
            ram_text: String::new(),
            active_profile: String::new(),
            hovered_item: None,
            active_slider: Some("Microphone".into()),
            sunset_active: false,
            core_loading: false,
            sunset_applying: false,
            sunset_pending: None,
            closing: false,
            service_statuses: HashMap::new(),
            busy_controls: HashSet::new(),
            unavailable_controls: HashSet::new(),
            control_generation: 0,
            controls_loading: None,
            action_error: None,
            entry_values: HashMap::new(),
            editing_entries: HashSet::new(),
        };
        app.sync_slider("Microphone", 50.0);
        assert_eq!(app.slider_values["Microphone"], 80.0);
        app.sync_slider("Volume", 60.0);
        assert_eq!(app.slider_values["Volume"], 60.0);

        let _ = app.update(Message::SliderSettled {
            key: "Microphone".into(), value: 70.0, on_change: None,
        });
        assert_eq!(app.active_slider.as_deref(), Some("Microphone"));
        let _ = app.update(Message::SliderReleased {
            key: "Microphone".into(), value: 70.0, on_change: None,
        });
        assert_eq!(app.slider_values["Microphone"], 80.0);
        assert_eq!(app.active_slider, None);

        app.active_slider = Some("Microphone".into());
        let _ = app.update(Message::SliderSettled {
            key: "Microphone".into(), value: 80.0, on_change: None,
        });
        assert_eq!(app.active_slider, None);
        app.sync_slider("Microphone", 75.0);
        assert_eq!(app.slider_values["Microphone"], 75.0);
        app.toggle_states.insert("Fixture".into(), true);
        app.busy_controls.insert("Fixture".into());
        app.control_generation = 4;
        app.controls_loading = Some(3);
        let _ = app.update(Message::ToggleFinished {
            key: "Fixture".into(), previous: false, result: Err("fixture failure".into()),
        });
        assert!(!app.toggle_states["Fixture"]);
        assert!(!app.busy_controls.contains("Fixture"));
        assert!(app.action_error.as_ref().unwrap().contains("fixture failure"));
        let _ = app.update(Message::ControlsLoaded {
            generation: 3, snapshot: Snapshot { toggles: vec![("Fixture".into(), Some(true))], ..Default::default() },
        });
        assert!(!app.toggle_states["Fixture"], "stale poll must not overwrite rollback");
        app.service_states.insert("user:fixture.service".into(), true);
        let _ = app.update(Message::ControlsLoaded {
            generation: app.control_generation,
            snapshot: Snapshot {
                services: HashMap::from([("user:fixture.service".into(), ServiceStatus {
                    load: "unavailable".into(), active: "unknown".into(), startup: "unknown".into(),
                })]),
                ..Default::default()
            },
        });
        assert!(app.service_states["user:fixture.service"], "unknown is not proof of off");
    }

    #[test]
    fn hardware_refresh_is_deferred_and_cannot_overwrite_a_released_edit() {
        let (mut app, _task) = CenterApp::new(AppConfig { pages: vec![] }, None, None);
        assert!(app.core_loading);
        assert!(app.slider_values.is_empty(), "hardware queries must be deferred to the task");
        assert_eq!(app.refresh_core().units(), 0, "only one hardware poll may run");
        app.slider_values.insert("Volume".into(), 80.0);
        let _ = app.update(Message::CoreLoaded {
            values: vec![("Volume".into(), Some(50.0), Some(40.0)),
                         ("Microphone".into(), None, Some(60.0))],
            sunset_active: true,
        });
        assert_eq!(app.slider_values["Volume"], 80.0);
        assert_eq!(app.slider_values["Microphone"], 60.0);
        assert!(app.sunset_active);
        assert!(!app.core_loading);
    }

    #[test]
    fn night_light_writes_coalesce_while_the_ui_remains_responsive() {
        let (mut app, _task) = CenterApp::new(AppConfig { pages: vec![] }, None, None);
        let _ = app.update(Message::SliderChanged { key: "Night Light".into(), value: 20.0, on_change: None });
        assert!(app.sunset_applying);
        let _ = app.update(Message::SliderChanged { key: "Night Light".into(), value: 30.0, on_change: None });
        let _ = app.update(Message::SliderChanged { key: "Night Light".into(), value: 40.0, on_change: None });
        assert_eq!(app.sunset_pending, Some(40.0));
        let _ = app.update(Message::CoreLoaded {
            values: vec![("Night Light".into(), Some(40.0), Some(10.0))], sunset_active: true,
        });
        assert_eq!(app.slider_values["Night Light"], 40.0);
        let _ = app.update(Message::CloseApp);
        assert!(app.closing);
        let _ = app.update(Message::SliderReleased { key: "Night Light".into(), value: 40.0, on_change: None });
        assert_eq!(app.sunset_pending, Some(40.0), "release during close must not enqueue another write");
        let _ = app.update(Message::SunsetFinished);
        assert!(app.sunset_applying);
        assert_eq!(app.sunset_pending, None);
        let exit = app.update(Message::SunsetFinished);
        assert!(!app.sunset_applying);
        assert!(exit.units() > 0);
    }

    #[test]
    fn worker_tasks_and_reload_run_with_isolated_command_fixtures() {
        use iced_futures::futures::{executor::block_on, StreamExt};
        use std::os::unix::fs::PermissionsExt;
        if let Some(root) = std::env::var_os("DUSKY_WORKER_FIXTURE") {
            let root = std::path::PathBuf::from(root);
            let (mut app, initial) = CenterApp::new(AppConfig { pages: vec![] }, None, None);
            let actions = block_on(iced_runtime::task::into_stream(initial).unwrap().collect::<Vec<_>>());
            for action in actions {
                if let iced_runtime::Action::Output(message) = action { let _ = app.update(message); }
            }
            assert_eq!(app.slider_values["Volume"], 50.0);
            assert_eq!(app.slider_values["Microphone"], 50.0);
            assert!(app.sunset_active);
            let first = app.update(Message::SliderChanged { key: "Night Light".into(), value: 20.0, on_change: None });
            let _ = app.update(Message::SliderChanged { key: "Night Light".into(), value: 40.0, on_change: None });
            let _ = app.update(Message::CloseApp);
            let actions = block_on(iced_runtime::task::into_stream(first).unwrap().collect::<Vec<_>>());
            let mut pending = Vec::new();
            for action in actions {
                if let iced_runtime::Action::Output(message) = action
                    && let Some(stream) = iced_runtime::task::into_stream(app.update(message))
                { pending.extend(block_on(stream.collect::<Vec<_>>())); }
            }
            for action in pending {
                if let iced_runtime::Action::Output(message) = action { let _ = app.update(message); }
            }
            assert!(!app.sunset_applying);
            assert_eq!(std::fs::read_to_string(root.join("writes")).unwrap(), "5400\n4300\n");
            assert_eq!(std::fs::read_to_string(sys::sunset_state_file()).unwrap(), "4300\n");
            let colors = root.join("matugen/generated");
            std::fs::create_dir_all(&colors).unwrap();
            let palette = colors.join("dusky_center.json");
            std::fs::write(&palette, r##"{"bg":"#123456","fg":"#eeeeee","accent":"#aabbcc","secondary":"#778899","tertiary":"#8899aa"}"##).unwrap();
            let _ = app.update(Message::Tick);
            let valid_theme = app.theme;
            assert_eq!(valid_theme.bg, Color::from_rgb8(0x12, 0x34, 0x56));
            std::fs::write(&palette, "{").unwrap();
            let _ = app.update(Message::Tick);
            assert_eq!(app.theme, valid_theme);
            std::fs::remove_file(&palette).unwrap();
            let _ = app.update(Message::Tick);
            assert_eq!(app.theme, valid_theme);
            std::fs::write(root.join("dusky_config.toml"), "pages = [").unwrap();
            let _ = app.update(Message::ReloadConfig);
            assert!(app.action_error.as_ref().unwrap().starts_with("Reload:"));
            assert!(app.config.pages.is_empty());
            assert_eq!(app.theme, valid_theme);
            std::fs::write(root.join("dusky_config.toml"), "[[pages]]\nid='fixture'\ntitle='Fixture'\n").unwrap();
            let _ = app.update(Message::ExecuteAction(ActionConfig::Reload));
            assert_eq!(app.config.pages[0].id, "fixture");
            assert!(app.action_error.is_none());
            return;
        }
        let root = std::env::temp_dir().join(format!("dusky-worker-fixture-{}", std::process::id()));
        let bin = root.join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        for (name, body) in [
            ("wpctl", "printf 'Volume: 0.50\\n'"),
            ("systemctl", "printf 'active\\n'"),
            ("hyprsunset", "exit 0"),
            ("hyprctl", "if [ \"$2\" = identity ]; then printf 'false\\n'; elif [ $# -eq 2 ]; then printf '4500\\n'; else printf '%s\\n' \"$3\" >> \"$DUSKY_WORKER_FIXTURE/writes\"; fi"),
        ] {
            let path = bin.join(name);
            std::fs::write(&path, format!("#!/usr/bin/bash\n{body}\n")).unwrap();
            std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o755)).unwrap();
        }
        let result = std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "ui::tests::worker_tasks_and_reload_run_with_isolated_command_fixtures", "--nocapture"])
            .current_dir(&root).env("DUSKY_WORKER_FIXTURE", &root)
            .env("PATH", &bin).env("HOME", &root).env("XDG_CONFIG_HOME", &root)
            .env("XDG_RUNTIME_DIR", &root).env("HYPRLAND_INSTANCE_SIGNATURE", "fixture")
            .output().unwrap();
        std::fs::remove_dir_all(root).unwrap();
        assert!(result.status.success(), "{}{}", String::from_utf8_lossy(&result.stdout), String::from_utf8_lossy(&result.stderr));
    }

    #[test]
    fn test_to_pretty_title() {
        assert_eq!(to_pretty_title("wg0"), "Wg0");
        assert_eq!(to_pretty_title("mullvad_us-nyc"), "Mullvad Us Nyc");
        assert_eq!(to_pretty_title("office-vpn_backup"), "Office Vpn Backup");
    }

    #[test]
    fn test_generator_template_substitution() {
        let temp_dir = std::env::temp_dir().join(format!("test_gen_{}", std::process::id()));
        let _ = std::fs::create_dir_all(&temp_dir);
        let file_path = temp_dir.join("test-wg.conf");
        let _ = std::fs::write(&file_path, "[Interface]\n");
        std::fs::write(temp_dir.join("z-second.conf"), "[Interface]\n").unwrap();

        let item = ItemConfig {
            item_type: "file_generator".to_string(),
            properties: crate::config::ItemProperties {
                path: temp_dir.to_string_lossy().to_string(),
                glob: "*.conf".to_string(),
                ..Default::default()
            },
            on_press: None,
            on_toggle: None,
            on_change: None,
            on_action: None,
            value: None,
            items: Vec::new(),
            layout: Vec::new(),
            item_template: Some(Box::new(ItemConfig {
                item_type: "expander".to_string(),
                properties: crate::config::ItemProperties {
                    title: "{name_pretty}".to_string(),
                    description: "{filename}".to_string(),
                    ..Default::default()
                },
                on_press: None,
                on_toggle: None,
                on_change: None,
                on_action: None,
                value: None,
                items: vec![ItemConfig {
                    item_type: "toggle".to_string(),
                    properties: crate::config::ItemProperties {
                        title: "Connect {name}".to_string(),
                        key: "wireguard/{name}".to_string(),
                        state_command: "wg show {name}".to_string(),
                        ..Default::default()
                    },
                    on_press: None,
                    on_toggle: Some(ToggleActionPair {
                        enabled: ActionConfig::Exec {
                            command: "wg-quick up {path}".to_string(),
                            argv: Vec::new(),
                            terminal: false,
                            requires_root: true,
                            mode: String::new(),
                            timeout: None,
                        },
                        disabled: ActionConfig::Exec {
                            command: "wg-quick down {path}".to_string(),
                            argv: Vec::new(),
                            terminal: false,
                            requires_root: true,
                            mode: String::new(),
                            timeout: None,
                        },
                    }),
                    on_change: None,
                    on_action: None,
                    value: None,
                    items: Vec::new(),
                    layout: Vec::new(),
                    item_template: None,
                }],
                layout: Vec::new(),
                item_template: None,
            })),
        };

        let generated = expand_file_generator(&item);
        let _ = std::fs::remove_dir_all(&temp_dir);

        assert_eq!(generated.len(), 2);
        assert_eq!(generated[0].items[0].properties.key, "wireguard/test-wg");
        assert_eq!(generated[1].items[0].properties.key, "wireguard/z-second");
        let first = &generated[0];
        assert_eq!(first.properties.title, "Test Wg");
        assert_eq!(first.properties.description, "test-wg.conf");
        assert_eq!(first.items.len(), 1);
        assert_eq!(first.items[0].properties.title, "Connect test-wg");
        assert_eq!(first.items[0].properties.state_command, "wg show test-wg");
        if let Some(toggle) = &first.items[0].on_toggle {
            if let ActionConfig::Exec { command, .. } = &toggle.enabled {
                assert!(command.contains("test-wg.conf"));
            } else {
                panic!("Expected Exec action");
            }
        }
    }

    #[test]
    fn test_expand_generators_suppresses_empty_wireguard() {
        if let Ok(mut cfg) = AppConfig::load() {
            expand_app_config_generators(&mut cfg);
            for page in &cfg.pages {
                for section in &page.layout {
                    if section.properties.title == "VPN Tunnels" {
                        // In unprivileged test environment /etc/wireguard is inaccessible,
                        // so generator expands to 0 items and section must be empty.
                        if std::fs::read_dir("/etc/wireguard").is_err() {
                            assert!(section.items.is_empty(), "VPN Tunnels section should be empty when /etc/wireguard cannot be read");
                        }
                    }
                }
            }
        }
    }
}
