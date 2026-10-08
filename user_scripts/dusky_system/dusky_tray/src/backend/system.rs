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
/// Priority: `$XDG_RUNTIME_DIR/dusky-tray` (tmpfs) → `/dev/shm/...` →
/// `/tmp/...`. Never falls back to `$HOME` so cache files cannot wear the SSD.
pub fn runtime_dir() -> PathBuf {
    let candidates = [
        std::env::var_os("XDG_RUNTIME_DIR").map(PathBuf::from),
        Some(PathBuf::from(format!("/run/user/{}", unsafe {
            libc::getuid()
        }))),
        Some(PathBuf::from("/dev/shm")),
        Some(std::env::temp_dir()),
    ];
    for base in candidates.into_iter().flatten() {
        // /dev/shm and /tmp need a per-user subdir.
        let dir = if base == *"/dev/shm" || base == std::env::temp_dir() {
            base.join(format!("dusky-tray-{}", unsafe { libc::getuid() }))
        } else {
            base.join("dusky-tray")
        };
        if std::fs::create_dir_all(&dir).is_ok() && dir.is_dir() {
            return dir;
        }
    }
    std::env::temp_dir().join("dusky-tray-fallback")
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

/// Hyprland's named Lua rules replace themselves instead of accumulating.
/// The alpha mask excludes the fullscreen click catcher from layer blur.
pub fn configure_panel_blur(enabled: bool) {
    let rule = format!(
        "hl.layer_rule({{name=\"dusky_tray\",match={{namespace=\"dusky-tray\"}},blur={enabled},xray=false,ignore_alpha=0.1}})"
    );
    let reply = run_command(
        &["hyprctl".into(), "eval".into(), rule],
        Duration::from_millis(800),
        true,
    );
    if !reply.is_some_and(|reply| reply.status && reply.stdout.trim() == "ok") {
        eprintln!("Could not apply the Hyprland tray blur rule");
    }
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

pub fn has_volume() -> bool {
    which("wpctl").is_some()
}

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
    for d in sysfs_backlights() {
        if access_ok(&d.path.join("brightness"), libc::W_OK) {
            return Some(d);
        }
    }
    sysfs_backlights().into_iter().next()
}

pub fn has_local_brightness() -> bool {
    preferred_backlight().is_some()
        && (which("brightnessctl").is_some()
            || which("busctl").is_some()
            || sysfs_backlights()
                .iter()
                .any(|d| access_ok(&d.path.join("brightness"), libc::W_OK)))
}

pub fn has_ddc_brightness() -> bool {
    which("ddcutil").is_some()
}

pub fn has_brightness() -> bool {
    has_local_brightness() || has_ddc_brightness()
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

pub fn cpu_ram() -> (String, String) {
    // CPU from /proc/stat delta is stateful; here we return instantaneous
    // companion: the UI keeps the previous sample for the delta.
    let mut cpu = "--".to_owned();
    let mut ram = "--".to_owned();
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
                        cpu = format!("{:.0}%", usage.clamp(0.0, 100.0));
                    }
                }
            }
            let _ = atomic_write_text(&sample_file, &format!("{idle} {total}\n"));
        }
    }
    if let Ok(mem) = std::fs::read_to_string("/proc/meminfo") {
        let (mut tot, mut av) = (0u64, 0u64);
        for line in mem.lines() {
            if line.starts_with("MemTotal:") {
                tot = line
                    .split_whitespace()
                    .nth(1)
                    .and_then(|x| x.parse().ok())
                    .unwrap_or(0);
            } else if line.starts_with("MemAvailable:") {
                av = line
                    .split_whitespace()
                    .nth(1)
                    .and_then(|x| x.parse().ok())
                    .unwrap_or(0);
            }
            if tot > 0 && av > 0 {
                break;
            }
        }
        if tot > 0 {
            ram = format!("{:.1} GB", (tot.saturating_sub(av)) as f64 / 1_048_576.0);
        }
    }
    (cpu, ram)
}

#[derive(Debug, Clone, Default)]
pub struct NetState {
    pub text: String,
    pub tooltip: String,
    pub disconnected: bool,
}

fn network_totals(text: &str, interfaces: &[String]) -> (u64, u64) {
    let mut rx = 0u64;
    let mut tx = 0u64;
    for line in text.lines() {
        let Some((name, counters)) = line.split_once(':') else {
            continue;
        };
        if !interfaces.iter().any(|i| i == name.trim()) {
            continue;
        }
        let mut values = counters.split_whitespace();
        rx = rx.saturating_add(
            values
                .next()
                .and_then(|v| v.parse::<u64>().ok())
                .unwrap_or(0),
        );
        tx = tx.saturating_add(
            values
                .nth(7)
                .and_then(|v| v.parse::<u64>().ok())
                .unwrap_or(0),
        );
    }
    (rx, tx)
}

pub fn net_state() -> NetState {
    // Physical interfaces avoid double-counting VPN/bridge traffic. Virtio
    // devices also expose a device link. No Waybar helper or daemon is needed.
    let interfaces: Vec<String> = std::fs::read_dir("/sys/class/net")
        .into_iter()
        .flatten()
        .flatten()
        .filter(|e| e.path().join("device").exists())
        .filter(|e| {
            std::fs::read_to_string(e.path().join("operstate"))
                .is_ok_and(|s| s.trim() == "up" || s.trim() == "unknown")
        })
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .collect();
    let totals = network_totals(
        &std::fs::read_to_string("/proc/net/dev").unwrap_or_default(),
        &interfaces,
    );
    type Sample = (std::time::Instant, (u64, u64), Vec<String>);
    static SAMPLE: std::sync::Mutex<Option<Sample>> = std::sync::Mutex::new(None);
    let now = std::time::Instant::now();
    let mut sample = SAMPLE.lock().unwrap();
    let (rx, tx) = match sample.as_ref() {
        Some((then, previous, previous_interfaces)) if previous_interfaces == &interfaces => {
            let seconds = now.duration_since(*then).as_secs_f64().max(0.001);
            (
                totals.0.saturating_sub(previous.0) as f64 / seconds,
                totals.1.saturating_sub(previous.1) as f64 / seconds,
            )
        }
        _ => (0.0, 0.0),
    };
    *sample = Some((now, totals, interfaces.clone()));
    let (scale, unit) = if rx.max(tx) >= 1_048_576.0 {
        (1_048_576.0, "MB")
    } else {
        (1024.0, "KB")
    };
    NetState {
        text: if interfaces.is_empty() {
            "--".into()
        } else {
            format!("{:.0} {unit} {:.0}", tx / scale, rx / scale)
        },
        tooltip: if interfaces.is_empty() {
            "Disconnected".into()
        } else {
            format!(
                "Upload: {:.1} {unit}/s\nDownload: {:.1} {unit}/s",
                tx / scale,
                rx / scale
            )
        },
        disconnected: interfaces.is_empty(),
    }
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
    let unknown = counts.iter().any(Option::is_none);
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
        badge: if total > 0 {
            total.to_string()
        } else if unknown {
            "?".into()
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
        assert_eq!(parse_updates("{}", "").badge, "?");
        assert_eq!(
            parse_updates(r#"{"counts":{"pacman":0,"aur":0}}"#, "0").badge,
            ""
        );
    }

    #[test]
    fn network_counters_select_interfaces_and_directions() {
        let text = "lo: 900 0 0 0 0 0 0 0 900 0\nwlan0: 2048 0 0 0 0 0 0 0 1024 0\ntun0: 2048 0 0 0 0 0 0 0 1024 0";
        assert_eq!(network_totals(text, &["wlan0".into()]), (2048, 1024));
        assert_eq!(network_totals(text, &[]), (0, 0));
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
        let s = d.to_string_lossy();
        assert!(
            s.starts_with("/run/user/") || s.starts_with("/dev/shm") || s.starts_with("/tmp"),
            "runtime dir must be tmpfs, got {s}"
        );
        assert!(!s.starts_with("/home/"), "must not be on SSD home");
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
}
