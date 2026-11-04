use std::cmp::Ordering;
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use walkdir::WalkDir;

#[derive(Debug, Clone)]
pub struct WallpaperItem {
    pub path: PathBuf,
    pub relative: String,
    pub search_key: String,
    pub name: String,
    pub thumb_path: PathBuf,
    pub is_favorite: bool,
    pub is_active: bool,
    pub mtime: std::time::SystemTime,
    pub color_bucket: Option<u8>,
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
    let (ab, bb) = (a.as_bytes(), b.as_bytes());
    let (mut ai, mut bi) = (0, 0);
    while ai < ab.len() && bi < bb.len() {
        if ab[ai].is_ascii_digit() && bb[bi].is_ascii_digit() {
            let (a_start, b_start) = (ai, bi);
            while ai < ab.len() && ab[ai].is_ascii_digit() {
                ai += 1;
            }
            while bi < bb.len() && bb[bi].is_ascii_digit() {
                bi += 1;
            }
            let a_digits = a[a_start..ai].trim_start_matches('0');
            let b_digits = b[b_start..bi].trim_start_matches('0');
            let order = a_digits
                .len()
                .cmp(&b_digits.len())
                .then_with(|| a_digits.cmp(b_digits));
            if order != Ordering::Equal {
                return order;
            }
        } else {
            let ca = a[ai..].chars().next().unwrap();
            let cb = b[bi..].chars().next().unwrap();
            let order = ca.to_lowercase().next().cmp(&cb.to_lowercase().next());
            if order != Ordering::Equal {
                return order;
            }
            ai += ca.len_utf8();
            bi += cb.len_utf8();
        }
    }
    match (ai == ab.len(), bi == bb.len()) {
        (true, true) => Ordering::Equal,
        (true, false) => Ordering::Less,
        (false, true) => Ordering::Greater,
        (false, false) => unreachable!(),
    }
}

pub fn scan_wallpapers(
    wallpapers_dir: &Path,
    thumb_dir: &Path,
    favorites: &HashSet<String>,
    active_id: Option<&str>,
) -> Result<Vec<WallpaperItem>, String> {
    match wallpapers_dir.try_exists() {
        Ok(false) => return Ok(Vec::new()),
        Err(error) => {
            return Err(format!(
                "Could not access {}: {error}",
                wallpapers_dir.display()
            ));
        }
        Ok(true) => {}
    }

    let mut items = Vec::new();
    let active_canonical = active_id
        .filter(|id| Path::new(id).is_absolute())
        .and_then(|id| Path::new(id).canonicalize().ok());

    for entry in WalkDir::new(wallpapers_dir).follow_links(true) {
        let entry = match entry {
            Ok(entry) => entry,
            Err(error)
                if error
                    .io_error()
                    .is_some_and(|io| io.kind() == std::io::ErrorKind::NotFound)
                    && error.path().is_some_and(|path| {
                        path.symlink_metadata()
                            .is_ok_and(|metadata| metadata.file_type().is_symlink())
                    }) =>
            {
                // A missing link target must not hide the rest of the gallery.
                continue;
            }
            Err(error) => return Err(format!("Could not scan wallpapers: {error}")),
        };
        let path = entry.path();
        if entry.file_type().is_file() && is_supported_image(path) {
            let relative = path
                .strip_prefix(wallpapers_dir)
                .map(|p| p.to_string_lossy().to_string())
                .unwrap_or_else(|_| path.to_string_lossy().to_string());

            let name = path
                .file_name()
                .map(|s| s.to_string_lossy().to_string())
                .unwrap_or_else(|| relative.clone());

            let is_fav = favorites.contains(&relative);

            let is_act = active_id.is_some_and(|id| {
                id == relative
                    || Path::new(id) == path
                    || active_canonical.as_ref().is_some_and(|active| {
                        path.canonicalize()
                            .is_ok_and(|resolved| &resolved == active)
                    })
            });

            let thumb_path = crate::cache::thumb_path_for(&relative, path, thumb_dir);
            let mtime = entry
                .metadata()
                .map_err(|error| {
                    format!("Could not read metadata for {}: {error}", path.display())
                })?
                .modified()
                .map_err(|error| {
                    format!(
                        "Could not read modification time for {}: {error}",
                        path.display()
                    )
                })?;

            items.push(WallpaperItem {
                path: path.to_path_buf(),
                search_key: relative.to_lowercase(),
                relative,
                name,
                thumb_path,
                is_favorite: is_fav,
                is_active: is_act,
                mtime,
                color_bucket: None,
            });
        }
    }

    items.sort_by(|a, b| {
        natural_cmp(&a.relative, &b.relative).then_with(|| a.relative.cmp(&b.relative))
    });
    Ok(items)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::os::unix::fs::symlink;

    struct Fixture(PathBuf);

    impl Fixture {
        fn new() -> Self {
            let root = std::env::temp_dir().join(format!(
                "dusky-papers-scan-{}-{}",
                std::process::id(),
                fastrand::u64(..)
            ));
            fs::create_dir(&root).unwrap();
            Self(root)
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn follows_image_and_directory_links_and_skips_broken_links() {
        let fixture = Fixture::new();
        let root = &fixture.0;
        let wallpapers = root.join("wallpapers");
        let external = root.join("external");
        fs::create_dir(&wallpapers).unwrap();
        fs::create_dir(&external).unwrap();
        fs::write(wallpapers.join("local.png"), []).unwrap();
        fs::write(external.join("linked.PNG"), []).unwrap();
        symlink(&external, wallpapers.join("linked directory")).unwrap();
        symlink(
            external.join("linked.PNG"),
            wallpapers.join("linked image.png"),
        )
        .unwrap();
        symlink(root.join("missing.png"), wallpapers.join("broken.png")).unwrap();
        symlink(
            root.join("missing directory"),
            wallpapers.join("broken directory"),
        )
        .unwrap();

        let items = scan_wallpapers(&wallpapers, &root.join("thumbs"), &HashSet::new(), None)
            .expect("broken links must not abort the scan");
        let relative: Vec<_> = items.iter().map(|item| item.relative.as_str()).collect();
        assert_eq!(
            relative,
            [
                "linked directory/linked.PNG",
                "linked image.png",
                "local.png"
            ]
        );
    }

    #[test]
    fn directory_link_loops_still_fail_the_scan() {
        let fixture = Fixture::new();
        symlink(&fixture.0, fixture.0.join("loop")).unwrap();
        let error = scan_wallpapers(&fixture.0, &fixture.0.join("thumbs"), &HashSet::new(), None)
            .expect_err("a directory loop must remain visible");
        assert!(error.contains("Could not scan wallpapers:"));
    }
}
