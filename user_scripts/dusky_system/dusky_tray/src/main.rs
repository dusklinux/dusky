//! dusky-tray: Rust-native control center (Iced + Wayland window).
//!
//! Layout parity with the original compact panel. Build + runtime caches stay
//! on tmpfs (see `scripts/build-on-tmpfs.sh` and `backend::system::runtime_dir`)
//! to avoid SSD write amplification.

mod appearance;
mod backend;
mod click_away;
mod config;
mod icons;
mod renderer;
mod theme;
mod ui;

use config::AppConfig;
use std::env;
use ui::TrayApp;

// Bounded background threads (GTK used 4 refresh workers; wallpaper uses 2).
struct BackgroundExecutor(iced_futures::futures::executor::ThreadPool);

impl iced_futures::Executor for BackgroundExecutor {
    fn new() -> Result<Self, iced_futures::futures::io::Error> {
        iced_futures::futures::executor::ThreadPool::builder()
            .pool_size(4)
            .name_prefix("tray-worker")
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
    println!("Dusky Tray (Rust)");
    println!("Usage: dusky-tray [OPTIONS]\n");
    println!("Options:");
    println!("  --version, -v   Show version and exit");
    println!("  --help, -h      Show this help");
    println!("\nBehavior:");
    println!("  Borderless Wayland window with a bottom-right panel.");
    println!("  Esc closes the panel. All runtime caches live on tmpfs");
    println!("  ($XDG_RUNTIME_DIR/dusky-tray, /dev/shm, or /tmp).");
    println!("  Build with scripts/build-on-tmpfs.sh to keep intermediates on RAM.");
}

struct SingleInstanceGuard {
    _lock: std::fs::File,
}

impl SingleInstanceGuard {
    pub fn acquire() -> Result<Option<Self>, String> {
        let path = backend::runtime_dir().join("instance.lock");
        let file = std::fs::OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(false)
            .open(&path)
            .map_err(|e| format!("Could not open {}: {e}", path.display()))?;
        match file.try_lock() {
            Ok(()) => Ok(Some(Self { _lock: file })),
            Err(std::fs::TryLockError::WouldBlock) => Ok(None),
            Err(e) => Err(format!("Could not lock {}: {e}", path.display())),
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
    if let Ok(aq_devices) = env::var("AQ_DRM_DEVICES")
        && let Some(first) = aq_devices.split(':').next()
    {
        let p = std::path::Path::new(first.trim());
        if let Ok(real) = std::fs::canonicalize(p)
            && let Some(name) = real.file_name().and_then(|s| s.to_str())
            && name.starts_with("card")
            && let Some(pair) = read_card_vendor_driver(name)
        {
            return Some(pair);
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
            if let Ok(content) = std::fs::read_to_string(&boot_vga_path)
                && content.trim() == "1"
            {
                return Some(gpu);
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

fn main() -> Result<(), iced_winit::Error> {
    renderer::trace_startup();
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
        println!("dusky-tray {}", env!("CARGO_PKG_VERSION"));
        return Ok(());
    }
    if let Some(unknown) = option {
        eprintln!("Unknown option: {unknown}; use --help for usage");
        std::process::exit(2);
    }

    let _guard = match SingleInstanceGuard::acquire() {
        Ok(Some(g)) => g,
        Ok(None) => return Ok(()),
        Err(error) => {
            eprintln!("Could not start Dusky Tray: {error}");
            std::process::exit(1);
        }
    };

    optimize_gpu_environment();
    let app_config = AppConfig::load();

    let Some(monitor) = backend::system::panel_monitor() else {
        eprintln!("Could not determine the focused Hyprland monitor");
        std::process::exit(1);
    };
    iced_winit::run(TrayProgram(app_config, monitor))
}

struct TrayProgram(AppConfig, backend::system::PanelMonitor);

impl iced_winit::program::Program for TrayProgram {
    type State = TrayApp;
    type Message = ui::Message;
    type Theme = iced_core::Theme;
    type Renderer = renderer::Renderer;
    type Executor = BackgroundExecutor;

    fn name() -> &'static str {
        "dusky-tray"
    }

    fn settings(&self) -> iced_core::Settings {
        iced_core::Settings {
            default_font: iced_core::Font::with_name("Atkinson Hyperlegible"),
            ..Default::default()
        }
    }

    fn window(&self) -> Option<iced_core::window::Settings> {
        Some(iced_core::window::Settings {
            size: self.1.limit(),
            transparent: true,
            decorations: false,
            platform_specific: iced_core::window::settings::PlatformSpecific {
                application_id: "dusky-tray".into(),
                ..Default::default()
            },
            ..Default::default()
        })
    }

    fn boot(&self) -> (TrayApp, iced_runtime::Task<ui::Message>) {
        let (mut app, task) = TrayApp::new(self.0.clone());
        app.panel_monitor = Some(self.1);
        (app, task)
    }

    fn update(&self, state: &mut TrayApp, message: ui::Message) -> iced_runtime::Task<ui::Message> {
        state.update(message)
    }

    fn view<'a>(
        &self,
        state: &'a TrayApp,
        _: iced_core::window::Id,
    ) -> iced_core::Element<'a, ui::Message, Self::Theme, Self::Renderer> {
        state.view()
    }

    fn title(&self, _: &TrayApp, _: iced_core::window::Id) -> String {
        "Dusky Tray".into()
    }

    fn subscription(&self, state: &TrayApp) -> iced_futures::Subscription<ui::Message> {
        state.subscription()
    }

    fn theme(&self, _: &TrayApp, _: iced_core::window::Id) -> Option<Self::Theme> {
        Some(iced_core::Theme::Dark)
    }

    fn style(&self, _: &TrayApp, theme: &Self::Theme) -> iced_core::theme::Style {
        iced_core::theme::Style {
            background_color: iced_core::Color::TRANSPARENT,
            text_color: theme.palette().text,
        }
    }
}
