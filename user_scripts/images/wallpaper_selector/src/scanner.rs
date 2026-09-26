use std::cmp::Ordering;
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use walkdir::WalkDir;

#[derive(Debug, Clone)]
pub struct WallpaperItem {
    pub path: PathBuf,
    pub relative: String,
    pub name: String,
    pub thumb_path: PathBuf,
    pub is_favorite: bool,
    pub is_active: bool,
    pub mtime: std::time::SystemTime,
    pub color_bucket: u8,
}

pub fn is_supported_image(path: &Path) -> bool {
    if let Some(ext) = path.extension().and_then(|s| s.to_str()) {
        matches!(
            ext.to_ascii_lowercase().as_str(),
            "jpg" | "jpeg" | "png" | "webp" | "gif"
        )
    } else {
        false
    }
}

pub fn natural_cmp(a: &str, b: &str) -> Ordering {
    let mut a_chars = a.chars().peekable();
    let mut b_chars = b.chars().peekable();

    loop {
        match (a_chars.peek(), b_chars.peek()) {
            (None, None) => return Ordering::Equal,
            (None, Some(_)) => return Ordering::Less,
            (Some(_), None) => return Ordering::Greater,
            (Some(ca), Some(cb)) => {
                if ca.is_ascii_digit() && cb.is_ascii_digit() {
                    let mut num_a: u64 = 0;
                    while let Some(d) = a_chars.peek() {
                        if let Some(digit) = d.to_digit(10) {
                            num_a = num_a.saturating_mul(10).saturating_add(digit as u64);
                            a_chars.next();
                        } else {
                            break;
                        }
                    }

                    let mut num_b: u64 = 0;
                    while let Some(d) = b_chars.peek() {
                        if let Some(digit) = d.to_digit(10) {
                            num_b = num_b.saturating_mul(10).saturating_add(digit as u64);
                            b_chars.next();
                        } else {
                            break;
                        }
                    }

                    match num_a.cmp(&num_b) {
                        Ordering::Equal => continue,
                        non_eq => return non_eq,
                    }
                } else {
                    let ca_lower = ca.to_lowercase().next().unwrap_or(*ca);
                    let cb_lower = cb.to_lowercase().next().unwrap_or(*cb);
                    match ca_lower.cmp(&cb_lower) {
                        Ordering::Equal => {
                            a_chars.next();
                            b_chars.next();
                        }
                        non_eq => return non_eq,
                    }
                }
            }
        }
    }
}

pub fn scan_wallpapers(
    wallpapers_dir: &Path,
    thumb_dir: &Path,
    favorites: &HashSet<String>,
    active_id: Option<&str>,
) -> Vec<WallpaperItem> {
    if !wallpapers_dir.exists() {
        return Vec::new();
    }

    let mut items = Vec::new();

    for entry in WalkDir::new(wallpapers_dir)
        .follow_links(true)
        .into_iter()
        .filter_map(|e| e.ok())
    {
        let path = entry.path();
        if path.is_file() && is_supported_image(path) {
            let relative = path
                .strip_prefix(wallpapers_dir)
                .map(|p| p.to_string_lossy().to_string())
                .unwrap_or_else(|_| path.to_string_lossy().to_string());

            let name = path
                .file_name()
                .map(|s| s.to_string_lossy().to_string())
                .unwrap_or_else(|| relative.clone());

            let is_fav = favorites.contains(&relative) || favorites.contains(&name);

            let is_act = active_id.is_some_and(|id| {
                id == relative
                    || id == name
                    || path.to_string_lossy().ends_with(id)
                    || id.ends_with(&relative)
                    || id.ends_with(&name)
            });

            let thumb_path = crate::cache::thumb_path_for(&relative, thumb_dir);
            let mtime = entry
                .metadata()
                .ok()
                .and_then(|m| m.modified().ok())
                .unwrap_or(std::time::SystemTime::UNIX_EPOCH);

            items.push(WallpaperItem {
                path: path.to_path_buf(),
                relative,
                name,
                thumb_path,
                is_favorite: is_fav,
                is_active: is_act,
                mtime,
                color_bucket: 12,
            });
        }
    }

    items.sort_by(|a, b| natural_cmp(&a.relative, &b.relative));
    items
}
