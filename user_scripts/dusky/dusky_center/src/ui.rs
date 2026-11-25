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
    Space, button, column, container, grid, mouse_area, pick_list, responsive, row,
    scrollable, slider, stack, text, text_input, toggler,
};
use iced::{Border, Color, Event, Length, Padding, Subscription, Task, mouse};

use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::time::{Duration, Instant};

use crate::appearance::{self, FloatTransition, NavDirection, NavTransition};
use crate::backend::controls::{self, ServiceStatus, Snapshot};
use crate::backend::system as sys;
use crate::backend::worker;
use crate::config::{
    ActionConfig, AppConfig, ButtonProperty, ChangeAction, ItemConfig, ItemProperties,
    LoadedConfig, PageConfig, SectionConfig, ToggleActionPair, ValueConfig,
};
use crate::icons::render_icon;
use crate::theme::{AppTheme, ThemeStamp, mix, saturate};

pub type Element<'a, Message> = iced_core::Element<'a, Message, iced_core::Theme, crate::renderer::Renderer>;
type DismissChannel = (
    iced_futures::futures::channel::mpsc::UnboundedSender<()>,
    std::sync::Mutex<Option<iced_futures::futures::channel::mpsc::UnboundedReceiver<()>>>,
);

const CORE_CONTROLS: [&str; 4] = ["Volume", "Microphone", "Night Light", "Brightness"];
const SIDEBAR_ANIM_DURATION: Duration = Duration::from_millis(170);
const SEARCH_ANIM_DURATION: Duration = Duration::from_millis(140);
const EXPANDER_ANIM_DURATION: Duration = Duration::from_millis(170);
const GROUP_BACKGROUND_MIX: f32 = 0.35;
const HOVER_HIGHLIGHT_MIX: f32 = 0.12;
const ICON_HOVER_HIGHLIGHT_MIX: f32 = 0.1;
const TILE_BACKGROUND_MIX: f32 = GROUP_BACKGROUND_MIX * 1.15;
const TILE_HOVER_HIGHLIGHT_MIX: f32 = HOVER_HIGHLIGHT_MIX * 1.1 * 1.2;
const ENABLED_TILE_HOVER_HIGHLIGHT_MIX: f32 = TILE_HOVER_HIGHLIGHT_MIX * 1.15;
const TILE_GLOW_ALPHA: f32 = 0.034875;

// ---------------------------------------------------------------------------
// Navigation Stack
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub enum NavView {
    Root(usize),
    SubPage {
        parent_page: usize,
        title: String,
        occurrence: usize,
        sections: Vec<SectionConfig>,
    },
}

#[derive(Debug, Clone)]
pub struct SearchLocation {
    page: usize,
    navigation: Vec<Vec<usize>>,
    expanders: Vec<String>,
    path: Vec<usize>,
}

#[derive(Debug, Clone)]
struct CachedSearchHit {
    breadcrumb: String,
    location: SearchLocation,
    score: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SliderHint {
    Generic,
    NativeVolume,
    NativeMicrophone,
    NativeBrightness,
    NativeSunset,
    DefaultVolumeAction,
    DefaultBrightnessAction,
}

// ---------------------------------------------------------------------------
// Messages
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub enum Message {
    SelectPage(usize),
    OpenSearchResult(SearchLocation),
    SearchPositioned { started: Instant, offset: Option<f32> },
    AnimationFrame(Instant),
    PushSubPage {
        title: String,
        occurrence: usize,
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
        debounce: bool,
    },
    SliderSettled {
        key: String,
        value: f32,
        on_change: Option<ChangeAction>,
        changed_at: Instant,
    },
    SliderReleased {
        key: String,
        value: f32,
        on_change: Option<ChangeAction>,
        debounce: bool,
    },
    SelectOption {
        key: String,
        option: String,
        action: Option<ActionConfig>,
    },
    ToggleService {
        unit: String,
        scope: String,
    },
    ControlsLoaded { generation: u64, slider_revision: u64, snapshot: Snapshot },
    ToggleFinished { key: String, previous: bool, result: Result<(), String> },
    ServiceFinished { key: String, previous: bool, result: Result<(), String> },
    EntryChanged { key: String, value: String },
    SubmitEntry { key: String, action: Option<ActionConfig> },
    EntryFinished { key: String, value: String, result: Result<(), String> },
    SelectionFinished { key: String, previous: Option<String>, result: Result<(), String> },
    DragWindow,
    Tick,
    ThemeLoaded(Option<(AppTheme, ThemeStamp)>),
    ActionFinished { observed: bool, result: Result<(), String> },
    SliderApplied { key: String, result: Result<(), String> },
    CoreLoaded { key: &'static str, previous: Option<f32>, value: Option<f32>, changed_at: Option<Instant>, sunset_active: Option<bool> },
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
    pub config_path: PathBuf,
    pub config_override: Option<PathBuf>,
    pub theme: AppTheme,
    pub theme_stamp: Option<ThemeStamp>,
    pub theme_loading: bool,
    pub nav_stack: Vec<NavView>,
    pub sidebar_open: bool,
    pub search_open: bool,
    pub search_query: String,
    search_cache: Option<(String, usize, Vec<CachedSearchHit>)>,
    pub loaded_pages: HashSet<usize>,
    pub toggle_states: HashMap<String, bool>,
    pub slider_values: HashMap<String, f32>,
    pub selected_options: HashMap<String, String>,
    pub dynamic_options: HashMap<String, Vec<String>>,
    pub service_states: HashMap<String, bool>,
    pub live_labels: HashMap<String, String>,
    pub file_texts: HashMap<String, String>,
    pub hovered_item: Option<String>,
    search_highlight: Option<(Vec<usize>, Instant)>,
    pub active_slider: Option<String>,
    pub sunset_active: bool,
    pub core_loading: HashSet<String>,
    pub applying_sliders: HashSet<String>,
    pub pending_sliders: HashMap<String, (f32, Option<ActionConfig>, Option<ItemConfig>, SliderHint)>,
    pub deferred_sliders: HashMap<String, (f32, Option<ChangeAction>, Instant)>,
    // Readback grace is independent of each deferred edit's timer identity.
    pub slider_changed_at: HashMap<String, Instant>,
    pub slider_revision: u64,
    pub closing: bool,
    pub service_statuses: HashMap<String, ServiceStatus>,
    pub busy_controls: HashSet<String>,
    pub unavailable_controls: HashSet<String>,
    pub active_actions: usize,
    pub control_generation: u64,
    pub controls_loading: Option<u64>,
    pub control_polled_at: HashMap<String, Instant>,
    pub polled_generation: Option<u64>,
    pub action_error: Option<String>,
    pub status_notice: Option<(String, Instant)>,
    pub entry_values: HashMap<String, String>,
    pub editing_entries: HashSet<String>,
    pub animation_now: Instant,
    pub nav_transition: Option<NavTransition>,
    pub sidebar_anim: FloatTransition,
    pub search_anim: FloatTransition,
    pub expander_anims: HashMap<String, FloatTransition>,
}

impl CenterApp {
    /// Build state and defer queries until the returned task runs.
    pub fn new(config: AppConfig, initial_page: Option<String>, initial_hover: Option<String>) -> (Self, Task<Message>) {
        Self::new_loaded(LoadedConfig { config, path: PathBuf::new() }, None, initial_page, initial_hover)
    }

    /// Build state from a resolved configuration without eager worker submission.
    pub fn new_loaded(
        loaded: LoadedConfig,
        config_override: Option<PathBuf>,
        initial_page: Option<String>,
        initial_hover: Option<String>,
    ) -> (Self, Task<Message>) {
        Self::initialize(loaded, config_override, initial_page, initial_hover, false)
    }

    /// Start read-only queries before Iced initializes the compositor.
    pub fn boot(config: AppConfig, initial_page: Option<String>, initial_hover: Option<String>) -> (Self, Task<Message>) {
        Self::boot_loaded(LoadedConfig { config, path: PathBuf::new() }, None, initial_page, initial_hover)
    }

    /// Start read-only queries from a resolved configuration before compositor setup.
    pub fn boot_loaded(
        loaded: LoadedConfig,
        config_override: Option<PathBuf>,
        initial_page: Option<String>,
        initial_hover: Option<String>,
    ) -> (Self, Task<Message>) {
        Self::initialize(loaded, config_override, initial_page, initial_hover, true)
    }

    fn initialize(
        loaded: LoadedConfig,
        config_override: Option<PathBuf>,
        initial_page: Option<String>,
        initial_hover: Option<String>,
        eager: bool,
    ) -> (Self, Task<Message>) {
        let mut config = loaded.config;
        expand_app_config_generators(&mut config);
        crate::renderer::trace_phase("generators-ready");
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

        let (theme, theme_stamp) = AppTheme::load_with_stamp();
        let boot_now = Instant::now();
        let mut app = Self {
            config,
            config_path: loaded.path,
            config_override,
            theme,
            theme_stamp,
            theme_loading: false,
            nav_stack: vec![NavView::Root(start_page)],
            sidebar_open: true,
            search_open: false,
            search_query: String::new(),
            search_cache: None,
            loaded_pages: HashSet::new(),
            toggle_states: HashMap::new(),
            slider_values: HashMap::new(),
            selected_options: HashMap::new(),
            dynamic_options: HashMap::new(),
            service_states: HashMap::new(),
            live_labels: HashMap::new(),
            file_texts: HashMap::new(),
            hovered_item: initial_hover,
            search_highlight: None,
            active_slider: None,
            sunset_active: false,
            core_loading: HashSet::new(),
            applying_sliders: HashSet::new(),
            pending_sliders: HashMap::new(),
            deferred_sliders: HashMap::new(),
            slider_changed_at: HashMap::new(),
            slider_revision: 0,
            closing: false,
            service_statuses: HashMap::new(),
            busy_controls: HashSet::new(),
            unavailable_controls: HashSet::new(),
            active_actions: 0,
            control_generation: 0,
            controls_loading: None,
            control_polled_at: HashMap::new(),
            polled_generation: None,
            action_error: None,
            status_notice: None,
            entry_values: HashMap::new(),
            editing_entries: HashSet::new(),
            animation_now: boot_now,
            nav_transition: None,
            sidebar_anim: FloatTransition::settled(1.0, boot_now),
            search_anim: FloatTransition::settled(0.0, boot_now),
            expander_anims: HashMap::new(),
        };

        // LAZY LOADING: Only initialize active page on cold start!
        app.lazy_load_page(start_page);
        let task = Task::batch([app.refresh_core_with(eager), app.refresh_controls_with(eager)]);
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

    /// Each visible hardware control refreshes independently; slow DDC only
    /// occupies its own slot. All subprocess waits use the blocking worker pool.
    fn refresh_core(&mut self) -> Task<Message> {
        self.refresh_core_with(false)
    }

    fn refresh_core_with(&mut self, eager: bool) -> Task<Message> {
        if self.closing { return Task::none(); }
        let visible: HashSet<_> = self.visible_items().into_iter()
            .filter(|item| matches!(item.item_type.as_str(), "slider" | "spin"))
            .map(controls::item_key).collect();
        let mut tasks = Vec::new();
        // Submit quick IPC before potentially slow DDC discovery.
        for key in CORE_CONTROLS {
            if !visible.contains(key) || !self.core_loading.insert(key.into()) { continue; }
            let changed_at = self.slider_changed_at.get(key).copied();
            let previous = self.slider_values.get(key).copied();
            let query = move || {
                let (value, sunset_active) = match key {
                    "Volume" => (sys::get_volume(), None),
                    "Microphone" => (sys::get_microphone_volume(), None),
                    "Brightness" => (sys::get_brightness(), None),
                    _ => {
                        let active = sys::is_sunset_active();
                        (if active { sys::get_sunset() } else { None }, Some(active))
                    }
                };
                Message::CoreLoaded { key, previous, value, changed_at, sunset_active }
            };
            tasks.push(if eager { worker::start(query) } else { worker::perform(query) });
        }
        Task::batch(tasks)
    }

    fn refresh_visible(&mut self) -> Task<Message> {
        Task::batch([self.refresh_core(), self.refresh_controls()])
    }

    fn queue_slider(&mut self, key: String, value: f32, on_change: Option<ChangeAction>) -> Task<Message> {
        let hint = classify_slider_action(&key, on_change.as_ref());
        let action = match on_change {
            Some(ChangeAction::Direct(action)) => {
                let mut action = controls::with_value(&action, &value.to_string());
                if let ActionConfig::Exec { timeout, .. } | ActionConfig::Argv { timeout, .. } = &mut action
                    && timeout.is_none() {
                    *timeout = Some(15);
                }
                Some(action)
            }
            _ if hint != SliderHint::Generic => None,
            _ if self.visible_items().iter().any(|item| controls::item_key(item) == key
                && item.properties.persistence == "app" && !item.properties.key.is_empty()) => None,
            _ => return Task::none(),
        };
        let item = self.visible_items().into_iter().find(|item| controls::item_key(item) == key).cloned();
        self.pending_sliders.insert(key.clone(), (value, action, item, hint));
        self.apply_pending_slider(key)
    }

    fn apply_pending_slider(&mut self, key: String) -> Task<Message> {
        if self.applying_sliders.contains(&key) { return Task::none(); }
        let Some((value, action, item, hint)) = self.pending_sliders.remove(&key) else { return Task::none(); };
        self.applying_sliders.insert(key.clone());
        worker::perform(move || match hint {
            SliderHint::NativeSunset => sys::apply_sunset(value),
            SliderHint::NativeVolume => sys::apply_volume(value),
            SliderHint::NativeMicrophone => sys::apply_microphone_volume(value),
            SliderHint::NativeBrightness => sys::apply_brightness(value),
            SliderHint::DefaultVolumeAction => {
                let Some(act) = action.as_ref() else {
                    return sys::apply_volume(value);
                };
                controls::run_action(act)?;
                if value.round() > 0.0 {
                    sys::unmute_default_sink();
                }
                Ok(())
            }
            SliderHint::DefaultBrightnessAction => {
                let Some(act) = action.as_ref() else {
                    return sys::apply_brightness(value);
                };
                match controls::run_action(act) {
                    Ok(()) => {
                        if sys::has_ddc_brightness() {
                            let _ = sys::apply_ddc_brightness(value);
                        }
                        Ok(())
                    }
                    Err(local_err) => {
                        if sys::has_ddc_brightness() {
                            sys::apply_ddc_brightness(value)
                        } else {
                            Err(local_err)
                        }
                    }
                }
            }
            SliderHint::Generic => {
                if let Some(item) = item {
                    controls::set_scalar(&item, &value.to_string(), action.as_ref())
                } else if let Some(action) = action {
                    controls::run_action(&action)
                } else {
                    Ok(())
                }
            }
        }).map(move |result| Message::SliderApplied { key: key.clone(), result })
    }

    fn sync_slider(&mut self, key: &str, value: f32) {
        let recent = self.slider_changed_at.get(key)
            .is_some_and(|time| time.elapsed() < Duration::from_secs(3));
        if self.active_slider.as_deref() != Some(key) && !recent
            && !self.applying_sliders.contains(key) && !self.pending_sliders.contains_key(key)
            && !self.deferred_sliders.contains_key(key)
        {
            self.slider_values.insert(key.into(), value);
        }
    }

    // ---------------------------------------------------------------------------
    // Update Loop
    // ---------------------------------------------------------------------------

    pub fn update(&mut self, message: Message) -> Task<Message> {
        match message {
            Message::OpenSearchResult(location) => {
                let prev_page = self.active_page_idx();
                let was_subpage = self.nav_stack.len() > 1;
                let target_has_subpage = !location.navigation.is_empty();
                let pending = self.flush_deferred_sliders();
                self.lazy_load_page(location.page);
                self.nav_stack = vec![NavView::Root(location.page)];
                for path in location.navigation {
                    let next = {
                        let sections = match self.nav_stack.last() {
                            Some(NavView::SubPage { sections, .. }) => sections.as_slice(),
                            _ => self.config.pages.get(location.page).map(|p| p.layout.as_slice()).unwrap_or(&[]),
                        };
                        navigation_at(sections, &path).map(|item| {
                            let occurrence = navigation_occurrence(sections, item);
                            (item.properties.title.clone(), occurrence, item.layout.clone())
                        })
                    };
                    let Some((title, occurrence, sections)) = next else { break; };
                    for section in &sections {
                        for item in &section.items {
                            collect_item_defaults(item, &mut self.slider_values, &mut self.toggle_states,
                                &mut self.service_states, &mut self.selected_options);
                        }
                    }
                    self.nav_stack.push(NavView::SubPage { parent_page: location.page, title, occurrence, sections });
                }
                for key in location.expanders { self.toggle_states.insert(key, true); }
                let started = Instant::now();
                self.animation_now = self.animation_now.max(started);
                // Snap search bar and expanders so LocateSearchTarget measures exact resting bounds.
                self.search_anim.snap(0.0, self.animation_now);
                self.expander_anims.clear();
                if target_has_subpage {
                    self.nav_transition = Some(NavTransition::start(NavDirection::Push, self.animation_now));
                } else if location.page > prev_page {
                    self.nav_transition = Some(NavTransition::start(NavDirection::PageDown, self.animation_now));
                } else if location.page < prev_page {
                    self.nav_transition = Some(NavTransition::start(NavDirection::PageUp, self.animation_now));
                } else if was_subpage {
                    self.nav_transition = Some(NavTransition::start(NavDirection::Pop, self.animation_now));
                }
                self.search_highlight = Some((location.path, self.animation_now));
                self.search_query.clear();
                self.search_cache = None;
                self.search_open = false;
                self.status_notice = None;
                self.control_generation += 1;
                let highlight_started = self.animation_now;
                let reveal = iced_runtime::task::widget(LocateSearchTarget {
                    target: iced_core::widget::Id::new("search_target"), viewport: None, target_bounds: None,
                }).map(move |offset| Message::SearchPositioned { started: highlight_started, offset });
                Task::batch([pending, self.refresh_visible(), reveal])
            }
            Message::SearchPositioned { started, offset } => {
                if self.search_highlight.as_ref().is_some_and(|(_, time)| *time == started)
                    && let Some(y) = offset
                {
                    return operation::scroll_to("page_content", operation::AbsoluteOffset { x: 0.0, y });
                }
                Task::none()
            }
            Message::AnimationFrame(now) => {
                self.animation_now = self.animation_now.max(now);
                let frame_now = self.animation_now;
                if self
                    .nav_transition
                    .as_ref()
                    .is_some_and(|anim| !anim.is_active(frame_now))
                {
                    self.nav_transition = None;
                }
                self.expander_anims.retain(|_, anim| anim.is_active(frame_now));
                if self.search_highlight.as_ref().is_some_and(|(_, started)| {
                    frame_now.saturating_duration_since(*started) >= Duration::from_millis(1200)
                }) {
                    self.search_highlight = None;
                }
                Task::none()
            }
            Message::SelectPage(idx) => {
                if idx < self.config.pages.len() {
                    let prev_page = self.active_page_idx();
                    let was_subpage = self.nav_stack.len() > 1;
                    let pending = self.flush_deferred_sliders();
                    self.lazy_load_page(idx);
                    self.nav_stack = vec![NavView::Root(idx)];
                    self.search_highlight = None;
                    self.search_query.clear();
                    self.search_cache = None;
                    self.status_notice = None;
                    self.expander_anims.clear();
                    if idx > prev_page {
                        self.start_nav_transition(NavDirection::PageDown);
                    } else if idx < prev_page {
                        self.start_nav_transition(NavDirection::PageUp);
                    } else if was_subpage {
                        self.start_nav_transition(NavDirection::Pop);
                    }
                    self.control_generation += 1;
                    return Task::batch([pending, self.refresh_visible()]);
                }
                Task::none()
            }

            Message::PushSubPage { title, occurrence, sections } => {
                self.search_highlight = None;
                let pending = self.flush_deferred_sliders();
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
                    occurrence,
                    sections,
                });
                self.search_query.clear();
                self.search_cache = None;
                self.status_notice = None;
                self.expander_anims.clear();
                self.start_nav_transition(NavDirection::Push);
                self.control_generation += 1;
                Task::batch([pending, self.refresh_visible()])
            }

            Message::PopSubPage => {
                self.search_highlight = None;
                self.status_notice = None;
                let pending = self.flush_deferred_sliders();
                if self.nav_stack.len() > 1 {
                    self.nav_stack.pop();
                    self.expander_anims.clear();
                    self.start_nav_transition(NavDirection::Pop);
                }
                self.control_generation += 1;
                Task::batch([pending, self.refresh_visible()])
            }

            Message::ToggleSidebar => {
                self.sidebar_open = !self.sidebar_open;
                let now = self.tick_animation_clock();
                self.sidebar_anim.retarget(
                    if self.sidebar_open { 1.0 } else { 0.0 },
                    now,
                    SIDEBAR_ANIM_DURATION,
                );
                Task::none()
            }

            Message::ToggleSearch => {
                self.search_open = !self.search_open;
                let now = self.tick_animation_clock();
                self.search_anim.retarget(
                    if self.search_open { 1.0 } else { 0.0 },
                    now,
                    SEARCH_ANIM_DURATION,
                );
                if self.search_open {
                    operation::focus("search_input")
                } else {
                    self.search_query.clear();
                    self.search_cache = None;
                    Task::none()
                }
            }

            Message::SearchChanged(q) => {
                self.search_highlight = None;
                self.search_query = q;
                self.rebuild_search_cache();
                Task::none()
            }

            Message::ClearSearch => {
                self.search_query.clear();
                self.search_cache = None;
                self.search_open = false;
                let now = self.tick_animation_clock();
                self.search_anim.retarget(0.0, now, SEARCH_ANIM_DURATION);
                Task::none()
            }

            Message::ExecuteAction(ActionConfig::Reload) => self.update(Message::ReloadConfig),

            Message::ExecuteAction(action) => {
                if self.closing { return Task::none(); }
                self.status_notice = None;
                if let ActionConfig::Redirect { page } = &action {
                    if let Some(index) = self.config.pages.iter().position(|p| &p.id == page) {
                        return self.update(Message::SelectPage(index));
                    }
                    return Task::none();
                }
                self.action_error = None;
                if controls::is_observed_button_action(&action) {
                    self.active_actions += 1;
                    self.control_generation += 1;
                    worker::perform(move || controls::run_action(&action))
                        .map(|result| Message::ActionFinished { observed: true, result })
                } else {
                    worker::perform(move || controls::launch(&action))
                        .map(|result| Message::ActionFinished { observed: false, result })
                }
            }

            Message::ActionFinished { observed, result } => {
                if observed {
                    self.active_actions = self.active_actions.saturating_sub(1);
                    self.control_generation += 1;
                }
                if let Err(error) = result {
                    self.action_error = Some(error);
                }
                if observed {
                    self.finish_or_refresh()
                } else {
                    Task::none()
                }
            }

            Message::ToggleItem { key, is_enabled, on_toggle } => {
                if self.closing || self.busy_controls.contains(&key) { return Task::none(); }
                self.status_notice = None;
                let previous = self.toggle_states.get(&key).copied().unwrap_or(false);
                let item = self.visible_items().into_iter().find(|i| controls::item_key(i) == key).cloned();
                let Some(item) = item else {
                    let pending = self.flush_deferred_sliders();
                    let now = self.tick_animation_clock();
                    let current = self
                        .expander_anims
                        .get(&key)
                        .map(|anim| anim.value(now))
                        .unwrap_or(if previous { 1.0 } else { 0.0 });
                    let mut anim = FloatTransition::settled(current, now);
                    anim.retarget(
                        if is_enabled { 1.0 } else { 0.0 },
                        now,
                        EXPANDER_ANIM_DURATION,
                    );
                    self.expander_anims.insert(key.clone(), anim);
                    self.toggle_states.insert(key.clone(), is_enabled);
                    self.control_generation += 1;
                    return Task::batch([pending, self.refresh_visible()]); // Local expander state.
                };
                let action = on_toggle.map(|pair| if is_enabled { pair.enabled } else { pair.disabled });
                let launched = action.as_ref().is_some_and(controls::is_launch);
                if !launched {
                    self.toggle_states.insert(key.clone(), is_enabled);
                }
                self.busy_controls.insert(key.clone());
                self.control_generation += 1;
                self.action_error = None;
                worker::perform(move || {
                    controls::set_toggle(&item, is_enabled, action.as_ref())
                }).map(move |result| Message::ToggleFinished { key: key.clone(), previous, result })
            }

            Message::ToggleFinished { key, previous, result } => {
                self.busy_controls.remove(&key);
                if let Err(error) = result {
                    self.toggle_states.insert(key.clone(), previous);
                    self.action_error = Some(format!("{key}: {error}"));
                }
                self.control_generation += 1;
                self.finish_or_refresh()
            }

            Message::ServiceFinished { key, previous, result } => {
                self.busy_controls.remove(&key);
                if let Err(error) = result {
                    self.service_states.insert(key.clone(), previous);
                    self.action_error = Some(format!("{key}: {error}"));
                }
                self.control_generation += 1;
                self.finish_or_refresh()
            }

            Message::ControlsLoaded { generation, slider_revision, snapshot } => {
                crate::renderer::trace_phase("controls-ready");
                if self.controls_loading == Some(generation) { self.controls_loading = None; }
                if self.control_generation != generation {
                    return if self.polled_generation != Some(self.control_generation) {
                        self.refresh_controls()
                    } else {
                        Task::none()
                    };
                }
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
                    if self.busy_controls.contains(&key) { continue; }
                    if let Some(value) = value {
                        self.selected_options.insert(key.clone(), value);
                        self.unavailable_controls.remove(&key);
                    } else {
                        self.selected_options.remove(&key);
                        self.unavailable_controls.insert(key);
                    }
                }
                for (key, options) in snapshot.options {
                    if self.selected_options.get(&key).is_some_and(|current| !options.contains(current)) {
                        self.selected_options.remove(&key);
                    }
                    self.dynamic_options.insert(key, options);
                }
                // Generic queries can finish after an edit and its grace period.
                if slider_revision == self.slider_revision {
                    for (key, value) in snapshot.sliders { self.sync_slider(&key, value); }
                }
                self.live_labels.extend(snapshot.labels);
                for (path, text) in snapshot.file_texts {
                    if let Some(value) = text {
                        self.file_texts.insert(path, value);
                    } else {
                        self.file_texts.remove(&path);
                    }
                }
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
                if self.closing || self.busy_controls.contains(&key) { return Task::none(); }
                self.status_notice = None;
                let value = self.entry_values.get(&key).cloned().unwrap_or_default();
                let action = action.map(|action| controls::with_value(&action, &value));
                let Some(item) = self.visible_items().into_iter().find(|i| controls::item_key(i) == key).cloned() else { return Task::none(); };
                self.busy_controls.insert(key.clone());
                self.control_generation += 1;
                self.action_error = None;
                worker::perform(move || {
                    let result = controls::set_scalar(&item, &value, action.as_ref());
                    Message::EntryFinished { key, value, result }
                })
            }

            Message::EntryFinished { key, value, result } => {
                self.busy_controls.remove(&key);
                match result {
                    Ok(()) => {
                        if self.entry_values.get(&key) == Some(&value) { self.editing_entries.remove(&key); }
                    }
                    Err(error) => { self.action_error = Some(format!("{key}: {error}")); }
                }
                self.control_generation += 1;
                self.finish_or_refresh()
            }

            Message::SliderChanged { key, value, on_change, debounce } => {
                if self.closing { return Task::none(); }
                self.status_notice = None;
                self.active_slider = debounce.then(|| key.clone());
                self.slider_values.insert(key.clone(), value);
                self.slider_revision += 1;
                let changed_at = Instant::now();
                self.slider_changed_at.insert(key.clone(), changed_at);
                if !debounce {
                    return self.queue_slider(key, value, on_change);
                }
                self.deferred_sliders.insert(key.clone(), (value, on_change.clone(), changed_at));
                // Explicitly debounced controls also commit keyboard/wheel edits.
                Task::perform(async move {
                    futures_timer::Delay::new(Duration::from_millis(100)).await;
                    Message::SliderSettled { key, value, on_change, changed_at }
                }, |message| message)
            }

            Message::SliderSettled { key, value, on_change, changed_at } => {
                if self.deferred_sliders.get(&key).is_some_and(|(pending, _, scheduled)|
                    *pending == value && *scheduled == changed_at)
                {
                    return self.update(Message::SliderReleased { key, value, on_change, debounce: true });
                }
                Task::none()
            }

            Message::SliderReleased {
                key,
                value,
                on_change,
                debounce,
            } => {
                if self.closing { return Task::none(); }
                let value = self.slider_values.get(&key).copied().unwrap_or(value);
                self.active_slider = None;
                self.slider_values.insert(key.clone(), value);
                if debounce && self.deferred_sliders.remove(&key).is_some() {
                    return self.queue_slider(key, value, on_change);
                }
                Task::none()
            }

            Message::SelectOption {
                key,
                option,
                action,
            } => {
                if self.closing || self.busy_controls.contains(&key) { return Task::none(); }
                self.status_notice = None;
                let Some(item) = self.visible_items().into_iter().find(|i| controls::item_key(i) == key).cloned() else { return Task::none(); };
                let previous = self.selected_options.get(&key).cloned();
                let launched = action.as_ref().is_some_and(controls::is_launch);
                if !launched {
                    self.selected_options.insert(key.clone(), option.clone());
                }
                self.busy_controls.insert(key.clone());
                self.control_generation += 1;
                self.action_error = None;
                worker::perform(move || {
                    let action = action.map(|act| controls::with_value(&act, &option));
                    controls::set_scalar(&item, &option, action.as_ref())
                }).map(move |result| Message::SelectionFinished { key: key.clone(), previous: previous.clone(), result })
            }

            Message::SelectionFinished { key, previous, result } => {
                self.busy_controls.remove(&key);
                if let Err(error) = result {
                    if let Some(value) = previous { self.selected_options.insert(key.clone(), value); }
                    else { self.selected_options.remove(&key); }
                    self.action_error = Some(format!("{key}: {error}"));
                }
                self.control_generation += 1;
                self.finish_or_refresh()
            }

            Message::ToggleService { unit, scope } => {
                let key = controls::service_key(&scope, &unit);
                if self.closing || self.busy_controls.contains(&key) { return Task::none(); }
                self.status_notice = None;
                let previous = self.service_states.get(&key).copied().unwrap_or(false);
                self.service_states.insert(key.clone(), !previous);
                self.busy_controls.insert(key.clone());
                self.control_generation += 1;
                self.action_error = None;
                worker::perform(move || {
                    controls::set_service(&scope, &unit, !previous)
                }).map(move |result| Message::ServiceFinished { key: key.clone(), previous, result })
            }

            Message::DragWindow => iced::window::latest().and_then(iced::window::drag),

            Message::Tick => {
                if self.closing { return Task::none(); }

                if self
                    .status_notice
                    .as_ref()
                    .is_some_and(|(_, created)| created.elapsed() >= Duration::from_secs(2))
                {
                    self.status_notice = None;
                }

                let mut tasks = vec![self.refresh_core(), self.refresh_controls()];
                if !self.theme_loading {
                    self.theme_loading = true;
                    let stamp = self.theme_stamp.clone();
                    tasks.push(worker::perform(move || {
                        Message::ThemeLoaded(AppTheme::load_if_changed(stamp.as_ref()))
                    }));
                }
                Task::batch(tasks)
            }

            Message::ThemeLoaded(loaded) => {
                self.theme_loading = false;
                if let Some((theme, stamp)) = loaded {
                    self.theme = theme;
                    self.theme_stamp = Some(stamp);
                }
                Task::none()
            }

            Message::SliderApplied { key, result } => {
                self.applying_sliders.remove(&key);
                self.slider_changed_at.insert(key.clone(), Instant::now());
                self.slider_revision += 1;
                if let Err(error) = result { self.action_error = Some(format!("{key}: {error}")); }
                if self.pending_sliders.contains_key(&key) { return self.apply_pending_slider(key); }
                if self.closing && self.writes_finished() { return iced::exit(); }
                Task::none()
            }

            Message::CoreLoaded { key, previous, value, changed_at, sunset_active } => {
                self.core_loading.remove(key);
                crate::renderer::trace_phase(format_args!("{key}-ready"));
                if let Some(active) = sunset_active { self.sunset_active = active; }
                if self.slider_changed_at.get(key).copied() == changed_at
                    && self.slider_values.get(key).copied() == previous
                    && let Some(value) = value
                { self.sync_slider(key, value); }
                Task::none()
            }

            Message::CloseApp | Message::EventOccurred(Event::Window(iced::window::Event::CloseRequested)) => {
                // Drain each slider's latest write before releasing workers.
                let deferred = std::mem::take(&mut self.deferred_sliders);
                let mut tasks: Vec<_> = deferred.into_iter().map(|(key, (value, action, _))| self.queue_slider(key, value, action)).collect();
                self.closing = true;
                let pending: Vec<_> = self.pending_sliders.keys().cloned().collect();
                tasks.extend(pending.into_iter().map(|key| self.apply_pending_slider(key)));
                if self.writes_finished() { iced::exit() }
                else { Task::batch(tasks) }
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
                if self.closing { return Task::none(); }
                if let Some((theme, stamp)) = AppTheme::load_if_changed(self.theme_stamp.as_ref()) {
                    self.theme = theme;
                    self.theme_stamp = Some(stamp);
                }
                let mut tasks = vec![];
                match AppConfig::load_resolved(self.config_override.as_deref()) {
                    Ok(mut loaded) => {
                        let page_id = self.config.pages.get(self.active_page_idx()).map(|page| page.id.clone());
                        let subpage_crumbs: Vec<(String, usize)> = self
                            .nav_stack
                            .iter()
                            .filter_map(|view| match view {
                                NavView::SubPage { title, occurrence, .. } => {
                                    Some((title.clone(), *occurrence))
                                }
                                NavView::Root(_) => None,
                            })
                            .collect();

                        // Flush deferred slider edits using old item metadata before swapping config.
                        tasks.push(self.flush_deferred_sliders());

                        expand_app_config_generators(&mut loaded.config);

                        let (index, mut fell_back) = match page_id
                            .as_deref()
                            .and_then(|id| loaded.config.pages.iter().position(|page| page.id == id))
                        {
                            Some(idx) => (idx, false),
                            None => (0, page_id.is_some()),
                        };

                        let mut new_nav_stack = if loaded.config.pages.is_empty() {
                            vec![]
                        } else {
                            vec![NavView::Root(index)]
                        };

                        if !fell_back && !new_nav_stack.is_empty() {
                            for (title, occurrence) in subpage_crumbs {
                                let next = {
                                    let current_sections = match new_nav_stack.last() {
                                        Some(NavView::SubPage { sections, .. }) => sections.as_slice(),
                                        _ => loaded
                                            .config
                                            .pages
                                            .get(index)
                                            .map(|p| p.layout.as_slice())
                                            .unwrap_or(&[]),
                                    };
                                    find_navigation_subpage(current_sections, &title, occurrence).map(
                                        |nav_item| NavView::SubPage {
                                            parent_page: index,
                                            title: nav_item.properties.title.clone(),
                                            occurrence,
                                            sections: nav_item.layout.clone(),
                                        },
                                    )
                                };
                                if let Some(view) = next {
                                    new_nav_stack.push(view);
                                } else {
                                    fell_back = true;
                                    break;
                                }
                            }
                        }

                        self.config = loaded.config;
                        self.config_path = loaded.path;
                        self.nav_stack = new_nav_stack;

                        let slider_bounds = collect_slider_bounds(&self.config);
                        self.slider_values.retain(|key, value| {
                            let keep = self.applying_sliders.contains(key)
                                || self.pending_sliders.contains_key(key)
                                || self.core_loading.contains(key)
                                || CORE_CONTROLS.contains(&key.as_str());
                            if keep
                                && let Some(&(min, max)) = slider_bounds.get(key)
                            {
                                *value = value.clamp(min, max);
                            }
                            keep
                        });
                        self.toggle_states.retain(|key, _| {
                            key.starts_with("expander:") || self.busy_controls.contains(key)
                        });
                        self.selected_options
                            .retain(|key, _| self.busy_controls.contains(key));
                        self.service_states
                            .retain(|key, _| self.busy_controls.contains(key));
                        self.service_statuses
                            .retain(|key, _| self.busy_controls.contains(key));
                        self.entry_values
                            .retain(|key, _| self.busy_controls.contains(key));
                        self.editing_entries
                            .retain(|key| self.busy_controls.contains(key));

                        self.dynamic_options.clear();
                        self.live_labels.clear();
                        self.file_texts.clear();
                        self.unavailable_controls.clear();
                        self.control_polled_at.clear();
                        self.loaded_pages.clear();
                        self.hovered_item = None;
                        self.search_highlight = None;
                        self.active_slider = None;
                        self.expander_anims.clear();
                        self.rebuild_search_cache();

                        self.lazy_load_page(self.active_page_idx());
                        for view in &self.nav_stack {
                            if let NavView::SubPage { sections, .. } = view {
                                for section in sections {
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

                        self.control_generation += 1;
                        self.slider_revision += 1;
                        self.controls_loading = None;
                        self.polled_generation = None;
                        self.action_error = None;
                        self.status_notice = Some((
                            format!("Reloaded {}", format_display_path(&self.config_path)),
                            Instant::now(),
                        ));

                        if fell_back {
                            self.start_nav_transition(NavDirection::Pop);
                            tasks.push(operation::scroll_to(
                                "page_content",
                                operation::AbsoluteOffset { x: 0.0, y: 0.0 },
                            ));
                        } else {
                            self.nav_transition = None;
                        }
                    }
                    Err(error) => {
                        self.status_notice = None;
                        self.action_error = Some(format!("Reload: {error}"));
                    }
                }
                tasks.push(self.refresh_visible());
                Task::batch(tasks)
            }

            Message::EventOccurred(Event::Keyboard(iced::keyboard::Event::KeyPressed {
                key,
                modifiers,
                ..
            })) => {
                if key == Key::Named(Named::Escape) {
                    if self.search_open || !self.search_query.is_empty() {
                        return self.update(Message::ClearSearch);
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
                    let now = self.tick_animation_clock();
                    self.search_anim.retarget(1.0, now, SEARCH_ANIM_DURATION);
                    return operation::focus("search_input");
                }
                Task::none()
            }

            Message::EventOccurred(_) => Task::none(),
        }
    }

    fn current_sections(&self) -> &[SectionConfig] {
        match self.nav_stack.last() {
            Some(NavView::SubPage { sections, .. }) => sections.as_slice(),
            Some(NavView::Root(index)) => self
                .config
                .pages
                .get(*index)
                .map(|p| p.layout.as_slice())
                .unwrap_or(&[]),
            None => self
                .config
                .pages
                .get(self.active_page_idx())
                .map(|p| p.layout.as_slice())
                .unwrap_or(&[]),
        }
    }

    fn visible_items(&self) -> Vec<&ItemConfig> {
        let sections = self.current_sections();
        fn collect<'a>(items: &'a [ItemConfig], states: &HashMap<String, bool>, output: &mut Vec<&'a ItemConfig>) {
            for item in items {
                output.push(item);
                if item.item_type != "expander" || states.get(&format!("expander:{}", controls::item_key(item))).copied().unwrap_or(false) {
                    collect(&item.items, states, output);
                }
            }
        }
        let mut items = vec![];
        for section in sections { collect(&section.items, &self.toggle_states, &mut items); }
        items
    }

    fn refresh_controls(&mut self) -> Task<Message> {
        self.refresh_controls_with(false)
    }

    fn refresh_controls_with(&mut self, eager: bool) -> Task<Message> {
        if self.closing { return Task::none(); }
        if self.controls_loading.is_some() { return Task::none(); }
        let generation = self.control_generation;
        self.controls_loading = Some(generation);
        let force = self.polled_generation != Some(generation);
        self.polled_generation = Some(generation);
        let now = Instant::now();
        let items = self.visible_items().into_iter().filter(|item| {
            let has_file_text = !item.properties.button_text_file.is_empty();
            if !has_file_text
                && !matches!(
                    item.item_type.as_str(),
                    "toggle"
                        | "toggle_card"
                        | "selection"
                        | "entry"
                        | "secret"
                        | "label"
                        | "slider"
                        | "spin"
                        | "service"
                        | "service_card"
                )
            {
                return false;
            }
            let key = if item.properties.service.is_empty() { controls::item_key(item) }
                else { controls::service_key(&item.properties.scope, &item.properties.service) };
            if self.busy_controls.contains(&key) { return false; }
            if matches!(item.item_type.as_str(), "slider" | "spin") && CORE_CONTROLS.contains(&key.as_str()) {
                return false;
            }
            let interval = item.properties.interval.unwrap_or(
                if !has_file_text && item.properties.key.is_empty() && matches!(item.item_type.as_str(), "entry" | "secret" | "label") { 0 } else { 3 });
            let poll_key = format!("{}:{key}", item.item_type);
            force
                || !self.control_polled_at.get(&poll_key).is_some_and(|last| {
                    interval == 0 || now.duration_since(*last) < Duration::from_secs(interval)
                })
        }).cloned().collect::<Vec<_>>();
        for item in &items {
            let key = if item.properties.service.is_empty() { controls::item_key(item) }
                else { controls::service_key(&item.properties.scope, &item.properties.service) };
            self.control_polled_at.insert(format!("{}:{key}", item.item_type), now);
        }
        if items.is_empty() {
            self.controls_loading = None;
            return Task::none();
        }
        let slider_revision = self.slider_revision;
        let query = move || controls::query(&items);
        let task = if eager { worker::start(query) } else { worker::perform(query) };
        task.map(move |snapshot| {
            Message::ControlsLoaded { generation, slider_revision, snapshot }
        })
    }

    fn writes_finished(&self) -> bool {
        self.applying_sliders.is_empty()
            && self.pending_sliders.is_empty()
            && self.busy_controls.is_empty()
            && self.active_actions == 0
    }

    fn rebuild_search_cache(&mut self) {
        if self.search_query.is_empty() {
            self.search_cache = None;
            return;
        }
        let (total, hits) = build_search_cache(&self.config, &self.search_query);
        self.search_cache = Some((self.search_query.clone(), total, hits));
    }

    fn visible_controls_items<'a>(&self, section: &'a SectionConfig) -> Vec<&'a ItemConfig> {
        section
            .items
            .iter()
            .filter(|item| {
                let key = if item.properties.key.is_empty() {
                    &item.properties.title
                } else {
                    &item.properties.key
                };
                key != "Night Light" || self.sunset_active
            })
            .collect()
    }

    fn flush_deferred_sliders(&mut self) -> Task<Message> {
        let deferred = std::mem::take(&mut self.deferred_sliders);
        Task::batch(deferred.into_iter().map(|(key, (value, action, _))| self.queue_slider(key, value, action)))
    }

    fn finish_or_refresh(&mut self) -> Task<Message> {
        if self.closing {
            if self.writes_finished() { iced::exit() } else { Task::none() }
        } else { self.refresh_controls() }
    }

    pub fn trigger_dismiss() {
        let _ = Self::dismiss_channel().0.unbounded_send(());
    }

    fn tick_animation_clock(&mut self) -> Instant {
        let now = self.animation_now.max(Instant::now());
        self.animation_now = now;
        now
    }

    fn start_nav_transition(&mut self, direction: NavDirection) {
        let now = self.tick_animation_clock();
        self.nav_transition = Some(NavTransition::start(direction, now));
    }

    pub fn has_active_animations(&self) -> bool {
        let now = self.animation_now;
        self.nav_transition
            .as_ref()
            .is_some_and(|anim| anim.is_active(now))
            || self.sidebar_anim.is_active(now)
            || self.search_anim.is_active(now)
            || self.expander_anims.values().any(|anim| anim.is_active(now))
            || self.search_highlight.is_some()
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
        let frames = if self.has_active_animations() {
            iced::window::frames().map(Message::AnimationFrame)
        } else {
            Subscription::none()
        };
        Subscription::batch([
            events,
            Subscription::run(Self::ticks),
            frames,
            Subscription::run(Self::dismiss_events),
        ])
    }

    fn is_search_target(&self, item: &ItemConfig) -> bool {
        let Some((path, _)) = &self.search_highlight else { return false; };
        item_at(self.current_sections(), path).is_some_and(|target| std::ptr::eq(target, item))
    }

    fn control_id(&self, item: &ItemConfig) -> iced_core::widget::Id {
        iced_core::widget::Id::new(if self.is_search_target(item) { "search_target" } else { "control" })
    }

    fn search_highlight_strength(&self, item: &ItemConfig) -> f32 {
        if !self.is_search_target(item) { return 0.0; }
        self.search_highlight.as_ref().map_or(0.0, |(_, started)| {
            // Hold gently, then fade out over the final 350 ms.
            let elapsed = self
                .animation_now
                .saturating_duration_since(*started)
                .as_secs_f32();
            ((1.2 - elapsed) / 0.35).clamp(0.0, 1.0)
        })
    }

    fn ticks() -> impl iced_futures::futures::Stream<Item = Message> {
        iced_futures::futures::stream::unfold((), |()| async {
            futures_timer::Delay::new(Duration::from_secs(3)).await;
            Some((Message::Tick, ()))
        })
    }

    fn dismiss_channel() -> &'static DismissChannel {
        static CHANNEL: std::sync::OnceLock<DismissChannel> = std::sync::OnceLock::new();
        CHANNEL.get_or_init(|| {
            let (tx, rx) = iced_futures::futures::channel::mpsc::unbounded();
            (tx, std::sync::Mutex::new(Some(rx)))
        })
    }

    fn dismiss_events() -> impl iced_futures::futures::Stream<Item = Message> {
        use iced_futures::futures::StreamExt;
        let rx = Self::dismiss_channel()
            .1
            .lock()
            .unwrap()
            .take()
            .unwrap_or_else(|| {
                let (_, rx) = iced_futures::futures::channel::mpsc::unbounded();
                rx
            });
        rx.map(|()| Message::CloseApp)
    }

    // ---------------------------------------------------------------------------
    // View Root: Unified, Seamless Window (No Disjoint Header Bar)
    // ---------------------------------------------------------------------------

    pub fn view<'a>(&'a self) -> Element<'a, Message> {
        let palette = self.theme;
        let sidebar_factor = self.sidebar_anim.value(self.animation_now);
        let sidebar_interactive = self.sidebar_anim.target() > 0.0;
        let divider_width = if sidebar_factor > 0.01 { 1 } else { 0 };
        let divider_alpha = 0.25 * sidebar_factor;

        row![
            appearance::reveal_width(
                self.view_sidebar(),
                sidebar_factor,
                sidebar_factor,
                sidebar_interactive,
            ),
            container(Space::new().width(divider_width).height(Length::Fill)).style(move |_| {
                container::Style {
                    background: Some(
                        Color::from_rgba(
                            palette.border.r,
                            palette.border.g,
                            palette.border.b,
                            divider_alpha,
                        )
                        .into(),
                    ),
                    ..Default::default()
                }
            }),
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

        // Fixed 4-slot layout preserves widget Tree identity (Scrollable offset + TextInput focus)
        // across banner updates, search reveals, and navigation transitions.
        let banner: Element<'a, Message> = if let Some(error) = &self.action_error {
            container(text(error).size(12).color(palette.danger))
                .padding([6, 18])
                .width(Length::Fill)
                .into()
        } else if let Some((notice, _)) = &self.status_notice {
            container(text(notice).size(12).color(palette.accent))
                .padding([6, 18])
                .width(Length::Fill)
                .into()
        } else {
            container(Space::new().width(0).height(0)).into()
        };

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
                    background: Some(if status == button::Status::Hovered {
                        palette.card_hover.into()
                    } else {
                        Color::TRANSPARENT.into()
                    }),
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

        let search_factor = if !self.search_query.is_empty() {
            1.0
        } else {
            self.search_anim.value(self.animation_now)
        };
        let search_interactive = self.search_anim.target() > 0.0 || !self.search_query.is_empty();
        let search_slot = appearance::reveal_height(
            search_bar.into(),
            search_factor,
            search_factor,
            search_interactive,
        );

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

        let (nav_offset, nav_opacity) = if !self.search_query.is_empty() {
            (iced::Vector::ZERO, 1.0)
        } else {
            self.nav_transition
                .as_ref()
                .map_or((iced::Vector::ZERO, 1.0), |anim| {
                    anim.sample(self.animation_now)
                })
        };

        let main_col = column![
            mouse_area(main_header).on_press(Message::DragWindow),
            banner,
            search_slot,
            appearance::slide_fade(content, nav_offset, nav_opacity),
        ];

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
        let fallback_cache;
        let (total_all, hits): (usize, &[CachedSearchHit]) = match &self.search_cache {
            Some((cached_q, total, cached_hits)) if cached_q == &self.search_query => {
                (*total, cached_hits.as_slice())
            }
            _ => {
                fallback_cache = build_search_cache(&self.config, &self.search_query);
                (fallback_cache.0, fallback_cache.1.as_slice())
            }
        };

        let resolved: Vec<(&ItemConfig, &CachedSearchHit)> = hits
            .iter()
            .filter_map(|hit| {
                resolve_search_location(&self.config, &hit.location).map(|item| (item, hit))
            })
            .collect();
        let hit_count = resolved.len();
        let mut results_box = column![].spacing(0);
        for (idx, (item, hit)) in resolved.into_iter().enumerate() {
            let item_view = self.view_item_row_rounded(
                item,
                &hit.breadcrumb,
                Some(hit.location.clone()),
                row_radius(idx, hit_count),
            );
            results_box = results_box.push(item_view);
        }
        let header = container(
            row![text(if total_all > hit_count {
                format!("Showing {hit_count} of {total_all} results for \"{}\"", self.search_query)
            } else { format!("Found {hit_count} results for \"{}\"", self.search_query) })
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
                background: Some(module_background(palette, false).into()),
                border: Border {
                    color: Color::TRANSPARENT,
                    width: 0.0,
                    radius: 14.0.into(),
                },
                shadow: iced::Shadow {
                    color: Color::from_rgba(0.0, 0.0, 0.0, 0.35),
                    offset: iced::Vector::new(0.0, 2.0),
                    blur_radius: 8.0,
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
        .id("page_content")
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
        .id("page_content")
        .into()
    }

    // ---------------------------------------------------------------------------
    // Section Views
    // ---------------------------------------------------------------------------

    fn view_section<'a>(&'a self, section: &'a SectionConfig) -> Element<'a, Message> {
        let palette = self.theme;

        // Preserve tile widths and outer margins; use one gap on both axes.
        if section.section_type == "grid_section" {
            return responsive(move |size| {
                let slot_width = (size.width - 20.0) / 3.0;
                let side_margin = slot_width * 0.055;
                let gap = (10.0 + side_margin * 2.0) * 0.70;
                let cards = grid(section.items.iter().map(|item| self.view_hero_card(item)))
                    .columns(3)
                    .height(Length::Shrink)
                    .spacing(gap);
                container(cards)
                    .width(Length::Fill)
                    .padding([0.0, side_margin])
                    .into()
            })
            .height(Length::Shrink)
            .into();
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

        let is_controls =
            section.section_type == "controls" || section.properties.title == "Quick Controls";
        let controls_items = if is_controls {
            let visible = self.visible_controls_items(section);
            if visible.is_empty() {
                return column![].into();
            }
            Some(visible)
        } else {
            None
        };

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

        // Slider groups use an explicit section type so titles remain editable.
        if let Some(visible) = controls_items {
            sec_col = sec_col.push(self.view_quick_controls_card(&visible));
            return sec_col.into();
        }

        // Boxed List Container for standard section items (matching modern grouped cards)
        let mut items_box = column![].spacing(0);
        for (idx, item) in section.items.iter().enumerate() {
            items_box = items_box.push(self.view_item_row_rounded(
                item, "", None, row_radius(idx, section.items.len()),
            ));
        }

        let container_card = container(items_box)
            .padding(1)
            .style(move |_| container::Style {
                background: Some(module_background(palette, false).into()),
                border: Border {
                    color: Color::TRANSPARENT,
                    width: 0.0,
                    radius: 14.0.into(),
                },
                shadow: iced::Shadow {
                    color: Color::from_rgba(0.0, 0.0, 0.0, 0.35),
                    offset: iced::Vector::new(0.0, 2.0),
                    blur_radius: 8.0,
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

        let highlight = self.search_highlight_strength(item);
        let service_key = controls::service_key(&item.properties.scope, &item.properties.service);
        let is_enabled = if is_service { self.service_states.get(&service_key).copied().unwrap_or(false) }
            else { self.toggle_states.get(&key).copied().unwrap_or(false) };
        let ready = if is_service {
            self.service_statuses.get(&service_key).is_some_and(ServiceStatus::actionable)
                && !self.busy_controls.contains(&service_key)
        } else { !is_toggle || (self.toggle_states.contains_key(&key)
            && !self.busy_controls.contains(&key) && !self.unavailable_controls.contains(&key)) };

        // Resolve title & button text mappings from worker-populated cache (zero UI-thread I/O)
        let mut display_title = item.properties.title.clone();
        let mut effective_style = item.properties.style.clone();
        if !item.properties.button_text_file.is_empty()
            && let Some(trimmed) = self.file_texts.get(&item.properties.button_text_file)
        {
            if let Some(style) = item
                .properties
                .style_map
                .get(trimmed)
                .or_else(|| item.properties.style_map.get("default"))
            {
                effective_style = style.clone();
            }
            if let Some(mapped) = item.properties.button_text_map.get(trimmed) {
                display_title = mapped.clone();
            } else if let Some(def) = item.properties.button_text_map.get("default") {
                display_title = def.clone();
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
            render_icon(icon_name, if is_service { 22.0 } else { 28.0 }, icon_fg),
            text(display_title)
                .size(12)
                .font(iced::Font {
                    weight: Weight::Bold,
                    ..Default::default()
                })
                .color(text_fg)
                .align_x(Horizontal::Center),
        ]
        .spacing(5)
        .align_x(Horizontal::Center);

        if is_service {
            let detail = self.service_statuses.get(&service_key).map(|s| format!("{} • {}", if s.enabled() { "On" } else { "Off" }, s.startup))
                .unwrap_or_else(|| "Checking…".into());
            card_content = card_content.push(text(detail).size(9)
                .align_x(Horizontal::Center)
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

        let glow_color = if is_destructive && !is_enabled { palette.danger } else { palette.accent };
        let glow = iced::Shadow {
            color: Color::from_rgba(glow_color.r, glow_color.g, glow_color.b, TILE_GLOW_ALPHA),
            offset: iced::Vector::new(0.0, 1.5),
            blur_radius: 6.5,
        };

        // Uniform 62px fixed height guarantees all rows have the exact same size!
        let mut card = button(
            container(card_content)
                .padding([0, 6])
                .width(Length::Fill)
                .height(Length::Fixed(62.0))
                .align_x(Horizontal::Center)
                .align_y(Vertical::Center),
        )
        .padding(0)
        .style(move |_, status| {
            let active_state = matches!(status, button::Status::Hovered | button::Status::Pressed);
            let is_pressed = status == button::Status::Pressed;
            let (bg, b_color) = if is_enabled {
                if active_state {
                    (active_hover_bg, active_hover_bg)
                } else {
                    (active_bg, active_bg)
                }
            } else if is_destructive {
                if active_state {
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
                if active_state {
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
            } else if active_state {
                (
                    palette.card_hover,
                    Color::from_rgba(palette.accent.r, palette.accent.g, palette.accent.b, 0.35),
                )
            } else {
                (card_bg, border_color)
            };

            let idle_bg = if is_enabled { card_bg }
                else { mix(palette.bg, card_bg, TILE_BACKGROUND_MIX) };
            let bg = if is_enabled { bg }
                else { mix(palette.bg, bg, TILE_BACKGROUND_MIX) };
            let (mut bg, b_color) = if active_state {
                let strength = if is_enabled { ENABLED_TILE_HOVER_HIGHLIGHT_MIX }
                    else { TILE_HOVER_HIGHLIGHT_MIX };
                (blend_hover(idle_bg, bg, strength), blend_hover(border_color, b_color, strength))
            } else { (bg, b_color) };
            if is_pressed {
                bg = if is_enabled {
                    mix(bg, Color::BLACK, 0.08)
                } else {
                    mix(bg, palette.accent, 0.04)
                };
            }
            let shadow = if active_state {
                // Add the same hover glow above each state's idle glow.
                let alpha = TILE_GLOW_ALPHA * 1.2 + if is_enabled { TILE_GLOW_ALPHA } else { 0.0 };
                iced::Shadow { color: Color { a: alpha, ..glow.color }, ..glow }
            } else if is_enabled { glow } else { iced::Shadow::default() };

            button::Style {
                background: Some(mix(bg, palette.accent, highlight * 0.14).into()),
                border: Border {
                    color: blend_hover(b_color, palette.accent, highlight * 0.65),
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
        container(card).id(self.control_id(item)).width(Length::FillPortion(1)).into()
    }

    // ---------------------------------------------------------------------------
    // TOML Controls Card (Audio and Display on Home)
    // ---------------------------------------------------------------------------

    fn view_quick_controls_card<'a>(&'a self, visible_items: &[&'a ItemConfig]) -> Element<'a, Message> {
        let palette = self.theme;
        let mut controls_col = column![].spacing(0);
        let item_count = visible_items.len();
        if item_count == 0 {
            return column![].into();
        }

        for (index, &item) in visible_items.iter().enumerate() {
            if !matches!(item.item_type.as_str(), "slider" | "spin") {
                controls_col = controls_col.push(self.view_item_row_rounded(
                    item,
                    "",
                    None,
                    row_radius(index, item_count),
                ));
                continue;
            }

            let key = if !item.properties.key.is_empty() {
                item.properties.key.clone()
            } else {
                item.properties.title.clone()
            };

            let icon_name = item.properties.icon.as_str();

            let min = item.properties.min.unwrap_or(0.0) as f32;
            let max = item.properties.max.unwrap_or(100.0) as f32;
            let step = item.properties.step.unwrap_or(1.0) as f32;
            let val = self.slider_values.get(&key).copied()
                .unwrap_or(item.properties.default.unwrap_or(min as f64) as f32);

            let on_change = item.on_change.clone();

            let is_row_hovered = self.hovered_item.as_deref() == Some(&key);
            let tint = slider_tint(palette, &key);

            let (icon_badge_bg, icon_badge_border, icon_color) = if is_row_hovered {
                (
                    mix(mix(palette.card_bg, tint, 0.08), mix(palette.card_hover, tint, 0.12), ICON_HOVER_HIGHLIGHT_MIX),
                    Color { a: ICON_HOVER_HIGHLIGHT_MIX, ..mix(palette.card_bg, tint, 0.18) },
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

            let debounce = item.properties.debounce;
            let s = render_modern_slider(
                palette,
                tint,
                min,
                max,
                val,
                step,
                key.clone(),
                on_change,
                debounce,
                None,
            );

            // Monospace numeric value on right
            let val_label = text(format!("{:.0}", val.round()))
                .size(13)
                .font(iced::Font::MONOSPACE)
                .color(mix(palette.card_bg, palette.fg, if is_row_hovered { 0.60 + (0.72 - 0.60) * HOVER_HIGHLIGHT_MIX } else { 0.60 }))
                .width(28)
                .align_x(Horizontal::Right);

            let control_name = text(&item.properties.title)
                .size(12)
                .color(mix(palette.card_bg, palette.fg, 0.86));
            let identity = row![icon_badge, control_name]
                .spacing(12)
                .align_y(Vertical::Center)
                .width(Length::FillPortion(2));
            let adjustment = row![
                s,
                val_label,
            ]
            .spacing(12)
            .align_y(Vertical::Center)
            .width(Length::FillPortion(3));
            let ctrl_row = row![identity, adjustment]
                .spacing(16)
                .align_y(Vertical::Center);

            let key_enter = key.clone();
            let key_exit = key.clone();
            let highlight = self.search_highlight_strength(item);
            let row_container = container(ctrl_row)
                // Move the group's inset/gaps into each row's hover bounds.
                .id(self.control_id(item))
                .padding(Padding {
                    top: if index == 0 { 13.0 } else { 7.0 },
                    right: 15.0,
                    bottom: if index + 1 == item_count { 13.0 } else { 7.0 },
                    left: 15.0,
                })
                .width(Length::Fill)
                .style(move |_| search_highlight_style(container::Style {
                    background: Some(if is_row_hovered {
                        module_background(palette, true)
                    } else { Color::TRANSPARENT }.into()),
                    border: Border { radius: row_radius(index, item_count), ..Default::default() },
                    ..Default::default()
                }, palette, highlight));
            let row_area = mouse_area(row_container)
                .on_enter(Message::ItemEntered(key_enter))
                .on_exit(Message::ItemExited(key_exit));

            controls_col = controls_col.push(row_area);
        }

        container(controls_col)
            .padding(1)
            .width(Length::Fill)
            .style(move |_| container::Style {
                background: Some(module_background(palette, false).into()),
                border: Border {
                    color: Color::TRANSPARENT,
                    width: 0.0,
                    radius: 14.0.into(),
                },
                shadow: iced::Shadow {
                    color: Color::from_rgba(0.0, 0.0, 0.0, 0.35),
                    offset: iced::Vector::new(0.0, 2.0),
                    blur_radius: 8.0,
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
        badge_tag: &str,
        _from_page: Option<SearchLocation>,
    ) -> Element<'a, Message> {
        self.view_item_row_rounded(item, badge_tag, _from_page, iced::border::Radius::default())
    }

    fn view_item_row_rounded<'a>(
        &'a self,
        item: &'a ItemConfig,
        badge_tag: &str,
        from_page: Option<SearchLocation>,
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
            subtle_hover(mix(palette.card_bg, palette.fg, 0.46), mix(palette.card_hover, palette.fg, 0.55))
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
                container(text(badge_tag.to_string()).size(10).color(palette.accent))
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
                mix(mix(palette.card_bg, palette.accent, 0.08), mix(palette.card_hover, palette.accent, 0.12), ICON_HOVER_HIGHLIGHT_MIX),
                Color { a: ICON_HOVER_HIGHLIGHT_MIX, ..mix(palette.card_bg, palette.accent, 0.18) },
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

            let highlight = self.search_highlight_strength(item);
            let row_container = container(row_content)
                .id(self.control_id(item))
                .padding([8, 15])
                .width(Length::Fill)
                .style(move |_| search_highlight_style(container::Style {
                    background: Some(if is_row_hovered {
                        module_background(palette, true)
                    } else { Color::TRANSPARENT }.into()),
                    border: Border { radius, ..Default::default() },
                    ..Default::default()
                }, palette, highlight));

            let key_enter = key.clone();
            let key_exit = key.clone();
            return mouse_area(row_container)
                .on_enter(Message::ItemEntered(key_enter))
                .on_exit(Message::ItemExited(key_exit))
                .into();
        }

        // Right interactive widget
        let right_widget: Option<Element<'a, Message>> = match if from_page.is_some() { "navigation" } else { item.item_type.as_str() } {
            "toggle" => {
                let is_active = self.toggle_states.get(&key).copied().unwrap_or(false);
                let toggle_pair = item.on_toggle.clone();
                let key_clone = key.clone();
                let mut control = toggler(is_active)
                    .size(22)
                    .spacing(0)
                    .width(44)
                    .style(move |_, status| toggle_style(palette, status));
                if self.toggle_states.contains_key(&key) && !self.busy_controls.contains(&key) && !self.unavailable_controls.contains(&key) {
                    control = control.on_toggle(move |val| Message::ToggleItem {
                        key: key_clone.clone(), is_enabled: val, on_toggle: toggle_pair.clone(),
                    });
                }
                Some(control.into())
            }

            "slider" | "spin" => {
                let min = item.properties.min.unwrap_or(0.0) as f32;
                let max = item.properties.max.unwrap_or(100.0) as f32;
                let step = item.properties.step.unwrap_or(1.0) as f32;
                let val = self.slider_values.get(&key).copied().unwrap_or(min);
                let tint = slider_tint(palette, &key);

                let s = render_modern_slider(
                    palette,
                    tint,
                    min,
                    max,
                    val,
                    step,
                    key.clone(),
                    item.on_change.clone(),
                    item.properties.debounce,
                    Some(136.0),
                );

                let val_label = text(format!("{:.0}", val.round()))
                    .size(12)
                    .font(iced::Font::MONOSPACE)
                    .color(palette.fg_muted)
                    .width(26)
                    .align_x(Horizontal::Right);

                Some(row![s, val_label]
                    .spacing(8)
                    .align_y(Vertical::Center)
                    .into())
            }

            "entry" | "secret" => {
                let value = self.entry_values.get(&key).map(String::as_str).unwrap_or("");
                let change_key = key.clone();
                let action = item.on_action.as_ref().or(item.on_press.as_ref());
                let submit = (action.is_some() || (item.properties.persistence == "app" && !item.properties.key.is_empty()))
                    .then(|| Message::SubmitEntry { key: key.clone(), action: action.cloned() });
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
                Some(row![input, apply].spacing(6).align_y(Vertical::Center).into())
            }

            "selection" => {
                let act_map = match &item.on_change {
                    Some(ChangeAction::Map(map)) => map.clone(),
                    _ => HashMap::new(),
                };
                let direct_act = match &item.on_change {
                    Some(ChangeAction::Direct(d)) => Some(d.clone()),
                    _ => None,
                };

                let key_clone = key.clone();
                let options = self.dynamic_options.get(&key).unwrap_or(&item.properties.options).clone();
                let current = self.selected_options.get(&key).filter(|value| options.contains(value)).cloned();

                if options.is_empty() {
                    None
                } else {
                    let dropdown = pick_list(
                        options,
                        current,
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
                    .placeholder("Unknown")
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

                    Some(dropdown.into())
                }
            }

            "navigation" => {
                let icon_color = if is_row_hovered {
                    palette.fg
                } else {
                    Color::from_rgba(palette.fg.r, palette.fg.g, palette.fg.b, 0.45)
                };
                Some(container(render_icon("chevron_right", 16.0, icon_color))
                    .padding([4, 6])
                    .align_y(Vertical::Center)
                    .into())
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
                Some(container(render_icon(chevron_icon, 16.0, icon_color))
                    .padding([4, 6])
                    .align_y(Vertical::Center)
                    .into())
            }

            "service" => {
                let unit = item.properties.service.clone();
                let scope = if item.properties.scope == "user" { "user" } else { "system" }.to_string();
                let service_key = controls::service_key(&scope, &unit);
                let active = self.service_states.get(&service_key).copied().unwrap_or(false);
                let ready = self.service_statuses.get(&service_key).is_some_and(ServiceStatus::actionable)
                    && !self.busy_controls.contains(&service_key);
                let mut control = toggler(active)
                    .size(22)
                    .spacing(0)
                    .width(44)
                    .style(move |_, status| toggle_style(palette, status));
                if ready { control = control.on_toggle(move |_| Message::ToggleService { unit: unit.clone(), scope: scope.clone() }); }
                Some(control.into())
            }

            "label" => {
                let label_val = self
                    .live_labels
                    .get(&key)
                    .cloned()
                    .unwrap_or_else(|| "…".into());

                Some(text(label_val)
                    .size(12)
                    .color(palette.fg_muted)
                    .align_x(Horizontal::Right)
                    .into())
            }

            // Explicit action buttons replace the default button, as in GTK.
            _ if !item.properties.buttons.is_empty() => None,

            _ => {
                let opt_action = item.on_press.as_ref().or(item.on_action.as_ref());
                if let Some(action) = opt_action {
                    let act = action.clone();

                    let mut dyn_btn_text = String::new();
                    let mut dyn_style = String::new();

                    if !item.properties.button_text_file.is_empty()
                        && let Some(trimmed) = self.file_texts.get(&item.properties.button_text_file)
                    {
                        if let Some(mapped) = item.properties.button_text_map.get(trimmed) {
                            dyn_btn_text = mapped.clone();
                        } else if let Some(def) = item.properties.button_text_map.get("default") {
                            dyn_btn_text = def.clone();
                        }
                        if let Some(s_mapped) = item
                            .properties
                            .style_map
                            .get(trimmed)
                            .or_else(|| item.properties.style_map.get("default"))
                        {
                            dyn_style = s_mapped.clone();
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

                    Some(button(text(btn_label).size(12).font(iced::Font {
                        weight: Weight::Semibold, ..iced::Font::with_name("Atkinson Hyperlegible")
                    }))
                    .on_press(Message::ExecuteAction(act))
                    .padding([6, 14])
                    .style(move |_, status| action_button_style(palette, &effective_style, false, 0, 1, status))
                    .into())
                } else {
                    None
                }
            }
        };

        // Missing widgets must not leave a trailing gap after paired actions.
        let right_container: Option<Element<'a, Message>> = if from_page.is_none() && !item.properties.buttons.is_empty() {
            let mut controls = row![buttons_row].spacing(8).align_y(Vertical::Center);
            if let Some(widget) = right_widget { controls = controls.push(widget); }
            Some(controls.into())
        } else {
            right_widget
        };

        let mut row_content = row![left_part]
            .spacing(12)
            .align_y(Vertical::Center)
            .width(Length::Fill);
        if let Some(controls) = right_container {
            row_content = row_content.push(container(controls).width(Length::Shrink).align_x(Horizontal::Right));
        }

        // Paired buttons own their actions; the row only invokes an explicit row action.
        let primary_action = item.on_press.as_ref().or(item.on_action.as_ref()).cloned();

        let highlight = self.search_highlight_strength(item);
        let row_container = container(row_content)
            .id(self.control_id(item))
            .padding([8, 15])
            .width(Length::Fill)
            .style(move |_| search_highlight_style(container::Style {
                background: Some(
                    if is_row_hovered {
                        module_background(palette, true)
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
            }, palette, highlight));

        let key_enter = key.clone();
        let key_exit = key.clone();
        let mut area = mouse_area(row_container)
            .on_enter(Message::ItemEntered(key_enter))
            .on_exit(Message::ItemExited(key_exit));

        if let Some(location) = &from_page {
            area = area.on_press(Message::OpenSearchResult(location.clone())).interaction(mouse::Interaction::Pointer);
        } else if item.item_type == "navigation" {
            let sub_sections = item.layout.clone();
            let sub_title = title.clone();
            let occurrence = navigation_occurrence(self.current_sections(), item);
            area = area
                .on_press(Message::PushSubPage {
                    title: sub_title,
                    occurrence,
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
        let exp_factor = self
            .expander_anims
            .get(&expander_key)
            .map(|anim| anim.value(self.animation_now))
            .unwrap_or(if is_expanded { 1.0 } else { 0.0 });

        if from_page.is_none()
            && item.item_type == "expander"
            && (is_expanded || exp_factor > 0.0)
            && !item.items.is_empty()
        {
            let mut sub_col = column![].spacing(0);
            for sub_item in &item.items {
                sub_col = sub_col.push(
                    container(self.view_item_row(sub_item, "", None)).padding(Padding {
                        top: 0.0,
                        right: 0.0,
                        bottom: 0.0,
                        left: 16.0,
                    }),
                );
            }
            let revealed =
                appearance::reveal_height(sub_col.into(), exp_factor, exp_factor, is_expanded);
            column![area, revealed].into()
        } else {
            area.into()
        }
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn search_highlight_style(mut style: container::Style, palette: AppTheme, strength: f32) -> container::Style {
    if strength > 0.0 {
        let base = match style.background {
            Some(iced::Background::Color(color)) if color.a > 0.0 => color,
            _ => module_background(palette, false),
        };
        style.background = Some(mix(base, palette.accent, 0.14 * strength).into());
        style.border.color = Color { a: 0.60 * strength, ..palette.accent };
        style.border.width = 1.0;
    }
    style
}

struct LocateSearchTarget {
    target: iced_core::widget::Id,
    viewport: Option<iced::Rectangle>,
    target_bounds: Option<iced::Rectangle>,
}

impl iced_core::widget::Operation<Option<f32>> for LocateSearchTarget {
    fn traverse(&mut self, operate: &mut dyn FnMut(&mut dyn iced_core::widget::Operation<Option<f32>>)) {
        operate(self);
    }

    fn container(&mut self, id: Option<&iced_core::widget::Id>, bounds: iced::Rectangle) {
        if id == Some(&self.target) { self.target_bounds = Some(bounds); }
    }

    fn scrollable(&mut self, id: Option<&iced_core::widget::Id>, bounds: iced::Rectangle,
        _content: iced::Rectangle, _translation: iced::Vector,
        _state: &mut dyn iced_core::widget::operation::Scrollable) {
        if id == Some(&iced_core::widget::Id::new("page_content")) { self.viewport = Some(bounds); }
    }

    fn finish(&self) -> iced_core::widget::operation::Outcome<Option<f32>> {
        let offset = self.viewport.zip(self.target_bounds).map(|(viewport, target)| {
            // Center normal rows; align oversized rows near the top. Iced clamps the bottom edge.
            let inset = ((viewport.height - target.height) / 2.0).max(12.0);
            (target.y - viewport.y - inset).max(0.0)
        });
        iced_core::widget::operation::Outcome::Some(offset)
    }
}

fn item_at<'a>(sections: &'a [SectionConfig], path: &[usize]) -> Option<&'a ItemConfig> {
    let (&section, path) = path.split_first()?;
    let (&index, path) = path.split_first()?;
    let mut item = sections.get(section)?.items.get(index)?;
    for &index in path { item = item.items.get(index)?; }
    Some(item)
}

fn navigation_at<'a>(sections: &'a [SectionConfig], path: &[usize]) -> Option<&'a ItemConfig> {
    item_at(sections, path).filter(|item| item.item_type == "navigation")
}

fn navigation_occurrence(sections: &[SectionConfig], target: &ItemConfig) -> usize {
    fn visit(items: &[ItemConfig], target: &ItemConfig, seen: &mut usize) -> bool {
        for item in items {
            if std::ptr::eq(item, target) {
                return true;
            }
            if item.item_type == "navigation" && item.properties.title == target.properties.title {
                *seen += 1;
            }
            if visit(&item.items, target, seen) {
                return true;
            }
        }
        false
    }
    let mut seen = 0;
    for section in sections {
        if visit(&section.items, target, &mut seen) {
            return seen;
        }
    }
    0
}

fn find_navigation_subpage<'a>(
    sections: &'a [SectionConfig],
    title: &str,
    occurrence: usize,
) -> Option<&'a ItemConfig> {
    fn visit<'a>(
        items: &'a [ItemConfig],
        title: &str,
        seen: &mut usize,
        occurrence: usize,
    ) -> Option<&'a ItemConfig> {
        for item in items {
            if item.item_type == "navigation" && item.properties.title == title {
                if *seen == occurrence {
                    return Some(item);
                }
                *seen += 1;
            }
            if let Some(found) = visit(&item.items, title, seen, occurrence) {
                return Some(found);
            }
        }
        None
    }
    let mut seen = 0;
    for section in sections {
        if let Some(found) = visit(&section.items, title, &mut seen, occurrence) {
            return Some(found);
        }
    }
    None
}

fn collect_slider_bounds(config: &AppConfig) -> HashMap<String, (f32, f32)> {
    fn visit(items: &[ItemConfig], bounds: &mut HashMap<String, (f32, f32)>) {
        for item in items {
            if matches!(item.item_type.as_str(), "slider" | "spin") {
                let min = item.properties.min.unwrap_or(0.0) as f32;
                let max = item.properties.max.unwrap_or(100.0) as f32;
                bounds.insert(controls::item_key(item), (min, max));
            }
            visit(&item.items, bounds);
            for section in &item.layout {
                visit(&section.items, bounds);
            }
        }
    }
    let mut bounds = HashMap::new();
    for page in &config.pages {
        for section in &page.layout {
            visit(&section.items, &mut bounds);
        }
    }
    bounds
}

fn format_display_path(path: &std::path::Path) -> String {
    if let Some(home) = std::env::var_os("HOME").filter(|h| !h.is_empty()) {
        let home_path = PathBuf::from(home);
        if home_path.is_absolute()
            && let Ok(rel) = path.strip_prefix(&home_path)
        {
            return format!("~/{}", rel.display());
        }
    }
    path.display().to_string()
}

fn classify_slider_action(key: &str, on_change: Option<&ChangeAction>) -> SliderHint {
    match on_change {
        None => match key {
            "Volume" => SliderHint::NativeVolume,
            "Microphone" => SliderHint::NativeMicrophone,
            "Brightness" => SliderHint::NativeBrightness,
            "Night Light" => SliderHint::NativeSunset,
            _ => SliderHint::Generic,
        },
        Some(ChangeAction::Direct(ActionConfig::Exec {
            command,
            argv,
            terminal: false,
            requires_root: false,
            ..
        })) if argv.is_empty() => match (key, command.trim()) {
            ("Brightness", "brightnessctl set {value}%") => SliderHint::DefaultBrightnessAction,
            ("Volume", "wpctl set-volume @DEFAULT_AUDIO_SINK@ {value}%") => {
                SliderHint::DefaultVolumeAction
            }
            _ => SliderHint::Generic,
        },
        _ => SliderHint::Generic,
    }
}

fn resolve_search_location<'a>(
    config: &'a AppConfig,
    location: &SearchLocation,
) -> Option<&'a ItemConfig> {
    let mut sections = config.pages.get(location.page)?.layout.as_slice();
    for nav_path in &location.navigation {
        sections = navigation_at(sections, nav_path)?.layout.as_slice();
    }
    item_at(sections, &location.path)
}

fn build_search_cache(config: &AppConfig, raw_query: &str) -> (usize, Vec<CachedSearchHit>) {
    fn visit(
        items: &[ItemConfig],
        breadcrumb: &str,
        location: &SearchLocation,
        path: &[usize],
        query: &str,
        hits: &mut Vec<CachedSearchHit>,
    ) {
        for (index, item) in items.iter().enumerate() {
            let mut item_path = path.to_vec();
            item_path.push(index);
            let mut target = location.clone();
            target.path = item_path.clone();
            let score = search_score(item, query);
            if score > 0 {
                hits.push(CachedSearchHit {
                    breadcrumb: breadcrumb.into(),
                    location: target.clone(),
                    score,
                });
            }
            let breadcrumb = format!("{breadcrumb} › {}", item.properties.title);
            if item.item_type == "navigation" {
                target.navigation.push(item_path.clone());
                for (section, layout) in item.layout.iter().enumerate() {
                    visit(&layout.items, &breadcrumb, &target, &[section], query, hits);
                }
            }
            if item.item_type == "expander" {
                target.expanders.push(format!("expander:{}", controls::item_key(item)));
            }
            visit(&item.items, &breadcrumb, &target, &item_path, query, hits);
        }
    }
    let query = raw_query.to_lowercase();
    let mut hits = Vec::new();
    for (page, config) in config.pages.iter().enumerate() {
        let location = SearchLocation {
            page,
            navigation: vec![],
            expanders: vec![],
            path: vec![],
        };
        for (section, layout) in config.layout.iter().enumerate() {
            visit(&layout.items, &config.title, &location, &[section], &query, &mut hits);
        }
    }
    hits.sort_by_key(|hit| std::cmp::Reverse(hit.score));
    let total = hits.len();
    hits.truncate(50);
    (total, hits)
}

fn search_score(item: &ItemConfig, query: &str) -> u32 {
    let query = query.split_whitespace().collect::<Vec<_>>().join(" ").to_lowercase();
    if query.is_empty() { return 0; }
    let title = item.properties.title.to_lowercase();
    if title == query { return 1000; }
    if title.starts_with(&query) { return 900; }
    if title.contains(&query) { return 800; }
    let terms: Vec<_> = query.split_whitespace().collect();
    if terms.len() > 1 {
        let scores: Vec<_> = terms.iter().map(|term| search_score(item, term)).collect();
        return if scores.contains(&0) { 0 } else { (scores.iter().sum::<u32>() / scores.len() as u32).saturating_sub(25) };
    }
    let clean = |s: &str| s.chars().filter(|c| c.is_alphanumeric()).collect::<String>();
    let q = clean(&query);
    let t = clean(&title);
    if !q.is_empty() {
        if t == q { return 950; }
        if t.starts_with(&q) { return 850; }
        if t.contains(&q) { return 750; }
    }
    let prefix = |s: &str| s.split(|c: char| !c.is_alphanumeric()).any(|word| word.starts_with(&query));
    let fuzzy = |s: &str| {
        if query.chars().count() < 3 { return false; }
        let mut chars = s.chars();
        query.chars().all(|c| chars.any(|next| next == c))
    };
    if prefix(&title) { return 500; }
    if fuzzy(&title) { return 300; }
    if item.properties.key.to_lowercase().contains(&query) || item.properties.service.to_lowercase().contains(&query) { return 250; }
    let description = item.properties.description.to_lowercase();
    if description.contains(&query) { return 200; }
    if !q.is_empty() && clean(&description).contains(&q) { return 175; }
    if prefix(&description) { return 150; }
    if fuzzy(&description) { return 100; }
    if item.properties.options.iter().any(|option| option.to_lowercase().contains(&query)) { return 75; }
    0
}

fn module_background(palette: AppTheme, hovered: bool) -> Color {
    let base = mix(palette.bg, palette.card_bg, GROUP_BACKGROUND_MIX);
    if hovered { subtle_hover(base, palette.card_hover) } else { base }
}

fn subtle_hover(base: Color, highlighted: Color) -> Color {
    blend_hover(base, highlighted, HOVER_HIGHLIGHT_MIX)
}

fn blend_hover(base: Color, highlighted: Color, strength: f32) -> Color {
    Color {
        a: base.a + (highlighted.a - base.a) * strength,
        ..mix(base, highlighted, strength)
    }
}

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

#[allow(clippy::too_many_arguments)]
fn render_modern_slider<'a>(
    palette: AppTheme,
    tint: Color,
    min: f32,
    max: f32,
    val: f32,
    step: f32,
    key: String,
    on_change: Option<ChangeAction>,
    debounce: bool,
    fixed_width: Option<f32>,
) -> Element<'a, Message> {
    let clamped = val.clamp(min, max);
    let span = (max - min).max(f32::EPSILON);
    let ratio = ((clamped - min) / span).clamp(0.0, 1.0);
    let rail_start = mix(saturate(tint, 1.40), palette.bg, 0.40);
    let rail_mid = saturate(tint, 1.25);
    let rail_end = mix(tint, Color::WHITE, 0.42);
    let knob_color = mix(saturate(tint, 1.30), Color::WHITE, 0.08);

    let track_bg = container(Space::new())
        .width(Length::Fill)
        .height(Length::Fixed(16.0))
        .style(move |_| {
            let recess = iced_core::gradient::Linear::new(iced_core::Radians::PI)
                .add_stop(
                    0.0,
                    Color {
                        a: 0.72,
                        ..mix(palette.bg, Color::BLACK, 0.62)
                    },
                )
                .add_stop(
                    1.0,
                    Color {
                        a: 0.52,
                        ..mix(palette.bg, Color::BLACK, 0.35)
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
    if clamped > min {
        let fill_portion = if ratio >= 0.98 {
            1000_u16
        } else {
            (59.0 + ratio * 882.0).round().clamp(1.0, 1000.0) as u16
        };
        let empty_portion = 1000_u16.saturating_sub(fill_portion);
        let right_radius = if ratio >= 0.98 { 8.0 } else { 0.0 };
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
                            top_right: right_radius,
                            bottom_right: right_radius,
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
        .style(move |_| {
            container::Style {
                background: Some(knob_color.into()),
                border: Border {
                    radius: 10.0.into(),
                    color: Color {
                        a: 0.32,
                        ..mix(knob_color, Color::WHITE, 0.45)
                    },
                    width: 1.0,
                },
                shadow: iced::Shadow {
                    color: Color::from_rgba(0.0, 0.0, 0.0, 0.75),
                    offset: iced::Vector::new(-3.5, 0.5),
                    blur_radius: 5.5,
                },
                ..Default::default()
            }
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

    let key_c1 = key.clone();
    let key_c2 = key;
    let on_change_c1 = on_change.clone();
    let on_change_c2 = on_change;
    let s = slider(
        min..=max,
        clamped,
        move |v| Message::SliderChanged {
            key: key_c1.clone(),
            value: v,
            on_change: on_change_c1.clone(),
            debounce,
        },
    )
    .step(step)
    .height(24)
    .width(Length::Fill)
    .on_release(Message::SliderReleased {
        key: key_c2,
        value: clamped,
        on_change: on_change_c2,
        debounce,
    })
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

    if let Some(w) = fixed_width {
        container(slider_stack).width(Length::Fixed(w)).into()
    } else {
        container(slider_stack)
            .width(Length::Fill)
            .padding(iced::Padding {
                top: 0.0,
                right: 14.0,
                bottom: 0.0,
                left: 14.0,
            })
            .into()
    }
}

fn toggle_style(palette: AppTheme, status: toggler::Status) -> toggler::Style {
    let (enabled, hovered) = match status {
        toggler::Status::Active { is_toggled } | toggler::Status::Disabled { is_toggled } =>
            (is_toggled, false),
        toggler::Status::Hovered { is_toggled } => (is_toggled, true),
    };
    let (bg, bg_border_color, bg_border_width, fg, fg_border_color, fg_border_width) = if enabled {
        (
            mix(palette.bg, saturate(palette.accent, 1.25), if hovered { 0.35 } else { 0.28 }).into(),
            Color { a: if hovered { 0.60 } else { 0.45 }, ..saturate(palette.accent, 1.25) },
            1.5,
            saturate(palette.accent, 1.35).into(),
            Color { a: 0.25, ..Color::WHITE },
            1.0,
        )
    } else {
        (
            Color { a: 0.08, ..mix(palette.bg, palette.fg, 0.15) }.into(),
            Color { a: 0.09, ..palette.fg },
            1.0,
            Color { a: 0.32, ..palette.fg }.into(),
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

    // Pure in-memory toggles (no state_command and no backing setting file) start immediately;
    // file-backed and command-backed toggles populate asynchronously via controls::query().
    if matches!(item.item_type.as_str(), "toggle" | "toggle_card")
        && item.properties.state_command.is_empty()
        && item.properties.key.is_empty()
    {
        toggles.entry(key.clone()).or_insert(item.properties.key_inverse);
    }

    if !item.properties.service.is_empty() {
        services
            .entry(controls::service_key(&item.properties.scope, &item.properties.service))
            .or_insert(false);
    }

    if item.item_type == "selection" && !item.properties.options.is_empty()
        && item.properties.key.is_empty() && item.properties.value_command.is_empty()
        && item.properties.options_command.is_empty()
    {
        options.entry(key.clone()).or_insert_with(|| item.properties.options[0].clone());
    }

    for sub in &item.items {
        collect_item_defaults(sub, sliders, toggles, services, options);
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

fn make_slim_scrollable<'a>(
    content: impl Into<Element<'a, Message>>,
    padding: Padding,
    accent: Color,
) -> iced_widget::Scrollable<'a, Message, iced_core::Theme, crate::renderer::Renderer> {
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
            if path.is_dir() {
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

fn substitute_string(text: &mut String, tokens: &[(String, &str)]) {
    if !text.contains('{') {
        return;
    }
    let raw = std::mem::take(text);
    let mut rest = raw.as_str();
    while !rest.is_empty() {
        let replacement = tokens
            .iter()
            .find_map(|(token, value)| rest.starts_with(token).then_some((token.len(), *value)));
        if let Some((length, value)) = replacement {
            text.push_str(value);
            rest = &rest[length..];
        } else {
            let ch = rest.chars().next().unwrap();
            text.push(ch);
            rest = &rest[ch.len_utf8()..];
        }
    }
}

fn substitute_action(action: &mut ActionConfig, tokens: &[(String, &str)]) {
    match action {
        ActionConfig::Reload => (),
        ActionConfig::Exec {
            command,
            argv,
            expanded_argv: _,
            terminal: _,
            requires_root: _,
            mode,
            timeout: _,
        } => {
            substitute_string(command, tokens);
            for arg in argv {
                substitute_string(arg, tokens);
            }
            substitute_string(mode, tokens);
        }
        ActionConfig::Argv {
            argv,
            expanded_argv: _,
            terminal: _,
            requires_root: _,
            mode,
            timeout: _,
        } => {
            for arg in argv {
                substitute_string(arg, tokens);
            }
            substitute_string(mode, tokens);
        }
        ActionConfig::Redirect { page } => substitute_string(page, tokens),
    }
}

fn substitute_properties(props: &mut ItemProperties, tokens: &[(String, &str)]) {
    let ItemProperties {
        title,
        description,
        icon,
        message,
        key,
        key_inverse: _,
        save_as_int: _,
        style,
        button_text,
        button_text_file,
        badge_file,
        button_text_map,
        style_map,
        options_map,
        options,
        min: _,
        max: _,
        step: _,
        default: _,
        state_command,
        value_command,
        options_command,
        interval: _,
        persistence,
        path,
        glob,
        recursive: _,
        sort,
        service,
        scope,
        toast,
        debounce: _,
        compact: _,
        center_title: _,
        show_label: _,
        buttons,
    } = props;

    for field in [
        title,
        description,
        icon,
        message,
        key,
        style,
        button_text,
        button_text_file,
        badge_file,
        state_command,
        value_command,
        options_command,
        persistence,
        path,
        glob,
        sort,
        service,
        scope,
        toast,
    ] {
        substitute_string(field, tokens);
    }
    for map in [button_text_map, style_map, options_map] {
        for value in map.values_mut() {
            substitute_string(value, tokens);
        }
    }
    for opt in options {
        substitute_string(opt, tokens);
    }
    for ButtonProperty {
        title,
        icon,
        style,
        on_press,
    } in buttons
    {
        substitute_string(title, tokens);
        substitute_string(icon, tokens);
        substitute_string(style, tokens);
        if let Some(action) = on_press {
            substitute_action(action, tokens);
        }
    }
}

fn substitute_section(section: &mut SectionConfig, tokens: &[(String, &str)]) {
    let SectionConfig {
        section_type,
        properties,
        items,
    } = section;
    substitute_string(section_type, tokens);
    substitute_properties(properties, tokens);
    for child in items {
        substitute_item_with_tokens(child, tokens);
    }
}

fn substitute_item_with_tokens(item: &mut ItemConfig, tokens: &[(String, &str)]) {
    let ItemConfig {
        item_type,
        properties,
        on_press,
        on_toggle,
        on_change,
        on_action,
        value,
        items,
        layout,
        item_template,
    } = item;

    substitute_string(item_type, tokens);
    substitute_properties(properties, tokens);
    if let Some(action) = on_press {
        substitute_action(action, tokens);
    }
    if let Some(ToggleActionPair { enabled, disabled }) = on_toggle {
        substitute_action(enabled, tokens);
        substitute_action(disabled, tokens);
    }
    if let Some(change) = on_change {
        match change {
            ChangeAction::Direct(action) => substitute_action(action, tokens),
            ChangeAction::Map(map) => {
                for action in map.values_mut() {
                    substitute_action(action, tokens);
                }
            }
        }
    }
    if let Some(action) = on_action {
        substitute_action(action, tokens);
    }
    if let Some(val) = value {
        match val {
            ValueConfig::Exec { command } => substitute_string(command, tokens),
            ValueConfig::File { path } => substitute_string(path, tokens),
            ValueConfig::System { key } => substitute_string(key, tokens),
            ValueConfig::Static { text } => substitute_string(text, tokens),
        }
    }
    for child in items {
        substitute_item_with_tokens(child, tokens);
    }
    for section in layout {
        substitute_section(section, tokens);
    }
    if let Some(template) = item_template {
        substitute_item_with_tokens(template, tokens);
    }
}

fn substitute_item_vars(item: &mut ItemConfig, vars: &HashMap<String, String>) {
    // Exhaustive typed walk across all config fields; inserted names are never re-expanded.
    let tokens: Vec<_> = vars
        .iter()
        .map(|(key, value)| (format!("{{{key}}}"), value.as_str()))
        .collect();
    substitute_item_with_tokens(item, &tokens);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn complete(app: &mut CenterApp, task: Task<Message>) {
        use iced_futures::futures::{executor::block_on, StreamExt};
        let Some(stream) = iced_runtime::task::into_stream(task) else { return; };
        for action in block_on(stream.collect::<Vec<_>>()) {
            if let iced_runtime::Action::Output(message) = action {
                let task = app.update(message);
                complete(app, task);
            }
        }
    }

    #[test]
    fn selection_workers_rollback_and_resist_stale_polls() {
        let config: AppConfig = toml::from_str("[[pages]]\nid='fixture'\ntitle='Fixture'\n[[pages.layout]]\ntype='section'\n[[pages.layout.items]]\ntype='selection'\n[pages.layout.items.properties]\ntitle='Choice'\noptions=['Old','New']\n[pages.layout.items.on_change.New]\ntype='exec'\ncommand='printf broken >&2; exit 7'").unwrap();
        let (mut app, _initial) = CenterApp::new(config, None, None);
        app.controls_loading = None;
        app.selected_options.insert("Choice".into(), "Old".into());
        let action = match app.config.pages[0].layout[0].items[0].on_change.as_ref().unwrap() {
            ChangeAction::Map(map) => map["New"].clone(), _ => unreachable!(),
        };
        let task = app.update(Message::SelectOption { key: "Choice".into(), option: "New".into(), action: Some(action) });
        assert_eq!(app.selected_options["Choice"], "New");
        assert!(app.busy_controls.contains("Choice"));
        let duplicate = app.update(Message::SelectOption { key: "Choice".into(), option: "Old".into(), action: None });
        assert_eq!(duplicate.units(), 0);
        let _ = app.update(Message::ControlsLoaded { slider_revision: app.slider_revision, generation: 0,
            snapshot: Snapshot { selections: vec![("Choice".into(), Some("Old".into()))], ..Default::default() } });
        assert_eq!(app.selected_options["Choice"], "New");
        complete(&mut app, task);
        assert_eq!(app.selected_options["Choice"], "Old");
        assert!(app.action_error.as_ref().unwrap().contains("broken"));
        assert!(!app.busy_controls.contains("Choice"));
    }

    #[test]
    fn close_waits_for_setting_workers_and_flushes_fractional_debounce() {
        let path = std::env::temp_dir().join(format!("dusky-fractional-{}", std::process::id()));
        let config: AppConfig = toml::from_str("[[pages]]\nid='fixture'\ntitle='Fixture'\n[[pages.layout]]\ntype='section'\n[[pages.layout.items]]\ntype='slider'\n[pages.layout.items.properties]\ntitle='Fraction'\nmin=0.0\nmax=1.0\nstep=0.01").unwrap();
        let (mut app, _initial) = CenterApp::new(config, None, None);
        let command = format!("printf '%s\\n' {{value}} >> '{}'", path.display());
        let action = Some(ChangeAction::Direct(ActionConfig::Exec { command, argv: vec![], expanded_argv: false, terminal: false,
            requires_root: false, mode: String::new(), timeout: None }));
        for i in 0..1000 {
            let _ = app.update(Message::SliderChanged { key: "Fraction".into(), value: i as f32 / 1000.0,
                on_change: action.clone(), debounce: true });
        }
        assert!(app.applying_sliders.is_empty());
        assert_eq!(app.deferred_sliders.len(), 1);
        app.busy_controls.insert("user:fixture.service".into());
        let close = app.update(Message::CloseApp);
        assert!(app.closing);
        assert!(app.deferred_sliders.is_empty());
        complete(&mut app, close);
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "0.999\n");
        std::fs::remove_file(path).unwrap();
        assert!(!app.writes_finished());
        let exit = app.update(Message::ServiceFinished { key: "user:fixture.service".into(), previous: false, result: Ok(()) });
        assert!(app.writes_finished());
        assert!(exit.units() > 0);
    }

    #[test]
    fn entry_completion_keeps_edits_made_after_submission() {
        let (mut app, _initial) = CenterApp::new(AppConfig { pages: vec![] }, None, None);
        let _ = app.update(Message::EntryChanged { key: "Entry".into(), value: "new edit".into() });
        app.busy_controls.insert("Entry".into());
        let _ = app.update(Message::EntryFinished { key: "Entry".into(), value: "submitted".into(), result: Ok(()) });
        let _ = app.update(Message::ControlsLoaded { slider_revision: app.slider_revision, generation: app.control_generation,
            snapshot: Snapshot { entries: vec![("Entry".into(), "submitted".into())], ..Default::default() } });
        assert_eq!(app.entry_values["Entry"], "new edit");
        assert!(app.editing_entries.contains("Entry"));
    }

    #[test]
    fn polling_skips_one_shot_and_not_yet_due_commands() {
        let path = std::env::temp_dir().join(format!("dusky-poll-count-{}", std::process::id()));
        let mut config: AppConfig = toml::from_str("[[pages]]\nid='fixture'\ntitle='Fixture'\n[[pages.layout]]\ntype='section'\n[[pages.layout.items]]\ntype='entry'\n[pages.layout.items.properties]\ntitle='Once'\nvalue_command='true'\n[[pages.layout.items]]\ntype='selection'\n[pages.layout.items.properties]\ntitle='Periodic'\ninterval=10\noptions=['ok']\nvalue_command='true'").unwrap();
        for item in &mut config.pages[0].layout[0].items {
            item.properties.value_command = format!("printf '%s\\n' '{}' >> '{}'; printf ok", item.properties.title, path.display());
        }
        let (mut app, _initial) = CenterApp::new(config, None, None);
        app.controls_loading = None;
        app.polled_generation = None;
        let first = app.refresh_controls();
        complete(&mut app, first);
        for _ in 0..20 {
            let task = app.refresh_controls();
            complete(&mut app, task);
        }
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "Once\nPeriodic\n");
        app.control_polled_at.insert("selection:Periodic".into(), Instant::now() - Duration::from_secs(11));
        let task = app.refresh_controls();
        complete(&mut app, task);
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "Once\nPeriodic\nPeriodic\n");
        app.control_generation += 1;
        let task = app.refresh_controls();
        complete(&mut app, task);
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "Once\nPeriodic\nPeriodic\nOnce\nPeriodic\n");
        std::fs::remove_file(path).unwrap();
    }

    #[test]
    fn every_configured_page_and_nested_view_constructs() {
        let config = AppConfig::load_from_path(&PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("dusky_config.toml")).unwrap();
        let (mut app, _initial) = CenterApp::new(config, None, None);
        fn collect(items: &[ItemConfig], views: &mut Vec<(String, Vec<SectionConfig>)>, expanders: &mut Vec<String>) {
            for item in items {
                if item.item_type == "navigation" { views.push((item.properties.title.clone(), item.layout.clone())); }
                if item.item_type == "expander" { expanders.push(format!("expander:{}", controls::item_key(item))); }
                collect(&item.items, views, expanders);
                for section in &item.layout { collect(&section.items, views, expanders); }
            }
        }
        let mut views = vec![];
        let mut expanders = vec![];
        for page in &app.config.pages { for section in &page.layout { collect(&section.items, &mut views, &mut expanders); } }
        for key in expanders { app.toggle_states.insert(key, true); }
        assert_eq!(app.config.pages.len(), 18);
        for index in 0..app.config.pages.len() {
            let _ = app.update(Message::SelectPage(index));
            let _ = app.view();
        }
        assert!(views.len() >= 27);
        for (title, sections) in views {
            let _ = app.update(Message::PushSubPage { title, occurrence: 0, sections });
            let _ = app.view();
            let _ = app.update(Message::PopSubPage);
        }
        app.search_query = "config".into();
        let _ = app.view();
    }

    #[test]
    fn search_reaches_nested_controls_and_opens_their_expanders() {
        let config = AppConfig::load_from_path(&PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("dusky_config.toml")).unwrap();
        let (_, hits) = build_search_cache(&config, "Active Waybar");
        assert_eq!(
            resolve_search_location(&config, &hits[0].location)
                .unwrap()
                .properties
                .title,
            "Active Waybar"
        );
        assert!(!hits[0].location.navigation.is_empty());
        let location = hits[0].location.clone();
        let (mut app, _initial) = CenterApp::new(config, None, None);
        let _ = app.update(Message::OpenSearchResult(location));
        assert!(app.visible_items().iter().any(|item| item.properties.title == "Active Waybar"));
        let config: AppConfig = toml::from_str("[[pages]]\nid='fixture'\ntitle='Fixture'\n[[pages.layout]]\ntype='section'\n[[pages.layout.items]]\ntype='expander'\n[pages.layout.items.properties]\ntitle='Group'\n[[pages.layout.items.items]]\ntype='label'\n[pages.layout.items.items.properties]\ntitle='Nested Target'\n[pages.layout.items.items.value]\ntype='static'\ntext='ok'").unwrap();
        let location = build_search_cache(&config, "target").1[0].location.clone();
        let (mut app, _initial) = CenterApp::new(config, None, None);
        assert_eq!(app.visible_items().len(), 1);
        let _ = app.update(Message::OpenSearchResult(location));
        assert_eq!(app.visible_items().len(), 2);
        assert!(app.toggle_states["expander:Group"]);
    }

    #[test]
    fn search_highlight_identifies_duplicate_rows_and_expires_independently_of_hover() {
        let config: AppConfig = toml::from_str("[[pages]]\nid='fixture'\ntitle='Fixture'\n[[pages.layout]]\ntype='section'\n[[pages.layout.items]]\ntype='label'\n[pages.layout.items.properties]\ntitle='Target'\n[[pages.layout.items]]\ntype='expander'\n[pages.layout.items.properties]\ntitle='Group'\n[[pages.layout.items.items]]\ntype='label'\n[pages.layout.items.items.properties]\ntitle='Target'").unwrap();
        let location = build_search_cache(&config, "Target").1[1].location.clone();
        let (mut app, _initial) = CenterApp::new(config, None, None);
        let _ = app.update(Message::OpenSearchResult(location.clone()));
        let selected = &app.config.pages[0].layout[0].items[1].items[0];
        assert!(app.is_search_target(selected));
        assert!(!app.is_search_target(&app.config.pages[0].layout[0].items[0]));
        assert!(app.search_highlight_strength(selected) > 0.99);
        let _ = app.update(Message::ItemEntered("Other".into()));
        let _ = app.update(Message::ItemExited("Other".into()));
        assert!(app.search_highlight.is_some());
        let started = app.search_highlight.as_ref().unwrap().1;
        let _ = app.update(Message::AnimationFrame(started + Duration::from_millis(1100)));
        let strength = app.search_highlight_strength(&app.config.pages[0].layout[0].items[1].items[0]);
        assert!(strength > 0.0 && strength < 0.4);
        let _ = app.update(Message::AnimationFrame(started + Duration::from_millis(1300)));
        assert!(app.search_highlight.is_none());
        let _ = app.update(Message::OpenSearchResult(location));
        let started = app.search_highlight.as_ref().unwrap().1;
        let _ = app.update(Message::SelectPage(0));
        assert!(app.search_highlight.is_none());
        assert!(app.update(Message::SearchPositioned { started, offset: Some(500.0) }).units() == 0);
    }

    #[test]
    fn search_scroll_uses_rendered_bounds_for_short_long_and_oversized_rows() {
        use iced_core::widget::{Operation, operation::Outcome};
        let target = iced_core::widget::Id::new("search_target");
        let mut locate = LocateSearchTarget {
            target: target.clone(),
            viewport: Some(iced::Rectangle { x: 0.0, y: 100.0, width: 600.0, height: 400.0 }),
            target_bounds: None,
        };
        assert!(matches!(locate.finish(), Outcome::Some(None)));
        for (y, height, expected) in [(120.0, 60.0, 0.0), (1200.0, 60.0, 930.0), (1200.0, 600.0, 1088.0)] {
            locate.container(Some(&target), iced::Rectangle { x: 30.0, y, width: 540.0, height });
            assert!(matches!(locate.finish(), Outcome::Some(Some(offset)) if offset == expected));
        }
    }

    #[test]
    fn generated_nested_fields_and_symlinked_themes_are_preserved() {
        let mut item: ItemConfig = toml::from_str("type='navigation'\n[properties]\ntitle='{name}'\n[[layout]]\ntype='section'\n[[layout.items]]\ntype='selection'\n[layout.items.properties]\ntitle='{name_pretty}'\nservice='{name}.service'\nvalue_command='printf {name}'\n[layout.items.on_change]\ntype='argv'\nargv=['printf','{path}']").unwrap();
        let vars = HashMap::from([("name".into(), "literal{path}".into()), ("name_pretty".into(), "Literal".into()), ("path".into(), "/tmp/a b".into())]);
        substitute_item_vars(&mut item, &vars);
        assert_eq!(item.properties.title, "literal{path}", "inserted filenames must not be re-expanded");
        let child = &item.layout[0].items[0];
        assert_eq!(child.properties.service, "literal{path}.service");
        if let Some(ChangeAction::Direct(action)) = &child.on_change {
            assert_eq!(controls::action_argv(action), ["printf", "/tmp/a b"]);
        } else { panic!("missing change action"); }
        let root = std::env::temp_dir().join(format!("dusky-theme-links-{}", std::process::id()));
        std::fs::create_dir_all(root.join("real")).unwrap();
        std::os::unix::fs::symlink(root.join("real"), root.join("linked")).unwrap();
        let mut generator: ItemConfig = toml::from_str("type='directory_generator'\n[item_template]\ntype='button'\n[item_template.properties]\ntitle='{name}'").unwrap();
        generator.properties.path = root.to_string_lossy().into_owned();
        let items = expand_directory_generator(&generator);
        assert_eq!(items.len(), 2);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn control_state_keeps_pending_values_and_rejects_stale_results() {
        // No hardware queries or action launches: exercise the actual update loop.
        let mut app = CenterApp {
            config: AppConfig { pages: vec![] },
            config_path: PathBuf::new(),
            config_override: None,
            theme: AppTheme::default(),
            theme_stamp: None,
            theme_loading: false,
            nav_stack: vec![],
            sidebar_open: true,
            search_open: false,
            search_query: String::new(),
            search_cache: None,
            loaded_pages: HashSet::new(),
            toggle_states: HashMap::new(),
            slider_values: HashMap::from([("Microphone".into(), 80.0)]),
            selected_options: HashMap::new(),
            dynamic_options: HashMap::new(),
            service_states: HashMap::new(),
            live_labels: HashMap::new(),
            file_texts: HashMap::new(),
            hovered_item: None,
            search_highlight: None,
            active_slider: Some("Microphone".into()),
            sunset_active: false,
            core_loading: HashSet::new(),
            applying_sliders: HashSet::new(),
            pending_sliders: HashMap::new(),
            deferred_sliders: HashMap::new(),
            slider_changed_at: HashMap::new(),
            slider_revision: 0,
            closing: false,
            service_statuses: HashMap::new(),
            busy_controls: HashSet::new(),
            unavailable_controls: HashSet::new(),
            active_actions: 0,
            control_generation: 0,
            controls_loading: None,
            control_polled_at: HashMap::new(),
            polled_generation: None,
            action_error: None,
            status_notice: None,
            entry_values: HashMap::new(),
            editing_entries: HashSet::new(),
            animation_now: Instant::now(),
            nav_transition: None,
            sidebar_anim: FloatTransition::settled(1.0, Instant::now()),
            search_anim: FloatTransition::settled(0.0, Instant::now()),
            expander_anims: HashMap::new(),
        };
        app.sync_slider("Microphone", 50.0);
        assert_eq!(app.slider_values["Microphone"], 80.0);
        app.sync_slider("Volume", 60.0);
        assert_eq!(app.slider_values["Volume"], 60.0);

        let _ = app.update(Message::SliderSettled {
            key: "Microphone".into(), value: 70.0, on_change: None, changed_at: Instant::now(),
        });
        assert_eq!(app.active_slider.as_deref(), Some("Microphone"));
        let _ = app.update(Message::SliderReleased {
            key: "Microphone".into(), value: 70.0, on_change: None, debounce: false,
        });
        assert_eq!(app.slider_values["Microphone"], 80.0);
        assert_eq!(app.active_slider, None);

        app.active_slider = Some("Microphone".into());
        let changed_at = Instant::now();
        app.deferred_sliders.insert("Microphone".into(), (80.0, None, changed_at));
        app.slider_changed_at.insert("Microphone".into(), changed_at);
        let settled = app.update(Message::SliderSettled {
            key: "Microphone".into(), value: 80.0, on_change: None, changed_at,
        });
        assert_eq!(settled.units(), 1, "native Microphone slider must dispatch a write on settle");
        assert_eq!(app.active_slider, None);
        assert!(app.applying_sliders.contains("Microphone"));
        // Stale readbacks stay blocked while the native write is still in flight.
        app.sync_slider("Microphone", 75.0);
        assert_eq!(app.slider_values["Microphone"], 80.0);
        let _ = app.update(Message::SliderApplied { key: "Microphone".into(), result: Ok(()) });
        assert!(!app.applying_sliders.contains("Microphone"));
        app.slider_changed_at.insert("Microphone".into(), Instant::now() - Duration::from_secs(4));
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
        let _ = app.update(Message::ControlsLoaded { slider_revision: app.slider_revision,
            generation: 3, snapshot: Snapshot { toggles: vec![("Fixture".into(), Some(true))], ..Default::default() },
        });
        assert!(!app.toggle_states["Fixture"], "stale poll must not overwrite rollback");
        app.service_states.insert("user:fixture.service".into(), true);
        let _ = app.update(Message::ControlsLoaded { slider_revision: app.slider_revision,
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
    fn stress_coalesced_writes_preserve_metadata_and_failed_persistence_on_close() {
        let root = std::env::temp_dir().join(format!("dusky_center-write-stress-{}", std::process::id()));
        std::fs::create_dir(&root).unwrap();
        let mut config: AppConfig = toml::from_str("[[pages]]\nid='old'\ntitle='Old'\n[[pages.layout]]\ntype='section'\n[[pages]]\nid='new'\ntitle='New'").unwrap();
        for index in 0..4 {
            let mut item: ItemConfig = toml::from_str("type='slider'\n[properties]\ntitle='Fixture'\npersistence='app'\nmin=0\nmax=10000").unwrap();
            item.properties.key = root.join(index.to_string()).to_string_lossy().into_owned();
            item.on_change = Some(ChangeAction::Direct(ActionConfig::Argv {
                argv: vec!["/usr/bin/bash".into(), "-c".into(),
                    "sleep .002; if [[ $1 == 4095.75 && $3 == fail ]]; then printf deliberate >&2; exit 7; fi; printf '%s\\n' \"$1\" >> \"$2\"".into(),
                    "fixture".into(), "{value}".into(), root.join(format!("log-{index}")).to_string_lossy().into_owned(),
                    if index == 3 { "fail" } else { "ok" }.into()],
                expanded_argv: false, terminal: false, requires_root: false, mode: String::new(), timeout: Some(2),
            }));
            config.pages[0].layout[0].items.push(item);
        }
        let (mut app, initial) = CenterApp::new(config, None, None);
        complete(&mut app, initial);
        let items = app.config.pages[0].layout[0].items.clone();
        let mut first = Vec::new();
        for item in &items {
            let key = controls::item_key(item);
            first.push(app.update(Message::SliderChanged { key: key.clone(), value: 0.25, on_change: item.on_change.clone(), debounce: false }));
            for value in 0..4096 {
                let task = app.update(Message::SliderChanged { key: key.clone(), value: value as f32 + 0.75, on_change: item.on_change.clone(), debounce: false });
                assert_eq!(task.units(), 0, "only one write per slider may run");
            }
        }
        assert_eq!(app.applying_sliders.len(), 4);
        assert_eq!(app.pending_sliders.len(), 4);
        let _ = app.update(Message::SelectPage(1));
        let _ = app.update(Message::CloseApp);
        complete(&mut app, Task::batch(first));
        assert!(app.writes_finished());
        assert!(app.action_error.as_ref().unwrap().contains("deliberate"));
        for index in 0..4 {
            assert_eq!(std::fs::read_to_string(root.join(index.to_string())).unwrap(),
                if index == 3 { "0.25" } else { "4095.75" });
            assert_eq!(std::fs::read_to_string(root.join(format!("log-{index}"))).unwrap(),
                if index == 3 { "0.25\n" } else { "0.25\n4095.75\n" });
        }
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn same_value_edit_after_read_cannot_restore_stale_hardware_state() {
        let (mut app, _) = CenterApp::new(AppConfig { pages: vec![] }, None, None);
        app.slider_values.insert("Volume".into(), 80.0);
        let _ = app.update(Message::SliderChanged { key: "Volume".into(), value: 80.0, on_change: None, debounce: false });
        app.slider_changed_at.insert("Volume".into(), Instant::now() - Duration::from_secs(4));
        let _ = app.update(Message::CoreLoaded {
            key: "Volume", previous: Some(80.0), value: Some(10.0), changed_at: None, sunset_active: None,
        });
        assert_eq!(app.slider_values["Volume"], 80.0);
    }

    #[test]
    fn unrelated_slider_edits_do_not_discard_core_reads() {
        let (mut app, _) = CenterApp::new(AppConfig { pages: vec![] }, None, None);
        app.slider_values.insert("Volume".into(), 50.0);
        let _ = app.update(Message::SliderChanged {
            key: "Brightness".into(), value: 80.0, on_change: None, debounce: false,
        });
        let _ = app.update(Message::CoreLoaded {
            key: "Volume", previous: Some(50.0), value: Some(60.0), changed_at: None, sunset_active: None,
        });
        assert_eq!(app.slider_values["Volume"], 60.0, "an unrelated edit cannot invalidate a Volume read");
    }

    #[test]
    fn hidden_core_controls_schedule_no_queries() {
        let (mut app, task) = CenterApp::new(AppConfig { pages: vec![] }, None, None);
        assert_eq!(task.units(), 0);
        assert!(app.core_loading.is_empty());
        assert_eq!(app.refresh_core().units(), 0);
        app.closing = true;
        assert_eq!(app.refresh_visible().units(), 0);
    }

    #[test]
    fn boot_queries_start_early_and_deliver_audio_independently() {
        use iced_futures::futures::{executor::block_on, StreamExt};
        use std::os::unix::fs::PermissionsExt;
        const FIXTURE: &str = "DUSKY_CENTER_BOOT_FIXTURE";
        if let Some(root) = std::env::var_os(FIXTURE) {
            let root = PathBuf::from(root);
            let config: AppConfig = toml::from_str("[[pages]]\nid='home'\ntitle='Home'\n[[pages.layout]]\ntype='controls'\n[[pages.layout.items]]\ntype='slider'\n[pages.layout.items.properties]\ntitle='Volume'\n[[pages.layout.items]]\ntype='slider'\n[pages.layout.items.properties]\ntitle='Microphone'").unwrap();
            let (mut app, task) = CenterApp::boot(config, None, None);
            // No Iced task has been polled yet: read-only boot work must already start.
            let deadline = Instant::now() + Duration::from_secs(2);
            while !root.join("@DEFAULT_AUDIO_SOURCE@").exists() {
                assert!(Instant::now() < deadline, "boot query did not start eagerly");
                std::thread::sleep(Duration::from_millis(2));
            }
            let mut stream = iced_runtime::task::into_stream(task).unwrap();
            let mut order = Vec::new();
            while let Some(action) = block_on(stream.next()) {
                if let iced_runtime::Action::Output(message) = action {
                    let core_reply = matches!(&message, Message::CoreLoaded { .. });
                    if let Message::CoreLoaded { key, .. } = &message {
                        order.push((*key).to_string());
                    }
                    let _ = app.update(message);
                    if core_reply && order == ["Microphone"] {
                        assert!((app.slider_values["Microphone"] - 60.0).abs() < 0.001);
                        assert!(app.core_loading.contains("Volume"));
                        assert!(!app.core_loading.contains("Microphone"));
                        // Only the still-running category is suppressed on the next cycle.
                        assert_eq!(app.refresh_core().units(), 1);
                    }
                }
            }
            assert_eq!(order, ["Microphone", "Volume"]);
            assert_eq!(app.slider_values["Volume"], 40.0);
            return;
        }
        let root = std::env::temp_dir().join(format!("dusky_center-boot-{}", std::process::id()));
        let bin = root.join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        let wpctl = bin.join("wpctl");
        std::fs::write(&wpctl, concat!("#!/usr/bin/bash\n",
            "printf started > \"$DUSKY_CENTER_BOOT_FIXTURE/$2\"\n",
            "if [[ $2 == @DEFAULT_AUDIO_SINK@ ]]; then /usr/bin/sleep .3; printf 'Volume: 0.40\\n'; else printf 'Volume: 0.60\\n'; fi\n")).unwrap();
        std::fs::set_permissions(wpctl, std::fs::Permissions::from_mode(0o755)).unwrap();
        let result = std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "ui::tests::boot_queries_start_early_and_deliver_audio_independently", "--nocapture"])
            .env(FIXTURE, &root).env("PATH", &bin).output().unwrap();
        std::fs::remove_dir_all(root).unwrap();
        assert!(result.status.success(), "{}{}", String::from_utf8_lossy(&result.stdout), String::from_utf8_lossy(&result.stderr));
    }

    #[test]
    fn hardware_refresh_is_deferred_and_cannot_overwrite_a_released_edit() {
        let config: AppConfig = toml::from_str("[[pages]]\nid='home'\ntitle='Home'\n[[pages.layout]]\ntype='controls'\n[[pages.layout.items]]\ntype='slider'\n[pages.layout.items.properties]\ntitle='Volume'\n[[pages.layout.items]]\ntype='slider'\n[pages.layout.items.properties]\ntitle='Microphone'").unwrap();
        let (mut app, _task) = CenterApp::new(config, None, None);
        assert_eq!(app.core_loading.len(), 2);
        assert_eq!(app.slider_values["Volume"], 0.0, "hardware queries must be deferred to the task");
        assert_eq!(app.refresh_core().units(), 0, "only one hardware poll may run");
        app.slider_values.insert("Volume".into(), 80.0);
        let _ = app.update(Message::CoreLoaded {
            key: "Volume", previous: Some(50.0), value: Some(40.0), changed_at: None, sunset_active: None,
        });
        let _ = app.update(Message::CoreLoaded {
            key: "Microphone", previous: Some(0.0), value: Some(60.0), changed_at: None, sunset_active: Some(true),
        });
        assert_eq!(app.slider_values["Volume"], 80.0);
        assert!((app.slider_values["Microphone"] - 60.0).abs() < 0.001);
        assert!(app.sunset_active);
        assert!(app.core_loading.is_empty());
    }

    #[test]
    fn night_light_writes_coalesce_while_the_ui_remains_responsive() {
        let (mut app, _task) = CenterApp::new(AppConfig { pages: vec![] }, None, None);
        let _ = app.update(Message::SliderChanged { key: "Night Light".into(), value: 20.0, on_change: None, debounce: false });
        assert!(app.applying_sliders.contains("Night Light"));
        let _ = app.update(Message::SliderChanged { key: "Night Light".into(), value: 30.0, on_change: None, debounce: false });
        let _ = app.update(Message::SliderChanged { key: "Night Light".into(), value: 40.0, on_change: None, debounce: false });
        assert_eq!(app.pending_sliders["Night Light"].0, 40.0);
        let _ = app.update(Message::CoreLoaded {
            key: "Night Light", previous: Some(40.0), value: Some(10.0), changed_at: None, sunset_active: Some(true),
        });
        assert_eq!(app.slider_values["Night Light"], 40.0);
        let _ = app.update(Message::CloseApp);
        assert!(app.closing);
        let _ = app.update(Message::SliderReleased { key: "Night Light".into(), value: 40.0, on_change: None, debounce: false });
        assert_eq!(app.pending_sliders["Night Light"].0, 40.0, "release during close must not enqueue another write");
        let _ = app.update(Message::SliderApplied { key: "Night Light".into(), result: Ok(()) });
        assert!(app.applying_sliders.contains("Night Light"));
        assert!(!app.pending_sliders.contains_key("Night Light"));
        let exit = app.update(Message::SliderApplied { key: "Night Light".into(), result: Ok(()) });
        assert!(!app.applying_sliders.contains("Night Light"));
        assert!(exit.units() > 0);
    }

    #[test]
    fn close_drains_independent_sliders_and_reports_write_failure() {
        let (mut app, _initial) = CenterApp::new(AppConfig { pages: vec![] }, None, None);
        let action: ActionConfig = toml::from_str("type='exec'\ncommand='true'").unwrap();
        for key in ["Brightness", "Microphone"] {
            let _ = app.update(Message::SliderChanged {
                key: key.into(), value: 30.0,
                on_change: Some(ChangeAction::Direct(action.clone())), debounce: false,
            });
        }
        assert_eq!(app.applying_sliders.len(), 2);
        let _ = app.update(Message::CloseApp);
        let first = app.update(Message::SliderApplied { key: "Brightness".into(), result: Err("fixture failure".into()) });
        assert_eq!(first.units(), 0, "another slider still has a write in progress");
        assert_eq!(app.action_error.as_deref(), Some("Brightness: fixture failure"));
        let last = app.update(Message::SliderApplied { key: "Microphone".into(), result: Ok(()) });
        assert!(last.units() > 0);
        assert!(app.applying_sliders.is_empty());
    }

    #[test]
    fn collapsing_expander_keeps_pending_scalar_and_file_entries_refresh() {
        let path = std::env::temp_dir().join(format!("dusky-collapse-{}", std::process::id()));
        let mut config: AppConfig = toml::from_str("[[pages]]\nid='fixture'\ntitle='Fixture'\n[[pages.layout]]\ntype='section'\n[[pages.layout.items]]\ntype='expander'\n[pages.layout.items.properties]\ntitle='Group'\n[[pages.layout.items.items]]\ntype='slider'\n[pages.layout.items.items.properties]\ntitle='Scalar'\npersistence='app'").unwrap();
        config.pages[0].layout[0].items[0].items[0].properties.key = path.to_string_lossy().into_owned();
        let key = path.to_string_lossy().into_owned();
        let (mut app, _) = CenterApp::new(config, None, None);
        let _ = app.update(Message::ToggleItem { key: "expander:Group".into(), is_enabled: true, on_toggle: None });
        let _ = app.update(Message::SliderChanged { key: key.clone(), value: 0.25, on_change: None, debounce: true });
        let collapse = app.update(Message::ToggleItem { key: "expander:Group".into(), is_enabled: false, on_toggle: None });
        complete(&mut app, collapse);
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "0.25");
        assert_eq!(app.visible_items().len(), 1);
        app.config.pages[0].layout[0].items[0].items[0].item_type = "entry".into();
        app.toggle_states.insert("expander:Group".into(), true);
        app.controls_loading = None;
        app.control_generation += 1;
        let initial = app.refresh_controls();
        complete(&mut app, initial);
        std::fs::write(&path, "externally changed").unwrap();
        app.control_polled_at.insert(format!("entry:{key}"), Instant::now() - Duration::from_secs(4));
        let refresh = app.refresh_controls();
        complete(&mut app, refresh);
        assert_eq!(app.entry_values[&key], "externally changed");
        std::fs::remove_file(path).unwrap();
    }

    #[test]
    fn sourced_selections_stay_unknown_and_removed_options_clear_the_value() {
        let config: AppConfig = toml::from_str("[[pages]]\nid='fixture'\ntitle='Fixture'\n[[pages.layout]]\ntype='section'\n[[pages.layout.items]]\ntype='selection'\n[pages.layout.items.properties]\ntitle='Choice'\noptions=['Old','New']\nvalue_command='printf New'").unwrap();
        let (mut app, _) = CenterApp::new(config, None, None);
        assert!(!app.selected_options.contains_key("Choice"));
        app.selected_options.insert("Choice".into(), "Old".into());
        let _ = app.update(Message::ControlsLoaded { slider_revision: app.slider_revision, generation: app.control_generation,
            snapshot: Snapshot { options: vec![("Choice".into(), vec!["New".into()])], ..Default::default() } });
        assert!(!app.selected_options.contains_key("Choice"));
    }

    #[test]
    fn search_routes_distinguish_identically_named_subpages() {
        let mut config: AppConfig = toml::from_str("[[pages]]\nid='fixture'\ntitle='Fixture'\n[[pages.layout]]\ntype='section'\n[[pages.layout.items]]\ntype='navigation'\n[pages.layout.items.properties]\ntitle='Same'\n[[pages.layout.items.layout]]\ntype='section'\n[[pages.layout.items.layout.items]]\ntype='label'\n[pages.layout.items.layout.items.properties]\ntitle='First'").unwrap();
        let mut second = config.pages[0].layout[0].items[0].clone();
        second.layout[0].items[0].properties.title = "Second Unique".into();
        config.pages[0].layout[0].items.push(second);
        let location = build_search_cache(&config, "Second Unique").1[0].location.clone();
        let (mut app, _) = CenterApp::new(config, None, None);
        let _ = app.update(Message::OpenSearchResult(location));
        assert_eq!(app.visible_items()[0].properties.title, "Second Unique");
    }

    #[test]
    fn final_integration_write_completion_does_not_cancel_a_later_debounce() {
        let (mut app, _) = CenterApp::new(AppConfig { pages: vec![] }, None, None);
        let action = Some(ChangeAction::Direct(toml::from_str("type='exec'\ncommand='true'").unwrap()));
        app.applying_sliders.insert("Deferred".into());
        let _ = app.update(Message::SliderChanged { key: "Deferred".into(), value: 40.0, on_change: action.clone(), debounce: true });
        let changed_at = app.slider_changed_at["Deferred"];
        let _ = app.update(Message::SliderApplied { key: "Deferred".into(), result: Ok(()) });
        let settled = app.update(Message::SliderSettled { key: "Deferred".into(), value: 40.0, on_change: action, changed_at });
        assert_eq!(settled.units(), 1, "completion of an earlier write must not invalidate the newer edit's timer");
        assert!(!app.deferred_sliders.contains_key("Deferred"));
    }

    #[test]
    fn final_integration_slow_control_snapshot_does_not_overwrite_a_completed_edit() {
        use iced_futures::futures::{executor::block_on, StreamExt};
        let mut config: AppConfig = toml::from_str("[[pages]]\nid='fixture'\ntitle='Fixture'\n[[pages.layout]]\ntype='section'\n[[pages.layout.items]]\ntype='slider'\n[pages.layout.items.properties]\ntitle='Scalar'\nvalue_command='printf 10'").unwrap();
        config.pages[0].layout[0].items.push(toml::from_str("type='toggle'\n[properties]\ntitle='Unrelated Toggle'\nstate_command='printf true'").unwrap());
        config.pages[0].layout[0].items.push(toml::from_str("type='entry'\n[properties]\ntitle='Unrelated Entry'\nvalue_command='printf observed'").unwrap());
        let (mut app, _) = CenterApp::new(config, None, None);
        app.controls_loading = None;
        app.polled_generation = None;
        let poll = app.refresh_controls();
        let replies = block_on(iced_runtime::task::into_stream(poll).unwrap().collect::<Vec<_>>());
        let action = Some(ChangeAction::Direct(toml::from_str("type='exec'\ncommand='true'").unwrap()));
        let _ = app.update(Message::SliderChanged { key: "Scalar".into(), value: 40.0, on_change: action, debounce: false });
        let _ = app.update(Message::SliderApplied { key: "Scalar".into(), result: Ok(()) });
        app.slider_changed_at.insert("Scalar".into(), Instant::now() - Duration::from_secs(4));
        for reply in replies {
            if let iced_runtime::Action::Output(message) = reply { let _ = app.update(message); }
        }
        assert_eq!(app.slider_values["Scalar"], 40.0, "a delayed generic query must reject observations from before an edit");
        assert!(app.toggle_states["Unrelated Toggle"], "rejecting an old slider observation must preserve other control reads");
        assert_eq!(app.entry_values["Unrelated Entry"], "observed");
        let _ = app.update(Message::ControlsLoaded { generation: app.control_generation, slider_revision: app.slider_revision,
            snapshot: Snapshot { sliders: vec![("Scalar".into(), 41.0)], ..Default::default() } });
        assert_eq!(app.slider_values["Scalar"], 41.0, "fresh external observations must still apply");
    }

    #[test]
    fn final_integration_navigation_drains_the_original_setting_metadata() {
        let path = std::env::temp_dir().join(format!("dusky-navigation-write-{}", std::process::id()));
        let mut config: AppConfig = toml::from_str("[[pages]]\nid='old'\ntitle='Old'\n[[pages.layout]]\ntype='section'\n[[pages.layout.items]]\ntype='slider'\n[pages.layout.items.properties]\ntitle='Scalar'\npersistence='app'\n[[pages]]\nid='new'\ntitle='New'").unwrap();
        let key = path.to_string_lossy().into_owned();
        config.pages[0].layout[0].items[0].properties.key = key.clone();
        let (mut app, _) = CenterApp::new(config, None, None);
        let first = app.update(Message::SliderChanged { key: key.clone(), value: 10.0, on_change: None, debounce: false });
        let _ = app.update(Message::SliderChanged { key: key.clone(), value: 40.25, on_change: None, debounce: true });
        let navigation = app.update(Message::SelectPage(1));
        assert!(app.deferred_sliders.is_empty());
        assert_eq!(app.active_page_idx(), 1);
        complete(&mut app, navigation);
        complete(&mut app, first);
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "40.25");
        assert!(app.pending_sliders.is_empty());
        assert!(app.applying_sliders.is_empty());
        std::fs::remove_file(path).unwrap();
    }

    #[test]
    fn repeated_slider_value_does_not_accept_an_older_timer() {
        let (mut app, _) = CenterApp::new(AppConfig { pages: vec![] }, None, None);
        let action = Some(ChangeAction::Direct(toml::from_str("type='exec'\ncommand='true'").unwrap()));
        let mut old = Instant::now();
        for (index, value) in [30.0, 20.0, 30.0].into_iter().enumerate() {
            let _ = app.update(Message::SliderChanged { key: "Repeated".into(), value, on_change: action.clone(), debounce: true });
            if index == 0 { old = app.slider_changed_at["Repeated"]; }
        }
        let current = app.slider_changed_at["Repeated"];
        assert_ne!(old, current);
        let stale = app.update(Message::SliderSettled { key: "Repeated".into(), value: 30.0, on_change: action.clone(), changed_at: old });
        assert_eq!(stale.units(), 0);
        assert!(app.deferred_sliders.contains_key("Repeated"));
        let valid = app.update(Message::SliderSettled { key: "Repeated".into(), value: 30.0, on_change: action, changed_at: current });
        assert_eq!(valid.units(), 1);
    }

    #[test]
    fn generator_substitution_handles_shell_braces_without_expanding_inserted_names() {
        let mut item: ItemConfig = toml::from_str("type='button'\n[properties]\ntitle='{name}'\n[on_press]\ntype='exec'\ncommand='{ printf %s {name}; }'").unwrap();
        substitute_item_vars(&mut item, &HashMap::from([("name".into(), "{path}".into()), ("path".into(), "wrong".into())]));
        assert_eq!(item.properties.title, "{path}");
        let Some(ActionConfig::Exec { command, .. }) = item.on_press else { panic!() };
        assert_eq!(command, "{ printf %s {path}; }");
    }

    #[test]
    fn explicit_debounce_waits_for_a_settled_value() {
        let (mut app, _initial) = CenterApp::new(AppConfig { pages: vec![] }, None, None);
        let action = Some(ChangeAction::Direct(toml::from_str("type='exec'\ncommand='true'").unwrap()));
        let _ = app.update(Message::SliderChanged {
            key: "Deferred".into(), value: 30.0, on_change: action.clone(), debounce: true,
        });
        assert!(app.applying_sliders.is_empty());
        let changed_at = app.slider_changed_at["Deferred"];
        let _ = app.update(Message::SliderSettled { key: "Deferred".into(), value: 20.0, on_change: action.clone(), changed_at });
        assert!(app.applying_sliders.is_empty(), "an older timer must not submit the current value");
        let write = app.update(Message::SliderSettled { key: "Deferred".into(), value: 30.0, on_change: action, changed_at });
        assert_eq!(write.units(), 1);
        assert!(app.applying_sliders.contains("Deferred"));
    }

    #[test]
    fn worker_tasks_and_reload_run_with_isolated_command_fixtures() {
        use iced_futures::futures::{executor::block_on, StreamExt};
        use std::os::unix::fs::PermissionsExt;
        if let Some(root) = std::env::var_os("DUSKY_WORKER_FIXTURE") {
            let root = std::path::PathBuf::from(root);
            let config: AppConfig = toml::from_str("[[pages]]\nid='home'\ntitle='Home'\n[[pages.layout]]\ntype='controls'\n[[pages.layout.items]]\ntype='slider'\n[pages.layout.items.properties]\ntitle='Volume'\n[[pages.layout.items]]\ntype='slider'\n[pages.layout.items.properties]\ntitle='Microphone'\n[[pages.layout.items]]\ntype='slider'\n[pages.layout.items.properties]\ntitle='Night Light'").unwrap();
            let (mut app, initial) = CenterApp::new(config, None, None);
            let actions = block_on(iced_runtime::task::into_stream(initial).unwrap().collect::<Vec<_>>());
            for action in actions {
                if let iced_runtime::Action::Output(message) = action { let _ = app.update(message); }
            }
            assert_eq!(app.slider_values["Volume"], 50.0);
            assert_eq!(app.slider_values["Microphone"], 50.0);
            assert!(app.sunset_active);
            let first = app.update(Message::SliderChanged { key: "Night Light".into(), value: 20.0, on_change: None, debounce: false });
            let _ = app.update(Message::SliderChanged { key: "Night Light".into(), value: 40.0, on_change: None, debounce: false });
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
            assert!(!app.applying_sliders.contains("Night Light"));
            assert_eq!(std::fs::read_to_string(root.join("writes")).unwrap(), "5400\n4300\n");
            assert_eq!(std::fs::read_to_string(sys::sunset_state_file()).unwrap(), "4300\n");
            app.closing = false;
            let brightness_action: ActionConfig = toml::from_str("type='exec'\ncommand='brightnessctl set {value}%'\n").unwrap();
            let first = app.update(Message::SliderChanged {
                key: "Brightness".into(), value: 10.0,
                on_change: Some(ChangeAction::Direct(brightness_action.clone())), debounce: false,
            });
            assert_eq!(first.units(), 1, "live changes start one write without a settle timer");
            assert!(app.active_slider.is_none(), "keyboard changes must not permanently suppress polling");
            for value in 11..=80 {
                let next = app.update(Message::SliderChanged {
                    key: "Brightness".into(), value: value as f32,
                    on_change: Some(ChangeAction::Direct(brightness_action.clone())), debounce: false,
                });
                assert_eq!(next.units(), 0, "intermediate values must not launch more workers");
            }
            let actions = block_on(iced_runtime::task::into_stream(first).unwrap().collect::<Vec<_>>());
            assert_eq!(std::fs::read_to_string(root.join("brightness-writes")).unwrap(), "set 10%\n", "hardware action must run before release");
            let release = app.update(Message::SliderReleased {
                key: "Brightness".into(), value: 80.0,
                on_change: Some(ChangeAction::Direct(brightness_action)), debounce: false,
            });
            assert_eq!(release.units(), 0, "release must not duplicate a live write");
            let _ = app.update(Message::CloseApp);
            for action in actions {
                if let iced_runtime::Action::Output(message) = action
                    && let Some(stream) = iced_runtime::task::into_stream(app.update(message))
                {
                    for action in block_on(stream.collect::<Vec<_>>()) {
                        if let iced_runtime::Action::Output(message) = action { let _ = app.update(message); }
                    }
                }
            }
            assert!(app.applying_sliders.is_empty());
            assert!(app.pending_sliders.is_empty());
            assert_eq!(std::fs::read_to_string(root.join("brightness-writes")).unwrap(), "set 10%\nset 80%\n");
            app.sync_slider("Brightness", 10.0);
            assert_eq!(app.slider_values["Brightness"], 80.0, "recent writes must resist hardware readback lag");
            let old_change = app.slider_changed_at.get("Brightness").copied();
            app.slider_changed_at.insert("Brightness".into(), Instant::now() - Duration::from_secs(4));
            let _ = app.update(Message::CoreLoaded {
                key: "Brightness", previous: Some(80.0), value: Some(10.0), changed_at: old_change, sunset_active: Some(true),
            });
            assert_eq!(app.slider_values["Brightness"], 80.0, "old polls must be rejected even after the grace period");
            let _ = app.update(Message::CoreLoaded {
                key: "Brightness", previous: Some(80.0), value: Some(79.0), changed_at: app.slider_changed_at.get("Brightness").copied(), sunset_active: Some(true),
            });
            assert_eq!(app.slider_values["Brightness"], 79.0, "fresh external changes must still be observed");
            // Simulate reopening after the completed close before testing reload.
            app.closing = false;
            let colors = root.join("matugen/generated");
            std::fs::create_dir_all(&colors).unwrap();
            let palette = colors.join("dusky_center.json");
            std::fs::write(&palette, r##"{"bg":"#123456","fg":"#eeeeee","accent":"#aabbcc","secondary":"#778899","tertiary":"#8899aa"}"##).unwrap();
            let tick = app.update(Message::Tick);
            complete(&mut app, tick);
            let valid_theme = app.theme;
            assert_eq!(valid_theme.bg, Color::from_rgb8(0x12, 0x34, 0x56));
            std::fs::write(&palette, "{").unwrap();
            let tick = app.update(Message::Tick);
            complete(&mut app, tick);
            assert_eq!(app.theme, valid_theme);
            std::fs::remove_file(&palette).unwrap();
            let tick = app.update(Message::Tick);
            complete(&mut app, tick);
            assert_eq!(app.theme, valid_theme);
            let config_file = root.join("dusky/dusky_config.toml");
            std::fs::create_dir_all(config_file.parent().unwrap()).unwrap();
            app.dynamic_options.insert("Choice".into(), vec!["Old dynamic".into()]);
            std::fs::write(&config_file, "pages = [").unwrap();
            let _ = app.update(Message::ReloadConfig);
            assert!(app.action_error.as_ref().unwrap().starts_with("Reload:"));
            assert!(app.status_notice.is_none());
            assert_eq!(app.config.pages[0].id, "home");
            assert_eq!(app.dynamic_options["Choice"], ["Old dynamic"]);
            assert_eq!(app.theme, valid_theme);
            std::fs::write(&config_file, "[[pages]]\nid='fixture'\ntitle='Fixture'\n").unwrap();
            let _ = app.update(Message::ExecuteAction(ActionConfig::Reload));
            assert_eq!(app.config.pages[0].id, "fixture");
            assert_eq!(app.config_path, config_file);
            assert!(app.status_notice.is_some());
            assert!(app.dynamic_options.is_empty(), "successful reload must discard old option sources");
            assert!(app.action_error.is_none());
            std::fs::write(&config_file, "[[pages]]\nid='first'\ntitle='First'\n[[pages]]\nid='second'\ntitle='Second'\n").unwrap();
            let _ = app.update(Message::ReloadConfig);
            let _ = app.update(Message::SelectPage(1));
            std::fs::write(&config_file, "[[pages]]\nid='second'\ntitle='Second'\n[[pages]]\nid='first'\ntitle='First'\n").unwrap();
            let _ = app.update(Message::ReloadConfig);
            assert_eq!(app.active_page_idx(), 0, "reload must preserve the page id when pages are reordered");
            std::fs::write(root.join("bin/hyprctl"), "#!/usr/bin/bash\nexit 1\n").unwrap();
            assert!(sys::apply_sunset(90.0).is_err());
            assert_eq!(std::fs::read_to_string(sys::sunset_state_file()).unwrap(), "4300\n", "failed IPC must not publish a false cached state");
            return;
        }
        let root = std::env::temp_dir().join(format!("dusky-worker-fixture-{}", std::process::id()));
        let bin = root.join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        std::os::unix::fs::symlink("/usr/bin/bash", bin.join("bash")).unwrap();
        for (name, body) in [
            ("brightnessctl", "printf '%s\\n' \"$*\" >> \"$DUSKY_WORKER_FIXTURE/brightness-writes\"; /usr/bin/sleep .06"),
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
                            expanded_argv: false,
                            terminal: false,
                            requires_root: true,
                            mode: String::new(),
                            timeout: None,
                        },
                        disabled: ActionConfig::Exec {
                            command: "wg-quick down {path}".to_string(),
                            argv: Vec::new(),
                            expanded_argv: false,
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

    #[test]
    fn reload_with_config_override_resets_stale_state_preserves_inflight_and_handles_mixed_controls() {
        let root = std::env::temp_dir().join(format!("dusky-reload-state-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).unwrap();
        let cfg_path = root.join("custom.toml");

        let v1 = r#"
[[pages]]
id = "home"
title = "Home"

[[pages.layout]]
type = "section"
[pages.layout.properties]
title = "Power Management"

[[pages.layout.items]]
type = "selection"
[pages.layout.items.properties]
title = "Active Profile"
options = ["Balanced", "Performance"]

[[pages.layout]]
type = "controls"
[pages.layout.properties]
title = "Audio"

[[pages.layout.items]]
type = "slider"
[pages.layout.items.properties]
title = "Volume"
min = 0.0
max = 100.0
default = 50.0

[[pages.layout.items]]
type = "slider"
[pages.layout.items.properties]
title = "Generic Gain"
min = 0.0
max = 100.0
default = 20.0

[[pages.layout.items]]
type = "label"
[pages.layout.items.properties]
title = "Status"
[pages.layout.items.value]
type = "static"
text = "v1"
"#;
        std::fs::write(&cfg_path, v1).unwrap();
        let loaded = AppConfig::load_resolved(Some(&cfg_path)).unwrap();
        let (mut app, _initial) = CenterApp::new_loaded(loaded, Some(cfg_path.clone()), None, None);

        // Seed stale state alongside protected in-flight / expander / core state.
        app.slider_values.insert("Volume".into(), 95.0);
        app.slider_values.insert("Generic Gain".into(), 88.0);
        app.slider_values.insert("InFlightSlider".into(), 42.0);
        app.applying_sliders.insert("InFlightSlider".into());
        app.toggle_states.insert("expander:Group".into(), true);
        app.toggle_states.insert("BusyToggle".into(), true);
        app.toggle_states.insert("StaleToggle".into(), true);
        app.busy_controls.insert("BusyToggle".into());
        app.selected_options.insert("Active Profile".into(), "Performance".into());
        app.live_labels.insert("Status".into(), "v1".into());
        app.unavailable_controls.insert("StaleToggle".into());
        app.control_polled_at.insert("label:Status".into(), Instant::now());
        app.hovered_item = Some("StaleToggle".into());
        // Simulate an in-flight pre-reload poll that must not block the reload refresh.
        let old_gen = app.control_generation;
        app.controls_loading = Some(old_gen);
        app.polled_generation = Some(old_gen);

        let v2 = r#"
[[pages]]
id = "home"
title = "Home"

[[pages.layout]]
type = "section"
[pages.layout.properties]
title = "Power Management"

[[pages.layout.items]]
type = "selection"
[pages.layout.items.properties]
title = "Active Profile"
options = ["Eco", "Turbo"]

[[pages.layout.items]]
type = "label"
[pages.layout.items.properties]
title = "Added Power Row"
[pages.layout.items.value]
type = "static"
text = "present"

[[pages.layout]]
type = "controls"
[pages.layout.properties]
title = "Audio"

[[pages.layout.items]]
type = "slider"
[pages.layout.items.properties]
title = "Volume"
min = 0.0
max = 80.0
default = 40.0

[[pages.layout.items]]
type = "slider"
[pages.layout.items.properties]
title = "Generic Gain"
min = 0.0
max = 100.0
default = 35.0

[[pages.layout.items]]
type = "label"
[pages.layout.items.properties]
title = "Status"
[pages.layout.items.value]
type = "static"
text = "v2"
"#;
        std::fs::write(&cfg_path, v2).unwrap();

        let reload_task = app.update(Message::ReloadConfig);
        assert!(reload_task.units() > 0, "reload must dispatch fresh queries even when a prior poll was in flight");
        assert_eq!(app.config_path, cfg_path);
        assert!(app.action_error.is_none());
        assert!(app.status_notice.as_ref().is_some_and(|(msg, _)| msg.starts_with("Reloaded ")));

        // Protected state survives; Volume is clamped to its new max (80.0).
        assert_eq!(app.slider_values["Volume"], 80.0);
        assert_eq!(app.slider_values["InFlightSlider"], 42.0);
        assert_eq!(app.slider_values["Generic Gain"], 35.0);
        assert_eq!(app.toggle_states.get("expander:Group"), Some(&true));
        assert_eq!(app.toggle_states.get("BusyToggle"), Some(&true));
        assert!(!app.toggle_states.contains_key("StaleToggle"));
        assert!(!app.unavailable_controls.contains("StaleToggle"));
        assert!(app.hovered_item.is_none());
        assert_eq!(app.selected_options.get("Active Profile").map(String::as_str), Some("Eco"));

        // Late out-of-order reply from old generation must not schedule a redundant poll.
        let stale_reply = app.update(Message::ControlsLoaded {
            generation: old_gen,
            slider_revision: 0,
            snapshot: Snapshot {
                labels: vec![("Status".into(), "stale".into())],
                ..Default::default()
            },
        });
        assert_eq!(stale_reply.units(), 0);

        // Complete reload queries and confirm both added Power Management row and updated label render cleanly.
        complete(&mut app, reload_task);
        assert_eq!(app.live_labels.get("Status").map(String::as_str), Some("v2"));
        assert_eq!(app.live_labels.get("Added Power Row").map(String::as_str), Some("present"));
        let _ = app.view();

        // Notice auto-clears on Tick after 2 seconds.
        app.status_notice.as_mut().unwrap().1 = Instant::now() - Duration::from_secs(3);
        let _ = app.update(Message::Tick);
        assert!(app.status_notice.is_none());

        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn reload_restores_nested_duplicate_subpages_and_falls_back_when_removed() {
        let root = std::env::temp_dir().join(format!("dusky-reload-nav-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).unwrap();
        let cfg_path = root.join("nav.toml");

        let v1 = r#"
[[pages]]
id = "net"
title = "Network"

[[pages.layout]]
type = "section"
[pages.layout.properties]
title = "Group"

[[pages.layout.items]]
type = "navigation"
[pages.layout.items.properties]
title = "Same"
[[pages.layout.items.layout]]
type = "section"
[[pages.layout.items.layout.items]]
type = "label"
[pages.layout.items.layout.items.properties]
title = "First"

[[pages.layout.items]]
type = "navigation"
[pages.layout.items.properties]
title = "Same"
[[pages.layout.items.layout]]
type = "section"
[[pages.layout.items.layout.items]]
type = "label"
[pages.layout.items.layout.items.properties]
title = "Second v1"
"#;
        std::fs::write(&cfg_path, v1).unwrap();
        let loaded = AppConfig::load_resolved(Some(&cfg_path)).unwrap();
        let (mut app, _) = CenterApp::new_loaded(loaded, Some(cfg_path.clone()), None, None);

        let second_sections = app.config.pages[0].layout[0].items[1].layout.clone();
        let _ = app.update(Message::PushSubPage {
            title: "Same".into(),
            occurrence: 1,
            sections: second_sections,
        });
        assert_eq!(app.visible_items()[0].properties.title, "Second v1");

        // Reload with an edited item inside the second "Same" subpage.
        let v2 = v1.replace("Second v1", "Second v2");
        std::fs::write(&cfg_path, v2).unwrap();
        let _ = app.update(Message::ReloadConfig);
        assert_eq!(app.nav_stack.len(), 2);
        assert_eq!(app.visible_items()[0].properties.title, "Second v2");

        // Remove the second "Same" subpage; reload must fall back cleanly to the root page.
        let v3 = r#"
[[pages]]
id = "net"
title = "Network"

[[pages.layout]]
type = "section"
[pages.layout.properties]
title = "Group"

[[pages.layout.items]]
type = "navigation"
[pages.layout.items.properties]
title = "Same"
[[pages.layout.items.layout]]
type = "section"
[[pages.layout.items.layout.items]]
type = "label"
[pages.layout.items.layout.items.properties]
title = "First"
"#;
        std::fs::write(&cfg_path, v3).unwrap();
        let _ = app.update(Message::ReloadConfig);
        assert_eq!(app.nav_stack.len(), 1);
        assert!(matches!(app.nav_stack[0], NavView::Root(0)));

        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn launch_mode_toggle_avoids_optimistic_flicker_and_observed_button_surfaces_failure() {
        let config: AppConfig = toml::from_str(r#"
[[pages]]
id = "home"
title = "Home"

[[pages.layout]]
type = "section"
[pages.layout.properties]
title = "Actions"

[[pages.layout.items]]
type = "toggle"
[pages.layout.items.properties]
title = "Launch Toggle"
state_command = "printf no"
[pages.layout.items.on_toggle.enabled]
type = "exec"
command = "true"
mode = "launch"
[pages.layout.items.on_toggle.disabled]
type = "exec"
command = "true"
mode = "launch"

[[pages.layout.items]]
type = "button"
[pages.layout.items.properties]
title = "Observed Fail"
[pages.layout.items.on_press]
type = "exec"
command = "printf boom >&2; exit 7"
mode = "apply"
"#).unwrap();
        let (mut app, initial) = CenterApp::new(config, None, None);
        complete(&mut app, initial);
        assert_eq!(app.toggle_states.get("Launch Toggle"), Some(&false));

        let pair = app.config.pages[0].layout[0].items[0].on_toggle.clone();
        let toggle_task = app.update(Message::ToggleItem {
            key: "Launch Toggle".into(),
            is_enabled: true,
            on_toggle: pair,
        });
        // Launch-mode toggle must NOT flip optimistically while the launcher runs.
        assert_eq!(app.toggle_states.get("Launch Toggle"), Some(&false));
        assert!(app.busy_controls.contains("Launch Toggle"));
        complete(&mut app, toggle_task);
        assert_eq!(app.toggle_states.get("Launch Toggle"), Some(&false));
        assert!(!app.busy_controls.contains("Launch Toggle"));

        // Observed apply button runs off-thread, tracks active_actions, and surfaces stderr.
        let fail_action = app.config.pages[0].layout[0].items[1].on_press.clone().unwrap();
        let btn_task = app.update(Message::ExecuteAction(fail_action));
        assert_eq!(app.active_actions, 1);
        assert!(!app.writes_finished());
        complete(&mut app, btn_task);
        assert_eq!(app.active_actions, 0);
        assert!(app.writes_finished());
        assert!(app.action_error.as_deref().is_some_and(|e| e.contains("boom")));

        // Detached missing-binary button surfaces spawn error off-thread without blocking close.
        let missing = ActionConfig::Argv {
            argv: vec!["/definitely-missing-dusky-bin-42".into()],
            expanded_argv: false,
            terminal: false,
            requires_root: false,
            mode: String::new(),
            timeout: None,
        };
        let launch_task = app.update(Message::ExecuteAction(missing));
        assert_eq!(app.active_actions, 0);
        assert!(app.writes_finished());
        complete(&mut app, launch_task);
        assert!(app.action_error.as_deref().is_some_and(|e| e.contains("Executable not found")));
    }

    #[test]
    fn cached_search_button_text_file_cache_and_empty_controls_section_behave_cleanly() {
        let root = std::env::temp_dir().join(format!("dusky-i2-ui-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).unwrap();
        let badge = root.join("behind");
        std::fs::write(&badge, "0\n").unwrap();

        let cfg_str = format!(r#"
[[pages]]
id = "home"
title = "Home"

[[pages.layout]]
type = "grid_section"

[[pages.layout.items]]
type = "grid_card"
[pages.layout.items.properties]
title = "Dusky"
button_text_file = "{}"
button_text_map = {{ "0" = "Updated", "default" = "Available" }}

[[pages.layout]]
type = "controls"
[pages.layout.properties]
title = "Display"

[[pages.layout.items]]
type = "slider"
[pages.layout.items.properties]
title = "Night Light"
"#, badge.display());
        let config: AppConfig = toml::from_str(&cfg_str).unwrap();
        let (mut app, initial) = CenterApp::new(config, None, None);

        // Night Light is inactive -> Display controls section has 0 visible items.
        assert!(app.visible_controls_items(&app.config.pages[0].layout[1]).is_empty());
        let _ = app.view();

        complete(&mut app, initial);
        let badge_key = badge.to_string_lossy().into_owned();
        assert_eq!(app.file_texts.get(&badge_key).map(String::as_str), Some("0"));

        // Deleting the file clears the cached entry on the next poll.
        std::fs::remove_file(&badge).unwrap();
        app.control_generation += 1;
        let refresh = app.refresh_controls();
        complete(&mut app, refresh);
        assert!(!app.file_texts.contains_key(&badge_key));

        // Search cache populates on SearchChanged and clears on ClearSearch.
        let _ = app.update(Message::SearchChanged("Dusky".into()));
        assert!(app.search_cache.as_ref().is_some_and(|(q, total, hits)| q == "Dusky" && *total == 1 && hits.len() == 1));
        let _ = app.view();
        let _ = app.update(Message::ClearSearch);
        assert!(app.search_cache.is_none());

        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn default_brightness_action_falls_back_to_ddc_when_local_backlight_fails() {
        const CHILD: &str = "DUSKY_CENTER_DDC_FALLBACK_FIXTURE";
        if let Some(root) = std::env::var_os(CHILD) {
            let root = PathBuf::from(root);
            let cache = sys::ddc_cache_file();
            crate::backend::cmd::atomic_write_text(&cache, r#"[{"bus": 4, "max": 100}]"#);
            let config: AppConfig = toml::from_str(r#"
[[pages]]
id = "home"
title = "Home"
[[pages.layout]]
type = "controls"
[[pages.layout.items]]
type = "slider"
[pages.layout.items.properties]
title = "Brightness"
min = 1.0
max = 100.0
[pages.layout.items.on_change]
type = "exec"
command = "brightnessctl set {value}%"
"#).unwrap();
            let (mut app, _) = CenterApp::new(config, None, None);
            let action = app.config.pages[0].layout[0].items[0].on_change.clone();
            let task = app.update(Message::SliderChanged {
                key: "Brightness".into(),
                value: 65.0,
                on_change: action,
                debounce: false,
            });
            complete(&mut app, task);
            assert!(app.action_error.is_none(), "DDC fallback should succeed: {:?}", app.action_error);
            assert_eq!(
                std::fs::read_to_string(root.join("ddc-calls")).unwrap(),
                "setvcp 10 65 --bus 4\n"
            );
            return;
        }
        use std::os::unix::fs::PermissionsExt;
        let root = std::env::temp_dir().join(format!("dusky-ddc-fallback-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let bin = root.join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        std::os::unix::fs::symlink("/usr/bin/bash", bin.join("bash")).unwrap();
        let brightnessctl = bin.join("brightnessctl");
        std::fs::write(&brightnessctl, "#!/usr/bin/bash\nprintf 'No backlight' >&2\nexit 1\n").unwrap();
        std::fs::set_permissions(&brightnessctl, std::fs::Permissions::from_mode(0o755)).unwrap();
        let ddcutil = bin.join("ddcutil");
        std::fs::write(
            &ddcutil,
            "#!/usr/bin/bash\nprintf '%s\\n' \"$*\" >> \"$DUSKY_CENTER_DDC_FALLBACK_FIXTURE/ddc-calls\"\n",
        )
        .unwrap();
        std::fs::set_permissions(&ddcutil, std::fs::Permissions::from_mode(0o755)).unwrap();

        let result = std::process::Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "ui::tests::default_brightness_action_falls_back_to_ddc_when_local_backlight_fails",
                "--nocapture",
            ])
            .env(CHILD, &root)
            .env("XDG_RUNTIME_DIR", &root)
            .env("PATH", &bin)
            .output()
            .unwrap();
        let _ = std::fs::remove_dir_all(&root);
        assert!(
            result.status.success(),
            "{}{}",
            String::from_utf8_lossy(&result.stdout),
            String::from_utf8_lossy(&result.stderr)
        );
    }

    #[test]
    fn transitions_activate_on_navigation_and_settle_to_zero_idle_animations() {
        let config: AppConfig = toml::from_str(r#"
[[pages]]
id = "first"
title = "First"
[[pages.layout]]
type = "section"
[pages.layout.properties]
title = "Main"
[[pages.layout.items]]
type = "navigation"
[pages.layout.items.properties]
title = "Sub"
[[pages.layout.items.layout]]
type = "section"
[[pages.layout.items.layout.items]]
type = "expander"
[pages.layout.items.layout.items.properties]
title = "Details"
[[pages.layout.items.layout.items.items]]
type = "label"
[pages.layout.items.layout.items.items.properties]
title = "Inner"
[pages.layout.items.layout.items.items.value]
type = "static"
text = "ok"

[[pages]]
id = "second"
title = "Second"
"#).unwrap();
        let (mut app, _) = CenterApp::new(config, None, None);
        assert!(!app.has_active_animations(), "cold boot must start with zero active animations");

        // PushSubPage starts Push glide.
        let sub_sections = app.config.pages[0].layout[0].items[0].layout.clone();
        let _ = app.update(Message::PushSubPage {
            title: "Sub".into(),
            occurrence: 0,
            sections: sub_sections,
        });
        assert!(app.has_active_animations());
        assert_eq!(app.nav_transition.unwrap().direction, NavDirection::Push);

        // Expander open animates and retargets cleanly; settles to idle.
        let _ = app.update(Message::ToggleItem {
            key: "expander:Details".into(),
            is_enabled: true,
            on_toggle: None,
        });
        assert!(app.expander_anims.contains_key("expander:Details"));
        let _ = app.view();

        let after_settle = app.animation_now + Duration::from_millis(250);
        let _ = app.update(Message::AnimationFrame(after_settle));
        assert!(!app.has_active_animations(), "all animations must stop after settling");
        assert!(app.nav_transition.is_none());
        assert!(app.expander_anims.is_empty());

        // PopSubPage starts Pop glide and clears expander_anims.
        let _ = app.update(Message::ToggleItem {
            key: "expander:Details".into(),
            is_enabled: false,
            on_toggle: None,
        });
        assert!(!app.expander_anims.is_empty());
        let _ = app.update(Message::PopSubPage);
        assert_eq!(app.nav_transition.unwrap().direction, NavDirection::Pop);
        assert!(app.expander_anims.is_empty());

        // Root page switch uses directional PageDown / PageUp.
        let _ = app.update(Message::SelectPage(1));
        assert_eq!(app.nav_transition.unwrap().direction, NavDirection::PageDown);
        let _ = app.update(Message::SelectPage(0));
        assert_eq!(app.nav_transition.unwrap().direction, NavDirection::PageUp);

        // Sidebar and search bar animate smoothly and settle cleanly.
        let _ = app.update(Message::ToggleSidebar);
        let _ = app.update(Message::ToggleSearch);
        assert!(app.has_active_animations());
        let _ = app.view();
        let later = app.animation_now + Duration::from_millis(300);
        let _ = app.update(Message::AnimationFrame(later));
        assert!(!app.has_active_animations());
        assert_eq!(app.sidebar_anim.value(later), 0.0);
        assert_eq!(app.search_anim.value(later), 1.0);

        // OpenSearchResult snaps search bar closed and ancestor expanders open while sliding.
        let loc = build_search_cache(&app.config, "Inner").1[0].location.clone();
        let _ = app.update(Message::OpenSearchResult(loc));
        assert_eq!(app.search_anim.value(app.animation_now), 0.0);
        assert!(!app.search_anim.is_active(app.animation_now));
        assert!(app.expander_anims.is_empty());
        assert_eq!(app.toggle_states.get("expander:Details"), Some(&true));
        assert_eq!(app.nav_transition.unwrap().direction, NavDirection::Push);
        assert!(app.has_active_animations());

        let finish = app.animation_now + Duration::from_millis(1300);
        let _ = app.update(Message::AnimationFrame(finish));
        assert!(!app.has_active_animations());
    }

    #[test]
    fn reload_stays_calm_in_place_and_pops_only_on_subpage_fallback() {
        let root = std::env::temp_dir().join(format!("dusky-reload-anim-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).unwrap();
        let cfg_path = root.join("anim.toml");

        let with_sub = r#"
[[pages]]
id = "p"
title = "Page"
[[pages.layout]]
type = "section"
[[pages.layout.items]]
type = "navigation"
[pages.layout.items.properties]
title = "Child"
[[pages.layout.items.layout]]
type = "section"
[[pages.layout.items.layout.items]]
type = "label"
[pages.layout.items.layout.items.properties]
title = "Row"
"#;
        std::fs::write(&cfg_path, with_sub).unwrap();
        let loaded = AppConfig::load_resolved(Some(&cfg_path)).unwrap();
        let (mut app, _) = CenterApp::new_loaded(loaded, Some(cfg_path.clone()), None, None);

        let sub_sections = app.config.pages[0].layout[0].items[0].layout.clone();
        let _ = app.update(Message::PushSubPage {
            title: "Child".into(),
            occurrence: 0,
            sections: sub_sections,
        });
        let settle = app.animation_now + Duration::from_millis(300);
        let _ = app.update(Message::AnimationFrame(settle));
        assert!(app.nav_transition.is_none());

        // In-place reload keeps view calm.
        let _ = app.update(Message::ReloadConfig);
        assert!(app.nav_transition.is_none());

        // Removing the active subpage triggers a gentle Pop fallback transition.
        std::fs::write(&cfg_path, "[[pages]]\nid = 'p'\ntitle = 'Page'\n").unwrap();
        let _ = app.update(Message::ReloadConfig);
        assert_eq!(app.nav_transition.unwrap().direction, NavDirection::Pop);

        let _ = std::fs::remove_dir_all(&root);
    }
}
