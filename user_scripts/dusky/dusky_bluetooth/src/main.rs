//! Dusky Bluetooth: Native Rust Bluetooth manager for Arch Linux / Hyprland.
//!
//! Uses Iced 0.14 (Wayland xdg-toplevel + wgpu Vulkan/EGL), native BlueZ D-Bus
//! integration (`zbus`), and dynamic Matugen theming (`dusky.json`).

#[path = "../../../rust_shared/ui_fonts.rs"]
mod fonts;

pub mod audio;
pub mod bluez;
pub mod gpu;
pub mod icons;
pub mod model;
pub mod renderer;
pub mod theme;
pub mod ui;

use iced_futures::futures::channel::mpsc::{self, UnboundedReceiver, UnboundedSender};
use std::env;
use std::fs::{self, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;
use ui::BluetoothApp;

const DEFAULT_WIDTH: f32 = 560.0;
const DEFAULT_HEIGHT: f32 = 680.0;
const MIN_WIDTH: f32 = 360.0;
const MIN_HEIGHT: f32 = 480.0;

static IPC_RECEIVER: Mutex<Option<UnboundedReceiver<IpcCommand>>> = Mutex::new(None);

#[derive(Debug, Clone, Default, PartialEq)]
pub struct LaunchOptions {
    pub win_width: Option<f32>,
    pub win_height: Option<f32>,
    pub toggle: bool,
}

impl LaunchOptions {
    pub fn resolved_window_size(&self) -> (f32, f32) {
        let width = self.win_width.unwrap_or(DEFAULT_WIDTH).max(MIN_WIDTH);
        let height = self.win_height.unwrap_or(DEFAULT_HEIGHT).max(MIN_HEIGHT);
        (width, height)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum IpcCommand {
    Focus,
    Toggle,
}

#[derive(Debug, PartialEq)]
enum CliAction {
    Help,
    Version,
    Run(LaunchOptions),
}

struct BackgroundExecutor(iced_futures::futures::executor::ThreadPool);

impl iced_futures::Executor for BackgroundExecutor {
    fn new() -> Result<Self, iced_futures::futures::io::Error> {
        iced_futures::futures::executor::ThreadPool::builder()
            .pool_size(2)
            .name_prefix("bluetooth-worker")
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

fn parse_args<I>(args: I) -> Result<CliAction, String>
where
    I: IntoIterator<Item = String>,
{
    let mut options = LaunchOptions::default();
    let mut args_iter = args.into_iter();

    while let Some(arg) = args_iter.next() {
        match arg.as_str() {
            "--help" | "-h" => return Ok(CliAction::Help),
            "--version" | "-v" | "-V" => return Ok(CliAction::Version),
            "--toggle" => {
                options.toggle = true;
            }
            "--width" | "-w" => {
                let raw = args_iter
                    .next()
                    .ok_or_else(|| "Option --width requires a positive pixel value".to_string())?;
                let width = raw
                    .parse::<f32>()
                    .ok()
                    .filter(|v| v.is_finite() && *v > 0.0)
                    .ok_or_else(|| format!("Invalid --width value '{raw}'"))?;
                options.win_width = Some(width.max(MIN_WIDTH));
            }
            "--height" => {
                let raw = args_iter
                    .next()
                    .ok_or_else(|| "Option --height requires a positive pixel value".to_string())?;
                let height = raw
                    .parse::<f32>()
                    .ok()
                    .filter(|v| v.is_finite() && *v > 0.0)
                    .ok_or_else(|| format!("Invalid --height value '{raw}'"))?;
                options.win_height = Some(height.max(MIN_HEIGHT));
            }
            other => {
                return Err(format!("Unknown option: {other}; use --help for usage"));
            }
        }
    }

    Ok(CliAction::Run(options))
}

fn print_help() {
    println!("Dusky Bluetooth (Rust)");
    println!("Usage: dusky_bluetooth [OPTIONS]\n");
    println!("Options:");
    println!("  --version, -v           Show version and exit");
    println!("  --width, -w <PX>        Set initial window width (min 360)");
    println!("  --height <PX>           Set initial window height (min 480)");
    println!("  --toggle                Close an existing instance if running, otherwise launch");
    println!("  --help, -h              Show this help\n");
    println!("Behavior:");
    println!(
        "  Native Wayland/Hyprland Bluetooth manager with BlueZ D-Bus integration and Matugen theming."
    );
    println!("  Re-launching focuses the running instance.");
}

pub fn runtime_dir() -> PathBuf {
    if let Some(dir) = env::var_os("XDG_RUNTIME_DIR")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .filter(|p| p.is_absolute() && p.is_dir())
    {
        return dir;
    }

    let uid = unsafe { libc::geteuid() };
    let fallback = PathBuf::from(format!("/tmp/dusky-runtime-{uid}"));
    if !fallback.exists() {
        let _ = fs::create_dir_all(&fallback);
        let _ = fs::set_permissions(&fallback, fs::Permissions::from_mode(0o700));
    }
    fallback
}

fn instance_lock_path() -> PathBuf {
    runtime_dir().join("dusky_bluetooth.lock")
}

fn instance_socket_path() -> PathBuf {
    runtime_dir().join("dusky_bluetooth.sock")
}

struct SingleInstanceGuard {
    _lock: fs::File,
}

impl SingleInstanceGuard {
    fn acquire() -> Result<Option<Self>, String> {
        let path = instance_lock_path();
        let file = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(false)
            .open(&path)
            .map_err(|e| format!("Could not open {}: {e}", path.display()))?;
        match file.try_lock() {
            Ok(()) => Ok(Some(Self { _lock: file })),
            Err(fs::TryLockError::WouldBlock) => Ok(None),
            Err(e) => Err(format!("Could not lock {}: {e}", path.display())),
        }
    }
}

struct SocketGuard(PathBuf);

impl Drop for SocketGuard {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}

pub fn focus_hyprland_window() {
    if env::var_os("HYPRLAND_INSTANCE_SIGNATURE").is_none() {
        return;
    }
    let _ = std::thread::Builder::new()
        .name("bluetooth-focus".into())
        .spawn(|| {
            let mut cmd = Command::new("hyprctl");
            gpu::inherit_session_environment(&mut cmd);
            let _ = cmd
                .args(["dispatch", "focuswindow", "class:^dusky_bluetooth$"])
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
        });
}

fn format_ipc_payload(options: &LaunchOptions) -> &'static str {
    if options.toggle {
        "toggle\n"
    } else {
        "focus\n"
    }
}

fn parse_ipc_line(line: &str) -> Option<IpcCommand> {
    match line.trim_end_matches(['\r', '\n']) {
        "focus" => Some(IpcCommand::Focus),
        "toggle" => Some(IpcCommand::Toggle),
        _ => None,
    }
}

fn send_to_existing_instance(sock_path: &Path, options: &LaunchOptions) -> bool {
    let Ok(mut stream) = UnixStream::connect(sock_path) else {
        return false;
    };
    let _ = stream.set_write_timeout(Some(Duration::from_millis(150)));
    let payload = format_ipc_payload(options);
    if stream.write_all(payload.as_bytes()).is_err() {
        return false;
    }
    let _ = stream.flush();
    true
}

fn spawn_ipc_listener(listener: UnixListener, sender: UnboundedSender<IpcCommand>) {
    let _ = std::thread::Builder::new()
        .name("bluetooth-ipc".into())
        .spawn(move || {
            for incoming in listener.incoming() {
                let Ok(stream) = incoming else {
                    continue;
                };
                let _ = stream.set_read_timeout(Some(Duration::from_millis(250)));
                let mut reader = BufReader::new(stream);
                let mut line = String::new();
                let mut total_bytes = 0usize;
                while total_bytes < 4096 {
                    line.clear();
                    match reader.read_line(&mut line) {
                        Ok(0) => break,
                        Ok(n) => {
                            total_bytes += n;
                            if let Some(cmd) = parse_ipc_line(&line)
                                && sender.unbounded_send(cmd).is_err()
                            {
                                return;
                            }
                        }
                        Err(_) => break,
                    }
                }
            }
        });
}

pub fn take_ipc_receiver() -> Option<UnboundedReceiver<IpcCommand>> {
    IPC_RECEIVER.lock().ok()?.take()
}

fn main() -> iced::Result {
    renderer::trace_startup();
    let options = match parse_args(env::args().skip(1)) {
        Ok(CliAction::Help) => {
            print_help();
            return Ok(());
        }
        Ok(CliAction::Version) => {
            println!("dusky_bluetooth {}", env!("CARGO_PKG_VERSION"));
            return Ok(());
        }
        Ok(CliAction::Run(options)) => options,
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(2);
        }
    };

    let sock_path = instance_socket_path();
    if send_to_existing_instance(&sock_path, &options) {
        return Ok(());
    }

    let _guard = match SingleInstanceGuard::acquire() {
        Ok(Some(guard)) => guard,
        Ok(None) => {
            for _ in 0..5 {
                std::thread::sleep(Duration::from_millis(25));
                if send_to_existing_instance(&sock_path, &options) {
                    return Ok(());
                }
            }
            return Ok(());
        }
        Err(error) => {
            eprintln!("Could not start Dusky Bluetooth: {error}");
            std::process::exit(1);
        }
    };

    // Pin compositor GPU drivers strictly before spawning IPC, D-Bus, or Iced worker threads.
    gpu::optimize_environment();
    renderer::trace_phase("gpu-ready");

    let _ = fs::remove_file(&sock_path);
    let _socket_guard = match UnixListener::bind(&sock_path) {
        Ok(listener) => {
            let (tx, rx) = mpsc::unbounded();
            if let Ok(mut slot) = IPC_RECEIVER.lock() {
                *slot = Some(rx);
            }
            spawn_ipc_listener(listener, tx);
            Some(SocketGuard(sock_path))
        }
        Err(error) => {
            eprintln!("Warning: could not bind dusky_bluetooth IPC socket: {error}");
            None
        }
    };

    fonts::initialize();
    renderer::trace_phase("fonts-ready");

    let (init_width, init_height) = options.resolved_window_size();
    let window_settings = iced::window::Settings {
        size: iced::Size::new(init_width, init_height),
        min_size: Some(iced::Size::new(MIN_WIDTH, MIN_HEIGHT)),
        resizable: true,
        decorations: false,
        platform_specific: iced_core::window::settings::PlatformSpecific {
            application_id: "dusky_bluetooth".into(),
            override_redirect: false,
        },
        exit_on_close_request: false,
        ..Default::default()
    };

    let launch = options.clone();
    iced::application(
        move || BluetoothApp::boot(launch.clone()),
        BluetoothApp::update,
        BluetoothApp::view,
    )
    .title("Dusky Bluetooth")
    .window(window_settings)
    .executor::<BackgroundExecutor>()
    .default_font(iced_core::Font::with_name(fonts::DEFAULT_FAMILY))
    .subscription(BluetoothApp::subscription)
    .theme(app_theme)
    .style(app_style)
    .run()
}

fn app_style(app: &BluetoothApp, _theme: &iced_core::Theme) -> iced_core::theme::Style {
    iced_core::theme::Style {
        background_color: app.theme.bg,
        text_color: app.theme.fg,
    }
}

fn app_theme(app: &BluetoothApp) -> iced_core::Theme {
    let palette = app.theme;
    iced_core::Theme::custom(
        "Matugen",
        iced_core::theme::Palette {
            background: palette.bg,
            text: palette.fg,
            primary: palette.accent,
            success: palette.success,
            warning: palette.warning,
            danger: palette.danger,
        },
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cli_parses_flags_clamps_dimensions_and_rejects_unknown_args() {
        let parsed = parse_args([
            "-w".into(),
            "200".into(),
            "--height".into(),
            "740".into(),
            "--toggle".into(),
        ])
        .unwrap();
        assert_eq!(
            parsed,
            CliAction::Run(LaunchOptions {
                win_width: Some(MIN_WIDTH),
                win_height: Some(740.0),
                toggle: true,
            })
        );

        assert_eq!(parse_args(["--help".into()]).unwrap(), CliAction::Help);
        assert_eq!(
            parse_args(["--version".into()]).unwrap(),
            CliAction::Version
        );
        assert!(parse_args(["--bogus".into()]).is_err());
        assert!(parse_args(["--width".into(), "abc".into()]).is_err());
    }

    #[test]
    fn ipc_payload_round_trips_focus_and_toggle() {
        let focus = format_ipc_payload(&LaunchOptions::default());
        assert_eq!(parse_ipc_line(focus), Some(IpcCommand::Focus));

        let toggle = format_ipc_payload(&LaunchOptions {
            toggle: true,
            ..Default::default()
        });
        assert_eq!(parse_ipc_line(toggle), Some(IpcCommand::Toggle));
        assert_eq!(parse_ipc_line("unknown\n"), None);
    }
}
