use crate::Result;
use image::RgbaImage;
use serde::{Deserialize, Serialize};
use std::sync::Arc;

#[derive(Clone, Copy, Debug, Default, Serialize, Deserialize, PartialEq)]
pub struct Point {
    pub x: f64,
    pub y: f64,
}
impl Point {
    pub fn new(x: f64, y: f64) -> Self {
        Self { x, y }
    }
}
#[derive(Clone, Copy, Debug, Default, Serialize, Deserialize, PartialEq)]
pub struct Rect {
    pub x: f64,
    pub y: f64,
    pub w: f64,
    pub h: f64,
}
impl Rect {
    pub fn between(a: Point, b: Point) -> Self {
        Self {
            x: a.x.min(b.x),
            y: a.y.min(b.y),
            w: (a.x - b.x).abs(),
            h: (a.y - b.y).abs(),
        }
    }
    pub fn pixel_bounds(self) -> Self {
        let x = self.x.floor();
        let y = self.y.floor();
        Self {
            x,
            y,
            w: (self.x + self.w).ceil() - x,
            h: (self.y + self.h).ceil() - y,
        }
    }
    pub fn contains(self, p: Point) -> bool {
        p.x >= self.x && p.y >= self.y && p.x <= self.x + self.w && p.y <= self.y + self.h
    }
    pub fn intersect(self, other: Self) -> Self {
        let x = self.x.max(other.x);
        let y = self.y.max(other.y);
        Self {
            x,
            y,
            w: (self.x + self.w).min(other.x + other.w).max(x) - x,
            h: (self.y + self.h).min(other.y + other.h).max(y) - y,
        }
    }
}
#[derive(Clone, Copy, Debug, Default, Serialize, Deserialize, PartialEq)]
pub enum Tool {
    #[default]
    Select,
    Arrow,
    DoubleArrow,
    Curve,
    Line,
    Pen,
    Highlight,
    Rectangle,
    Ellipse,
    Marker,
    Text,
    Redact,
    Pixelate,
    Blur,
    Crop,
    Ocr,
    Spotlight,
    Cut,
    Insert,
    Eyedropper,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Layer {
    pub tool: Tool,
    pub points: Vec<Point>,
    pub color: [f64; 4],
    pub width: f64,
    pub filled: bool,
    pub text: String,
    #[serde(default = "default_font")]
    pub font: String,
    #[serde(default)]
    pub text_style: u8,
}
fn default_font() -> String {
    "Sans".into()
}
impl Layer {
    pub fn bounds(&self) -> Rect {
        let mut r = Rect {
            x: f64::MAX,
            y: f64::MAX,
            w: f64::MIN,
            h: f64::MIN,
        };
        for p in &self.points {
            r.x = r.x.min(p.x);
            r.y = r.y.min(p.y);
            r.w = r.w.max(p.x);
            r.h = r.h.max(p.y);
        }
        if self.tool == Tool::Text {
            let size = self.width * 5.0 + 12.0;
            let width = self
                .text
                .lines()
                .map(|l| l.chars().count())
                .max()
                .unwrap_or(1) as f64
                * size
                * 0.8;
            return Rect {
                x: r.x,
                y: r.y,
                w: width.max(size),
                h: self.text.lines().count().max(1) as f64 * size * 1.5,
            };
        }
        let pad = self.width * 3.0;
        Rect {
            x: r.x - pad,
            y: r.y - pad,
            w: (r.w - r.x + pad * 2.0).max(1.0),
            h: (r.h - r.y + pad * 2.0).max(1.0),
        }
    }
    pub fn translate(&mut self, dx: f64, dy: f64) {
        for p in &mut self.points {
            p.x += dx;
            p.y += dy;
        }
    }
    pub fn scale(&mut self, factor: f64) {
        let r = self.bounds();
        let c = Point::new(r.x + r.w / 2.0, r.y + r.h / 2.0);
        for p in &mut self.points {
            p.x = c.x + (p.x - c.x) * factor;
            p.y = c.y + (p.y - c.y) * factor;
        }
        self.width = (self.width * factor).clamp(0.5, 200.0);
    }
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Band {
    pub y: u32,
    pub height: u32,
    pub insert: bool,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct State {
    #[serde(default)]
    pub bands: Vec<Band>,
    pub crop: Rect,
    pub layers: Vec<Layer>,
    pub backdrop: u8,
    pub grow: bool,
    pub shadow: bool,
}
#[derive(Clone)]
pub struct Document {
    pub source: Arc<RgbaImage>,
    pub state: State,
    undo: Vec<State>,
    redo: Vec<State>,
    pub id: String,
    pub revision: u64,
    pub saved_path: Option<std::path::PathBuf>,
}
#[derive(Serialize, Deserialize)]
pub struct SavedDocument {
    pub version: u8,
    #[serde(default)]
    pub revision: u64,
    #[serde(default)]
    pub saved_path: Option<std::path::PathBuf>,
    pub state: State,
    pub undo: Vec<State>,
    pub redo: Vec<State>,
}
impl Document {
    pub fn new(source: RgbaImage) -> Self {
        let crop = Rect {
            x: 0.0,
            y: 0.0,
            w: source.width() as f64,
            h: source.height() as f64,
        };
        Self {
            source: Arc::new(source),
            state: State {
                bands: vec![],
                crop,
                layers: vec![],
                backdrop: 0,
                grow: false,
                shadow: true,
            },
            undo: vec![],
            redo: vec![],
            revision: 1,
            saved_path: None,
            id: format!(
                "{}-{}",
                std::process::id(),
                chrono::Local::now()
                    .timestamp_nanos_opt()
                    .unwrap_or_default()
            ),
        }
    }
    pub fn checkpoint(&mut self) {
        self.revision = self.revision.wrapping_add(1);
        self.undo.push(self.state.clone());
        self.redo.clear();
    }
    pub fn undo(&mut self) {
        self.revision = self.revision.wrapping_add(1);
        if let Some(s) = self.undo.pop() {
            self.redo.push(std::mem::replace(&mut self.state, s));
        }
    }
    pub fn redo(&mut self) {
        self.revision = self.revision.wrapping_add(1);
        if let Some(s) = self.redo.pop() {
            self.undo.push(std::mem::replace(&mut self.state, s));
        }
    }
    pub fn saved(&self) -> SavedDocument {
        SavedDocument {
            version: 1,
            revision: self.revision,
            saved_path: self.saved_path.clone(),
            state: self.state.clone(),
            undo: self.undo.clone(),
            redo: self.redo.clone(),
        }
    }
    pub fn restore(source: RgbaImage, saved: SavedDocument, id: String) -> Result<Self> {
        if saved.version != 1 {
            return Err("Unsupported working document version".into());
        }
        if saved.state.crop.w < 1.0 || saved.state.crop.h < 1.0 {
            return Err("Empty saved crop".into());
        }
        Ok(Self {
            source: Arc::new(source),
            state: saved.state,
            undo: saved.undo,
            redo: saved.redo,
            id,
            revision: saved.revision.max(1),
            saved_path: saved.saved_path,
        })
    }
    pub fn canvas(&self) -> Rect {
        let mut r = self.state.crop.pixel_bounds();
        if self.state.grow {
            for l in &self.state.layers {
                let b = l.bounds();
                let x = r.x.min(b.x);
                let y = r.y.min(b.y);
                r = Rect {
                    x,
                    y,
                    w: (r.x + r.w).max(b.x + b.w) - x,
                    h: (r.y + r.h).max(b.y + b.h) - y,
                };
            }
        }
        r.pixel_bounds()
    }
    pub fn render(&self) -> Result<RgbaImage> {
        crate::raster::render(self)
    }
    pub fn composed_dimensions(&self) -> (u32, u32) {
        let height = self.state.bands.iter().fold(self.source.height(), |h, b| {
            if b.insert {
                h.saturating_add(b.height)
            } else {
                h.saturating_sub(b.height)
            }
        });
        (self.source.width(), height)
    }
    pub fn composed_source(&self) -> Result<Arc<RgbaImage>> {
        if self.state.bands.is_empty() {
            return Ok(self.source.clone());
        }
        let mut image = self.source.clone();
        for band in &self.state.bands {
            let y = band.y.min(image.height());
            let amount = if band.insert {
                band.height
            } else {
                band.height.min(image.height() - y)
            };
            let height = if band.insert {
                image
                    .height()
                    .checked_add(amount)
                    .ok_or("Band is too large")?
            } else {
                image.height() - amount
            };
            if height == 0 || u64::from(height) * u64::from(image.width()) > 160_000_000 {
                return Err("Band exceeds the canvas budget".into());
            }
            let mut out = RgbaImage::new(image.width(), height);
            let row_bytes = image.width() as usize * 4;
            let top = y as usize * row_bytes;
            out.as_mut()[..top].copy_from_slice(&image.as_raw()[..top]);
            let from = if band.insert { y } else { y + amount };
            let to = if band.insert { y + amount } else { y };
            out.as_mut()[to as usize * row_bytes..]
                .copy_from_slice(&image.as_raw()[from as usize * row_bytes..]);
            image = Arc::new(out);
        }
        Ok(image)
    }
    pub fn apply_band(&mut self, a: Point, b: Point, insert: bool) {
        let y =
            a.y.min(b.y)
                .clamp(self.state.crop.y, self.state.crop.y + self.state.crop.h)
                .round() as u32;
        let end =
            a.y.max(b.y)
                .clamp(self.state.crop.y, self.state.crop.y + self.state.crop.h)
                .round() as u32;
        let height = end.saturating_sub(y);
        if height == 0 || (!insert && height as f64 >= self.state.crop.h) {
            return;
        }
        self.checkpoint();
        self.state.bands.push(Band { y, height, insert });
        let delta = if insert {
            height as f64
        } else {
            -(height as f64)
        };
        for layer in &mut self.state.layers {
            for p in &mut layer.points {
                if p.y >= y as f64 {
                    p.y = if insert {
                        p.y + delta
                    } else {
                        (p.y + delta).max(y as f64)
                    };
                }
            }
        }
        self.state.crop.h = (self.state.crop.h + delta).max(1.0);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn crop_preserves_native_pixels() {
        let mut im = RgbaImage::new(12, 8);
        for (x, y, p) in im.enumerate_pixels_mut() {
            *p = image::Rgba([x as u8, y as u8, 77, 255]);
        }
        let mut d = Document::new(im);
        d.state.crop = Rect {
            x: 3.0,
            y: 2.0,
            w: 5.0,
            h: 4.0,
        };
        let out = d.render().unwrap();
        assert_eq!(out.dimensions(), (5, 4));
        assert_eq!(*out.get_pixel(0, 0), image::Rgba([3, 2, 77, 255]));
    }
    #[test]
    fn redaction_destroys_pixels_and_undo_restores() {
        let mut d = Document::new(RgbaImage::from_pixel(10, 10, image::Rgba([255, 0, 0, 255])));
        d.checkpoint();
        d.state.layers.push(Layer {
            tool: Tool::Redact,
            points: vec![Point::new(2.0, 2.0), Point::new(8.0, 8.0)],
            color: [1.0; 4],
            width: 1.0,
            filled: false,
            text: String::new(),
            font: default_font(),
            text_style: 0,
        });
        assert_eq!(
            *d.render().unwrap().get_pixel(4, 4),
            image::Rgba([0, 0, 0, 255])
        );
        d.undo();
        assert_eq!(
            *d.render().unwrap().get_pixel(4, 4),
            image::Rgba([255, 0, 0, 255])
        );
        d.redo();
        assert_eq!(d.state.layers.len(), 1);
    }
    #[test]
    fn state_roundtrip_keeps_history() {
        let mut d = Document::new(RgbaImage::new(8, 8));
        d.checkpoint();
        d.state.backdrop = 2;
        let saved: SavedDocument =
            serde_json::from_slice(&serde_json::to_vec(&d.saved()).unwrap()).unwrap();
        let mut restored = Document::restore((*d.source).clone(), saved, d.id).unwrap();
        restored.undo();
        assert_eq!(restored.state.backdrop, 0);
    }
}

#[cfg(test)]
mod regression {
    use super::*;
    fn sample() -> Document {
        let mut im = RgbaImage::new(80, 60);
        for (x, y, p) in im.enumerate_pixels_mut() {
            *p = image::Rgba([x as u8, y as u8, 80, 255]);
        }
        Document::new(im)
    }
    fn layer(tool: Tool) -> Layer {
        Layer {
            tool,
            points: vec![Point::new(12.0, 12.0), Point::new(65.0, 45.0)],
            color: [1.0, 0.2, 0.3, 1.0],
            width: 2.0,
            filled: false,
            text: "Test 12".into(),
            font: default_font(),
            text_style: 0,
        }
    }
    #[test]
    fn cut_and_insert_replay_exactly() {
        let mut d = sample();
        let original = d.render().unwrap();
        d.apply_band(Point::new(0.0, 10.0), Point::new(0.0, 20.0), false);
        let cut = d.render().unwrap();
        assert_eq!(cut.dimensions(), (80, 50));
        assert_eq!(cut.get_pixel(5, 10), original.get_pixel(5, 20));
        d.undo();
        assert_eq!(d.render().unwrap(), original);
        d.apply_band(Point::new(0.0, 10.0), Point::new(0.0, 15.0), true);
        let insert = d.render().unwrap();
        assert_eq!(insert.dimensions(), (80, 65));
        assert_eq!(insert.get_pixel(5, 12)[3], 0);
        assert_eq!(insert.get_pixel(5, 15), original.get_pixel(5, 10));
    }
    #[test]
    fn all_annotation_renderers_export() {
        for tool in [
            Tool::Arrow,
            Tool::DoubleArrow,
            Tool::Curve,
            Tool::Line,
            Tool::Pen,
            Tool::Highlight,
            Tool::Rectangle,
            Tool::Ellipse,
            Tool::Marker,
            Tool::Text,
            Tool::Redact,
            Tool::Pixelate,
            Tool::Spotlight,
        ] {
            let mut d = sample();
            d.state.layers.push(layer(tool));
            let rendered = d.render().unwrap();
            assert_eq!(rendered.dimensions(), (80, 60));
            assert_ne!(rendered, *d.source, "{tool:?} did not render");
        }
    }
    #[test]
    fn growing_and_backdrops_preserve_the_source() {
        let mut d = sample();
        let original = d.source.get_pixel(0, 0);
        let mut l = layer(Tool::Rectangle);
        l.points = vec![Point::new(-20.0, -20.0), Point::new(-5.0, -5.0)];
        d.state.layers.push(l);
        assert_eq!(d.render().unwrap().dimensions(), (80, 60));
        d.state.grow = true;
        assert!(d.render().unwrap().width() > 80);
        d.state.layers.clear();
        d.state.backdrop = 1;
        let out = d.render().unwrap();
        assert_eq!(out.dimensions(), (144, 124));
        assert_eq!(out.get_pixel(32, 32), original);
    }
    #[test]
    fn pixelation_after_cut_samples_the_composed_source() {
        let mut d = sample();
        d.apply_band(Point::new(0.0, 10.0), Point::new(0.0, 20.0), false);
        let mut l = layer(Tool::Pixelate);
        l.points = vec![Point::new(10.0, 10.0), Point::new(30.0, 30.0)];
        d.state.layers.push(l);
        assert_eq!(d.render().unwrap().get_pixel(12, 12)[1], 20);
    }
}
#[cfg(test)]
mod fixture {
    use super::*;
    #[test]
    fn text_fixture_roundtrips_through_png() {
        let mut d = Document::new(RgbaImage::from_pixel(
            900,
            540,
            image::Rgba([245, 245, 250, 255]),
        ));
        d.state.layers.push(Layer {
            tool: Tool::Text,
            points: vec![Point::new(40.0, 60.0), Point::new(40.0, 60.0)],
            color: [0.04, 0.04, 0.08, 1.0],
            width: 6.0,
            filled: false,
            text: "DUSKY SNAP TEST 123".into(),
            font: "Sans".into(),
            text_style: 0,
        });
        let rendered = d.render().unwrap();
        let bytes = crate::output::encode(&rendered).unwrap();
        assert_eq!(
            image::load_from_memory(&bytes).unwrap().into_rgba8(),
            rendered
        );
        if let Some(path) = std::env::var_os("DUSKY_SNAP_TEST_FIXTURE") {
            std::fs::write(path, bytes).unwrap();
        }
    }
}

#[cfg(test)]
mod fractional_crop {
    use super::*;
    #[test]
    fn fractional_selection_keeps_native_pixels() {
        let mut source = RgbaImage::new(16, 12);
        for (x, y, p) in source.enumerate_pixels_mut() {
            *p = image::Rgba([x as u8, y as u8, 73, 255]);
        }
        let mut doc = Document::new(source);
        doc.state.crop = Rect {
            x: 3.25,
            y: 2.5,
            w: 5.5,
            h: 4.25,
        };
        let image = doc.render().unwrap();
        assert_eq!(image.dimensions(), (6, 5));
        assert_eq!(image.get_pixel(0, 0), doc.source.get_pixel(3, 2));
        assert_eq!(image.get_pixel(5, 4), doc.source.get_pixel(8, 6));
    }
}

#[cfg(test)]
mod blur_regression {
    use super::*;
    #[test]
    fn blur_softens_only_selected_pixels_and_undo_restores_source() {
        let mut im = RgbaImage::new(96, 72);
        for (x, y, p) in im.enumerate_pixels_mut() {
            let v = if (x / 3 + y / 3) % 2 == 0 { 0 } else { 255 };
            *p = image::Rgba([v, v, v, 255]);
        }
        let mut d = Document::new(im.clone());
        d.checkpoint();
        d.state.layers.push(Layer {
            tool: Tool::Blur,
            points: vec![Point::new(20.0, 15.0), Point::new(70.0, 55.0)],
            color: [0.0; 4],
            width: 5.0,
            filled: false,
            text: String::new(),
            font: default_font(),
            text_style: 0,
        });
        let blurred = d.render().unwrap();
        for (x, y, p) in blurred.enumerate_pixels() {
            if (20..70).contains(&x) && (15..55).contains(&y) {
                assert!((50..205).contains(&p[0]));
            } else {
                assert_eq!(p, im.get_pixel(x, y));
            }
        }
        let saved = d.saved();
        let restored = Document::restore(im.clone(), saved, d.id.clone()).unwrap();
        assert_eq!(restored.render().unwrap(), blurred);
        d.undo();
        assert_eq!(d.render().unwrap(), im);
        d.redo();
        assert_eq!(d.render().unwrap(), blurred);
        d.state.crop = Rect {
            x: 24.0,
            y: 18.0,
            w: 40.0,
            h: 30.0,
        };
        assert_eq!(d.render().unwrap().dimensions(), (40, 30));
    }
}
