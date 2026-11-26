//! Dusky Calendar: Native Rust calendar application for Arch Linux / Hyprland.
//!
//! Uses Iced 0.14 (Wayland xdg-toplevel + wgpu Vulkan/EGL), local `.ics` storage,
//! and dynamic Matugen theming (`dusky.json`).

#[path = "../../../rust_shared/ui_fonts.rs"]
mod fonts;

pub mod gpu;
pub mod icons;
pub mod layout;
pub mod model;
pub mod renderer;
pub mod storage;
pub mod theme;
pub mod ui;

use chrono::NaiveDate;
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
use ui::app::CalendarApp;

const DEFAULT_WIDTH: f32 = 1060.0;
const DEFAULT_HEIGHT: f32 = 680.0;
const MIN_WIDTH: f32 = 360.0;
const MIN_HEIGHT: f32 = 600.0;

static IPC_RECEIVER: Mutex<Option<UnboundedReceiver<IpcCommand>>> = Mutex::new(None);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InitialView {
    Month,
    Week,
    Agenda,
}

impl InitialView {
    pub fn parse(raw: &str) -> Option<Self> {
        match raw.trim().to_ascii_lowercase().as_str() {
            "month" => Some(Self::Month),
            "week" => Some(Self::Week),
            "agenda" => Some(Self::Agenda),
            _ => None,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Month => "month",
            Self::Week => "week",
            Self::Agenda => "agenda",
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct LaunchOptions {
    pub initial_view: Option<InitialView>,
    pub initial_date: Option<NaiveDate>,
    pub win_width: Option<f32>,
    pub win_height: Option<f32>,
    pub import_files: Vec<PathBuf>,
    pub data_dir: Option<PathBuf>,
    pub toggle: bool,
}

impl Default for LaunchOptions {
    fn default() -> Self {
        Self {
            initial_view: None,
            initial_date: None,
            win_width: None,
            win_height: None,
            import_files: Vec::new(),
            data_dir: None,
            toggle: false,
        }
    }
}

impl LaunchOptions {
    /// Resolves initial window geometry with precedence: explicit CLI override > saved prefs > default.
    pub fn resolved_window_size(&self, saved: Option<(f32, f32)>) -> (f32, f32) {
        let width = self
            .win_width
            .or_else(|| saved.map(|(w, _)| w))
            .unwrap_or(DEFAULT_WIDTH)
            .max(MIN_WIDTH);
        let height = self
            .win_height
            .or_else(|| saved.map(|(_, h)| h))
            .unwrap_or(DEFAULT_HEIGHT)
            .max(MIN_HEIGHT);
        (width, height)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum IpcCommand {
    Focus,
    Toggle,
    Import(PathBuf),
    Goto {
        view: Option<InitialView>,
        date: Option<NaiveDate>,
    },
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
            .name_prefix("calendar-worker")
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

fn resolve_cli_path(raw: &str, cwd: &Path) -> PathBuf {
    let expanded = if let Some(rest) = raw.strip_prefix("~/") {
        if let Some(home) = env::var_os("HOME").filter(|h| !h.is_empty()) {
            PathBuf::from(home).join(rest)
        } else {
            PathBuf::from(raw)
        }
    } else {
        PathBuf::from(raw)
    };

    if expanded.is_absolute() {
        expanded
    } else {
        cwd.join(expanded)
    }
}

fn parse_args<I>(args: I, cwd: &Path) -> Result<CliAction, String>
where
    I: IntoIterator<Item = String>,
{
    let mut options = LaunchOptions::default();
    let mut args_iter = args.into_iter();
    let mut positional_only = false;

    while let Some(arg) = args_iter.next() {
        if positional_only {
            if !arg.trim().is_empty() {
                options.import_files.push(resolve_cli_path(&arg, cwd));
            }
            continue;
        }

        match arg.as_str() {
            "--" => {
                positional_only = true;
            }
            "--help" | "-h" => return Ok(CliAction::Help),
            "--version" | "-v" | "-V" => return Ok(CliAction::Version),
            "--toggle" => {
                options.toggle = true;
            }
            "--view" => {
                let raw = args_iter
                    .next()
                    .ok_or_else(|| "Option --view requires month, week, or agenda".to_string())?;
                options.initial_view = Some(InitialView::parse(&raw).ok_or_else(|| {
                    format!("Invalid --view value '{raw}'; expected month, week, or agenda")
                })?);
            }
            "--date" => {
                let raw = args_iter
                    .next()
                    .ok_or_else(|| "Option --date requires YYYY-MM-DD".to_string())?;
                let parsed = NaiveDate::parse_from_str(raw.trim(), "%Y-%m-%d").map_err(|_| {
                    format!("Invalid --date value '{raw}'; expected YYYY-MM-DD")
                })?;
                options.initial_date = Some(parsed);
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
            "--import" | "-i" => {
                let raw = args_iter
                    .next()
                    .filter(|v| !v.trim().is_empty())
                    .ok_or_else(|| "Option --import requires a file path".to_string())?;
                options.import_files.push(resolve_cli_path(&raw, cwd));
            }
            "--data-dir" => {
                let raw = args_iter
                    .next()
                    .filter(|v| !v.trim().is_empty())
                    .ok_or_else(|| "Option --data-dir requires a directory path".to_string())?;
                options.data_dir = Some(resolve_cli_path(&raw, cwd));
            }
            flag if flag.starts_with('-') => {
                return Err(format!("Unknown option: {flag}; use --help for usage"));
            }
            positional => {
                if !positional.trim().is_empty() {
                    options
                        .import_files
                        .push(resolve_cli_path(positional, cwd));
                }
            }
        }
    }

    Ok(CliAction::Run(options))
}

fn print_help() {
    println!("Dusky Calendar (Rust)");
    println!("Usage: dusky_calendar [OPTIONS] [FILE.ics ...]\n");
    println!("Options:");
    println!("  --version, -v           Show version and exit");
    println!("  --view <VIEW>           Open to view: month, week, or agenda");
    println!("  --date <YYYY-MM-DD>     Open anchored to the specified date");
    println!("  --import, -i <PATH>     Import an .ics file into the default calendar");
    println!("  --data-dir <PATH>       Override the local calendar storage directory");
    println!("  --width, -w <PX>        Set initial window width (min 360)");
    println!("  --height <PX>           Set initial window height (min 600)");
    println!("  --toggle                Close an existing instance if running, otherwise launch");
    println!("  --help, -h              Show this help\n");
    println!("Behavior:");
    println!("  Native Wayland/Hyprland calendar with local .ics storage and Matugen theming.");
    println!("  Re-launching focuses the running instance and forwards any .ics file imports.");
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
    runtime_dir().join("dusky_calendar.lock")
}

fn instance_socket_path() -> PathBuf {
    runtime_dir().join("dusky_calendar.sock")
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
    let mut cmd = Command::new("hyprctl");
    gpu::inherit_session_environment(&mut cmd);
    let _ = cmd
        .args(["dispatch", "focuswindow", "class:^dusky_calendar$"])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn();
}

fn format_ipc_payload(options: &LaunchOptions) -> String {
    let mut payload = String::new();
    if options.toggle {
        payload.push_str("toggle\n");
        return payload;
    }

    if options.initial_view.is_some() || options.initial_date.is_some() {
        let view = options
            .initial_view
            .map(InitialView::as_str)
            .unwrap_or_default();
        let date = options
            .initial_date
            .map(|d| d.format("%Y-%m-%d").to_string())
            .unwrap_or_default();
        payload.push_str(&format!("goto\t{view}\t{date}\n"));
    }

    for path in &options.import_files {
        let raw = path.to_string_lossy();
        if !raw.contains('\n') && !raw.contains('\r') {
            payload.push_str(&format!("import\t{raw}\n"));
        }
    }

    payload.push_str("focus\n");
    payload
}

fn parse_ipc_line(line: &str) -> Option<IpcCommand> {
    let trimmed = line.trim_end_matches(['\r', '\n']);
    if trimmed.is_empty() {
        return None;
    }
    if trimmed == "focus" {
        return Some(IpcCommand::Focus);
    }
    if trimmed == "toggle" {
        return Some(IpcCommand::Toggle);
    }
    if let Some(rest) = trimmed.strip_prefix("import\t") {
        if !rest.is_empty() {
            return Some(IpcCommand::Import(PathBuf::from(rest)));
        }
        return None;
    }
    if let Some(rest) = trimmed.strip_prefix("goto\t") {
        let mut parts = rest.splitn(2, '\t');
        let view_str = parts.next().unwrap_or_default();
        let date_str = parts.next().unwrap_or_default();
        let view = (!view_str.is_empty())
            .then(|| InitialView::parse(view_str))
            .flatten();
        let date = (!date_str.is_empty())
            .then(|| NaiveDate::parse_from_str(date_str, "%Y-%m-%d").ok())
            .flatten();
        if view.is_some() || date.is_some() {
            return Some(IpcCommand::Goto { view, date });
        }
    }
    None
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
    if !options.toggle {
        focus_hyprland_window();
    }
    true
}

fn spawn_ipc_listener(listener: UnixListener, sender: UnboundedSender<IpcCommand>) {
    let _ = std::thread::Builder::new()
        .name("calendar-ipc".into())
        .spawn(move || {
            for incoming in listener.incoming() {
                let Ok(stream) = incoming else {
                    continue;
                };
                let _ = stream.set_read_timeout(Some(Duration::from_millis(250)));
                let mut reader = BufReader::new(stream);
                let mut line = String::new();
                let mut total_bytes = 0usize;
                while total_bytes < 8192 {
                    line.clear();
                    match reader.read_line(&mut line) {
                        Ok(0) => break,
                        Ok(n) => {
                            total_bytes += n;
                            if let Some(cmd) = parse_ipc_line(&line) {
                                if sender.unbounded_send(cmd).is_err() {
                                    return;
                                }
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
    let cwd = env::current_dir().unwrap_or_default();
    let options = match parse_args(env::args().skip(1), &cwd) {
        Ok(CliAction::Help) => {
            print_help();
            return Ok(());
        }
        Ok(CliAction::Version) => {
            println!("dusky_calendar {}", env!("CARGO_PKG_VERSION"));
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
            // Another instance just acquired the lock; wait briefly for its socket to bind.
            for _ in 0..5 {
                std::thread::sleep(Duration::from_millis(25));
                if send_to_existing_instance(&sock_path, &options) {
                    return Ok(());
                }
            }
            return Ok(());
        }
        Err(error) => {
            eprintln!("Could not start Dusky Calendar: {error}");
            std::process::exit(1);
        }
    };

    // Pin compositor GPU drivers strictly before spawning any IPC, worker, or Iced threads.
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
            eprintln!("Warning: could not bind dusky_calendar IPC socket: {error}");
            None
        }
    };

    fonts::initialize();
    renderer::trace_phase("fonts-ready");

    let saved_size = storage::load_saved_window_size(options.data_dir.as_deref());
    let (init_width, init_height) = options.resolved_window_size(saved_size);

    let window_settings = iced::window::Settings {
        size: iced::Size::new(init_width, init_height),
        min_size: Some(iced::Size::new(MIN_WIDTH, MIN_HEIGHT)),
        resizable: true,
        decorations: false,
        platform_specific: iced_core::window::settings::PlatformSpecific {
            application_id: "dusky_calendar".into(),
            override_redirect: false,
        },
        exit_on_close_request: false,
        ..Default::default()
    };

    let launch = options.clone();
    iced::application(
        move || CalendarApp::boot(launch.clone()),
        CalendarApp::update,
        CalendarApp::view,
    )
    .title("Dusky Calendar")
    .window(window_settings)
    .executor::<BackgroundExecutor>()
    .default_font(iced_core::Font::with_name(fonts::DEFAULT_FAMILY))
    .subscription(CalendarApp::subscription)
    .theme(app_theme)
    .style(app_style)
    .run()
}

fn app_style(app: &CalendarApp, _theme: &iced_core::Theme) -> iced_core::theme::Style {
    iced_core::theme::Style {
        background_color: app.theme.bg,
        text_color: app.theme.fg,
    }
}

fn app_theme(app: &CalendarApp) -> iced_core::Theme {
    let palette = app.theme;
    iced_core::Theme::custom(
        "Matugen",
        iced_core::theme::Palette {
            background: palette.bg,
            text: palette.fg,
            primary: palette.accent,
            success: palette.profiles[0],
            warning: palette.tertiary,
            danger: palette.danger,
        },
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cli_parses_flags_positional_ics_and_relative_paths() {
        let cwd = Path::new("/tmp/work");
        let parsed = parse_args(
            [
                "--view".into(),
                "week".into(),
                "--date".into(),
                "2026-11-26".into(),
                "-w".into(),
                "1024".into(),
                "--height".into(),
                "768".into(),
                "--import".into(),
                "invite.ics".into(),
                "/abs/other.ics".into(),
                "--data-dir".into(),
                "cal_data".into(),
            ],
            cwd,
        )
        .unwrap();

        assert_eq!(
            parsed,
            CliAction::Run(LaunchOptions {
                initial_view: Some(InitialView::Week),
                initial_date: NaiveDate::from_ymd_opt(2026, 11, 26),
                win_width: Some(1024.0),
                win_height: Some(768.0),
                import_files: vec![
                    PathBuf::from("/tmp/work/invite.ics"),
                    PathBuf::from("/abs/other.ics"),
                ],
                data_dir: Some(PathBuf::from("/tmp/work/cal_data")),
                toggle: false,
            })
        );
    }

    #[test]
    fn resolved_window_size_prioritizes_cli_over_saved_and_defaults() {
        let default_opts = LaunchOptions::default();
        assert_eq!(
            default_opts.resolved_window_size(None),
            (DEFAULT_WIDTH, DEFAULT_HEIGHT)
        );
        assert_eq!(
            default_opts.resolved_window_size(Some((1180.0, 760.0))),
            (1180.0, 760.0)
        );

        let partial_cli = LaunchOptions {
            win_width: Some(960.0),
            ..Default::default()
        };
        assert_eq!(
            partial_cli.resolved_window_size(Some((1180.0, 760.0))),
            (960.0, 760.0)
        );
    }

    #[test]
    fn cli_handles_help_version_and_rejects_invalid_args() {
        let cwd = Path::new("/tmp");
        assert_eq!(parse_args(["--help".into()], cwd).unwrap(), CliAction::Help);
        assert_eq!(
            parse_args(["--version".into()], cwd).unwrap(),
            CliAction::Version
        );
        assert!(parse_args(["--view".into(), "year".into()], cwd).is_err());
        assert!(parse_args(["--date".into(), "not-a-date".into()], cwd).is_err());
        assert!(parse_args(["--bogus".into()], cwd).is_err());
    }

    #[test]
    fn ipc_round_trips_commands() {
        let options = LaunchOptions {
            initial_view: Some(InitialView::Agenda),
            initial_date: NaiveDate::from_ymd_opt(2026, 12, 1),
            import_files: vec![PathBuf::from("/home/user/event.ics")],
            ..Default::default()
        };
        let payload = format_ipc_payload(&options);
        let cmds: Vec<IpcCommand> = payload.lines().filter_map(parse_ipc_line).collect();
        assert_eq!(
            cmds,
            vec![
                IpcCommand::Goto {
                    view: Some(InitialView::Agenda),
                    date: NaiveDate::from_ymd_opt(2026, 12, 1),
                },
                IpcCommand::Import(PathBuf::from("/home/user/event.ics")),
                IpcCommand::Focus,
            ]
        );

        let toggle_payload = format_ipc_payload(&LaunchOptions {
            toggle: true,
            ..Default::default()
        });
        assert_eq!(
            parse_ipc_line(toggle_payload.trim()),
            Some(IpcCommand::Toggle)
        );
    }
}
