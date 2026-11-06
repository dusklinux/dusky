//! Load the fixed Dusky font set from hardcoded system paths without
//! spawning Fontconfig subprocesses or scanning font directories.

use crate::{DUSKY_FONT_FAMILY, Database};
use std::{path::Path, sync::OnceLock};

struct FaceSpec {
    family: &'static str,
    style: &'static str,
    candidates: &'static [&'static str],
}

const FACES: &[FaceSpec] = &[
    FaceSpec {
        family: DUSKY_FONT_FAMILY,
        style: "Regular",
        candidates: &[
            "/usr/share/fonts/OTF/AtkinsonHyperlegible-Regular.otf",
            "/usr/share/fonts/TTF/AtkinsonHyperlegible-Regular.ttf",
        ],
    },
    FaceSpec {
        family: DUSKY_FONT_FAMILY,
        style: "Bold",
        candidates: &[
            "/usr/share/fonts/OTF/AtkinsonHyperlegible-Bold.otf",
            "/usr/share/fonts/TTF/AtkinsonHyperlegible-Bold.ttf",
        ],
    },
    FaceSpec {
        family: DUSKY_FONT_FAMILY,
        style: "Italic",
        candidates: &[
            "/usr/share/fonts/OTF/AtkinsonHyperlegible-Italic.otf",
            "/usr/share/fonts/TTF/AtkinsonHyperlegible-Italic.ttf",
        ],
    },
    FaceSpec {
        family: DUSKY_FONT_FAMILY,
        style: "Bold Italic",
        candidates: &[
            "/usr/share/fonts/OTF/AtkinsonHyperlegible-BoldItalic.otf",
            "/usr/share/fonts/TTF/AtkinsonHyperlegible-BoldItalic.ttf",
        ],
    },
    FaceSpec {
        family: "DejaVu Sans",
        style: "Book",
        candidates: &["/usr/share/fonts/TTF/DejaVuSans.ttf"],
    },
    FaceSpec {
        family: "DejaVu Sans",
        style: "Bold",
        candidates: &["/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"],
    },
    FaceSpec {
        family: "DejaVu Sans Mono",
        style: "Book",
        candidates: &["/usr/share/fonts/TTF/DejaVuSansMono.ttf"],
    },
];

struct ResolvedFonts {
    files: Vec<&'static Path>,
    has_primary_family: bool,
}

fn resolve_files() -> ResolvedFonts {
    let mut files = Vec::with_capacity(FACES.len());
    let mut has_primary_family = false;

    for spec in FACES {
        let resolved = spec
            .candidates
            .iter()
            .map(|&raw| Path::new(raw))
            .find(|path| path.is_file());

        if let Some(path) = resolved {
            if spec.family == DUSKY_FONT_FAMILY {
                has_primary_family = true;
            }
            files.push(path);
        } else {
            log::warn!(
                "Selected font face is missing: {} {} (checked: {:?})",
                spec.family,
                spec.style,
                spec.candidates
            );
        }
    }

    ResolvedFonts {
        files,
        has_primary_family,
    }
}

pub(super) fn load(db: &mut Database) {
    // UI text, SVG text, and annotation rendering each own a database. Resolve
    // the small file list once per process; initialize each database normally.
    static RESOLVED: OnceLock<ResolvedFonts> = OnceLock::new();
    let resolved = RESOLVED.get_or_init(resolve_files);

    for &file in &resolved.files {
        if let Err(error) = db.load_font_file(file) {
            log::warn!("Could not load {}: {error}", file.display());
        }
    }

    let primary = if resolved.has_primary_family {
        DUSKY_FONT_FAMILY
    } else {
        "DejaVu Sans"
    };
    db.set_sans_serif_family(primary);
    db.set_serif_family("DejaVu Sans");
    db.set_cursive_family(primary);
    db.set_fantasy_family(primary);
    db.set_monospace_family("DejaVu Sans Mono");
}
