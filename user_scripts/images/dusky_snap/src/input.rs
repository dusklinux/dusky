use crate::{
    Result,
    capture::{self, Monitor},
    document::Rect,
};
use std::time::Duration;
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

// A protocol request, with no uinput device, daemon, or compositor configuration.
pub fn scroll(monitor: &Monitor, crop: Rect, position: bool) -> Result<()> {
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
        pointer.motion_absolute(
            now,
            (crop.x + crop.w / 2.0).max(0.0) as u32,
            (crop.y + crop.h / 2.0).max(0.0) as u32,
            output.physical_size.width,
            output.physical_size.height,
        );
        pointer.frame();
        capture.conn.flush()?;
        queue.roundtrip(&mut State)?;
    }
    let option = capture::command(
        "hyprctl",
        &["-j", "getoption", "input:natural_scroll"],
        None,
        Duration::from_secs(2),
    )?;
    let value: serde_json::Value = serde_json::from_slice(&option)?;
    let direction = if value
        .get("bool")
        .and_then(|v| v.as_bool())
        .ok_or("Hyprland did not report natural-scroll policy")?
    {
        -1.0
    } else {
        1.0
    };
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
