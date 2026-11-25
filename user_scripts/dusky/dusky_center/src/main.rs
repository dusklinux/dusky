//! Dusky Center: Native Rust control center for Arch Linux / Hyprland.
//!
//! A fast, data-driven system control panel using Iced (Wayland xdg-toplevel),
//! configured entirely by `dusky_config.toml`. Builds and caches stay on tmpfs.

#[path = "../../../rust_shared/ui_fonts.rs"]
mod fonts;

pub mod appearance;
pub mod backend;
pub mod config;
pub mod icons;
mod gpu;
pub mod renderer;
pub mod theme;
pub mod ui;

use config::AppConfig;
use std::env;
use std::io::Write;
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::time::Duration;
use ui::CenterApp;

struct BackgroundExecutor(iced_futures::futures::executor::ThreadPool);

impl iced_futures::Executor for BackgroundExecutor {
    fn new() -> Result<Self, iced_futures::futures::io::Error> {
        backend::worker::initialize()?;
        iced_futures::futures::executor::ThreadPool::builder()
            .pool_size(2)
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

#[derive(Debug, PartialEq)]
struct CliOptions {
    config_path: Option<PathBuf>,
    initial_page: Option<String>,
    initial_hover: Option<String>,
    win_width: f32,
    win_height: f32,
}

#[derive(Debug, PartialEq)]
enum CliAction {
    Help,
    Version,
    Run(CliOptions),
}

fn resolve_cli_config_path(raw: &str, cwd: &Path) -> PathBuf {
    let path = Path::new(raw);
    if path.is_absolute() {
        path.to_path_buf()
    } else {
        cwd.join(path)
    }
}

fn parse_args<I>(args: I, cwd: &Path) -> Result<CliAction, String>
where
    I: IntoIterator<Item = String>,
{
    let mut options = CliOptions {
        config_path: None,
        initial_page: None,
        initial_hover: None,
        win_width: 670.0,
        win_height: 720.0,
    };
    let mut args_iter = args.into_iter();
    while let Some(arg) = args_iter.next() {
        match arg.as_str() {
            "--help" | "-h" => return Ok(CliAction::Help),
            "--version" | "-v" | "-V" => return Ok(CliAction::Version),
            "--config" | "-c" => {
                let raw = args_iter
                    .next()
                    .filter(|value| !value.trim().is_empty())
                    .ok_or_else(|| "Option --config requires a nonempty path".to_string())?;
                options.config_path = Some(resolve_cli_config_path(&raw, cwd));
            }
            "--page" | "-p" => {
                options.initial_page = args_iter.next();
            }
            "--hover" => {
                options.initial_hover = args_iter.next();
            }
            "--width" | "-w" => {
                if let Some(w_str) = args_iter.next()
                    && let Ok(w) = w_str.parse::<f32>()
                    && w.is_finite()
                    && w > 0.0
                {
                    options.win_width = w;
                }
            }
            "--height" => {
                if let Some(h_str) = args_iter.next()
                    && let Ok(h) = h_str.parse::<f32>()
                    && h.is_finite()
                    && h > 0.0
                {
                    options.win_height = h;
                }
            }
            unknown => {
                return Err(format!("Unknown option: {unknown}; use --help for usage"));
            }
        }
    }
    Ok(CliAction::Run(options))
}

fn print_help() {
    println!("Dusky Center (Rust)");
    println!("Usage: dusky_center [OPTIONS]\n");
    println!("Options:");
    println!("  --version, -v        Show version and exit");
    println!("  --config, -c <PATH>  Load configuration from PATH");
    println!("  --page, -p <PG>      Open directly to page PG (e.g. audio, system)");
    println!("  --width, -w <WIDTH>  Set initial window width");
    println!("  --height <HEIGHT>    Set initial window height");
    println!("  --help, -h           Show this help");
    println!("\nBehavior:");
    println!("  Full system control center (Iced + Wayland xdg-toplevel).");
    println!("  Esc or window close exits and frees all memory.");
    println!("  Build with scripts/build_on_tmpfs.sh to keep intermediates on RAM.");
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

struct SocketGuard(PathBuf);
impl Drop for SocketGuard {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}

fn instance_socket_path() -> PathBuf {
    backend::runtime_dir().join("dusky_center.sock")
}

fn spawn_ipc_watcher(listener: UnixListener) {
    std::thread::spawn(move || {
        if let Ok((_stream, _)) = listener.accept() {
            ui::CenterApp::trigger_dismiss();
            std::thread::sleep(Duration::from_millis(250));
            backend::cmd::shutdown();
            std::process::exit(0);
        }
    });
}

fn main() -> iced::Result {
    renderer::trace_startup();
    let cwd = env::current_dir().unwrap_or_default();
    let options = match parse_args(env::args().skip(1), &cwd) {
        Ok(CliAction::Help) => {
            print_help();
            return Ok(());
        }
        Ok(CliAction::Version) => {
            println!("dusky_center {}", env!("CARGO_PKG_VERSION"));
            return Ok(());
        }
        Ok(CliAction::Run(options)) => options,
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(2);
        }
    };

    // Single-instance zero-latency toggle: if already running, signal it to dismiss and exit immediately.
    let sock_path = instance_socket_path();
    if let Ok(mut stream) = UnixStream::connect(&sock_path) {
        let _ = stream.set_write_timeout(Some(Duration::from_millis(50)));
        let _ = stream.write_all(b"dismiss\n");
        return Ok(());
    }

    let _guard = match SingleInstanceGuard::acquire() {
        Ok(Some(g)) => g,
        Ok(None) => return Ok(()),
        Err(error) => {
            eprintln!("Could not start Dusky Center: {error}");
            std::process::exit(1);
        }
    };

    let _ = std::fs::remove_file(&sock_path);
    let _socket_guard = match UnixListener::bind(&sock_path) {
        Ok(listener) => {
            spawn_ipc_watcher(listener);
            Some(SocketGuard(sock_path))
        }
        Err(error) => {
            eprintln!("Warning: could not bind dusky_center toggle socket: {error}");
            None
        }
    };

    gpu::optimize_environment();
    fonts::initialize();

    let loaded_config = match AppConfig::load_resolved(options.config_path.as_deref()) {
        Ok(loaded) => loaded,
        Err(err) => {
            eprintln!("Failed to load configuration: {err}");
            std::process::exit(1);
        }
    };

    renderer::trace_phase("config-ready");

    let window_settings = iced::window::Settings {
        size: iced::Size::new(options.win_width, options.win_height),
        min_size: Some(iced::Size::new(500.0, 420.0)),
        resizable: true,
        decorations: false,
        platform_specific: iced_core::window::settings::PlatformSpecific {
            application_id: "dusky_center".into(),
            override_redirect: false,
        },
        exit_on_close_request: false,
        ..Default::default()
    };

    let config_override = options.config_path.clone();
    let initial_page_c = options.initial_page.clone();
    let initial_hover_c = options.initial_hover.clone();
    let result = iced::application(
        move || {
            CenterApp::boot_loaded(
                loaded_config.clone(),
                config_override.clone(),
                initial_page_c.clone(),
                initial_hover_c.clone(),
            )
        },
        CenterApp::update,
        CenterApp::view,
    )
    .title("Dusky Center")
    .window(window_settings)
    .executor::<BackgroundExecutor>()
    .default_font(iced_core::Font::with_name(fonts::DEFAULT_FAMILY))
    .subscription(CenterApp::subscription)
    .theme(theme)
    .style(style)
    .run();
    backend::cmd::shutdown();
    result
}

fn style(app: &CenterApp, _theme: &iced_core::Theme) -> iced_core::theme::Style {
    iced_core::theme::Style {
        background_color: app.theme.bg,
        text_color: app.theme.fg,
    }
}

fn theme(app: &CenterApp) -> iced_core::Theme {
    let palette = app.theme;
    iced_core::Theme::custom("Matugen", iced_core::theme::Palette {
        background: palette.bg,
        text: palette.fg,
        primary: palette.accent,
        success: palette.profiles[0],
        warning: palette.tertiary,
        danger: palette.danger,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cli_parses_config_override_and_anchors_relative_paths() {
        let cwd = Path::new("/work/dir");
        let parsed = parse_args(
            [
                "-c".into(),
                "custom/dusky_config.toml".into(),
                "--page".into(),
                "audio".into(),
                "-w".into(),
                "800".into(),
                "--height".into(),
                "900".into(),
            ],
            cwd,
        )
        .unwrap();
        assert_eq!(
            parsed,
            CliAction::Run(CliOptions {
                config_path: Some(PathBuf::from("/work/dir/custom/dusky_config.toml")),
                initial_page: Some("audio".into()),
                initial_hover: None,
                win_width: 800.0,
                win_height: 900.0,
            })
        );

        let absolute = parse_args(["--config".into(), "/etc/dusky.toml".into()], cwd).unwrap();
        assert_eq!(
            absolute,
            CliAction::Run(CliOptions {
                config_path: Some(PathBuf::from("/etc/dusky.toml")),
                initial_page: None,
                initial_hover: None,
                win_width: 670.0,
                win_height: 720.0,
            })
        );
    }

    #[test]
    fn cli_rejects_missing_or_empty_config_and_unknown_flags() {
        let cwd = Path::new("/work");
        assert!(parse_args(["--config".into()], cwd).is_err());
        assert!(parse_args(["-c".into(), "   ".into()], cwd).is_err());
        assert!(parse_args(["--bogus".into()], cwd).is_err());
        assert_eq!(parse_args(["--help".into()], cwd).unwrap(), CliAction::Help);
        assert_eq!(parse_args(["-v".into()], cwd).unwrap(), CliAction::Version);
    }
}
