//! Hardware + status backend.
//!
//! Rust port of `dusky_backend.py`. All *writes* go to [`runtime_dir`]
//! (tmpfs: `$XDG_RUNTIME_DIR`, `/dev/shm`, or `/tmp`) so the SSD never sees
//! cache churn. Reads may touch `/proc`, `/sys`, and SSD config files.

use crate::backend::cmd::{CmdOutput, atomic_write_text, run_command};
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

/// Lock this window's preference so config reloads cannot discard it or let
/// workspace rules override it. Global blur.enabled still controls rendering.
/// Call only after the panel has mapped (its first successful fit).
pub fn configure_panel_blur(enabled: bool) {
    let rule = format!(
        "hl.dispatch(hl.dsp.window.set_prop({{window=\"class:^dusky_tray$\",prop=\"no_blur\",value=\"{}\",lock=true}}))",
        !enabled
    );
    let reply = run_command(
        &["hyprctl".into(), "eval".into(), rule],
        Duration::from_millis(800),
        true,
    );
    if !reply.is_some_and(|reply| reply.status && reply.stdout.trim() == "ok") {
        eprintln!("Could not apply the Hyprland tray blur preference");
    }
}

/// Read the compositor's actual blur switch, rather than the shared Visuals
/// indicator (which can stay true for shadows or other enabled effects).
/// Use Hyprland's request socket to avoid spawning a process every refresh.
pub fn global_blur_enabled() -> Option<bool> {
    use std::io::{Read, Write};
    use std::os::unix::net::UnixStream;

    let signature = std::env::var_os("HYPRLAND_INSTANCE_SIGNATURE")?;
    let socket = shared_runtime_dir()
        .join("hypr")
        .join(signature)
        .join(".socket.sock");
    let mut stream = UnixStream::connect(socket).ok()?;
    let timeout = Some(Duration::from_millis(800));
    stream.set_read_timeout(timeout).ok()?;
    stream.set_write_timeout(timeout).ok()?;
    stream
        .write_all(b"j/getoption decoration:blur:enabled")
        .ok()?;
    let mut response = String::new();
    stream.read_to_string(&mut response).ok()?;
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
    let result = run_command(
        &["hyprctl".into(), "-j".into(), "monitors".into()],
        Duration::from_millis(800),
        true,
    )?;
    let monitors: serde_json::Value = serde_json::from_str(&result.stdout).ok()?;
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
        "hl.dispatch(hl.dsp.window.resize({{window=\"class:^dusky_tray$\",x={},y={},relative=false}})); hl.dispatch(hl.dsp.window.move({{window=\"class:^dusky_tray$\",x={},y={},relative=false}}))",
        size.width,
        size.height,
        monitor.x + monitor.size.width - size.width - 20.0,
        monitor.y + monitor.size.height - size.height - 20.0
    );
    let result = run_command(
        &["hyprctl".into(), "eval".into(), actions],
        Duration::from_millis(800),
        true,
    );
    let success = result.is_some_and(|result| result.status && result.stdout.trim() == "ok");
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

fn which(bin: &str) -> Option<String> {
    std::env::var_os("PATH").and_then(|paths| {
        std::env::split_paths(&paths).find_map(|dir| {
            let p = dir.join(bin);
            if p.is_file() && access_ok(&p, libc::X_OK) {
                return Some(p.to_string_lossy().into_owned());
            }
            None
        })
    })
}

// ---------------------------------------------------------------------------
// clock
// ---------------------------------------------------------------------------

pub fn current_time_date() -> (String, String) {
    let now = chrono::Local::now();
    (
        now.format("%I:%M").to_string(),
        now.format("%A, %B %d").to_string(),
    )
}

// ---------------------------------------------------------------------------
// volume (wpctl)
// ---------------------------------------------------------------------------

pub fn get_volume() -> Option<f32> {
    let wpctl = which("wpctl")?;
    let out = run_command(
        &[wpctl, "get-volume".into(), "@DEFAULT_AUDIO_SINK@".into()],
        Duration::from_millis(800),
        true,
    )?;
    if !out.status {
        return None;
    }
    let parts: Vec<&str> = out.stdout.split_whitespace().collect();
    if parts.len() < 2 {
        return None;
    }
    let v = parse_float(parts[1])?;
    Some(clamp(v * 100.0, 0.0, 100.0))
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

fn ddc_buses() -> Vec<i32> {
    let Some(ddc) = which("ddcutil") else {
        return Vec::new();
    };
    // Prefer tmpfs cache.
    let cache = ddc_cache_file();
    let mut buses = Vec::new();
    if let Ok(txt) = std::fs::read_to_string(&cache)
        && let Ok(v) = serde_json::from_str::<serde_json::Value>(&txt)
        && let Some(arr) = v.as_array()
    {
        for item in arr {
            let b = item
                .get("bus")
                .and_then(|x| x.as_i64())
                .or_else(|| item.as_i64())
                .unwrap_or(-1);
            if b >= 0 {
                buses.push(b as i32);
            }
        }
    }
    // Discovery is slow; reuse a successful cache for 45 seconds, including
    // empty scans, so dragging never runs detect for every new value.
    if cache
        .metadata()
        .ok()
        .and_then(|m| m.modified().ok())
        .and_then(|t| t.elapsed().ok())
        .is_some_and(|age| age < Duration::from_secs(45))
    {
        return buses;
    }
    let out = run_command(
        &[ddc.clone(), "detect".into(), "--terse".into()],
        Duration::from_secs(8),
        true,
    );
    if let Some(out) = out
        && out.status
    {
        let fresh = parse_detect_buses(&out.stdout);
        {
            let records: Vec<serde_json::Value> = fresh
                .iter()
                .map(|b| serde_json::json!({"bus": b, "max": 100}))
                .collect();
            let _ = atomic_write_text(
                &cache,
                &format!("{}\n", serde_json::to_string(&records).unwrap_or_default()),
            );
            return fresh;
        }
    }
    buses
}

pub fn ddc_current_percent() -> Option<f32> {
    let ddc = which("ddcutil")?;
    for bus in ddc_buses() {
        let out = run_command(
            &[
                ddc.clone(),
                "getvcp".into(),
                "10".into(),
                "--terse".into(),
                "--bus".into(),
                bus.to_string(),
            ],
            Duration::from_secs(2),
            true,
        );
        let Some(out) = out else {
            continue;
        };
        if out.status
            && let Some((cur, max)) = parse_getvcp(&out.stdout)
        {
            return Some(clamp(cur as f32 / max.max(1) as f32 * 100.0, 0.0, 100.0));
        }
    }
    None
}

pub fn apply_ddc_brightness(value: f32) {
    let Some(ddc) = which("ddcutil") else { return };
    let pct = percent_int(value, 1);
    for bus in ddc_buses() {
        let Some(out) = run_command(
            &[
                ddc.clone(),
                "getvcp".into(),
                "10".into(),
                "--terse".into(),
                "--bus".into(),
                bus.to_string(),
            ],
            Duration::from_secs(2),
            true,
        ) else {
            continue;
        };
        if !out.status {
            continue;
        }
        let Some((_, maximum)) = parse_getvcp(&out.stdout) else {
            continue;
        };
        let raw = ((pct as f64 / 100.0) * maximum as f64).round() as i32;
        let raw = raw.clamp(1, maximum);
        let _ = run_command(
            &[
                ddc.clone(),
                "setvcp".into(),
                "10".into(),
                raw.to_string(),
                "--bus".into(),
                bus.to_string(),
            ],
            Duration::from_millis(2200),
            false,
        );
    }
}

// ---------------------------------------------------------------------------
// hyprsunset (night light)
// ---------------------------------------------------------------------------

pub fn has_sunset() -> bool {
    which("hyprctl").is_some()
        && which("hyprsunset").is_some()
        && std::env::var_os("HYPRLAND_INSTANCE_SIGNATURE").is_some()
}

pub fn is_sunset_service_enabled() -> bool {
    let Some(sys) = which("systemctl") else {
        return false;
    };
    let out = run_command(
        &[
            sys,
            "--user".into(),
            "is-enabled".into(),
            "hyprsunset.service".into(),
        ],
        Duration::from_millis(500),
        true,
    );
    out.map(|o| o.status).unwrap_or(false)
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
    if let Some(ctl) = which("hyprctl")
        && let Some(out) = run_command(
            &[ctl, "hyprsunset".into(), "identity".into(), "get".into()],
            Duration::from_millis(800),
            true,
        )
        && out.status
        && out.stdout.trim() == "true"
    {
        return Some(0.0);
    }
    // Read the running backend so GTK changes and keybinds are reflected on
    // first open, rather than presenting a stale Rust-only cache.
    if let Some(ctl) = which("hyprctl")
        && let Some(out) = run_command(
            &[ctl, "hyprsunset".into(), "temperature".into()],
            Duration::from_millis(800),
            true,
        )
        && out.status
        && let Some(v) = parse_float(&out.stdout)
    {
        return Some(sunset_percent(v));
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
        vec!["hyprsunset".into(), "identity".into()]
    } else {
        vec![
            "hyprsunset".into(),
            "temperature".into(),
            target.to_string(),
        ]
    };
    if let Some(hyprctl) = which("hyprctl") {
        let ok = run_command(
            &[vec![hyprctl], request.clone()].concat(),
            Duration::from_millis(800),
            false,
        )
        .map(|o| o.status)
        .unwrap_or(false);
        if ok {
            let _ = atomic_write_text(&sunset_state_file(), &format!("{target}\n"));
            return;
        }
    }
    // Fallback: ensure service / process, then retry once.
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
    if let (Some(hyprctl), Some(_)) = (which("hyprctl"), which("hyprsunset")) {
        std::thread::sleep(Duration::from_millis(300));
        let ok = run_command(
            &[vec![hyprctl], request.clone()].concat(),
            Duration::from_millis(800),
            false,
        )
        .map(|o| o.status)
        .unwrap_or(false);
        if ok {
            let _ = atomic_write_text(&sunset_state_file(), &format!("{target}\n"));
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
    let home = std::env::var("HOME").unwrap_or_default();
    let p = Path::new(&home).join(".config/dusky/settings/tlp_state");
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

fn parse_wifi_connected(status: &str) -> bool {
    status.lines().any(|line| {
        matches!(
            line.split_once(':'),
            Some(("wifi", "connected" | "connected (externally)"))
        )
    })
}

pub fn wifi_connected() -> Option<bool> {
    let nmcli = which("nmcli")?;
    let out = run_command(
        &[
            nmcli,
            "-t".into(),
            "-f".into(),
            "TYPE,STATE".into(),
            "device".into(),
            "status".into(),
        ],
        Duration::from_millis(800),
        true,
    )?;
    out.status.then(|| parse_wifi_connected(&out.stdout))
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
    pub tooltip: String,
}

impl Default for CpuState {
    fn default() -> Self {
        Self {
            text: "--".into(),
            power_watts: None,
            tooltip: "CPU: -- · Power: N/A\nLMB: Open btop".into(),
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

pub fn cpu_ram_state() -> (CpuState, RamState) {
    let mut cpu_text = "--".to_owned();
    if let Ok(first) = std::fs::read_to_string("/proc/stat") {
        // Store sample in tmpfs so deltas survive without SSD writes.
        let sample_file = runtime_dir().join("cpu_sample.txt");
        let parse = |txt: &str| -> Option<(u64, u64)> {
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
        };
        if let Some((idle, total)) = parse(&first) {
            if let Ok(prev_txt) = std::fs::read_to_string(&sample_file) {
                let mut it = prev_txt
                    .split_whitespace()
                    .filter_map(|x| x.parse::<u64>().ok());
                if let (Some(pi), Some(pt)) = (it.next(), it.next()) {
                    let di = idle.saturating_sub(pi);
                    let dt = total.saturating_sub(pt);
                    if dt > 0 {
                        let usage = 100.0 * (1.0 - di as f64 / dt as f64);
                        cpu_text = format!("{:.0}%", usage.clamp(0.0, 100.0));
                    }
                }
            }
            let _ = atomic_write_text(&sample_file, &format!("{idle} {total}\n"));
        }
    }

    let power_watts = sample_cpu_power_watts();
    let power_str = match power_watts {
        Some(w) => format!("{w:.1} W"),
        None => "N/A".to_owned(),
    };
    let cpu = CpuState {
        tooltip: format!("CPU: {cpu_text} · Power: {power_str}\nLMB: Open btop"),
        text: cpu_text,
        power_watts,
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
        };
    }
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
        "Upload: {:.1} {unit}/s\nDownload: {:.1} {unit}/s",
        tx / scale,
        rx / scale
    );
    append_warp_status(&mut tooltip, warp);
    NetState {
        text: format!("{:.0} {unit} {:.0}", tx / scale, rx / scale),
        tooltip,
        disconnected: false,
        warp,
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

pub fn weather_text() -> Option<String> {
    let home = std::env::var("HOME").unwrap_or_default();
    let p = PathBuf::from(format!("{home}/.config/dusky/settings/waybar_weather"));
    if let Ok(txt) = std::fs::read_to_string(p)
        && let Ok(raw) = serde_json::from_str::<serde_json::Value>(&txt)
    {
        let data = if raw.get("version").and_then(|v| v.as_i64()) == Some(2) {
            raw.get("payload").cloned().unwrap_or_default()
        } else {
            raw
        };
        if let Some(t) = data.get("text").and_then(|x| x.as_str())
            && !t.trim().is_empty()
        {
            return Some(t.trim().to_owned());
        }
    }
    None
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
    let home = std::env::var("HOME").unwrap_or_default();
    let base = Path::new(&home).join(".config/dusky/settings");
    parse_updates(
        &std::fs::read_to_string(base.join("waybar_update_counter_h")).unwrap_or_default(),
        &std::fs::read_to_string(base.join("dusky_update_behind_commit")).unwrap_or_default(),
    )
}

pub fn animations_enabled() -> Option<bool> {
    let out = run_command(
        &[
            "hyprctl".into(),
            "getoption".into(),
            "animations:enabled".into(),
            "-j".into(),
        ],
        Duration::from_millis(800),
        true,
    )?;
    if !out.status {
        return None;
    }
    serde_json::from_str::<serde_json::Value>(&out.stdout)
        .ok()?
        .get("bool")?
        .as_bool()
}

pub fn is_idle_active() -> Option<bool> {
    let pgrep = which("pgrep")?;
    let out = run_command(
        &[
            pgrep,
            "-u".into(),
            unsafe { libc::getuid() }.to_string(),
            "-x".into(),
            "hypridle".into(),
        ],
        Duration::from_millis(800),
        false,
    )?;
    Some(out.status)
}

pub fn is_blur_on() -> Option<bool> {
    let home = std::env::var("HOME").unwrap_or_default();
    let txt =
        std::fs::read_to_string(format!("{home}/.config/dusky/settings/opacity_blur")).ok()?;
    Some(txt.trim().eq_ignore_ascii_case("true"))
}

pub fn is_audio_active() -> bool {
    let home = std::env::var("HOME").unwrap_or_default();
    let pid_file = format!("{home}/.config/dusky/settings/dusky_studio/daemon.pid");
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
    fn wifi_connection_distinguishes_radio_enabled_from_connected() {
        assert!(!parse_wifi_connected(
            "ethernet:connected\nwifi:disconnected\nwifi-p2p:connected"
        ));
        assert!(!parse_wifi_connected("wifi:connecting (prepare)"));
        assert!(parse_wifi_connected("wifi:disconnected\nwifi:connected"));
        assert!(parse_wifi_connected("wifi:connected (externally)"));
        assert!(!parse_wifi_connected(""));
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
        assert_eq!(
            reading.tooltip,
            "Upload: 3.0 KB/s\nDownload: 3.0 KB/s\nCloudflare WARP: Connected (ON)"
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
        assert_eq!(
            reading_off.tooltip,
            "Upload: 1.0 MB/s\nDownload: 1.0 MB/s\nCloudflare WARP: Disconnected (OFF)"
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
            "Upload: 1.0 MB/s\nDownload: 1.0 MB/s"
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
        assert_eq!(
            detect_warp_status_in(&root, true),
            WarpStatus::Disconnected
        );

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
        assert_eq!(
            detect_warp_status_in(&root, true),
            WarpStatus::Disconnected
        );

        // Operstate "unknown" but IFF_UP cleared (0x1090) -> Disconnected
        std::fs::write(warp.join("operstate"), "unknown\n").unwrap();
        std::fs::write(warp.join("flags"), "0x1090\n").unwrap();
        assert_eq!(
            detect_warp_status_in(&root, true),
            WarpStatus::Disconnected
        );

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
}
