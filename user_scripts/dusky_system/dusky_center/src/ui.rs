//! Dusky Center UI: Pure Rust native control center for Arch Linux / Hyprland.
//!
//! Visual parity with Dusky Control Center (GTK) and Dusky Tray using Iced and wgpu.
//! Features:
//! - 3-column quick hero cards with uniform geometry and solid accent highlights.
//! - Sub-15ms cold start with lazy-loading for background pages.
//! - Native Wayland xdg-toplevel windowing (movable, resizable, tileable).
//! - Dynamic wallpaper theme sync via Matugen.
//! - Dusky Tray slider design with pill-shaped rails and white handles.
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
use std::time::Duration;

use crate::backend::cmd::{execute_detached, execute_shell_detached};
use crate::backend::system as sys;
use crate::config::{
    ActionConfig, AppConfig, ChangeAction, ItemConfig, PageConfig, SectionConfig, ToggleActionPair,
};
use crate::icons::render_icon;
use crate::theme::AppTheme;

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
    DragWindow,
    Tick,
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
}

impl CenterApp {
    pub fn new(
        config: AppConfig,
        initial_page: Option<String>,
        initial_hover: Option<String>,
    ) -> (Self, Task<Message>) {
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
        };

        // LAZY LOADING: Only initialize active page on cold start!
        app.lazy_load_page(start_page);
        app.poll_core_system_states();

        (app, Task::none())
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

    /// Poll core hardware states needed on home screen and quick tiles.
    fn poll_core_system_states(&mut self) {
        if let Some(wifi) = sys::get_wifi() {
            self.toggle_states.insert("Wi-Fi".into(), wifi);
            self.toggle_states.insert("wlan".into(), wifi);
        }
        if let Some(bt) = sys::get_bt() {
            self.toggle_states.insert("Bluetooth".into(), bt);
        }

        // Dark mode state check
        if let Ok(dark_val) =
            std::fs::read_to_string(dirs_fallback().join("settings/dusky_theme/state"))
        {
            let is_dark = dark_val.trim() == "true" || dark_val.trim() == "1";
            self.toggle_states.insert("Dark Mode".into(), is_dark);
            self.toggle_states.insert("dusky_theme/state".into(), is_dark);
        }

        // Live volume & brightness & night light
        if let Some(vol) = sys::get_volume() {
            self.slider_values.insert("Volume".into(), vol);
        }
        if let Some(bri) = sys::get_brightness() {
            self.slider_values.insert("Brightness".into(), bri);
        }
        if let Some(sunset) = sys::get_sunset() {
            self.slider_values.insert("Night Light".into(), sunset);
        }

        // TLP active power profile
        let tlp_cmd = dirs_home().join("user_scripts/battery/tlp/tlp_mode_toggle.sh");
        if tlp_cmd.is_file()
            && let Ok(out) = std::process::Command::new("bash")
                .arg(tlp_cmd)
                .arg("status")
                .output()
        {
            let s = String::from_utf8_lossy(&out.stdout).trim().to_lowercase();
            self.active_profile = match s.as_str() {
                "performance" => "Performance".into(),
                "power-saver" | "powersave" => "Power Saver".into(),
                _ => "Balanced".into(),
            };
        }

        // Kernel release
        if let Ok(osrelease) = std::fs::read_to_string("/proc/sys/kernel/osrelease") {
            self.live_labels.insert("Kernel".into(), format!("Linux {}", osrelease.trim()));
        }

        self.live_labels.insert("Memory Used".into(), self.ram_text.clone());
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
                }
                Task::none()
            }

            Message::PushSubPage { title, sections } => {
                let parent = self.active_page_idx();
                self.nav_stack.push(NavView::SubPage {
                    parent_page: parent,
                    title,
                    sections,
                });
                self.search_query.clear();
                Task::none()
            }

            Message::PopSubPage => {
                if self.nav_stack.len() > 1 {
                    self.nav_stack.pop();
                }
                Task::none()
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

            Message::ExecuteAction(action) => {
                self.dispatch_action(&action);
                Task::none()
            }

            Message::ToggleItem {
                key,
                is_enabled,
                on_toggle,
            } => {
                self.toggle_states.insert(key, is_enabled);
                if let Some(pair) = on_toggle {
                    let action = if is_enabled {
                        &pair.enabled
                    } else {
                        &pair.disabled
                    };
                    self.dispatch_action(action);
                }
                Task::none()
            }

            Message::SliderChanged { key, value } => {
                self.slider_values.insert(key.clone(), value);
                if key == "Night Light" {
                    sys::apply_sunset(value);
                }
                Task::none()
            }

            Message::SliderReleased {
                key,
                value,
                on_change,
            } => {
                self.slider_values.insert(key.clone(), value);
                if key == "Night Light" {
                    sys::apply_sunset(value);
                } else if let Some(ChangeAction::Direct(action)) = on_change {
                    self.dispatch_action_with_value(&action, value);
                }
                Task::none()
            }

            Message::ProfileSelected(profile) => {
                self.active_profile = profile.clone();
                let cmd_arg = match profile.as_str() {
                    "Performance" => "performance",
                    "Power Saver" => "power-saver",
                    _ => "balanced",
                };
                let script = dirs_home().join("user_scripts/battery/tlp/tlp_mode_toggle.sh");
                if script.is_file() {
                    let cmd = format!("{} {}", script.display(), cmd_arg);
                    execute_shell_detached(&cmd);
                }
                Task::none()
            }

            Message::SelectOption {
                key,
                option,
                action,
            } => {
                self.selected_options.insert(key, option);
                if let Some(act) = action {
                    self.dispatch_action(&act);
                }
                Task::none()
            }

            Message::ToggleService { unit, scope } => {
                let current = self.service_states.get(&unit).copied().unwrap_or(false);
                let new_state = !current;
                self.service_states.insert(unit.clone(), new_state);
                let verb = if new_state { "start" } else { "stop" };
                let cmd = if scope == "user" {
                    format!("systemctl --user {verb} {unit}")
                } else {
                    format!("pkexec systemctl {verb} {unit}")
                };
                execute_shell_detached(&cmd);
                Task::none()
            }

            Message::DragWindow => iced::window::drag(iced::window::Id::unique()),

            Message::Tick => {
                let (cpu, ram) = sys::cpu_ram();
                self.cpu_text = cpu;
                self.ram_text = ram;
                self.poll_core_system_states();
                // Dynamically sync theme palette with wallpaper
                self.theme = AppTheme::load();
                Task::none()
            }

            Message::CloseApp => iced::exit(),

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
                self.theme = AppTheme::load();
                if let Ok(cfg) = AppConfig::load() {
                    self.config = cfg;
                    self.loaded_pages.clear();
                    self.lazy_load_page(0);
                    self.poll_core_system_states();
                }
                Task::none()
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
                        self.nav_stack.pop();
                        return Task::none();
                    }
                    return iced::exit();
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

    fn dispatch_action(&self, action: &ActionConfig) {
        match action {
            ActionConfig::Exec {
                command,
                terminal,
                requires_root,
                ..
            } => {
                let mut cmd = command.clone();
                if *requires_root && !cmd.starts_with("pkexec") {
                    cmd = format!("pkexec {cmd}");
                }
                if *terminal {
                    cmd = format!("kitty -e bash -c '{cmd}'");
                }
                execute_shell_detached(&cmd);
            }
            ActionConfig::Argv {
                argv,
                terminal,
                requires_root,
                ..
            } => {
                let mut final_argv = argv.clone();
                if *requires_root {
                    final_argv.insert(0, "pkexec".into());
                }
                let full = final_argv.join(" ");
                if *terminal {
                    execute_shell_detached(&format!("kitty -e bash -c '{full}'"));
                } else {
                    execute_detached(&full);
                }
            }
            ActionConfig::Redirect { page } => {
                println!("Redirecting to page: {page}");
            }
        }
    }

    fn dispatch_action_with_value(&self, action: &ActionConfig, value: f32) {
        match action {
            ActionConfig::Exec { command, .. } => {
                let val_str = format!("{}", value.round() as i64);
                let substituted = command
                    .replace("{value}", &val_str)
                    .replace("$VALUE", &val_str);
                execute_shell_detached(&substituted);
            }
            ActionConfig::Argv { argv, .. } => {
                let val_str = format!("{}", value.round() as i64);
                let substituted: Vec<String> = argv
                    .iter()
                    .map(|a| a.replace("{value}", &val_str).replace("$VALUE", &val_str))
                    .collect();
                let full = substituted.join(" ");
                execute_detached(&full);
            }
            _ => self.dispatch_action(action),
        }
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
            text("Dusky")
                .size(15)
                .font(iced::Font {
                    weight: Weight::Bold,
                    ..Default::default()
                })
                .color(palette.fg),
            Space::new().width(Length::Fill),
            search_btn,
        ]
        .padding([10, 10])
        .align_y(Vertical::Center);

        let mut page_list = column![].spacing(2);

        for (idx, page) in self.config.pages.iter().enumerate() {
            let is_active = idx == active_idx && self.search_query.is_empty();
            let icon_name = if !page.icon.is_empty() {
                &page.icon
            } else {
                &page.id
            };

            // Selected item: Solid coral/peach pill background with DARK text & icon!
            let (fg_color, icon_color) = if is_active {
                (palette.accent_fg, palette.accent_fg)
            } else {
                (palette.fg, palette.accent)
            };

            let row_content = row![
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
            .spacing(9)
            .align_y(Vertical::Center);

            let btn = button(container(row_content).padding([5, 8]).width(Length::Fill))
                .on_press(Message::SelectPage(idx))
                .style(move |_, status| {
                    let bg = if is_active {
                        palette.accent
                    } else if status == button::Status::Hovered {
                        Color::from_rgba(palette.accent.r, palette.accent.g, palette.accent.b, 0.08)
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

        // Window close button (Sleek circle, perfectly centered 12px cross)
        let close_btn = button(
            container(render_icon("close", 12.0, Color::from_rgb(0.92, 0.92, 0.92)))
                .width(Length::Fill)
                .height(Length::Fill)
                .align_x(Horizontal::Center)
                .align_y(Vertical::Center),
        )
        .on_press(Message::CloseApp)
        .padding(0)
        .width(26)
        .height(26)
        .style(move |_, status| {
            let is_hovered = status == button::Status::Hovered;
            button::Style {
                background: Some(
                    if is_hovered {
                        Color::from_rgba(0.92, 0.28, 0.28, 0.90)
                    } else {
                        Color::from_rgba(1.0, 1.0, 1.0, 0.06)
                    }
                    .into(),
                ),
                border: Border {
                    radius: 13.0.into(),
                    ..Default::default()
                },
                ..Default::default()
            }
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
        .padding([12, 18]);

        let mut main_col = column![mouse_area(main_header).on_press(Message::DragWindow)];

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
                    .style(|_, status| button::Style {
                        background: Some(
                            if status == button::Status::Hovered {
                                Color::from_rgba(1.0, 1.0, 1.0, 0.1).into()
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
                    .max_width(520.0),
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
                        let item_view = self.view_item_row(item, &page.title, Some(page_idx));
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
            .padding([2, 10])
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

        column![
            header,
            make_slim_scrollable(
                column![results_card],
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

        make_slim_scrollable(
            sections_col,
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

        make_slim_scrollable(
            sections_col,
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

            let capped_grid = container(grid_rows)
                .width(Length::Fill)
                .max_width(520.0);

            return container(capped_grid)
                .width(Length::Fill)
                .align_x(Horizontal::Center)
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

        // Avoid rendering an empty container card if a section has 0 items
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

        // Special handling for Power Management (with PickList dropdown)
        if section.properties.title == "Power Management" {
            sec_col = sec_col.push(self.view_power_management_card(section));
            return sec_col.into();
        }

        // Special handling for Quick Controls (with Dusky Tray sliders)
        if section.properties.title == "Quick Controls" {
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
            items_box = items_box.push(self.view_item_row(item, "", None));
        }

        let container_card = container(items_box)
            .padding([2, 10])
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

        let key = if !item.properties.key.is_empty() {
            item.properties.key.clone()
        } else {
            item.properties.title.clone()
        };

        let is_enabled = self.toggle_states.get(&key).copied().unwrap_or(false);

        // Resolve title & button text mappings (e.g. Dusky "0" -> "Updated")
        let mut display_title = item.properties.title.clone();
        if !item.properties.button_text_file.is_empty() {
            let expanded = expand_path(&item.properties.button_text_file);
            if let Ok(content) = std::fs::read_to_string(expanded) {
                let trimmed = content.trim();
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

        let is_destructive = item.properties.style == "destructive"
            || item.properties.title == "Reload CC"
            || item.properties.title == "Power"
            || item.properties.title == "Reboot";
        let is_suggested = item.properties.style == "suggested";

        // Hero card styling: Active -> solid accent; Destructive -> reddish tint; Standard -> card_bg
        let (card_bg, text_fg, icon_fg, border_color) = if is_enabled {
            (
                palette.accent,
                palette.accent_fg,
                palette.accent_fg,
                palette.accent,
            )
        } else if is_destructive {
            (
                Color::from_rgba(palette.danger.r, palette.danger.g, palette.danger.b, 0.05),
                palette.danger,
                palette.danger,
                Color::from_rgba(palette.danger.r, palette.danger.g, palette.danger.b, 0.28),
            )
        } else if is_suggested {
            (
                Color::from_rgba(palette.accent.r, palette.accent.g, palette.accent.b, 0.06),
                palette.fg,
                palette.accent,
                Color::from_rgba(palette.accent.r, palette.accent.g, palette.accent.b, 0.25),
            )
        } else {
            (
                palette.card_bg,
                palette.fg,
                palette.accent,
                Color::from_rgba(palette.border.r, palette.border.g, palette.border.b, 0.30),
            )
        };

        let card_content = column![
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

        let on_press_msg = if is_toggle {
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

        // Uniform 62px fixed height guarantees all rows have the exact same size!
        button(
            container(card_content)
                .padding([6, 6])
                .width(Length::Fill)
                .height(Length::Fixed(62.0))
                .align_x(Horizontal::Center)
                .align_y(Vertical::Center),
        )
        .on_press(on_press_msg)
        .style(move |_, status| {
            let (bg, b_color) = if is_enabled {
                if status == button::Status::Hovered {
                    (palette.accent_hover, palette.accent_hover)
                } else {
                    (palette.accent, palette.accent)
                }
            } else if is_destructive {
                if status == button::Status::Hovered {
                    (
                        Color::from_rgba(palette.danger.r, palette.danger.g, palette.danger.b, 0.12),
                        Color::from_rgba(palette.danger.r, palette.danger.g, palette.danger.b, 0.45),
                    )
                } else {
                    (card_bg, border_color)
                }
            } else if is_suggested {
                if status == button::Status::Hovered {
                    (
                        Color::from_rgba(palette.accent.r, palette.accent.g, palette.accent.b, 0.14),
                        Color::from_rgba(palette.accent.r, palette.accent.g, palette.accent.b, 0.40),
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
                ..Default::default()
            }
        })
        .width(Length::FillPortion(1))
        .height(Length::Fixed(62.0))
        .into()
    }

    // ---------------------------------------------------------------------------
    // Power Management Card (Icon Badge + Interactive PickList Dropdown)
    // ---------------------------------------------------------------------------

    fn view_power_management_card<'a>(&'a self, _section: &'a SectionConfig) -> Element<'a, Message> {
        let palette = self.theme;
        let is_hovered = self.hovered_item.as_deref() == Some("Active Profile");

        let (icon_badge_bg, icon_badge_border, icon_color) = if is_hovered {
            (
                Color::from_rgba(palette.accent.r, palette.accent.g, palette.accent.b, 0.22),
                Color::from_rgba(palette.accent.r, palette.accent.g, palette.accent.b, 0.40),
                palette.accent_hover,
            )
        } else {
            (
                Color::from_rgba(palette.accent.r, palette.accent.g, palette.accent.b, 0.12),
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
                    weight: Weight::Bold,
                    ..Default::default()
                })
                .color(if is_hovered { Color::WHITE } else { palette.fg }),
            text("Select performance mode")
                .size(11)
                .color(if is_hovered { Color::from_rgb(0.85, 0.85, 0.88) } else { palette.fg_muted }),
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
    // Quick Controls Card (Borrowed from Dusky Tray: 12px Pill Rails & White Handle)
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

            let is_vol = key.contains("Volume") || item.properties.icon.contains("volume");
            let icon_name = if is_vol { "audio" } else { "brightness" };

            let min = item.properties.min.unwrap_or(0.0) as f32;
            let max = item.properties.max.unwrap_or(100.0) as f32;
            let step = item.properties.step.unwrap_or(1.0) as f32;
            let val = self.slider_values.get(&key).copied().unwrap_or(50.0);

            let key_c1 = key.clone();
            let key_c2 = key.clone();
            let on_change = item.on_change.clone();

            let is_row_hovered = self.hovered_item.as_deref() == Some(&key);

            let (icon_badge_bg, icon_badge_border, icon_color) = if is_row_hovered {
                (
                    Color::from_rgba(palette.accent.r, palette.accent.g, palette.accent.b, 0.22),
                    Color::from_rgba(palette.accent.r, palette.accent.g, palette.accent.b, 0.40),
                    palette.accent_hover,
                )
            } else {
                (
                    Color::from_rgba(palette.accent.r, palette.accent.g, palette.accent.b, 0.12),
                    Color::TRANSPARENT,
                    palette.accent,
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

            // Dusky Tray Sliders: 12px thick rounded pill rail with solid accent fill and crisp white circle handle
            let s = slider(min..=max, val, move |v| Message::SliderChanged {
                key: key_c1.clone(),
                value: v,
            })
            .step(step)
            .height(24)
            .width(Length::Fill)
            .on_release(Message::SliderReleased {
                key: key_c2,
                value: val,
                on_change,
            })
            .style(move |_, _| slider::Style {
                rail: slider::Rail {
                    backgrounds: (
                        palette.accent.into(),
                        Color::from_rgba(1.0, 1.0, 1.0, 0.09).into(),
                    ),
                    width: 12.0,
                    border: Border {
                        radius: 6.0.into(),
                        ..Default::default()
                    },
                },
                handle: slider::Handle {
                    shape: slider::HandleShape::Circle { radius: 7.0 },
                    background: Color::WHITE.into(),
                    border_width: 0.0,
                    border_color: Color::TRANSPARENT,
                },
            });

            // Monospace numeric value on right
            let val_label = text(format!("{:.0}", val.round()))
                .size(13)
                .font(iced::Font::MONOSPACE)
                .color(if is_row_hovered { Color::WHITE } else { palette.fg_muted })
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

        // 3rd slider on Quick Controls for Night Light when hyprsunset service is active
        if sys::is_sunset_active() {
            let key = "Night Light".to_string();
            let val = self
                .slider_values
                .get(&key)
                .copied()
                .or_else(sys::get_sunset)
                .unwrap_or(0.0);

            let key_c1 = key.clone();
            let key_c2 = key.clone();

            let is_row_hovered = self.hovered_item.as_deref() == Some("Night Light");

            let (icon_badge_bg, icon_badge_border, icon_color) = if is_row_hovered {
                (
                    Color::from_rgba(palette.accent.r, palette.accent.g, palette.accent.b, 0.22),
                    Color::from_rgba(palette.accent.r, palette.accent.g, palette.accent.b, 0.40),
                    palette.accent_hover,
                )
            } else {
                (
                    Color::from_rgba(palette.accent.r, palette.accent.g, palette.accent.b, 0.12),
                    Color::TRANSPARENT,
                    palette.accent,
                )
            };

            let icon_badge = container(render_icon("weather-clear-night-symbolic", 18.0, icon_color))
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

            let s = slider(0.0..=100.0, val, move |v| Message::SliderChanged {
                key: key_c1.clone(),
                value: v,
            })
            .step(1.0_f32)
            .height(24)
            .width(Length::Fill)
            .on_release(Message::SliderReleased {
                key: key_c2,
                value: val,
                on_change: None,
            })
            .style(move |_, _| slider::Style {
                rail: slider::Rail {
                    backgrounds: (
                        palette.accent.into(),
                        Color::from_rgba(1.0, 1.0, 1.0, 0.09).into(),
                    ),
                    width: 12.0,
                    border: Border {
                        radius: 6.0.into(),
                        ..Default::default()
                    },
                },
                handle: slider::Handle {
                    shape: slider::HandleShape::Circle { radius: 7.0 },
                    background: Color::WHITE.into(),
                    border_width: 0.0,
                    border_color: Color::TRANSPARENT,
                },
            });

            let val_label = text(format!("{:.0}", val.round()))
                .size(13)
                .font(iced::Font::MONOSPACE)
                .color(if is_row_hovered { Color::WHITE } else { palette.fg_muted })
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
        let palette = self.theme;
        let title = &item.properties.title;
        let desc = &item.properties.description;

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

        // Title and description colors brighten on hover
        let title_color = if is_row_hovered {
            Color::WHITE
        } else {
            palette.fg
        };
        let desc_color = if is_row_hovered {
            Color::from_rgb(0.85, 0.85, 0.88)
        } else {
            palette.fg_muted
        };

        // Left info container with title and description
        let mut info_col = column![row![
            text(title)
                .size(13)
                .font(iced::Font {
                    weight: Weight::Semibold,
                    ..Default::default()
                })
                .color(title_color),
            if !badge_tag.is_empty() {
                container(text(badge_tag).size(10).color(palette.accent))
                    .padding([2, 8])
                    .style(move |_| container::Style {
                        background: Some(
                            Color::from_rgba(palette.accent.r, palette.accent.g, palette.accent.b, 0.15).into(),
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
            info_col = info_col.push(text(desc).size(11).color(desc_color));
        }

        // Icon badge lights up brighter with accent glow border on hover
        let (icon_badge_bg, icon_badge_border, icon_color) = if is_row_hovered {
            (
                Color::from_rgba(palette.accent.r, palette.accent.g, palette.accent.b, 0.22),
                Color::from_rgba(palette.accent.r, palette.accent.g, palette.accent.b, 0.40),
                palette.accent_hover,
            )
        } else {
            (
                Color::from_rgba(palette.accent.r, palette.accent.g, palette.accent.b, 0.10),
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

        // Helper action buttons (e.g. edit config, reset, launch tool)
        let mut buttons_row = row![].spacing(6).align_y(Vertical::Center);
        for b in &item.properties.buttons {
            let btn_icon = if !b.icon.is_empty() {
                &b.icon
            } else {
                "settings"
            };
            let is_suggested = b.style == "suggested";
            let mut btn = button(render_icon(
                btn_icon,
                14.0,
                if is_suggested {
                    palette.accent
                } else {
                    palette.fg
                },
            ))
            .padding([5, 8])
            .style(move |_, status| button::Style {
                background: Some(
                    if status == button::Status::Hovered {
                        palette.card_hover
                    } else if is_suggested {
                        Color::from_rgba(palette.accent.r, palette.accent.g, palette.accent.b, 0.12)
                    } else {
                        Color::from_rgba(1.0, 1.0, 1.0, 0.05)
                    }
                    .into(),
                ),
                border: Border {
                    color: if is_suggested {
                        palette.accent
                    } else {
                        palette.border
                    },
                    width: 1.0,
                    radius: 6.0.into(),
                },
                ..Default::default()
            });

            if let Some(action) = &b.on_press {
                btn = btn.on_press(Message::ExecuteAction(action.clone()));
            }
            buttons_row = buttons_row.push(btn);
        }

        // Multi-option selections for Voice Character Preset
        // Render title/description on top and flow wrapped pills underneath indented by 46px
        if item.item_type == "selection" && item.properties.title == "Voice Character Preset" {
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
                        Color::from_rgba(1.0, 1.0, 1.0, 0.08)
                    } else {
                        Color::from_rgba(1.0, 1.0, 1.0, 0.03)
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
                .padding([8, 6])
                .width(Length::Fill)
                .style(move |_| container::Style {
                    background: Some(Color::TRANSPARENT.into()),
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
        let right_widget: Element<'a, Message> = match item.item_type.as_str() {
            "toggle" => {
                let is_active = self.toggle_states.get(&key).copied().unwrap_or(false);
                let toggle_pair = item.on_toggle.clone();
                let key_clone = key.clone();
                toggler(is_active)
                    .on_toggle(move |val| Message::ToggleItem {
                        key: key_clone.clone(),
                        is_enabled: val,
                        on_toggle: toggle_pair.clone(),
                    })
                    .size(20)
                    .style(move |_, _status| toggler::Style {
                        background: if is_active {
                            palette.accent.into()
                        } else {
                            Color::from_rgba(1.0, 1.0, 1.0, 0.1).into()
                        },
                        background_border_width: 0.0,
                        background_border_color: Color::TRANSPARENT,
                        foreground: if is_active {
                            palette.accent_fg.into()
                        } else {
                            palette.fg_muted.into()
                        },
                        foreground_border_width: 0.0,
                        foreground_border_color: Color::TRANSPARENT,
                        text_color: None,
                        border_radius: None,
                        padding_ratio: 0.1,
                    })
                    .into()
            }

            "slider" | "spin" => {
                let min = item.properties.min.unwrap_or(0.0) as f32;
                let max = item.properties.max.unwrap_or(100.0) as f32;
                let step = item.properties.step.unwrap_or(1.0) as f32;
                let val = self.slider_values.get(&key).copied().unwrap_or(min);

                let key_c1 = key.clone();
                let key_c2 = key.clone();
                let on_change = item.on_change.clone();

                let s = slider(min..=max, val, move |v| Message::SliderChanged {
                    key: key_c1.clone(),
                    value: v,
                })
                .step(step)
                .height(24)
                .width(130)
                .on_release(Message::SliderReleased {
                    key: key_c2,
                    value: val,
                    on_change,
                })
                .style(move |_, _| slider::Style {
                    rail: slider::Rail {
                        backgrounds: (
                            palette.accent.into(),
                            Color::from_rgba(1.0, 1.0, 1.0, 0.09).into(),
                        ),
                        width: 10.0,
                        border: Border {
                            radius: 5.0.into(),
                            ..Default::default()
                        },
                    },
                    handle: slider::Handle {
                        shape: slider::HandleShape::Circle { radius: 6.0 },
                        background: Color::WHITE.into(),
                        border_width: 0.0,
                        border_color: Color::TRANSPARENT,
                    },
                });

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
                let sub_sections = item.layout.clone();
                let sub_title = title.clone();

                button(
                    row![
                        text("Open").size(11).color(palette.accent),
                        render_icon("chevron_right", 14.0, palette.accent),
                    ]
                    .spacing(4)
                    .align_y(Vertical::Center),
                )
                .on_press(Message::PushSubPage {
                    title: sub_title,
                    sections: sub_sections,
                })
                .padding([5, 10])
                .style(move |_, status| button::Style {
                    background: Some(
                        if status == button::Status::Hovered {
                            Color::from_rgba(palette.accent.r, palette.accent.g, palette.accent.b, 0.2)
                        } else {
                            Color::from_rgba(palette.accent.r, palette.accent.g, palette.accent.b, 0.08)
                        }
                        .into(),
                    ),
                    border: Border {
                        radius: 6.0.into(),
                        ..Default::default()
                    },
                    ..Default::default()
                })
                .into()
            }

            "service" => {
                let unit = item.properties.service.clone();
                let scope = if item.properties.scope.is_empty() {
                    "user".to_string()
                } else {
                    item.properties.scope.clone()
                };
                let is_active = self.service_states.get(&unit).copied().unwrap_or(false);

                let btn_text = if is_active { "Active" } else { "Start" };
                button(text(btn_text).size(11).color(palette.fg))
                    .on_press(Message::ToggleService { unit, scope })
                    .padding([5, 12])
                    .style(move |_, _| {
                        let bg = if is_active {
                            Color::from_rgba(0.2, 0.7, 0.35, 0.4)
                        } else {
                            Color::from_rgba(1.0, 1.0, 1.0, 0.06)
                        };
                        button::Style {
                            background: Some(bg.into()),
                            border: Border {
                                color: if is_active {
                                    Color::from_rgb(0.25, 0.8, 0.4)
                                } else {
                                    palette.border
                                },
                                width: 1.0,
                                radius: 6.0.into(),
                            },
                            ..Default::default()
                        }
                    })
                    .into()
            }

            "label" => {
                let label_val = self
                    .live_labels
                    .get(title)
                    .cloned()
                    .unwrap_or_else(|| "Active".into());

                container(text(label_val).size(12).color(palette.fg))
                    .padding([4, 10])
                    .style(move |_| container::Style {
                        background: Some(Color::from_rgba(1.0, 1.0, 1.0, 0.05).into()),
                        border: Border {
                            radius: 6.0.into(),
                            ..Default::default()
                        },
                        ..Default::default()
                    })
                    .into()
            }

            _ => {
                if let Some(action) = &item.on_press {
                    let act = action.clone();
                    button(text("Run").size(11).color(palette.fg))
                        .on_press(Message::ExecuteAction(act))
                        .padding([5, 14])
                        .style(move |_, status| button::Style {
                            background: Some(
                                if status == button::Status::Hovered {
                                    palette.accent
                                } else {
                                    Color::from_rgba(1.0, 1.0, 1.0, 0.06)
                                }
                                .into(),
                            ),
                            border: Border {
                                color: if status == button::Status::Hovered {
                                    palette.accent
                                } else {
                                    Color::from_rgba(
                                        palette.border.r,
                                        palette.border.g,
                                        palette.border.b,
                                        0.30,
                                    )
                                },
                                width: 1.0,
                                radius: 6.0.into(),
                            },
                            ..Default::default()
                        })
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

        let primary_action = if let Some(action) = &item.on_press {
            Some(action.clone())
        } else if let Some(suggested) = item
            .properties
            .buttons
            .iter()
            .find(|b| b.style == "suggested" && b.on_press.is_some())
        {
            suggested.on_press.clone()
        } else if let Some(last) = item
            .properties
            .buttons
            .iter()
            .rev()
            .find(|b| b.on_press.is_some())
        {
            last.on_press.clone()
        } else {
            None
        };

        let row_container = container(row_content)
            .padding([8, 6])
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
                    radius: 8.0.into(),
                    ..Default::default()
                },
                ..Default::default()
            });

        let key_enter = key.clone();
        let key_exit = key.clone();
        let mut area = mouse_area(row_container)
            .on_enter(Message::ItemEntered(key_enter))
            .on_exit(Message::ItemExited(key_exit));

        if item.item_type == "navigation" {
            let sub_sections = item.layout.clone();
            let sub_title = title.clone();
            area = area
                .on_press(Message::PushSubPage {
                    title: sub_title,
                    sections: sub_sections,
                })
                .interaction(mouse::Interaction::Pointer);
        } else if item.item_type == "toggle" {
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
        } else if (item.item_type == "button" || item.item_type.is_empty())
            && let Some(act) = primary_action
        {
            area = area
                .on_press(Message::ExecuteAction(act))
                .interaction(mouse::Interaction::Pointer);
        }

        area.into()
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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

    if item.item_type == "toggle" || item.item_type == "toggle_card" {
        toggles.entry(key.clone()).or_insert(false);
    }

    if !item.properties.service.is_empty() {
        services
            .entry(item.properties.service.clone())
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

        // 2. If not found, check value_command
        if initial.is_none() && !item.properties.value_command.is_empty() {
            let cmd = expand_path(&item.properties.value_command);
            if let Ok(out) = std::process::Command::new("bash")
                .arg("-c")
                .arg(cmd.to_string_lossy().as_ref())
                .output()
            {
                let stdout = String::from_utf8_lossy(&out.stdout).trim().to_string();
                let lower = stdout.to_lowercase();
                if let Some(mapped) = item
                    .properties
                    .options_map
                    .get(&stdout)
                    .or_else(|| item.properties.options_map.get(&lower))
                {
                    initial = Some(mapped.clone());
                } else if let Some(found) = item
                    .properties
                    .options
                    .iter()
                    .find(|o| o.eq_ignore_ascii_case(&stdout))
                {
                    initial = Some(found.clone());
                }
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
        std::path::PathBuf::from("/home/dusk")
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
                Some(Color::from_rgba(1.0, 1.0, 1.0, 0.04).into())
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

