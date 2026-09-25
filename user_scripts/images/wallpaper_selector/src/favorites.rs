use std::collections::HashSet;
use std::fs::{self, File};
use std::path::Path;

pub fn load_favorites(path: &Path) -> HashSet<String> {
    if !path.exists() {
        return HashSet::new();
    }

    let content = fs::read_to_string(path).unwrap_or_default();
    content
        .lines()
        .map(|l| l.trim().to_string())
        .filter(|l| !l.is_empty())
        .collect()
}

fn save_favorites(path: &Path, favorites: &HashSet<String>) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }

    let mut list: Vec<&str> = favorites.iter().map(|s| s.as_str()).collect();
    list.sort_by(|a, b| crate::scanner::natural_cmp(a, b));

    let content = list.join("\n") + "\n";
    let tmp = path.with_extension(format!("tmp.{}", std::process::id()));
    fs::write(&tmp, content)?;
    fs::rename(tmp, path)
}

pub fn toggle_favorite(
    path: &Path,
    lock_path: &Path,
    relative: &str,
    name: &str,
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
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .map(str::to_owned)
        .collect();
    if favorites.remove(relative) {
        favorites.remove(name);
    } else if favorites.remove(name) {
        // Remove legacy basename entries without adding duplicate favorites.
    } else {
        favorites.insert(relative.to_string());
    }
    save_favorites(path, &favorites)?;
    Ok(favorites)
}

pub fn read_active_wallpaper(theme_dir: &Path) -> Option<String> {
    for name in &["current_fav", "dark_wal", "light_wal", "current_image"] {
        let p = theme_dir.join(name);
        if p.exists() {
            if let Ok(content) = fs::read_to_string(&p) {
                let trimmed = content.trim().to_string();
                if !trimmed.is_empty() {
                    return Some(trimmed);
                }
            }
        }
    }
    None
}
