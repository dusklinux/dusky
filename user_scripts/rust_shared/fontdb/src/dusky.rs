//! Select installed font faces using Fontconfig's metadata, without scanning
//! and parsing the entire installed font collection.

use crate::{DUSKY_FONT_FAMILY, Database};
use std::{path::PathBuf, process::Command, sync::OnceLock};

const FACES: &[(&str, &str)] = &[
    (DUSKY_FONT_FAMILY, "Regular"),
    (DUSKY_FONT_FAMILY, "Bold"),
    (DUSKY_FONT_FAMILY, "Italic"),
    (DUSKY_FONT_FAMILY, "Bold Italic"),
    ("DejaVu Sans", "Book"),
    ("DejaVu Sans", "Bold"),
    ("DejaVu Sans Mono", "Book"),
];

fn discover() -> Vec<PathBuf> {
    let output = match Command::new("fc-list")
        .args(["--format", "%{family[0]}\t%{style[0]}\t%{file}\n"])
        .output()
    {
        Ok(output) if output.status.success() => output,
        result => {
            log::error!("Could not query selected fonts using fc-list: {result:?}");
            return Vec::new();
        }
    };
    let mut selected: Vec<Option<PathBuf>> = vec![None; FACES.len()];
    for line in String::from_utf8_lossy(&output.stdout).lines() {
        let mut fields = line.splitn(3, '\t');
        let (Some(family), Some(style), Some(file)) = (fields.next(), fields.next(), fields.next())
        else {
            continue;
        };
        if let Some(index) = FACES.iter().position(|&(f, s)| f == family && s == style) {
            let path = PathBuf::from(file);
            // Both TTF and OTF packages can expose the same face. Pick one
            // deterministically instead of loading duplicate families/styles.
            if selected[index].as_ref().is_none_or(|old| path < *old) {
                selected[index] = Some(path);
            }
        }
    }
    for (face, path) in FACES.iter().zip(&selected) {
        if path.is_none() {
            log::warn!("Selected font face is missing: {} {}", face.0, face.1);
        }
    }
    selected.into_iter().flatten().collect()
}

pub(super) fn load(db: &mut Database) {
    // UI text, SVG text, and annotation rendering each own a database. Resolve
    // the small file list once per process; initialize each database normally.
    static FILES: OnceLock<Vec<PathBuf>> = OnceLock::new();
    for file in FILES.get_or_init(discover) {
        if let Err(error) = db.load_font_file(file) {
            log::warn!("Could not load {}: {error}", file.display());
        }
    }
    db.set_sans_serif_family(DUSKY_FONT_FAMILY);
    db.set_serif_family("DejaVu Sans");
    db.set_cursive_family(DUSKY_FONT_FAMILY);
    db.set_fantasy_family(DUSKY_FONT_FAMILY);
    db.set_monospace_family("DejaVu Sans Mono");
}
