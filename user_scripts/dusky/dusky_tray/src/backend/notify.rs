//! Mako notifications: list/history merge, blacklist, ignored apps, DND.
//!
//! Mirrors `dusky_backend.fetch_notifications` + `NotificationsPanel`.
//! Blacklist + time cache live in tmpfs ([`crate::backend::system`]).

use crate::backend::cmd::{run_command, spawn_detached_argv};
use crate::backend::system::{mako_blacklist_file, notif_times_file};
use crate::config::{FileStamp, xdg_config_dir};
use std::collections::{BTreeMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::Arc;
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

fn resolve_ignored_apps_path() -> PathBuf {
    let config = xdg_config_dir().join("dusky/tray/ignored_apps.toml");
    if config.is_file() {
        return config;
    }
    if let Ok(home) = std::env::var("HOME") {
        let local = PathBuf::from(home).join(".local/share/dusky/dusky_tray/ignored_apps.toml");
        if local.is_file() {
            return local;
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
    let packaged = PathBuf::from("/usr/share/dusky_tray/ignored_apps.toml");
    if packaged.is_file() {
        packaged
    } else {
        PathBuf::from("ignored_apps.toml")
    }
}

fn ignored_apps_path() -> &'static Path {
    static PATH: std::sync::OnceLock<PathBuf> = std::sync::OnceLock::new();
    PATH.get_or_init(resolve_ignored_apps_path).as_path()
}

fn parse_ignored_apps(txt: &str) -> HashSet<String> {
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

fn load_ignored_apps_from_path(
    path: &Path,
    cache: &mut Option<(FileStamp, Arc<HashSet<String>>)>,
) -> Arc<HashSet<String>> {
    let Some(stamp) = FileStamp::from_file(path) else {
        *cache = None;
        return Arc::new(HashSet::new());
    };
    if let Some((cached_stamp, cached_apps)) = cache.as_ref()
        && *cached_stamp == stamp
    {
        return Arc::clone(cached_apps);
    }
    let parsed = Arc::new(
        std::fs::read_to_string(path)
            .map(|txt| parse_ignored_apps(&txt))
            .unwrap_or_default(),
    );
    *cache = Some((stamp, Arc::clone(&parsed)));
    parsed
}

pub fn load_ignored_apps() -> Arc<HashSet<String>> {
    static CACHE: std::sync::Mutex<Option<(FileStamp, Arc<HashSet<String>>)>> =
        std::sync::Mutex::new(None);
    let mut guard = CACHE.lock().unwrap_or_else(|e| e.into_inner());
    load_ignored_apps_from_path(ignored_apps_path(), &mut guard)
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
    combined.into_values().rev().take(50).collect()
}

/// Group by app for the stacked view (borrowed to avoid per-frame cloning).
pub fn group_notifications(notifs: &[Notification]) -> Vec<(&str, Vec<&Notification>)> {
    let mut groups: Vec<(&str, Vec<&Notification>)> = Vec::new();
    for n in notifs {
        let app = if n.app.is_empty() {
            "Unknown"
        } else {
            n.app.as_str()
        };
        if let Some(g) = groups.iter_mut().find(|(a, _)| *a == app) {
            g.1.push(n);
        } else {
            groups.push((app, vec![n]));
        }
    }
    groups
}

pub fn dismiss_mako_ids(ids: &[i32]) {
    for &id in ids {
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
}

pub fn invoke(notification: Notification) -> bool {
    if notification.source == "active" {
        return invoke_default(notification.id);
    }
    // History entries no longer have a callable Mako action. Resolve their
    // desktop entry and launch it through GIO (no GTK dependency in the panel).
    append_blacklist_ids(&[notification.id]);
    let name = if notification.desktop_entry.is_empty() {
        &notification.app
    } else {
        &notification.desktop_entry
    };
    if name.is_empty() || name == "notify-send" || name == "mako" {
        return false;
    }
    let filename = if name.ends_with(".desktop") {
        name.clone()
    } else {
        format!("{name}.desktop")
    };
    let mut dirs = Vec::new();
    if let Some(home) = std::env::var_os("HOME").filter(|h| !h.is_empty()) {
        dirs.push(
            std::env::var_os("XDG_DATA_HOME")
                .filter(|p| !p.is_empty())
                .map(PathBuf::from)
                .filter(|p| p.is_absolute())
                .unwrap_or_else(|| PathBuf::from(home).join(".local/share")),
        );
    }
    let shared = std::env::var_os("XDG_DATA_DIRS")
        .filter(|p| !p.is_empty())
        .unwrap_or_else(|| "/usr/local/share:/usr/share".into());
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
        return spawn_detached_argv(&[
            "gio".into(),
            "launch".into(),
            path.to_string_lossy().into_owned(),
        ]);
    }
    false
}

fn invoke_default(id: i32) -> bool {
    append_blacklist_ids(&[id]);
    let invoked = run_command(
        &[
            "makoctl".into(),
            "invoke".into(),
            "-n".into(),
            id.to_string(),
            "default".into(),
        ],
        Duration::from_millis(800),
        false,
    )
    .is_some_and(|o| o.status);
    dismiss_mako_ids(&[id]);
    invoked
}

fn append_blacklist_ids_to(path: &Path, ids: &[i32]) {
    if ids.is_empty() {
        return;
    }
    let existing: HashSet<String> = std::fs::read_to_string(path)
        .map(|t| t.lines().map(str::to_owned).collect())
        .unwrap_or_default();
    let mut seen_new = HashSet::new();
    let mut buf = String::new();
    for &id in ids {
        if id < 0 {
            continue;
        }
        let s = id.to_string();
        if !existing.contains(&s) && seen_new.insert(id) {
            buf.push_str(&s);
            buf.push('\n');
        }
    }
    if buf.is_empty() {
        return;
    }
    if let Some(parent) = path.parent()
        && !parent.is_dir()
    {
        let _ = std::fs::create_dir_all(parent);
    }
    use std::io::Write;
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
    {
        let _ = f.write_all(buf.as_bytes());
    }
}

pub fn append_blacklist_ids(ids: &[i32]) {
    append_blacklist_ids_to(&mako_blacklist_file(), ids);
}

fn extract_raw_ids(items: &[serde_json::Value]) -> Vec<i32> {
    items
        .iter()
        .filter_map(|item| item.get("id").and_then(|v| v.as_i64()))
        .filter(|&id| id >= 0)
        .map(|id| id as i32)
        .collect()
}

pub fn clear_all() {
    let mut ids = extract_raw_ids(&mako_query(&["list", "-j"]));
    ids.extend(extract_raw_ids(&mako_query(&["history", "-j"])));
    append_blacklist_ids(&ids);
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
    fn grouping_preserves_order_with_borrowed_refs() {
        let n = |id: i32, app: &str| Notification {
            id,
            app: app.into(),
            summary: "s".into(),
            body: String::new(),
            source: "active".into(),
            desktop_entry: String::new(),
            time: String::new(),
        };
        let list = [n(3, "A"), n(2, ""), n(1, "A")];
        let groups = group_notifications(&list);
        assert_eq!(groups.len(), 2);
        assert_eq!(groups[0].0, "A");
        assert_eq!(groups[0].1.len(), 2);
        assert_eq!(groups[0].1[0].id, 3);
        assert_eq!(groups[0].1[1].id, 1);
        assert_eq!(groups[1].0, "Unknown");
        assert_eq!(groups[1].1.len(), 1);
    }

    #[test]
    fn batched_blacklist_deduplicates_and_preserves_existing() {
        let root = crate::backend::runtime_dir().join(format!("bl-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).unwrap();
        let path = root.join("mako_rofi_blacklist");

        append_blacklist_ids_to(&path, &[10, 20, 10, -1, 30]);
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "10\n20\n30\n");

        append_blacklist_ids_to(&path, &[20, 40, 30, 40]);
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "10\n20\n30\n40\n");

        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn extract_raw_ids_collects_beyond_ui_truncation_cap() {
        let items: Vec<serde_json::Value> = (1..=80)
            .map(|id| serde_json::json!({"id": id, "summary": ""}))
            .chain(std::iter::once(serde_json::json!({"id": -5})))
            .collect();
        let ids = extract_raw_ids(&items);
        assert_eq!(ids.len(), 80);
        assert_eq!(ids[0], 1);
        assert_eq!(ids[79], 80);
    }

    #[test]
    fn ignored_apps_stat_cache_reuses_and_reloads_on_change() {
        let root =
            crate::backend::runtime_dir().join(format!("ignored-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).unwrap();
        let file = root.join("ignored_apps.toml");
        let tmp = root.join("ignored_apps.toml.tmp");

        let mut cache = None;
        assert!(load_ignored_apps_from_path(&file, &mut cache).is_empty());

        std::fs::write(&file, "[ignored]\napps = [\"OSD\"]\n").unwrap();
        let first = load_ignored_apps_from_path(&file, &mut cache);
        assert!(first.contains("OSD"));
        let second = load_ignored_apps_from_path(&file, &mut cache);
        assert!(Arc::ptr_eq(&first, &second));

        std::fs::write(&tmp, "[ignored]\napps = [\"Spotify\"]\n").unwrap();
        std::fs::rename(&tmp, &file).unwrap();
        let third = load_ignored_apps_from_path(&file, &mut cache);
        assert!(!third.contains("OSD"));
        assert!(third.contains("Spotify"));

        std::fs::remove_dir_all(&root).unwrap();
    }
}
