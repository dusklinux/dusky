use iced_core::alignment::{Horizontal, Vertical};
use iced_core::keyboard::Key;
use iced_core::keyboard::key::Named;
use iced_core::{Background, Border, Color, ContentFit, Event, Length, Padding, Shadow, Vector};
use iced_futures::Subscription;
use iced_runtime::Task;
use iced_widget::scrollable::AbsoluteOffset;
use iced_widget::{
    Float, Space, Stack, button, column, container, image, mouse_area, row, scrollable, text,
    text_input, tooltip,
};

type Element<'a, Message> =
    iced_core::Element<'a, Message, iced_core::Theme, crate::renderer::Renderer>;

use std::collections::HashSet;
use std::path::PathBuf;

use crate::config::{Config, MotionProfile, Preferences, ViewLayout};
use crate::scanner::WallpaperItem;
use crate::theme::AppTheme;

#[derive(Debug, Clone)]
pub enum Message {
    SearchChanged(String),
    FocusSearch,
    SearchSubmitted,
    SearchTab,
    SearchEscape,
    SearchFocusChanged(bool),
    CheckSearchFocus,
    YankPath(bool),
    ClipboardWritten(Result<String, String>),
    NextFavorite,
    PrevFavorite,
    ToggleFavoritesView(bool),
    ToggleColorFilter(u8),
    CycleSortMode,
    CycleMotionProfile,
    ToggleViewLayout,
    GridScroll(f32, f32),
    WindowOpened(iced_core::window::Id),
    WindowResized(iced_core::Size),
    SelectWallpaper(usize),
    ApplyWallpaper(usize, bool),
    WallpaperApplied(Result<String, String>),
    ToggleFavorite(usize),
    NextWallpaper,
    PrevWallpaper,
    JumpWallpapers(isize),
    ApplyRandom,
    RefreshList,
    LibraryLoaded(LibrarySnapshot),
    ActiveWallpaperLoaded(u64, Option<String>, Option<String>),
    ThumbnailsReady(
        u64,
        Vec<(
            PathBuf,
            crate::cache::ThumbStatus,
            Option<u8>,
            Option<image::Handle>,
        )>,
    ),
    AnimationFrame(std::time::Instant),
    EventOccurred(Event),
    Close,
}

const SLICE_WIDTH: f32 = 120.0;
const EXPANDED_WIDTH: f32 = 540.0;
const CARD_HEIGHT: f32 = 337.5;
const CARD_GAP: f32 = 10.0;

fn unfocus_search() -> Task<Message> {
    iced_runtime::task::widget(iced_core::widget::operation::focusable::unfocus())
}

#[derive(Debug, Clone)]
pub struct LibrarySnapshot {
    wallpapers: Vec<WallpaperItem>,
    favorites: HashSet<String>,
    active: Option<String>,
    theme: AppTheme,
    generated: usize,
    failed: usize,
    refreshed: bool,
    ready: HashSet<PathBuf>,
    color_cache: std::collections::HashMap<String, u8>,
    colors_dirty: bool,
    error: Option<String>,
    warning: Option<String>,
}

fn load_library(config: Config, refreshed: bool) -> LibrarySnapshot {
    let favorites = match crate::favorites::load_favorites(&config.fav_file) {
        Ok(favorites) => favorites,
        Err(error) => {
            return LibrarySnapshot {
                wallpapers: Vec::new(),
                favorites: HashSet::new(),
                active: None,
                theme: AppTheme::load(),
                generated: 0,
                failed: 0,
                refreshed,
                ready: HashSet::new(),
                color_cache: std::collections::HashMap::new(),
                colors_dirty: false,
                error: Some(format!("Could not read favorites: {error}")),
                warning: None,
            };
        }
    };
    let active = if refreshed {
        crate::favorites::read_active_wallpaper(&config.theme_dir)
    } else {
        crate::favorites::read_tracked_wallpaper(&config.theme_dir)
    };
    let mut wallpapers = match crate::scanner::scan_wallpapers(
        &config.wallpaper_dir,
        &config.thumb_dir,
        &favorites,
        active.as_deref(),
    ) {
        Ok(items) => items,
        Err(error) => {
            return LibrarySnapshot {
                wallpapers: Vec::new(),
                favorites,
                active,
                theme: AppTheme::load(),
                generated: 0,
                failed: 0,
                refreshed,
                ready: HashSet::new(),
                color_cache: std::collections::HashMap::new(),
                colors_dirty: false,
                error: Some(error),
                warning: None,
            };
        }
    };
    let (generated, failed, prune_error) = if refreshed {
        let stats = crate::cache::batch_generate_thumbs(&wallpapers, false);
        let prune_error = crate::cache::prune_thumbnails(&wallpapers, &config.thumb_dir)
            .err()
            .map(|error| format!("Could not prune thumbnails: {error}"));
        (stats.generated, stats.failed, prune_error)
    } else {
        (0, 0, None)
    };
    let (mut color_cache, saved) = if refreshed {
        let (_, cache, saved) = crate::color::ensure_color_cache(&wallpapers, &config.colors_file);
        (cache, saved)
    } else {
        // Startup only reads cached results. Missing colors are computed by the
        // bounded preview loader, after the window is usable.
        (crate::color::load_color_cache(&config.colors_file), Ok(()))
    };
    let old_color_count = color_cache.len();
    if !refreshed {
        let wanted: HashSet<_> = wallpapers
            .iter()
            .filter_map(|item| item.thumb_path.file_name())
            .collect();
        color_cache.retain(|key, _| wanted.contains(std::ffi::OsStr::new(key)));
    }
    let colors_dirty = saved.is_err() || old_color_count != color_cache.len();
    let color_error = saved
        .err()
        .map(|error| format!("Could not save wallpaper colors: {error}"));
    for item in &mut wallpapers {
        if let Some(&bucket) = item
            .thumb_path
            .file_name()
            .and_then(|name| color_cache.get(name.to_string_lossy().as_ref()))
        {
            item.color_bucket = Some(bucket);
        }
    }
    LibrarySnapshot {
        wallpapers,
        favorites,
        active,
        theme: AppTheme::load(),
        generated,
        failed,
        refreshed,
        ready: HashSet::new(),
        color_cache,
        colors_dirty,
        error: None,
        warning: color_error.or(prune_error),
    }
}

struct CarouselAnimation {
    target: f32,
    velocity: f32,
    last_tick: std::time::Instant,
    profile: MotionProfile,
}

impl CarouselAnimation {
    fn tick(&mut self, position: &mut f32, now: std::time::Instant) -> bool {
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
            let accel =
                -omega * omega * (*position - self.target) - 2.0 * zeta * omega * self.velocity;
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

pub struct DuskyPapersApp {
    config: Config,
    theme: AppTheme,
    all_wallpapers: Vec<WallpaperItem>,
    filtered_indices: Vec<usize>,
    favorites: HashSet<String>,
    active_wallpaper: Option<String>,
    search_query: String,
    search_focused: bool,
    last_g_press: Option<std::time::Instant>,
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
    animation: Option<CarouselAnimation>,
    visual_position: f32,
    grid_scroll_offset: f32,
    grid_viewport_height: f32,
    window_width: f32,
    wheel_pixels: f32,
    ready_thumbs: HashSet<PathBuf>,
    failed_thumbs: HashSet<PathBuf>,
    thumb_handles: std::collections::HashMap<PathBuf, image::Handle>,
    color_cache: std::collections::HashMap<String, u8>,
    colors_dirty: bool,
    color_prefetch_cursor: usize,
    prefetch_running: bool,
    library_generation: u64,
    launch_instant: std::time::Instant,
}

impl DuskyPapersApp {
    fn grid_columns(&self) -> usize {
        (((self.window_width - 32.0 + 14.0) / 264.0).floor() as usize).max(1)
    }

    fn grid_card_width(&self) -> f32 {
        (self.window_width - 32.0).clamp(80.0, 250.0)
    }

    fn thumbnail_handle(&self, item: &WallpaperItem) -> image::Handle {
        self.thumb_handles
            .get(&item.thumb_path)
            .cloned()
            .unwrap_or_else(|| image::Handle::from_path(item.thumb_path.clone()))
    }

    pub fn new(config: Config) -> (Self, Task<Message>) {
        let preferences = Preferences::load(&config.preferences_file);
        let loader_config = config.clone();
        let app = Self {
            config,
            theme: AppTheme::default(),
            all_wallpapers: Vec::new(),
            filtered_indices: Vec::new(),
            favorites: HashSet::new(),
            active_wallpaper: None,
            search_query: String::new(),
            search_focused: false,
            last_g_press: None,
            show_only_favorites: false,
            selected_color: None,
            sort_mode: preferences.sort_mode,
            motion_profile: preferences.motion_profile,
            view_layout: preferences.view_layout,
            random_seed: fastrand::u64(..),
            selected_index: None,
            applying: false,
            refreshing: true,
            error_message: None,
            refresh_status: Some("Loading library…".to_owned()),
            animation: None,
            visual_position: 0.0,
            grid_scroll_offset: 0.0,
            grid_viewport_height: 520.0,
            window_width: 1280.0,
            wheel_pixels: 0.0,
            ready_thumbs: HashSet::new(),
            failed_thumbs: HashSet::new(),
            thumb_handles: std::collections::HashMap::new(),
            color_cache: std::collections::HashMap::new(),
            colors_dirty: false,
            color_prefetch_cursor: 0,
            prefetch_running: false,
            library_generation: 0,
            launch_instant: std::time::Instant::now(),
        };

        (
            app,
            Task::perform(
                async move { load_library(loader_config, false) },
                Message::LibraryLoaded,
            ),
        )
    }

    fn prefetch_around_selected(&mut self) -> Task<Message> {
        if self.refreshing || self.prefetch_running {
            return Task::none();
        }
        let mut indices = Vec::new();
        if let Some(sel) = self.selected_index {
            let count = self.filtered_indices.len();
            if count > 0 {
                indices.push(sel);
                for distance in 1..=6 {
                    if sel + distance < count {
                        indices.push(sel + distance);
                    }
                    if let Some(index) = sel.checked_sub(distance) {
                        indices.push(index);
                    }
                }
            }
        }
        if self.view_layout == ViewLayout::Grid {
            let columns = self.grid_columns();
            let first_row = (self.grid_scroll_offset / 154.0).floor().max(0.0) as usize;
            let visible_rows = (self.grid_viewport_height / 154.0).ceil() as usize + 2;
            let first = first_row.saturating_sub(1) * columns;
            let last = ((first_row + visible_rows) * columns).min(self.filtered_indices.len());
            indices.extend(first..last);
        }
        // Keep decoded previews only for the viewport and its nearby items.
        // Revisiting distant items reuses their existing disk previews.
        let wanted: HashSet<_> = indices
            .iter()
            .filter_map(|&index| {
                self.filtered_indices
                    .get(index)
                    .map(|&item| self.all_wallpapers[item].thumb_path.clone())
            })
            .collect();
        self.thumb_handles.retain(|path, _| wanted.contains(path));
        self.ready_thumbs.retain(|path| wanted.contains(path));
        let mut seen = HashSet::new();
        let mut missing = Vec::new();
        for index in indices {
            let Some(item) = self
                .filtered_indices
                .get(index)
                .and_then(|&index| self.all_wallpapers.get(index))
            else {
                continue;
            };
            if !self.ready_thumbs.contains(&item.thumb_path)
                && !self.failed_thumbs.contains(&item.thumb_path)
                && seen.insert(item.thumb_path.clone())
            {
                missing.push((
                    item.path.clone(),
                    item.thumb_path.clone(),
                    item.color_bucket,
                ));
                if missing.len() == 8 {
                    break;
                }
            }
        }
        if self.selected_color.is_some() {
            while missing.len() < 8 && self.color_prefetch_cursor < self.all_wallpapers.len() {
                let item = &self.all_wallpapers[self.color_prefetch_cursor];
                self.color_prefetch_cursor += 1;
                if item.color_bucket.is_none()
                    && (!self.show_only_favorites || item.is_favorite)
                    && !self.failed_thumbs.contains(&item.thumb_path)
                    && seen.insert(item.thumb_path.clone())
                {
                    missing.push((
                        item.path.clone(),
                        item.thumb_path.clone(),
                        item.color_bucket,
                    ));
                }
            }
        }
        if missing.is_empty() {
            if self.colors_dirty {
                match crate::color::save_color_cache(&self.config.colors_file, &self.color_cache) {
                    Ok(()) => self.colors_dirty = false,
                    Err(error) => {
                        self.error_message =
                            Some(format!("Could not save wallpaper colors: {error}"))
                    }
                }
            }
            return Task::none();
        }
        self.prefetch_running = true;
        let generation = self.library_generation;
        Task::perform(
            async move {
                let results: Vec<_> = missing
                    .into_iter()
                    .map(|(source, thumb, cached_color)| {
                        let mut status = crate::cache::generate_thumb(&source, &thumb);
                        let color = if status == crate::cache::ThumbStatus::Failed {
                            None
                        } else {
                            cached_color.or_else(|| crate::color::extract_color_from_file(&thumb))
                        };
                        if status != crate::cache::ThumbStatus::Failed && color.is_none() {
                            let _ = std::fs::remove_file(&thumb);
                            status = crate::cache::ThumbStatus::Failed;
                        }
                        // Iced uploads these small RGBA previews synchronously;
                        // decode on this worker to avoid relying on a later redraw.
                        let handle = (status != crate::cache::ThumbStatus::Failed)
                            .then(|| ::image::open(&thumb).ok())
                            .flatten()
                            .map(|decoded| {
                                let rgba = decoded.into_rgba8();
                                image::Handle::from_rgba(
                                    rgba.width(),
                                    rgba.height(),
                                    rgba.into_raw(),
                                )
                            });
                        (thumb, status, color, handle)
                    })
                    .collect();
                (generation, results)
            },
            |(generation, results)| Message::ThumbnailsReady(generation, results),
        )
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
                    if item.color_bucket != Some(color_bucket) {
                        return false;
                    }
                }
                if query.is_empty() {
                    return true;
                }
                item.search_key.contains(&query)
            })
            .map(|(idx, _)| idx)
            .collect();

        // Apply sorting
        match self.sort_mode {
            crate::config::SortMode::Name => {
                filtered.sort_by(|&a, &b| {
                    crate::scanner::natural_cmp(
                        &self.all_wallpapers[a].relative,
                        &self.all_wallpapers[b].relative,
                    )
                    .then_with(|| {
                        self.all_wallpapers[a]
                            .relative
                            .cmp(&self.all_wallpapers[b].relative)
                    })
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
                fastrand::Rng::with_seed(self.random_seed).shuffle(&mut filtered);
            }
        }

        self.filtered_indices = filtered;

        // Retain the previously selected wallpaper if present in the new set
        self.selected_index = prev_selected_relative.and_then(|rel| {
            self.filtered_indices
                .iter()
                .position(|&item_idx| self.all_wallpapers[item_idx].relative == rel)
        });

        if self.selected_index.is_none() && !self.filtered_indices.is_empty() {
            self.selected_index = Some(0);
        }

        self.animation = None;
        self.visual_position = self.selected_index.unwrap_or(0) as f32;
        self.grid_scroll_offset = 0.0;
    }

    fn select_wallpaper(&mut self, next: usize) -> Task<Message> {
        if next >= self.filtered_indices.len() || self.selected_index == Some(next) {
            return Task::none();
        }
        self.selected_index = Some(next);
        if self.view_layout == ViewLayout::Carousel && self.motion_profile.is_enabled() {
            if let Some(animation) = &mut self.animation {
                animation.target = next as f32;
                animation.profile = self.motion_profile;
            } else {
                self.animation = Some(CarouselAnimation {
                    target: next as f32,
                    velocity: 0.0,
                    last_tick: std::time::Instant::now(),
                    profile: self.motion_profile,
                });
            }
        } else {
            self.animation = None;
            self.visual_position = next as f32;
        }

        if self.view_layout == ViewLayout::Grid {
            let row = next / self.grid_columns();
            let row_top = row as f32 * 154.0;
            let row_bottom = row_top + 154.0;
            let visible_height = self.grid_viewport_height;
            if row_top < self.grid_scroll_offset {
                return iced_runtime::widget::operation::scroll_to(
                    iced_core::widget::Id::new("grid_scroll"),
                    AbsoluteOffset {
                        x: None,
                        y: Some(row_top),
                    },
                );
            } else if row_bottom > self.grid_scroll_offset + visible_height {
                let target = (row_bottom - visible_height).max(0.0);
                return iced_runtime::widget::operation::scroll_to(
                    iced_core::widget::Id::new("grid_scroll"),
                    AbsoluteOffset {
                        x: None,
                        y: Some(target),
                    },
                );
            }
        }

        Task::none()
    }

    pub fn update(&mut self, message: Message) -> Task<Message> {
        let reset_scroll = matches!(
            &message,
            Message::LibraryLoaded(..)
                | Message::SearchChanged(..)
                | Message::SearchEscape
                | Message::ToggleFavoritesView(..)
                | Message::ToggleColorFilter(..)
                | Message::CycleSortMode
                | Message::ToggleFavorite(..)
        );
        let prefetch = matches!(
            &message,
            Message::LibraryLoaded(..)
                | Message::ActiveWallpaperLoaded(..)
                | Message::ThumbnailsReady(..)
                | Message::SelectWallpaper(..)
                | Message::NextWallpaper
                | Message::PrevWallpaper
                | Message::JumpWallpapers(..)
                | Message::GridScroll(..)
                | Message::WindowResized(..)
                | Message::SearchChanged(..)
                | Message::SearchEscape
                | Message::ToggleFavoritesView(..)
                | Message::ToggleColorFilter(..)
                | Message::CycleSortMode
                | Message::ToggleViewLayout
                | Message::ToggleFavorite(..)
        );
        let previous_selection = self.selected_index;
        let task = self.update_inner(message);
        // Keyboard shortcuts can select directly instead of emitting a
        // navigation message. Every selection change needs its previews.
        if prefetch || self.selected_index != previous_selection {
            let scroll_target = if reset_scroll && self.view_layout == ViewLayout::Grid {
                let row = self.selected_index.unwrap_or(0) / self.grid_columns();
                let target = (row as f32 * 154.0 + 77.0 - self.grid_viewport_height * 0.5).max(0.0);
                self.grid_scroll_offset = target;
                Some(target)
            } else {
                None
            };
            let prefetch_task = self.prefetch_around_selected();
            if let Some(target) = scroll_target {
                Task::batch([
                    task,
                    prefetch_task,
                    iced_runtime::widget::operation::scroll_to(
                        iced_core::widget::Id::new("grid_scroll"),
                        AbsoluteOffset {
                            x: None,
                            y: Some(target),
                        },
                    ),
                ])
            } else {
                Task::batch([task, prefetch_task])
            }
        } else {
            task
        }
    }

    fn update_inner(&mut self, message: Message) -> Task<Message> {
        if !matches!(
            &message,
            Message::RefreshList
                | Message::LibraryLoaded(..)
                | Message::ActiveWallpaperLoaded(..)
                | Message::ThumbnailsReady(..)
                | Message::AnimationFrame(..)
                | Message::WindowResized(..)
                | Message::YankPath(_)
                | Message::EventOccurred(_)
        ) {
            self.refresh_status = None;
        }
        match message {
            Message::SearchChanged(query) => {
                self.search_focused = true;
                self.search_query = query;
                self.refilter();
                Task::none()
            }
            Message::FocusSearch => {
                self.search_focused = true;
                iced_runtime::widget::operation::focus(iced_core::widget::Id::new("search_input"))
            }
            Message::SearchSubmitted => {
                self.search_focused = false;
                unfocus_search()
            }
            Message::SearchTab => {
                if self.search_focused {
                    self.search_focused = false;
                    unfocus_search()
                } else {
                    Task::none()
                }
            }
            Message::SearchEscape => {
                if !self.search_focused {
                    return Task::none();
                }
                self.search_focused = false;
                self.search_query.clear();
                self.refilter();
                unfocus_search()
            }
            Message::SearchFocusChanged(focused) => {
                self.search_focused = focused;
                Task::none()
            }
            Message::CheckSearchFocus => iced_runtime::widget::operation::is_focused(
                iced_core::widget::Id::new("search_input"),
            )
            .map(Message::SearchFocusChanged),
            Message::YankPath(filename_only) => {
                if let Some(sel) = self.selected_index {
                    if let Some(&item_idx) = self.filtered_indices.get(sel) {
                        if let Some(item) = self.all_wallpapers.get(item_idx) {
                            let text_to_copy = if filename_only {
                                item.name.clone()
                            } else {
                                item.path.to_string_lossy().to_string()
                            };
                            return Task::perform(
                                async move {
                                    let output = std::process::Command::new("wl-copy")
                                        .arg("--")
                                        .arg(&text_to_copy)
                                        .output()
                                        .map_err(|error| error.to_string())?;
                                    if output.status.success() {
                                        Ok(text_to_copy)
                                    } else {
                                        Err(String::from_utf8_lossy(&output.stderr)
                                            .trim()
                                            .to_owned())
                                    }
                                },
                                Message::ClipboardWritten,
                            );
                        }
                    }
                }
                Task::none()
            }
            Message::ClipboardWritten(result) => {
                match result {
                    Ok(text) => self.refresh_status = Some(format!("Yanked: {text}")),
                    Err(error) => {
                        self.error_message = Some(format!("Could not copy path: {error}"))
                    }
                }
                Task::none()
            }
            Message::NextFavorite => {
                if let Some(curr) = self.selected_index {
                    let count = self.filtered_indices.len();
                    if count > 0 {
                        for offset in 1..count {
                            let next_idx = (curr + offset) % count;
                            let item_idx = self.filtered_indices[next_idx];
                            if self.all_wallpapers[item_idx].is_favorite {
                                return self.select_wallpaper(next_idx);
                            }
                        }
                    }
                }
                Task::none()
            }
            Message::PrevFavorite => {
                if let Some(curr) = self.selected_index {
                    let count = self.filtered_indices.len();
                    if count > 0 {
                        for offset in 1..count {
                            let prev_idx = (curr + count - offset) % count;
                            let item_idx = self.filtered_indices[prev_idx];
                            if self.all_wallpapers[item_idx].is_favorite {
                                return self.select_wallpaper(prev_idx);
                            }
                        }
                    }
                }
                Task::none()
            }
            Message::ToggleFavoritesView(favs_only) => {
                self.search_focused = false;
                self.show_only_favorites = favs_only;
                self.color_prefetch_cursor = 0;
                self.refilter();
                Task::none()
            }
            Message::SelectWallpaper(filtered_idx) => {
                self.search_focused = false;
                self.select_wallpaper(filtered_idx)
            }
            Message::NextWallpaper => {
                if let Some(sel) = self.selected_index {
                    if sel + 1 < self.filtered_indices.len() {
                        return self.select_wallpaper(sel + 1);
                    }
                }
                Task::none()
            }
            Message::PrevWallpaper => {
                if let Some(sel) = self.selected_index {
                    if sel > 0 {
                        return self.select_wallpaper(sel - 1);
                    }
                }
                Task::none()
            }
            Message::JumpWallpapers(delta) => {
                if let Some(sel) = self.selected_index {
                    let count = self.filtered_indices.len();
                    if count > 0 {
                        let next = (sel as isize + delta).clamp(0, count as isize - 1) as usize;
                        return self.select_wallpaper(next);
                    }
                }
                Task::none()
            }
            Message::ApplyRandom => {
                if !self.filtered_indices.is_empty() {
                    let random_idx = fastrand::usize(..self.filtered_indices.len());
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
                self.color_prefetch_cursor = 0;
                if let Some(&item_idx) = self.filtered_indices.get(filtered_idx) {
                    if let Some(item) = self.all_wallpapers.get(item_idx) {
                        let lock_path = self.config.theme_dir.join("favorites.lock");
                        match crate::favorites::toggle_favorite(
                            &self.config.fav_file,
                            &lock_path,
                            &item.relative,
                        ) {
                            Ok(favorites) => {
                                self.favorites = favorites;
                                for wallpaper in &mut self.all_wallpapers {
                                    wallpaper.is_favorite =
                                        self.favorites.contains(&wallpaper.relative);
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
                self.library_generation = self.library_generation.wrapping_add(1);
                self.prefetch_running = false;
                self.color_prefetch_cursor = 0;
                self.refreshing = true;
                self.refresh_status = Some("Refreshing library…".to_owned());
                let config = self.config.clone();
                Task::perform(
                    async move { load_library(config, true) },
                    Message::LibraryLoaded,
                )
            }
            Message::LibraryLoaded(snapshot) => {
                if let Some(error) = snapshot.error {
                    self.refreshing = false;
                    self.error_message = Some(error);
                    self.refresh_status = None;
                    return Task::none();
                }
                self.library_generation = self.library_generation.wrapping_add(1);
                self.prefetch_running = false;
                self.color_prefetch_cursor = 0;
                let selected = self.selected_index.and_then(|index| {
                    self.filtered_indices
                        .get(index)
                        .and_then(|&item| self.all_wallpapers.get(item))
                        .map(|item| item.relative.clone())
                });
                self.theme = snapshot.theme;
                self.ready_thumbs = snapshot.ready;
                self.color_cache = snapshot.color_cache;
                self.colors_dirty = snapshot.colors_dirty;
                self.failed_thumbs.clear();
                self.thumb_handles
                    .retain(|path, _| self.ready_thumbs.contains(path));
                self.favorites = snapshot.favorites;
                self.active_wallpaper = snapshot.active;
                self.all_wallpapers = snapshot.wallpapers;
                self.selected_index = None;
                self.filtered_indices.clear();
                self.refilter();
                if let Some(index) = self
                    .filtered_indices
                    .iter()
                    .position(|&item| {
                        selected.as_deref() == Some(self.all_wallpapers[item].relative.as_str())
                    })
                    .or_else(|| {
                        self.filtered_indices
                            .iter()
                            .position(|&item| self.all_wallpapers[item].is_active)
                    })
                {
                    self.selected_index = Some(index);
                }
                self.visual_position = self.selected_index.unwrap_or(0) as f32;
                self.refreshing = false;
                if snapshot.failed > 0 {
                    self.error_message = Some(format!(
                        "Could not update {} wallpaper previews",
                        snapshot.failed
                    ));
                } else {
                    self.error_message = snapshot.warning;
                    self.refresh_status = Some(if snapshot.refreshed {
                        format!(
                            "Library refreshed: {} wallpapers, {} previews updated",
                            self.all_wallpapers.len(),
                            snapshot.generated
                        )
                    } else {
                        format!("{} wallpapers loaded", self.all_wallpapers.len())
                    });
                }
                if snapshot.refreshed {
                    Task::none()
                } else {
                    let generation = self.library_generation;
                    let expected = self.active_wallpaper.clone();
                    let theme_dir = self.config.theme_dir.clone();
                    Task::perform(
                        async move {
                            // Batched Iced tasks share a stream poll. Waiting for
                            // the subprocess here would also stall preview work.
                            let (sender, receiver) =
                                iced_futures::futures::channel::oneshot::channel();
                            let started = std::thread::Builder::new()
                                .name("wallpaper-active".into())
                                .spawn(move || {
                                    let active =
                                        crate::favorites::read_active_wallpaper(&theme_dir);
                                    let _ = sender.send(active);
                                });
                            let active = if started.is_ok() {
                                receiver.await.unwrap_or_else(|_| expected.clone())
                            } else {
                                expected.clone()
                            };
                            (generation, expected, active)
                        },
                        |(generation, expected, active)| {
                            Message::ActiveWallpaperLoaded(generation, expected, active)
                        },
                    )
                }
            }
            Message::ActiveWallpaperLoaded(generation, expected, active) => {
                if generation != self.library_generation
                    || self.active_wallpaper != expected
                    || active == expected
                    || self.applying
                {
                    return Task::none();
                }
                let follow_active = self
                    .selected_index
                    .and_then(|index| self.filtered_indices.get(index))
                    .is_some_and(|&index| self.all_wallpapers[index].is_active)
                    || (expected.is_none() && self.selected_index == Some(0));
                let canonical = active.as_deref().and_then(|path| {
                    let path = std::path::Path::new(path);
                    path.is_absolute()
                        .then(|| path.canonicalize().ok())
                        .flatten()
                });
                for item in &mut self.all_wallpapers {
                    item.is_active = active.as_deref().is_some_and(|id| {
                        id == item.relative
                            || std::path::Path::new(id) == item.path
                            || canonical.as_ref().is_some_and(|path| {
                                item.path
                                    .canonicalize()
                                    .is_ok_and(|resolved| &resolved == path)
                            })
                    });
                }
                self.active_wallpaper = active;
                if follow_active {
                    if let Some(index) = self
                        .filtered_indices
                        .iter()
                        .position(|&index| self.all_wallpapers[index].is_active)
                    {
                        return self.select_wallpaper(index);
                    }
                }
                Task::none()
            }
            Message::ThumbnailsReady(generation, results) => {
                if generation != self.library_generation || self.refreshing {
                    return Task::none();
                }
                self.prefetch_running = false;
                let mut filter_changed = false;
                for (path, status, color, handle) in results {
                    if status == crate::cache::ThumbStatus::Failed {
                        self.failed_thumbs.insert(path);
                        continue;
                    }
                    if let Some(handle) = handle {
                        self.ready_thumbs.insert(path.clone());
                        self.thumb_handles.insert(path.clone(), handle);
                    } else {
                        self.failed_thumbs.insert(path.clone());
                    }
                    if let Some(bucket) = color {
                        if let Some(name) = path.file_name() {
                            if self
                                .color_cache
                                .insert(name.to_string_lossy().into_owned(), bucket)
                                != Some(bucket)
                            {
                                self.colors_dirty = true;
                            }
                        }
                        for item in &mut self.all_wallpapers {
                            if item.thumb_path == path {
                                if item.color_bucket.is_none()
                                    && self.selected_color == Some(bucket)
                                {
                                    filter_changed = true;
                                }
                                item.color_bucket = Some(bucket);
                            }
                        }
                    }
                }
                if filter_changed {
                    let scroll = self.grid_scroll_offset;
                    self.refilter();
                    self.grid_scroll_offset = scroll;
                }
                Task::none()
            }
            Message::ToggleColorFilter(bucket) => {
                self.color_prefetch_cursor = 0;
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
                    self.random_seed = fastrand::u64(..);
                }
                let preferences = Preferences {
                    sort_mode: self.sort_mode,
                    motion_profile: self.motion_profile,
                    view_layout: self.view_layout,
                };
                if let Err(error) = preferences.save(&self.config.preferences_file) {
                    self.error_message =
                        Some(format!("Could not save Dusky Papers preferences: {error}"));
                }
                self.refilter();
                Task::none()
            }
            Message::CycleMotionProfile => {
                let next_profile = self.motion_profile.next();
                let preferences = Preferences {
                    sort_mode: self.sort_mode,
                    motion_profile: next_profile,
                    view_layout: self.view_layout,
                };
                if let Err(error) = preferences.save(&self.config.preferences_file) {
                    self.error_message =
                        Some(format!("Could not save Dusky Papers preferences: {error}"));
                }
                self.motion_profile = next_profile;
                if !next_profile.is_enabled() {
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
                    sort_mode: self.sort_mode,
                    motion_profile: self.motion_profile,
                    view_layout: next_layout,
                };
                if let Err(error) = preferences.save(&self.config.preferences_file) {
                    self.error_message =
                        Some(format!("Could not save Dusky Papers preferences: {error}"));
                }
                self.view_layout = next_layout;
                if next_layout == ViewLayout::Grid {
                    if let Some(sel) = self.selected_index {
                        let row = sel / self.grid_columns();
                        let target = (row as f32 * 154.0 - 154.0).max(0.0);
                        return iced_runtime::widget::operation::scroll_to(
                            iced_core::widget::Id::new("grid_scroll"),
                            AbsoluteOffset {
                                x: None,
                                y: Some(target),
                            },
                        );
                    }
                }
                Task::none()
            }
            Message::GridScroll(y, height) => {
                self.grid_scroll_offset = y;
                self.grid_viewport_height = height;
                Task::none()
            }
            Message::WindowOpened(id) => iced_runtime::window::size(id).map(Message::WindowResized),
            Message::WindowResized(size) => {
                self.window_width = size.width;
                if self.view_layout == ViewLayout::Grid {
                    let row = self.selected_index.unwrap_or(0) / self.grid_columns();
                    let target =
                        (row as f32 * 154.0 + 77.0 - self.grid_viewport_height * 0.5).max(0.0);
                    self.grid_scroll_offset = target;
                    iced_runtime::widget::operation::scroll_to(
                        iced_core::widget::Id::new("grid_scroll"),
                        AbsoluteOffset {
                            x: None,
                            y: Some(target),
                        },
                    )
                } else {
                    Task::none()
                }
            }
            Message::AnimationFrame(at) => {
                if let Some(animation) = &mut self.animation {
                    if !animation.tick(&mut self.visual_position, at) {
                        self.animation = None;
                    }
                }
                Task::none()
            }
            Message::Close => iced_runtime::exit(),
            Message::EventOccurred(Event::Mouse(iced_core::mouse::Event::WheelScrolled {
                delta,
            })) => {
                if self.view_layout == ViewLayout::Grid {
                    // Let scrollable handle wheel scrolling natively
                    Task::none()
                } else {
                    match delta {
                        iced_core::mouse::ScrollDelta::Lines { x, y } => {
                            if y < 0.0 || x > 0.0 {
                                return self.update(Message::NextWallpaper);
                            } else if y > 0.0 || x < 0.0 {
                                return self.update(Message::PrevWallpaper);
                            }
                        }
                        iced_core::mouse::ScrollDelta::Pixels { x, y } => {
                            let movement = if x.abs() > y.abs() { x } else { -y };
                            self.wheel_pixels += movement;
                            let steps = (self.wheel_pixels / 80.0).trunc() as isize;
                            if steps != 0 {
                                self.wheel_pixels -= steps as f32 * 80.0;
                                return self.update(Message::JumpWallpapers(steps));
                            }
                        }
                    }
                    Task::none()
                }
            }
            Message::EventOccurred(Event::Keyboard(iced_core::keyboard::Event::KeyPressed {
                key,
                modified_key,
                modifiers,
                ..
            })) => {
                // If search is focused, handle only Escape, Enter, and Up/Down navigation.
                if self.search_focused {
                    match key {
                        Key::Named(Named::Escape) => {
                            self.search_focused = false;
                            if !self.search_query.is_empty() {
                                self.search_query.clear();
                                self.refilter();
                            }
                            return unfocus_search();
                        }
                        Key::Named(Named::Enter) => {
                            self.search_focused = false;
                            return unfocus_search();
                        }
                        Key::Named(Named::ArrowDown) => {
                            if self.view_layout == ViewLayout::Grid {
                                return self
                                    .update(Message::JumpWallpapers(self.grid_columns() as isize));
                            } else {
                                return self.update(Message::NextWallpaper);
                            }
                        }
                        Key::Named(Named::ArrowUp) => {
                            if self.view_layout == ViewLayout::Grid {
                                return self.update(Message::JumpWallpapers(
                                    -(self.grid_columns() as isize),
                                ));
                            } else {
                                return self.update(Message::PrevWallpaper);
                            }
                        }
                        Key::Named(Named::ArrowLeft) => return self.update(Message::PrevWallpaper),
                        Key::Named(Named::ArrowRight) => {
                            return self.update(Message::NextWallpaper);
                        }
                        _ => return Task::none(),
                    }
                }

                // Normal Mode Keybindings:
                if modifiers.control() {
                    match key {
                        Key::Character(ref c) if c == "d" || c == "D" => {
                            self.last_g_press = None;
                            return self.update(Message::JumpWallpapers(15));
                        }
                        Key::Character(ref c) if c == "u" || c == "U" => {
                            self.last_g_press = None;
                            return self.update(Message::JumpWallpapers(-15));
                        }
                        Key::Character(ref c) if c == "f" || c == "F" => {
                            self.last_g_press = None;
                            return self.update(Message::JumpWallpapers(25));
                        }
                        Key::Character(ref c) if c == "b" || c == "B" => {
                            self.last_g_press = None;
                            return self.update(Message::JumpWallpapers(-25));
                        }
                        Key::Character(ref c) if c == "c" || c == "C" => {
                            return iced_runtime::exit();
                        }
                        _ => return Task::none(),
                    }
                }

                match modified_key {
                    Key::Named(Named::Escape) => {
                        self.last_g_press = None;
                        if !self.search_query.is_empty() {
                            self.search_query.clear();
                            self.refilter();
                            Task::none()
                        } else {
                            iced_runtime::exit()
                        }
                    }
                    Key::Named(Named::Enter) => {
                        self.last_g_press = None;
                        if let Some(sel) = self.selected_index {
                            return self.update(Message::ApplyWallpaper(sel, true));
                        }
                        Task::none()
                    }
                    Key::Named(Named::Space) => {
                        self.last_g_press = None;
                        if let Some(sel) = self.selected_index {
                            return self.update(Message::ApplyWallpaper(sel, false));
                        }
                        Task::none()
                    }
                    Key::Named(Named::ArrowRight) => {
                        self.last_g_press = None;
                        self.update(Message::NextWallpaper)
                    }
                    Key::Named(Named::ArrowLeft) => {
                        self.last_g_press = None;
                        self.update(Message::PrevWallpaper)
                    }
                    Key::Named(Named::ArrowUp) => {
                        self.last_g_press = None;
                        if self.view_layout == ViewLayout::Grid {
                            self.update(Message::JumpWallpapers(-(self.grid_columns() as isize)))
                        } else {
                            Task::none()
                        }
                    }
                    Key::Named(Named::ArrowDown) => {
                        self.last_g_press = None;
                        if self.view_layout == ViewLayout::Grid {
                            self.update(Message::JumpWallpapers(self.grid_columns() as isize))
                        } else {
                            Task::none()
                        }
                    }
                    Key::Named(Named::PageDown) => {
                        self.last_g_press = None;
                        self.update(Message::JumpWallpapers(25))
                    }
                    Key::Named(Named::PageUp) => {
                        self.last_g_press = None;
                        self.update(Message::JumpWallpapers(-25))
                    }
                    Key::Named(Named::Home) => {
                        self.last_g_press = None;
                        self.select_wallpaper(0)
                    }
                    Key::Named(Named::End) => {
                        self.last_g_press = None;
                        if !self.filtered_indices.is_empty() {
                            self.select_wallpaper(self.filtered_indices.len() - 1)
                        } else {
                            Task::none()
                        }
                    }
                    Key::Character(ref c) => match c.as_str() {
                        "h" => {
                            self.last_g_press = None;
                            self.update(Message::PrevWallpaper)
                        }
                        "l" => {
                            self.last_g_press = None;
                            self.update(Message::NextWallpaper)
                        }
                        "j" => {
                            self.last_g_press = None;
                            if self.view_layout == ViewLayout::Grid {
                                self.update(Message::JumpWallpapers(self.grid_columns() as isize))
                            } else {
                                self.update(Message::NextWallpaper)
                            }
                        }
                        "k" => {
                            self.last_g_press = None;
                            if self.view_layout == ViewLayout::Grid {
                                self.update(Message::JumpWallpapers(
                                    -(self.grid_columns() as isize),
                                ))
                            } else {
                                self.update(Message::PrevWallpaper)
                            }
                        }
                        "g" => {
                            if modifiers.shift() {
                                self.last_g_press = None;
                                if !self.filtered_indices.is_empty() {
                                    return self.select_wallpaper(self.filtered_indices.len() - 1);
                                } else {
                                    return Task::none();
                                }
                            }
                            if let Some(last) = self.last_g_press {
                                if last.elapsed().as_millis() < 500 {
                                    self.last_g_press = None;
                                    return self.select_wallpaper(0);
                                }
                            }
                            self.last_g_press = Some(std::time::Instant::now());
                            Task::none()
                        }
                        "G" => {
                            self.last_g_press = None;
                            if !self.filtered_indices.is_empty() {
                                self.select_wallpaper(self.filtered_indices.len() - 1)
                            } else {
                                Task::none()
                            }
                        }
                        "0" | "^" => {
                            self.last_g_press = None;
                            if let Some(sel) = self.selected_index {
                                if self.view_layout == ViewLayout::Grid {
                                    let columns = self.grid_columns();
                                    let row_start = (sel / columns) * columns;
                                    self.select_wallpaper(row_start)
                                } else {
                                    self.select_wallpaper(0)
                                }
                            } else {
                                Task::none()
                            }
                        }
                        "$" => {
                            self.last_g_press = None;
                            if let Some(sel) = self.selected_index {
                                if self.view_layout == ViewLayout::Grid {
                                    let columns = self.grid_columns();
                                    let row_end = ((sel / columns) * columns + columns - 1)
                                        .min(self.filtered_indices.len().saturating_sub(1));
                                    self.select_wallpaper(row_end)
                                } else {
                                    self.select_wallpaper(
                                        self.filtered_indices.len().saturating_sub(1),
                                    )
                                }
                            } else {
                                Task::none()
                            }
                        }
                        "/" => {
                            self.last_g_press = None;
                            self.update(Message::FocusSearch)
                        }
                        "n" => {
                            self.last_g_press = None;
                            self.update(Message::NextFavorite)
                        }
                        "N" => {
                            self.last_g_press = None;
                            self.update(Message::PrevFavorite)
                        }
                        "o" => {
                            self.last_g_press = None;
                            if let Some(sel) = self.selected_index {
                                self.update(Message::ApplyWallpaper(sel, true))
                            } else {
                                Task::none()
                            }
                        }
                        "O" => {
                            self.last_g_press = None;
                            if let Some(sel) = self.selected_index {
                                self.update(Message::ApplyWallpaper(sel, false))
                            } else {
                                Task::none()
                            }
                        }
                        "f" | "m" => {
                            self.last_g_press = None;
                            if let Some(sel) = self.selected_index {
                                self.update(Message::ToggleFavorite(sel))
                            } else {
                                Task::none()
                            }
                        }
                        "p" | "P" => {
                            self.last_g_press = None;
                            self.update(Message::ToggleFavoritesView(!self.show_only_favorites))
                        }
                        "y" => {
                            self.last_g_press = None;
                            self.update(Message::YankPath(false))
                        }
                        "Y" => {
                            self.last_g_press = None;
                            self.update(Message::YankPath(true))
                        }
                        "v" | "V" => {
                            self.last_g_press = None;
                            self.update(Message::ToggleViewLayout)
                        }
                        "s" | "S" => {
                            self.last_g_press = None;
                            self.update(Message::CycleSortMode)
                        }
                        "r" | "R" => {
                            self.last_g_press = None;
                            self.update(Message::ApplyRandom)
                        }
                        "c" | "C" => {
                            self.last_g_press = None;
                            if self.selected_color.is_some() {
                                self.selected_color = None;
                                self.refilter();
                            }
                            Task::none()
                        }
                        "q" | "Q" => {
                            self.last_g_press = None;
                            iced_runtime::exit()
                        }
                        _ => {
                            self.last_g_press = None;
                            Task::none()
                        }
                    },
                    _ => {
                        self.last_g_press = None;
                        Task::none()
                    }
                }
            }
            Message::EventOccurred(_) => Task::none(),
        }
    }

    pub fn view(&self) -> Element<'_, Message> {
        if self.motion_profile.is_enabled() && self.launch_instant.elapsed().as_secs_f32() < 0.015 {
            return container(Space::new())
                .width(Length::Fill)
                .height(Length::Fill)
                .into();
        }

        let accent = self.theme.accent;

        // --- Top Bar: Floating HUD Capsule ---
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
        let search_input = text_input("Dusky Papers  /", &self.search_query)
            .id(iced_core::widget::Id::new("search_input"))
            .on_input(Message::SearchChanged)
            .on_submit(Message::SearchSubmitted)
            .padding([6, 10])
            .size(12)
            .width(Length::Fixed(140.0))
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
                background: Some(Background::Color(if status == button::Status::Hovered {
                    Color::from_rgba8(30, 34, 46, 0.96)
                } else {
                    Color::from_rgba8(20, 22, 30, 0.94)
                })),
                text_color: Color::from_rgb8(240, 243, 255),
                border: Border {
                    radius: 14.0.into(),
                    color: if is_active {
                        Color { a: 0.75, ..accent }
                    } else {
                        Color::from_rgba8(255, 255, 255, 0.08)
                    },
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
            text("⇄ Random")
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

        // Top Bar: Centered mode toggle with refresh & view layout on left, motion & close on right
        let top_bar = container(
            row![
                row![Space::new().width(Length::Fill), refresh_btn, view_btn]
                    .width(Length::Fill)
                    .spacing(8)
                    .align_y(Vertical::Center),
                Space::new().width(Length::Fixed(10.0)),
                mode_pill,
                Space::new().width(Length::Fixed(10.0)),
                row![motion_btn, close_btn, Space::new().width(Length::Fill)]
                    .width(Length::Fill)
                    .spacing(8)
                    .align_y(Vertical::Center),
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
            let btn = button(
                Space::new()
                    .width(Length::Fixed(11.0))
                    .height(Length::Fixed(11.0)),
            )
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
            swatches_row = swatches_row.push(tooltip(
                btn,
                crate::color::swatch_name(bucket),
                iced_widget::tooltip::Position::Bottom,
            ));
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

        // Bottom capsule (Sort & Random on far left, Colors & Search in middle, Counter on right)
        let bottom_capsule = row![sort_btn, random_btn, color_pill, search_input, counter_pill,]
            .spacing(8)
            .align_y(Vertical::Center);

        // Center Content (Carousel or Grid)
        let main_content = if self.filtered_indices.is_empty() {
            let indexing_colors = self.selected_color.is_some()
                && self.prefetch_running
                && self.color_prefetch_cursor < self.all_wallpapers.len();
            container(
                column![
                    text(if self.refreshing {
                        "Loading wallpapers…"
                    } else if indexing_colors {
                        "Indexing wallpaper colors…"
                    } else {
                        "No wallpapers found"
                    })
                    .size(20)
                    .color(Color::from_rgb8(160, 165, 185)),
                    Space::new().height(6),
                    text(if self.refreshing {
                        "Scanning the wallpaper library."
                    } else if indexing_colors {
                        "Matching wallpapers will appear as previews are indexed."
                    } else {
                        "Try clearing search, favorites, or the color filter."
                    })
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
        } else if self.search_focused {
            "Search Mode  •  Type to filter  •  Enter: Focus Grid  •  Esc: Normal Mode (Clear)"
        } else {
            "hjkl / ←↓↑→: Nav  •  Enter/o: Apply  •  Space/O: Fast  •  f: Fav  •  /: Search  •  y/Y: Yank  •  v: View  •  q: Quit"
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

        // --- Layered HUD & Content (Prevents grid cards from intercepting HUD clicks) ---
        match self.view_layout {
            ViewLayout::Carousel => {
                let hud_layer = column![
                    Space::new().height(Length::Fixed(16.0)),
                    top_bar,
                    Space::new().height(Length::Fill),
                    bottom_bar,
                    Space::new().height(Length::Fixed(12.0)),
                ]
                .width(Length::Fill)
                .height(Length::Fill)
                .align_x(Horizontal::Center);

                let carousel_layer = container(main_content)
                    .width(Length::Fill)
                    .height(Length::Fill)
                    .padding(Padding {
                        top: 75.0,
                        right: 0.0,
                        bottom: 95.0,
                        left: 0.0,
                    });

                Stack::new()
                    .push(carousel_layer)
                    .push(hud_layer)
                    .width(Length::Fill)
                    .height(Length::Fill)
                    .into()
            }
            ViewLayout::Grid => {
                let content_column = column![
                    Space::new().height(Length::Fixed(16.0)),
                    top_bar,
                    Space::new().height(Length::Fixed(10.0)),
                    main_content,
                    Space::new().height(Length::Fixed(10.0)),
                    bottom_bar,
                    Space::new().height(Length::Fixed(12.0)),
                ]
                .width(Length::Fill)
                .height(Length::Fill)
                .align_x(Horizontal::Center);

                container(content_column)
                    .width(Length::Fill)
                    .height(Length::Fill)
                    .into()
            }
        }
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
        let entrance = self.entrance_progress();
        let accent = self.theme.accent;
        let total_items = self.filtered_indices.len();
        if total_items == 0 {
            return container(Space::new()).into();
        }

        let chunk_size = self.grid_columns();
        let card_width = self.grid_card_width();
        let row_height = 154.0_f32;
        let total_rows = (total_items + chunk_size - 1) / chunk_size;

        let scroll = self.grid_scroll_offset;
        let start_row = (scroll / row_height).floor().max(0.0) as usize;
        let start_row = start_row.saturating_sub(1);
        let end_row = (start_row + (self.grid_viewport_height / row_height).ceil() as usize + 2)
            .min(total_rows);

        let top_spacer = start_row as f32 * row_height;
        let bottom_spacer = (total_rows - end_row) as f32 * row_height;

        let mut grid_col = column![].spacing(0).align_x(Horizontal::Center);

        if top_spacer > 0.0 {
            grid_col = grid_col.push(Space::new().height(Length::Fixed(top_spacer)));
        }

        for r in start_row..end_row {
            let start_idx = r * chunk_size;
            let end_idx = (start_idx + chunk_size).min(total_items);
            let mut row_cards = row![].spacing(14).align_y(Vertical::Center);

            for filtered_idx in start_idx..end_idx {
                let item_idx = self.filtered_indices[filtered_idx];
                let item = &self.all_wallpapers[item_idx];
                let is_selected = self.selected_index == Some(filtered_idx);

                let card_radius = 10.0;
                let img_layer: Element<'_, Message> =
                    if self.ready_thumbs.contains(&item.thumb_path) {
                        image(self.thumbnail_handle(item))
                            .width(Length::Fill)
                            .height(Length::Fill)
                            .content_fit(ContentFit::Cover)
                            .border_radius(card_radius)
                            .opacity(entrance)
                            .into()
                    } else {
                        container(Space::new())
                            .width(Length::Fill)
                            .height(Length::Fill)
                            .style(move |_| container::Style {
                                background: Some(Background::Color(Color::from_rgba8(
                                    18, 20, 28, entrance,
                                ))),
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
                            background: Some(Background::Color(Color::from_rgba8(
                                16, 185, 129, 0.25,
                            ))),
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
                    badge_row =
                        badge_row.push(text("♥").size(12).color(Color::from_rgb8(243, 139, 168)));
                }

                let bottom_badge =
                    container(badge_row)
                        .padding([4, 8])
                        .style(|_| container::Style {
                            background: Some(Background::Color(Color::from_rgba8(
                                10, 12, 18, 0.75,
                            ))),
                            border: Border {
                                radius: 8.0.into(),
                                ..Border::default()
                            },
                            ..container::Style::default()
                        });

                let overlay_col =
                    column![Space::new().height(Length::Fill), bottom_badge,].padding(6);

                let card_stack = Stack::new().push(img_layer).push(overlay_col).clip(true);

                let card_container = container(card_stack)
                    .width(Length::Fixed(card_width))
                    .height(Length::Fixed(140.0))
                    .style(move |_| container::Style {
                        background: Some(Background::Color(Color::from_rgba8(
                            15, 17, 24, entrance,
                        ))),
                        border: Border {
                            radius: card_radius.into(),
                            color: if is_selected {
                                Color {
                                    a: entrance,
                                    ..accent
                                }
                            } else {
                                Color::from_rgba8(255, 255, 255, 0.08 * entrance)
                            },
                            width: if is_selected { 2.0 } else { 1.0 },
                        },
                        shadow: Shadow {
                            color: if is_selected {
                                Color {
                                    a: 0.35 * entrance,
                                    ..accent
                                }
                            } else {
                                Color::TRANSPARENT
                            },
                            offset: iced_core::Vector::ZERO,
                            blur_radius: 12.0 * entrance,
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

                let card_area = mouse_area(card_btn)
                    .on_right_press(Message::ApplyWallpaper(filtered_idx, false))
                    .on_middle_press(Message::ToggleFavorite(filtered_idx));

                row_cards = row_cards.push(card_area);
            }

            let row_width = chunk_size as f32 * card_width + (chunk_size - 1) as f32 * 14.0;
            let row_container = container(row_cards)
                .width(Length::Fixed(row_width))
                .height(Length::Fixed(row_height));
            grid_col = grid_col.push(row_container);
        }

        if bottom_spacer > 0.0 {
            grid_col = grid_col.push(Space::new().height(Length::Fixed(bottom_spacer)));
        }

        let centered_grid = container(grid_col)
            .width(Length::Fill)
            .align_x(Horizontal::Center);

        scrollable(centered_grid)
            .id(iced_core::widget::Id::new("grid_scroll"))
            .on_scroll(|vp| Message::GridScroll(vp.absolute_offset().y, vp.bounds().height))
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

    /// Center expanded card
    fn build_center_card<'a>(
        &'a self,
        item: &'a WallpaperItem,
        filtered_idx: usize,
        width: f32,
        emphasis: f32,
        distance: f32,
    ) -> Element<'a, Message> {
        let entrance = self.entrance_progress();
        let accent = self.theme.accent;
        let card_radius = 12.0 + 2.0 * emphasis;
        // Recede gently into the flanks using layout geometry, so drawing and
        // pointer bounds stay aligned without a separate transform or animation.
        let depth = (distance * 0.5).min(1.0);
        let card_height = CARD_HEIGHT * (1.0 - 0.10 * depth);

        // Native carousel falloff: 1.0, 1.0, 0.95, 0.40, 0.10, then to transparent.
        let edge_fade = (if distance <= 1.0 {
            1.0
        } else if distance <= 2.0 {
            1.0 - (distance - 1.0) * 0.05
        } else if distance <= 3.0 {
            0.95 - (distance - 2.0) * 0.55
        } else if distance <= 4.0 {
            0.40 - (distance - 3.0) * 0.30
        } else if distance <= 5.0 {
            0.10 * (1.0 - (distance - 4.0))
        } else {
            0.0
        }) * entrance;

        // Image layer (reads thumbnail from disk; NEVER generates synchronously)
        let img_layer: Element<'_, Message> = if self.ready_thumbs.contains(&item.thumb_path) {
            image(self.thumbnail_handle(item))
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
            .height(Length::Fixed(card_height))
            .style(move |_| container::Style {
                background: Some(Background::Color(if emphasis > 0.5 {
                    Color::from_rgba8(15, 17, 24, entrance)
                } else {
                    Color::from_rgba8(14, 16, 22, 0.85 * edge_fade)
                })),
                border: Border {
                    radius: card_radius.into(),
                    color: border_color,
                    width: border_width,
                },
                shadow: Shadow {
                    // Keep the focused accent glow; blend into a downward
                    // neutral shadow as the same card recedes into the flanks.
                    color: Color {
                        r: accent.r * emphasis,
                        g: accent.g * emphasis,
                        b: accent.b * emphasis,
                        a: 0.30 * edge_fade,
                    },
                    offset: Vector::new(0.0, 5.0 * (1.0 - emphasis)),
                    blur_radius: 12.0 + 4.0 * emphasis,
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
                                offset: iced_core::Vector::ZERO,
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

    fn entrance_progress(&self) -> f32 {
        if !self.motion_profile.is_enabled() {
            return 1.0;
        }
        let elapsed = self.launch_instant.elapsed().as_secs_f32();
        if elapsed < 0.015 {
            0.0
        } else {
            ((elapsed - 0.015) / 0.065).clamp(0.0, 1.0)
        }
    }

    pub fn subscription(&self) -> Subscription<Message> {
        let events = iced_futures::event::listen_with(|event, status, _| match (status, &event) {
            (
                iced_core::event::Status::Captured,
                Event::Keyboard(iced_core::keyboard::Event::KeyPressed {
                    key: Key::Named(Named::Escape),
                    ..
                }),
            ) => Some(Message::SearchEscape),
            (_, Event::Mouse(iced_core::mouse::Event::ButtonPressed(_))) => {
                Some(Message::CheckSearchFocus)
            }
            (
                _,
                Event::Keyboard(iced_core::keyboard::Event::KeyPressed {
                    key: Key::Named(Named::Tab),
                    ..
                }),
            ) => Some(Message::SearchTab),
            (
                iced_core::event::Status::Captured,
                Event::Keyboard(iced_core::keyboard::Event::KeyPressed {
                    key: Key::Named(Named::ArrowUp | Named::ArrowDown),
                    ..
                }),
            ) => Some(Message::EventOccurred(event)),
            (iced_core::event::Status::Ignored, _) => Some(Message::EventOccurred(event)),
            _ => None,
        });
        let events = Subscription::batch([
            events,
            iced_runtime::window::open_events().map(Message::WindowOpened),
            iced_runtime::window::resize_events().map(|(_, size)| Message::WindowResized(size)),
        ]);
        let animating = self.animation.is_some()
            || (self.motion_profile.is_enabled()
                && self.launch_instant.elapsed().as_secs_f32() < 0.08);
        if animating {
            Subscription::batch([
                events,
                iced_runtime::window::frames().map(Message::AnimationFrame),
            ])
        } else {
            events
        }
    }
}
