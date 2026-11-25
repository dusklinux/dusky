//! Hardware + status backend.
//!
//! Rust port of `dusky_backend.py`. All *writes* go to [`runtime_dir`]
//! (tmpfs: `$XDG_RUNTIME_DIR`, `/dev/shm`, or `/tmp`) so the SSD never sees
//! cache churn. Reads may touch `/proc`, `/sys`, and SSD config files.

use crate::backend::cmd::{
    CmdOutput, atomic_write_tmpfs_text, run_command, spawn_detached_service,
};
use crate::config::{FileStamp, xdg_config_dir};
use std::path::{Path, PathBuf};
use std::time::Duration;

// ---------------------------------------------------------------------------
// tmpfs runtime dir (NO SSD WRITE AMPLIFICATION)
// ---------------------------------------------------------------------------

/// Tmpfs-only runtime directory.
///
/// Priority: `$XDG_RUNTIME_DIR/dusky_tray` → `/run/user/$UID/dusky_tray`
/// → `/dev/shm/...` → the temporary directory, verifying tmpfs in each case.
pub fn runtime_dir() -> PathBuf {
    static DIRECTORY: std::sync::OnceLock<PathBuf> = std::sync::OnceLock::new();
    DIRECTORY
        .get_or_init(|| {
            let temp = std::env::temp_dir();
            let uid = unsafe { libc::getuid() };
            let candidates = [
                std::env::var_os("XDG_RUNTIME_DIR")
                    .filter(|p| !p.is_empty())
                    .map(PathBuf::from),
                Some(PathBuf::from(format!("/run/user/{uid}"))),
                Some(PathBuf::from("/dev/shm")),
                Some(temp.clone()),
            ];
            for base in candidates.into_iter().flatten() {
                let dir = if base == *"/dev/shm" || base == temp {
                    base.join(format!("dusky_tray-{uid}"))
                } else {
                    base.join("dusky_tray")
                };
                // Check the existing ancestor before mkdir: never create cache
                // directories on disk just to discover that /tmp is not tmpfs.
                if is_tmpfs(&dir) && std::fs::create_dir_all(&dir).is_ok() && dir.is_dir() {
                    return dir;
                }
            }
            eprintln!("Could not find a writable tmpfs directory for Dusky Tray");
            std::process::exit(1);
        })
        .clone()
}

fn is_tmpfs(path: &Path) -> bool {
    use std::os::unix::ffi::OsStrExt;
    let mut ancestor = path;
    loop {
        let Ok(name) = std::ffi::CString::new(ancestor.as_os_str().as_bytes()) else {
            return false;
        };
        let mut stat = std::mem::MaybeUninit::<libc::statfs>::uninit();
        if unsafe { libc::statfs(name.as_ptr(), stat.as_mut_ptr()) } == 0 {
            return unsafe { stat.assume_init() }.f_type == libc::TMPFS_MAGIC;
        }
        if std::io::Error::last_os_error().kind() != std::io::ErrorKind::NotFound {
            return false;
        }
        let Some(parent) = ancestor.parent() else {
            return false;
        };
        if parent == ancestor {
            return false;
        }
        ancestor = parent;
    }
}

pub fn sunset_state_file() -> PathBuf {
    runtime_dir().join("hyprsunset_state.txt")
}
pub fn ddc_cache_file() -> PathBuf {
    runtime_dir().join("ddcutil_displays.json")
}
fn shared_runtime_dir() -> PathBuf {
    std::env::var_os("XDG_RUNTIME_DIR")
        .filter(|p| !p.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(format!("/run/user/{}", unsafe { libc::getuid() })))
}
pub fn notif_times_file() -> PathBuf {
    shared_runtime_dir().join("dusky_notif_times.json")
}
pub fn mako_blacklist_file() -> PathBuf {
    shared_runtime_dir().join("mako_rofi_blacklist")
}

fn hyprland_socket_path() -> Option<&'static Path> {
    static PATH: std::sync::OnceLock<Option<PathBuf>> = std::sync::OnceLock::new();
    PATH.get_or_init(|| {
        let signature = std::env::var_os("HYPRLAND_INSTANCE_SIGNATURE")?;
        Some(
            shared_runtime_dir()
                .join("hypr")
                .join(signature)
                .join(".socket.sock"),
        )
    })
    .as_deref()
}

fn hyprland_ipc(request: &str) -> Option<String> {
    use std::io::{Read, Write};
    use std::os::unix::net::UnixStream;

    let socket = hyprland_socket_path()?;
    let mut stream = UnixStream::connect(socket).ok()?;
    let timeout = Some(Duration::from_millis(800));
    stream.set_read_timeout(timeout).ok()?;
    stream.set_write_timeout(timeout).ok()?;
    stream.write_all(request.as_bytes()).ok()?;
    let mut response = String::new();
    stream.read_to_string(&mut response).ok()?;
    Some(response)
}

/// Lock this window's preference so config reloads cannot discard it or let
/// workspace rules override it. Global blur.enabled still controls rendering.
/// Call only after the panel has mapped (its first successful fit).
pub fn configure_panel_blur(enabled: bool) {
    let rule = format!(
        "eval hl.dispatch(hl.dsp.window.set_prop({{window=\"class:^dusky_tray$\",prop=\"no_blur\",value=\"{}\",lock=true}}))",
        !enabled
    );
    if !hyprland_ipc(&rule).is_some_and(|reply| reply.trim() == "ok") {
        eprintln!("Could not apply the Hyprland tray blur preference");
    }
}

/// Read the compositor's actual blur switch, rather than the shared Visuals
/// indicator (which can stay true for shadows or other enabled effects).
/// Use Hyprland's request socket to avoid spawning a process every refresh.
pub fn global_blur_enabled() -> Option<bool> {
    let response = hyprland_ipc("j/getoption decoration:blur:enabled")?;
    serde_json::from_str::<serde_json::Value>(&response)
        .ok()?
        .get("bool")?
        .as_bool()
}

#[derive(Clone, Copy)]
pub struct PanelMonitor {
    pub x: f32,
    pub y: f32,
    pub size: iced_core::Size,
}
impl PanelMonitor {
    pub fn limit(self) -> iced_core::Size {
        iced_core::Size::new(
            320.0_f32.min((self.size.width - 40.0).max(1.0)),
            (self.size.height * 0.85)
                .min(self.size.height - 40.0)
                .max(1.0),
        )
    }
}
pub fn panel_monitor() -> Option<PanelMonitor> {
    let response = hyprland_ipc("j/monitors")?;
    let monitors: serde_json::Value = serde_json::from_str(&response).ok()?;
    let monitor = monitors
        .as_array()?
        .iter()
        .find(|m| m["focused"].as_bool() == Some(true))?;
    let scale = monitor["scale"].as_f64()? as f32;
    let mut width = monitor["width"].as_f64()? as f32;
    let mut height = monitor["height"].as_f64()? as f32;
    if monitor["transform"].as_u64()? % 2 == 1 {
        std::mem::swap(&mut width, &mut height);
    }
    Some(PanelMonitor {
        x: monitor["x"].as_f64()? as f32,
        y: monitor["y"].as_f64()? as f32,
        size: iced_core::Size::new(width / scale, height / scale),
    })
}
pub fn fit_panel(monitor: PanelMonitor, size: iced_core::Size) -> bool {
    let actions = format!(
        "eval hl.dispatch(hl.dsp.window.resize({{window=\"class:^dusky_tray$\",x={},y={},relative=false}})); hl.dispatch(hl.dsp.window.move({{window=\"class:^dusky_tray$\",x={},y={},relative=false}}))",
        size.width,
        size.height,
        monitor.x + monitor.size.width - size.width - 20.0,
        monitor.y + monitor.size.height - size.height - 20.0
    );
    let success = hyprland_ipc(&actions).is_some_and(|reply| reply.trim() == "ok");
    if !success {
        eprintln!("Could not resize/position the tray window");
    }
    success
}

// ---------------------------------------------------------------------------
// math helpers
// ---------------------------------------------------------------------------

pub fn clamp(v: f32, lo: f32, hi: f32) -> f32 {
    if !v.is_finite() {
        return lo;
    }
    v.clamp(lo, hi)
}

pub fn percent_int(v: f32, lower: i32) -> i32 {
    (v.round() as i32).clamp(lower, 100)
}

pub fn snap_to_step(v: f32, lo: f32, hi: f32, step: f32) -> f32 {
    let c = clamp(v, lo, hi);
    if step <= 0.0 {
        return c;
    }
    let snapped = ((c - lo) / step).round() * step + lo;
    snapped.clamp(lo, hi)
}

pub fn parse_float(s: &str) -> Option<f32> {
    s.trim().parse::<f32>().ok()
}

fn access_ok(path: &Path, mode: libc::c_int) -> bool {
    // OsStr bytes are not NUL-terminated; CString is required for access().
    let Some(s) = path.as_os_str().to_str() else {
        return false;
    };
    let Ok(c) = std::ffi::CString::new(s) else {
        return false;
    };
    unsafe { libc::access(c.as_ptr(), mode) == 0 }
}

fn which(bin: &'static str) -> Option<String> {
    static CACHE: std::sync::Mutex<
        Option<std::collections::HashMap<&'static str, Option<String>>>,
    > = std::sync::Mutex::new(None);
    let mut guard = CACHE.lock().unwrap_or_else(|e| e.into_inner());
    let cache = guard.get_or_insert_with(std::collections::HashMap::new);
    if let Some(found) = cache.get(bin) {
        return found.clone();
    }
    let resolved = std::env::var_os("PATH").and_then(|paths| {
        std::env::split_paths(&paths).find_map(|dir| {
            let p = dir.join(bin);
            if p.is_file() && access_ok(&p, libc::X_OK) {
                return Some(p.to_string_lossy().into_owned());
            }
            None
        })
    });
    cache.insert(bin, resolved.clone());
    resolved
}

// ---------------------------------------------------------------------------
// clock
// ---------------------------------------------------------------------------

pub fn current_time_date() -> (String, String, chrono::NaiveDate) {
    let now = chrono::Local::now();
    (
        now.format("%I:%M").to_string(),
        now.format("%A, %B %d").to_string(),
        now.date_naive(),
    )
}

// ---------------------------------------------------------------------------
// volume (wpctl)
// ---------------------------------------------------------------------------

fn parse_wpctl_volume(stdout: &str) -> Option<(f32, bool)> {
    let parts: Vec<&str> = stdout.split_whitespace().collect();
    if parts.len() < 2 {
        return None;
    }
    let v = parse_float(parts[1])?;
    let muted = parts[2..].contains(&"[MUTED]");
    Some((clamp(v * 100.0, 0.0, 100.0), muted))
}

pub fn get_volume() -> Option<(f32, bool)> {
    let wpctl = which("wpctl")?;
    let out = run_command(
        &[wpctl, "get-volume".into(), "@DEFAULT_AUDIO_SINK@".into()],
        Duration::from_millis(800),
        true,
    )?;
    if !out.status {
        return None;
    }
    parse_wpctl_volume(&out.stdout)
}

pub fn apply_volume(value: f32) {
    let Some(wpctl) = which("wpctl") else { return };
    let vol = percent_int(value, 0);
    let a = vec![
        wpctl.clone(),
        "set-volume".into(),
        "@DEFAULT_AUDIO_SINK@".into(),
        format!("{vol}%"),
    ];
    let ok = run_command(&a, Duration::from_millis(1200), false)
        .map(|o| o.status)
        .unwrap_or(false);
    if ok && vol > 0 {
        let _ = run_command(
            &[
                wpctl,
                "set-mute".into(),
                "@DEFAULT_AUDIO_SINK@".into(),
                "0".into(),
            ],
            Duration::from_millis(1200),
            false,
        );
    }
}

// ---------------------------------------------------------------------------
// brightness: sysfs + brightnessctl + logind + ddcutil
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
struct BacklightDevice {
    name: String,
    max: i64,
    path: PathBuf,
}

fn backlight_priority(name: &str) -> i32 {
    let l = name.to_lowercase();
    if l.starts_with("intel_backlight") {
        400
    } else if l.starts_with("amdgpu_bl") {
        350
    } else if l.starts_with("nvidia") {
        300
    } else if l.starts_with("ddcci") {
        250
    } else if l.contains("backlight") {
        200
    } else if l.starts_with("acpi_video") {
        100
    } else {
        0
    }
}

fn sysfs_backlights() -> Vec<BacklightDevice> {
    let base = Path::new("/sys/class/backlight");
    let Ok(entries) = std::fs::read_dir(base) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        let max_p = path.join("max_brightness");
        let bri_p = path.join("brightness");
        if !max_p.is_file() || !bri_p.is_file() {
            continue;
        }
        let Ok(max_txt) = std::fs::read_to_string(&max_p) else {
            continue;
        };
        let Ok(max) = max_txt.trim().parse::<i64>() else {
            continue;
        };
        if max <= 0 {
            continue;
        }
        let name = entry.file_name().to_string_lossy().into_owned();
        out.push(BacklightDevice { name, max, path });
    }
    out.sort_by(|a, b| {
        backlight_priority(&b.name)
            .cmp(&backlight_priority(&a.name))
            .then(b.max.cmp(&a.max))
    });
    out
}

fn preferred_backlight() -> Option<BacklightDevice> {
    // Prefer writable device.
    let devices = sysfs_backlights();
    let index = devices
        .iter()
        .position(|d| access_ok(&d.path.join("brightness"), libc::W_OK))
        .unwrap_or(0);
    devices.into_iter().nth(index)
}

pub fn has_local_brightness() -> bool {
    preferred_backlight().is_some()
        && (which("brightnessctl").is_some()
            || which("busctl").is_some()
            || sysfs_backlights()
                .iter()
                .any(|d| access_ok(&d.path.join("brightness"), libc::W_OK)))
}

fn read_sysfs_brightness() -> Option<f32> {
    let dev = preferred_backlight()?;
    let actual_p = dev.path.join("actual_brightness");
    let read_p = if actual_p.is_file() {
        actual_p
    } else {
        dev.path.join("brightness")
    };
    let cur = parse_float(&std::fs::read_to_string(read_p).ok()?)?;
    let max = parse_float(&std::fs::read_to_string(dev.path.join("max_brightness")).ok()?)?;
    if max <= 0.0 {
        return None;
    }
    Some(clamp(cur / max * 100.0, 0.0, 100.0))
}

fn read_brightnessctl() -> Option<f32> {
    let ctl = which("brightnessctl")?;
    let mut args = vec![ctl, "--class=backlight".into()];
    if let Some(dev) = preferred_backlight() {
        args.push(format!("--device={}", dev.name));
    }
    args.push("--machine-readable".into());
    let out = run_command(&args, Duration::from_millis(800), true)?;
    if !out.status {
        return None;
    }
    let line = out.stdout.lines().next()?;
    let parts: Vec<&str> = line.split(',').collect();
    if parts.len() < 5 {
        return None;
    }
    let v = parse_float(parts[3].trim_end_matches('%'))?;
    Some(clamp(v, 0.0, 100.0))
}

pub fn get_brightness() -> Option<f32> {
    if let Some(v) = read_sysfs_brightness() {
        return Some(v);
    }
    if has_local_brightness()
        && let Some(v) = read_brightnessctl()
    {
        return Some(v);
    }
    ddc_current_percent()
}

pub fn apply_brightness(value: f32) {
    apply_local_brightness(value);
    apply_ddc_brightness(value);
}

fn apply_logind_brightness(device: &BacklightDevice, value: i32) -> bool {
    let Some(bus) = which("busctl") else {
        return false;
    };
    let raw = ((value as f64 / 100.0) * device.max as f64).round() as i64;
    run_command(
        &[
            bus,
            "--system".into(),
            "call".into(),
            "org.freedesktop.login1".into(),
            // Resolve the caller's session (or display session for user services).
            // No environment session ID, cached object path, or extra lookup.
            "/org/freedesktop/login1/session/auto".into(),
            "org.freedesktop.login1.Session".into(),
            "SetBrightness".into(),
            "ssu".into(),
            "backlight".into(),
            device.name.clone(),
            raw.clamp(1, device.max).to_string(),
        ],
        Duration::from_millis(1200),
        false,
    )
    .is_some_and(|out| out.status)
}

pub fn apply_local_brightness(value: f32) {
    let target = percent_int(value, 1);
    // 1. direct sysfs write
    if let Some(dev) = sysfs_backlights()
        .into_iter()
        .find(|d| access_ok(&d.path.join("brightness"), libc::W_OK))
    {
        let raw = ((target as f64 / 100.0) * dev.max as f64).round() as i64;
        let raw = raw.clamp(1, dev.max);
        if std::fs::write(dev.path.join("brightness"), format!("{raw}\n")).is_ok() {
            return;
        }
    }
    // 2. logind controls read-only backlights without an extra GUI or daemon.
    if let Some(device) = preferred_backlight()
        && apply_logind_brightness(&device, target)
    {
        return;
    }
    // 3. brightnessctl
    if let Some(ctl) = which("brightnessctl") {
        let mut args = vec![ctl, "--class=backlight".into()];
        if let Some(dev) = preferred_backlight() {
            args.push(format!("--device={}", dev.name));
        }
        args.extend(["--quiet".into(), "set".into(), format!("{target}%")]);
        let _ = run_command(&args, Duration::from_millis(1200), false);
    }
}

// --- DDC (simplified parallel query, sequential set) ---

fn parse_detect_buses(stdout: &str) -> Vec<i32> {
    let mut buses = std::collections::BTreeSet::new();
    for line in stdout.lines() {
        for token in line.replace([':', ','], " ").split_whitespace() {
            let suffix = if let Some(s) = token.strip_prefix("/dev/i2c-") {
                s
            } else if let Some(s) = token.strip_prefix("i2c-") {
                s
            } else {
                continue;
            };
            if let Ok(n) = suffix.parse::<i32>() {
                buses.insert(n);
            }
        }
    }
    buses.into_iter().collect()
}

fn parse_getvcp(stdout: &str) -> Option<(i32, i32)> {
    for line in stdout.lines() {
        let parts: Vec<&str> = line.split_whitespace().collect();
        if parts.len() >= 5
            && parts[0] == "VCP"
            && parts[2] == "C"
            && let (Ok(cur), Ok(max)) = (parts[3].parse::<i32>(), parts[4].parse::<i32>())
            && max > 0
        {
            return Some((cur, max));
        }
    }
    None
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct DdcDisplay {
    bus: i32,
    max: i32,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct DdcCacheData {
    connectors: Vec<String>,
    displays: Vec<DdcDisplay>,
}

fn is_internal_drm_connector(connector: &str) -> bool {
    connector.starts_with("eDP-") || connector.starts_with("LVDS-") || connector.starts_with("DSI-")
}

fn connected_external_drm_connectors_in(drm_root: &Path) -> Option<Vec<String>> {
    let entries = std::fs::read_dir(drm_root).ok()?;
    let mut connected = Vec::new();
    for entry in entries.flatten() {
        let name = entry.file_name();
        let Some(name_str) = name.to_str() else {
            continue;
        };
        let Some(rest) = name_str.strip_prefix("card") else {
            continue;
        };
        let Some((idx, connector)) = rest.split_once('-') else {
            continue;
        };
        if idx.is_empty()
            || !idx.bytes().all(|b| b.is_ascii_digit())
            || is_internal_drm_connector(connector)
        {
            continue;
        }
        let Ok(status) = std::fs::read_to_string(entry.path().join("status")) else {
            continue;
        };
        if status.trim() == "connected" {
            connected.push(name_str.to_owned());
        }
    }
    connected.sort_unstable();
    Some(connected)
}

fn parse_ddc_cache(raw: &str) -> Option<DdcCacheData> {
    let v: serde_json::Value = serde_json::from_str(raw).ok()?;
    let connectors = v
        .get("connectors")?
        .as_array()?
        .iter()
        .filter_map(|c| c.as_str().map(str::to_owned))
        .collect();
    let displays = v
        .get("displays")?
        .as_array()?
        .iter()
        .filter_map(|item| {
            let bus = item.get("bus")?.as_i64()?;
            let max = item.get("max")?.as_i64().unwrap_or(100);
            (bus >= 0 && max > 0).then_some(DdcDisplay {
                bus: bus as i32,
                max: max as i32,
            })
        })
        .collect();
    Some(DdcCacheData {
        connectors,
        displays,
    })
}

fn serialize_ddc_cache(data: &DdcCacheData) -> String {
    let displays: Vec<serde_json::Value> = data
        .displays
        .iter()
        .map(|d| serde_json::json!({"bus": d.bus, "max": d.max}))
        .collect();
    let obj = serde_json::json!({
        "connectors": data.connectors,
        "displays": displays,
    });
    format!("{}\n", serde_json::to_string(&obj).unwrap_or_default())
}

fn ddc_cached_displays(
    cache_path: &Path,
    connectors: &[String],
    ttl: Duration,
) -> Option<Vec<DdcDisplay>> {
    let fresh = cache_path
        .metadata()
        .ok()
        .and_then(|m| m.modified().ok())
        .and_then(|t| t.elapsed().ok())
        .is_some_and(|age| age < ttl);
    if !fresh {
        return None;
    }
    let txt = std::fs::read_to_string(cache_path).ok()?;
    let cached = parse_ddc_cache(&txt)?;
    (cached.connectors == connectors).then_some(cached.displays)
}

fn ddc_read_vcp10(ddc: &str, bus: i32) -> Option<(i32, i32)> {
    let out = run_command(
        &[
            ddc.to_owned(),
            "getvcp".into(),
            "10".into(),
            "--terse".into(),
            "--bus".into(),
            bus.to_string(),
        ],
        Duration::from_secs(2),
        true,
    )?;
    out.status.then(|| parse_getvcp(&out.stdout)).flatten()
}

fn ddc_displays_with_live_reading() -> (Vec<DdcDisplay>, Option<(i32, i32)>) {
    let connectors = match connected_external_drm_connectors_in(Path::new("/sys/class/drm")) {
        Some(c) if c.is_empty() => return (Vec::new(), None),
        Some(c) => c,
        None => Vec::new(),
    };
    let Some(ddc) = which("ddcutil") else {
        return (Vec::new(), None);
    };
    let cache = ddc_cache_file();
    if let Some(displays) = ddc_cached_displays(&cache, &connectors, Duration::from_secs(45)) {
        return (displays, None);
    }
    let stale_displays = std::fs::read_to_string(&cache)
        .ok()
        .and_then(|txt| parse_ddc_cache(&txt))
        .filter(|c| c.connectors == connectors)
        .map(|c| c.displays)
        .unwrap_or_default();

    let out = run_command(
        &[ddc.clone(), "detect".into(), "--terse".into()],
        Duration::from_secs(8),
        true,
    );
    if let Some(out) = out
        && out.status
    {
        let mut displays = Vec::new();
        let mut live_reading = None;
        for bus in parse_detect_buses(&out.stdout) {
            let max = if let Some((cur, max)) = ddc_read_vcp10(&ddc, bus) {
                if live_reading.is_none() {
                    live_reading = Some((cur, max));
                }
                max
            } else {
                100
            };
            displays.push(DdcDisplay { bus, max });
        }
        let data = DdcCacheData {
            connectors,
            displays: displays.clone(),
        };
        let _ = atomic_write_tmpfs_text(&cache, &serialize_ddc_cache(&data));
        return (displays, live_reading);
    }
    (stale_displays, None)
}

pub fn ddc_current_percent() -> Option<f32> {
    let (displays, live) = ddc_displays_with_live_reading();
    if let Some((cur, max)) = live {
        return Some(clamp(cur as f32 / max.max(1) as f32 * 100.0, 0.0, 100.0));
    }
    let ddc = which("ddcutil")?;
    for display in displays {
        if let Some((cur, max)) = ddc_read_vcp10(&ddc, display.bus) {
            return Some(clamp(cur as f32 / max.max(1) as f32 * 100.0, 0.0, 100.0));
        }
    }
    None
}

pub fn apply_ddc_brightness(value: f32) {
    let (displays, _) = ddc_displays_with_live_reading();
    if displays.is_empty() {
        return;
    }
    let Some(ddc) = which("ddcutil") else { return };
    let pct = percent_int(value, 1);
    for display in displays {
        let maximum = display.max.max(1);
        let raw = ((pct as f64 / 100.0) * maximum as f64).round() as i32;
        let raw = raw.clamp(1, maximum);
        let _ = run_command(
            &[
                ddc.clone(),
                "setvcp".into(),
                "10".into(),
                raw.to_string(),
                "--bus".into(),
                display.bus.to_string(),
            ],
            Duration::from_millis(2200),
            false,
        );
    }
}

// ---------------------------------------------------------------------------
// hyprsunset (night light)
// ---------------------------------------------------------------------------

fn hyprsunset_socket_path() -> Option<&'static Path> {
    static PATH: std::sync::OnceLock<Option<PathBuf>> = std::sync::OnceLock::new();
    PATH.get_or_init(|| {
        let signature = std::env::var_os("HYPRLAND_INSTANCE_SIGNATURE")?;
        Some(
            shared_runtime_dir()
                .join("hypr")
                .join(signature)
                .join(".hyprsunset.sock"),
        )
    })
    .as_deref()
}

fn hyprsunset_ipc_at(socket: &Path, request: &str) -> Option<String> {
    use std::io::{Read, Write};
    use std::os::unix::net::UnixStream;

    let mut stream = UnixStream::connect(socket).ok()?;
    let timeout = Some(Duration::from_millis(800));
    stream.set_read_timeout(timeout).ok()?;
    stream.set_write_timeout(timeout).ok()?;
    stream.write_all(request.as_bytes()).ok()?;
    let _ = stream.shutdown(std::net::Shutdown::Write);
    let mut response = String::new();
    stream.read_to_string(&mut response).ok()?;
    Some(response)
}

fn hyprsunset_ipc(request: &str) -> Option<String> {
    hyprsunset_ipc_at(hyprsunset_socket_path()?, request)
}

pub fn has_sunset() -> bool {
    hyprsunset_socket_path().is_some() && which("hyprsunset").is_some()
}

pub fn is_sunset_service_enabled() -> bool {
    static ENABLED: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
    *ENABLED.get_or_init(|| {
        let Some(sys) = which("systemctl") else {
            return false;
        };
        run_command(
            &[
                sys,
                "--user".into(),
                "is-enabled".into(),
                "hyprsunset.service".into(),
            ],
            Duration::from_millis(500),
            true,
        )
        .is_some_and(|o| o.status)
    })
}

// Keep cache/IPC temperatures in kelvin; expose increasing warmth to the UI.
const SUNSET_NEUTRAL: f32 = 6500.0;
const SUNSET_WARMEST: f32 = 1000.0;

fn sunset_percent(kelvin: f32) -> f32 {
    clamp(
        (SUNSET_NEUTRAL - kelvin) * 100.0 / (SUNSET_NEUTRAL - SUNSET_WARMEST),
        0.0,
        100.0,
    )
}

fn sunset_temperature(percent: f32) -> i32 {
    (SUNSET_NEUTRAL - clamp(percent, 0.0, 100.0) * (SUNSET_NEUTRAL - SUNSET_WARMEST) / 100.0)
        .round() as i32
}

pub fn get_sunset() -> Option<f32> {
    if let Some(identity) = hyprsunset_ipc("identity get") {
        if identity.trim() == "true" {
            return Some(0.0);
        }
        if let Some(temp) = hyprsunset_ipc("temperature")
            && let Some(v) = parse_float(&temp)
        {
            return Some(sunset_percent(v));
        }
    }
    if !has_sunset() || !is_sunset_service_enabled() {
        return None;
    }
    if let Ok(txt) = std::fs::read_to_string(sunset_state_file())
        && let Some(v) = parse_float(&txt)
    {
        return Some(sunset_percent(v));
    }
    Some(sunset_percent(4500.0))
}

pub fn apply_sunset(value: f32) {
    let target = sunset_temperature(value);
    let request = if target == SUNSET_NEUTRAL as i32 {
        "identity".to_owned()
    } else {
        format!("temperature {target}")
    };
    let send =
        |req: &str| hyprsunset_ipc(req).is_some_and(|reply| matches!(reply.trim(), "" | "ok"));
    if send(&request) {
        let _ = atomic_write_tmpfs_text(&sunset_state_file(), &format!("{target}\n"));
        return;
    }
    // Fallback: ensure service is started, then retry once over the socket.
    if let Some(sys) = which("systemctl") {
        let _ = run_command(
            &[
                sys,
                "--user".into(),
                "start".into(),
                "hyprsunset.service".into(),
            ],
            Duration::from_millis(1200),
            false,
        );
    }
    if has_sunset() {
        std::thread::sleep(Duration::from_millis(300));
        if send(&request) {
            let _ = atomic_write_tmpfs_text(&sunset_state_file(), &format!("{target}\n"));
        }
    }
}

// ---------------------------------------------------------------------------
// power profiles (TLP)
// ---------------------------------------------------------------------------

pub fn get_power_profile() -> Option<String> {
    if let Ok(txt) = std::fs::read_to_string("/run/tlp/last_pwr")
        && let Some(code) = txt.split_whitespace().next()
    {
        let mapped = match code {
            "0" => "performance",
            "1" => "balanced",
            "2" => "power-saver",
            _ => code,
        };
        if ["balanced", "performance", "power-saver"].contains(&mapped) {
            return Some(mapped.to_owned());
        }
    }
    let p = xdg_config_dir().join("dusky/settings/tlp_state");
    if let Ok(txt) = std::fs::read_to_string(p) {
        let s = txt.trim().to_lowercase();
        if ["balanced", "performance", "power-saver"].contains(&s.as_str()) {
            return Some(s);
        }
    }
    None
}

pub fn apply_power_profile(profile: &str) -> bool {
    let home = std::env::var("HOME").unwrap_or_default();
    let script = format!("{home}/user_scripts/battery/tlp/tlp_mode_toggle.sh");
    if !Path::new(&script).is_file() {
        return false;
    }
    let arg = match profile {
        "balanced" => "balanced",
        "performance" => "performance",
        "power-saver" | "power_saver" | "powersaver" => "power-saver",
        _ => return false,
    };
    match run_command(&[script, arg.into()], Duration::from_secs(4), true) {
        Some(out) if out.status => true,
        Some(out) => {
            eprintln!("Power profile failed ({}): {}", out.code, out.stderr.trim());
            false
        }
        None => false,
    }
}

pub fn powertop_autotune() -> bool {
    run_command(
        &[
            "/usr/bin/sudo".into(),
            "-n".into(),
            "/usr/bin/powertop".into(),
            "--auto-tune".into(),
        ],
        Duration::from_secs(120),
        true,
    )
    .map(|o| o.status)
    .unwrap_or(false)
}

// ---------------------------------------------------------------------------
// radios: wifi (NM) + bluetooth (bluez)
// ---------------------------------------------------------------------------

fn read_busctl_bool(out: &Option<CmdOutput>) -> Option<bool> {
    let o = out.as_ref()?;
    if !o.status {
        return None;
    }
    let parts: Vec<&str> = o.stdout.split_whitespace().collect();
    if parts.len() == 2 && parts[0] == "b" {
        match parts[1] {
            "true" => Some(true),
            "false" => Some(false),
            _ => None,
        }
    } else {
        None
    }
}

fn wifi_connected_in(net_dir: &Path) -> Option<bool> {
    let entries = std::fs::read_dir(net_dir).ok()?;
    for entry in entries.flatten() {
        let path = entry.path();
        if !path.join("device").exists() {
            continue;
        }
        if !path.join("phy80211").exists() && !path.join("wireless").exists() {
            continue;
        }
        let Ok(kind) = std::fs::read_to_string(path.join("type")) else {
            continue;
        };
        if kind.trim() != "1" {
            continue;
        }
        if let Ok(state) = std::fs::read_to_string(path.join("operstate"))
            && state.trim() == "up"
        {
            return Some(true);
        }
    }
    Some(false)
}

pub fn wifi_connected() -> Option<bool> {
    wifi_connected_in(Path::new("/sys/class/net"))
}

pub fn get_wifi() -> Option<bool> {
    let bus = which("busctl")?;
    let out = run_command(
        &[
            bus,
            "get-property".into(),
            "org.freedesktop.NetworkManager".into(),
            "/org/freedesktop/NetworkManager".into(),
            "org.freedesktop.NetworkManager".into(),
            "WirelessEnabled".into(),
        ],
        Duration::from_millis(800),
        true,
    );
    read_busctl_bool(&out)
}

pub fn set_wifi(on: bool) -> Option<bool> {
    let bus = which("busctl")?;
    let val = if on { "true" } else { "false" };
    let _ = run_command(
        &[
            bus.clone(),
            "set-property".into(),
            "org.freedesktop.NetworkManager".into(),
            "/org/freedesktop/NetworkManager".into(),
            "org.freedesktop.NetworkManager".into(),
            "WirelessEnabled".into(),
            "b".into(),
            val.into(),
        ],
        Duration::from_secs(2),
        false,
    );
    let check = run_command(
        &[
            bus,
            "get-property".into(),
            "org.freedesktop.NetworkManager".into(),
            "/org/freedesktop/NetworkManager".into(),
            "org.freedesktop.NetworkManager".into(),
            "WirelessEnabled".into(),
        ],
        Duration::from_secs(1),
        true,
    );
    read_busctl_bool(&check)
}

pub fn bt_adapter() -> Option<String> {
    let base = Path::new("/sys/class/bluetooth");
    let entries = std::fs::read_dir(base).ok()?;
    let mut adapters: Vec<String> = entries
        .flatten()
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .filter(|n| n.starts_with("hci") && n[3..].chars().all(|c| c.is_ascii_digit()))
        .collect();
    adapters.sort();
    adapters.into_iter().next()
}

pub fn get_bt() -> Option<bool> {
    let adapter = bt_adapter()?;
    let bus = which("busctl")?;
    let out = run_command(
        &[
            bus,
            "get-property".into(),
            "org.bluez".into(),
            format!("/org/bluez/{adapter}"),
            "org.bluez.Adapter1".into(),
            "Powered".into(),
        ],
        Duration::from_millis(800),
        true,
    );
    read_busctl_bool(&out)
}

pub fn set_bt(on: bool) -> Option<bool> {
    let adapter = bt_adapter()?;
    let bus = which("busctl")?;
    if on {
        let _ = run_command(
            &[
                "/usr/bin/sudo".into(),
                "-n".into(),
                "/usr/bin/rfkill".into(),
                "unblock".into(),
                "bluetooth".into(),
            ],
            Duration::from_secs(2),
            false,
        );
    }
    let val = if on { "true" } else { "false" };
    let _ = run_command(
        &[
            bus.clone(),
            "set-property".into(),
            "org.bluez".into(),
            format!("/org/bluez/{adapter}"),
            "org.bluez.Adapter1".into(),
            "Powered".into(),
            "b".into(),
            val.into(),
        ],
        Duration::from_secs(2),
        false,
    );
    let check = run_command(
        &[
            bus,
            "get-property".into(),
            "org.bluez".into(),
            format!("/org/bluez/{adapter}"),
            "org.bluez.Adapter1".into(),
            "Powered".into(),
        ],
        Duration::from_secs(1),
        true,
    );
    read_busctl_bool(&check)
}

// ---------------------------------------------------------------------------
// metrics: cpu / ram / net / weather / updates / idle / blur / audio / dnd
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq)]
pub struct CpuState {
    pub text: String,
    pub power_watts: Option<f64>,
    pub temp_c: Option<f64>,
    pub tooltip: String,
}

impl Default for CpuState {
    fn default() -> Self {
        Self {
            text: "--".into(),
            power_watts: None,
            temp_c: None,
            tooltip: "Power: N/A\nTemp: N/A\nLMB: Open btop".into(),
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct RamState {
    pub text: String,
    pub tooltip: String,
}

impl Default for RamState {
    fn default() -> Self {
        Self {
            text: "--".into(),
            tooltip: "Memory · Open zramctl".into(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct RaplDomain {
    energy_path: PathBuf,
    max_range_path: PathBuf,
}

#[derive(Debug, Clone)]
struct RaplEnergyReading {
    energy_uj: u64,
    at: Duration,
}

#[derive(Debug, Default)]
struct RaplTracker {
    domains: Option<Vec<RaplDomain>>,
    last_discovery: Option<Duration>,
    previous: Vec<Option<RaplEnergyReading>>,
}

fn scan_rapl_prefix(powercap_dir: &Path, prefix: &str) -> Vec<RaplDomain> {
    let Ok(entries) = std::fs::read_dir(powercap_dir) else {
        return Vec::new();
    };
    let mut names: Vec<String> = entries
        .flatten()
        .filter_map(|e| e.file_name().into_string().ok())
        .collect();
    names.sort_unstable();

    let mut domains = Vec::new();
    for name in names {
        let Some(suffix) = name.strip_prefix(prefix) else {
            continue;
        };
        // Match only top-level package domains (e.g. intel-rapl:0), skipping subzones (intel-rapl:0:0).
        if suffix.is_empty() || !suffix.bytes().all(|b| b.is_ascii_digit()) {
            continue;
        }
        let dir = powercap_dir.join(&name);
        let Ok(domain_name) = std::fs::read_to_string(dir.join("name")) else {
            continue;
        };
        if !domain_name.trim().starts_with("package-") {
            continue;
        }
        let energy_path = dir.join("energy_uj");
        if read_sysfs_u64(&energy_path, false).is_some() {
            domains.push(RaplDomain {
                energy_path,
                max_range_path: dir.join("max_energy_range_uj"),
            });
        }
    }
    domains
}

fn discover_rapl_domains_in(powercap_dir: &Path) -> Vec<RaplDomain> {
    let msr = scan_rapl_prefix(powercap_dir, "intel-rapl:");
    if !msr.is_empty() {
        return msr;
    }
    scan_rapl_prefix(powercap_dir, "intel-rapl-mmio:")
}

fn rapl_power_reading(
    powercap_dir: &Path,
    now: Option<Duration>,
    tracker: &mut RaplTracker,
) -> Option<f64> {
    let Some(now) = now else {
        tracker.previous.clear();
        return None;
    };

    let needs_discovery = tracker.domains.is_none()
        || (tracker.domains.as_ref().is_some_and(Vec::is_empty)
            && tracker
                .last_discovery
                .is_none_or(|t| now.saturating_sub(t) >= Duration::from_secs(30)));

    if needs_discovery {
        let found = discover_rapl_domains_in(powercap_dir);
        tracker.previous = vec![None; found.len()];
        tracker.domains = Some(found);
        tracker.last_discovery = Some(now);
    }

    let domains = tracker.domains.as_ref()?;
    if domains.is_empty() {
        return None;
    }
    if tracker.previous.len() != domains.len() {
        tracker.previous.resize(domains.len(), None);
    }

    let mut total_watts = 0.0_f64;
    let mut valid_domains = 0usize;

    for (domain, prev_slot) in domains.iter().zip(tracker.previous.iter_mut()) {
        let Some(cur_uj) = read_sysfs_u64(&domain.energy_path, false) else {
            // Invalidate cache if permissions change or driver reloads; re-probe on next cycle.
            tracker.domains = None;
            tracker.previous.clear();
            return None;
        };

        if let Some(prev) = prev_slot.as_ref()
            && let Some(elapsed) = now.checked_sub(prev.at)
            && (Duration::from_millis(500)..=Duration::from_secs(5)).contains(&elapsed)
        {
            let dt_us = elapsed.as_micros();
            if dt_us > 0 {
                let delta_uj = if cur_uj >= prev.energy_uj {
                    Some(u128::from(cur_uj - prev.energy_uj))
                } else {
                    read_sysfs_u64(&domain.max_range_path, false).and_then(|max_range| {
                        if max_range > 0 && cur_uj < max_range && prev.energy_uj < max_range {
                            Some(u128::from(max_range - prev.energy_uj) + u128::from(cur_uj))
                        } else {
                            None
                        }
                    })
                };

                if let Some(duj) = delta_uj
                    && duj <= dt_us.saturating_mul(5_000)
                {
                    total_watts += duj as f64 / dt_us as f64;
                    valid_domains += 1;
                }
            }
        }

        *prev_slot = Some(RaplEnergyReading {
            energy_uj: cur_uj,
            at: now,
        });
    }

    (valid_domains > 0).then_some(total_watts)
}

fn sample_cpu_power_watts() -> Option<f64> {
    static TRACKER: std::sync::Mutex<Option<RaplTracker>> = std::sync::Mutex::new(None);
    let now = network_time();
    let mut guard = TRACKER.lock().unwrap_or_else(|e| e.into_inner());
    let tracker = guard.get_or_insert_with(RaplTracker::default);
    rapl_power_reading(Path::new("/sys/class/powercap"), now, tracker)
}

#[derive(Debug, Default)]
struct CpuTempTracker {
    sensor_path: Option<PathBuf>,
    last_discovery: Option<Duration>,
}

fn sorted_prefixed_entries(dir: &Path, prefix: &str) -> Vec<(u32, PathBuf)> {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return Vec::new();
    };
    let mut items: Vec<(u32, PathBuf)> = entries
        .flatten()
        .filter_map(|entry| {
            let name = entry.file_name();
            let name_str = name.to_str()?;
            let suffix = name_str.strip_prefix(prefix)?;
            if suffix.is_empty() || !suffix.bytes().all(|b| b.is_ascii_digit()) {
                return None;
            }
            let idx = suffix.parse::<u32>().ok()?;
            Some((idx, entry.path()))
        })
        .collect();
    items.sort_unstable_by(|a, b| a.0.cmp(&b.0).then_with(|| a.1.cmp(&b.1)));
    items
}

fn read_temp_c(path: &Path) -> Option<f64> {
    let raw = std::fs::read_to_string(path).ok()?;
    let token = raw.lines().next()?.trim();
    let millideg = token.parse::<i64>().ok()?;
    let celsius = millideg as f64 / 1000.0;
    (-40.0..=150.0).contains(&celsius).then_some(celsius)
}

fn find_labeled_hwmon_input(hwmon_dir: &Path, wanted_label: &str) -> Option<PathBuf> {
    let Ok(entries) = std::fs::read_dir(hwmon_dir) else {
        return None;
    };
    let mut labels: Vec<(u32, PathBuf)> = entries
        .flatten()
        .filter_map(|entry| {
            let name = entry.file_name();
            let name_str = name.to_str()?;
            let mid = name_str.strip_prefix("temp")?.strip_suffix("_label")?;
            if mid.is_empty() || !mid.bytes().all(|b| b.is_ascii_digit()) {
                return None;
            }
            let idx = mid.parse::<u32>().ok()?;
            Some((idx, entry.path()))
        })
        .collect();
    labels.sort_unstable_by_key(|(idx, _)| *idx);

    for (idx, label_path) in labels {
        let Ok(label_txt) = std::fs::read_to_string(&label_path) else {
            continue;
        };
        if label_txt.trim() == wanted_label {
            let input_path = hwmon_dir.join(format!("temp{idx}_input"));
            if read_temp_c(&input_path).is_some() {
                return Some(input_path);
            }
        }
    }
    None
}

fn discover_cpu_temp_sensor_in(hwmon_root: &Path, thermal_root: &Path) -> Option<PathBuf> {
    let hwmons = sorted_prefixed_entries(hwmon_root, "hwmon");
    for wanted in ["coretemp", "k10temp", "zenpower", "cpu_thermal"] {
        for (_, dir) in &hwmons {
            let Ok(name) = std::fs::read_to_string(dir.join("name")) else {
                continue;
            };
            if name.trim() != wanted {
                continue;
            }
            if wanted == "coretemp"
                && let Some(pkg_input) = find_labeled_hwmon_input(dir, "Package id 0")
            {
                return Some(pkg_input);
            }
            if wanted == "k10temp"
                && let Some(tdie_input) = find_labeled_hwmon_input(dir, "Tdie")
            {
                return Some(tdie_input);
            }
            let temp1 = dir.join("temp1_input");
            if read_temp_c(&temp1).is_some() {
                return Some(temp1);
            }
        }
    }

    let zones = sorted_prefixed_entries(thermal_root, "thermal_zone");
    for (_, dir) in &zones {
        let Ok(zone_type) = std::fs::read_to_string(dir.join("type")) else {
            continue;
        };
        if zone_type.trim().contains("x86_pkg_temp") {
            let temp_path = dir.join("temp");
            if read_temp_c(&temp_path).is_some() {
                return Some(temp_path);
            }
        }
    }
    for (_, dir) in &zones {
        let Ok(zone_type) = std::fs::read_to_string(dir.join("type")) else {
            continue;
        };
        if zone_type.trim().to_ascii_lowercase().contains("cpu") {
            let temp_path = dir.join("temp");
            if read_temp_c(&temp_path).is_some() {
                return Some(temp_path);
            }
        }
    }

    None
}

fn cpu_temp_reading(
    hwmon_root: &Path,
    thermal_root: &Path,
    now: Option<Duration>,
    tracker: &mut CpuTempTracker,
) -> Option<f64> {
    if let Some(path) = tracker.sensor_path.as_ref() {
        if let Some(temp) = read_temp_c(path) {
            return Some(temp);
        }
        tracker.sensor_path = None;
    }

    let can_discover = match (now, tracker.last_discovery) {
        (Some(now), Some(last)) => now.saturating_sub(last) >= Duration::from_secs(5),
        (Some(_), None) => true,
        (None, _) => tracker.last_discovery.is_none(),
    };
    if !can_discover {
        return None;
    }

    tracker.last_discovery = now;
    let found = discover_cpu_temp_sensor_in(hwmon_root, thermal_root)?;
    let temp = read_temp_c(&found)?;
    tracker.sensor_path = Some(found);
    Some(temp)
}

fn sample_cpu_temp_c() -> Option<f64> {
    static TRACKER: std::sync::Mutex<Option<CpuTempTracker>> = std::sync::Mutex::new(None);
    let now = network_time();
    let mut guard = TRACKER.lock().unwrap_or_else(|e| e.into_inner());
    let tracker = guard.get_or_insert_with(CpuTempTracker::default);
    cpu_temp_reading(
        Path::new("/sys/class/hwmon"),
        Path::new("/sys/class/thermal"),
        now,
        tracker,
    )
}

fn format_iec_bytes(bytes: u64) -> String {
    const GIB: u64 = 1_073_741_824;
    const MIB: u64 = 1_048_576;
    if bytes >= GIB {
        format!("{:.1} GiB", bytes as f64 / GIB as f64)
    } else if bytes > 0 && bytes < MIB {
        format!("{:.1} MiB", bytes as f64 / MIB as f64)
    } else {
        format!("{:.0} MiB", bytes as f64 / MIB as f64)
    }
}

fn parse_zram_mm_stat(raw: &str) -> Option<(u64, u64, u64)> {
    let mut nums = raw.split_whitespace().filter_map(|t| t.parse::<u64>().ok());
    let orig = nums.next()?;
    let compr = nums.next()?;
    let mem_used = nums.next()?;
    Some((orig, compr, mem_used))
}

fn read_zram_mm_stats_in(block_dir: &Path) -> Option<(u64, u64, u64)> {
    let entries = std::fs::read_dir(block_dir).ok()?;
    let mut total_orig = 0u64;
    let mut total_compr = 0u64;
    let mut total_mem_used = 0u64;
    let mut found = false;

    for entry in entries.flatten() {
        let name = entry.file_name();
        let Some(name_str) = name.to_str() else {
            continue;
        };
        let Some(suffix) = name_str.strip_prefix("zram") else {
            continue;
        };
        if suffix.is_empty() || !suffix.bytes().all(|b| b.is_ascii_digit()) {
            continue;
        }
        let Ok(raw) = std::fs::read_to_string(entry.path().join("mm_stat")) else {
            continue;
        };
        if let Some((orig, compr, mem_used)) = parse_zram_mm_stat(&raw) {
            total_orig = total_orig.saturating_add(orig);
            total_compr = total_compr.saturating_add(compr);
            total_mem_used = total_mem_used.saturating_add(mem_used);
            found = true;
        }
    }

    found.then_some((total_orig, total_compr, total_mem_used))
}

fn format_swap_line(
    swap_total_kb: u64,
    swap_free_kb: u64,
    zram_stat: Option<(u64, u64, u64)>,
) -> String {
    if swap_total_kb == 0 {
        return match zram_stat {
            Some((orig, compr, mem_used)) if mem_used > 0 => {
                let phys_str = format_iec_bytes(mem_used);
                if compr > 0 {
                    let ratio = orig as f64 / compr as f64;
                    format!("Swap: Disabled\nZRAM: {phys_str} physical · {ratio:.1}:1 ratio")
                } else {
                    format!("Swap: Disabled\nZRAM: {phys_str} physical")
                }
            }
            _ => "Swap: Disabled".to_owned(),
        };
    }

    let swap_used_kb = swap_total_kb.saturating_sub(swap_free_kb);
    let used_str = format_iec_bytes(swap_used_kb.saturating_mul(1024));
    let total_str = format_iec_bytes(swap_total_kb.saturating_mul(1024));

    match zram_stat {
        Some((orig, compr, mem_used)) => {
            let phys_str = format_iec_bytes(mem_used);
            if compr > 0 {
                let ratio = orig as f64 / compr as f64;
                format!(
                    "Swap: {used_str} / {total_str}\nZRAM: {phys_str} physical · {ratio:.1}:1 ratio"
                )
            } else {
                format!("Swap: {used_str} / {total_str}\nZRAM: {phys_str} physical")
            }
        }
        None => format!("Swap: {used_str} / {total_str}"),
    }
}

fn parse_ram_state(meminfo: &str, zram_stat: Option<(u64, u64, u64)>) -> RamState {
    let (mut mem_total, mut mem_avail) = (0u64, 0u64);
    let (mut dirty, mut writeback) = (0u64, 0u64);
    let (mut swap_total, mut swap_free) = (0u64, 0u64);

    for line in meminfo.lines() {
        let Some((key, rest)) = line.split_once(':') else {
            continue;
        };
        let val = rest
            .split_whitespace()
            .next()
            .and_then(|x| x.parse::<u64>().ok())
            .unwrap_or(0);
        match key {
            "MemTotal" => mem_total = val,
            "MemAvailable" => mem_avail = val,
            "Dirty" => dirty = val,
            "Writeback" => writeback = val,
            "SwapTotal" => swap_total = val,
            "SwapFree" => swap_free = val,
            _ => {}
        }
    }

    if mem_total == 0 {
        return RamState::default();
    }

    let used_kb = mem_total.saturating_sub(mem_avail);
    let used_gib = used_kb as f64 / 1_048_576.0;
    let total_gib = mem_total as f64 / 1_048_576.0;
    let used_pct = (used_kb as f64 / mem_total as f64) * 100.0;

    let dirty_kb = dirty.saturating_add(writeback);
    let dirty_str = if dirty_kb >= 1_048_576 {
        format!("{:.2} GiB", dirty_kb as f64 / 1_048_576.0)
    } else {
        format!("{:.1} MiB", dirty_kb as f64 / 1024.0)
    };

    let swap_line = format_swap_line(swap_total, swap_free, zram_stat);

    RamState {
        text: format!("{used_gib:.1} GB"),
        tooltip: format!(
            "Memory: {used_gib:.1} GiB / {total_gib:.1} GiB ({used_pct:.0}%)\nDirty cache: {dirty_str}\n{swap_line}\nLMB: Open zramctl"
        ),
    }
}

fn parse_proc_stat_cpu(txt: &str) -> Option<(u64, u64)> {
    let line = txt.lines().next()?;
    let nums: Vec<u64> = line
        .split_whitespace()
        .skip(1)
        .take(8)
        .filter_map(|x| x.parse().ok())
        .collect();
    if nums.len() < 8 {
        return None;
    }
    let idle = nums[3] + nums[4];
    let total: u64 = nums.iter().sum();
    Some((idle, total))
}

fn parse_cpu_sample(raw: &str) -> Option<(u64, u64, Duration)> {
    let mut it = raw.split_whitespace().filter_map(|x| x.parse::<u64>().ok());
    let idle = it.next()?;
    let total = it.next()?;
    let at_ms = it.next()?;
    Some((idle, total, Duration::from_millis(at_ms)))
}

fn compute_cpu_usage_text(
    current: (u64, u64),
    now: Option<Duration>,
    previous: Option<(u64, u64, Duration)>,
) -> String {
    let (idle, total) = current;
    if let (Some(now), Some((pi, pt, prev_at))) = (now, previous)
        && let Some(elapsed) = now.checked_sub(prev_at)
        && (Duration::from_millis(500)..=Duration::from_secs(5)).contains(&elapsed)
        && idle >= pi
        && total > pt
    {
        let di = idle - pi;
        let dt = total - pt;
        let usage = 100.0 * (1.0 - di as f64 / dt as f64);
        return format!("{:.0}%", usage.clamp(0.0, 100.0));
    }
    "--".to_owned()
}

pub fn cpu_ram_state() -> (CpuState, RamState) {
    let mut cpu_text = "--".to_owned();
    if let Ok(first) = std::fs::read_to_string("/proc/stat")
        && let Some((idle, total)) = parse_proc_stat_cpu(&first)
    {
        // Store timestamped sample in tmpfs so deltas survive without SSD writes
        // and rebase cleanly after suspend or long idle intervals.
        let sample_file = runtime_dir().join("cpu_sample.txt");
        let now = network_time();
        let previous = std::fs::read_to_string(&sample_file)
            .ok()
            .and_then(|txt| parse_cpu_sample(&txt));
        cpu_text = compute_cpu_usage_text((idle, total), now, previous);
        if let Some(now) = now {
            let _ = atomic_write_tmpfs_text(
                &sample_file,
                &format!("{idle} {total} {}\n", now.as_millis() as u64),
            );
        }
    }

    let power_watts = sample_cpu_power_watts();
    let power_str = match power_watts {
        Some(w) => format!("{w:.1} W"),
        None => "N/A".to_owned(),
    };
    let temp_c = sample_cpu_temp_c();
    let temp_str = match temp_c {
        Some(t) => format!("{t:.0}°C"),
        None => "N/A".to_owned(),
    };
    let cpu = CpuState {
        tooltip: format!("Power: {power_str}\nTemp: {temp_str}\nLMB: Open btop"),
        text: cpu_text,
        power_watts,
        temp_c,
    };

    let ram = match std::fs::read_to_string("/proc/meminfo") {
        Ok(mem) => parse_ram_state(&mem, read_zram_mm_stats_in(Path::new("/sys/block"))),
        Err(_) => RamState::default(),
    };

    (cpu, ram)
}

#[allow(dead_code)]
pub fn cpu_ram() -> (String, String) {
    let (cpu, ram) = cpu_ram_state();
    (cpu.text, ram.text)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum WarpStatus {
    #[default]
    NotInstalled,
    Disconnected,
    Connected,
}

impl WarpStatus {
    pub fn is_installed(self) -> bool {
        !matches!(self, Self::NotInstalled)
    }

    pub fn is_connected(self) -> bool {
        matches!(self, Self::Connected)
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct NetState {
    pub text: String,
    pub tooltip: String,
    pub disconnected: bool,
    pub warp: WarpStatus,
    pub total_rx_bytes: u64,
    pub total_tx_bytes: u64,
}

fn format_net_bytes(bytes: u64) -> String {
    const KIB: u64 = 1_024;
    const MIB: u64 = 1_048_576;
    const GIB: u64 = 1_073_741_824;
    const TIB: u64 = 1_099_511_627_776;
    if bytes < KIB {
        format!("{bytes} B")
    } else if bytes < MIB {
        format!("{:.1} KiB", bytes as f64 / KIB as f64)
    } else if bytes < GIB {
        format!("{:.1} MiB", bytes as f64 / MIB as f64)
    } else if bytes < TIB {
        format!("{:.2} GiB", bytes as f64 / GIB as f64)
    } else {
        format!("{:.2} TiB", bytes as f64 / TIB as f64)
    }
}

#[derive(Clone, Debug)]
struct NetworkCounters {
    iface: String,
    ifindex: u32,
    carrier: Option<u64>,
    rx: u64,
    tx: u64,
}

struct NetworkSample {
    at: Duration,
    counters: Vec<NetworkCounters>,
}

fn read_network_counters(net_dev: &Path, devices: &Path) -> Option<Vec<NetworkCounters>> {
    // Aggregate physical links (including virtio), avoiding VPN/bridge duplication.
    // A failed sample must never be converted into valid zero counters.
    let mut counters = Vec::new();
    for entry in std::fs::read_dir(devices).ok()? {
        let entry = entry.ok()?;
        let path = entry.path();
        if !path.join("device").exists() {
            continue;
        }
        let state = std::fs::read_to_string(path.join("operstate")).ok()?;
        if !matches!(state.trim(), "up" | "unknown") {
            continue;
        }
        counters.push(NetworkCounters {
            iface: entry.file_name().into_string().ok()?,
            ifindex: std::fs::read_to_string(path.join("ifindex"))
                .ok()?
                .trim()
                .parse()
                .ok()?,
            carrier: std::fs::read_to_string(path.join("carrier_up_count"))
                .ok()
                .and_then(|value| value.trim().parse().ok()),
            rx: 0,
            tx: 0,
        });
    }
    counters.sort_unstable_by(|a, b| a.iface.cmp(&b.iface));
    // No active links: return immediately without reading the counters file.
    if counters.is_empty() {
        return Some(counters);
    }
    let text = std::fs::read_to_string(net_dev).ok()?;
    for counter in &mut counters {
        let fields = text.lines().find_map(|line| {
            let (name, values) = line.rsplit_once(':')?;
            (name.trim() == counter.iface).then_some(values)
        })?;
        let mut fields = fields.split_whitespace();
        counter.rx = fields.next()?.parse().ok()?;
        counter.tx = fields.nth(7)?.parse().ok()?;
    }
    Some(counters)
}

fn network_time() -> Option<Duration> {
    // CLOCK_BOOTTIME includes suspend and does not jump when wall time is set.
    let mut time = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // SAFETY: time is writable, correctly sized storage for the Linux API.
    if unsafe { libc::clock_gettime(libc::CLOCK_BOOTTIME, &mut time) } != 0 {
        return None;
    }
    Some(Duration::new(
        time.tv_sec.try_into().ok()?,
        time.tv_nsec.try_into().ok()?,
    ))
}

fn is_warp_cli_installed(net_dir: &Path, now: Option<Duration>) -> bool {
    if access_ok(Path::new("/usr/bin/warp-cli"), libc::X_OK)
        || access_ok(Path::new("/usr/local/bin/warp-cli"), libc::X_OK)
    {
        return true;
    }

    static CACHE: std::sync::Mutex<Option<(bool, Duration)>> = std::sync::Mutex::new(None);
    let warp_iface_present = net_dir.join("CloudflareWARP").exists();
    let mut guard = CACHE.lock().unwrap_or_else(|e| e.into_inner());

    if !warp_iface_present
        && let Some((cached, checked_at)) = *guard
        && let Some(now) = now
        && now.saturating_sub(checked_at) < Duration::from_secs(30)
    {
        return cached;
    }

    let found = which("warp-cli").is_some();
    *guard = now.map(|t| (found, t));
    found
}

fn detect_warp_connected(net_dir: &Path) -> bool {
    let warp_dir = net_dir.join("CloudflareWARP");
    let Ok(operstate) = std::fs::read_to_string(warp_dir.join("operstate")) else {
        return false;
    };
    if !matches!(operstate.trim(), "up" | "unknown") {
        return false;
    }
    let Ok(flags_txt) = std::fs::read_to_string(warp_dir.join("flags")) else {
        return false;
    };
    let trimmed = flags_txt.trim();
    let hex = trimmed
        .strip_prefix("0x")
        .or_else(|| trimmed.strip_prefix("0X"))
        .unwrap_or(trimmed);
    let Ok(flags) = u32::from_str_radix(hex, 16) else {
        return false;
    };
    // IFF_UP (0x1) confirms the TUN interface is administratively active.
    (flags & 0x1) != 0
}

fn detect_warp_status_in(net_dir: &Path, warp_installed: bool) -> WarpStatus {
    if !warp_installed {
        WarpStatus::NotInstalled
    } else if detect_warp_connected(net_dir) {
        WarpStatus::Connected
    } else {
        WarpStatus::Disconnected
    }
}

pub fn append_warp_status(tooltip: &mut String, warp: WarpStatus) {
    match warp {
        WarpStatus::NotInstalled => {}
        WarpStatus::Connected => tooltip.push_str("\nCloudflare WARP: Connected (ON)"),
        WarpStatus::Disconnected => tooltip.push_str("\nCloudflare WARP: Disconnected (OFF)"),
    }
}

fn network_reading(
    counters: Option<Vec<NetworkCounters>>,
    now: Option<Duration>,
    warp: WarpStatus,
    previous: &mut Option<NetworkSample>,
) -> NetState {
    let Some((counters, now)) = counters.zip(now) else {
        *previous = None;
        let mut tooltip = "Network reading unavailable".to_owned();
        append_warp_status(&mut tooltip, warp);
        return NetState {
            text: "--".into(),
            tooltip,
            disconnected: false,
            warp,
            total_rx_bytes: 0,
            total_tx_bytes: 0,
        };
    };
    if counters.is_empty() {
        *previous = None;
        let mut tooltip = "Disconnected".to_owned();
        append_warp_status(&mut tooltip, warp);
        return NetState {
            text: "--".into(),
            tooltip,
            disconnected: true,
            warp,
            total_rx_bytes: 0,
            total_tx_bytes: 0,
        };
    }
    let (total_rx_u128, total_tx_u128) = counters.iter().fold((0u128, 0u128), |(rx, tx), c| {
        (rx + u128::from(c.rx), tx + u128::from(c.tx))
    });
    let total_rx_bytes = total_rx_u128.min(u128::from(u64::MAX)) as u64;
    let total_tx_bytes = total_tx_u128.min(u128::from(u64::MAX)) as u64;
    let (rx, tx) = previous
        .as_ref()
        .and_then(|sample| {
            let elapsed = now.checked_sub(sample.at)?;
            // Tray refreshes every two seconds. Rebase after a stall/suspend, an
            // unusually early sample, any link change, or any per-interface reset.
            if !(Duration::from_millis(500)..=Duration::from_secs(5)).contains(&elapsed)
                || sample.counters.len() != counters.len()
                || !sample.counters.iter().zip(&counters).all(|(old, new)| {
                    old.iface == new.iface
                        && old.ifindex == new.ifindex
                        && old.carrier == new.carrier
                        && new.rx >= old.rx
                        && new.tx >= old.tx
                })
            {
                return None;
            }
            let (rx, tx) = sample.counters.iter().zip(&counters).fold(
                (0u128, 0u128),
                |(rx, tx), (old, new)| {
                    (
                        rx + u128::from(new.rx - old.rx),
                        tx + u128::from(new.tx - old.tx),
                    )
                },
            );
            Some((
                rx as f64 / elapsed.as_secs_f64(),
                tx as f64 / elapsed.as_secs_f64(),
            ))
        })
        .unwrap_or((0.0, 0.0));
    *previous = Some(NetworkSample { at: now, counters });
    let (scale, unit) = if rx.max(tx) >= 1_048_576.0 {
        (1_048_576.0, "MB")
    } else {
        (1024.0, "KB")
    };
    let mut tooltip = format!(
        "Upload: {} (session)\nDownload: {} (session)",
        format_net_bytes(total_tx_bytes),
        format_net_bytes(total_rx_bytes),
    );
    append_warp_status(&mut tooltip, warp);
    NetState {
        text: format!("{:.0} {unit} {:.0}", tx / scale, rx / scale),
        tooltip,
        disconnected: false,
        warp,
        total_rx_bytes,
        total_tx_bytes,
    }
}

pub fn net_state() -> NetState {
    static SAMPLE: std::sync::Mutex<Option<NetworkSample>> = std::sync::Mutex::new(None);
    let net_dir = Path::new("/sys/class/net");
    let counters = read_network_counters(Path::new("/proc/net/dev"), net_dir);
    let now = network_time();
    let warp = detect_warp_status_in(net_dir, is_warp_cli_installed(net_dir, now));
    let mut sample = SAMPLE.lock().unwrap_or_else(|e| e.into_inner());
    network_reading(counters, now, warp, &mut sample)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum BatteryStatus {
    #[default]
    Unknown,
    Discharging,
    Charging,
    Full,
    NotCharging,
}

impl BatteryStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Unknown => "Unknown",
            Self::Discharging => "Discharging",
            Self::Charging => "Charging",
            Self::Full => "Full",
            Self::NotCharging => "Not charging",
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct BatteryState {
    pub present: bool,
    pub status: BatteryStatus,
    pub watts: f64,
    pub text: String,
    pub tooltip: String,
}

impl BatteryState {
    pub fn is_discharging(&self) -> bool {
        self.status == BatteryStatus::Discharging
    }

    #[allow(dead_code)]
    pub fn is_charging(&self) -> bool {
        self.status == BatteryStatus::Charging
    }
}

fn read_sysfs_u64(path: &Path, magnitude: bool) -> Option<u64> {
    let raw = std::fs::read_to_string(path).ok()?;
    let token = raw.lines().next().unwrap_or("").trim();
    let digits = if magnitude {
        token.strip_prefix('-').unwrap_or(token)
    } else {
        token
    };
    if digits.is_empty() || digits.len() > 18 || !digits.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    digits.parse::<u64>().ok()
}

pub fn format_watts(power_uw: u64) -> String {
    // Convert µW to centiwatts with half-up rounding, capped at 999.99W (max 7 chars).
    let cw = (power_uw.saturating_add(5_000) / 10_000).min(99_999);
    format!("{}.{:02}W", cw / 100, cw % 100)
}

pub fn format_battery_time(secs: u64) -> String {
    let mut hrs = secs / 3600;
    let mut mins = (secs % 3600 + 30) / 60;
    if mins >= 60 {
        hrs += 1;
        mins -= 60;
    }
    format!("{hrs}h {mins}m")
}

pub fn read_battery_state(power_supply: &Path) -> BatteryState {
    let Ok(entries) = std::fs::read_dir(power_supply) else {
        return BatteryState {
            present: false,
            status: BatteryStatus::Unknown,
            watts: 0.0,
            text: "--".into(),
            tooltip: "No battery present".into(),
        };
    };

    let mut nodes: Vec<PathBuf> = entries.flatten().map(|e| e.path()).collect();
    nodes.sort();

    let mut n = 0usize;
    let mut csum = 0u64;
    let mut ccnt = 0usize;
    let mut esum = 0u64;
    let mut ecnt = 0usize;
    let mut efsum = 0u64;
    let mut efcnt = 0usize;
    let mut psum = 0u64;
    let mut pcnt = 0usize;
    let mut net = 0i128;
    let mut charging = false;
    let mut discharging = false;
    let mut time_ok = true;
    let mut state = BatteryStatus::Unknown;

    for path in nodes {
        let Ok(kind) = std::fs::read_to_string(path.join("type")) else {
            continue;
        };
        if kind.trim() != "Battery" {
            continue;
        }
        if let Ok(scope) = std::fs::read_to_string(path.join("scope"))
            && scope.trim().eq_ignore_ascii_case("Device")
        {
            continue;
        }
        if read_sysfs_u64(&path.join("present"), false) == Some(0) {
            continue;
        }

        n += 1;

        let status = std::fs::read_to_string(path.join("status"))
            .ok()
            .and_then(|s| s.lines().next().map(|l| l.trim().to_owned()))
            .map(|s| match s.as_str() {
                "Discharging" => BatteryStatus::Discharging,
                "Charging" => BatteryStatus::Charging,
                "Full" => BatteryStatus::Full,
                "Not charging" => BatteryStatus::NotCharging,
                _ => BatteryStatus::Unknown,
            })
            .unwrap_or(BatteryStatus::Unknown);

        let volt = [
            "voltage_now",
            "voltage_avg",
            "voltage_min_design",
            "voltage_max_design",
        ]
        .into_iter()
        .find_map(|f| read_sysfs_u64(&path.join(f), false).filter(|&v| v > 0));

        // Normalise energy to µWh using matching now/full attributes (zero is valid).
        let (enow_uwh, efull_uwh) =
            if let Some(enow) = read_sysfs_u64(&path.join("energy_now"), false) {
                let efull = ["energy_full", "energy_full_design"]
                    .into_iter()
                    .find_map(|f| read_sysfs_u64(&path.join(f), false).filter(|&v| v > 0));
                (Some(enow), efull)
            } else if let Some(qnow) = read_sysfs_u64(&path.join("charge_now"), false) {
                let enow = volt.map(|v| ((qnow as u128 * v as u128) / 1_000_000) as u64);
                let efull = volt.and_then(|v| {
                    ["charge_full", "charge_full_design"]
                        .into_iter()
                        .find_map(|f| read_sysfs_u64(&path.join(f), false).filter(|&q| q > 0))
                        .map(|qfull| ((qfull as u128 * v as u128) / 1_000_000) as u64)
                });
                (enow, efull)
            } else {
                (None, None)
            };

        if let Some(e) = enow_uwh {
            esum = esum.saturating_add(e);
            ecnt += 1;
        }
        if let Some(ef) = efull_uwh.filter(|&ef| ef > 0) {
            efsum = efsum.saturating_add(ef);
            efcnt += 1;
        }

        let cap = read_sysfs_u64(&path.join("capacity"), false)
            .filter(|&c| c <= 100)
            .or_else(|| {
                let enow = enow_uwh?;
                let efull = efull_uwh.filter(|&ef| ef > 0)?;
                Some(((enow as u128 * 100 + (efull as u128 / 2)) / efull as u128) as u64)
            })
            .map(|c| c.min(100));

        if let Some(c) = cap {
            csum = csum.saturating_add(c);
            ccnt += 1;
        }

        // Keep an instantaneous zero instead of substituting a stale average.
        let mut power_uw = ["power_now", "power_avg"]
            .into_iter()
            .find_map(|f| read_sysfs_u64(&path.join(f), true));

        if power_uw.is_none()
            && let Some(v) = volt
        {
            power_uw = ["current_now", "current_avg"]
                .into_iter()
                .find_map(|f| read_sysfs_u64(&path.join(f), true))
                .map(|curr| ((curr as u128 * v as u128) / 1_000_000) as u64);
        }

        if let Some(p) = power_uw {
            psum = psum.saturating_add(p);
            pcnt += 1;
            match status {
                BatteryStatus::Charging => net += p as i128,
                BatteryStatus::Discharging => net -= p as i128,
                _ => {}
            }
        }

        match status {
            BatteryStatus::Charging => {
                charging = true;
                state = BatteryStatus::Charging;
            }
            BatteryStatus::Discharging => {
                discharging = true;
                if state != BatteryStatus::Charging {
                    state = BatteryStatus::Discharging;
                }
            }
            BatteryStatus::NotCharging => {
                if !matches!(state, BatteryStatus::Charging | BatteryStatus::Discharging) {
                    state = BatteryStatus::NotCharging;
                }
            }
            BatteryStatus::Full => {
                if !matches!(
                    state,
                    BatteryStatus::Charging
                        | BatteryStatus::Discharging
                        | BatteryStatus::NotCharging
                ) {
                    state = BatteryStatus::Full;
                }
            }
            BatteryStatus::Unknown => {
                time_ok = false;
            }
        }
    }

    if n == 0 {
        return BatteryState {
            present: false,
            status: BatteryStatus::Unknown,
            watts: 0.0,
            text: "--".into(),
            tooltip: "No battery present".into(),
        };
    }

    if charging && discharging {
        if pcnt == n {
            if net > 0 {
                state = BatteryStatus::Charging;
            } else if net < 0 {
                state = BatteryStatus::Discharging;
            } else {
                state = BatteryStatus::Unknown;
                time_ok = false;
            }
            psum = net.unsigned_abs() as u64;
        } else {
            state = BatteryStatus::Unknown;
            time_ok = false;
        }
    }

    let (watts, text) = if pcnt > 0 {
        (psum as f64 / 1_000_000.0, format_watts(psum))
    } else if matches!(state, BatteryStatus::Full | BatteryStatus::NotCharging) {
        (0.0, format_watts(0))
    } else {
        (0.0, "--".into())
    };

    let estimated_secs = if time_ok && pcnt == n && ecnt == n && psum > 0 {
        match state {
            BatteryStatus::Discharging => Some(((esum as u128 * 3600) / psum as u128) as u64),
            BatteryStatus::Charging if efcnt == n => {
                let rem_uwh = efsum.saturating_sub(esum);
                Some(((rem_uwh as u128 * 3600) / psum as u128) as u64)
            }
            _ => None,
        }
    } else {
        None
    };

    let remaining = if let Some(secs) = estimated_secs {
        format_battery_time(secs)
    } else {
        match state {
            BatteryStatus::Full => "Full".into(),
            BatteryStatus::NotCharging => "Held".into(),
            _ => "N/A".into(),
        }
    };

    let battery_pct = if n == 1 && ccnt == 1 {
        Some(csum)
    } else if ecnt == n && efcnt == n && efsum > 0 {
        Some((((esum as u128 * 100) + (efsum as u128 / 2)) / efsum as u128).min(100) as u64)
    } else if ccnt == n {
        Some((((csum as u128) + (ccnt as u128 / 2)) / ccnt as u128).min(100) as u64)
    } else {
        None
    };

    let battery_header = match battery_pct {
        Some(pct) => format!("Battery: {pct}%"),
        None => format!("Battery: {}", state.as_str()),
    };

    BatteryState {
        present: true,
        status: state,
        watts,
        tooltip: format!("{battery_header}\nRemaining: {remaining}"),
        text,
    }
}

pub fn battery_state() -> BatteryState {
    read_battery_state(Path::new("/sys/class/power_supply"))
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WeatherState {
    pub temperature: String,
    pub condition: Option<String>,
    pub temp_max: Option<String>,
    pub temp_min: Option<String>,
    pub precip_chance: Option<String>,
    pub location: Option<String>,
    pub offline_note: Option<String>,
}

impl WeatherState {
    pub fn is_offline(&self) -> bool {
        self.offline_note.is_some()
    }
}

#[derive(Debug, Clone, PartialEq)]
struct WeatherRequestKey {
    source: String,
    unit_pref: String,
    lat: Option<f64>,
    lon: Option<f64>,
}

#[derive(Debug, Clone)]
struct WeatherRecord {
    state: WeatherState,
    saved_at: Option<f64>,
    request_key: Option<WeatherRequestKey>,
}

#[derive(Debug, Clone)]
struct WeatherCache {
    stamp: FileStamp,
    record: Option<WeatherRecord>,
}

const WEATHER_TTL_SECS: f64 = 3540.0;
const WEATHER_BACKOFF_SECS: f64 = 45.0;

fn strip_pango(input: &str) -> String {
    let mut out = String::with_capacity(input.len());
    let mut in_tag = false;
    for ch in input.chars() {
        match ch {
            '<' => in_tag = true,
            '>' => in_tag = false,
            _ if !in_tag => out.push(ch),
            _ => {}
        }
    }
    if !out.contains('&') {
        return out;
    }
    out.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", "\"")
        .replace("&apos;", "'")
        .replace("&#39;", "'")
        .replace("&amp;", "&")
}

fn is_signed_number(s: &str) -> bool {
    let s = s.strip_prefix(['-', '+', '−']).unwrap_or(s);
    if s.is_empty() {
        return false;
    }
    let mut saw_digit = false;
    let mut saw_dot = false;
    for b in s.bytes() {
        if b.is_ascii_digit() {
            saw_digit = true;
        } else if b == b'.' && !saw_dot {
            saw_dot = true;
        } else {
            return false;
        }
    }
    saw_digit
}

fn is_temp_token(token: &str) -> bool {
    let Some(deg_pos) = token.find('°') else {
        return false;
    };
    let num = &token[..deg_pos];
    let unit = &token[deg_pos + '°'.len_utf8()..];
    (unit.is_empty() || matches!(unit, "C" | "F" | "c" | "f")) && is_signed_number(num)
}

fn is_percent_token(token: &str) -> bool {
    let Some(num) = token.strip_suffix('%') else {
        return false;
    };
    !num.starts_with(['-', '+', '−']) && is_signed_number(num)
}

fn is_symbol_token(token: &str) -> bool {
    !token.is_empty() && !token.chars().any(|c| c.is_ascii_alphanumeric())
}

fn take_temp(tokens: &[&str], idx: usize) -> Option<(String, usize)> {
    let tok = *tokens.get(idx)?;
    if is_temp_token(tok) {
        return Some((tok.to_owned(), 1));
    }
    if let Some(&next) = tokens.get(idx + 1)
        && is_signed_number(tok)
        && matches!(next, "°C" | "°F" | "°c" | "°f" | "°")
    {
        return Some((format!("{tok}{next}"), 2));
    }
    None
}

fn extract_temp(raw: &str) -> Option<String> {
    let tokens: Vec<&str> = raw.split_whitespace().collect();
    let mut plain_num = None;

    for (i, &tok) in tokens.iter().enumerate() {
        if let Some((t, _used)) = take_temp(&tokens, i) {
            return Some(t);
        }
        if plain_num.is_none() && is_signed_number(tok) {
            plain_num = Some(tok.to_owned());
        }
    }

    plain_num
}

fn tokenize_metrics(line: &str) -> Vec<&str> {
    let mut out = Vec::new();
    for raw in line.split_whitespace() {
        if let Some(first) = raw.chars().next()
            && matches!(first, '' | '' | '')
        {
            let split = first.len_utf8();
            out.push(&raw[..split]);
            if split < raw.len() {
                out.push(&raw[split..]);
            }
        } else {
            out.push(raw);
        }
    }
    out
}

fn is_metrics_line(line: &str) -> bool {
    if line.contains(['', '', '']) {
        return true;
    }
    let tokens = tokenize_metrics(line);
    let mut temps = 0usize;
    let mut percents = 0usize;
    let mut i = 0;
    while i < tokens.len() {
        if let Some((_, used)) = take_temp(&tokens, i) {
            temps += 1;
            i += used;
            continue;
        }
        if is_percent_token(tokens[i]) {
            percents += 1;
        }
        i += 1;
    }
    temps >= 2 || (temps >= 1 && percents >= 1)
}

fn parse_metrics_line(line: &str) -> (Option<String>, Option<String>, Option<String>) {
    let tokens = tokenize_metrics(line);
    let mut temp_max = None;
    let mut temp_min = None;
    let mut precip = None;

    let mut i = 0;
    while i < tokens.len() {
        match tokens[i] {
            "" => {
                if let Some((t, used)) = take_temp(&tokens, i + 1) {
                    temp_max = Some(t);
                    i += 1 + used;
                    continue;
                }
            }
            "" => {
                if let Some((t, used)) = take_temp(&tokens, i + 1) {
                    temp_min = Some(t);
                    i += 1 + used;
                    continue;
                }
            }
            "" => {
                if let Some(&next) = tokens.get(i + 1)
                    && is_percent_token(next)
                {
                    precip = Some(next.to_owned());
                    i += 2;
                    continue;
                }
            }
            _ => {
                if let Some((t, used)) = take_temp(&tokens, i) {
                    if temp_max.is_none() {
                        temp_max = Some(t);
                    } else if temp_min.is_none() {
                        temp_min = Some(t);
                    }
                    i += used;
                    continue;
                }
                if is_percent_token(tokens[i]) && precip.is_none() {
                    precip = Some(tokens[i].to_owned());
                }
            }
        }
        i += 1;
    }

    (temp_max, temp_min, precip)
}

fn parse_weather_request_key(raw: &serde_json::Value) -> Option<WeatherRequestKey> {
    let source = raw.get("source")?.as_str()?;
    let unit_pref = raw.get("unit_pref")?.as_str()?;
    if !matches!(source, "manual" | "ip") || !matches!(unit_pref, "auto" | "celsius" | "fahrenheit")
    {
        return None;
    }
    if source == "manual" {
        let lat = raw.get("lat")?.as_f64()?;
        let lon = raw.get("lon")?.as_f64()?;
        if !lat.is_finite()
            || !lon.is_finite()
            || !(-90.0..=90.0).contains(&lat)
            || !(-180.0..=180.0).contains(&lon)
        {
            return None;
        }
        return Some(WeatherRequestKey {
            source: source.to_owned(),
            unit_pref: unit_pref.to_owned(),
            lat: Some(if lat == 0.0 { 0.0 } else { lat }),
            lon: Some(if lon == 0.0 { 0.0 } else { lon }),
        });
    }
    Some(WeatherRequestKey {
        source: source.to_owned(),
        unit_pref: unit_pref.to_owned(),
        lat: None,
        lon: None,
    })
}

fn weather_request_args(key: Option<&WeatherRequestKey>) -> Vec<String> {
    let mut args = Vec::new();
    let Some(key) = key else {
        return args;
    };
    if key.source == "manual"
        && let (Some(lat), Some(lon)) = (key.lat, key.lon)
    {
        args.push("--lat".to_owned());
        args.push(lat.to_string());
        args.push("--lon".to_owned());
        args.push(lon.to_string());
    }
    match key.unit_pref.as_str() {
        "celsius" => args.push("-c".to_owned()),
        "fahrenheit" => args.push("-f".to_owned()),
        _ => {}
    }
    args
}

fn format_weather_age(age_secs: f64) -> String {
    let secs = if age_secs.is_finite() && age_secs > 0.0 {
        age_secs as u64
    } else {
        0
    };
    if secs < 60 {
        "<1m".into()
    } else if secs < 3600 {
        format!("{}m", secs / 60)
    } else if secs < 86_400 {
        let hours = secs / 3600;
        let mins = (secs % 3600) / 60;
        if mins > 0 {
            format!("{hours}h {mins}m")
        } else {
            format!("{hours}h")
        }
    } else {
        let days = secs / 86_400;
        let hours = (secs % 86_400) / 3600;
        if hours > 0 {
            format!("{days}d {hours}h")
        } else {
            format!("{days}d")
        }
    }
}

fn is_weather_fresh(saved_at: Option<f64>, now_unix: f64) -> bool {
    saved_at.is_some_and(|ts| {
        let age = now_unix - ts;
        (0.0..WEATHER_TTL_SECS).contains(&age)
    })
}

fn weather_runtime_dir() -> PathBuf {
    if let Some(xdg) = std::env::var_os("XDG_RUNTIME_DIR")
        .filter(|p| !p.is_empty())
        .map(PathBuf::from)
        .filter(|p| p.is_absolute())
    {
        return xdg;
    }
    std::env::temp_dir().join(format!("dusky-waybar-weather-{}", unsafe {
        libc::getuid()
    }))
}

fn is_weather_lock_held(lock_path: &Path) -> bool {
    let Ok(file) = std::fs::File::open(lock_path) else {
        return false;
    };
    matches!(file.try_lock(), Err(std::fs::TryLockError::WouldBlock))
}

fn is_weather_backoff_active(backoff_path: &Path, now_boot: Option<Duration>) -> bool {
    let Some(now_boot) = now_boot else {
        return false;
    };
    let Ok(raw) = std::fs::read_to_string(backoff_path) else {
        return false;
    };
    let Ok(v) = serde_json::from_str::<serde_json::Value>(&raw) else {
        return false;
    };
    let Some(failed_at) = v.get("failed_at").and_then(|x| x.as_f64()) else {
        return false;
    };
    if !failed_at.is_finite() {
        return false;
    }
    let elapsed = now_boot.as_secs_f64() - failed_at;
    (0.0..WEATHER_BACKOFF_SECS).contains(&elapsed)
}

fn parse_weather_record(raw_json: &str) -> Option<WeatherRecord> {
    let root: serde_json::Value = serde_json::from_str(raw_json).ok()?;
    if root.get("version").and_then(|v| v.as_i64()) != Some(2) {
        return None;
    }
    let data = root.get("payload")?;

    let text_temp = data
        .get("text")
        .and_then(|v| v.as_str())
        .and_then(extract_temp);

    let mut hero_temp = None;
    let mut condition = None;
    let mut temp_max = None;
    let mut temp_min = None;
    let mut precip_chance = None;
    let mut offline_note = None;

    if let Some(raw_tooltip) = data.get("tooltip").and_then(|v| v.as_str()) {
        let cleaned = strip_pango(raw_tooltip);
        for raw_line in cleaned.lines() {
            let line = raw_line.trim();
            if line.is_empty() {
                continue;
            }
            if line.starts_with('⚠') || line.to_ascii_lowercase().contains("offline") {
                let note = line.trim_start_matches('⚠').trim();
                offline_note = Some(if note.is_empty() {
                    "Offline — cached data".to_owned()
                } else {
                    note.to_owned()
                });
                continue;
            }
            if is_metrics_line(line) {
                let (hi, lo, rain) = parse_metrics_line(line);
                if hi.is_some() {
                    temp_max = hi;
                }
                if lo.is_some() {
                    temp_min = lo;
                }
                if rain.is_some() {
                    precip_chance = rain;
                }
                continue;
            }
            if hero_temp.is_none() {
                let tokens: Vec<&str> = line.split_whitespace().collect();
                if let Some((t, used)) = take_temp(&tokens, 0)
                    && used == tokens.len()
                {
                    hero_temp = Some(t);
                    continue;
                }
            }
            if condition.is_none() {
                let mut parts = line.splitn(2, char::is_whitespace);
                let first = parts.next().unwrap_or("");
                let rest = parts.next().map(str::trim).unwrap_or("");
                if is_symbol_token(first) && !rest.is_empty() {
                    condition = Some(rest.to_owned());
                } else {
                    condition = Some(line.to_owned());
                }
            }
        }
    }

    let temperature = hero_temp.or(text_temp)?;

    let class_offline = match data.get("class") {
        Some(serde_json::Value::String(s)) => {
            let lower = s.to_ascii_lowercase();
            lower.contains("offline") || lower.contains("stale")
        }
        Some(serde_json::Value::Array(arr)) => arr.iter().any(|v| {
            v.as_str().is_some_and(|s| {
                let lower = s.to_ascii_lowercase();
                lower.contains("offline") || lower.contains("stale")
            })
        }),
        _ => false,
    };
    if offline_note.is_none() && class_offline {
        offline_note = Some("Offline — cached data".to_owned());
    }

    let location = root
        .get("city")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .or_else(|| {
            data.get("alt")
                .and_then(|v| v.as_str())
                .map(str::trim)
                .filter(|s| !s.is_empty())
        })
        .map(str::to_owned);

    let saved_at = root
        .get("saved_at")
        .and_then(|v| v.as_f64())
        .filter(|v| v.is_finite());
    let request_key = root.get("request_key").and_then(parse_weather_request_key);

    Some(WeatherRecord {
        state: WeatherState {
            temperature,
            condition,
            temp_max,
            temp_min,
            precip_chance,
            location,
            offline_note,
        },
        saved_at,
        request_key,
    })
}

#[cfg(test)]
fn parse_weather_json(raw_json: &str) -> Option<WeatherState> {
    parse_weather_record(raw_json).map(|r| r.state)
}

fn weather_record_from_path(
    path: &Path,
    cache: &mut Option<WeatherCache>,
) -> Option<WeatherRecord> {
    let Some(stamp) = FileStamp::from_file(path) else {
        *cache = None;
        return None;
    };
    if let Some(cached) = cache.as_ref()
        && cached.stamp == stamp
    {
        return cached.record.clone();
    }
    let mut record = std::fs::read_to_string(path)
        .ok()
        .and_then(|raw| parse_weather_record(&raw));
    if let Some(rec) = record.as_mut()
        && rec.saved_at.is_none()
    {
        rec.saved_at = std::fs::metadata(path)
            .ok()
            .and_then(|m| m.modified().ok())
            .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
            .map(|d| d.as_secs_f64());
    }
    *cache = Some(WeatherCache {
        stamp,
        record: record.clone(),
    });
    record
}

#[cfg(test)]
fn weather_state_from_path(path: &Path, cache: &mut Option<WeatherCache>) -> Option<WeatherState> {
    weather_record_from_path(path, cache).map(|r| r.state)
}

fn resolve_python3_with(from_path: Option<String>, local_override: &Path) -> String {
    if let Some(ref found) = from_path
        && found != "/usr/bin/python3"
    {
        return found.clone();
    }
    if local_override.is_file() && access_ok(local_override, libc::X_OK) {
        return local_override.to_string_lossy().into_owned();
    }
    from_path.unwrap_or_else(|| "/usr/bin/python3".to_owned())
}

fn resolve_python3() -> String {
    resolve_python3_with(which("python3"), Path::new("/usr/local/bin/python3"))
}

pub fn weather_state() -> Option<WeatherState> {
    static PATH: std::sync::OnceLock<PathBuf> = std::sync::OnceLock::new();
    static SCRIPT: std::sync::OnceLock<Option<PathBuf>> = std::sync::OnceLock::new();
    static CACHE: std::sync::Mutex<Option<WeatherCache>> = std::sync::Mutex::new(None);
    static LAST_SPAWN: std::sync::Mutex<Option<Duration>> = std::sync::Mutex::new(None);

    let path = PATH.get_or_init(|| xdg_config_dir().join("dusky/settings/waybar_weather"));
    let mut record = {
        let mut guard = CACHE.lock().unwrap_or_else(|e| e.into_inner());
        weather_record_from_path(path, &mut guard)
    };

    let now_unix = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);

    let stale = match record.as_ref() {
        Some(rec) => !is_weather_fresh(rec.saved_at, now_unix),
        None => true,
    };

    if stale {
        let now_boot = network_time();
        let rt_dir = weather_runtime_dir();
        let lock_held = is_weather_lock_held(&rt_dir.join("dusky-waybar-weather.lock"));
        let backoff_active =
            is_weather_backoff_active(&rt_dir.join("dusky-waybar-weather.backoff"), now_boot);

        let script = SCRIPT
            .get_or_init(|| {
                std::env::var_os("HOME")
                    .filter(|h| !h.is_empty())
                    .map(|h| PathBuf::from(h).join("user_scripts/waybar/weather.py"))
            })
            .as_ref()
            .filter(|p| p.is_file());

        let mut spawn_guard = LAST_SPAWN.lock().unwrap_or_else(|e| e.into_inner());
        let in_spawn_cooldown = match (*spawn_guard, now_boot) {
            (Some(last), Some(now)) => {
                now.saturating_sub(last) < Duration::from_secs(WEATHER_BACKOFF_SECS as u64)
            }
            _ => false,
        };

        let mut refreshing = lock_held || (in_spawn_cooldown && !backoff_active);
        if !lock_held
            && !backoff_active
            && !in_spawn_cooldown
            && let Some(script_path) = script
        {
            let mut argv = vec![
                resolve_python3(),
                script_path.to_string_lossy().into_owned(),
            ];
            argv.extend(weather_request_args(
                record.as_ref().and_then(|r| r.request_key.as_ref()),
            ));
            let xdg_config = std::env::var("XDG_CONFIG_HOME")
                .ok()
                .filter(|v| !v.is_empty());
            let xdg_runtime = std::env::var("XDG_RUNTIME_DIR")
                .ok()
                .filter(|v| !v.is_empty());
            let mut extra_env: Vec<(&str, &str)> = Vec::new();
            if let Some(ref v) = xdg_config {
                extra_env.push(("XDG_CONFIG_HOME", v));
            }
            if let Some(ref v) = xdg_runtime {
                extra_env.push(("XDG_RUNTIME_DIR", v));
            }
            if spawn_detached_service(&argv, &extra_env) {
                *spawn_guard = now_boot;
                refreshing = true;
            }
        }
        drop(spawn_guard);

        if let Some(rec) = record.as_mut()
            && rec.state.offline_note.is_none()
            && let Some(saved_at) = rec.saved_at
        {
            let age = now_unix - saved_at;
            let age_str = if age >= 0.0 {
                format!("cached {} ago", format_weather_age(age))
            } else {
                "cached data".to_owned()
            };
            if backoff_active || script.is_none() {
                rec.state.offline_note = Some(format!("Offline — {age_str}"));
            } else if refreshing {
                rec.state.offline_note = Some(format!("Updating — {age_str}"));
            }
        }
    }

    record.map(|r| r.state)
}

#[derive(Debug, Clone, Default)]
pub struct UpdatesState {
    pub badge: String,
    pub tooltip: String,
}

fn parse_updates(cached: &str, behind: &str) -> UpdatesState {
    let raw: serde_json::Value = serde_json::from_str(cached).unwrap_or_default();
    let count = |key| {
        raw.get("counts")
            .and_then(|c| c.get(key))
            .and_then(|v| v.as_u64())
            .filter(|v| *v < 1_000_000_000_000_000_000)
    };
    let behind = behind.trim();
    let dusky =
        if !behind.is_empty() && behind.len() <= 18 && behind.bytes().all(|b| b.is_ascii_digit()) {
            behind.parse::<u64>().ok()
        } else {
            None
        };
    let counts = [count("pacman"), count("aur"), dusky];
    let total: u64 = counts.iter().flatten().sum();
    let details = ["Pacman", "AUR", "Dusky commits (main)"]
        .into_iter()
        .zip(counts)
        .map(|(name, n)| {
            format!(
                "{name}: {}",
                n.map(|v| v.to_string()).unwrap_or_else(|| "unknown".into())
            )
        })
        .collect::<Vec<_>>()
        .join("\n");
    UpdatesState {
        // Badges count confirmed updates; unavailable checks belong in the tooltip.
        badge: if total > 0 {
            total.to_string()
        } else {
            String::new()
        },
        tooltip: format!(
            "Cached update counts\n{details}\n\nLMB: System Update\nRMB: Dusky Update"
        ),
    }
}

pub fn updates_state() -> UpdatesState {
    let base = xdg_config_dir().join("dusky/settings");
    parse_updates(
        &std::fs::read_to_string(base.join("waybar_update_counter_h")).unwrap_or_default(),
        &std::fs::read_to_string(base.join("dusky_update_behind_commit")).unwrap_or_default(),
    )
}

pub fn animations_enabled() -> Option<bool> {
    let response = hyprland_ipc("j/getoption animations:enabled")?;
    serde_json::from_str::<serde_json::Value>(&response)
        .ok()?
        .get("bool")?
        .as_bool()
}

fn is_process_running_in(proc_dir: &Path, uid: u32, target_comm: &str) -> Option<bool> {
    use std::os::unix::fs::MetadataExt;

    let entries = std::fs::read_dir(proc_dir).ok()?;
    for entry in entries.flatten() {
        let name = entry.file_name();
        let Some(name_str) = name.to_str() else {
            continue;
        };
        if name_str.is_empty() || !name_str.bytes().all(|b| b.is_ascii_digit()) {
            continue;
        }
        let path = entry.path();
        let Ok(meta) = std::fs::metadata(&path) else {
            continue;
        };
        if meta.uid() != uid {
            continue;
        }
        let Ok(comm) = std::fs::read_to_string(path.join("comm")) else {
            continue;
        };
        if comm.trim_end_matches('\n') != target_comm {
            continue;
        }
        if let Ok(stat) = std::fs::read_to_string(path.join("stat"))
            && let Some((_, after_comm)) = stat.rsplit_once(')')
            && after_comm.trim_start().starts_with('Z')
        {
            continue;
        }
        return Some(true);
    }
    Some(false)
}

pub fn is_idle_active() -> Option<bool> {
    let uid = unsafe { libc::getuid() };
    is_process_running_in(Path::new("/proc"), uid, "hypridle")
}

pub fn is_blur_on() -> Option<bool> {
    let txt = std::fs::read_to_string(xdg_config_dir().join("dusky/settings/opacity_blur")).ok()?;
    Some(txt.trim().eq_ignore_ascii_case("true"))
}

pub fn is_audio_active() -> bool {
    let pid_file = xdg_config_dir().join("dusky/settings/dusky_studio/daemon.pid");
    let Ok(txt) = std::fs::read_to_string(pid_file) else {
        return false;
    };
    let Ok(pid) = txt.trim().parse::<i32>() else {
        return false;
    };
    if pid <= 0 {
        return false;
    }
    std::fs::read(format!("/proc/{pid}/cmdline"))
        .map(|b| {
            b.windows(b"dusky_audio_studio".len())
                .any(|w| w == b"dusky_audio_studio")
        })
        .unwrap_or(false)
}

pub fn mako_dnd() -> Option<bool> {
    let out = run_command(
        &["makoctl".into(), "mode".into()],
        Duration::from_millis(500),
        true,
    )?;
    if !out.status {
        return None;
    }
    Some(out.stdout.lines().any(|l| l.trim() == "do-not-disturb"))
}

pub fn toggle_dnd() {
    let _ = run_command(
        &[
            "makoctl".into(),
            "mode".into(),
            "-t".into(),
            "do-not-disturb".into(),
        ],
        Duration::from_millis(800),
        false,
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn logind_auto_session_works_without_environment_id() {
        const LOG: &str = "DUSKY_TRAY_TEST_LOGIND_LOG";
        const EXIT: &str = "DUSKY_TRAY_TEST_LOGIND_EXIT";
        if std::env::var_os(LOG).is_some() {
            assert!(std::env::var_os("XDG_SESSION_ID").is_none());
            let device = BacklightDevice {
                name: "fixture-backlight".into(),
                max: 200,
                path: PathBuf::new(),
            };
            assert_eq!(
                apply_logind_brightness(&device, 37),
                std::env::var(EXIT).unwrap() == "0",
            );
            return;
        }
        // Run in a child test process so PATH/environment changes cannot race
        // the other tests. A failing logind method must preserve the fallback.
        use std::os::unix::fs::PermissionsExt;
        let directory = runtime_dir().join(format!("logind-test-{}", std::process::id()));
        std::fs::create_dir(&directory).unwrap();
        let bus = directory.join("busctl");
        std::fs::write(
            &bus,
            concat!(
                "#!/usr/bin/bash\n",
                "printf '%s\\n' \"$*\" >> \"$DUSKY_TRAY_TEST_LOGIND_LOG\"\n",
                "exit \"$DUSKY_TRAY_TEST_LOGIND_EXIT\"\n",
            ),
        )
        .unwrap();
        std::fs::set_permissions(&bus, std::fs::Permissions::from_mode(0o755)).unwrap();
        let log = directory.join("calls");
        for exit in ["0", "1"] {
            let output = std::process::Command::new(std::env::current_exe().unwrap())
                .args([
                    "--exact",
                    "backend::system::tests::logind_auto_session_works_without_environment_id",
                ])
                .env_remove("XDG_SESSION_ID")
                .env("PATH", &directory)
                .env(LOG, &log)
                .env(EXIT, exit)
                .output()
                .unwrap();
            assert!(
                output.status.success(),
                "{}",
                String::from_utf8_lossy(&output.stdout)
            );
        }
        let calls = std::fs::read_to_string(&log).unwrap();
        std::fs::remove_dir_all(&directory).unwrap();
        let expected = "--system call org.freedesktop.login1 /org/freedesktop/login1/session/auto org.freedesktop.login1.Session SetBrightness ssu backlight fixture-backlight 74\n";
        assert_eq!(calls, expected.repeat(2));
    }

    #[test]
    fn wifi_sysfs_detects_phy80211_and_wireless() {
        let root = runtime_dir().join(format!("wifi-sysfs-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).unwrap();

        // Wired ethernet up (no phy80211/wireless) -> false
        let eth0 = root.join("eth0");
        std::fs::create_dir_all(eth0.join("device")).unwrap();
        std::fs::write(eth0.join("type"), "1\n").unwrap();
        std::fs::write(eth0.join("operstate"), "up\n").unwrap();
        assert_eq!(wifi_connected_in(&root), Some(false));

        // Modern WEXT-free driver (phy80211 only) down -> false
        let wlan0 = root.join("wlan0");
        std::fs::create_dir_all(wlan0.join("device")).unwrap();
        std::fs::create_dir_all(wlan0.join("phy80211")).unwrap();
        std::fs::write(wlan0.join("type"), "1\n").unwrap();
        std::fs::write(wlan0.join("operstate"), "down\n").unwrap();
        assert_eq!(wifi_connected_in(&root), Some(false));

        // Non-ethernet wireless monitor interface (type 803) up -> ignored
        let mon0 = root.join("mon0");
        std::fs::create_dir_all(mon0.join("device")).unwrap();
        std::fs::create_dir_all(mon0.join("phy80211")).unwrap();
        std::fs::write(mon0.join("type"), "803\n").unwrap();
        std::fs::write(mon0.join("operstate"), "up\n").unwrap();
        assert_eq!(wifi_connected_in(&root), Some(false));

        // wlan0 comes up -> true
        std::fs::write(wlan0.join("operstate"), "up\n").unwrap();
        assert_eq!(wifi_connected_in(&root), Some(true));

        // Legacy wireless/ directory without phy80211 -> true
        std::fs::write(wlan0.join("operstate"), "dormant\n").unwrap();
        let wlan1 = root.join("wlan1");
        std::fs::create_dir_all(wlan1.join("device")).unwrap();
        std::fs::create_dir_all(wlan1.join("wireless")).unwrap();
        std::fs::write(wlan1.join("type"), "1\n").unwrap();
        std::fs::write(wlan1.join("operstate"), "up\n").unwrap();
        assert_eq!(wifi_connected_in(&root), Some(true));

        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn night_light_scale_increases_warmth_and_round_trips() {
        assert_eq!(sunset_temperature(0.0), 6500);
        assert_eq!(sunset_temperature(100.0), 1000);
        assert_eq!(sunset_temperature(50.0), 3750);
        for percent in 0..=100 {
            assert!(
                (sunset_percent(sunset_temperature(percent as f32) as f32) - percent as f32).abs()
                    < 0.01
            );
        }
        assert_eq!(sunset_percent(20000.0), 0.0);
        assert_eq!(sunset_temperature(150.0), 1000);
    }

    #[test]
    fn update_badge_uses_counts_and_current_commit_file() {
        let data = r#"{"counts":{"pacman":10,"aur":0,"dusky":999},"tooltip":"Total: 10)"}"#;
        assert_eq!(parse_updates(data, "0").badge, "10");
        assert_eq!(parse_updates(data, "2").badge, "12");
        assert_eq!(
            parse_updates(r#"{"counts":{"pacman":0,"aur":0}}"#, "0").badge,
            ""
        );
    }

    #[test]
    fn update_badge_is_empty_without_confirmed_updates() {
        for cached in [
            "",
            "invalid json",
            "{}",
            r#"{"counts":{"pacman":null,"aur":null,"dusky":0}}"#,
            r#"{"counts":{"pacman":0,"aur":null}}"#,
            r#"{"counts":{"pacman":-1,"aur":"0"}}"#,
            r#"{"counts":{"pacman":1000000000000000000,"aur":0}}"#,
        ] {
            for behind in ["", "0", "invalid", "-1", "1000000000000000000"] {
                let state = parse_updates(cached, behind);
                assert!(state.badge.is_empty(), "{cached:?}, {behind:?}");
                assert!(state.tooltip.contains("unknown"));
            }
        }
        let state = parse_updates(r#"{"counts":{"pacman":0,"aur":0}}"#, "0");
        assert!(state.badge.is_empty());
        assert!(!state.tooltip.contains("unknown"));
    }

    #[test]
    fn update_badge_keeps_confirmed_counts_when_other_checks_are_unavailable() {
        let state = parse_updates(r#"{"counts":{"pacman":8,"aur":null}}"#, "0");
        assert_eq!(state.badge, "8");
        assert!(state.tooltip.contains("AUR: unknown"));
        assert_eq!(parse_updates("{}", "2").badge, "2");
        let state = parse_updates(r#"{"counts":{"pacman":8,"aur":9}}"#, "0");
        assert_eq!(state.badge, "17");
        assert!(!state.tooltip.contains("unknown"));
    }

    fn net_counter(iface: &str, rx: u64, tx: u64) -> NetworkCounters {
        NetworkCounters {
            iface: iface.into(),
            ifindex: 1,
            carrier: Some(1),
            rx,
            tx,
        }
    }

    #[test]
    fn network_reads_require_complete_samples_and_skip_offline_counters() {
        let root = runtime_dir().join(format!("network-test-{}", std::process::id()));
        std::fs::create_dir_all(&root).unwrap();
        let devices = root.join("devices");
        let net_dev = root.join("dev");
        for iface in ["eth0", "wlan0", "lo", "tun0"] {
            let path = devices.join(iface);
            std::fs::create_dir_all(&path).unwrap();
            std::fs::write(path.join("operstate"), "up\n").unwrap();
            std::fs::write(path.join("ifindex"), "1\n").unwrap();
            if iface != "lo" && iface != "tun0" {
                std::fs::create_dir(path.join("device")).unwrap();
            }
        }
        std::fs::write(devices.join("wlan0/operstate"), "unknown\n").unwrap();
        std::fs::write(devices.join("wlan0/carrier_up_count"), "7\n").unwrap();
        std::fs::write(&net_dev, "h\nh\neth0: 20 0 0 0 0 0 0 0 10 0\nwlan0: 30 0 0 0 0 0 0 0 15 0\ntun0: 99999 0 0 0 0 0 0 0 99999 0\n").unwrap();
        let counters = read_network_counters(&net_dev, &devices).unwrap();
        assert_eq!(counters.len(), 2);
        assert_eq!(
            (counters[0].iface.as_str(), counters[0].rx, counters[0].tx),
            ("eth0", 20, 10)
        );
        assert_eq!(
            (counters[1].iface.as_str(), counters[1].rx, counters[1].tx),
            ("wlan0", 30, 15)
        );
        assert_eq!(counters[0].carrier, None);
        assert_eq!(counters[1].carrier, Some(7));
        for invalid in [
            "",
            "eth0: broken",
            "eth0: 20 0 0 0 0 0 0 0 bad\nwlan0: 30 0 0 0 0 0 0 0 15",
            "eth0: 20 0 0 0 0 0 0 0 10",
        ] {
            std::fs::write(&net_dev, invalid).unwrap();
            assert!(read_network_counters(&net_dev, &devices).is_none());
        }
        std::fs::remove_file(&net_dev).unwrap();
        assert!(read_network_counters(&net_dev, &devices).is_none());
        std::fs::write(devices.join("eth0/operstate"), "down\n").unwrap();
        std::fs::write(devices.join("wlan0/operstate"), "lowerlayerdown\n").unwrap();
        // The counters file does not exist, yet an offline sample succeeds.
        assert!(
            read_network_counters(&net_dev, &devices)
                .unwrap()
                .is_empty()
        );
        assert!(read_network_counters(&net_dev, &root.join("absent")).is_none());
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn network_aggregates_deltas_and_preserves_compact_format() {
        let mut previous = None;
        let first = vec![
            net_counter("eth0", 1000, 2000),
            net_counter("wlan0", 3000, 4000),
        ];
        assert_eq!(
            network_reading(
                Some(first.clone()),
                Some(Duration::ZERO),
                WarpStatus::NotInstalled,
                &mut previous
            )
            .text,
            "0 KB 0"
        );
        let next = vec![
            net_counter("eth0", 5096, 4048),
            net_counter("wlan0", 5048, 8096),
        ];
        let reading = network_reading(
            Some(next),
            Some(Duration::from_secs(2)),
            WarpStatus::Connected,
            &mut previous,
        );
        assert_eq!(reading.text, "3 KB 3");
        assert_eq!(reading.warp, WarpStatus::Connected);
        assert_eq!(reading.total_rx_bytes, 10_144);
        assert_eq!(reading.total_tx_bytes, 12_144);
        assert_eq!(
            reading.tooltip,
            "Upload: 11.9 KiB (session)\nDownload: 9.9 KiB (session)\nCloudflare WARP: Connected (ON)"
        );
        let mb = vec![net_counter("eth0", 2 << 20, 2 << 20)];
        previous = None;
        network_reading(
            Some(vec![net_counter("eth0", 0, 0)]),
            Some(Duration::ZERO),
            WarpStatus::Disconnected,
            &mut previous,
        );
        let reading_off = network_reading(
            Some(mb.clone()),
            Some(Duration::from_secs(2)),
            WarpStatus::Disconnected,
            &mut previous,
        );
        assert_eq!(reading_off.text, "1 MB 1");
        assert_eq!(reading_off.warp, WarpStatus::Disconnected);
        assert_eq!(reading_off.total_rx_bytes, 2 << 20);
        assert_eq!(reading_off.total_tx_bytes, 2 << 20);
        assert_eq!(
            reading_off.tooltip,
            "Upload: 2.0 MiB (session)\nDownload: 2.0 MiB (session)\nCloudflare WARP: Disconnected (OFF)"
        );

        // When WARP is not installed, omit WARP status from the tooltip entirely
        previous = None;
        network_reading(
            Some(vec![net_counter("eth0", 0, 0)]),
            Some(Duration::ZERO),
            WarpStatus::NotInstalled,
            &mut previous,
        );
        let reading_not_installed = network_reading(
            Some(mb),
            Some(Duration::from_secs(2)),
            WarpStatus::NotInstalled,
            &mut previous,
        );
        assert_eq!(reading_not_installed.warp, WarpStatus::NotInstalled);
        assert_eq!(
            reading_not_installed.tooltip,
            "Upload: 2.0 MiB (session)\nDownload: 2.0 MiB (session)"
        );
    }

    #[test]
    fn network_read_failure_recovery_cannot_create_false_spike() {
        let mut previous = None;
        let counters = vec![net_counter("eth0", 1 << 30, 1 << 29)];
        network_reading(
            Some(counters.clone()),
            Some(Duration::ZERO),
            WarpStatus::NotInstalled,
            &mut previous,
        );
        let failed = network_reading(
            None,
            Some(Duration::from_secs(2)),
            WarpStatus::NotInstalled,
            &mut previous,
        );
        assert_eq!(failed.text, "--");
        assert_eq!(failed.tooltip, "Network reading unavailable");
        assert!(!failed.disconnected);
        assert!(previous.is_none());
        assert_eq!(
            network_reading(
                Some(counters),
                Some(Duration::from_secs(4)),
                WarpStatus::Disconnected,
                &mut previous
            )
            .text,
            "0 KB 0"
        );
        let offline = network_reading(
            Some(vec![]),
            Some(Duration::from_secs(6)),
            WarpStatus::Disconnected,
            &mut previous,
        );
        assert!(offline.disconnected);
        assert_eq!(
            offline.tooltip,
            "Disconnected\nCloudflare WARP: Disconnected (OFF)"
        );
        assert!(previous.is_none());
    }

    #[test]
    fn network_rebases_individual_resets_recreation_and_reconnects() {
        let initial = vec![
            net_counter("eth0", 1000, 1000),
            net_counter("wlan0", 1000, 1000),
        ];
        let mut variants = vec![vec![
            net_counter("eth0", 10, 1000),
            net_counter("wlan0", 5000, 5000),
        ]];
        let mut changed = initial.clone();
        changed[0].ifindex = 2;
        changed[0].rx = 1 << 30;
        variants.push(changed);
        let mut changed = initial.clone();
        changed[0].carrier = Some(2);
        changed[0].rx = 1 << 30;
        variants.push(changed);
        let mut changed = initial.clone();
        changed[0].tx = 0;
        changed[1].tx = 5000;
        variants.push(changed);
        variants.push(vec![net_counter("eth0", 1 << 30, 1 << 30)]);
        let mut changed = initial.clone();
        changed.push(net_counter("eth1", 1 << 30, 1 << 30));
        variants.push(changed);
        for counters in variants {
            let mut previous = None;
            network_reading(
                Some(initial.clone()),
                Some(Duration::ZERO),
                WarpStatus::NotInstalled,
                &mut previous,
            );
            assert_eq!(
                network_reading(
                    Some(counters),
                    Some(Duration::from_secs(2)),
                    WarpStatus::NotInstalled,
                    &mut previous
                )
                .text,
                "0 KB 0"
            );
        }
    }

    #[test]
    fn network_rebases_delayed_early_and_failed_clock_samples() {
        for now in [
            Duration::from_secs(9),
            Duration::from_secs(10),
            Duration::from_millis(10100),
            Duration::from_secs(16),
            Duration::from_secs(3700),
        ] {
            let mut previous = None;
            network_reading(
                Some(vec![net_counter("eth0", 0, 0)]),
                Some(Duration::from_secs(10)),
                WarpStatus::NotInstalled,
                &mut previous,
            );
            assert_eq!(
                network_reading(
                    Some(vec![net_counter("eth0", 1 << 30, 1 << 30)]),
                    Some(now),
                    WarpStatus::NotInstalled,
                    &mut previous
                )
                .text,
                "0 KB 0"
            );
        }
        let mut previous = None;
        network_reading(
            Some(vec![net_counter("eth0", 0, 0)]),
            Some(Duration::ZERO),
            WarpStatus::NotInstalled,
            &mut previous,
        );
        assert_eq!(
            network_reading(
                Some(vec![net_counter("eth0", 1 << 30, 1 << 30)]),
                None,
                WarpStatus::NotInstalled,
                &mut previous
            )
            .text,
            "--"
        );
        assert!(previous.is_none());
        let first = network_time().unwrap();
        assert!(network_time().unwrap() >= first);
    }

    #[test]
    fn network_large_multi_interface_deltas_do_not_saturate_or_overflow() {
        let mut previous = None;
        network_reading(
            Some(vec![net_counter("eth0", 0, 0), net_counter("wlan0", 0, 0)]),
            Some(Duration::ZERO),
            WarpStatus::NotInstalled,
            &mut previous,
        );
        let reading = network_reading(
            Some(vec![
                net_counter("eth0", u64::MAX, u64::MAX),
                net_counter("wlan0", u64::MAX, u64::MAX),
            ]),
            Some(Duration::from_secs(2)),
            WarpStatus::NotInstalled,
            &mut previous,
        );
        assert_eq!(reading.text, "17592186044416 MB 17592186044416");
        assert!(!reading.tooltip.contains("inf"));
        assert!(!reading.tooltip.contains("NaN"));
    }

    #[test]
    fn warp_detection_checks_interface_operstate_and_up_flag() {
        let root = runtime_dir().join(format!("warp-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).unwrap();

        // Missing CloudflareWARP interface -> Disconnected (when installed) or NotInstalled
        assert_eq!(
            detect_warp_status_in(&root, false),
            WarpStatus::NotInstalled
        );
        assert_eq!(detect_warp_status_in(&root, true), WarpStatus::Disconnected);

        let warp = root.join("CloudflareWARP");
        std::fs::create_dir_all(&warp).unwrap();

        // Active TUN interface (operstate unknown, IFF_UP 0x1 set in 0x10d1) -> Connected
        std::fs::write(warp.join("operstate"), "unknown\n").unwrap();
        std::fs::write(warp.join("flags"), "0x10d1\n").unwrap();
        assert_eq!(detect_warp_status_in(&root, true), WarpStatus::Connected);
        // Even if sysfs interface exists, uninstalled flag yields NotInstalled
        assert_eq!(
            detect_warp_status_in(&root, false),
            WarpStatus::NotInstalled
        );

        // Explicit operstate "up" with IFF_UP -> Connected
        std::fs::write(warp.join("operstate"), "up\n").unwrap();
        assert_eq!(detect_warp_status_in(&root, true), WarpStatus::Connected);

        // Operstate "down" -> Disconnected
        std::fs::write(warp.join("operstate"), "down\n").unwrap();
        assert_eq!(detect_warp_status_in(&root, true), WarpStatus::Disconnected);

        // Operstate "unknown" but IFF_UP cleared (0x1090) -> Disconnected
        std::fs::write(warp.join("operstate"), "unknown\n").unwrap();
        std::fs::write(warp.join("flags"), "0x1090\n").unwrap();
        assert_eq!(detect_warp_status_in(&root, true), WarpStatus::Disconnected);

        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn rapl_power_handles_normal_delta_wraparound_and_rebase() {
        let root = runtime_dir().join(format!("rapl-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);

        // MSR domain + subzone (subzone intel-rapl:0:0 must be ignored)
        let pkg0 = root.join("intel-rapl:0");
        let sub0 = root.join("intel-rapl:0:0");
        std::fs::create_dir_all(&pkg0).unwrap();
        std::fs::create_dir_all(&sub0).unwrap();
        std::fs::write(pkg0.join("name"), "package-0\n").unwrap();
        std::fs::write(pkg0.join("max_energy_range_uj"), "1000000000\n").unwrap();
        std::fs::write(pkg0.join("energy_uj"), "100000000\n").unwrap();
        std::fs::write(sub0.join("name"), "core\n").unwrap();
        std::fs::write(sub0.join("energy_uj"), "50000000\n").unwrap();

        let mut tracker = RaplTracker::default();
        // First sample establishes baseline -> None
        assert!(rapl_power_reading(&root, Some(Duration::from_secs(10)), &mut tracker).is_none());

        // Normal 2s delta: +50_000_000 uJ over 2_000_000 us = 25.0 W
        std::fs::write(pkg0.join("energy_uj"), "150000000\n").unwrap();
        let w = rapl_power_reading(&root, Some(Duration::from_secs(12)), &mut tracker).unwrap();
        assert!((w - 25.0).abs() < 1e-6, "expected 25.0W, got {w}");

        // Counter wraparound: prev = 980_000_000, max = 1_000_000_000, cur = 20_000_000 -> 40_000_000 uJ / 2s = 20.0 W
        std::fs::write(pkg0.join("energy_uj"), "980000000\n").unwrap();
        let _ = rapl_power_reading(&root, Some(Duration::from_secs(14)), &mut tracker);
        std::fs::write(pkg0.join("energy_uj"), "20000000\n").unwrap();
        let w_wrap =
            rapl_power_reading(&root, Some(Duration::from_secs(16)), &mut tracker).unwrap();
        assert!(
            (w_wrap - 20.0).abs() < 1e-6,
            "expected 20.0W after wraparound, got {w_wrap}"
        );

        // Elapsed > 5s (suspend) rebases cleanly without returning a skewed value
        std::fs::write(pkg0.join("energy_uj"), "220000000\n").unwrap();
        assert!(rapl_power_reading(&root, Some(Duration::from_secs(30)), &mut tracker).is_none());

        // Subsequent 2s sample resumes accurately: +30_000_000 uJ / 2s = 15.0 W
        std::fs::write(pkg0.join("energy_uj"), "250000000\n").unwrap();
        let w_after =
            rapl_power_reading(&root, Some(Duration::from_secs(32)), &mut tracker).unwrap();
        assert!(
            (w_after - 15.0).abs() < 1e-6,
            "expected 15.0W after rebase, got {w_after}"
        );

        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn memory_dirty_cache_and_zram_swap_format_cleanly() {
        let meminfo = concat!(
            "MemTotal:       16777216 kB\n",
            "MemFree:         4194304 kB\n",
            "MemAvailable:    8388608 kB\n",
            "Dirty:             12288 kB\n",
            "Writeback:          4096 kB\n",
            "SwapTotal:       8388608 kB\n",
            "SwapFree:        6291456 kB\n",
        );

        // 1. ZRAM active with compression ratio (512 MiB orig / 128 MiB compr = 4.0:1, 160 MiB phys)
        let mm_stat = "536870912 134217728 167772160 0 167772160 100 0 0 0\n";
        let state = parse_ram_state(meminfo, parse_zram_mm_stat(mm_stat));
        assert_eq!(state.text, "8.0 GB");
        assert_eq!(
            state.tooltip,
            "Memory: 8.0 GiB / 16.0 GiB (50%)\nDirty cache: 16.0 MiB\nSwap: 2.0 GiB / 8.0 GiB\nZRAM: 160 MiB physical · 4.0:1 ratio\nLMB: Open zramctl"
        );

        // 2. ZRAM active with 0 compressed bytes (no division by zero)
        let empty_zram = "0 0 4194304 0 4194304 0 0 0 0\n";
        let state_empty = parse_ram_state(meminfo, parse_zram_mm_stat(empty_zram));
        assert_eq!(
            state_empty.tooltip,
            "Memory: 8.0 GiB / 16.0 GiB (50%)\nDirty cache: 16.0 MiB\nSwap: 2.0 GiB / 8.0 GiB\nZRAM: 4 MiB physical\nLMB: Open zramctl"
        );

        // 3. Disk swap without ZRAM
        let state_disk = parse_ram_state(meminfo, None);
        assert_eq!(
            state_disk.tooltip,
            "Memory: 8.0 GiB / 16.0 GiB (50%)\nDirty cache: 16.0 MiB\nSwap: 2.0 GiB / 8.0 GiB\nLMB: Open zramctl"
        );

        // 4. No swap configured
        let no_swap_meminfo = concat!(
            "MemTotal:       16777216 kB\n",
            "MemAvailable:   12582912 kB\n",
            "Dirty:              2048 kB\n",
            "Writeback:             0 kB\n",
            "SwapTotal:             0 kB\n",
            "SwapFree:              0 kB\n",
        );
        let state_no_swap = parse_ram_state(no_swap_meminfo, None);
        assert_eq!(state_no_swap.text, "4.0 GB");
        assert_eq!(
            state_no_swap.tooltip,
            "Memory: 4.0 GiB / 16.0 GiB (25%)\nDirty cache: 2.0 MiB\nSwap: Disabled\nLMB: Open zramctl"
        );

        // 5. Multi-device ZRAM aggregation (zram0 + zram1, ignoring non-zram block devices)
        let root = runtime_dir().join(format!("zram-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let z0 = root.join("zram0");
        let z1 = root.join("zram1");
        let nvme = root.join("nvme0n1");
        std::fs::create_dir_all(&z0).unwrap();
        std::fs::create_dir_all(&z1).unwrap();
        std::fs::create_dir_all(&nvme).unwrap();
        std::fs::write(
            z0.join("mm_stat"),
            "536870912 134217728 167772160 0 167772160 100 0 0 0\n",
        )
        .unwrap();
        std::fs::write(
            z1.join("mm_stat"),
            "536870912 134217728 167772160 0 167772160 100 0 0 0\n",
        )
        .unwrap();

        let aggregated = read_zram_mm_stats_in(&root);
        assert_eq!(aggregated, Some((1_073_741_824, 268_435_456, 335_544_320)));
        let state_multi = parse_ram_state(meminfo, aggregated);
        assert_eq!(
            state_multi.tooltip,
            "Memory: 8.0 GiB / 16.0 GiB (50%)\nDirty cache: 16.0 MiB\nSwap: 2.0 GiB / 8.0 GiB\nZRAM: 320 MiB physical · 4.0:1 ratio\nLMB: Open zramctl"
        );
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn clamp_and_snap() {
        assert_eq!(clamp(f32::NAN, 0.0, 100.0), 0.0);
        assert_eq!(snap_to_step(50.4, 0.0, 100.0, 1.0), 50.0);
        assert_eq!(snap_to_step(150.0, 0.0, 100.0, 1.0), 100.0);
        assert_eq!(percent_int(99.6, 0), 100);
    }

    #[test]
    fn runtime_dir_is_tmpfs_path() {
        let d = runtime_dir();
        assert!(d.is_dir());
        assert!(is_tmpfs(&d), "runtime directory must use tmpfs: {d:?}");
        assert_eq!(d, runtime_dir(), "directory selection stays stable");
        let missing = d
            .join(format!("missing-{}", std::process::id()))
            .join("nested");
        assert!(!missing.exists());
        assert!(
            is_tmpfs(&missing),
            "missing paths probe their existing ancestor"
        );
        assert!(!is_tmpfs(Path::new("/proc")), "procfs is not tmpfs");
    }

    #[test]
    fn parse_detect_and_getvcp() {
        let buses = parse_detect_buses("Display 1: /dev/i2c-3 foo\nDisplay 2: i2c-5 bar");
        assert_eq!(buses, vec![3, 5]);
        let v = parse_getvcp("VCP 10 C 60 100").unwrap();
        assert_eq!(v, (60, 100));
        assert!(parse_getvcp("garbage").is_none());
    }

    #[test]
    fn power_mapping() {
        // Pure mapping covered indirectly; at least ensure no panic without files.
        let _ = get_power_profile();
    }

    #[test]
    fn cpu_ram_no_panic() {
        let (c, r) = cpu_ram();
        assert!(!c.is_empty() && !r.is_empty());
    }

    #[test]
    fn format_net_bytes_scales_iec_units_cleanly() {
        assert_eq!(format_net_bytes(0), "0 B");
        assert_eq!(format_net_bytes(999), "999 B");
        assert_eq!(format_net_bytes(1_024), "1.0 KiB");
        assert_eq!(format_net_bytes(1_536), "1.5 KiB");
        assert_eq!(format_net_bytes(126_248_550), "120.4 MiB");
        assert_eq!(format_net_bytes(1_556_925_645), "1.45 GiB");
        assert_eq!(format_net_bytes(2_199_023_255_552), "2.00 TiB");
    }

    #[test]
    fn cpu_temp_discovery_prioritizes_drivers_tdie_and_caches_with_cooldown() {
        let root = runtime_dir().join(format!("cputemp-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let hwmon = root.join("hwmon");
        let thermal = root.join("thermal");
        std::fs::create_dir_all(&hwmon).unwrap();
        std::fs::create_dir_all(&thermal).unwrap();

        // Populate a lower-priority thermal_zone first
        let tz0 = thermal.join("thermal_zone0");
        std::fs::create_dir_all(&tz0).unwrap();
        std::fs::write(tz0.join("type"), "x86_pkg_temp\n").unwrap();
        std::fs::write(tz0.join("temp"), "39000\n").unwrap();

        let mut tracker = CpuTempTracker::default();
        let t_tz = cpu_temp_reading(
            &hwmon,
            &thermal,
            Some(Duration::from_secs(10)),
            &mut tracker,
        )
        .unwrap();
        assert!((t_tz - 39.0).abs() < 1e-6);

        // Add k10temp with Tctl (temp1) and Tdie (temp2)
        let h2 = hwmon.join("hwmon2");
        std::fs::create_dir_all(&h2).unwrap();
        std::fs::write(h2.join("name"), "k10temp\n").unwrap();
        std::fs::write(h2.join("temp1_label"), "Tctl\n").unwrap();
        std::fs::write(h2.join("temp1_input"), "68000\n").unwrap();
        std::fs::write(h2.join("temp2_label"), "Tdie\n").unwrap();
        std::fs::write(h2.join("temp2_input"), "54500\n").unwrap();

        // While cached path (tz0) remains valid, hot path uses it directly
        let still_cached = cpu_temp_reading(
            &hwmon,
            &thermal,
            Some(Duration::from_secs(11)),
            &mut tracker,
        )
        .unwrap();
        assert!((still_cached - 39.0).abs() < 1e-6);

        // Removing cached sensor invalidates cache; cooldown (<5s) prevents immediate rescan
        std::fs::remove_file(tz0.join("temp")).unwrap();
        assert!(
            cpu_temp_reading(
                &hwmon,
                &thermal,
                Some(Duration::from_secs(12)),
                &mut tracker
            )
            .is_none()
        );

        // After 5s cooldown, rediscovery finds k10temp Tdie (54.5°C)
        let t_tdie = cpu_temp_reading(
            &hwmon,
            &thermal,
            Some(Duration::from_secs(16)),
            &mut tracker,
        )
        .unwrap();
        assert!((t_tdie - 54.5).abs() < 1e-6);

        // Out-of-bounds sentinel reading invalidates cache cleanly
        std::fs::write(h2.join("temp2_input"), "-273000\n").unwrap();
        assert!(
            cpu_temp_reading(
                &hwmon,
                &thermal,
                Some(Duration::from_secs(18)),
                &mut tracker
            )
            .is_none()
        );

        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn battery_watts_formatting_never_exceeds_seven_characters() {
        assert_eq!(format_watts(0), "0.00W");
        assert_eq!(format_watts(6_420_000), "6.42W");
        assert_eq!(format_watts(24_600_000), "24.60W");
        assert_eq!(format_watts(102_340_000), "102.34W");
        assert_eq!(format_watts(999_990_000), "999.99W");
        assert_eq!(format_watts(1_500_000_000), "999.99W");
        assert_eq!(format_watts(u64::MAX), "999.99W");
        for uw in [0, 6_420_000, 24_600_000, 102_340_000, 999_990_000, u64::MAX] {
            let formatted = format_watts(uw);
            assert!(formatted.ends_with('W'));
            assert!(formatted.len() <= 7, "{formatted} exceeded 7 chars");
        }
    }

    #[test]
    fn battery_sysfs_filters_nodes_and_computes_power_and_current_fallback() {
        let root = runtime_dir().join(format!("battery-test-{}", std::process::id()));
        std::fs::create_dir_all(&root).unwrap();

        // Ignored nodes: Mains, Device scope, present == 0
        let ac = root.join("AC");
        std::fs::create_dir_all(&ac).unwrap();
        std::fs::write(ac.join("type"), "Mains\n").unwrap();

        let mouse = root.join("hidpp_battery_0");
        std::fs::create_dir_all(&mouse).unwrap();
        std::fs::write(mouse.join("type"), "Battery\n").unwrap();
        std::fs::write(mouse.join("scope"), "Device\n").unwrap();
        std::fs::write(mouse.join("present"), "1\n").unwrap();
        std::fs::write(mouse.join("status"), "Discharging\n").unwrap();
        std::fs::write(mouse.join("power_now"), "50000000\n").unwrap();

        let empty = root.join("BAT_EMPTY");
        std::fs::create_dir_all(&empty).unwrap();
        std::fs::write(empty.join("type"), "Battery\n").unwrap();
        std::fs::write(empty.join("present"), "0\n").unwrap();
        std::fs::write(empty.join("power_now"), "50000000\n").unwrap();

        // Active system battery using power_now + energy_now (57.81 Wh / 24.6 W = 2h 21m)
        let bat0 = root.join("BAT0");
        std::fs::create_dir_all(&bat0).unwrap();
        std::fs::write(bat0.join("type"), "Battery\n").unwrap();
        std::fs::write(bat0.join("scope"), "System\n").unwrap();
        std::fs::write(bat0.join("present"), "1\n").unwrap();
        std::fs::write(bat0.join("status"), "Discharging\n").unwrap();
        std::fs::write(bat0.join("power_now"), "24600000\n").unwrap();
        std::fs::write(bat0.join("energy_now"), "57810000\n").unwrap();
        std::fs::write(bat0.join("energy_full"), "80000000\n").unwrap();

        let state = read_battery_state(&root);
        assert!(state.present);
        assert_eq!(state.status, BatteryStatus::Discharging);
        assert_eq!(state.text, "24.60W");
        assert_eq!(state.tooltip, "Battery: 72%\nRemaining: 2h 21m");
        assert!((state.watts - 24.6).abs() < 1e-6);

        // Fallback to current_now (µA) * voltage_now (µV) / 1_000_000 and charge_now/charge_full
        // Remaining to full: (6,000,000 - 375,350) µAh * 12V = 67,495,800 µWh
        // at 535,000 µA * 12V = 6,420,000 µW -> 37,848.11s -> 10h 31m
        std::fs::remove_file(bat0.join("power_now")).unwrap();
        std::fs::remove_file(bat0.join("energy_now")).unwrap();
        std::fs::remove_file(bat0.join("energy_full")).unwrap();
        std::fs::write(bat0.join("status"), "Charging\n").unwrap();
        std::fs::write(bat0.join("current_now"), "-535000\n").unwrap();
        std::fs::write(bat0.join("voltage_now"), "12000000\n").unwrap();
        std::fs::write(bat0.join("charge_now"), "375350\n").unwrap();
        std::fs::write(bat0.join("charge_full"), "6000000\n").unwrap();

        let state = read_battery_state(&root);
        assert!(state.present);
        assert_eq!(state.status, BatteryStatus::Charging);
        assert_eq!(state.text, "6.42W");
        assert_eq!(state.tooltip, "Battery: 6%\nRemaining: 10h 31m");
        assert!((state.watts - 6.42).abs() < 1e-6);

        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn weather_parses_v2_live_payload() {
        let json = r#"{
          "version": 2,
          "saved_at": 1795516390.0,
          "payload": {
            "text": "   11°C",
            "alt": "City",
            "tooltip": "<span size='xx-large'>11°C</span>\n<big> Partly cloudy</big>\n 12°C   -1°C   0%",
            "class": "weather"
          },
          "effective_unit": "metric",
          "country_code": "NO",
          "city": "City"
        }"#;
        let state = parse_weather_json(json).unwrap();
        assert_eq!(state.temperature, "11°C");
        assert_eq!(state.condition.as_deref(), Some("Partly cloudy"));
        assert_eq!(state.temp_max.as_deref(), Some("12°C"));
        assert_eq!(state.temp_min.as_deref(), Some("-1°C"));
        assert_eq!(state.precip_chance.as_deref(), Some("0%"));
        assert_eq!(state.location.as_deref(), Some("City"));
        assert_eq!(state.offline_note, None);
        assert!(!state.is_offline());
    }

    #[test]
    fn weather_parses_offline_and_rejects_invalid() {
        let offline_v2 = r#"{
          "version": 2,
          "payload": {
            "text": "   72.5 °F",
            "alt": "Seattle",
            "tooltip": "<span size='xx-large'>72.5°F</span>\n<big>Rain &amp; Drizzle</big>\n 78°F   54°F   45%\n\n<span color='#ff6b6b'>⚠ Offline — cached 18m ago</span>",
            "class": "weather"
          },
          "city": "Seattle"
        }"#;
        let state = parse_weather_json(offline_v2).unwrap();
        assert_eq!(state.temperature, "72.5°F");
        assert_eq!(state.condition.as_deref(), Some("Rain & Drizzle"));
        assert_eq!(state.temp_max.as_deref(), Some("78°F"));
        assert_eq!(state.temp_min.as_deref(), Some("54°F"));
        assert_eq!(state.precip_chance.as_deref(), Some("45%"));
        assert_eq!(state.location.as_deref(), Some("Seattle"));
        assert!(state.is_offline());
        assert_eq!(
            state.offline_note.as_deref(),
            Some("Offline — cached 18m ago")
        );

        let class_only = r#"{
          "version": 2,
          "payload": {
            "text": " 9°C",
            "class": ["weather", "offline"]
          }
        }"#;
        let fallback = parse_weather_json(class_only).unwrap();
        assert!(fallback.is_offline());
        assert_eq!(
            fallback.offline_note.as_deref(),
            Some("Offline — cached data")
        );

        for invalid in [
            "",
            "not json",
            "{}",
            r#"{"text":"11°C"}"#,
            r#"{"version":1,"payload":{"text":"11°C"}}"#,
            r#"{"version":2}"#,
            r#"{"version":2,"payload":{"text":"   "}}"#,
        ] {
            assert!(parse_weather_json(invalid).is_none(), "{invalid:?}");
        }
    }

    #[test]
    fn weather_stat_cache_reuses_and_invalidates_on_replace_or_remove() {
        let root = runtime_dir().join(format!("weather-cache-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).unwrap();
        let file = root.join("waybar_weather");
        let tmp = root.join("waybar_weather.tmp");

        let mut cache = None;
        assert!(weather_state_from_path(&file, &mut cache).is_none());
        assert!(cache.is_none());

        std::fs::write(&file, r#"{"version":2,"payload":{"text":"11°C"}}"#).unwrap();
        let first = weather_state_from_path(&file, &mut cache).unwrap();
        assert_eq!(first.temperature, "11°C");
        assert!(cache.is_some());

        // Atomic rename with identical length still changes inode and invalidates cache.
        std::fs::write(&tmp, r#"{"version":2,"payload":{"text":"22°C"}}"#).unwrap();
        std::fs::rename(&tmp, &file).unwrap();
        let updated = weather_state_from_path(&file, &mut cache).unwrap();
        assert_eq!(updated.temperature, "22°C");

        std::fs::remove_file(&file).unwrap();
        assert!(weather_state_from_path(&file, &mut cache).is_none());
        assert!(cache.is_none());

        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn weather_staleness_clock_skew_and_request_key_args() {
        // Freshness respects exact [0, 3540) TTL and rejects future timestamps (clock skew).
        assert!(is_weather_fresh(Some(1000.0), 1000.0));
        assert!(is_weather_fresh(Some(1000.0), 4539.9));
        assert!(!is_weather_fresh(Some(1000.0), 4540.0));
        assert!(!is_weather_fresh(Some(1005.0), 1000.0));
        assert!(!is_weather_fresh(None, 1000.0));

        assert_eq!(format_weather_age(0.0), "<1m");
        assert_eq!(format_weather_age(45.0), "<1m");
        assert_eq!(format_weather_age(900.0), "15m");
        assert_eq!(format_weather_age(3600.0), "1h");
        assert_eq!(format_weather_age(5400.0), "1h 30m");
        assert_eq!(format_weather_age(90_000.0), "1d 1h");

        let manual = parse_weather_record(
            r#"{
              "version": 2,
              "saved_at": 1700000000.0,
              "request_key": {"source": "manual", "unit_pref": "fahrenheit", "lat": 47.6, "lon": -122.3},
              "payload": {"text": "70°F"}
            }"#,
        )
        .unwrap();
        assert_eq!(
            weather_request_args(manual.request_key.as_ref()),
            vec!["--lat", "47.6", "--lon", "-122.3", "-f"]
        );

        let ip_celsius = parse_weather_record(
            r#"{
              "version": 2,
              "request_key": {"source": "ip", "unit_pref": "celsius"},
              "payload": {"text": "15°C"}
            }"#,
        )
        .unwrap();
        assert_eq!(
            weather_request_args(ip_celsius.request_key.as_ref()),
            vec!["-c"]
        );
        assert!(weather_request_args(None).is_empty());
    }

    #[test]
    fn wpctl_volume_parses_level_and_muted_state() {
        assert_eq!(parse_wpctl_volume("Volume: 0.50\n"), Some((50.0, false)));
        assert_eq!(
            parse_wpctl_volume("Volume: 0.73 [MUTED]\n"),
            Some((73.0, true))
        );
        assert_eq!(
            parse_wpctl_volume("Volume: 1.25 [MUTED]\n"),
            Some((100.0, true))
        );
        assert_eq!(parse_wpctl_volume("Volume: 0.00"), Some((0.0, false)));
        assert_eq!(parse_wpctl_volume("garbage"), None);
    }

    #[test]
    fn ddc_connector_cache_skips_laptop_only_and_detects_hotplug() {
        let root = runtime_dir().join(format!("ddc-conn-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let drm = root.join("drm");
        let edp = drm.join("card0-eDP-1");
        let hdmi = drm.join("card0-HDMI-A-1");
        let dp = drm.join("card0-DP-1");
        std::fs::create_dir_all(&edp).unwrap();
        std::fs::create_dir_all(&hdmi).unwrap();
        std::fs::create_dir_all(&dp).unwrap();
        std::fs::write(edp.join("status"), "connected\n").unwrap();
        std::fs::write(hdmi.join("status"), "disconnected\n").unwrap();
        std::fs::write(dp.join("status"), "disconnected\n").unwrap();

        // Laptop internal panel only -> zero external connectors.
        assert_eq!(connected_external_drm_connectors_in(&drm), Some(vec![]));

        // External DP monitor connected -> discovered and cached with real VCP max.
        std::fs::write(dp.join("status"), "connected\n").unwrap();
        let connectors = connected_external_drm_connectors_in(&drm).unwrap();
        assert_eq!(connectors, vec!["card0-DP-1"]);

        let cache_file = root.join("ddcutil_displays.json");
        let saved = DdcCacheData {
            connectors: connectors.clone(),
            displays: vec![DdcDisplay { bus: 4, max: 100 }],
        };
        std::fs::write(&cache_file, serialize_ddc_cache(&saved)).unwrap();
        assert_eq!(
            ddc_cached_displays(&cache_file, &connectors, Duration::from_secs(45)),
            Some(vec![DdcDisplay { bus: 4, max: 100 }])
        );

        // Hotplugging a second external monitor invalidates the cache immediately.
        std::fs::write(hdmi.join("status"), "connected\n").unwrap();
        let hotplugged = connected_external_drm_connectors_in(&drm).unwrap();
        assert_eq!(hotplugged, vec!["card0-DP-1", "card0-HDMI-A-1"]);
        assert_eq!(
            ddc_cached_displays(&cache_file, &hotplugged, Duration::from_secs(45)),
            None
        );

        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn cpu_sample_rebases_after_suspend() {
        assert_eq!(
            compute_cpu_usage_text(
                (150, 200),
                Some(Duration::from_secs(12)),
                Some((100, 100, Duration::from_secs(10)))
            ),
            "50%"
        );
        // Stale sample (>5s, e.g. suspend or tray closed for hours) rebases cleanly.
        assert_eq!(
            compute_cpu_usage_text(
                (150, 200),
                Some(Duration::from_secs(30)),
                Some((100, 100, Duration::from_secs(10)))
            ),
            "--"
        );
        // Too-short sample (<500ms) rebases cleanly.
        assert_eq!(
            compute_cpu_usage_text(
                (150, 200),
                Some(Duration::from_millis(10_200)),
                Some((100, 100, Duration::from_secs(10)))
            ),
            "--"
        );
        // Old 2-field format without timestamp fails parse and rebases cleanly.
        assert_eq!(parse_cpu_sample("100 200\n"), None);
        assert_eq!(
            parse_cpu_sample("100 200 12345\n"),
            Some((100, 200, Duration::from_millis(12_345)))
        );
    }

    #[test]
    fn hyprsunset_ipc_shuts_down_write_half_before_reading() {
        use std::io::{Read, Write};
        use std::os::unix::net::UnixListener;

        let sock = runtime_dir().join(format!("hyprsunset-test-{}.sock", std::process::id()));
        let _ = std::fs::remove_file(&sock);
        let listener = UnixListener::bind(&sock).unwrap();

        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut req = String::new();
            // Blocks until the client closes its write half via Shutdown::Write.
            stream.read_to_string(&mut req).unwrap();
            assert_eq!(req, "temperature");
            stream.write_all(b"2375").unwrap();
        });

        let started = std::time::Instant::now();
        let reply = hyprsunset_ipc_at(&sock, "temperature");
        let elapsed = started.elapsed();
        server.join().unwrap();
        let _ = std::fs::remove_file(&sock);

        assert_eq!(reply.as_deref(), Some("2375"));
        assert!(
            elapsed < Duration::from_millis(250),
            "IPC must not wait for 800ms read timeout (took {elapsed:?})"
        );
    }

    #[test]
    fn python3_resolver_prefers_local_override_over_usr_bin() {
        use std::os::unix::fs::PermissionsExt;

        let root = runtime_dir().join(format!("py3-resolve-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).unwrap();
        let local = root.join("python3");
        std::fs::write(&local, "#!/usr/bin/bash\n").unwrap();
        std::fs::set_permissions(&local, std::fs::Permissions::from_mode(0o755)).unwrap();

        // Explicit non-/usr/bin PATH match wins directly.
        assert_eq!(
            resolve_python3_with(Some("/opt/py/python3".into()), &local),
            "/opt/py/python3"
        );
        // Minimal PATH resolving to /usr/bin/python3 still picks executable local override.
        assert_eq!(
            resolve_python3_with(Some("/usr/bin/python3".into()), &local),
            local.to_string_lossy()
        );
        // Empty PATH still picks executable local override.
        assert_eq!(resolve_python3_with(None, &local), local.to_string_lossy());

        // Without a local override, falls back cleanly to /usr/bin/python3.
        let missing = root.join("missing-python3");
        assert_eq!(
            resolve_python3_with(Some("/usr/bin/python3".into()), &missing),
            "/usr/bin/python3"
        );
        assert_eq!(resolve_python3_with(None, &missing), "/usr/bin/python3");

        std::fs::remove_dir_all(&root).unwrap();
    }
}
