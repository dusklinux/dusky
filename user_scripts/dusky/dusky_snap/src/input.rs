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
        mpsc,
    },
    thread::JoinHandle,
    time::{Duration, Instant},
};
use wayland_client::{
    Connection, Dispatch, Proxy, QueueHandle, delegate_noop,
    globals::{GlobalListContents, registry_queue_init},
    protocol::{wl_output, wl_pointer, wl_registry},
};
use wayland_protocols_wlr::virtual_pointer::v1::client::{
    zwlr_virtual_pointer_manager_v1::ZwlrVirtualPointerManagerV1,
    zwlr_virtual_pointer_v1::ZwlrVirtualPointerV1,
};

struct State {
    target_name: String,
    matched: Option<wl_output::WlOutput>,
}

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

impl Dispatch<wl_output::WlOutput, ()> for State {
    fn event(
        state: &mut Self,
        proxy: &wl_output::WlOutput,
        event: wl_output::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let wl_output::Event::Name { name } = event
            && name == state.target_name
        {
            state.matched = Some(proxy.clone());
        }
    }
}

delegate_noop!(State: ignore ZwlrVirtualPointerManagerV1);
delegate_noop!(State: ignore ZwlrVirtualPointerV1);

fn natural_scroll_direction() -> f64 {
    let Ok(option) = capture::command(
        "hyprctl",
        &["-j", "getoption", "input:natural_scroll"],
        None,
        Duration::from_secs(2),
    ) else {
        return 1.0;
    };
    let Ok(value) = serde_json::from_slice::<serde_json::Value>(&option) else {
        return 1.0;
    };
    let natural = value
        .get("int")
        .and_then(|v| v.as_i64())
        .map(|v| v != 0)
        .or_else(|| value.get("bool").and_then(|v| v.as_bool()))
        .unwrap_or(false);
    if natural { -1.0 } else { 1.0 }
}

/// Park inside the main scrollable pane while avoiding headers, sidebars,
/// scrollbars, and the trailing splice region where `:hover` effects bake into bands.
pub fn auto_scroll_park_point(crop: Rect, axis: Axis) -> (u32, u32) {
    let (fx, fy) = match axis {
        Axis::Vertical => (0.50, 0.30),
        Axis::Horizontal => (0.30, 0.50),
    };
    let x = (crop.x + crop.w * fx).max(0.0).round() as u32;
    let y = (crop.y + crop.h * fy).max(0.0).round() as u32;
    (x, y)
}

pub fn spawn_scroll_injector(
    stop: Arc<AtomicBool>,
    handshake: Arc<CaptureHandshake>,
    monitor: Monitor,
    crop: Rect,
    source_size: (u32, u32),
    axis: Axis,
) -> Result<JoinHandle<()>> {
    let direction = natural_scroll_direction();
    let conn = Connection::connect_to_env()?;
    let (init_tx, init_rx) = mpsc::sync_channel::<Result<()>>(1);

    let handle = std::thread::Builder::new()
        .name("snap-scroll-inject".into())
        .spawn(move || {
            let setup = || -> Result<(
                wayland_client::EventQueue<State>,
                State,
                wl_output::WlOutput,
                ZwlrVirtualPointerManagerV1,
                ZwlrVirtualPointerV1,
                Instant,
            )> {
                let (globals, mut queue) = registry_queue_init::<State>(&conn)?;
                let qh = queue.handle();
                let mut bound_outputs = Vec::new();
                for global in globals.contents().clone_list() {
                    if global.interface == wl_output::WlOutput::interface().name {
                        let version = global.version.min(4);
                        let out: wl_output::WlOutput =
                            globals.registry().bind(global.name, version, &qh, ());
                        bound_outputs.push(out);
                    }
                }
                let mut state = State {
                    target_name: monitor.name.clone(),
                    matched: None,
                };
                queue.roundtrip(&mut state)?;
                let output = state
                    .matched
                    .take()
                    .ok_or("Scroll output disappeared")?;
                for other in bound_outputs {
                    if other != output && other.version() >= 3 {
                        other.release();
                    }
                }
                let manager: ZwlrVirtualPointerManagerV1 = globals.bind(&qh, 2..=2, ())?;
                let pointer = manager.create_virtual_pointer_with_output(
                    None,
                    Some(&output),
                    &qh,
                    (),
                );
                let pw = source_size.0.max(1);
                let ph = source_size.1.max(1);
                let (park_x, park_y) = auto_scroll_park_point(crop, axis);
                let epoch = Instant::now();
                pointer.motion_absolute(
                    0,
                    park_x.min(pw - 1),
                    park_y.min(ph - 1),
                    pw,
                    ph,
                );
                pointer.frame();
                conn.flush()?;
                queue.roundtrip(&mut state)?;
                Ok((queue, state, output, manager, pointer, epoch))
            };

            let (mut queue, mut state, output, manager, pointer, epoch) = match setup() {
                Ok(v) => {
                    let _ = init_tx.send(Ok(()));
                    v
                }
                Err(e) => {
                    let _ = init_tx.send(Err(e));
                    stop.store(true, Ordering::Release);
                    handshake.notify_stop();
                    return;
                }
            };

            let now = || epoch.elapsed().as_millis() as u32;
            std::thread::sleep(Duration::from_millis(50));

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
                let n = notches.clamp(1, 10);
                let step_dir = if direction < 0.0 { -1 } else { 1 };
                for i in 0..n {
                    if stop.load(Ordering::Acquire) {
                        break;
                    }
                    pointer.axis_source(wl_pointer::AxisSource::Wheel);
                    pointer.axis_discrete(now(), wl_axis, direction * 15.0, step_dir);
                    pointer.frame();
                    if conn.flush().is_err() {
                        stop.store(true, Ordering::Release);
                        break;
                    }
                    if i + 1 < n {
                        std::thread::sleep(Duration::from_millis(14));
                    }
                }
                if stop.load(Ordering::Acquire) {
                    break;
                }
                if queue.roundtrip(&mut state).is_err() {
                    break;
                }
                std::thread::sleep(Duration::from_millis(35));
                if stop.load(Ordering::Acquire) {
                    break;
                }
                cycle = handshake.publish_ready();
            }

            pointer.destroy();
            manager.destroy();
            if output.version() >= 3 {
                output.release();
            }
            let _ = conn.flush();
            stop.store(true, Ordering::Release);
            handshake.notify_stop();
        })?;

    match init_rx.recv() {
        Ok(Ok(())) => Ok(handle),
        Ok(Err(e)) => {
            let _ = handle.join();
            Err(e)
        }
        Err(_) => {
            let _ = handle.join();
            Err("Scroll injector exited during setup".into())
        }
    }
}
