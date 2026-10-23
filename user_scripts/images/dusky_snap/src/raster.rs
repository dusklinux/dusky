//! CPU export in Rust. Unchanged screenshot pixels are copied byte for byte;
//! antialiased vectors are composited only where they contribute ink.
use crate::{
    Result,
    document::{Document, Layer, Point, Rect, Tool},
};
use image::{Rgba, RgbaImage};
use std::cell::RefCell;
use tiny_skia::{FillRule, LineCap, LineJoin, Paint, PathBuilder, Pixmap, Stroke, Transform};

pub fn render(doc: &Document) -> Result<RgbaImage> {
    let canvas = doc.canvas();
    let pad = if doc.state.backdrop > 0 { 32 } else { 0 };
    let w = canvas.w as u32 + pad * 2;
    let h = canvas.h as u32 + pad * 2;
    if w == 0 || h == 0 || u64::from(w) * u64::from(h) > 160_000_000 {
        return Err("Canvas exceeds the 160 megapixel render budget".into());
    }
    let mut out = RgbaImage::new(w, h);
    if doc.state.backdrop > 0 {
        let colors = [[31, 33, 43], [71, 56, 135], [15, 97, 110], [140, 61, 82]];
        let rgb = colors[(doc.state.backdrop as usize - 1) % colors.len()];
        for (x, y, p) in out.enumerate_pixels_mut() {
            let t = 0.55 * (x as f64 / w as f64 + y as f64 / h as f64) / 2.0;
            let shadow = if doc.state.shadow {
                let dx = (pad as f64 - x as f64)
                    .max(x as f64 - (w - pad) as f64)
                    .max(0.0);
                let dy = (pad as f64 + 4.0 - y as f64)
                    .max(y as f64 - (h - pad) as f64 - 4.0)
                    .max(0.0);
                1.0 - 0.25 * (-(dx * dx + dy * dy) / 72.0).exp()
            } else {
                1.0
            };
            *p = Rgba([
                ((rgb[0] as f64) * (1.0 - t) * shadow) as u8,
                ((rgb[1] as f64) * (1.0 - t) * shadow) as u8,
                ((rgb[2] as f64) * (1.0 - t) * shadow) as u8,
                255,
            ]);
        }
    }
    let source = doc.composed_source()?;
    let crop = doc.state.crop.pixel_bounds().intersect(Rect {
        x: 0.0,
        y: 0.0,
        w: source.width() as f64,
        h: source.height() as f64,
    });
    let dx = pad as i32 - canvas.x as i32;
    let dy = pad as i32 - canvas.y as i32;
    if doc.state.backdrop == 0 {
        let region = crop.intersect(Rect {
            x: -dx as f64,
            y: -dy as f64,
            w: w as f64,
            h: h as f64,
        });
        if region.w > 0.0 && region.h > 0.0 {
            let bytes = region.w as usize * 4;
            for y in region.y as u32..(region.y + region.h) as u32 {
                let from = (y as usize * source.width() as usize + region.x as usize) * 4;
                let to =
                    ((y as i32 + dy) as usize * w as usize + (region.x as i32 + dx) as usize) * 4;
                out.as_mut()[to..to + bytes].copy_from_slice(&source.as_raw()[from..from + bytes]);
            }
        }
    } else {
        for y in crop.y as u32..(crop.y + crop.h) as u32 {
            for x in crop.x as u32..(crop.x + crop.w) as u32 {
                let (ox, oy) = (x as i32 + dx, y as i32 + dy);
                if ox >= 0 && oy >= 0 && ox < w as i32 && oy < h as i32 {
                    image::Pixel::blend(
                        out.get_pixel_mut(ox as u32, oy as u32),
                        source.get_pixel(x, y),
                    );
                }
            }
        }
    }
    for layer in doc
        .state
        .layers
        .iter()
        .filter(|l| matches!(l.tool, Tool::Redact | Tool::Pixelate | Tool::Blur))
    {
        let Some(&a) = layer.points.first() else {
            continue;
        };
        let b = *layer.points.last().unwrap_or(&a);
        let rect = Rect::between(a, b).pixel_bounds().intersect(canvas);
        if layer.tool == Tool::Blur {
            let region = rect.intersect(Rect {
                x: -dx as f64,
                y: -dy as f64,
                w: w as f64,
                h: h as f64,
            });
            if region.w < 1.0 || region.h < 1.0 {
                continue;
            }
            let radius = layer.width.clamp(1.0, 80.0) as f32;
            let halo = (radius * 3.0).ceil() as u32;
            let x = (region.x as i32 + dx) as u32;
            let y = (region.y as i32 + dy) as u32;
            let left = x.saturating_sub(halo);
            let top = y.saturating_sub(halo);
            let right = (x + region.w as u32).saturating_add(halo).min(w);
            let bottom = (y + region.h as u32).saturating_add(halo).min(h);
            let sample =
                image::imageops::crop_imm(&out, left, top, right - left, bottom - top).to_image();
            let blurred = image::imageops::blur(&sample, radius);
            let patch = image::imageops::crop_imm(
                &blurred,
                x - left,
                y - top,
                region.w as u32,
                region.h as u32,
            );
            image::imageops::replace(&mut out, &patch.to_image(), x as i64, y as i64);
            continue;
        }
        let step = (layer.width * 4.0).max(8.0) as i32;
        for y in rect.y as i32..(rect.y + rect.h) as i32 {
            for x in rect.x as i32..(rect.x + rect.w) as i32 {
                let (ox, oy) = (x + dx, y + dy);
                if ox < 0 || oy < 0 || ox >= w as i32 || oy >= h as i32 {
                    continue;
                }
                let pixel = if layer.tool == Tool::Redact {
                    Rgba([0, 0, 0, 255])
                } else {
                    let sx = (rect.x as i32 + ((x - rect.x as i32) / step) * step)
                        .clamp(0, source.width() as i32 - 1) as u32;
                    let sy = (rect.y as i32 + ((y - rect.y as i32) / step) * step)
                        .clamp(0, source.height() as i32 - 1) as u32;
                    let p = source.get_pixel(sx, sy);
                    Rgba([p[0], p[1], p[2], 255])
                };
                *out.get_pixel_mut(ox as u32, oy as u32) = pixel;
            }
        }
    }
    let mut vectors = doc
        .state
        .layers
        .iter()
        .filter(|l| {
            !matches!(
                l.tool,
                Tool::Redact | Tool::Pixelate | Tool::Blur | Tool::Cut | Tool::Insert
            )
        })
        .peekable();
    if vectors.peek().is_none() {
        return Ok(out);
    }
    let mut overlay = Pixmap::new(w, h).ok_or("Cannot allocate annotation canvas")?;
    for layer in vectors {
        draw_layer(&mut overlay, layer, dx as f32, dy as f32)?;
    }
    for (dst, src) in out
        .pixels_mut()
        .zip(overlay.data().as_chunks::<4>().0.iter())
    {
        let a = src[3] as u32;
        if a == 0 {
            continue;
        }
        let da = dst[3] as u32;
        let alpha = a + (da * (255 - a) + 127) / 255;
        for k in 0..3 {
            dst[k] =
                ((src[k] as u32 * 255 + (dst[k] as u32 * da * (255 - a) + 127) / 255 + alpha / 2)
                    / alpha)
                    .min(255) as u8;
        }
        dst[3] = alpha as u8;
    }
    Ok(out)
}
fn paint(color: [f64; 4]) -> Paint<'static> {
    let mut paint = Paint::default();
    paint.set_color_rgba8(
        (color[0] * 255.0) as u8,
        (color[1] * 255.0) as u8,
        (color[2] * 255.0) as u8,
        (color[3] * 255.0) as u8,
    );
    paint
}
fn head(path: &mut PathBuilder, tip: Point, tail: Point, width: f64) {
    let angle = (tip.y - tail.y).atan2(tip.x - tail.x);
    let size = (width * 4.0).max(12.0);
    path.move_to(tip.x as f32, tip.y as f32);
    path.line_to(
        (tip.x - size * (angle - 0.45).cos()) as f32,
        (tip.y - size * (angle - 0.45).sin()) as f32,
    );
    path.line_to(
        (tip.x - size * (angle + 0.45).cos()) as f32,
        (tip.y - size * (angle + 0.45).sin()) as f32,
    );
    path.close();
}
fn draw_layer(pix: &mut Pixmap, l: &Layer, dx: f32, dy: f32) -> Result<()> {
    let Some(&a) = l.points.first() else {
        return Ok(());
    };
    let b = *l.points.last().unwrap_or(&a);
    let r = Rect::between(a, b);
    let transform = Transform::from_translate(dx, dy);
    let mut color = l.color;
    if l.tool == Tool::Highlight {
        color[3] = 0.3;
    }
    let ink = paint(color);
    let mut path = PathBuilder::new();
    let stroke = Stroke {
        width: l.width as f32,
        line_cap: LineCap::Round,
        line_join: LineJoin::Round,
        ..Default::default()
    };
    let mut fill = l.filled;
    match l.tool {
        Tool::Arrow
        | Tool::DoubleArrow
        | Tool::Curve
        | Tool::Line
        | Tool::Pen
        | Tool::Highlight => {
            path.move_to(a.x as f32, a.y as f32);
            if l.tool == Tool::Curve {
                let mid = Point::new(
                    (a.x + b.x) / 2.0 - (b.y - a.y) * 0.2,
                    (a.y + b.y) / 2.0 + (b.x - a.x) * 0.2,
                );
                path.cubic_to(
                    mid.x as f32,
                    mid.y as f32,
                    mid.x as f32,
                    mid.y as f32,
                    b.x as f32,
                    b.y as f32,
                );
                if let Some(p) = path.finish() {
                    pix.stroke_path(&p, &ink, &stroke, transform, None);
                }
                path = PathBuilder::new();
                head(&mut path, b, mid, l.width);
                fill = true;
            } else {
                for p in &l.points[1..] {
                    path.line_to(p.x as f32, p.y as f32);
                }
                if l.points.len() == 1 {
                    path.line_to(a.x as f32 + 0.01, a.y as f32);
                }
                let mut stroke = stroke.clone();
                if l.tool == Tool::Highlight {
                    stroke.width *= 5.0;
                }
                if let Some(p) = path.finish() {
                    pix.stroke_path(&p, &ink, &stroke, transform, None);
                }
                path = PathBuilder::new();
                if matches!(l.tool, Tool::Arrow | Tool::DoubleArrow) {
                    head(&mut path, b, a, l.width);
                    fill = true;
                }
                if l.tool == Tool::DoubleArrow {
                    head(&mut path, a, b, l.width);
                }
            }
        }
        Tool::Rectangle => {
            if let Some(r) =
                tiny_skia::Rect::from_xywh(r.x as f32, r.y as f32, r.w as f32, r.h as f32)
            {
                path.push_rect(r);
            }
        }
        Tool::Ellipse => {
            if let Some(r) = tiny_skia::Rect::from_xywh(
                r.x as f32,
                r.y as f32,
                r.w.max(0.1) as f32,
                r.h.max(0.1) as f32,
            ) {
                path.push_oval(r);
            }
        }
        Tool::Marker => {
            path.push_circle(a.x as f32, a.y as f32, (l.width * 4.0 + 9.0) as f32);
            if let Some(p) = path.finish() {
                pix.fill_path(&p, &ink, FillRule::Winding, transform, None);
            }
            text(
                pix,
                &l.text,
                "Sans",
                (l.width * 4.0 + 8.0) as f32,
                a.x as f32 + dx,
                a.y as f32 + dy,
                [1.0; 4],
                0,
                true,
            );
            return Ok(());
        }
        Tool::Text => {
            text(
                pix,
                &l.text,
                &l.font,
                (l.width * 5.0 + 12.0) as f32,
                a.x as f32 + dx,
                a.y as f32 + dy,
                l.color,
                l.text_style,
                false,
            );
            return Ok(());
        }
        Tool::Spotlight => {
            path.push_rect(
                tiny_skia::Rect::from_xywh(-dx, -dy, pix.width() as f32, pix.height() as f32)
                    .unwrap(),
            );
            if let Some(r) =
                tiny_skia::Rect::from_xywh(r.x as f32, r.y as f32, r.w as f32, r.h as f32)
            {
                path.push_rect(r);
            }
            if let Some(p) = path.finish() {
                pix.fill_path(
                    &p,
                    &paint([0.0, 0.0, 0.0, 0.55]),
                    FillRule::EvenOdd,
                    transform,
                    None,
                );
            }
            return Ok(());
        }
        _ => return Ok(()),
    }
    if let Some(p) = path.finish() {
        if fill {
            pix.fill_path(&p, &ink, FillRule::Winding, transform, None);
        } else {
            pix.stroke_path(&p, &ink, &stroke, transform, None);
        }
    }
    Ok(())
}
thread_local! {static FONTS:RefCell<(cosmic_text::FontSystem,cosmic_text::SwashCache)>=RefCell::new((cosmic_text::FontSystem::new(),cosmic_text::SwashCache::new()));}
#[allow(clippy::too_many_arguments)]
fn text(
    pix: &mut Pixmap,
    value: &str,
    family: &str,
    size: f32,
    x: f32,
    y: f32,
    color: [f64; 4],
    style: u8,
    center: bool,
) {
    use cosmic_text::{Attrs, Buffer, Family, Metrics, Shaping};
    FONTS.with(|fonts| {
        let mut fonts = fonts.borrow_mut();
        let (fonts, cache) = &mut *fonts;
        let mut buffer = Buffer::new(fonts, Metrics::new(size, size * 1.3));
        buffer.set_size(fonts, None, None);
        let family = if family == "Sans" {
            Family::SansSerif
        } else {
            Family::Name(family)
        };
        buffer.set_text(
            fonts,
            value,
            &Attrs::new().family(family),
            Shaping::Advanced,
            None,
        );
        buffer.shape_until_scroll(fonts, false);
        let width = buffer.layout_runs().map(|r| r.line_w).fold(0.0, f32::max);
        let height = buffer.layout_runs().count() as f32 * size * 1.3;
        let (x, y) = if center {
            (x - width / 2.0, y - height / 2.0)
        } else {
            (x, y)
        };
        if style == 2
            && let Some(r) =
                tiny_skia::Rect::from_xywh(x - 6.0, y - 3.0, width + 12.0, height + 6.0)
        {
            pix.fill_rect(
                r,
                &paint([0.04, 0.05, 0.08, 0.92]),
                Transform::identity(),
                None,
            );
        }
        let mut pixels = Vec::new();
        buffer.draw(
            fonts,
            cache,
            cosmic_text::Color::rgba(
                (color[0] * 255.0) as u8,
                (color[1] * 255.0) as u8,
                (color[2] * 255.0) as u8,
                (color[3] * 255.0) as u8,
            ),
            |gx, gy, w, h, c| {
                pixels.push((gx, gy, w, h, c));
            },
        );
        if style == 1 {
            for &(gx, gy, w, h, c) in &pixels {
                if let Some(r) = tiny_skia::Rect::from_xywh(
                    x + gx as f32 - 1.0,
                    y + gy as f32 - 1.0,
                    w as f32 + 2.0,
                    h as f32 + 2.0,
                ) {
                    pix.fill_rect(
                        r,
                        &paint([0.02, 0.02, 0.03, c.a() as f64 / 255.0]),
                        Transform::identity(),
                        None,
                    );
                }
            }
        }
        for (gx, gy, w, h, c) in pixels {
            if let Some(r) =
                tiny_skia::Rect::from_xywh(x + gx as f32, y + gy as f32, w as f32, h as f32)
            {
                pix.fill_rect(
                    r,
                    &paint([
                        c.r() as f64 / 255.0,
                        c.g() as f64 / 255.0,
                        c.b() as f64 / 255.0,
                        c.a() as f64 / 255.0,
                    ]),
                    Transform::identity(),
                    None,
                );
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn row_copies_preserve_transparency_crops_and_grown_origins() {
        let mut source = RgbaImage::new(17, 13);
        for (x, y, p) in source.enumerate_pixels_mut() {
            *p = Rgba([x as u8, y as u8, 73, ((x + y) * 7) as u8]);
        }
        for crop in [
            Rect {
                x: 2.25,
                y: 1.5,
                w: 9.5,
                h: 7.25,
            },
            Rect {
                x: -3.0,
                y: -2.0,
                w: 12.0,
                h: 10.0,
            },
            Rect {
                x: 14.0,
                y: 11.0,
                w: 8.0,
                h: 6.0,
            },
            Rect {
                x: 22.0,
                y: 20.0,
                w: 4.0,
                h: 3.0,
            },
        ] {
            for grow in [false, true] {
                let mut doc = Document::new(source.clone());
                doc.state.crop = crop;
                doc.state.grow = grow;
                doc.state.layers.push(Layer {
                    tool: Tool::Select,
                    points: vec![Point::new(-4.0, -3.0), Point::new(-1.0, -1.0)],
                    color: [0.0; 4],
                    width: 1.0,
                    filled: false,
                    text: String::new(),
                    font: "Sans".into(),
                    text_style: 0,
                });
                let canvas = doc.canvas();
                let out = doc.render().unwrap();
                for (x, y, p) in out.enumerate_pixels() {
                    let sx = canvas.x as i32 + x as i32;
                    let sy = canvas.y as i32 + y as i32;
                    let expected = if (0..17).contains(&sx)
                        && (0..13).contains(&sy)
                        && crop
                            .pixel_bounds()
                            .contains(Point::new(sx as f64, sy as f64))
                    {
                        *source.get_pixel(sx as u32, sy as u32)
                    } else {
                        Rgba([0; 4])
                    };
                    assert_eq!(*p, expected, "crop={crop:?}, grow={grow}, at {sx},{sy}");
                }
            }
        }
    }
    #[test]
    fn untouched_rgba_pixels_survive_without_premultiplication_loss() {
        let mut source = RgbaImage::new(17, 13);
        for (x, y, p) in source.enumerate_pixels_mut() {
            *p = Rgba([(x * 13) as u8, (y * 19) as u8, 73, ((x + y) * 7) as u8]);
        }
        let doc = Document::new(source.clone());
        assert_eq!(doc.render().unwrap(), source);
    }
}
