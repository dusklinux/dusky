//! Mako notifications: list/history merge, blacklist, ignored apps, DND.
//!
//! Mirrors `dusky_backend.fetch_notifications` + `NotificationsPanel`.
//! Blacklist + time cache live in tmpfs ([`crate::backend::system`]).

use crate::backend::cmd::run_command;
use crate::backend::system::{mako_blacklist_file, notif_times_file};
use std::collections::{BTreeMap, HashSet};
use std::path::PathBuf;
use std::time::Duration;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Notification {
    pub id: i32,
    pub app: String,
    pub summary: String,
    pub body: String,
    pub source: String,
    pub desktop_entry: String,
    pub time: String,
}

pub fn ignored_apps_file() -> PathBuf {
    if let Ok(home) = std::env::var("HOME") {
        let p = PathBuf::from(format!(
            "{home}/user_scripts/dusky_system/quickpanal/ignored_apps.toml"
        ));
        if p.is_file() {
            return p;
        }
    }
    // Installed defaults live beside the binary; source-tree builds used bin/.
    if let Ok(exe) = std::env::current_exe()
        && let Some(directory) = exe.parent()
    {
        for base in [Some(directory), directory.parent()].into_iter().flatten() {
            let p = base.join("ignored_apps.toml");
            if p.is_file() {
                return p;
            }
        }
    }
    PathBuf::from("ignored_apps.toml")
}

pub fn load_ignored_apps() -> HashSet<String> {
    let path = ignored_apps_file();
    let Ok(txt) = std::fs::read_to_string(path) else {
        return HashSet::new();
    };
    let Ok(table) = txt.parse::<toml::Table>() else {
        return HashSet::new();
    };
    let mut out = HashSet::new();
    if let Some(ignored) = table.get("ignored").and_then(|v| v.as_table())
        && let Some(apps) = ignored.get("apps").and_then(|v| v.as_array())
    {
        for a in apps.iter().filter_map(|v| v.as_str()) {
            out.insert(a.to_owned());
        }
    }
    out
}

pub fn is_ignored(app: &str, ignored: &HashSet<String>) -> bool {
    if app.is_empty() {
        return false;
    }
    if ignored.contains(app) {
        return true;
    }
    for item in ignored {
        if let Some(prefix) = item.strip_suffix('*')
            && app.starts_with(prefix)
        {
            return true;
        }
    }
    false
}

fn read_blacklist() -> HashSet<String> {
    std::fs::read_to_string(mako_blacklist_file())
        .map(|t| t.lines().map(|l| l.to_owned()).collect())
        .unwrap_or_default()
}

fn mako_query(args: &[&str]) -> Vec<serde_json::Value> {
    let argv: Vec<String> = std::iter::once("makoctl".to_owned())
        .chain(args.iter().map(|s| s.to_string()))
        .collect();
    let Some(out) = run_command(&argv, Duration::from_millis(800), true) else {
        return Vec::new();
    };
    if !out.status {
        return Vec::new();
    }
    let Ok(parsed) = serde_json::from_str::<serde_json::Value>(out.stdout.trim()) else {
        return Vec::new();
    };
    let mut items = parsed.get("data").cloned().unwrap_or(parsed);
    // makoctl -j sometimes nests one list level.
    if let Some(arr) = items.as_array()
        && arr.len() == 1
        && arr[0].is_array()
    {
        items = arr[0].clone();
    }
    items.as_array().cloned().unwrap_or_default()
}

fn clean_body(body: &str) -> String {
    // Strip simple HTML tags like the GTK version.
    let mut out = String::with_capacity(body.len());
    let mut in_tag = false;
    for c in body.chars() {
        match c {
            '<' => in_tag = true,
            '>' => in_tag = false,
            _ if !in_tag => out.push(c),
            _ => {}
        }
    }
    out.replace('\n', " ").trim().to_owned()
}

/// Fetch up to 50 notifications, history first then active overrides.
pub fn fetch_notifications() -> Vec<Notification> {
    let blacklist = read_blacklist();
    let ignored = load_ignored_apps();
    let active_items = mako_query(&["list", "-j"]);
    let history_items = mako_query(&["history", "-j"]);

    let mut combined: BTreeMap<i32, Notification> = BTreeMap::new();
    // Time cache (dusky_notif_time service) lives on tmpfs.
    let times: std::collections::HashMap<String, String> =
        std::fs::read_to_string(notif_times_file())
            .ok()
            .and_then(|t| serde_json::from_str(&t).ok())
            .unwrap_or_default();

    for (source, items) in [("history", history_items), ("active", active_items)] {
        for item in items {
            let id = item.get("id").and_then(|v| v.as_i64()).unwrap_or(-1) as i32;
            if id < 0 || blacklist.contains(&id.to_string()) {
                continue;
            }
            let app = item
                .get("app_name")
                .or_else(|| item.get("app-name"))
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_owned();
            if is_ignored(&app, &ignored) {
                continue;
            }
            let summary = item.get("summary").and_then(|v| v.as_str()).unwrap_or("");
            if summary.is_empty() {
                continue;
            }
            combined.insert(
                id,
                Notification {
                    id,
                    app,
                    summary: summary.to_owned(),
                    body: clean_body(item.get("body").and_then(|v| v.as_str()).unwrap_or("")),
                    source: source.to_owned(),
                    desktop_entry: item
                        .get("desktop_entry")
                        .or_else(|| item.get("desktop-entry"))
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_owned(),
                    time: times.get(&id.to_string()).cloned().unwrap_or_default(),
                },
            );
        }
    }
    let mut out: Vec<Notification> = combined.into_values().collect();
    out.sort_by_key(|n| -n.id);
    out.truncate(50);
    out
}

/// Group by app for the stacked view (mirrors GTK grouping).
pub fn group_notifications(notifs: &[Notification]) -> Vec<(String, Vec<Notification>)> {
    let mut groups: Vec<(String, Vec<Notification>)> = Vec::new();
    for n in notifs {
        let app = if n.app.is_empty() {
            "Unknown".into()
        } else {
            n.app.clone()
        };
        if let Some(g) = groups.iter_mut().find(|(a, _)| *a == app) {
            g.1.push(n.clone());
        } else {
            groups.push((app, vec![n.clone()]));
        }
    }
    groups
}

pub fn dismiss(id: i32) {
    append_blacklist(id);
    let _ = run_command(
        &[
            "makoctl".into(),
            "dismiss".into(),
            "-n".into(),
            id.to_string(),
        ],
        Duration::from_millis(800),
        false,
    );
}

pub fn invoke(notification: Notification) {
    if notification.source == "active" {
        invoke_default(notification.id);
        return;
    }
    // History entries no longer have a callable Mako action. Resolve their
    // desktop entry and launch it through GIO (no GTK dependency in the panel).
    append_blacklist(notification.id);
    let name = if notification.desktop_entry.is_empty() {
        &notification.app
    } else {
        &notification.desktop_entry
    };
    if name.is_empty() || name == "notify-send" || name == "mako" {
        return;
    }
    let filename = if name.ends_with(".desktop") {
        name.clone()
    } else {
        format!("{name}.desktop")
    };
    let mut dirs = Vec::new();
    if let Some(home) = std::env::var_os("HOME") {
        dirs.push(
            std::env::var_os("XDG_DATA_HOME")
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from(home).join(".local/share")),
        );
    }
    let shared =
        std::env::var_os("XDG_DATA_DIRS").unwrap_or_else(|| "/usr/local/share:/usr/share".into());
    dirs.extend(std::env::split_paths(&shared));
    let mut entry = dirs
        .iter()
        .map(|dir| dir.join("applications").join(&filename))
        .find(|p| p.is_file());
    if entry.is_none() {
        // Mako's app_name can be a display name rather than a desktop ID.
        for dir in &dirs {
            let Ok(entries) = std::fs::read_dir(dir.join("applications")) else {
                continue;
            };
            for candidate in entries
                .flatten()
                .map(|e| e.path())
                .filter(|p| p.extension().is_some_and(|e| e == "desktop"))
            {
                if std::fs::read_to_string(&candidate).is_ok_and(|text| {
                    text.lines().any(|l| {
                        l.strip_prefix("Name=")
                            .is_some_and(|v| v.eq_ignore_ascii_case(name))
                    })
                }) {
                    entry = Some(candidate);
                    break;
                }
            }
            if entry.is_some() {
                break;
            }
        }
    }
    if let Some(path) = entry {
        use std::os::unix::process::CommandExt;
        let mut command = std::process::Command::new("systemd-run");
        command
            .args([
                "--user",
                "--scope",
                "--collect",
                "--quiet",
                "--",
                "gio",
                "launch",
            ])
            .arg(path)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .process_group(0);
        if let Ok(mut child) = command.spawn() {
            let _ = child.wait();
        }
    }
}

pub fn invoke_default(id: i32) {
    append_blacklist(id);
    let _ = run_command(
        &[
            "makoctl".into(),
            "invoke".into(),
            "-n".into(),
            id.to_string(),
            "default".into(),
        ],
        Duration::from_millis(800),
        false,
    );
    let _ = run_command(
        &[
            "makoctl".into(),
            "dismiss".into(),
            "-n".into(),
            id.to_string(),
        ],
        Duration::from_millis(800),
        false,
    );
}

fn append_blacklist(id: i32) {
    let path = mako_blacklist_file();
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    use std::io::Write;
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
    {
        let _ = writeln!(f, "{id}");
    }
}

pub fn clear_all() {
    for n in fetch_notifications() {
        append_blacklist(n.id);
    }
    let _ = run_command(
        &[
            "makoctl".into(),
            "dismiss".into(),
            "--all".into(),
            "--no-history".into(),
        ],
        Duration::from_millis(1200),
        false,
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wildcard_ignore() {
        let mut set = HashSet::new();
        set.insert("dusky-glance*".to_owned());
        assert!(is_ignored("dusky-glance-foo", &set));
        assert!(!is_ignored("other", &set));
        assert!(!is_ignored("", &set));
    }

    #[test]
    fn body_cleaned() {
        assert_eq!(clean_body("<b>hi</b>\nthere"), "hi there");
    }

    #[test]
    fn grouping_preserves_order() {
        let n = |id: i32, app: &str| Notification {
            id,
            app: app.into(),
            summary: "s".into(),
            body: String::new(),
            source: "active".into(),
            desktop_entry: String::new(),
            time: String::new(),
        };
        let groups = group_notifications(&[n(2, "A"), n(1, "B"), n(0, "A")]);
        assert_eq!(groups.len(), 2);
        assert_eq!(groups[0].1.len(), 2);
    }
}
