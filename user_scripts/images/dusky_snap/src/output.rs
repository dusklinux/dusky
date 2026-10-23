use crate::{
    Result,
    capture::command,
    document::{Document, SavedDocument},
};
use image::RgbaImage;
use serde::{Deserialize, Serialize};
use std::{
    fs,
    io::{BufWriter, Write},
    path::{Path, PathBuf},
    time::Duration,
};

#[derive(Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct Config {
    pub directory: Option<PathBuf>,
    pub filename: String,
    pub ocr_languages: String,
    pub colors: Vec<String>,
    pub line_width: f64,
}
impl Default for Config {
    fn default() -> Self {
        Self {
            directory: None,
            filename: "Screenshot_%Y-%m-%d_%H-%M-%S-%f.png".into(),
            ocr_languages: "eng".into(),
            colors: vec![
                "#f7768e".into(),
                "#ff9e64".into(),
                "#e0af68".into(),
                "#9ece6a".into(),
                "#7aa2f7".into(),
                "#bb9af7".into(),
                "#000000".into(),
                "#ffffff".into(),
            ],
            line_width: 3.0,
        }
    }
}
pub fn home() -> PathBuf {
    std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}
pub fn runtime() -> Result<PathBuf> {
    let p = std::env::var_os("XDG_RUNTIME_DIR")
        .map(PathBuf::from)
        .ok_or("XDG_RUNTIME_DIR is unset; run inside your Wayland session")?
        .join("dusky-snap");
    fs::create_dir_all(&p)?;
    Ok(p)
}
pub fn config() -> Result<Config> {
    let p = std::env::var_os("XDG_CONFIG_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| home().join(".config"))
        .join("dusky/snap/config.json");
    match fs::read(&p) {
        Ok(b) => Ok(serde_json::from_slice(&b)?),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(Config::default()),
        Err(e) => Err(e.into()),
    }
}
pub fn thumbnail(image: &RgbaImage, max_width: u32, max_height: u32) -> RgbaImage {
    let (w, h) = image.dimensions();
    let scale = (max_width as f64 / w.max(1) as f64)
        .min(max_height as f64 / h.max(1) as f64)
        .min(1.0);
    image::imageops::thumbnail(
        image,
        (w as f64 * scale).round().max(1.0) as u32,
        (h as f64 * scale).round().max(1.0) as u32,
    )
}

pub fn encode(image: &RgbaImage) -> Result<Vec<u8>> {
    let mut out = Vec::new();
    {
        let mut encoder = png::Encoder::new(&mut out, image.width(), image.height());
        encoder.set_color(png::ColorType::Rgba);
        encoder.set_depth(png::BitDepth::Eight);
        encoder.set_compression(png::Compression::Fast);
        let mut writer = encoder.write_header()?;
        writer.write_image_data(image.as_raw())?;
    }
    Ok(out)
}
pub fn atomic_write(path: &Path, data: &[u8]) -> Result<()> {
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    fs::create_dir_all(parent)?;
    let tmp = parent.join(format!(
        ".dusky-snap-{}-{}.tmp",
        std::process::id(),
        chrono::Local::now()
            .timestamp_nanos_opt()
            .unwrap_or_default()
    ));
    let result = (|| {
        let mut stream = BufWriter::new(
            fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&tmp)?,
        );
        stream.write_all(data)?;
        stream.flush()?;
        drop(stream);
        fs::rename(&tmp, path)?;
        Ok(())
    })();
    let _ = fs::remove_file(tmp);
    result
}
pub fn copy(bytes: Vec<u8>, mime: &str) -> Result<()> {
    command(
        "wl-copy",
        &["--type", mime],
        Some(bytes),
        Duration::from_secs(5),
    )?;
    let types = command("wl-paste", &["--list-types"], None, Duration::from_secs(3))?;
    if !String::from_utf8_lossy(&types).lines().any(|s| s == mime) {
        return Err("Clipboard did not advertise the copied format".into());
    }
    Ok(())
}
pub fn destination(config: &Config) -> Result<PathBuf> {
    let directory = if let Some(p) = &config.directory {
        p.clone()
    } else {
        let pictures = command("xdg-user-dir", &["PICTURES"], None, Duration::from_secs(2))
            .ok()
            .and_then(|b| String::from_utf8(b).ok())
            .map(|s| PathBuf::from(s.trim()))
            .filter(|p| p.is_absolute())
            .unwrap_or_else(|| home().join("Pictures"));
        pictures.join("Screenshots")
    };
    let mut name = String::new();
    std::fmt::write(
        &mut name,
        format_args!("{}", chrono::Local::now().format(&config.filename)),
    )
    .map_err(|_| "Invalid filename date format")?;
    if Path::new(&name).file_name() != Some(std::ffi::OsStr::new(&name)) {
        return Err("filename must be a filename, without directories".into());
    }
    Ok(directory.join(name))
}
pub fn persist(doc: &Document) -> Result<PathBuf> {
    let base = runtime()?;
    let global = fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(base.join("documents.lock"))?;
    global.lock()?;
    let dir = base.join("recents");
    fs::create_dir_all(&dir)?;
    let state = dir.join(format!("{}.json", doc.id));
    if fs::read(&state)
        .ok()
        .and_then(|b| serde_json::from_slice::<SavedDocument>(&b).ok())
        .is_some_and(|s| s.revision > doc.revision)
    {
        return Ok(state);
    }

    let lock = fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(dir.join(".lock"))?;
    lock.lock()?;
    let source = dir.join(format!("{}.png", doc.id));
    if !source.exists() {
        atomic_write(&source, &encode(&doc.source)?)?;
    }
    let state = dir.join(format!("{}.json", doc.id));
    atomic_write(&state, &serde_json::to_vec(&doc.saved())?)?;
    let thumb = doc.render()?;
    let thumb = thumbnail(&thumb, 240, 160);
    atomic_write(&dir.join(format!("{}-thumb.png", doc.id)), &encode(&thumb)?)?;
    let mut recents = recent_paths()?;
    recents.retain(|p| p.file_stem() != state.file_stem());
    recents.insert(0, state.clone());
    for p in recents.iter().skip(5) {
        for suffix in ["png", "json"] {
            let _ = fs::remove_file(p.with_extension(suffix));
        }
        if let Some(id) = p.file_stem() {
            let _ =
                fs::remove_file(p.with_file_name(format!("{}-thumb.png", id.to_string_lossy())));
        }
    }
    for ext in ["png", "json"] {
        let _ = fs::remove_file(
            runtime()?
                .join("working")
                .join(format!("{}.{}", doc.id, ext)),
        );
    }
    Ok(state)
}
pub fn recent_paths() -> Result<Vec<PathBuf>> {
    let root = runtime()?;
    let mut entries = Vec::new();
    for dir in [root.join("recents"), root.join("working")] {
        if !dir.exists() {
            continue;
        }
        entries.extend(
            fs::read_dir(dir)?
                .filter_map(|e| e.ok())
                .filter(|e| e.path().extension().is_some_and(|e| e == "json"))
                .filter_map(|e| Some((e.metadata().ok()?.modified().ok()?, e.path()))),
        );
    }
    entries.sort_by_key(|e| std::cmp::Reverse(e.0));
    Ok(entries.into_iter().map(|e| e.1).collect())
}
pub fn load_document(path: &Path) -> Result<Document> {
    let state: SavedDocument = serde_json::from_slice(&fs::read(path)?)?;
    let id = path
        .file_stem()
        .ok_or("Document has no filename")?
        .to_string_lossy()
        .into_owned();
    Document::restore(
        image::open(path.with_extension("png"))?.into_rgba8(),
        state,
        id,
    )
}
pub fn ocr(doc: &Document, config: &Config) -> Result<String> {
    let bytes = encode(&doc.render()?)?;
    let out = command(
        "tesseract",
        &["stdin", "stdout", "-l", &config.ocr_languages],
        Some(bytes),
        Duration::from_secs(60),
    )?;
    let text = String::from_utf8(out)?;
    copy(text.as_bytes().to_vec(), "text/plain;charset=utf-8")?;
    Ok(text)
}

pub fn working(doc: &Document) -> Result<PathBuf> {
    let base = runtime()?;
    let global = fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(base.join("documents.lock"))?;
    global.lock()?;
    let completed = base.join("recents").join(format!("{}.json", doc.id));
    if fs::read(&completed)
        .ok()
        .and_then(|b| serde_json::from_slice::<SavedDocument>(&b).ok())
        .is_some_and(|s| s.revision >= doc.revision)
    {
        return Ok(completed);
    }
    let dir = base.join("working");
    fs::create_dir_all(&dir)?;
    let source = dir.join(format!("{}.png", doc.id));
    if !source.exists() {
        atomic_write(&source, &encode(&doc.source)?)?;
    }
    let path = dir.join(format!("{}.json", doc.id));
    atomic_write(&path, &serde_json::to_vec(&doc.saved())?)?;
    Ok(path)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn previews_preserve_aspect_ratio_without_upscaling() {
        for (source, expected) in [
            ((900, 540), (260, 156)),
            ((540, 900), (204, 340)),
            ((20, 10), (20, 10)),
        ] {
            let image = RgbaImage::new(source.0, source.1);
            assert_eq!(thumbnail(&image, 260, 340).dimensions(), expected);
        }
    }
}
