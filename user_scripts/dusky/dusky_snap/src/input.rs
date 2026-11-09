use crate::{
    Result,
    capture::{
        self, Monitor,
        stitch::{Axis, CaptureHandshake},
    },
    document::Rect,
};
use std::{
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    thread::JoinHandle,
    time::Duration,
};
use wayland_client::{
    Connection, Dispatch, QueueHandle, delegate_noop,
    globals::{GlobalListContents, registry_queue_init},
    protocol::{wl_pointer, wl_registry},
};
use wayland_protocols_wlr::virtual_pointer::v1::client::{
    zwlr_virtual_pointer_manager_v1::ZwlrVirtualPointerManagerV1,
    zwlr_virtual_pointer_v1::ZwlrVirtualPointerV1,
};

struct State;
impl Dispatch<wl_registry::WlRegistry, GlobalListContents> for State {
    fn event(
        _: &mut Self,
        _: &wl_registry::WlRegistry,
        _: wl_registry::Event,
        _: &GlobalListContents,
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
    }
}
delegate_noop!(State:ignore ZwlrVirtualPointerManagerV1);
delegate_noop!(State:ignore ZwlrVirtualPointerV1);

fn natural_scroll_direction() -> Result<f64> {
    let Ok(option) = capture::command(
        "hyprctl",
        &["-j", "getoption", "input:natural_scroll"],
        None,
        Duration::from_secs(2),
    ) else {
        return Ok(1.0);
    };
    let Ok(value) = serde_json::from_slice::<serde_json::Value>(&option) else {
        return Ok(1.0);
    };
    let natural = value
        .get("int")
        .and_then(|v| v.as_i64())
        .map(|v| v != 0)
        .or_else(|| value.get("bool").and_then(|v| v.as_bool()))
        .unwrap_or(false);
    Ok(if natural { -1.0 } else { 1.0 })
}

pub fn auto_scroll_park_point(crop: Rect) -> (u32, u32) {
    let x = (crop.x + crop.w * 0.72).max(0.0).round() as u32;
    let y = (crop.y + crop.h * 0.72).max(0.0).round() as u32;
    (x, y)
}

fn transformed_physical_size(monitor: &Monitor, fallback: (u32, u32)) -> (u32, u32) {
    let (w, h) = if monitor.transform % 2 == 1 {
        (monitor.height, monitor.width)
    } else {
        (monitor.width, monitor.height)
    };
    if w > 0 && h > 0 {
        (w, h)
    } else {
        (fallback.0.max(1), fallback.1.max(1))
    }
}

pub fn spawn_scroll_injector(
    stop: Arc<AtomicBool>,
    handshake: Arc<CaptureHandshake>,
    monitor: Monitor,
    crop: Rect,
    axis: Axis,
) -> Result<JoinHandle<()>> {
    let direction = natural_scroll_direction()?;
    let capture = libwayshot::WayshotConnection::new()?;
    let output = capture
        .get_all_outputs()
        .iter()
        .find(|o| o.name == monitor.name)
        .cloned()
        .ok_or("Scroll output disappeared")?;

    let handle = std::thread::Builder::new()
        .name("snap-scroll-inject".into())
        .spawn(move || {
            let run = || -> Result<()> {
                let (globals, mut queue) = registry_queue_init::<State>(&capture.conn)?;
                let qh = queue.handle();
                let manager: ZwlrVirtualPointerManagerV1 = globals.bind(&qh, 2..=2, ())?;
                let pointer = manager.create_virtual_pointer_with_output(
                    None,
                    Some(&output.wl_output),
                    &qh,
                    (),
                );
                let (pw, ph) = transformed_physical_size(
                    &monitor,
                    (output.physical_size.width, output.physical_size.height),
                );
                let (park_x, park_y) = auto_scroll_park_point(crop);
                let now = || chrono::Local::now().timestamp_millis() as u32;
                pointer.motion_absolute(
                    now(),
                    park_x.min(pw.saturating_sub(1)),
                    park_y.min(ph.saturating_sub(1)),
                    pw,
                    ph,
                );
                pointer.frame();
                capture.conn.flush()?;
                queue.roundtrip(&mut State)?;
                std::thread::sleep(Duration::from_millis(60));

                let mut cycle = handshake.publish_ready();
                let wl_axis = match axis {
                    Axis::Vertical => wl_pointer::Axis::VerticalScroll,
                    Axis::Horizontal => wl_pointer::Axis::HorizontalScroll,
                };
                while !stop.load(Ordering::Acquire) {
                    let Some(notches) = handshake.wait_for_capture(cycle, &stop) else {
                        break;
                    };
                    if stop.load(Ordering::Acquire) {
                        break;
                    }
                    let n = notches.clamp(1, 12);
                    let step_dir = if direction < 0.0 { -1 } else { 1 };
                    for i in 0..n {
                        if stop.load(Ordering::Acquire) {
                            break;
                        }
                        pointer.axis_source(wl_pointer::AxisSource::Wheel);
                        pointer.axis_discrete(now(), wl_axis, direction * 15.0, step_dir);
                        pointer.frame();
                        capture.conn.flush()?;
                        if i + 1 < n {
                            std::thread::sleep(Duration::from_millis(14));
                        }
                    }
                    queue.roundtrip(&mut State)?;
                    std::thread::sleep(Duration::from_millis(40));
                    cycle = handshake.publish_ready();
                }
                pointer.destroy();
                manager.destroy();
                let _ = capture.conn.flush();
                Ok(())
            };
            let _ = run();
            stop.store(true, Ordering::Release);
        })?;
    Ok(handle)
}

pub fn scroll(monitor: &Monitor, crop: Rect, position: bool) -> Result<()> {
    let direction = natural_scroll_direction()?;
    let capture = libwayshot::WayshotConnection::new()?;
    let output = capture
        .get_all_outputs()
        .iter()
        .find(|o| o.name == monitor.name)
        .ok_or("Scroll output disappeared")?;
    let (globals, mut queue) = registry_queue_init::<State>(&capture.conn)?;
    let qh = queue.handle();
    let manager: ZwlrVirtualPointerManagerV1 = globals.bind(&qh, 2..=2, ())?;
    let pointer =
        manager.create_virtual_pointer_with_output(None, Some(&output.wl_output), &qh, ());
    let now = chrono::Local::now().timestamp_millis() as u32;
    if position {
        let (pw, ph) = transformed_physical_size(
            monitor,
            (output.physical_size.width, output.physical_size.height),
        );
        let (px, py) = auto_scroll_park_point(crop);
        pointer.motion_absolute(
            now,
            px.min(pw.saturating_sub(1)),
            py.min(ph.saturating_sub(1)),
            pw,
            ph,
        );
        pointer.frame();
        capture.conn.flush()?;
        queue.roundtrip(&mut State)?;
    }
    pointer.axis_source(wl_pointer::AxisSource::Wheel);
    pointer.axis_discrete(
        now,
        wl_pointer::Axis::VerticalScroll,
        direction * 45.0,
        (direction * 3.0) as i32,
    );
    pointer.frame();
    capture.conn.flush()?;
    queue.roundtrip(&mut State)?;
    pointer.destroy();
    manager.destroy();
    capture.conn.flush()?;
    Ok(())
}
