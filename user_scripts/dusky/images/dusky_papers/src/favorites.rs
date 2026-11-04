use std::collections::HashSet;
use std::fs::{self, File};
use std::path::Path;
use std::process::Command;

pub fn load_favorites(path: &Path) -> std::io::Result<HashSet<String>> {
    let content = match fs::read_to_string(path) {
        Ok(content) => content,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => String::new(),
        Err(error) => return Err(error),
    };
    Ok(content
        .lines()
        .map(str::to_owned)
        .filter(|l| !l.is_empty())
        .collect())
}

fn save_favorites(path: &Path, favorites: &HashSet<String>) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }

    let mut list: Vec<&str> = favorites.iter().map(|s| s.as_str()).collect();
    list.sort_by(|a, b| crate::scanner::natural_cmp(a, b).then_with(|| a.cmp(b)));

    let content = list.join("\n") + "\n";
    let tmp = path.with_extension(format!("tmp.{}", std::process::id()));
    fs::write(&tmp, content)?;
    fs::rename(tmp, path)
}

pub fn toggle_favorite(
    path: &Path,
    lock_path: &Path,
    relative: &str,
) -> std::io::Result<HashSet<String>> {
    if let Some(parent) = lock_path.parent() {
        fs::create_dir_all(parent)?;
    }
    let lock = File::create(lock_path)?;
    lock.lock()?;
    let content = match fs::read_to_string(path) {
        Ok(content) => content,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => String::new(),
        Err(error) => return Err(error),
    };
    let mut favorites: HashSet<String> = content
        .lines()
        .filter(|line| !line.is_empty())
        .map(str::to_owned)
        .collect();
    if !favorites.remove(relative) {
        favorites.insert(relative.to_string());
    }
    save_favorites(path, &favorites)?;
    Ok(favorites)
}

pub fn read_active_wallpaper(theme_dir: &Path) -> Option<String> {
    if let Ok(output) = Command::new("timeout")
        .args(["-k", "1s", "2s", "awww", "query"])
        .output()
        && output.status.success()
    {
        let mut displayed = Vec::new();
        for line in String::from_utf8_lossy(&output.stdout).lines() {
            if let Some(path) = line.rsplit_once("image: ").map(|(_, path)| path.trim())
                && !path.is_empty()
            {
                let monitor = line
                    .strip_prefix(": ")
                    .and_then(|line| line.split_once(':'))
                    .map(|(name, _)| name);
                displayed.push((monitor.map(str::to_owned), path.to_owned()));
            }
        }
        if displayed.len() > 1 {
            let focused = Command::new("timeout")
                .args(["-k", "1s", "2s", "hyprctl", "monitors", "-j"])
                .output()
                .ok()
                .filter(|output| output.status.success())
                .and_then(|output| serde_json::from_slice::<serde_json::Value>(&output.stdout).ok())
                .and_then(|monitors| {
                    monitors
                        .as_array()?
                        .iter()
                        .find(|monitor| monitor["focused"].as_bool() == Some(true))?["name"]
                        .as_str()
                        .map(str::to_owned)
                });
            if let Some((_, path)) = displayed.iter().find(|(monitor, _)| *monitor == focused) {
                return Some(path.clone());
            }
        }
        if let Some((_, path)) = displayed.into_iter().next() {
            return Some(path);
        }
    }
    read_tracked_wallpaper(theme_dir)
}

/// Read controller state without waiting for a wallpaper-daemon round trip.
pub fn read_tracked_wallpaper(theme_dir: &Path) -> Option<String> {
    if let Ok(record) = fs::read(theme_dir.join("current_image")) {
        if let Some(path) = record.split(|&byte| byte == 0).next()
            && let Ok(path) = std::str::from_utf8(path)
            && !path.is_empty()
        {
            return Some(path.to_owned());
        }
    }
    let mode = fs::read_to_string(theme_dir.join("state")).unwrap_or_default();
    let tracker = if mode.trim() == "false" {
        "light_wal"
    } else {
        "dark_wal"
    };
    if let Ok(path) = fs::read_to_string(theme_dir.join(tracker)) {
        let path = path.trim_end_matches('\n');
        if !path.is_empty() {
            return Some(path.to_owned());
        }
    }
    None
}
