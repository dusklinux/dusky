#[path = "../../../rust_shared/ui_fonts.rs"]
mod fonts;

mod hyprland;
mod ui;

use iced::Color;
use iced_exwlshell::reexport::{Anchor, KeyboardInteractivity, Layer};
use iced_exwlshell::settings::LayerShellSettings;
use std::env;
use std::io::Write;
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::PathBuf;
use std::time::Duration;
use ui::OverviewApp;

impl TryInto<iced_exwlshell::actions::ExwlShellCustomActionWithId> for ui::Message {
    type Error = Self;

    fn try_into(self) -> Result<iced_exwlshell::actions::ExwlShellCustomActionWithId, Self> {
        Err(self)
    }
}

fn instance_socket_path() -> PathBuf {
    if let Ok(runtime_dir) = env::var("XDG_RUNTIME_DIR") {
        PathBuf::from(runtime_dir).join("dusky_overview.sock")
    } else {
        PathBuf::from("/tmp/dusky_overview.sock")
    }
}

fn print_help() {
    println!("dusky_overview {}", env!("CARGO_PKG_VERSION"));
    println!("Usage: dusky_overview [OPTIONS]\n");
    println!("Options:");
    println!("  --version, -v, -V Show version information and exit");
    println!("  --help, -h       Show this help message and exit");
    println!("\nBehavior:");
    println!("  One-shot, zero-resident-RAM Wayland workspace overview for Hyprland 0.57+.");
    println!("  Displays active workspaces and proportional window layout miniatures.");
    println!("  Click or press Enter on a window/workspace to switch focus and exit.");
    println!("  Press Escape or click outside to dismiss and exit immediately.");
}

fn is_card_sleeping(sys_base: &str) -> bool {
    if let Ok(state) = std::fs::read_to_string(format!("{sys_base}/power_state")) {
        let s = state.trim();
        if s.eq_ignore_ascii_case("d3cold") || s.eq_ignore_ascii_case("d3hot") {
            return true;
        }
    }
    if let Ok(status) = std::fs::read_to_string(format!("{sys_base}/power/runtime_status"))
        && status.trim().eq_ignore_ascii_case("suspended")
    {
        return true;
    }
    false
}

fn read_card_vendor_driver(card_name: &str) -> Option<(String, String, String, bool)> {
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

    let sleeping = is_card_sleeping(&sys_base);

    Some((vendor, driver, pci_device, sleeping))
}

fn detect_primary_gpu_vendor() -> Option<(String, String, String)> {
    // 1. Check AQ_DRM_DEVICES (set by Hyprland via gpu.lua, ordered with primary card first)
    if let Ok(aq_devices) = env::var("AQ_DRM_DEVICES") {
        let mut fallback_aq = None;
        for entry in aq_devices.split(':') {
            let p = std::path::Path::new(entry.trim());
            if let Ok(real) = std::fs::canonicalize(p)
                && let Some(name) = real.file_name().and_then(|s| s.to_str())
                && name.starts_with("card")
                && let Some((vendor, driver, pci, sleeping)) = read_card_vendor_driver(name)
            {
                if !sleeping {
                    return Some((vendor, driver, pci));
                }
                if fallback_aq.is_none() {
                    fallback_aq = Some((vendor, driver, pci));
                }
            }
        }
        if fallback_aq.is_some() {
            return fallback_aq;
        }
    }

    let mut boot_awake = None;
    let mut first_igpu = None;
    let mut first_awake_gpu = None;
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
            let Some((vendor, driver, pci, sleeping)) = read_card_vendor_driver(name) else {
                continue;
            };
            let gpu = (vendor, driver, pci);

            let boot_vga_path = format!("/sys/class/drm/{name}/device/boot_vga");
            let is_boot_vga = std::fs::read_to_string(&boot_vga_path)
                .is_ok_and(|content| content.trim() == "1");

            if is_boot_vga && !sleeping && boot_awake.is_none() {
                boot_awake = Some(gpu.clone());
            }
            if first_igpu.is_none() && matches!(gpu.0.as_str(), "0x8086" | "0x1002" | "0x1af4") {
                first_igpu = Some(gpu.clone());
            }
            if !sleeping && first_awake_gpu.is_none() {
                first_awake_gpu = Some(gpu.clone());
            }
            if first_gpu.is_none() {
                first_gpu = Some(gpu);
            }
        }
    }

    // Prefer an awake iGPU over waking a suspended NVIDIA dGPU from D3cold.
    first_igpu.or(boot_awake).or(first_awake_gpu).or(first_gpu)
}

fn optimize_gpu_environment() {
    let backend_overridden = env::var_os("WGPU_BACKEND").is_some();
    if env::var_os("WGPU_POWER_PREF").is_none() {
        unsafe { env::set_var("WGPU_POWER_PREF", "low") };
    }
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
        // VirtIO Venus
        "0x1af4" => &[
            "/usr/share/vulkan/icd.d/virtio_icd.x86_64.json",
            "/usr/share/vulkan/icd.d/virtio_icd.json",
        ],
        _ => return,
    };

    if matches!(vendor.as_str(), "0x8086" | "0x1002" | "0x1af4") {
        // Strictly block the Vulkan and GLVND loaders from probing sleeping NVIDIA dGPUs on hybrid laptops.
        unsafe {
            env::set_var("VK_LOADER_DRIVERS_DISABLE", "*nvidia*");
            env::set_var("__NV_PRIME_RENDER_OFFLOAD", "0");
        }

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

    if let Some(&primary_icd) = candidates
        .iter()
        .find(|&&candidate| std::path::Path::new(candidate).is_file())
    {
        unsafe {
            env::set_var("VK_DRIVER_FILES", primary_icd);
            env::set_var("VK_ICD_FILENAMES", primary_icd);
        }
    } else if !backend_overridden {
        unsafe { env::set_var("WGPU_BACKEND", "gl") };
    }
}

fn main() -> Result<(), iced_exwlshell::Error> {
    let args: Vec<String> = env::args().collect();
    if args.len() > 1 {
        let opt = args[1].as_str();
        if matches!(opt, "--help" | "-h") {
            print_help();
            return Ok(());
        }
        if matches!(opt, "--version" | "-v" | "-V") {
            println!("dusky_overview {}", env!("CARGO_PKG_VERSION"));
            return Ok(());
        }
    }

    // Single-instance zero-latency toggle: if already running, signal it to dismiss and exit immediately.
    let sock_path = instance_socket_path();
    if let Ok(mut stream) = UnixStream::connect(&sock_path) {
        let _ = stream.set_write_timeout(Some(Duration::from_millis(50)));
        let _ = stream.write_all(b"dismiss\n");
        std::process::exit(0);
    }
    let _ = std::fs::remove_file(&sock_path);
    if let Ok(listener) = UnixListener::bind(&sock_path) {
        ui::spawn_ipc_watchers(listener);
    }

    let snapshot = match hyprland::fetch_snapshot() {
        Ok(snapshot) => snapshot,
        Err(err) => {
            let _ = std::fs::remove_file(&sock_path);
            eprintln!("dusky_overview: failed to query Hyprland 0.57 state: {err}");
            std::process::exit(1);
        }
    };

    if snapshot.workspaces.is_empty() {
        let _ = std::fs::remove_file(&sock_path);
        std::process::exit(0);
    }

    optimize_gpu_environment();
    fonts::initialize();

    let palette = ui::ThemePalette::load();

    let settings = iced_exwlshell::Settings {
        default_font: iced_core::Font::with_name(fonts::DEFAULT_FAMILY),
        layer_settings: LayerShellSettings {
            layer: Layer::Overlay,
            keyboard_interactivity: KeyboardInteractivity::Exclusive,
            anchor: Anchor::Top | Anchor::Bottom | Anchor::Left | Anchor::Right,
            exclusive_zone: -1,
            ..Default::default()
        },
        keep_compositor_alive: false,
        ..Default::default()
    };

    let run_result = iced_exwlshell::layershell::application(
        move || OverviewApp::new(snapshot.clone(), palette),
        OverviewApp::namespace,
        OverviewApp::update,
        OverviewApp::view,
    )
    .subscription(OverviewApp::subscription)
    .theme(OverviewApp::theme)
    .style(|state, _theme| iced::theme::Style {
        background_color: Color::TRANSPARENT,
        text_color: state.palette().fg,
    })
    .settings(settings)
    .run();

    let _ = std::fs::remove_file(&sock_path);

    if let Some(action) = ui::take_post_exit_action() {
        let dispatch_res = match action {
            ui::PostExitAction::FocusWindow {
                workspace,
                address,
                floating,
            } => hyprland::focus_window(workspace, address, *floating),
            ui::PostExitAction::FocusWorkspace(ws_key) => hyprland::focus_workspace(ws_key),
            ui::PostExitAction::FocusSnapPicker => hyprland::focus_snap_picker(),
        };
        if let Err(err) = dispatch_res {
            eprintln!("dusky_overview: post-unmap dispatch error: {err}");
        }
    }

    run_result
}
