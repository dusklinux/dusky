use iced::Color;
use rayon::prelude::*;
use std::collections::HashMap;
use std::fs;
use std::io;
use std::path::Path;

pub const COLOR_BUCKET_COUNT: usize = 13;

pub fn swatch_color(bucket: u8, bright: bool) -> Color {
    if bucket == 12 {
        if bright {
            Color::from_rgb(0.80, 0.82, 0.88)
        } else {
            Color::from_rgb(0.50, 0.52, 0.58)
        }
    } else {
        let hue = bucket as f32 / 12.0;
        hsl(
            hue,
            if bright { 0.85 } else { 0.70 },
            if bright { 0.55 } else { 0.45 },
        )
    }
}

pub fn swatch_name(bucket: u8) -> &'static str {
    match bucket {
        0 => "Red",
        1 => "Orange",
        2 => "Yellow",
        3 => "Lime",
        4 => "Green",
        5 => "Mint",
        6 => "Cyan",
        7 => "Azure",
        8 => "Blue",
        9 => "Purple",
        10 => "Magenta",
        11 => "Pink",
        _ => "Monochrome",
    }
}

fn hsl(h: f32, s: f32, l: f32) -> Color {
    let c = (1.0 - (2.0 * l - 1.0).abs()) * s;
    let hp = (h * 6.0).rem_euclid(6.0);
    let x = c * (1.0 - (hp % 2.0 - 1.0).abs());
    let (r, g, b) = match hp as u32 {
        0 => (c, x, 0.0),
        1 => (x, c, 0.0),
        2 => (0.0, c, x),
        3 => (0.0, x, c),
        4 => (x, 0.0, c),
        _ => (c, 0.0, x),
    };
    let m = l - c / 2.0;
    Color::from_rgb(r + m, g + m, b + m)
}

/// Extract dominant color bucket from decoded RGB image pixels.
pub fn extract_dominant_color(img: &image::RgbImage) -> u8 {
    let mut weights = [0.0f32; 13];

    for pixel in img.pixels().step_by(8) {
        let r = pixel[0] as f32 / 255.0;
        let g = pixel[1] as f32 / 255.0;
        let b = pixel[2] as f32 / 255.0;

        let max = r.max(g).max(b);
        let min = r.min(g).min(b);
        let delta = max - min;

        // Low saturation or extreme darkness/lightness -> monochrome bucket (12)
        if delta < 0.12 || max < 0.12 || (delta / max.max(0.001)) < 0.15 {
            weights[12] += 0.8;
            continue;
        }

        let hue = if max == r {
            60.0 * (((g - b) / delta).rem_euclid(6.0))
        } else if max == g {
            60.0 * ((b - r) / delta + 2.0)
        } else {
            60.0 * ((r - g) / delta + 4.0)
        };

        let bucket = ((hue + 15.0).rem_euclid(360.0) / 30.0) as usize % 12;
        let saturation = delta / max;
        weights[bucket] += saturation * max;
    }

    weights
        .iter()
        .enumerate()
        .max_by(|(_, a), (_, b)| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal))
        .map(|(idx, _)| idx as u8)
        .unwrap_or(12)
}

/// Extract dominant color directly from an image/thumbnail file on disk.
pub fn extract_color_from_file(path: &Path) -> Option<u8> {
    let reader = image::ImageReader::open(path)
        .ok()?
        .with_guessed_format()
        .ok()?;
    let img = reader.decode().ok()?.to_rgb8();
    Some(extract_dominant_color(&img))
}

pub fn load_color_cache(path: &Path) -> HashMap<String, u8> {
    fs::read(path)
        .ok()
        .and_then(|data| serde_json::from_slice(&data).ok())
        .unwrap_or_default()
}

pub fn save_color_cache(path: &Path, cache: &HashMap<String, u8>) -> io::Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let temp = path.with_extension(format!("tmp.{}", std::process::id()));
    let data = serde_json::to_vec(cache).map_err(io::Error::other)?;
    fs::write(&temp, data)?;
    if let Err(err) = fs::rename(&temp, path) {
        let _ = fs::remove_file(&temp);
        return Err(err);
    }
    Ok(())
}

/// Ensure all wallpapers have their dominant color in the cache.
/// Missing colors are computed in parallel using Rayon from cached thumbnails.
pub fn ensure_color_cache(
    wallpapers: &[crate::scanner::WallpaperItem],
    colors_file: &Path,
) -> HashMap<String, u8> {
    let mut cache = load_color_cache(colors_file);
    let missing: Vec<_> = wallpapers
        .iter()
        .filter(|w| !cache.contains_key(&w.relative))
        .collect();

    if !missing.is_empty() {
        let newly_computed: Vec<(String, u8)> = missing
            .par_iter()
            .map(|item| {
                let target_path = if item.thumb_path.exists() {
                    &item.thumb_path
                } else {
                    &item.path
                };
                let bucket = extract_color_from_file(target_path).unwrap_or(12);
                (item.relative.clone(), bucket)
            })
            .collect();

        for (rel, bucket) in newly_computed {
            cache.insert(rel, bucket);
        }
        let _ = save_color_cache(colors_file, &cache);
    }

    cache
}
