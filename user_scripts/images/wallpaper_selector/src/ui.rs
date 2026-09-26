use iced::alignment::{Horizontal, Vertical};
use iced::keyboard::Key;
use iced::keyboard::key::Named;
use iced::widget::{
    Float, Space, Stack, button, column, container, image, mouse_area, row, scrollable, text,
    text_input,
};
use iced::{
    Background, Border, Color, ContentFit, Element, Event, Length, Shadow, Subscription, Task,
    Vector,
};
use std::collections::HashSet;

use crate::config::{Config, MotionProfile, Preferences, ViewLayout};
use crate::scanner::WallpaperItem;
use crate::theme::AppTheme;

#[derive(Debug, Clone)]
pub enum Message {
    SearchChanged(String),
    ToggleFavoritesView(bool),
    ToggleColorFilter(u8),
    CycleSortMode,
    CycleMotionProfile,
    ToggleViewLayout,
    SelectWallpaper(usize),
    ApplyWallpaper(usize, bool),
    WallpaperApplied(Result<String, String>),
    ToggleFavorite(usize),
    NextWallpaper,
    PrevWallpaper,
    JumpWallpapers(isize),
    ApplyRandom,
    RefreshList,
    RefreshFinished(usize, usize, usize),
    ToggleAnimation,
    AnimationFrame(iced::time::Instant),
    EventOccurred(Event),
    Close,
}

const SLICE_WIDTH: f32 = 120.0;
const EXPANDED_WIDTH: f32 = 540.0;
const CARD_HEIGHT: f32 = 337.5;
const CARD_GAP: f32 = 10.0;

struct CarouselAnimation {
    target: f32,
    velocity: f32,
    last_tick: iced::time::Instant,
    profile: MotionProfile,
}

impl CarouselAnimation {
    fn tick(&mut self, position: &mut f32, now: iced::time::Instant) -> bool {
        let mut remaining = now
            .saturating_duration_since(self.last_tick)
            .as_secs_f32()
            .min(0.05);
        self.last_tick = now;
        let (omega, zeta) = self.profile.spring_params();
        if omega <= 0.0 {
            *position = self.target;
            self.velocity = 0.0;
            return false;
        }
        while remaining > 0.0 {
            let step = remaining.min(1.0 / 240.0);
            let accel = -omega * omega * (*position - self.target) - 2.0 * zeta * omega * self.velocity;
            self.velocity += accel * step;
            *position += self.velocity * step;
            remaining -= step;
        }
        if (*position - self.target).abs() < 0.001 && self.velocity.abs() < 0.01 {
            *position = self.target;
            self.velocity = 0.0;
            false
        } else {
            true
        }
    }
}

pub struct WallpaperSelectorApp {
    config: Config,
    theme: AppTheme,
    all_wallpapers: Vec<WallpaperItem>,
    filtered_indices: Vec<usize>,
    favorites: HashSet<String>,
    active_wallpaper: Option<String>,
    search_query: String,
    show_only_favorites: bool,
    selected_color: Option<u8>,
    sort_mode: crate::config::SortMode,
    motion_profile: MotionProfile,
    view_layout: ViewLayout,
    random_seed: u64,
    selected_index: Option<usize>,
    applying: bool,
    refreshing: bool,
    error_message: Option<String>,
    refresh_status: Option<String>,
    animate_carousel: bool,
    animation: Option<CarouselAnimation>,
    visual_position: f32,
}

impl WallpaperSelectorApp {
    pub fn new(config: Config) -> (Self, Task<Message>) {
        let preferences = Preferences::load(&config.preferences_file);
        let theme = AppTheme::load();
        let favorites = crate::favorites::load_favorites(&config.fav_file);
        let active_wallpaper = crate::favorites::read_active_wallpaper(&config.theme_dir);

        let mut all_wallpapers = crate::scanner::scan_wallpapers(
            &config.wallpaper_dir,
            &config.thumb_dir,
            &favorites,
            active_wallpaper.as_deref(),
        );

        let colors = crate::color::ensure_color_cache(&all_wallpapers, &config.colors_file);
        for item in &mut all_wallpapers {
            if let Some(&b) = colors.get(&item.relative) {
                item.color_bucket = b;
            }
        }

        let mut app = Self {
            config,
            theme,
            all_wallpapers,
            filtered_indices: Vec::new(),
            favorites,
            active_wallpaper,
            search_query: String::new(),
            show_only_favorites: false,
            selected_color: None,
            sort_mode: preferences.sort_mode,
            motion_profile: preferences.motion_profile,
            view_layout: preferences.view_layout,
            random_seed: 42,
            selected_index: None,
            applying: false,
            refreshing: false,
            error_message: None,
            refresh_status: None,
            animate_carousel: preferences.motion_profile.is_enabled(),
            animation: None,
            visual_position: 0.0,
        };

        app.refilter();

        // Auto-select the active wallpaper if present
        if let Some(ref active) = app.active_wallpaper {
            for (idx, &item_idx) in app.filtered_indices.iter().enumerate() {
                if let Some(item) = app.all_wallpapers.get(item_idx) {
                    if item.is_active || item.name == *active || item.relative == *active {
                        app.selected_index = Some(idx);
                        break;
                    }
                }
            }
        }

        if app.selected_index.is_none() && !app.filtered_indices.is_empty() {
            app.selected_index = Some(0);
        }

        app.visual_position = app.selected_index.unwrap_or(0) as f32;

        app.prefetch_around_selected();

        (app, Task::none())
    }

    fn prefetch_around_selected(&self) {
        if self.refreshing {
            return;
        }
        if let Some(sel) = self.selected_index {
            let count = self.filtered_indices.len();
            if count == 0 {
                return;
            }
            let start = sel.saturating_sub(6);
            let end = (sel + 6).min(count.saturating_sub(1));
            let mut missing = Vec::new();
            for i in start..=end {
                if let Some(&item_idx) = self.filtered_indices.get(i) {
                    if let Some(item) = self.all_wallpapers.get(item_idx) {
                        if !item.thumb_path.exists() {
                            missing.push((item.path.clone(), item.thumb_path.clone()));
                        }
                    }
                }
            }
            if !missing.is_empty() {
                std::thread::spawn(move || {
                    for (src, dst) in missing {
                        crate::cache::generate_thumb(&src, &dst);
                    }
                });
            }
        }
    }

    fn refilter(&mut self) {
        let prev_selected_relative = self
            .selected_index
            .and_then(|idx| self.filtered_indices.get(idx))
            .and_then(|&item_idx| self.all_wallpapers.get(item_idx))
            .map(|item| item.relative.clone());

        let query = self.search_query.trim().to_lowercase();
        let show_favs = self.show_only_favorites;
        let selected_color = self.selected_color;

        let mut filtered: Vec<usize> = self
            .all_wallpapers
            .iter()
            .enumerate()
            .filter(|(_, item)| {
                if show_favs && !item.is_favorite {
                    return false;
                }
                if let Some(color_bucket) = selected_color {
                    if item.color_bucket != color_bucket {
                        return false;
                    }
                }
                if query.is_empty() {
                    return true;
                }
                item.name.to_lowercase().contains(&query)
                    || item.relative.to_lowercase().contains(&query)
            })
            .map(|(idx, _)| idx)
            .collect();

        // Apply sorting
        match self.sort_mode {
            crate::config::SortMode::Name => {
                filtered.sort_by(|&a, &b| {
                    self.all_wallpapers[a]
                        .name
                        .to_lowercase()
                        .cmp(&self.all_wallpapers[b].name.to_lowercase())
                });
            }
            crate::config::SortMode::Newest => {
                filtered.sort_by(|&a, &b| {
                    self.all_wallpapers[b]
                        .mtime
                        .cmp(&self.all_wallpapers[a].mtime)
                });
            }
            crate::config::SortMode::Random => {
                let mut seed = self.random_seed;
                for i in (1..filtered.len()).rev() {
                    seed ^= seed << 13;
                    seed ^= seed >> 7;
                    seed ^= seed << 17;
                    let j = (seed as usize) % (i + 1);
                    filtered.swap(i, j);
                }
            }
        }

        self.filtered_indices = filtered;

        // Retain the previously selected wallpaper if present in the new set
        if let Some(ref rel) = prev_selected_relative {
            self.selected_index = self
                .filtered_indices
                .iter()
                .position(|&item_idx| self.all_wallpapers[item_idx].relative == *rel);
        }

        if self.selected_index.is_none() && !self.filtered_indices.is_empty() {
            self.selected_index = Some(0);
        }

        self.animation = None;
        self.visual_position = self.selected_index.unwrap_or(0) as f32;
        self.prefetch_around_selected();
    }

    fn select_wallpaper(&mut self, next: usize) {
        if next >= self.filtered_indices.len() || self.selected_index == Some(next) {
            return;
        }
        self.selected_index = Some(next);
        if self.motion_profile.is_enabled() {
            if let Some(animation) = &mut self.animation {
                animation.target = next as f32;
                animation.profile = self.motion_profile;
            } else {
                self.animation = Some(CarouselAnimation {
                    target: next as f32,
                    velocity: 0.0,
                    last_tick: iced::time::Instant::now(),
                    profile: self.motion_profile,
                });
            }
        } else {
            self.animation = None;
            self.visual_position = next as f32;
        }
        self.prefetch_around_selected();
    }

    pub fn update(&mut self, message: Message) -> Task<Message> {
        if !matches!(
            &message,
            Message::RefreshList | Message::RefreshFinished(..) | Message::EventOccurred(_)
        ) {
            self.refresh_status = None;
        }
        match message {
            Message::SearchChanged(query) => {
                self.search_query = query;
                self.refilter();
                Task::none()
            }
            Message::ToggleFavoritesView(favs_only) => {
                self.show_only_favorites = favs_only;
                self.refilter();
                Task::none()
            }
            Message::SelectWallpaper(filtered_idx) => {
                self.select_wallpaper(filtered_idx);
                Task::none()
            }
            Message::NextWallpaper => {
                if let Some(sel) = self.selected_index {
                    if sel + 1 < self.filtered_indices.len() {
                        self.select_wallpaper(sel + 1);
                    }
                }
                Task::none()
            }
            Message::PrevWallpaper => {
                if let Some(sel) = self.selected_index {
                    if sel > 0 {
                        self.select_wallpaper(sel - 1);
                    }
                }
                Task::none()
            }
            Message::JumpWallpapers(delta) => {
                if let Some(sel) = self.selected_index {
                    let count = self.filtered_indices.len();
                    if count > 0 {
                        let next = (sel as isize + delta).clamp(0, count as isize - 1) as usize;
                        self.select_wallpaper(next);
                    }
                }
                Task::none()
            }
            Message::ApplyRandom => {
                if !self.filtered_indices.is_empty() {
                    use std::time::SystemTime;
                    let seed = SystemTime::now()
                        .duration_since(SystemTime::UNIX_EPOCH)
                        .map(|d| d.as_nanos() as usize)
                        .unwrap_or(42);
                    let random_idx = seed % self.filtered_indices.len();
                    return self.update(Message::ApplyWallpaper(random_idx, true));
                }
                Task::none()
            }
            Message::ApplyWallpaper(filtered_idx, regen) => {
                if self.applying {
                    return Task::none();
                }
                if let Some(&item_idx) = self.filtered_indices.get(filtered_idx) {
                    if let Some(item) = self.all_wallpapers.get(item_idx) {
                        let path = item.path.clone();
                        let theme_ctl = self.config.theme_ctl.clone();
                        let relative = item.relative.clone();
                        self.applying = true;
                        self.error_message = None;
                        return Task::perform(
                            async move {
                                crate::apply::apply_wallpaper(&path, &theme_ctl, regen)
                                    .map(|()| relative)
                            },
                            Message::WallpaperApplied,
                        );
                    }
                }
                Task::none()
            }
            Message::WallpaperApplied(result) => {
                self.applying = false;
                match result {
                    Ok(relative) => {
                        self.active_wallpaper = Some(relative.clone());
                        for w in &mut self.all_wallpapers {
                            w.is_active = w.relative == relative;
                        }
                        Task::none()
                    }
                    Err(error) => {
                        self.error_message = Some(format!("Could not apply wallpaper: {error}"));
                        Task::none()
                    }
                }
            }
            Message::ToggleFavorite(filtered_idx) => {
                if let Some(&item_idx) = self.filtered_indices.get(filtered_idx) {
                    if let Some(item) = self.all_wallpapers.get(item_idx) {
                        let lock_path = self
                            .config
                            .home
                            .join(".cache/dusky_images/wallpaper_selector/favorites.lock");
                        match crate::favorites::toggle_favorite(
                            &self.config.fav_file,
                            &lock_path,
                            &item.relative,
                            &item.name,
                        ) {
                            Ok(favorites) => {
                                self.favorites = favorites;
                                for wallpaper in &mut self.all_wallpapers {
                                    wallpaper.is_favorite =
                                        self.favorites.contains(&wallpaper.relative)
                                            || self.favorites.contains(&wallpaper.name);
                                }
                                self.error_message = None;
                            }
                            Err(error) => {
                                self.error_message =
                                    Some(format!("Could not save favorite: {error}"));
                            }
                        }
                    }
                }
                if self.show_only_favorites {
                    self.refilter();
                }
                Task::none()
            }
            Message::RefreshList => {
                if self.refreshing {
                    return Task::none();
                }
                let selected = self.selected_index.and_then(|index| {
                    self.filtered_indices
                        .get(index)
                        .and_then(|&item| self.all_wallpapers.get(item))
                        .map(|item| item.relative.clone())
                });
                self.refreshing = true;
                self.refresh_status = Some("Refreshing library…".to_owned());
                self.theme = AppTheme::load();
                self.favorites = crate::favorites::load_favorites(&self.config.fav_file);
                self.active_wallpaper =
                    crate::favorites::read_active_wallpaper(&self.config.theme_dir);
                self.all_wallpapers = crate::scanner::scan_wallpapers(
                    &self.config.wallpaper_dir,
                    &self.config.thumb_dir,
                    &self.favorites,
                    self.active_wallpaper.as_deref(),
                );
                let colors = crate::color::ensure_color_cache(&self.all_wallpapers, &self.config.colors_file);
                for item in &mut self.all_wallpapers {
                    if let Some(&b) = colors.get(&item.relative) {
                        item.color_bucket = b;
                    }
                }
                self.refilter();
                if let Some(selected) = selected {
                    if let Some(index) = self
                        .filtered_indices
                        .iter()
                        .position(|&item| self.all_wallpapers[item].relative == selected)
                    {
                        self.selected_index = Some(index);
                    }
                }
                self.visual_position = self.selected_index.unwrap_or(0) as f32;
                self.prefetch_around_selected();
                let wallpapers = self.all_wallpapers.clone();
                let total = wallpapers.len();
                Task::perform(
                    async move {
                        let stats = crate::cache::batch_generate_thumbs(&wallpapers, false);
                        (total, stats.generated, stats.failed)
                    },
                    |(total, generated, failed)| Message::RefreshFinished(total, generated, failed),
                )
            }
            Message::RefreshFinished(total, generated, failed) => {
                self.refreshing = false;
                if failed > 0 {
                    self.error_message =
                        Some(format!("Could not update {failed} wallpaper previews"));
                } else {
                    self.error_message = None;
                    self.refresh_status = Some(format!(
                        "Library refreshed: {total} wallpapers, {generated} previews updated"
                    ));
                }
                Task::none()
            }
            Message::ToggleColorFilter(bucket) => {
                if self.selected_color == Some(bucket) {
                    self.selected_color = None;
                } else {
                    self.selected_color = Some(bucket);
                }
                self.refilter();
                Task::none()
            }
            Message::CycleSortMode => {
                self.sort_mode = self.sort_mode.next();
                if self.sort_mode == crate::config::SortMode::Random {
                    use std::time::SystemTime;
                    self.random_seed = SystemTime::now()
                        .duration_since(SystemTime::UNIX_EPOCH)
                        .map(|d| d.as_nanos() as u64)
                        .unwrap_or(42);
                }
                let preferences = Preferences {
                    animate_carousel: self.animate_carousel,
                    sort_mode: self.sort_mode,
                    motion_profile: self.motion_profile,
                    view_layout: self.view_layout,
                };
                let _ = preferences.save(&self.config.preferences_file);
                self.refilter();
                Task::none()
            }
            Message::CycleMotionProfile => {
                let next_profile = self.motion_profile.next();
                let preferences = Preferences {
                    animate_carousel: next_profile.is_enabled(),
                    sort_mode: self.sort_mode,
                    motion_profile: next_profile,
                    view_layout: self.view_layout,
                };
                let _ = preferences.save(&self.config.preferences_file);
                self.motion_profile = next_profile;
                self.animate_carousel = next_profile.is_enabled();
                if !self.animate_carousel {
                    self.animation = None;
                    self.visual_position = self.selected_index.unwrap_or(0) as f32;
                } else if let Some(animation) = &mut self.animation {
                    animation.profile = next_profile;
                }
                Task::none()
            }
            Message::ToggleViewLayout => {
                let next_layout = self.view_layout.toggle();
                let preferences = Preferences {
                    animate_carousel: self.animate_carousel,
                    sort_mode: self.sort_mode,
                    motion_profile: self.motion_profile,
                    view_layout: next_layout,
                };
                let _ = preferences.save(&self.config.preferences_file);
                self.view_layout = next_layout;
                Task::none()
            }
            Message::ToggleAnimation => self.update(Message::CycleMotionProfile),
            Message::AnimationFrame(at) => {
                if let Some(animation) = &mut self.animation {
                    if !animation.tick(&mut self.visual_position, at) {
                        self.animation = None;
                    }
                }
                Task::none()
            }
            Message::Close => iced::exit(),
            Message::EventOccurred(Event::Mouse(iced::mouse::Event::WheelScrolled { delta })) => {
                match delta {
                    iced::mouse::ScrollDelta::Lines { x, y } => {
                        if y < 0.0 || x > 0.0 {
                            return self.update(Message::NextWallpaper);
                        } else if y > 0.0 || x < 0.0 {
                            return self.update(Message::PrevWallpaper);
                        }
                    }
                    iced::mouse::ScrollDelta::Pixels { x, y } => {
                        if y < 0.0 || x > 0.0 {
                            return self.update(Message::NextWallpaper);
                        } else if y > 0.0 || x < 0.0 {
                            return self.update(Message::PrevWallpaper);
                        }
                    }
                }
                Task::none()
            }
            Message::EventOccurred(Event::Keyboard(iced::keyboard::Event::KeyPressed {
                key,
                modifiers: _,
                ..
            })) => match key {
                Key::Named(Named::Escape) => {
                    if !self.search_query.is_empty() {
                        self.search_query.clear();
                        self.refilter();
                        Task::none()
                    } else {
                        iced::exit()
                    }
                }
                Key::Named(Named::Enter) => {
                    if let Some(sel) = self.selected_index {
                        return self.update(Message::ApplyWallpaper(sel, true));
                    }
                    Task::none()
                }
                Key::Named(Named::ArrowRight) => self.update(Message::NextWallpaper),
                Key::Named(Named::ArrowLeft) => self.update(Message::PrevWallpaper),
                Key::Named(Named::PageDown) => self.update(Message::JumpWallpapers(5)),
                Key::Named(Named::PageUp) => self.update(Message::JumpWallpapers(-5)),
                Key::Named(Named::Home) => {
                    self.select_wallpaper(0);
                    Task::none()
                }
                Key::Named(Named::End) => {
                    if !self.filtered_indices.is_empty() {
                        self.select_wallpaper(self.filtered_indices.len() - 1);
                    }
                    Task::none()
                }
                Key::Character(ref c) if (c == "f" || c == "F") && self.search_query.is_empty() => {
                    if let Some(sel) = self.selected_index {
                        return self.update(Message::ToggleFavorite(sel));
                    }
                    Task::none()
                }
                Key::Character(ref c) if (c == "r" || c == "R") && self.search_query.is_empty() => {
                    self.update(Message::ApplyRandom)
                }
                Key::Character(ref c) if (c == "s" || c == "S") && self.search_query.is_empty() => {
                    self.update(Message::CycleSortMode)
                }
                Key::Character(ref c) if (c == "m" || c == "M") && self.search_query.is_empty() => {
                    self.update(Message::CycleMotionProfile)
                }
                Key::Character(ref c) if (c == "g" || c == "G") && self.search_query.is_empty() => {
                    self.update(Message::ToggleViewLayout)
                }
                Key::Character(ref c) if (c == "c" || c == "C") && self.search_query.is_empty() => {
                    if self.selected_color.is_some() {
                        self.selected_color = None;
                        self.refilter();
                    }
                    Task::none()
                }
                _ => Task::none(),
            },
            Message::EventOccurred(_) => Task::none(),
        }
    }

    pub fn view(&self) -> Element<'_, Message> {
        let accent = self.theme.accent;

        // --- Top Bar: Floating HUD Capsule (skwd-wall style) ---
        let all_active = !self.show_only_favorites;
        let all_btn = button(
            text(format!("ALL ({})", self.all_wallpapers.len()))
                .size(11)
                .align_x(Horizontal::Center)
                .align_y(Vertical::Center),
        )
        .padding([6, 14])
        .on_press(Message::ToggleFavoritesView(false))
        .style(move |_theme, status| {
            let is_hovered = status == button::Status::Hovered;
            button::Style {
                background: Some(Background::Color(if all_active {
                    Color { a: 0.22, ..accent }
                } else if is_hovered {
                    Color::from_rgba8(255, 255, 255, 0.08)
                } else {
                    Color::TRANSPARENT
                })),
                text_color: if all_active {
                    accent
                } else {
                    Color::from_rgb8(180, 185, 200)
                },
                border: Border {
                    radius: 14.0.into(),
                    color: if all_active {
                        Color { a: 0.6, ..accent }
                    } else {
                        Color::TRANSPARENT
                    },
                    width: 1.0,
                },
                ..button::Style::default()
            }
        });

        let favs_count = self.all_wallpapers.iter().filter(|w| w.is_favorite).count();
        let favs_active = self.show_only_favorites;
        let favs_btn = button(
            text(format!("♥ FAVS ({favs_count})"))
                .size(11)
                .align_x(Horizontal::Center)
                .align_y(Vertical::Center),
        )
        .padding([6, 14])
        .on_press(Message::ToggleFavoritesView(true))
        .style(move |_theme, status| {
            let is_hovered = status == button::Status::Hovered;
            button::Style {
                background: Some(Background::Color(if favs_active {
                    Color::from_rgba8(243, 139, 168, 0.22)
                } else if is_hovered {
                    Color::from_rgba8(255, 255, 255, 0.08)
                } else {
                    Color::TRANSPARENT
                })),
                text_color: if favs_active {
                    Color::from_rgb8(243, 139, 168)
                } else {
                    Color::from_rgb8(180, 185, 200)
                },
                border: Border {
                    radius: 14.0.into(),
                    color: if favs_active {
                        Color::from_rgba8(243, 139, 168, 0.6)
                    } else {
                        Color::TRANSPARENT
                    },
                    width: 1.0,
                },
                ..button::Style::default()
            }
        });

        let mode_pill = container(row![all_btn, favs_btn].spacing(2))
            .padding(2)
            .style(|_| container::Style {
                background: Some(Background::Color(Color::from_rgba8(20, 22, 30, 0.90))),
                border: Border {
                    radius: 16.0.into(),
                    color: Color::from_rgba8(255, 255, 255, 0.08),
                    width: 1.0,
                },
                ..container::Style::default()
            });

        // Search capsule
        let search_input = text_input("dusky wallpapers", &self.search_query)
            .on_input(Message::SearchChanged)
            .padding([6, 10])
            .size(12)
            .width(Length::Fixed(120.0))
            .style(move |_theme, status| {
                let is_focused = matches!(status, text_input::Status::Focused { .. });
                text_input::Style {
                    background: Background::Color(Color::from_rgba8(20, 22, 30, 0.90)),
                    border: Border {
                        color: if is_focused {
                            Color { a: 0.8, ..accent }
                        } else {
                            Color::from_rgba8(255, 255, 255, 0.08)
                        },
                        width: 1.0,
                        radius: 16.0.into(),
                    },
                    icon: Color::from_rgb8(180, 190, 210),
                    placeholder: Color::from_rgba8(130, 140, 160, 0.6),
                    value: Color::from_rgb8(240, 243, 255),
                    selection: Color { a: 0.3, ..accent },
                }
            });

        // Position Counter pill
        let current_pos_text = if let Some(sel) = self.selected_index {
            format!("{}/{}", sel + 1, self.filtered_indices.len())
        } else {
            format!("0/{}", self.filtered_indices.len())
        };

        let counter_pill = container(
            text(current_pos_text)
                .size(11)
                .color(Color::from_rgb8(150, 160, 185)),
        )
        .padding([6, 12])
        .style(|_| container::Style {
            background: Some(Background::Color(Color::from_rgba8(20, 22, 30, 0.90))),
            border: Border {
                color: Color::from_rgba8(255, 255, 255, 0.08),
                width: 1.0,
                radius: 14.0.into(),
            },
            ..container::Style::default()
        });

        // Action buttons
        let motion_profile = self.motion_profile;
        let motion_btn = button(
            text(motion_profile.label())
                .size(11)
                .align_x(Horizontal::Center)
                .align_y(Vertical::Center),
        )
        .padding([6, 12])
        .on_press(Message::CycleMotionProfile)
        .style(move |_theme, status| {
            let is_active = motion_profile.is_enabled();
            button::Style {
                background: Some(Background::Color(if is_active {
                    Color { a: 0.22, ..accent }
                } else if status == button::Status::Hovered {
                    Color::from_rgba8(255, 255, 255, 0.12)
                } else {
                    Color::from_rgba8(20, 22, 30, 0.90)
                })),
                text_color: if is_active {
                    accent
                } else {
                    Color::from_rgb8(210, 215, 235)
                },
                border: Border {
                    radius: 14.0.into(),
                    color: Color::from_rgba8(255, 255, 255, 0.08),
                    width: 1.0,
                },
                ..button::Style::default()
            }
        });

        let view_layout = self.view_layout;
        let view_btn = button(
            text(view_layout.label())
                .size(11)
                .align_x(Horizontal::Center)
                .align_y(Vertical::Center),
        )
        .padding([6, 12])
        .on_press(Message::ToggleViewLayout)
        .style(move |_theme, status| {
            let is_hovered = status == button::Status::Hovered;
            button::Style {
                background: Some(Background::Color(if is_hovered {
                    Color::from_rgba8(255, 255, 255, 0.12)
                } else {
                    Color::from_rgba8(20, 22, 30, 0.90)
                })),
                text_color: Color::from_rgb8(210, 215, 235),
                border: Border {
                    radius: 14.0.into(),
                    color: Color::from_rgba8(255, 255, 255, 0.08),
                    width: 1.0,
                },
                ..button::Style::default()
            }
        });

        let random_btn = button(
            text("🎲 Random")
                .size(11)
                .align_x(Horizontal::Center)
                .align_y(Vertical::Center),
        )
        .padding([6, 12])
        .on_press(Message::ApplyRandom)
        .style(move |_theme, status| {
            let is_hovered = status == button::Status::Hovered;
            button::Style {
                background: Some(Background::Color(if is_hovered {
                    Color::from_rgba8(255, 255, 255, 0.12)
                } else {
                    Color::from_rgba8(20, 22, 30, 0.90)
                })),
                text_color: Color::from_rgb8(210, 215, 235),
                border: Border {
                    radius: 14.0.into(),
                    color: Color::from_rgba8(255, 255, 255, 0.08),
                    width: 1.0,
                },
                ..button::Style::default()
            }
        });

        let refresh_btn = button(
            text("↻ Refresh")
                .size(11)
                .align_x(Horizontal::Center)
                .align_y(Vertical::Center),
        )
        .padding([6, 10])
        .on_press_maybe((!self.refreshing).then_some(Message::RefreshList))
        .style(|_theme, status| button::Style {
            background: Some(Background::Color(if status == button::Status::Hovered {
                Color::from_rgba8(255, 255, 255, 0.15)
            } else {
                Color::from_rgba8(20, 22, 30, 0.90)
            })),
            text_color: Color::from_rgb8(210, 215, 235),
            border: Border {
                color: Color::from_rgba8(255, 255, 255, 0.08),
                width: 1.0,
                radius: 14.0.into(),
            },
            ..button::Style::default()
        });

        let close_btn = button(
            text("✕")
                .size(12)
                .align_x(Horizontal::Center)
                .align_y(Vertical::Center),
        )
        .padding([6, 10])
        .on_press(Message::Close)
        .style(|_theme, status| button::Style {
            background: Some(Background::Color(if status == button::Status::Hovered {
                Color::from_rgba8(239, 68, 68, 0.3)
            } else {
                Color::from_rgba8(20, 22, 30, 0.90)
            })),
            text_color: if status == button::Status::Hovered {
                Color::from_rgb8(252, 165, 165)
            } else {
                Color::from_rgb8(210, 215, 235)
            },
            border: Border {
                color: if status == button::Status::Hovered {
                    Color::from_rgba8(239, 68, 68, 0.5)
                } else {
                    Color::from_rgba8(255, 255, 255, 0.08)
                },
                width: 1.0,
                radius: 14.0.into(),
            },
            ..button::Style::default()
        });

        // Top capsule (Discovery & window actions)
        let top_capsule = row![
            mode_pill,
            view_btn,
            motion_btn,
            search_input,
            random_btn,
            refresh_btn,
            close_btn,
        ]
        .spacing(8)
        .align_y(Vertical::Center);

        let top_bar = container(
            row![
                Space::new().width(Length::Fill),
                top_capsule,
                Space::new().width(Length::Fill),
            ]
            .align_y(Vertical::Center),
        )
        .padding([8, 16])
        .width(Length::Fill);

        // --- Bottom Refinement Capsule ---
        // Sort button
        let sort_label = self.sort_mode.label();
        let sort_btn = button(
            text(sort_label)
                .size(11)
                .align_x(Horizontal::Center)
                .align_y(Vertical::Center),
        )
        .padding([6, 12])
        .on_press(Message::CycleSortMode)
        .style(move |_theme, status| {
            let is_hovered = status == button::Status::Hovered;
            button::Style {
                background: Some(Background::Color(if is_hovered {
                    Color::from_rgba8(255, 255, 255, 0.12)
                } else {
                    Color::from_rgba8(20, 22, 30, 0.90)
                })),
                text_color: Color::from_rgb8(210, 215, 235),
                border: Border {
                    radius: 14.0.into(),
                    color: Color::from_rgba8(255, 255, 255, 0.08),
                    width: 1.0,
                },
                ..button::Style::default()
            }
        });

        // Color palette filter pill
        let mut swatches_row = row![].spacing(3).align_y(Vertical::Center);
        for bucket in 0..crate::color::COLOR_BUCKET_COUNT as u8 {
            let is_selected = self.selected_color == Some(bucket);
            let col = crate::color::swatch_color(bucket, is_selected);
            let btn = button(Space::new().width(Length::Fixed(11.0)).height(Length::Fixed(11.0)))
                .padding(2)
                .on_press(Message::ToggleColorFilter(bucket))
                .style(move |_theme, status| {
                    let is_hovered = status == button::Status::Hovered;
                    button::Style {
                        background: Some(Background::Color(if is_hovered {
                            crate::color::swatch_color(bucket, true)
                        } else {
                            col
                        })),
                        border: Border {
                            radius: 8.0.into(),
                            color: if is_selected {
                                Color::WHITE
                            } else if is_hovered {
                                Color::from_rgba8(255, 255, 255, 0.7)
                            } else {
                                Color::from_rgba8(0, 0, 0, 0.4)
                            },
                            width: if is_selected { 2.0 } else { 1.0 },
                        },
                        ..button::Style::default()
                    }
                });
            swatches_row = swatches_row.push(btn);
        }

        if let Some(active_bucket) = self.selected_color {
            let clear_btn = button(
                text("✕")
                    .size(9)
                    .align_x(Horizontal::Center)
                    .align_y(Vertical::Center),
            )
            .padding([1, 4])
            .on_press(Message::ToggleColorFilter(active_bucket))
            .style(|_theme, status| button::Style {
                background: Some(Background::Color(if status == button::Status::Hovered {
                    Color::from_rgba8(239, 68, 68, 0.4)
                } else {
                    Color::TRANSPARENT
                })),
                text_color: Color::from_rgb8(210, 215, 235),
                border: Border {
                    radius: 8.0.into(),
                    color: Color::TRANSPARENT,
                    width: 0.0,
                },
                ..button::Style::default()
            });
            swatches_row = swatches_row.push(clear_btn);
        }

        let color_pill = container(swatches_row)
            .padding([4, 8])
            .style(|_| container::Style {
                background: Some(Background::Color(Color::from_rgba8(20, 22, 30, 0.90))),
                border: Border {
                    radius: 16.0.into(),
                    color: Color::from_rgba8(255, 255, 255, 0.08),
                    width: 1.0,
                },
                ..container::Style::default()
            });

        // Bottom capsule (Sort, Color Swatches, Counter)
        let bottom_capsule = row![
            sort_btn,
            color_pill,
            counter_pill,
        ]
        .spacing(8)
        .align_y(Vertical::Center);

        // Center Content (Carousel or Grid)
        let main_content = if self.filtered_indices.is_empty() {
            container(
                column![
                    text("No wallpapers found")
                        .size(20)
                        .color(Color::from_rgb8(160, 165, 185)),
                    Space::new().height(6),
                    text("Try clearing the search or switching view tabs.")
                        .size(13)
                        .color(Color::from_rgb8(104, 109, 126)),
                ]
                .align_x(Horizontal::Center)
                .spacing(4),
            )
            .width(Length::Fill)
            .height(Length::Fill)
            .align_x(Horizontal::Center)
            .align_y(Vertical::Center)
            .into()
        } else {
            match self.view_layout {
                ViewLayout::Carousel => self.build_motion_carousel(),
                ViewLayout::Grid => self.build_grid_view(),
            }
        };

        // --- Bottom Micro Bar ---
        let bottom_hint = text(if let Some(error) = &self.error_message {
            error.as_str()
        } else if self.applying {
            "Applying wallpaper…"
        } else if let Some(status) = &self.refresh_status {
            status.as_str()
        } else {
            "← / →: Navigate  •  G: Grid / Slices  •  M: Motion  •  S: Sort  •  C: Clear color  •  Click: Apply  •  Esc: Close"
        })
        .size(11)
        .color(if self.error_message.is_some() {
            Color::from_rgb8(252, 165, 165)
        } else {
            Color::from_rgba8(160, 170, 195, 0.7)
        });

        let bottom_bar = container(
            column![
                row![
                    Space::new().width(Length::Fill),
                    bottom_capsule,
                    Space::new().width(Length::Fill),
                ]
                .align_y(Vertical::Center),
                Space::new().height(Length::Fixed(8.0)),
                row![
                    Space::new().width(Length::Fill),
                    bottom_hint,
                    Space::new().width(Length::Fill),
                ]
                .align_y(Vertical::Center),
            ]
            .align_x(Horizontal::Center),
        )
        .padding([4, 16])
        .width(Length::Fill);

        // --- Fullscreen Transparent Overlay (skwd-wall style) ---
        let content_column = match self.view_layout {
            ViewLayout::Carousel => column![
                Space::new().height(Length::Fixed(16.0)),
                top_bar,
                Space::new().height(Length::Fill),
                main_content,
                Space::new().height(Length::Fill),
                bottom_bar,
                Space::new().height(Length::Fixed(12.0)),
            ],
            ViewLayout::Grid => column![
                Space::new().height(Length::Fixed(16.0)),
                top_bar,
                Space::new().height(Length::Fixed(10.0)),
                main_content,
                Space::new().height(Length::Fixed(10.0)),
                bottom_bar,
                Space::new().height(Length::Fixed(12.0)),
            ],
        };

        container(
            content_column
                .width(Length::Fill)
                .height(Length::Fill)
                .align_x(Horizontal::Center),
        )
        .width(Length::Fill)
        .height(Length::Fill)
        .style(|_| container::Style {
            background: None,
            ..container::Style::default()
        })
        .into()
    }

    fn build_motion_carousel(&self) -> Element<'_, Message> {
        let total = self.filtered_indices.len();
        let position = self.visual_position.clamp(0.0, (total - 1) as f32);
        let start = (position.floor() as usize).saturating_sub(4);
        let end = ((position.ceil() as usize) + 4).min(total - 1);
        let mut indices: Vec<usize> = (start..=end).collect();
        indices.sort_by(|a, b| {
            (b.abs_diff(position.round() as usize)).cmp(&a.abs_diff(position.round() as usize))
        });

        let mut cards = Stack::new().width(Length::Fill).height(Length::Fill);
        for index in indices {
            let item = &self.all_wallpapers[self.filtered_indices[index]];
            let distance = (index as f32 - position).abs();
            let emphasis = (1.0 - distance).max(0.0);
            let width = Self::motion_width(index, position);
            let x = Self::motion_center(index, position);

            let card = self.build_center_card(item, index, width, emphasis, distance);
            cards = cards.push(Float::new(card).translate(move |bounds, viewport| {
                Vector::new(
                    viewport.x + viewport.width * 0.5 - bounds.x - bounds.width * 0.5 + x + 0.001,
                    viewport.y + viewport.height * 0.5 - bounds.y - bounds.height * 0.5,
                )
            }));
        }
        cards.clip(true).into()
    }

    fn build_grid_view(&self) -> Element<'_, Message> {
        let accent = self.theme.accent;
        let mut grid_col = column![].spacing(14).padding([12, 20]).align_x(Horizontal::Center);
        let chunk_size = 5;

        for chunk in self.filtered_indices.chunks(chunk_size) {
            let mut r = row![].spacing(14).align_y(Vertical::Center);
            for &item_idx in chunk {
                let filtered_idx = self
                    .filtered_indices
                    .iter()
                    .position(|&idx| idx == item_idx)
                    .unwrap_or(0);
                let item = &self.all_wallpapers[item_idx];
                let is_selected = self.selected_index == Some(filtered_idx);

                let card_radius = 10.0;
                let img_layer: Element<'_, Message> = if item.thumb_path.exists() {
                    image(item.thumb_path.clone())
                        .width(Length::Fill)
                        .height(Length::Fill)
                        .content_fit(ContentFit::Cover)
                        .border_radius(card_radius)
                        .into()
                } else {
                    container(Space::new())
                        .width(Length::Fill)
                        .height(Length::Fill)
                        .style(move |_| container::Style {
                            background: Some(Background::Color(Color::from_rgb8(18, 20, 28))),
                            border: Border {
                                radius: card_radius.into(),
                                ..Border::default()
                            },
                            ..container::Style::default()
                        })
                        .into()
                };

                let name = item
                    .path
                    .file_stem()
                    .map(|stem| stem.to_string_lossy())
                    .unwrap_or_else(|| item.name.as_str().into());
                let display_name = if name.chars().count() > 24 {
                    format!("{}…", name.chars().take(23).collect::<String>())
                } else {
                    name.into_owned()
                };

                let mut badge_row = row![text(display_name).size(10).color(Color::WHITE)]
                    .spacing(6)
                    .align_y(Vertical::Center);

                if item.is_active {
                    badge_row = badge_row.push(
                        container(
                            text("● ACTIVE")
                                .size(8)
                                .color(Color::from_rgb8(52, 211, 153)),
                        )
                        .padding([1, 5])
                        .style(|_| container::Style {
                            background: Some(Background::Color(Color::from_rgba8(16, 185, 129, 0.25))),
                            border: Border {
                                color: Color::from_rgba8(52, 211, 153, 0.6),
                                width: 1.0,
                                radius: 8.0.into(),
                            },
                            ..container::Style::default()
                        }),
                    );
                }

                if item.is_favorite {
                    badge_row = badge_row.push(
                        text("♥")
                            .size(12)
                            .color(Color::from_rgb8(243, 139, 168)),
                    );
                }

                let bottom_badge = container(badge_row)
                    .padding([4, 8])
                    .style(|_| container::Style {
                        background: Some(Background::Color(Color::from_rgba8(10, 12, 18, 0.75))),
                        border: Border {
                            radius: 8.0.into(),
                            ..Border::default()
                        },
                        ..container::Style::default()
                    });

                let overlay_col = column![
                    Space::new().height(Length::Fill),
                    bottom_badge,
                ]
                .padding(6);

                let card_stack = Stack::new().push(img_layer).push(overlay_col).clip(true);

                let card_container = container(card_stack)
                    .width(Length::Fixed(250.0))
                    .height(Length::Fixed(140.0))
                    .style(move |_| container::Style {
                        background: Some(Background::Color(Color::from_rgb8(15, 17, 24))),
                        border: Border {
                            radius: card_radius.into(),
                            color: if is_selected {
                                accent
                            } else {
                                Color::from_rgba8(255, 255, 255, 0.08)
                            },
                            width: if is_selected { 2.0 } else { 1.0 },
                        },
                        shadow: Shadow {
                            color: if is_selected {
                                Color { a: 0.35, ..accent }
                            } else {
                                Color::TRANSPARENT
                            },
                            offset: iced::Vector::ZERO,
                            blur_radius: 12.0,
                        },
                        ..container::Style::default()
                    });

                let card_btn = button(card_container)
                    .padding(0)
                    .on_press(if is_selected {
                        Message::ApplyWallpaper(filtered_idx, true)
                    } else {
                        Message::SelectWallpaper(filtered_idx)
                    })
                    .style(move |_theme, status| {
                        let is_hovered = status == button::Status::Hovered;
                        button::Style {
                            background: None,
                            border: Border {
                                radius: card_radius.into(),
                                color: if is_hovered && !is_selected {
                                    Color { a: 0.7, ..accent }
                                } else {
                                    Color::TRANSPARENT
                                },
                                width: 1.5,
                            },
                            ..button::Style::default()
                        }
                    });

                r = r.push(card_btn);
            }

            grid_col = grid_col.push(r);
        }

        let centered_grid = container(grid_col)
            .width(Length::Fill)
            .align_x(Horizontal::Center);

        scrollable(centered_grid)
            .width(Length::Fill)
            .height(Length::Fill)
            .into()
    }

    fn motion_width(index: usize, position: f32) -> f32 {
        SLICE_WIDTH
            + (EXPANDED_WIDTH - SLICE_WIDTH) * (1.0 - (index as f32 - position).abs()).max(0.0)
    }

    fn motion_center(index: usize, position: f32) -> f32 {
        let anchor = position.floor() as usize;
        let mut center = 0.0;
        if index > anchor {
            for left in anchor..index {
                center += (Self::motion_width(left, position)
                    + Self::motion_width(left + 1, position))
                    * 0.5
                    + CARD_GAP;
            }
        } else if index < anchor {
            for right in (index + 1)..=anchor {
                center -= (Self::motion_width(right - 1, position)
                    + Self::motion_width(right, position))
                    * 0.5
                    + CARD_GAP;
            }
        }
        let fraction = position - anchor as f32;
        let focus_step =
            (Self::motion_width(anchor, position) + Self::motion_width(anchor + 1, position)) * 0.5
                + CARD_GAP;
        center - fraction * focus_step
    }

    /// Center expanded card matching skwd-wall Frame 1
    fn build_center_card<'a>(
        &'a self,
        item: &'a WallpaperItem,
        filtered_idx: usize,
        width: f32,
        emphasis: f32,
        distance: f32,
    ) -> Element<'a, Message> {
        let accent = self.theme.accent;
        let card_radius = 12.0 + 2.0 * emphasis;

        // Smoothly fade cards to transparent as they approach the viewport boundary (distance > 2.0 up to 4.0)
        let edge_fade = if distance > 2.0 {
            (1.0 - ((distance - 2.0) / 2.0).clamp(0.0, 1.0)).powf(1.5)
        } else {
            1.0
        };

        // Image layer (reads thumbnail from disk; NEVER generates synchronously)
        let img_layer: Element<'_, Message> = if item.thumb_path.exists() {
            image(item.thumb_path.clone())
                .width(Length::Fill)
                .height(Length::Fill)
                .content_fit(ContentFit::Cover)
                .border_radius(card_radius)
                .opacity(edge_fade)
                .into()
        } else {
            container(Space::new())
                .width(Length::Fill)
                .height(Length::Fill)
                .style(move |_| container::Style {
                    background: Some(Background::Color(Color::from_rgba8(18, 20, 28, edge_fade))),
                    border: Border {
                        radius: card_radius.into(),
                        ..Border::default()
                    },
                    ..container::Style::default()
                })
                .into()
        };

        // --- Top Bar on Center Card ---
        let name = item
            .path
            .file_stem()
            .map(|stem| stem.to_string_lossy())
            .unwrap_or_else(|| item.name.as_str().into());
        let display_name = if name.chars().count() > 48 {
            format!("{}…", name.chars().take(47).collect::<String>())
        } else {
            name.into_owned()
        };
        let mut top_left = row![text(display_name).size(10).color(Color::from_rgba8(
            255,
            255,
            255,
            0.85 * emphasis
        )),]
        .spacing(8)
        .align_y(Vertical::Center);

        if item.is_active && emphasis > 0.5 {
            top_left = top_left.push(
                container(
                    text("● ACTIVE")
                        .size(9)
                        .color(Color::from_rgb8(52, 211, 153)),
                )
                .padding([2, 8])
                .style(|_| container::Style {
                    background: Some(Background::Color(Color::from_rgba8(16, 185, 129, 0.2))),
                    border: Border {
                        color: Color::from_rgba8(52, 211, 153, 0.5),
                        width: 1.0,
                        radius: 10.0.into(),
                    },
                    ..container::Style::default()
                }),
            );
        }

        let fav_char = if item.is_favorite { "♥" } else { "♡" };
        let fav_btn = button(
            text(fav_char)
                .size(17)
                .color(if item.is_favorite {
                    Color::from_rgb8(243, 139, 168)
                } else {
                    Color::from_rgba8(255, 255, 255, 0.8)
                })
                .align_x(Horizontal::Center)
                .align_y(Vertical::Center),
        )
        .padding([4, 10])
        .on_press(Message::ToggleFavorite(filtered_idx))
        .style(|_theme, status| button::Style {
            background: Some(Background::Color(if status == button::Status::Hovered {
                Color::from_rgba8(243, 139, 168, 0.25)
            } else {
                Color::from_rgba8(10, 12, 18, 0.6)
            })),
            border: Border {
                radius: 14.0.into(),
                color: Color::from_rgba8(255, 255, 255, 0.12),
                width: 1.0,
            },
            ..button::Style::default()
        });

        let mut top_row =
            row![top_left, Space::new().width(Length::Fill)].align_y(Vertical::Center);
        if emphasis > 0.5 {
            top_row = top_row.push(fav_btn);
        }
        let top_container = container(top_row).padding([12, 16]).width(Length::Fill);

        let overlay_column = column![top_container, Space::new().height(Length::Fill),]
            .width(Length::Fill)
            .height(Length::Fill);

        let dim_alpha = (distance * 0.30).min(0.65) * edge_fade;
        let dim_overlay = container(Space::new())
            .width(Length::Fill)
            .height(Length::Fill)
            .style(move |_| container::Style {
                background: Some(Background::Color(Color::from_rgba8(0, 0, 0, dim_alpha))),
                border: Border {
                    radius: card_radius.into(),
                    ..Border::default()
                },
                ..container::Style::default()
            });
        let card_stack = Stack::new()
            .push(img_layer)
            .push(dim_overlay)
            .push(overlay_column)
            .clip(true);

        let border_color = if emphasis > 0.0 {
            Color {
                r: accent.r * emphasis + 1.0 * (1.0 - emphasis),
                g: accent.g * emphasis + 1.0 * (1.0 - emphasis),
                b: accent.b * emphasis + 1.0 * (1.0 - emphasis),
                a: (emphasis + (1.0 - emphasis) * 0.08) * edge_fade,
            }
        } else {
            Color::from_rgba8(255, 255, 255, 0.08 * edge_fade)
        };
        let border_width = 1.0 + 0.5 * emphasis;

        let card_container = container(card_stack)
            .width(Length::Fixed(width))
            .height(Length::Fixed(CARD_HEIGHT))
            .style(move |_| container::Style {
                background: Some(Background::Color(if emphasis > 0.5 {
                    Color::from_rgb8(15, 17, 24)
                } else {
                    Color::from_rgba8(14, 16, 22, 0.85 * edge_fade)
                })),
                border: Border {
                    radius: card_radius.into(),
                    color: border_color,
                    width: border_width,
                },
                shadow: Shadow {
                    color: Color {
                        a: 0.30 * emphasis * edge_fade,
                        ..accent
                    },
                    offset: iced::Vector::ZERO,
                    blur_radius: 16.0 * edge_fade,
                },
                ..container::Style::default()
            });

        // Clicking the center card also applies the wallpaper
        let is_flanking = emphasis <= 0.5;
        mouse_area(
            button(card_container)
                .padding(0)
                .on_press(if emphasis > 0.5 {
                    Message::ApplyWallpaper(filtered_idx, true)
                } else {
                    Message::SelectWallpaper(filtered_idx)
                })
                .style(move |_theme, status| {
                    let is_hovered = is_flanking && status == button::Status::Hovered;
                    button::Style {
                        background: None,
                        text_color: Color::WHITE,
                        border: Border {
                            radius: card_radius.into(),
                            color: if is_hovered {
                                Color { a: 0.8, ..accent }
                            } else {
                                Color::TRANSPARENT
                            },
                            width: if is_hovered { 1.5 } else { 0.0 },
                        },
                        shadow: if is_hovered {
                            Shadow {
                                color: Color { a: 0.35, ..accent },
                                offset: iced::Vector::ZERO,
                                blur_radius: 12.0,
                            }
                        } else {
                            Shadow::default()
                        },
                        ..button::Style::default()
                    }
                }),
        )
        .on_right_press(Message::ApplyWallpaper(filtered_idx, false))
        .on_middle_press(Message::ToggleFavorite(filtered_idx))
        .into()
    }

    pub fn subscription(&self) -> Subscription<Message> {
        let events = iced::event::listen().map(Message::EventOccurred);
        if self.animation.is_some() {
            Subscription::batch([events, iced::window::frames().map(Message::AnimationFrame)])
        } else {
            events
        }
    }
}
