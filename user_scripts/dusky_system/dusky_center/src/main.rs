//! Dusky Center: Native Rust control center for Arch Linux / Hyprland.
//!
//! A fast, data-driven system control panel using Iced (Wayland xdg-toplevel),
//! configured entirely by `dusky_config.toml`. Builds and caches stay on tmpfs.

pub mod appearance;
pub mod backend;
pub mod config;
pub mod icons;
pub mod renderer;
pub mod theme;
pub mod ui;

use config::AppConfig;
use std::env;
use ui::CenterApp;

struct BackgroundExecutor(iced_futures::futures::executor::ThreadPool);

impl iced_futures::Executor for BackgroundExecutor {
    fn new() -> Result<Self, iced_futures::futures::io::Error> {
        iced_futures::futures::executor::ThreadPool::builder()
            .pool_size(4)
            .name_prefix("center-worker")
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
    println!("Dusky Center (Rust)");
    println!("Usage: dusky-center [OPTIONS]\n");
    println!("Options:");
    println!("  --version, -v   Show version and exit");
    println!("  --help, -h      Show this help");
    println!("\nBehavior:");
    println!("  Full system control center (Iced + Wayland xdg-toplevel).");
    println!("  Esc or window close exits and frees all memory.");
    println!("  Build with scripts/build-on-tmpfs.sh to keep intermediates on RAM.");
}

struct SingleInstanceGuard {
    _lock: std::fs::File,
}

impl SingleInstanceGuard {
    pub fn acquire() -> Result<Option<Self>, String> {
        let path = backend::runtime_dir().join("dusky_center_instance.lock");
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

fn main() -> iced::Result {
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
        println!("dusky-center {}", env!("CARGO_PKG_VERSION"));
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
            eprintln!("Could not start Dusky Center: {error}");
            std::process::exit(1);
        }
    };

    let app_config = match AppConfig::load() {
        Ok(cfg) => cfg,
        Err(err) => {
            eprintln!("Failed to load configuration: {err}");
            std::process::exit(1);
        }
    };

    let window_settings = iced::window::Settings {
        size: iced::Size::new(670.0, 720.0),
        min_size: Some(iced::Size::new(500.0, 420.0)),
        resizable: true,
        decorations: false,
        platform_specific: iced_core::window::settings::PlatformSpecific {
            application_id: "dusky-center".into(),
            override_redirect: false,
        },
        exit_on_close_request: true,
        ..Default::default()
    };

    iced::application(
        move || CenterApp::new(app_config.clone()),
        CenterApp::update,
        CenterApp::view,
    )
    .title("Dusky Center")
    .window(window_settings)
    .executor::<BackgroundExecutor>()
    .default_font(iced_core::Font::with_name("Atkinson Hyperlegible"))
    .subscription(CenterApp::subscription)
    .theme(theme)
    .style(style)
    .run()
}

fn style(app: &CenterApp, _theme: &iced_core::Theme) -> iced_core::theme::Style {
    iced_core::theme::Style {
        background_color: app.theme.bg,
        text_color: app.theme.fg,
    }
}

fn theme(_: &CenterApp) -> iced_core::Theme {
    iced_core::Theme::Dark
}
