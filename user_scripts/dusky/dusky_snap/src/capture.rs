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
    pub fn workspace(&self) -> &str {
        if self.special_workspace.address.is_empty() {
            &self.active_workspace.address
        } else {
            &self.special_workspace.address
        }
    }
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
#[derive(Clone, Debug)]
pub struct Window {
    pub rect: Rect,
}
#[derive(Clone)]
pub struct Capture {
    pub document: Document,
    pub monitor: Monitor,
    pub windows: Vec<Window>,
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

fn window_targets(
    mut clients: Vec<Client>,
    monitor: &Monitor,
    sx: f64,
    sy: f64,
    bounds: Rect,
) -> Vec<Window> {
    // Hyprland hit-tests pinned windows before ordinary floating/tiled windows.
    // Focus history alone can put a focused tiled window above its pinned overlay.
    clients.sort_by_key(|c| (!c.pinned, !c.floating, c.order));
    let workspace = monitor.workspace();
    clients
        .into_iter()
        .filter(|c| !c.hidden && (c.pinned || c.workspace.address == workspace))
        .map(|c| {
            let rect = Rect {
                x: (c.at[0] - monitor.x as f64) * sx,
                y: (c.at[1] - monitor.y as f64) * sy,
                w: c.size[0] * sx,
                h: c.size[1] * sy,
            }
            .pixel_bounds()
            .intersect(bounds);
            Window { rect }
        })
        .filter(|w| w.rect.w > 0.0 && w.rect.h > 0.0)
        .collect()
}

pub use crate::process::command;

fn force_opaque(image: &mut RgbaImage) {
    // Physical output formats like XRGB8888 leave undefined/compositor-blended
    // values in byte 3, and libwayshot 0.9 preserves byte 3 as-is. Forcing
    // 255 guarantees the output scanout is 100% physically opaque.
    for chunk in image.as_mut().chunks_exact_mut(4) {
        chunk[3] = 255;
    }
}

fn output_image(
    conn: &libwayshot::WayshotConnection,
    output: &libwayshot::output::OutputInfo,
    cursor: bool,
) -> Result<RgbaImage> {
    let mut output = output.clone();
    if conn.image_copy_support() {
        // Hyprland sends ext-image-copy frames in NORMAL orientation, with
        // the output transform already applied. libwayshot 0.9 instead uses
        // wl_output.transform and would rotate/flip those pixels a second time.
        // WLR screencopy still needs the original output transform.
        output.transform = wayland_client::protocol::wl_output::Transform::Normal;
    }
    let mut image = conn.screenshot_single_output(output, cursor)?.into_rgba8();
    force_opaque(&mut image);
    Ok(image)
}

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
    let source = output_image(&conn, output, o.cursor)?;
    let logical = output.logical_size();
    let sx = source.width() as f64 / logical.width.max(1) as f64;
    let sy = source.height() as f64 / logical.height.max(1) as f64;
    let bounds = Rect {
        x: 0.0,
        y: 0.0,
        w: source.width() as f64,
        h: source.height() as f64,
    };
    let windows = window_targets(clients, &monitor, sx, sy, bounds);
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

#[path = "stitch.rs"]
pub mod stitch;

pub struct ScrollCapturer {
    conn: libwayshot::WayshotConnection,
    output: libwayshot::output::OutputInfo,
}

impl ScrollCapturer {
    pub fn new(monitor: &Monitor) -> Result<Self> {
        let conn = libwayshot::WayshotConnection::new()?;
        let output = conn
            .get_all_outputs()
            .iter()
            .find(|m| m.name == monitor.name)
            .cloned()
            .ok_or("Output was disconnected")?;
        Ok(Self { conn, output })
    }

    pub fn grab(&self, crop: Rect, cursor: bool) -> Result<RgbaImage> {
        let image = output_image(&self.conn, &self.output, cursor)?;
        let x = crop.x.max(0.0).round() as u32;
        let y = crop.y.max(0.0).round() as u32;
        let w = (crop.w.max(0.0).round() as u32).min(image.width().saturating_sub(x));
        let h = (crop.h.max(0.0).round() as u32).min(image.height().saturating_sub(y));
        if w == 0 || h == 0 {
            return Err("Scroll region is outside the output".into());
        }
        Ok(image::imageops::crop_imm(&image, x, y, w, h).to_image())
    }

    pub fn acquire_settled_frame(
        &self,
        crop: Rect,
        axis: stitch::Axis,
        before: Option<&stitch::GrayView>,
        stop: &std::sync::atomic::AtomicBool,
    ) -> Result<(RgbaImage, stitch::GrayView)> {
        use std::sync::atomic::Ordering;
        const MOTION_START_MAD: f64 = 1.25;

        let start = std::time::Instant::now();
        let mut moved = before.is_none();
        let mut prev: Option<(RgbaImage, stitch::GrayView)> = None;
        let mut calm_streak = 0u8;

        while !stop.load(Ordering::Acquire) {
            let current = self.grab(crop, false)?;
            let cur_gray = stitch::downsample_to_gray(&current, axis);

            if !moved {
                let diff = before.map_or(f64::INFINITY, |b| {
                    stitch::frame_diff_at_zero(b, &cur_gray, axis)
                });
                if diff > MOTION_START_MAD {
                    moved = true;
                } else if start.elapsed() >= Duration::from_millis(500) {
                    return Ok((current, cur_gray));
                } else {
                    std::thread::sleep(Duration::from_millis(25));
                    continue;
                }
            }

            if let Some((_, ref prev_gray)) = prev {
                let diff = stitch::frame_diff_at_zero(prev_gray, &cur_gray, axis);
                if diff <= stitch::STATIONARY_ERROR {
                    calm_streak += 1;
                    if calm_streak >= 2 {
                        return Ok((current, cur_gray));
                    }
                } else {
                    calm_streak = 0;
                }
            }

            prev = Some((current, cur_gray));
            if start.elapsed() >= Duration::from_millis(1500) {
                return Ok(prev.unwrap());
            }
            std::thread::sleep(Duration::from_millis(30));
        }
        Err("Scroll capture stopped".into())
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
    fn invocation_workspace_prefers_the_visible_special_workspace() {
        for normal in ["1", "name:work"] {
            let mut monitor = Monitor {
                active_workspace: Workspace {
                    address: normal.into(),
                },
                ..Default::default()
            };
            assert_eq!(monitor.workspace(), normal);
            for special in ["special:magic", "special:another"] {
                monitor.special_workspace.address = special.into();
                assert_eq!(monitor.workspace(), special);
            }
            monitor.special_workspace.address.clear();
            assert_eq!(monitor.workspace(), normal);
        }
    }

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
            let windows = window_targets(
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
            assert_eq!(
                windows[0].rect,
                Rect {
                    x: 100.0,
                    y: 80.0,
                    w: 200.0,
                    h: 150.0
                }
            );
            let hover = Point::new(150.0, 120.0);
            assert_eq!(
                windows
                    .iter()
                    .find(|w| w.rect.contains(hover))
                    .map(|w| w.rect),
                Some(Rect {
                    x: 100.0,
                    y: 80.0,
                    w: 200.0,
                    h: 150.0
                })
            );
            assert_eq!(
                windows[1].rect,
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
        let clients: Vec<Client> = serde_json::from_str(
            r#"[
            {"at":[-1350,150],"size":[200,100],"workspace":{"address":"other"},"pinned":true,"floating":true}
        ]"#,
        )
        .unwrap();
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
            window_targets(clients, &monitor, 1.5, 1.5, bounds)
                .into_iter()
                .map(|w| w.rect)
                .collect::<Vec<_>>(),
            vec![Rect {
                x: 0.0,
                y: 45.0,
                w: 195.0,
                h: 150.0
            }]
        );
    }

    #[test]
    fn fractional_window_geometry_snaps_to_integer_pixel_bounds() {
        let clients: Vec<Client> = serde_json::from_str(
            r#"[
            {"at":[10,15],"size":[101,51],"workspace":{"address":"1"}}
        ]"#,
        )
        .unwrap();
        let monitor = Monitor {
            x: 0,
            y: 0,
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
        let targets = window_targets(clients, &monitor, 1.25, 1.25, bounds);
        assert_eq!(targets.len(), 1);
        let r = targets[0].rect;
        assert_eq!(r.x.fract(), 0.0);
        assert_eq!(r.y.fract(), 0.0);
        assert_eq!(r.w.fract(), 0.0);
        assert_eq!(r.h.fract(), 0.0);
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

    #[test]
    fn output_capture_forces_opaque_alpha_without_altering_rgb() {
        let mut image = RgbaImage::from_raw(
            3,
            1,
            vec![12, 34, 56, 0, 100, 150, 200, 217, 255, 128, 64, 255],
        )
        .unwrap();
        force_opaque(&mut image);
        assert_eq!(
            image.into_raw(),
            vec![12, 34, 56, 255, 100, 150, 200, 255, 255, 128, 64, 255]
        );
    }
}
