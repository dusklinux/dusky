use rayon::prelude::*;
use sha2::{Digest, Sha256};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::SystemTime;

const THUMB_RECIPE: &str = "dusky-rust-thumb-v4-jpeg85-16x10";
const PREVIOUS_THUMB_RECIPE: &str = "dusky-rust-thumb-v3-jpeg85";
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

pub fn thumb_digest(relative_path: &str) -> String {
    digest_for_recipe(relative_path, THUMB_RECIPE)
}

fn digest_for_recipe(relative_path: &str, recipe: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(relative_path.as_bytes());
    hasher.update(b"\0");
    hasher.update(recipe.as_bytes());
    hex::encode(hasher.finalize())
}

fn fastrand() -> u64 {
    use std::time::SystemTime;
    SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .map(|d| d.as_nanos() as u64)
        .unwrap_or(0)
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

pub fn thumb_path_for(relative_path: &str, thumb_dir: &Path) -> PathBuf {
    let digest = thumb_digest(relative_path);
    thumb_dir.join(format!("{digest}.jpg"))
}

pub fn is_thumb_valid(source_path: &Path, thumb_path: &Path) -> bool {
    if !thumb_path.exists() {
        return false;
    }

    let source_meta = match fs::metadata(source_path) {
        Ok(m) => m,
        Err(_) => return false,
    };

    let thumb_meta = match fs::metadata(thumb_path) {
        Ok(m) => m,
        Err(_) => return false,
    };

    if thumb_meta.len() == 0 {
        return false;
    }

    let source_mtime = source_meta.modified().unwrap_or(SystemTime::UNIX_EPOCH);
    let thumb_mtime = thumb_meta.modified().unwrap_or(SystemTime::UNIX_EPOCH);

    thumb_mtime >= source_mtime
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
    let img = match reader.decode() {
        Ok(img) => img,
        Err(error) => {
            eprintln!("Could not decode {}: {error}", source_path.display());
            return ThumbStatus::Failed;
        }
    };

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
    let tmp_path =
        thumb_path.with_file_name(format!("tmp.{}.{}.jpg", std::process::id(), fastrand()));
    let encoded = fs::File::create(&tmp_path)
        .map_err(image::ImageError::IoError)
        .and_then(|file| {
            let mut encoder = image::codecs::jpeg::JpegEncoder::new_with_quality(file, 85);
            encoder.encode(
                &rgb,
                THUMB_WIDTH,
                THUMB_HEIGHT,
                image::ExtendedColorType::Rgb8,
            )
        });
    let source_unchanged = fs::metadata(source_path).is_ok_and(|after| {
        source_before.len() == after.len() && source_before.modified().ok() == after.modified().ok()
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

pub fn remove_legacy_pngs(thumb_dir: &Path) -> std::io::Result<usize> {
    let mut removed = 0;
    for entry in fs::read_dir(thumb_dir)? {
        let entry = entry?;
        let name = entry.file_name();
        let name = name.to_string_lossy();
        let stem = name.strip_suffix(".png");
        if stem.is_some_and(|stem| stem.len() == 64 && stem.bytes().all(|b| b.is_ascii_hexdigit()))
        {
            fs::remove_file(entry.path())?;
            removed += 1;
        }
    }
    Ok(removed)
}

pub fn remove_previous_jpegs(
    items: &[crate::scanner::WallpaperItem],
    thumb_dir: &Path,
) -> std::io::Result<usize> {
    let mut removed = 0;
    for item in items {
        let old = thumb_dir.join(format!(
            "{}.jpg",
            digest_for_recipe(&item.relative, PREVIOUS_THUMB_RECIPE)
        ));
        match fs::remove_file(old) {
            Ok(()) => removed += 1,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(error),
        }
    }
    Ok(removed)
}

pub fn batch_generate_thumbs(items: &[crate::scanner::WallpaperItem], force: bool) -> CacheStats {
    items
        .par_iter()
        .map(|item| {
            let status = generate_thumb_with_mode(&item.path, &item.thumb_path, force);
            if status == ThumbStatus::Failed {
                eprintln!("Could not generate thumbnail for {}", item.path.display());
            }
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
}
