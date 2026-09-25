use iced::alignment::{Horizontal, Vertical};
use iced::keyboard::Key;
use iced::keyboard::key::Named;
use iced::widget::{Space, Stack, button, column, container, image, row, text, text_input};
use iced::{
    Background, Border, Color, ContentFit, Element, Event, Length, Shadow, Subscription, Task,
};
use std::collections::HashSet;

use crate::config::Config;
use crate::scanner::WallpaperItem;
use crate::theme::AppTheme;

#[derive(Debug, Clone)]
pub enum Message {
    SearchChanged(String),
    ToggleFavoritesView(bool),
    SelectWallpaper(usize),
    ApplyWallpaper(usize),
    WallpaperApplied(Result<(String, String), String>),
    ToggleFavorite(usize),
    NextWallpaper,
    PrevWallpaper,
    JumpWallpapers(isize),
    ApplyRandom,
    RefreshList,
    EventOccurred(Event),
    Close,
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
    selected_index: Option<usize>,
    applying: bool,
    error_message: Option<String>,
}

impl WallpaperSelectorApp {
    pub fn new(config: Config) -> (Self, Task<Message>) {
        let theme = AppTheme::load();
        let favorites = crate::favorites::load_favorites(&config.fav_file);
        let active_wallpaper = crate::favorites::read_active_wallpaper(&config.theme_dir);

        let all_wallpapers = crate::scanner::scan_wallpapers(
            &config.wallpaper_dir,
            &config.thumb_dir,
            &favorites,
            active_wallpaper.as_deref(),
        );

        let mut app = Self {
            config,
            theme,
            all_wallpapers,
            filtered_indices: Vec::new(),
            favorites,
            active_wallpaper,
            search_query: String::new(),
            show_only_favorites: false,
            selected_index: None,
            applying: false,
            error_message: None,
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

        app.prefetch_around_selected();

        (app, Task::none())
    }

    fn prefetch_around_selected(&self) {
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
        let query = self.search_query.trim().to_lowercase();
        let show_favs = self.show_only_favorites;

        self.filtered_indices = self
            .all_wallpapers
            .iter()
            .enumerate()
            .filter(|(_, item)| {
                if show_favs && !item.is_favorite {
                    return false;
                }
                if query.is_empty() {
                    return true;
                }
                item.name.to_lowercase().contains(&query)
                    || item.relative.to_lowercase().contains(&query)
            })
            .map(|(idx, _)| idx)
            .collect();

        if let Some(sel) = self.selected_index {
            if sel >= self.filtered_indices.len() {
                self.selected_index = if self.filtered_indices.is_empty() {
                    None
                } else {
                    Some(self.filtered_indices.len() - 1)
                };
            }
        } else if !self.filtered_indices.is_empty() {
            self.selected_index = Some(0);
        }

        self.prefetch_around_selected();
    }

    pub fn update(&mut self, message: Message) -> Task<Message> {
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
                self.selected_index = Some(filtered_idx);
                self.prefetch_around_selected();
                Task::none()
            }
            Message::NextWallpaper => {
                if let Some(sel) = self.selected_index {
                    if sel + 1 < self.filtered_indices.len() {
                        self.selected_index = Some(sel + 1);
                        self.prefetch_around_selected();
                    }
                }
                Task::none()
            }
            Message::PrevWallpaper => {
                if let Some(sel) = self.selected_index {
                    if sel > 0 {
                        self.selected_index = Some(sel - 1);
                        self.prefetch_around_selected();
                    }
                }
                Task::none()
            }
            Message::JumpWallpapers(delta) => {
                if let Some(sel) = self.selected_index {
                    let count = self.filtered_indices.len();
                    if count > 0 {
                        let next = (sel as isize + delta).clamp(0, count as isize - 1) as usize;
                        self.selected_index = Some(next);
                        self.prefetch_around_selected();
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
                    return self.update(Message::ApplyWallpaper(random_idx));
                }
                Task::none()
            }
            Message::ApplyWallpaper(filtered_idx) => {
                if self.applying {
                    return Task::none();
                }
                if let Some(&item_idx) = self.filtered_indices.get(filtered_idx) {
                    if let Some(item) = self.all_wallpapers.get(item_idx) {
                        let path = item.path.clone();
                        let theme_ctl = self.config.theme_ctl.clone();
                        let name = item.name.clone();
                        let relative = item.relative.clone();
                        self.applying = true;
                        self.error_message = None;
                        return Task::perform(
                            async move {
                                crate::apply::apply_wallpaper(&path, &theme_ctl, true)
                                    .map(|()| (name, relative))
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
                    Ok((name, relative)) => {
                        crate::apply::notify_wallpaper(&name);
                        self.active_wallpaper = Some(relative.clone());
                        for w in &mut self.all_wallpapers {
                            w.is_active = w.relative == relative;
                        }
                        iced::exit()
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
                self.refilter();
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
                        return self.update(Message::ApplyWallpaper(sel));
                    }
                    Task::none()
                }
                Key::Named(Named::ArrowRight) => self.update(Message::NextWallpaper),
                Key::Named(Named::ArrowLeft) => self.update(Message::PrevWallpaper),
                Key::Named(Named::PageDown) => self.update(Message::JumpWallpapers(5)),
                Key::Named(Named::PageUp) => self.update(Message::JumpWallpapers(-5)),
                Key::Named(Named::Home) => {
                    self.selected_index = Some(0);
                    self.prefetch_around_selected();
                    Task::none()
                }
                Key::Named(Named::End) => {
                    if !self.filtered_indices.is_empty() {
                        self.selected_index = Some(self.filtered_indices.len() - 1);
                        self.prefetch_around_selected();
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
        let search_input = text_input("Search wallpapers...", &self.search_query)
            .on_input(Message::SearchChanged)
            .padding([6, 14])
            .size(12)
            .width(Length::Fixed(220.0))
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
            text("↻")
                .size(14)
                .align_x(Horizontal::Center)
                .align_y(Vertical::Center),
        )
        .padding([5, 10])
        .on_press(Message::RefreshList)
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

        let top_capsule = row![
            mode_pill,
            search_input,
            counter_pill,
            random_btn,
            refresh_btn,
            close_btn,
        ]
        .spacing(10)
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

        // --- Center Slices Carousel ---
        let carousel_content = self.build_carousel();

        // --- Bottom Micro Bar ---
        let bottom_hint = text(if let Some(error) = &self.error_message {
            error.as_str()
        } else if self.applying {
            "Applying wallpaper…"
        } else {
            "← / → or Scroll: Navigate  •  Enter or Click: Apply  •  F: Favorite  •  R: Random  •  Esc: Close"
        })
        .size(11)
        .color(if self.error_message.is_some() {
            Color::from_rgb8(252, 165, 165)
        } else {
            Color::from_rgba8(160, 170, 195, 0.7)
        });

        let bottom_bar = container(
            row![
                Space::new().width(Length::Fill),
                bottom_hint,
                Space::new().width(Length::Fill),
            ]
            .align_y(Vertical::Center),
        )
        .padding([4, 16])
        .width(Length::Fill);

        // --- Fullscreen Transparent Overlay (skwd-wall style) ---
        container(
            column![
                Space::new().height(Length::Fixed(20.0)),
                top_bar,
                Space::new().height(Length::Fill),
                carousel_content,
                Space::new().height(Length::Fill),
                bottom_bar,
                Space::new().height(Length::Fixed(14.0)),
            ]
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

    fn build_carousel<'a>(&'a self) -> Element<'a, Message> {
        if self.filtered_indices.is_empty() {
            return container(
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
            .into();
        }

        let current = self.selected_index.unwrap_or(0);
        let total = self.filtered_indices.len();
        const SLICE_COUNT: usize = 2; // 2 left + 1 center + 2 right = 5 slices matching skwd-wall frame 1

        let mut row_items = row![].spacing(10).align_y(Vertical::Center);

        // Flanking Left Slices
        let left_start = current.saturating_sub(SLICE_COUNT);
        for idx in left_start..current {
            let dist = current - idx;
            let item_idx = self.filtered_indices[idx];
            let item = &self.all_wallpapers[item_idx];
            row_items = row_items.push(self.build_slice_card(item, idx, dist));
        }

        // Expanded Center Card
        let center_item_idx = self.filtered_indices[current];
        let center_item = &self.all_wallpapers[center_item_idx];
        row_items = row_items.push(self.build_center_card(center_item, current));

        // Flanking Right Slices
        let right_end = (current + SLICE_COUNT).min(total.saturating_sub(1));
        if current < total {
            for idx in (current + 1)..=right_end {
                let dist = idx - current;
                let item_idx = self.filtered_indices[idx];
                let item = &self.all_wallpapers[item_idx];
                row_items = row_items.push(self.build_slice_card(item, idx, dist));
            }
        }

        container(row_items)
            .width(Length::Fill)
            .height(Length::Fill)
            .align_x(Horizontal::Center)
            .align_y(Vertical::Center)
            .into()
    }

    /// Center expanded card matching skwd-wall Frame 1
    fn build_center_card<'a>(
        &'a self,
        item: &'a WallpaperItem,
        filtered_idx: usize,
    ) -> Element<'a, Message> {
        let accent = self.theme.accent;

        // Image layer (reads thumbnail from disk; NEVER generates synchronously)
        let img_layer: Element<'_, Message> = if item.thumb_path.exists() {
            image(item.thumb_path.clone())
                .width(Length::Fill)
                .height(Length::Fill)
                .content_fit(ContentFit::Cover)
                .border_radius(14.0)
                .into()
        } else {
            container(Space::new())
                .width(Length::Fill)
                .height(Length::Fill)
                .style(|_| container::Style {
                    background: Some(Background::Color(Color::from_rgb8(18, 20, 28))),
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
        let mut top_left = row![
            text(display_name)
                .size(10)
                .color(Color::from_rgba8(255, 255, 255, 0.85)),
        ]
        .spacing(8)
        .align_y(Vertical::Center);

        if item.is_active {
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

        let top_container = container(
            row![top_left, Space::new().width(Length::Fill), fav_btn,].align_y(Vertical::Center),
        )
        .padding([12, 16])
        .width(Length::Fill);

        let overlay_column = column![top_container, Space::new().height(Length::Fill),]
            .width(Length::Fill)
            .height(Length::Fill);

        let card_stack = Stack::new().push(img_layer).push(overlay_column).clip(true);

        let card_container = container(card_stack)
            .width(Length::Fixed(540.0))
            .height(Length::Fixed(337.5))
            .style(move |_| container::Style {
                background: Some(Background::Color(Color::from_rgb8(15, 17, 24))),
                border: Border {
                    radius: 14.0.into(),
                    color: accent,
                    width: 1.5,
                },
                shadow: Shadow {
                    color: Color { a: 0.30, ..accent },
                    offset: iced::Vector::ZERO,
                    blur_radius: 16.0,
                },
                ..container::Style::default()
            });

        // Clicking the center card also applies the wallpaper
        button(card_container)
            .padding(0)
            .on_press(Message::ApplyWallpaper(filtered_idx))
            .style(|_theme, _status| button::Style {
                background: None,
                text_color: Color::WHITE,
                border: Border::default(),
                shadow: Shadow::default(),
                ..button::Style::default()
            })
            .into()
    }

    /// Slim flanking vertical slice card (matching skwd-wall Frame 1)
    fn build_slice_card<'a>(
        &'a self,
        item: &'a WallpaperItem,
        filtered_idx: usize,
        dist: usize,
    ) -> Element<'a, Message> {
        let accent = self.theme.accent;

        let img_layer: Element<'_, Message> = if item.thumb_path.exists() {
            image(item.thumb_path.clone())
                .width(Length::Fill)
                .height(Length::Fill)
                .content_fit(ContentFit::Cover)
                .border_radius(12.0)
                .into()
        } else {
            container(Space::new())
                .width(Length::Fill)
                .height(Length::Fill)
                .style(|_| container::Style {
                    background: Some(Background::Color(Color::from_rgb8(18, 20, 28))),
                    ..container::Style::default()
                })
                .into()
        };

        let dim_alpha = match dist {
            1 => 0.35,
            2 => 0.60,
            _ => 0.75,
        };

        let dim_overlay = container(Space::new())
            .width(Length::Fill)
            .height(Length::Fill)
            .style(move |_| container::Style {
                background: Some(Background::Color(Color::from_rgba8(0, 0, 0, dim_alpha))),
                border: Border {
                    radius: 12.0.into(),
                    ..Border::default()
                },
                ..container::Style::default()
            });

        let slice_stack = Stack::new().push(img_layer).push(dim_overlay).clip(true);

        let slice_container = container(slice_stack)
            .width(Length::Fixed(120.0))
            .height(Length::Fixed(337.5))
            .style(move |_| container::Style {
                background: Some(Background::Color(Color::from_rgb8(14, 16, 22))),
                border: Border {
                    radius: 12.0.into(),
                    color: Color::from_rgba8(255, 255, 255, 0.08),
                    width: 1.0,
                },
                ..container::Style::default()
            });

        button(slice_container)
            .padding(0)
            .on_press(Message::SelectWallpaper(filtered_idx))
            .style(move |_theme, status| {
                let is_hovered = status == button::Status::Hovered;
                button::Style {
                    background: None,
                    text_color: Color::WHITE,
                    border: Border {
                        radius: 12.0.into(),
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
            })
            .into()
    }

    pub fn subscription(&self) -> Subscription<Message> {
        iced::event::listen().map(Message::EventOccurred)
    }
}
