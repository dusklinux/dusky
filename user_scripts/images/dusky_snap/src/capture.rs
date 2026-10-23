use crate::{
    Options, Result,
    document::{Document, Point, Rect},
};
use image::RgbaImage;
use serde::Deserialize;
use std::time::Duration;
#[cfg(test)]
use std::time::Instant;

#[derive(Clone, Debug, Default, Deserialize)]
pub struct Workspace {
    pub address: String,
}
#[derive(Clone, Debug, Default, Deserialize)]
pub struct Monitor {
    pub name: String,
    pub x: i32,
    pub y: i32,
    #[serde(default)]
    pub width: u32,
    #[serde(default)]
    pub height: u32,
    #[serde(default)]
    pub scale: f64,
    #[serde(default)]
    pub transform: u32,
    #[serde(default)]
    pub reserved: [i32; 4],
    #[serde(default)]
    pub focused: bool,
    #[serde(default, rename = "activeWorkspace")]
    pub active_workspace: Workspace,
    #[serde(default, rename = "specialWorkspace")]
    pub special_workspace: Workspace,
}
impl Monitor {
    pub fn logical_size(&self) -> (i32, i32) {
        let scale = self.scale.max(0.1);
        let (w, h) = if self.transform % 2 == 1 {
            (self.height, self.width)
        } else {
            (self.width, self.height)
        };
        (
            (w as f64 / scale).round() as i32,
            (h as f64 / scale).round() as i32,
        )
    }
}
pub fn focused_monitor() -> Result<Monitor> {
    let monitors: Vec<Monitor> = serde_json::from_slice(&command(
        "hyprctl",
        &["-j", "monitors"],
        None,
        Duration::from_secs(3),
    )?)?;
    monitors
        .into_iter()
        .find(|m| m.focused)
        .ok_or_else(|| "No focused Hyprland output".into())
}
#[derive(Clone, Debug, Deserialize)]
struct Client {
    at: [f64; 2],
    size: [f64; 2],
    workspace: Workspace,
    #[serde(default)]
    hidden: bool,
    #[serde(default)]
    pinned: bool,
    #[serde(default)]
    floating: bool,
    #[serde(default, rename = "focusHistoryID")]
    order: i64,
}
#[derive(Clone)]
pub struct Capture {
    pub document: Document,
    pub monitor: Monitor,
    pub windows: Vec<Rect>,
    pub pointer: Option<Point>,
}

fn parse_snapshot(bytes: &[u8]) -> Result<(Monitor, Vec<Client>, Point)> {
    // hyprctl batches concatenate JSON values, separated by whitespace.
    let mut json = serde_json::Deserializer::from_slice(bytes);
    let monitors = Vec::<Monitor>::deserialize(&mut json)?;
    let clients = Vec::<Client>::deserialize(&mut json)?;
    let pointer = Point::deserialize(&mut json)?;
    json.end()?;
    let monitor = monitors
        .into_iter()
        .find(|m| m.focused)
        .ok_or("No focused Hyprland output")?;
    Ok((monitor, clients, pointer))
}

fn pointer_to_source(pointer: Point, sx: f64, sy: f64, monitor: &Monitor) -> Point {
    Point::new(
        (pointer.x - monitor.x as f64) * sx,
        (pointer.y - monitor.y as f64) * sy,
    )
}

fn window_rects(
    mut clients: Vec<Client>,
    monitor: &Monitor,
    sx: f64,
    sy: f64,
    bounds: Rect,
) -> Vec<Rect> {
    // Hyprland hit-tests pinned windows before ordinary floating/tiled windows.
    // Focus history alone can put a focused tiled window above its pinned overlay.
    clients.sort_by_key(|c| (!c.pinned, !c.floating, c.order));
    let workspace = if monitor.special_workspace.address.is_empty() {
        &monitor.active_workspace.address
    } else {
        &monitor.special_workspace.address
    };
    clients
        .into_iter()
        .filter(|c| !c.hidden && (c.pinned || c.workspace.address == *workspace))
        .map(|c| {
            Rect {
                x: (c.at[0] - monitor.x as f64) * sx,
                y: (c.at[1] - monitor.y as f64) * sy,
                w: c.size[0] * sx,
                h: c.size[1] * sy,
            }
            .intersect(bounds)
        })
        .filter(|r| r.w > 0.0 && r.h > 0.0)
        .collect()
}

pub use crate::process::command;

pub fn take(o: &Options) -> Result<Capture> {
    // One compositor snapshot, before our own window can affect focus/history.
    let snapshot = command(
        "hyprctl",
        &["-j", "--batch", "monitors; clients; cursorpos"],
        None,
        Duration::from_secs(3),
    )?;
    let (monitor, clients, pointer) = parse_snapshot(&snapshot)?;
    let conn = libwayshot::WayshotConnection::new()?;
    let output = conn
        .get_all_outputs()
        .iter()
        .find(|m| m.name == monitor.name)
        .ok_or("Focused output was disconnected")?;
    let source = conn
        .screenshot_single_output(output, o.cursor)?
        .into_rgba8();
    let sx = source.width() as f64 / output.logical_size().width as f64;
    let sy = source.height() as f64 / output.logical_size().height as f64;
    let bounds = Rect {
        x: 0.0,
        y: 0.0,
        w: source.width() as f64,
        h: source.height() as f64,
    };
    let windows = window_rects(clients, &monitor, sx, sy, bounds);
    let pointer = pointer_to_source(pointer, sx, sy, &monitor);
    Ok(Capture {
        document: Document::new(source),
        monitor,
        windows,
        pointer: bounds.contains(pointer).then_some(pointer),
    })
}
pub fn load(o: &Options) -> Result<Capture> {
    if o.file.is_none() && !o.clipboard {
        return take(o);
    }
    let document = if let Some(path) = &o.file {
        if path.extension().is_some_and(|e| e == "json") {
            crate::output::load_document(path)?
        } else {
            Document::new(image::open(path)?.into_rgba8())
        }
    } else {
        Document::new(
            image::load_from_memory(&command(
                "wl-paste",
                &["--no-newline", "--type", "image/png"],
                None,
                Duration::from_secs(5),
            )?)?
            .into_rgba8(),
        )
    };
    let monitor = focused_monitor()?;
    Ok(Capture {
        document,
        monitor,
        windows: vec![],
        pointer: None,
    })
}
pub fn frame(monitor: &Monitor, crop: Rect, cursor: bool) -> Result<RgbaImage> {
    let conn = libwayshot::WayshotConnection::new()?;
    let output = conn
        .get_all_outputs()
        .iter()
        .find(|m| m.name == monitor.name)
        .ok_or("Output was disconnected")?;
    let image = conn.screenshot_single_output(output, cursor)?.into_rgba8();
    let x = crop.x.max(0.0) as u32;
    let y = crop.y.max(0.0) as u32;
    let w = (crop.w as u32).min(image.width().saturating_sub(x));
    let h = (crop.h as u32).min(image.height().saturating_sub(y));
    if w == 0 || h == 0 {
        return Err("Scroll region is outside the output".into());
    }
    Ok(image::imageops::crop_imm(&image, x, y, w, h).to_image())
}

// Match the new frame against the tail, requiring agreement across multiple columns.
// Ambiguous or unrelated frames are rejected instead of silently corrupting the stitch.
pub fn stitch(previous: &RgbaImage, next: &RgbaImage) -> Result<RgbaImage> {
    if previous.width() != next.width() || next.height() < 16 {
        return Err("Scroll frame dimensions changed".into());
    }
    let max_overlap = previous.height().min(next.height());
    if previous.height() >= next.height()
        && previous.as_raw()[previous.as_raw().len() - next.as_raw().len()..] == *next.as_raw()
    {
        return Ok(previous.clone());
    }
    let mut best = (f64::MAX, 0u32);
    for overlap in 12..max_overlap {
        let mut error = 0u64;
        let mut samples = 0u64;
        for row in (0..overlap).step_by((overlap / 12).max(1) as usize) {
            for x in (next.width() / 10..next.width() * 9 / 10)
                .step_by((next.width() / 32).max(1) as usize)
            {
                let a = previous.get_pixel(x, previous.height() - overlap + row);
                let b = next.get_pixel(x, row);
                for i in 0..3 {
                    error += a[i].abs_diff(b[i]) as u64;
                }
                samples += 3;
            }
        }
        let score = error as f64 / samples.max(1) as f64;
        if score <= best.0 {
            best = (score, overlap);
        }
    }
    let sample = next.as_raw();
    let texture = sample
        .as_chunks::<4>()
        .0
        .iter()
        .step_by((sample.len() / 4 / 4096).max(1))
        .filter(|p| p[..3] != sample[..3])
        .count();
    if texture < 8 {
        return Err("Frame has too little detail for reliable alignment".into());
    }
    if best.0 > 6.0 {
        return Err(format!(
            "Cannot align frames (error {:.1}); scroll less and capture again",
            best.0
        )
        .into());
    }
    let h = previous.height() + next.height() - best.1;
    if u64::from(h) * u64::from(next.width()) > 160_000_000 {
        return Err("Scroll capture reached its 160 megapixel budget".into());
    }
    let mut out = RgbaImage::new(next.width(), h);
    let previous_bytes = previous.as_raw().len();
    out.as_mut()[..previous_bytes].copy_from_slice(previous.as_raw());
    out.as_mut()[previous_bytes..]
        .copy_from_slice(&next.as_raw()[best.1 as usize * next.width() as usize * 4..]);
    Ok(out)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn stitch_scrolled_fixture() {
        let mut page = RgbaImage::new(80, 180);
        for (x, y, p) in page.enumerate_pixels_mut() {
            *p = image::Rgba([
                ((x * 37 + y * 23) % 251) as u8,
                ((y * 11 + x * 3) % 253) as u8,
                (y % 255) as u8,
                255,
            ]);
        }
        let a = image::imageops::crop_imm(&page, 0, 0, 80, 110).to_image();
        let b = image::imageops::crop_imm(&page, 0, 70, 80, 110).to_image();
        assert_eq!(stitch(&a, &b).unwrap(), page);
        assert_eq!(stitch(&a, &a).unwrap(), a);
    }
}

#[derive(Clone)]
pub struct Stitcher {
    pub image: RgbaImage,
    last: RgbaImage,
}
impl Stitcher {
    pub fn new(image: RgbaImage) -> Self {
        Self {
            last: image.clone(),
            image,
        }
    }
    pub fn append(&mut self, next: RgbaImage) -> Result<bool> {
        if self.last.dimensions() != next.dimensions() {
            return Err("Scroll frame dimensions changed".into());
        }
        if self.last == next {
            return Ok(false);
        }
        let h = next.height();
        let w = next.width();
        let same_row = |y: u32| -> bool {
            let start = y as usize * w as usize * 4;
            let end = start + w as usize * 4;
            self.last.as_raw()[start..end] == next.as_raw()[start..end]
        };
        let top = (0..h / 4).take_while(|&y| same_row(y)).count() as u32;
        let bottom = (0..h / 4)
            .take_while(|&offset| same_row(h - 1 - offset))
            .count() as u32;
        let old = (bottom > 0).then(|| {
            image::imageops::crop_imm(&self.image, 0, 0, w, self.image.height() - bottom).to_image()
        });
        let content = (top > 0 || bottom > 0)
            .then(|| image::imageops::crop_imm(&next, 0, top, w, h - top - bottom).to_image());
        let joined = stitch(
            old.as_ref().unwrap_or(&self.image),
            content.as_ref().unwrap_or(&next),
        )?;
        self.image = if bottom == 0 {
            joined
        } else {
            let mut image = RgbaImage::new(w, joined.height() + bottom);
            let bytes = joined.as_raw().len();
            image.as_mut()[..bytes].copy_from_slice(joined.as_raw());
            image.as_mut()[bytes..]
                .copy_from_slice(&next.as_raw()[(h - bottom) as usize * w as usize * 4..]);
            image
        };
        self.last = next;
        Ok(true)
    }
}

#[cfg(test)]
mod scrolling_regression {
    use super::*;
    #[test]
    fn sticky_header_and_footer_are_kept_once() {
        let mut page = RgbaImage::new(80, 200);
        for (x, y, p) in page.enumerate_pixels_mut() {
            *p = image::Rgba([
                ((x * 17 + y * 23) % 251) as u8,
                ((y * 7 + x * 3) % 253) as u8,
                (y % 255) as u8,
                255,
            ]);
        }
        let viewport = |y| {
            let content = image::imageops::crop_imm(&page, 0, y, 80, 80).to_image();
            let mut frame = RgbaImage::from_pixel(80, 100, image::Rgba([33, 44, 55, 255]));
            image::imageops::replace(&mut frame, &content, 0, 10);
            frame
        };
        let mut s = Stitcher::new(viewport(0));
        s.append(viewport(50)).unwrap();
        s.append(viewport(100)).unwrap();
        assert_eq!(s.image.dimensions(), (80, 200));
        let body = image::imageops::crop_imm(&s.image, 0, 10, 80, 180).to_image();
        assert_eq!(
            body,
            image::imageops::crop_imm(&page, 0, 0, 80, 180).to_image()
        );
    }
    #[test]
    fn unrelated_frames_fail_instead_of_corrupting_output() {
        let a = RgbaImage::from_pixel(80, 60, image::Rgba([0, 0, 0, 255]));
        let b = RgbaImage::from_pixel(80, 60, image::Rgba([255, 255, 255, 255]));
        assert!(stitch(&a, &b).is_err());
    }
}
#[cfg(test)]
mod process_regression {
    use super::*;
    #[test]
    fn timeout_reaps_descendants_and_drains_pipes() {
        let start = Instant::now();
        let result = command("sh", &["-c", "sleep 30"], None, Duration::from_millis(50));
        assert!(result.is_err());
        assert!(start.elapsed() < Duration::from_secs(2));
    }
}

#[cfg(test)]
mod workspace_regression {
    use super::*;

    #[test]
    fn pinned_windows_win_over_focused_windows_across_workspaces() {
        for workspace in ["1", "2", "special:magic"] {
            let clients: Vec<Client> = serde_json::from_value(serde_json::json!([
                {"at":[0,0],"size":[640,480],"workspace":{"address":workspace},"focusHistoryID":0},
                {"at":[100,80],"size":[200,150],"workspace":{"address":"1"},"floating":true,"pinned":true,"focusHistoryID":9},
                {"at":[90,70],"size":[240,180],"workspace":{"address":workspace},"floating":true,"focusHistoryID":1},
                {"at":[0,0],"size":[640,480],"workspace":{"address":"other"},"floating":true,"pinned":true,"hidden":true,"focusHistoryID":0},
                {"at":[900,900],"size":[200,150],"workspace":{"address":"other"},"floating":true,"pinned":true,"focusHistoryID":0},
                {"at":[0,0],"size":[640,480],"workspace":{"address":"other"},"focusHistoryID":0}
            ])).unwrap();
            let monitor = Monitor {
                active_workspace: Workspace {
                    address: workspace.into(),
                },
                special_workspace: Workspace {
                    address: if workspace.starts_with("special:") {
                        workspace.into()
                    } else {
                        String::new()
                    },
                },
                ..Default::default()
            };
            let windows = window_rects(
                clients,
                &monitor,
                1.0,
                1.0,
                Rect {
                    x: 0.0,
                    y: 0.0,
                    w: 640.0,
                    h: 480.0,
                },
            );
            assert_eq!(windows.len(), 3);
            let hover = Point::new(150.0, 120.0);
            assert_eq!(
                windows.iter().find(|r| r.contains(hover)).copied(),
                Some(Rect {
                    x: 100.0,
                    y: 80.0,
                    w: 200.0,
                    h: 150.0
                })
            );
            assert_eq!(
                windows[1],
                Rect {
                    x: 90.0,
                    y: 70.0,
                    w: 240.0,
                    h: 180.0
                }
            );
        }
    }

    #[test]
    fn pinned_window_geometry_uses_monitor_offsets_scale_and_clipping() {
        let clients: Vec<Client> = serde_json::from_str(r#"[
            {"at":[-1350,150],"size":[200,100],"workspace":{"address":"other"},"pinned":true,"floating":true}
        ]"#).unwrap();
        let monitor = Monitor {
            x: -1280,
            y: 120,
            active_workspace: Workspace {
                address: "1".into(),
            },
            ..Default::default()
        };
        let bounds = Rect {
            x: 0.0,
            y: 0.0,
            w: 1920.0,
            h: 1080.0,
        };
        assert_eq!(
            window_rects(clients, &monitor, 1.5, 1.5, bounds),
            vec![Rect {
                x: 0.0,
                y: 45.0,
                w: 195.0,
                h: 150.0
            }]
        );
    }
    #[test]
    fn special_workspace_clients_do_not_need_numeric_ids() {
        let client: Client = serde_json::from_str(r#"{"at":[0,0],"size":[640,480],"workspace":{"address":"special:magic","type":"special","name":"special:magic"}}"#).unwrap();
        assert_eq!(client.workspace.address, "special:magic");
        let monitor: Monitor = serde_json::from_str(r#"{"name":"fixture","x":0,"y":0,"activeWorkspace":{"address":"2","id":2},"specialWorkspace":{"address":"","type":"","name":""}}"#).unwrap();
        assert_eq!(monitor.active_workspace.address, "2");
        assert!(monitor.special_workspace.address.is_empty());
    }

    #[test]
    fn batched_snapshot_and_scaled_pointer_use_global_monitor_offsets() {
        let bytes = br#"[{"name":"left","x":-1280,"y":120,"focused":true}]
            [] {"x":-960,"y":300}"#;
        let (monitor, clients, pointer) = parse_snapshot(bytes).unwrap();
        assert!(clients.is_empty());
        assert_eq!(
            pointer_to_source(pointer, 1.5, 1.5, &monitor),
            Point::new(480.0, 270.0)
        );
        for bad in [b"[] [] {}".as_slice(), b"[]", b"{} [] {}"] {
            assert!(parse_snapshot(bad).is_err());
        }
        let trailing = [bytes.as_slice(), b" {}"].concat();
        assert!(parse_snapshot(&trailing).is_err());
    }
}
