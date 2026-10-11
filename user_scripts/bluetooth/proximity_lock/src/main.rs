use clap::Parser;
use std::{path::PathBuf, time::Duration};
use tokio::signal::unix::{SignalKind, signal};
mod bluetooth;
mod config;
mod hypr;
mod notify;
mod state;
use bluetooth::{BluetoothWatcher, device_update, scan_available_devices};
use config::{Config, DeviceEntry};
use hypr::{execute_script_series, unlock_via_signal, wake_display_direct};
use notify::NotificationClient;
use state::{ActionRequired, StateTracker};

#[derive(Parser, Debug)]
#[command(
    name = "dusky_proximity",
    version,
    about = "Bluetooth proximity automation for Dusky / Hyprland"
)]
struct Args {
    /// TOML configuration (default: XDG_CONFIG_HOME/dusky/settings/bluetooth_proximity/config.toml)
    #[arg(short, long)]
    config: Option<PathBuf>,
    /// Scan for five seconds; show live RSSI and known devices
    #[arg(short, long)]
    scan: bool,
    /// Log live measurements and transitions without executing actions
    #[arg(short, long)]
    test: bool,
    /// Validate configuration and exit without Bluetooth or session actions
    #[arg(long)]
    check_config: bool,
    /// Create the TOML and optional hook scripts; preserve existing files
    #[arg(long, conflicts_with_all=["scan","test","check_config","device"])]
    init_config: bool,
    #[arg(short, long)]
    device: Option<String>,
    /// Departure threshold in dBm; overrides proximity_level
    #[arg(
        long,
        alias = "lock_rssi",
        alias = "far_rssi",
        allow_hyphen_values = true
    )]
    far_rssi: Option<i16>,
    /// Approach threshold in dBm; overrides proximity_level
    #[arg(
        long,
        alias = "unlock_rssi",
        alias = "near_rssi",
        allow_hyphen_values = true
    )]
    near_rssi: Option<i16>,
    #[arg(long)]
    timeout: Option<u64>,
    #[arg(long,value_parser=["auto","le","bredr"])]
    transport: Option<String>,
    /// Adapter object path; otherwise select a powered adapter automatically
    #[arg(long)]
    adapter: Option<String>,
    #[arg(long)]
    no_notify: bool,
}
/// BOOTTIME includes suspend, unlike Instant. Detect observation gaps after resume.
fn boot_time() -> Duration {
    let mut ts = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    let result = unsafe { libc::clock_gettime(libc::CLOCK_BOOTTIME, &mut ts) };
    assert_eq!(result, 0, "CLOCK_BOOTTIME unavailable");
    Duration::new(ts.tv_sec as u64, ts.tv_nsec as u32)
}
fn refresh_observation_clock(tracker: &mut StateTracker, previous: &mut Duration) {
    let now = boot_time();
    if now.saturating_sub(*previous) > Duration::from_secs(5) {
        eprintln!("[daemon] Observation gap/resume; resetting stale signal evidence");
        tracker.reset_observations();
    }
    *previous = now;
}
fn action(action: ActionRequired, c: &Config, dry_run: bool, notifier: &NotificationClient) {
    let (event, mac, name, rssi, body) = match action {
        ActionRequired::None => return,
        ActionRequired::Far {
            reason,
            rssi,
            device_name,
            mac,
        } => ("far", mac, device_name, rssi, reason),
        ActionRequired::Near {
            device_name,
            mac,
            rssi,
        } => (
            "near",
            mac,
            device_name,
            Some(rssi),
            format!("signal returned ({rssi} dBm)"),
        ),
    };
    println!("[ACTION] {event}: {name}: {body}");
    if dry_run {
        println!("[TEST] No actions executed");
        return;
    }
    // Keep hooks out of the observation loop. Display/notification I/O is also asynchronous.
    if event == "far" {
        execute_script_series(c.get_far_commands(), event, mac, name, rssi);
    } else {
        if c.direct_hyprlock_signal {
            unlock_via_signal();
        }
        execute_script_series(c.get_near_commands(), event, mac, name, rssi);
        if c.wake_display {
            let delay = c.dpms_settle_ms;
            tokio::spawn(async move {
                tokio::time::sleep(Duration::from_millis(delay)).await;
                wake_display_direct().await;
            });
        }
    }
    if c.notifications {
        notifier.notify("Dusky Proximity", &body, "bluetooth");
    }
}
#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    if args.init_config {
        let path = args.config.unwrap_or_else(Config::preferred_config_path);
        Config::initialize(&path)?;
        println!("Configuration and hooks ready: {}", path.display());
        return Ok(());
    }
    let mut c = if args.scan && args.config.is_none() {
        Config::default()
    } else {
        match Config::load(args.config.as_deref()) {
            Ok(c) => c,
            Err(e) => {
                eprintln!("[config] {e}");
                std::process::exit(2);
            }
        }
    };
    if let Err(e) = c.apply_level() {
        eprintln!("[config] {e}");
        std::process::exit(2);
    }
    if let Some(mac) = args.device {
        c.device_mac = mac.clone();
        c.devices = vec![DeviceEntry {
            mac,
            name: "Custom Device".into(),
            enabled: true,
            ..DeviceEntry::default()
        }];
    }
    if let Some(rssi) = args.far_rssi {
        c.lock_rssi = rssi;
    }
    if let Some(rssi) = args.near_rssi {
        c.unlock_rssi = rssi;
    }
    if let Some(secs) = args.timeout {
        c.signal_timeout_secs = secs;
    }
    if let Some(transport) = args.transport {
        c.transport = transport;
    }
    if let Some(adapter) = args.adapter {
        c.adapter_path = adapter;
    }
    if args.no_notify {
        c.notifications = false;
    }
    if args.scan {
        c.devices.clear();
        c.device_mac.clear();
        c.proximity_enabled = true;
        let devices = scan_available_devices(&c, Duration::from_secs(5)).await?;
        println!("#   MAC Address         Device Name                    Status      Live RSSI");
        for (idx, d) in devices.iter().enumerate() {
            let status = if d.connected {
                "Connected"
            } else if d.paired {
                "Paired"
            } else {
                "Discovered"
            };
            let strength = d
                .rssi
                .map(StateTracker::format_rssi_meter)
                .unwrap_or_else(|| "N/A (no live RSSI update)".into());
            println!(
                "{:<3} {:<19} {:<30} {:<11} {}",
                idx + 1,
                d.mac,
                d.name,
                status,
                strength
            );
        }
        return Ok(());
    }
    if let Err(e) = c.validate() {
        eprintln!("[config] {e}");
        std::process::exit(2);
    }
    if args.check_config {
        println!(
            "Configuration valid: {} enabled device(s), far < {} dBm, near >= {} dBm",
            c.active_devices().len(),
            c.lock_rssi,
            c.unlock_rssi
        );
        return Ok(());
    }
    if c.active_devices().is_empty() {
        println!(
            "[daemon] No enabled devices. Edit {} and restart dusky_proximity.service to activate.",
            args.config
                .as_deref()
                .map_or_else(Config::preferred_config_path, |p| p.to_path_buf())
                .display()
        );
        return Ok(());
    }
    let mut watcher = BluetoothWatcher::new(&c).await?;
    println!(
        "[daemon] Adapter {}; far < {} dBm held {}s, near >= {} dBm held {}s; loss {}s + hold {}s; median {}",
        watcher.adapter_path,
        c.lock_rssi,
        c.lock_hold_secs,
        c.unlock_rssi,
        c.unlock_hold_secs,
        c.signal_timeout_secs,
        c.lock_hold_secs,
        c.rssi_window
    );
    let notifier = NotificationClient::new(c.notifications && !args.test).await;
    let mut tracker = StateTracker::new(&c.active_devices());
    tracker.seed_connections(&watcher.initial_connections, &c);
    let adapter_prefix = format!("{}/", watcher.adapter_path);
    let mut tick = tokio::time::interval(Duration::from_millis(500));
    tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    let mut health = tokio::time::interval(Duration::from_secs(5));
    health.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    let mut term = signal(SignalKind::terminate())?;
    let mut previous_tick = boot_time();
    let result: Result<(),Box<dyn std::error::Error>> = async {
        loop {
            tokio::select! {
                msg = watcher.next_message() => {
                    let msg = msg?;
                    refresh_observation_clock(&mut tracker, &mut previous_tick);
                    let Some((path, iface, props)) = device_update(&msg) else { continue; };
                    if path == watcher.adapter_path && iface == "org.bluez.Adapter1" {
                        if (props.contains_key("Powered") || props.contains_key("Discovering")) && watcher.ensure_discovery().await? {
                            tracker.reset_observations();
                        }
                        continue;
                    }
                    if iface != "org.bluez.Device1" { continue; }
                    let Some(key) = path.strip_prefix(&adapter_prefix) else { continue; };
                    if !tracker.devices.contains_key(key) { continue; }
                    // Connection state is independent of RSSI reception or proximity policy.
                    if let Some(connected) = props.get("Connected").and_then(|v| bool::try_from(v).ok()) {
                        tracker.observe_connection(key, connected);
                        connection_actions(&mut tracker, &c, args.test, &notifier);
                    }
                    if !c.proximity_enabled || !tracker.devices[key].config.proximity { continue; }
                    if props.contains_key("ManufacturerData") || props.contains_key("ServiceData") {
                        tracker.heartbeat(key);
                        if args.test { println!("[TEST] {} advertisement heartbeat", tracker.devices[key].config.name); }
                    }
                    if let Some(rssi) = props.get("RSSI").and_then(|v| i16::try_from(v).ok()) {
                        let previous_state = tracker.current_state;
                        let a = tracker.handle_rssi_sample(key, rssi, &c);
                        if args.test {
                            let d = &tracker.devices[key];
                            println!("[TEST] {} raw={rssi} filtered={:?} state={:?}", d.config.name, d.filtered_rssi, tracker.current_state);
                        }
                        if matches!(a, ActionRequired::Far { .. }) && watcher.ensure_discovery().await? {
                            tracker.current_state = previous_state;
                            tracker.reset_observations();
                            continue;
                        }
                        action(a, &c, args.test, &notifier);
                    }
                }
                _ = tick.tick() => {
                    refresh_observation_clock(&mut tracker, &mut previous_tick);
                    connection_actions(&mut tracker, &c, args.test, &notifier);
                    let previous_state = tracker.current_state;
                    let a = tracker.check_timeouts(&c);
                    if matches!(a, ActionRequired::Far { .. }) && watcher.ensure_discovery().await? {
                        tracker.current_state = previous_state;
                        tracker.reset_observations();
                        continue;
                    }
                    action(a, &c, args.test, &notifier);
                }
                _ = health.tick() => {
                    if watcher.ensure_discovery().await? { tracker.reset_observations(); }
                }
                _ = tokio::signal::ctrl_c() => { break; }
                _ = term.recv() => { break; }
            }
        }
        Ok(())
    }.await;
    watcher.stop_discovery().await;
    result
}
fn connection_actions(
    tracker: &mut StateTracker,
    c: &Config,
    dry_run: bool,
    notifier: &NotificationClient,
) {
    for (key, connected) in tracker.connection_actions(c) {
        let d = &tracker.devices[&key].config;
        let event = if connected { "connect" } else { "disconnect" };
        println!("[bluetooth] {} {event}", d.name);
        if dry_run {
            println!("[TEST] No {event} hooks executed");
            continue;
        }
        execute_script_series(
            c.connection_commands(d, connected),
            event,
            d.mac.clone(),
            d.name.clone(),
            None,
        );
        if c.notifications {
            notifier.notify(
                "Dusky Bluetooth",
                &format!("{}: {event}", d.name),
                "bluetooth",
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    async fn wait_content(path: &std::path::Path, expected: &str) {
        tokio::time::timeout(Duration::from_secs(3), async {
            loop {
                if std::fs::read_to_string(path).is_ok_and(|s| s == expected) {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(1)).await;
            }
        })
        .await
        .expect("hook did not produce its expected output");
    }
    #[tokio::test]
    async fn phone_connection_hooks_execute_with_correct_environment_without_proximity() {
        let tmp = tempfile::tempdir().unwrap();
        let path = tmp.path().join("events");
        let quoted = format!("'{}'", path.to_string_lossy().replace('\'', "'\\''"));
        let cmd = format!(
            "printf '%s|%s|%s|%s\\n' \"$PROXIMITY_EVENT\" \"$PROXIMITY_STATE\" \"$PROXIMITY_DEVICE_MAC\" \"$PROXIMITY_RSSI\" >> {quoted}"
        );
        let c = Config {
            proximity_enabled: false,
            notifications: false,
            disconnect_hold_secs: 0,
            on_connect: config::CommandList::Single(cmd.clone()),
            on_disconnect: config::CommandList::Single(cmd),
            device_mac: "AA:BB:CC:DD:EE:FF".into(),
            ..Config::default()
        };
        let mut tracker = StateTracker::new(&c.active_devices());
        tracker.seed_connections(&std::collections::HashMap::new(), &c);
        let notifier = NotificationClient::new(false).await;
        tracker.observe_connection("dev_AA_BB_CC_DD_EE_FF", true);
        connection_actions(&mut tracker, &c, false, &notifier);
        let first = "connect|UNKNOWN|AA:BB:CC:DD:EE:FF|N/A\n";
        wait_content(&path, first).await;
        tracker.observe_connection("dev_AA_BB_CC_DD_EE_FF", false);
        connection_actions(&mut tracker, &c, false, &notifier);
        wait_content(
            &path,
            &format!("{first}disconnect|UNKNOWN|AA:BB:CC:DD:EE:FF|N/A\n"),
        )
        .await;
        assert_eq!(tracker.check_timeouts(&c), ActionRequired::None);
    }
}
