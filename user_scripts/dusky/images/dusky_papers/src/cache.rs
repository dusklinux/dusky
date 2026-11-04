use image::ImageDecoder;
use rayon::prelude::*;
use sha2::{Digest, Sha256};
use std::collections::HashSet;
use std::fs;
use std::io::{BufWriter, Read, Seek, SeekFrom, Write};
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};

const THUMB_RECIPE: &str = "dusky-rust-thumb-v5-jpeg85-16x10-oriented";
const THUMB_WIDTH: u32 = 640;
const THUMB_HEIGHT: u32 = 400;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ThumbStatus {
    Cached,
    Generated,
    Failed,
}

#[derive(Debug, Default)]
pub struct CacheStats {
    pub cached: usize,
    pub generated: usize,
    pub failed: usize,
}

impl CacheStats {
    fn add(&mut self, status: ThumbStatus) {
        match status {
            ThumbStatus::Cached => self.cached += 1,
            ThumbStatus::Generated => self.generated += 1,
            ThumbStatus::Failed => self.failed += 1,
        }
    }
}

pub fn thumb_digest(relative_path: &str, source_path: &Path) -> String {
    let mut hasher = Sha256::new();
    hasher.update(relative_path.as_bytes());
    hasher.update(b"\0");
    hasher.update(THUMB_RECIPE.as_bytes());
    if let Ok(canonical) = source_path.canonicalize() {
        use std::os::unix::ffi::OsStrExt;
        hasher.update(canonical.as_os_str().as_bytes());
    }
    if let Ok(metadata) = fs::metadata(source_path) {
        for value in [
            // Btrfs st_dev is allocated at mount time, not a persistent identity.
            // The canonical path and remaining metadata identify the source.
            metadata.ino(),
            metadata.len(),
            metadata.mtime() as u64,
            metadata.mtime_nsec() as u64,
            metadata.ctime() as u64,
            metadata.ctime_nsec() as u64,
        ] {
            hasher.update(value.to_le_bytes());
        }
    }
    hex::encode(hasher.finalize())
}

// Minimal manual hex encoding so we don't need another crate
mod hex {
    pub fn encode(data: impl AsRef<[u8]>) -> String {
        let mut s = String::with_capacity(data.as_ref().len() * 2);
        for &byte in data.as_ref() {
            use std::fmt::Write;
            let _ = write!(s, "{byte:02x}");
        }
        s
    }
}

pub fn thumb_path_for(relative_path: &str, source_path: &Path, thumb_dir: &Path) -> PathBuf {
    let digest = thumb_digest(relative_path, source_path);
    thumb_dir.join(format!("{digest}.jpg"))
}

pub fn is_thumb_valid(source_path: &Path, thumb_path: &Path) -> bool {
    if !source_path.is_file() {
        return false;
    }
    let mut thumb = match fs::File::open(thumb_path) {
        Ok(file) => file,
        Err(_) => return false,
    };
    let mut marker = [0; 2];
    if thumb.read_exact(&mut marker).is_err() || marker != [0xff, 0xd8] {
        return false;
    }
    thumb.seek(SeekFrom::End(-2)).is_ok()
        && thumb.read_exact(&mut marker).is_ok()
        && marker == [0xff, 0xd9]
}

pub fn generate_thumb(source_path: &Path, thumb_path: &Path) -> ThumbStatus {
    generate_thumb_with_mode(source_path, thumb_path, false)
}

fn generate_thumb_with_mode(source_path: &Path, thumb_path: &Path, force: bool) -> ThumbStatus {
    if !force && is_thumb_valid(source_path, thumb_path) {
        return ThumbStatus::Cached;
    }

    let source_before = match fs::metadata(source_path) {
        Ok(metadata) => metadata,
        Err(_) => return ThumbStatus::Failed,
    };

    if let Some(parent) = thumb_path.parent() {
        let _ = fs::create_dir_all(parent);
    }

    let reader = match image::ImageReader::open(source_path)
        .and_then(|reader| reader.with_guessed_format())
    {
        Ok(reader) => reader,
        Err(error) => {
            eprintln!("Could not read {}: {error}", source_path.display());
            return ThumbStatus::Failed;
        }
    };
    let decoder = match reader.into_decoder() {
        Ok(decoder) => decoder,
        Err(error) => {
            eprintln!("Could not decode {}: {error}", source_path.display());
            return ThumbStatus::Failed;
        }
    };
    let mut decoder = decoder;
    let orientation = decoder
        .orientation()
        .unwrap_or(image::metadata::Orientation::NoTransforms);
    let mut img = match image::DynamicImage::from_decoder(decoder) {
        Ok(img) => img,
        Err(error) => {
            eprintln!("Could not decode {}: {error}", source_path.display());
            return ThumbStatus::Failed;
        }
    };
    img.apply_orientation(orientation);

    let thumb = img.resize_to_fill(
        THUMB_WIDTH,
        THUMB_HEIGHT,
        image::imageops::FilterType::Triangle,
    );

    let rgb = if thumb.color().has_alpha() {
        let mut background =
            image::RgbaImage::from_pixel(THUMB_WIDTH, THUMB_HEIGHT, image::Rgba([18, 20, 28, 255]));
        image::imageops::overlay(&mut background, &thumb.to_rgba8(), 0, 0);
        image::DynamicImage::ImageRgba8(background).to_rgb8()
    } else {
        thumb.to_rgb8()
    };
    let tmp_path = thumb_path.with_file_name(format!(
        "tmp.{}.{}.jpg",
        std::process::id(),
        fastrand::u64(..)
    ));
    let encoded = fs::File::create(&tmp_path)
        .map_err(image::ImageError::IoError)
        .and_then(|file| {
            let mut writer = BufWriter::new(file);
            let result = image::codecs::jpeg::JpegEncoder::new_with_quality(&mut writer, 85)
                .encode(
                    &rgb,
                    THUMB_WIDTH,
                    THUMB_HEIGHT,
                    image::ExtendedColorType::Rgb8,
                );
            result?;
            writer.flush().map_err(image::ImageError::IoError)
        });
    let source_unchanged = fs::metadata(source_path).is_ok_and(|after| {
        source_before.dev() == after.dev()
            && source_before.ino() == after.ino()
            && source_before.len() == after.len()
            && source_before.mtime() == after.mtime()
            && source_before.mtime_nsec() == after.mtime_nsec()
            && source_before.ctime() == after.ctime()
            && source_before.ctime_nsec() == after.ctime_nsec()
    });
    if encoded.is_ok() && source_unchanged {
        if fs::rename(&tmp_path, thumb_path).is_ok() {
            ThumbStatus::Generated
        } else {
            let _ = fs::remove_file(&tmp_path);
            ThumbStatus::Failed
        }
    } else {
        let _ = fs::remove_file(&tmp_path);
        ThumbStatus::Failed
    }
}

pub fn prune_thumbnails(
    items: &[crate::scanner::WallpaperItem],
    thumb_dir: &Path,
) -> std::io::Result<usize> {
    let wanted: HashSet<_> = items
        .iter()
        .filter_map(|item| item.thumb_path.file_name().map(|name| name.to_os_string()))
        .collect();
    let entries = match fs::read_dir(thumb_dir) {
        Ok(entries) => entries,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(0),
        Err(error) => return Err(error),
    };
    let mut removed = 0;
    for entry in entries {
        let entry = entry?;
        let name = entry.file_name();
        let name_text = name.to_string_lossy();
        let stem = name_text
            .strip_suffix(".jpg")
            .or_else(|| name_text.strip_suffix(".png"));
        if stem.is_some_and(|stem| stem.len() == 64 && stem.bytes().all(|b| b.is_ascii_hexdigit()))
            && !wanted.contains(&name)
        {
            fs::remove_file(entry.path())?;
            removed += 1;
        }
    }
    Ok(removed)
}

pub fn batch_generate_thumbs(items: &[crate::scanner::WallpaperItem], force: bool) -> CacheStats {
    let pending: Vec<_> = items
        .iter()
        .filter(|item| force || !is_thumb_valid(&item.path, &item.thumb_path))
        .collect();
    let cached = items.len() - pending.len();
    println!("Thumbnails: {cached} cached, {} to generate", pending.len());
    if pending.is_empty() {
        return CacheStats {
            cached,
            ..CacheStats::default()
        };
    }
    let completed = AtomicUsize::new(0);
    let last_report = AtomicU64::new(0);
    let started = std::time::Instant::now();
    let report_progress = || {
        let done = completed.fetch_add(1, Ordering::Relaxed) + 1;
        let seconds = started.elapsed().as_secs();
        if last_report.fetch_max(seconds, Ordering::Relaxed) < seconds || done == pending.len() {
            println!("Thumbnail progress: {done}/{}", pending.len());
        }
    };
    let available = available_memory_bytes().unwrap_or(2 * 1024 * 1024 * 1024);
    let memory_workers = (available / (1024 * 1024 * 1024)).max(1) as usize;
    let cpu_workers = std::thread::available_parallelism().map_or(1, |count| count.get());
    let workers = memory_workers.min(cpu_workers).min(8);
    let generate = || {
        pending
            .par_iter()
            .map(|item| {
                let status = generate_thumb_with_mode(&item.path, &item.thumb_path, force);
                if status == ThumbStatus::Failed {
                    eprintln!("Could not generate thumbnail for {}", item.path.display());
                }
                report_progress();
                status
            })
            .fold(CacheStats::default, |mut stats, status| {
                stats.add(status);
                stats
            })
            .reduce(CacheStats::default, |mut left, right| {
                left.cached += right.cached;
                left.generated += right.generated;
                left.failed += right.failed;
                left
            })
    };
    let mut stats = match rayon::ThreadPoolBuilder::new().num_threads(workers).build() {
        Ok(pool) => pool.install(generate),
        Err(error) => {
            eprintln!("Could not create thumbnail workers: {error}");
            pending
                .iter()
                .fold(CacheStats::default(), |mut stats, item| {
                    stats.add(generate_thumb_with_mode(
                        &item.path,
                        &item.thumb_path,
                        force,
                    ));
                    report_progress();
                    stats
                })
        }
    };
    stats.cached += cached;
    stats
}

fn available_memory_bytes() -> Option<u64> {
    let meminfo = fs::read_to_string("/proc/meminfo").ok()?;
    let mut available = meminfo
        .lines()
        .find_map(|line| {
            line.strip_prefix("MemAvailable:")
                .and_then(|value| value.split_whitespace().next())
        })?
        .parse::<u64>()
        .ok()?
        * 1024;
    let cgroup = fs::read_to_string("/proc/self/cgroup").ok()?;
    let group = cgroup.lines().find_map(|line| line.strip_prefix("0::"))?;
    let root = Path::new("/sys/fs/cgroup");
    let path = root.join(group.trim_start_matches('/'));
    for directory in path
        .ancestors()
        .take_while(|directory| directory.starts_with(root))
    {
        let limit = fs::read_to_string(directory.join("memory.max"))
            .ok()
            .and_then(|value| value.trim().parse::<u64>().ok());
        let current = fs::read_to_string(directory.join("memory.current"))
            .ok()
            .and_then(|value| value.trim().parse::<u64>().ok());
        if let (Some(limit), Some(current)) = (limit, current) {
            available = available.min(limit.saturating_sub(current));
        }
    }
    Some(available)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn incremental_cache_repairs_only_missing_changed_or_corrupt_images() {
        let root = std::env::temp_dir().join(format!(
            "dusky-cache-test-{}-{}",
            std::process::id(),
            fastrand::u64(..)
        ));
        let sources = root.join("sources");
        let thumbs = root.join("thumbs");
        fs::create_dir_all(&sources).unwrap();
        let write_image = |name: &str, rgb| {
            image::RgbImage::from_pixel(32, 32, image::Rgb(rgb))
                .save(sources.join(name))
                .unwrap();
        };
        let scan =
            || crate::scanner::scan_wallpapers(&sources, &thumbs, &HashSet::new(), None).unwrap();
        write_image("first.png", [255, 0, 0]);
        let first = scan();
        assert_eq!(batch_generate_thumbs(&first, false).generated, 1);
        let original = fs::metadata(&first[0].thumb_path)
            .unwrap()
            .modified()
            .unwrap();
        let stats = batch_generate_thumbs(&scan(), false);
        assert_eq!((stats.generated, stats.cached, stats.failed), (0, 1, 0));
        write_image("new.png", [0, 255, 0]);
        let stats = batch_generate_thumbs(&scan(), false);
        assert_eq!((stats.generated, stats.cached, stats.failed), (1, 1, 0));
        assert_eq!(
            fs::metadata(&first[0].thumb_path)
                .unwrap()
                .modified()
                .unwrap(),
            original
        );
        write_image("first.png", [0, 0, 255]);
        let changed = scan();
        assert_ne!(first[0].thumb_path, changed[0].thumb_path);
        let stats = batch_generate_thumbs(&changed, false);
        assert_eq!((stats.generated, stats.cached, stats.failed), (1, 1, 0));
        fs::write(&changed[0].thumb_path, b"broken JPEG").unwrap();
        assert_eq!(batch_generate_thumbs(&changed, false).generated, 1);
        let stats = batch_generate_thumbs(&changed, true);
        assert_eq!((stats.generated, stats.cached, stats.failed), (2, 0, 0));
        assert_eq!(prune_thumbnails(&changed, &thumbs).unwrap(), 1);
        fs::remove_file(sources.join("first.png")).unwrap();
        assert_eq!(prune_thumbnails(&scan(), &thumbs).unwrap(), 1);
        fs::remove_dir_all(root).unwrap();
    }
}
