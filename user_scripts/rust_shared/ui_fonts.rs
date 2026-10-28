//! Common font aliases for Iced and screenshot annotation rendering.
use iced_graphics::text::cosmic_text::{self, fontdb};

// This exported constant also makes a missing Cargo patch a compile error.
pub const DEFAULT_FAMILY: &str = fontdb::DUSKY_FONT_FAMILY;

fn configure(db: &mut fontdb::Database) {
    db.set_sans_serif_family(DEFAULT_FAMILY);
    db.set_serif_family("DejaVu Sans");
    db.set_cursive_family(DEFAULT_FAMILY);
    db.set_fantasy_family(DEFAULT_FAMILY);
    db.set_monospace_family("DejaVu Sans Mono");
}

/// Set generic family names after Cosmic Text applies its platform defaults.
pub fn initialize() {
    let mut fonts = iced_graphics::text::font_system()
        .write()
        .expect("font system");
    configure(fonts.raw().db_mut());
}

/// Annotation rendering uses the same selected installed fontdb and aliases as the UI.
#[allow(dead_code)] // Used by Snap's rasterizer; the other apps only initialize Iced.
pub fn font_system() -> cosmic_text::FontSystem {
    let mut fonts = cosmic_text::FontSystem::new();
    configure(fonts.db_mut());
    fonts
}

#[cfg(test)]
mod tests {
    use super::*;
    use iced_graphics::text::cosmic_text::{Attrs, Buffer, Family, Metrics, Shaping, Weight};

    #[test]
    fn ui_and_svg_font_databases_are_bounded_and_have_working_aliases() {
        let mut svg_db = fontdb::Database::new();
        svg_db.load_system_fonts();
        assert_eq!(svg_db.len(), 7);
        assert_eq!(
            svg_db.family_name(&fontdb::Family::SansSerif),
            DEFAULT_FAMILY
        );
        initialize();
        let mut ui = iced_graphics::text::font_system().write().unwrap();
        let db = ui.raw().db();
        // Seven selected faces plus Iced's own built-in icon font.
        assert_eq!(db.len(), 8);
        assert_eq!(db.family_name(&fontdb::Family::SansSerif), DEFAULT_FAMILY);
        assert_eq!(
            db.family_name(&fontdb::Family::Monospace),
            "DejaVu Sans Mono"
        );
    }

    #[test]
    fn selected_families_styles_and_aliases_are_available() {
        let fonts = font_system();
        let db = fonts.db();
        assert_eq!(db.len(), 7);
        assert_eq!(db.family_name(&fontdb::Family::SansSerif), DEFAULT_FAMILY);
        assert_eq!(
            db.family_name(&fontdb::Family::Monospace),
            "DejaVu Sans Mono"
        );
        for weight in [fontdb::Weight::NORMAL, fontdb::Weight::BOLD] {
            for style in [fontdb::Style::Normal, fontdb::Style::Italic] {
                let id = db
                    .query(&fontdb::Query {
                        families: &[fontdb::Family::Name(DEFAULT_FAMILY)],
                        weight,
                        style,
                        ..Default::default()
                    })
                    .unwrap();
                let face = db.face(id).unwrap();
                assert_eq!(face.weight, weight);
                assert_eq!(face.style, style);
            }
        }
    }

    #[test]
    fn primary_fallback_monospace_and_unknown_family_shape_without_missing_glyphs() {
        let mut fonts = font_system();
        for family in [
            Family::SansSerif,
            Family::Monospace,
            Family::Name("Unavailable Font"),
        ] {
            let mut buffer = Buffer::new(&mut fonts, Metrics::new(16.0, 20.0));
            buffer.set_size(&mut fonts, Some(1200.0), Some(300.0));
            buffer.set_text(
                &mut fonts,
                "Dusky café Ω Ж العربية שלום ←↑→↓ ↻⇄ ●♡♥ ✕ …",
                &Attrs::new().family(family).weight(Weight::NORMAL),
                Shaping::Advanced,
                None,
            );
            buffer.shape_until_scroll(&mut fonts, false);
            let glyphs: Vec<_> = buffer.layout_runs().flat_map(|run| run.glyphs).collect();
            assert!(!glyphs.is_empty());
            assert!(
                glyphs.iter().all(|glyph| glyph.glyph_id != 0),
                "missing glyph for {family:?}"
            );
            assert!(
                glyphs.iter().any(|glyph| {
                    fonts
                        .db()
                        .face(glyph.font_id)
                        .unwrap()
                        .families
                        .iter()
                        .any(|(name, _)| name == "DejaVu Sans")
                }),
                "fallback was not exercised"
            );
        }
    }
}
