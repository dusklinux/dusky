#[path = "../../../rust_shared/ui_fonts.rs"]
mod fonts;

mod apply;
mod cache;
mod color;
mod config;
mod favorites;
mod renderer;
mod scanner;
mod theme;
mod ui;

use config::Config;
use std::env;
use ui::DuskyPapersApp;

// Bound background task threads instead of creating one for every CPU.
struct BackgroundExecutor(iced_futures::futures::executor::ThreadPool);

impl iced_futures::Executor for BackgroundExecutor {
    fn new() -> Result<Self, iced_futures::futures::io::Error> {
        iced_futures::futures::executor::ThreadPool::builder()
            .pool_size(2)
            .name_prefix("wallpaper-worker")
            .create()
            .map(Self)
    }

    fn spawn(&self, future: impl std::future::Future<Output = ()> + Send + 'static) {
        self.0.spawn_ok(future);
    }

    fn block_on<T>(&self, future: impl std::future::Future<Output = T>) -> T {
        iced_futures::futures::executor::block_on(future)
    }
}

fn print_help() {
    println!("Dusky Papers");
    println!("Usage: dusky-papers [OPTIONS]\n");
    println!("Options:");
    println!("  --next-fav       Cycle to next favorite wallpaper and exit");
    println!("  --prev-fav       Cycle to previous favorite wallpaper and exit");
    println!("  --random         Select and apply a random wallpaper and exit");
    println!("  --build-cache    Generate only missing or outdated thumbnails and exit");
    println!("  --update-cache   Alias for --build-cache");
    println!("  --rebuild-cache  Force-regenerate every thumbnail and exit");
    println!("  --version, -v, -V Show version information and exit");
    println!("  --help, -h       Show this help message");
}

fn cycle_favorite(direction_next: bool, config: &Config) -> bool {
    let favorites_set = match favorites::load_favorites(&config.fav_file) {
        Ok(favorites) => favorites,
        Err(error) => {
            eprintln!("Could not read favorites: {error}");
            return false;
        }
    };
    let active_id = favorites::read_active_wallpaper(&config.theme_dir);

    let all = match scanner::scan_wallpapers(
        &config.wallpaper_dir,
        &config.thumb_dir,
        &favorites_set,
        active_id.as_deref(),
    ) {
        Ok(items) => items,
        Err(error) => {
            eprintln!("{error}");
            return false;
        }
    };

    let fav_items: Vec<_> = all.into_iter().filter(|w| w.is_favorite).collect();

    if fav_items.is_empty() {
        println!("No favorites found in {}", config.fav_file.display());
        let _ = std::process::Command::new("notify-send")
            .args([
                "-a",
                "dusky-papers",
                "No Favorites",
                "No favorite wallpapers found.",
            ])
            .spawn();
        return false;
    }

    let current_index = fav_items.iter().position(|item| item.is_active);

    let next_index = if direction_next {
        current_index.map_or(0, |index| (index + 1) % fav_items.len())
    } else {
        current_index.map_or(fav_items.len() - 1, |index| {
            (index + fav_items.len() - 1) % fav_items.len()
        })
    };

    let target = &fav_items[next_index];
    apply::apply_wallpaper(&target.path, &config.theme_ctl, true)
        .map_err(|e| eprintln!("Failed to apply wallpaper: {e}"))
        .is_ok()
}

fn apply_random(config: &Config) -> bool {
    let favorites_set = match favorites::load_favorites(&config.fav_file) {
        Ok(favorites) => favorites,
        Err(error) => {
            eprintln!("Could not read favorites: {error}");
            return false;
        }
    };
    let all = match scanner::scan_wallpapers(
        &config.wallpaper_dir,
        &config.thumb_dir,
        &favorites_set,
        None,
    ) {
        Ok(items) => items,
        Err(error) => {
            eprintln!("{error}");
            return false;
        }
    };

    if all.is_empty() {
        eprintln!("No wallpapers found in {}", config.wallpaper_dir.display());
        return false;
    }

    let choice = &all[fastrand::usize(..all.len())];

    apply::apply_wallpaper(&choice.path, &config.theme_ctl, true)
        .map_err(|e| eprintln!("Failed to apply wallpaper: {e}"))
        .is_ok()
}

fn build_cache(config: &Config, force: bool) -> bool {
    println!("Scanning {}...", config.wallpaper_dir.display());
    let favs = match favorites::load_favorites(&config.fav_file) {
        Ok(favorites) => favorites,
        Err(error) => {
            eprintln!("Could not read favorites: {error}");
            return false;
        }
    };
    let all = match scanner::scan_wallpapers(&config.wallpaper_dir, &config.thumb_dir, &favs, None)
    {
        Ok(items) => items,
        Err(error) => {
            eprintln!("{error}");
            return false;
        }
    };
    println!("Checking {} wallpapers...", all.len());
    let stats = cache::batch_generate_thumbs(&all, force);
    println!(
        "Cache result: generated={}, cached={}, failed={}",
        stats.generated, stats.cached, stats.failed
    );
    if stats.failed > 0 {
        eprintln!(
            "Failed to generate {} of {} thumbnails",
            stats.failed,
            all.len()
        );
        false
    } else {
        match cache::prune_thumbnails(&all, &config.thumb_dir) {
            Ok(removed) if removed > 0 => println!("Removed {removed} obsolete thumbnails"),
            Err(error) => {
                eprintln!("Could not prune obsolete thumbnails: {error}");
                return false;
            }
            _ => {}
        }
        println!("Indexing wallpaper colors...");
        let (colors, _, saved) = color::ensure_color_cache(&all, &config.colors_file);
        if let Err(error) = saved {
            eprintln!("Could not save wallpaper color index: {error}");
            return false;
        }
        if colors.len() != all.len() {
            eprintln!(
                "Could not index colors for {} wallpapers",
                all.len() - colors.len()
            );
            return false;
        }
        println!("Color index ready: {} wallpapers indexed", colors.len());
        println!("Cache generation complete!");
        true
    }
}

struct SingleInstanceGuard {
    _lock: std::fs::File,
}

impl SingleInstanceGuard {
    pub fn acquire() -> Result<Option<Self>, String> {
        let runtime_dir = std::env::var_os("XDG_RUNTIME_DIR")
            .map(std::path::PathBuf::from)
            .ok_or("XDG_RUNTIME_DIR is not set")?;
        let path = runtime_dir.join("dusky-papers.lock");
        let file = std::fs::File::create(&path)
            .map_err(|e| format!("Could not open {}: {e}", path.display()))?;
        match file.try_lock() {
            Ok(()) => Ok(Some(Self { _lock: file })),
            Err(std::fs::TryLockError::WouldBlock) => Ok(None),
            Err(error) => Err(format!("Could not lock {}: {error}", path.display())),
        }
    }
}

fn read_card_vendor_driver(card_name: &str) -> Option<(String, String, String)> {
    let sys_base = format!("/sys/class/drm/{card_name}/device");
    let vendor_path = format!("{sys_base}/vendor");
    let vendor = std::fs::read_to_string(&vendor_path)
        .ok()?
        .trim()
        .to_ascii_lowercase();

    let driver = std::fs::read_link(format!("{sys_base}/driver"))
        .ok()
        .and_then(|p| p.file_name().map(|f| f.to_string_lossy().to_string()))
        .unwrap_or_default()
        .to_ascii_lowercase();

    let pci_device = std::fs::canonicalize(&sys_base)
        .ok()?
        .file_name()?
        .to_string_lossy()
        .into_owned();

    Some((vendor, driver, pci_device))
}

fn detect_primary_gpu_vendor() -> Option<(String, String, String)> {
    // 1. Check AQ_DRM_DEVICES (set by Hyprland via gpu.lua, ordered with primary card first)
    if let Ok(aq_devices) = env::var("AQ_DRM_DEVICES") {
        if let Some(first) = aq_devices.split(':').next() {
            let p = std::path::Path::new(first.trim());
            if let Ok(real) = std::fs::canonicalize(p) {
                if let Some(name) = real.file_name().and_then(|s| s.to_str()) {
                    if name.starts_with("card") {
                        if let Some(pair) = read_card_vendor_driver(name) {
                            return Some(pair);
                        }
                    }
                }
            }
        }
    }

    // boot_vga is a firmware hint, not necessarily Hyprland's render device.
    // Without AQ_DRM_DEVICES, prefer it, then the first identifiable DRM card.
    let mut first_gpu = None;
    if let Ok(entries) = std::fs::read_dir("/sys/class/drm") {
        let mut card_names: Vec<String> = entries
            .filter_map(|e| e.ok())
            .map(|e| e.file_name().to_string_lossy().to_string())
            .filter(|name| {
                name.strip_prefix("card").is_some_and(|index| {
                    !index.is_empty() && index.bytes().all(|b| b.is_ascii_digit())
                })
            })
            .collect();
        card_names.sort_by_key(|name| name[4..].parse::<u32>().unwrap_or(u32::MAX));

        for name in &card_names {
            let Some(gpu) = read_card_vendor_driver(name) else {
                continue;
            };
            let boot_vga_path = format!("/sys/class/drm/{name}/device/boot_vga");
            if let Ok(content) = std::fs::read_to_string(&boot_vga_path) {
                if content.trim() == "1" {
                    return Some(gpu);
                }
            }
            if first_gpu.is_none() {
                first_gpu = Some(gpu);
            }
        }
    }

    first_gpu
}

fn optimize_gpu_environment() {
    // With no backend override, our compositor tries Vulkan, then EGL only
    // if Vulkan initialization fails. Pin drivers before starting any threads.
    let backend_overridden = env::var_os("WGPU_BACKEND").is_some();
    if env::var_os("WGPU_POWER_PREF").is_none() {
        unsafe { env::set_var("WGPU_POWER_PREF", "low") };
    }
    // Respect explicit GPU/offload selection, including the legacy Vulkan API.
    if [
        "VK_DRIVER_FILES",
        "VK_ICD_FILENAMES",
        "VK_ADD_DRIVER_FILES",
        "VK_LOADER_DRIVERS_SELECT",
        "VK_LOADER_DRIVERS_DISABLE",
        "VK_LOADER_DEVICE_SELECT",
        "DRI_PRIME",
        "MESA_VK_DEVICE_SELECT",
        "MESA_LOADER_DRIVER_OVERRIDE",
        "__NV_PRIME_RENDER_OFFLOAD",
        "__NV_PRIME_RENDER_OFFLOAD_PROVIDER",
        "__EGL_VENDOR_LIBRARY_FILENAMES",
        "__EGL_VENDOR_LIBRARY_DIRS",
    ]
    .iter()
    .any(|name| env::var_os(name).is_some())
    {
        return;
    }

    let Some((vendor, driver, pci_device)) = detect_primary_gpu_vendor() else {
        return;
    };

    // 2. Determine matching Vulkan ICD candidates based on the actual primary GPU
    let candidates: &[&str] = match vendor.as_str() {
        // Intel (Iris Xe, UHD, Arc)
        "0x8086" => &[
            "/usr/share/vulkan/icd.d/intel_icd.x86_64.json",
            "/usr/share/vulkan/icd.d/intel_icd.json",
            "/usr/share/vulkan/icd.d/intel_hasvk_icd.x86_64.json",
            "/usr/share/vulkan/icd.d/intel_hasvk_icd.json",
        ],
        // AMD (Radeon, Ryzen iGPU, Radeon dGPU)
        "0x1002" => &[
            "/usr/share/vulkan/icd.d/radeon_icd.x86_64.json",
            "/usr/share/vulkan/icd.d/radeon_icd.json",
        ],
        // NVIDIA (Desktop discrete GPU or single-GPU system)
        "0x10de" => {
            if driver == "nouveau" {
                &[
                    "/usr/share/vulkan/icd.d/nouveau_icd.x86_64.json",
                    "/usr/share/vulkan/icd.d/nouveau_icd.json",
                ]
            } else {
                &[
                    "/usr/share/vulkan/icd.d/nvidia_icd.x86_64.json",
                    "/usr/share/vulkan/icd.d/nvidia_icd.json",
                ]
            }
        }
        // VirtIO's Venus Vulkan driver is optional; VirGL-only guests use EGL.
        "0x1af4" => &[
            "/usr/share/vulkan/icd.d/virtio_icd.x86_64.json",
            "/usr/share/vulkan/icd.d/virtio_icd.json",
        ],
        // Other unknown/virtual drivers use Vulkan-first compositor fallback.
        _ => return,
    };

    // Mesa selects this PCI GPU for EGL and puts it first for Vulkan.
    // This does not prevent enumeration/initialization of same-driver GPUs.
    // Leave unknown/non-PCI devices to their driver defaults.
    if matches!(vendor.as_str(), "0x8086" | "0x1002" | "0x1af4") {
        if matches!(vendor.as_str(), "0x8086" | "0x1002")
            && pci_device.split([':', '.']).count() == 4
            && pci_device
                .chars()
                .all(|c| c.is_ascii_hexdigit() || c == ':' || c == '.')
        {
            let prime = format!("pci-{}", pci_device.replace([':', '.'], "_"));
            unsafe { env::set_var("DRI_PRIME", prime) };
        }
        let mesa_egl = "/usr/share/glvnd/egl_vendor.d/50_mesa.json";
        if env::var_os("__EGL_VENDOR_LIBRARY_FILENAMES").is_none()
            && std::path::Path::new(mesa_egl).is_file()
        {
            unsafe { env::set_var("__EGL_VENDOR_LIBRARY_FILENAMES", mesa_egl) };
        }
    }

    // Include all installed matching ICDs: ANV and HASVK cover different Intel
    // generations. Driver filtering is not physical-device filtering.
    // If the matching driver is missing,
    // use EGL rather than letting the loader probe unrelated discrete drivers.
    let drivers: Vec<_> = candidates
        .iter()
        .copied()
        .filter(|candidate| std::path::Path::new(candidate).is_file())
        .collect();
    if !drivers.is_empty() {
        unsafe { env::set_var("VK_DRIVER_FILES", drivers.join(":")) };
    } else if !backend_overridden {
        unsafe { env::set_var("WGPU_BACKEND", "gl") };
    }
}

fn main() -> iced_exwlshell::Result {
    let config = Config::load();
    let args: Vec<String> = env::args().collect();

    if args.len() > 2 {
        eprintln!("Expected at most one option; use --help for usage");
        std::process::exit(2);
    }
    let option = args.get(1).map(String::as_str);

    if matches!(option, Some("--help" | "-h")) {
        print_help();
        return Ok(());
    }

    if matches!(option, Some("--version" | "-v" | "-V")) {
        println!("dusky-papers {}", env!("CARGO_PKG_VERSION"));
        return Ok(());
    }

    if option == Some("--next-fav") {
        if !cycle_favorite(true, &config) {
            std::process::exit(1);
        }
        return Ok(());
    }

    if option == Some("--prev-fav") {
        if !cycle_favorite(false, &config) {
            std::process::exit(1);
        }
        return Ok(());
    }

    if option == Some("--random") {
        if !apply_random(&config) {
            std::process::exit(1);
        }
        return Ok(());
    }

    if matches!(
        option,
        Some("--build-cache" | "--update-cache" | "--rebuild-cache")
    ) {
        if !build_cache(&config, option == Some("--rebuild-cache")) {
            std::process::exit(1);
        }
        return Ok(());
    }
    if let Some(unknown) = option {
        eprintln!("Unknown option: {unknown}; use --help for usage");
        std::process::exit(2);
    }

    // Single-instance guard prevents duplicate instances and CPU thrashing
    let _guard = match SingleInstanceGuard::acquire() {
        Ok(Some(g)) => g,
        // The existing overlay already covers its monitor, across workspaces.
        Ok(None) => return Ok(()),
        Err(error) => {
            eprintln!("Could not start Dusky Papers: {error}");
            std::process::exit(1);
        }
    };

    optimize_gpu_environment();
    fonts::initialize();
    // Cover the active output without entering native fullscreen or hiding its windows.
    let settings = iced_exwlshell::Settings {
        default_font: iced_core::Font::with_name(fonts::DEFAULT_FAMILY),
        layer_settings: iced_exwlshell::settings::LayerShellSettings {
            layer: iced_exwlshell::reexport::Layer::Overlay,
            blur_option: iced_exwlshell::reexport::BlurOption::FullRegion,
            keyboard_interactivity: iced_exwlshell::reexport::KeyboardInteractivity::Exclusive,
            ..Default::default()
        },
        keep_compositor_alive: false,
        ..Default::default()
    };

    let app_config = config.clone();
    iced_exwlshell::layershell::application(
        move || DuskyPapersApp::new(app_config.clone()),
        "dusky-papers",
        DuskyPapersApp::update,
        DuskyPapersApp::view,
    )
    .executor::<BackgroundExecutor>()
    .settings(settings)
    .subscription(DuskyPapersApp::subscription)
    .theme(theme)
    .style(style)
    .run()
}

fn style(_: &DuskyPapersApp, theme: &iced_core::Theme) -> iced_core::theme::Style {
    iced_core::theme::Style {
        background_color: iced_core::Color::from_rgba(0.06, 0.07, 0.09, 0.15),
        text_color: theme.palette().text,
    }
}

fn theme(_: &DuskyPapersApp) -> iced_core::Theme {
    iced_core::Theme::Dark
}

// UI messages are ordinary Iced messages, with no layer reconfiguration actions.
impl TryInto<iced_exwlshell::actions::ExwlShellCustomActionWithId> for ui::Message {
    type Error = Self;

    fn try_into(self) -> Result<iced_exwlshell::actions::ExwlShellCustomActionWithId, Self> {
        Err(self)
    }
}
