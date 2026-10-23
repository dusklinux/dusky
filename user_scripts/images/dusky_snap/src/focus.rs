//! Hand input from a click-away popup to the already captured picker.
use crate::Result;
use iced::window::{
    Window,
    raw_window_handle::{RawDisplayHandle, RawWindowHandle},
};
use wayland_client::{
    Connection, Dispatch, Proxy, QueueHandle,
    backend::{Backend, ObjectId},
    delegate_noop,
    globals::{GlobalListContents, registry_queue_init},
    protocol::{wl_registry, wl_surface},
};

mod protocol {
    #![allow(
        dead_code,
        non_camel_case_types,
        non_upper_case_globals,
        non_snake_case,
        unused_imports,
        unused_unsafe,
        unused_variables,
        clippy::all
    )]
    use wayland_client::{self, protocol::*};
    mod __interfaces {
        use wayland_client::{backend as wayland_backend, protocol::__interfaces::*};
        wayland_scanner::generate_interfaces!("src/hyprland-focus-grab-v1.xml");
    }
    use __interfaces::*;
    wayland_scanner::generate_client_code!("src/hyprland-focus-grab-v1.xml");
}
use protocol::{
    hyprland_focus_grab_manager_v1::HyprlandFocusGrabManagerV1,
    hyprland_focus_grab_v1::HyprlandFocusGrabV1,
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
delegate_noop!(State: ignore HyprlandFocusGrabManagerV1);
delegate_noop!(State: ignore HyprlandFocusGrabV1);

pub fn handoff(window: &dyn Window) -> Result<()> {
    let RawDisplayHandle::Wayland(display) = window.display_handle()?.as_raw() else {
        return Err("Picker requires a Wayland display".into());
    };
    let RawWindowHandle::Wayland(surface) = window.window_handle()?.as_raw() else {
        return Err("Picker requires a Wayland surface".into());
    };
    // Iced retains both handles throughout this synchronous window callback.
    // Guest mode borrows the display; the temporary grab and manager are
    // destroyed here without taking over Winit's surface event handling.
    let backend = unsafe { Backend::from_foreign_display(display.display.as_ptr().cast()) };
    let conn = Connection::from_backend(backend);
    let id = unsafe {
        ObjectId::from_ptr(
            wl_surface::WlSurface::interface(),
            surface.surface.as_ptr().cast(),
        )?
    };
    let surface = wl_surface::WlSurface::from_id(&conn, id)?;
    let (globals, mut queue) = registry_queue_init::<State>(&conn)?;
    let qh = queue.handle();
    let manager: HyprlandFocusGrabManagerV1 = globals.bind(&qh, 1..=1, ())?;
    let grab = manager.create_grab(&qh, ());
    grab.add_surface(&surface);
    grab.commit();
    // A new grab clears the popup's grab. Release ours immediately: the
    // pinned picker already receives ordinary input, including across outputs.
    grab.destroy();
    manager.destroy();
    globals.destroy();
    queue.roundtrip(&mut State)?;
    Ok(())
}
