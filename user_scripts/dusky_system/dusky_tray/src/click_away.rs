//! Use Dusky's shared focus-grab helper without a GTK dependency.
use iced_futures::futures::{Stream, StreamExt, channel::mpsc};
use raw_window_handle::{RawDisplayHandle, RawWindowHandle};
use std::sync::{Mutex, OnceLock};

unsafe extern "C" {
    fn init_wayland_grab_raw(
        display: *mut libc::c_void,
        surface: *mut libc::c_void,
        callback: extern "C" fn(),
    ) -> libc::c_int;
    fn destroy_wayland_grab();
}
type Channel = (
    mpsc::UnboundedSender<()>,
    Mutex<Option<mpsc::UnboundedReceiver<()>>>,
);
static CHANNEL: OnceLock<Channel> = OnceLock::new();
fn channel() -> &'static Channel {
    CHANNEL.get_or_init(|| {
        let (tx, rx) = mpsc::unbounded();
        (tx, Mutex::new(Some(rx)))
    })
}
extern "C" fn cleared() {
    let _ = channel().0.unbounded_send(());
}
pub fn events() -> impl Stream<Item = crate::ui::Message> {
    channel()
        .1
        .lock()
        .unwrap()
        .take()
        .expect("one tray subscription")
        .map(|()| crate::ui::Message::BackdropPressed)
}

pub struct Guard {
    display: usize,
    surface: usize,
    started: bool,
    // Keep the underlying Wayland connection alive until the reader joins.
    _window: Box<dyn iced_renderer::graphics::compositor::Window>,
}
impl Guard {
    pub fn new(window: impl iced_renderer::graphics::compositor::Window) -> Result<Self, String> {
        let RawDisplayHandle::Wayland(display) =
            window.display_handle().map_err(|e| e.to_string())?.as_raw()
        else {
            return Err("Click-away requires Wayland".into());
        };
        let RawWindowHandle::Wayland(surface) =
            window.window_handle().map_err(|e| e.to_string())?.as_raw()
        else {
            return Err("Click-away requires a Wayland surface".into());
        };
        // The helper borrows these handles; _window retains their connection.
        // Drop joins the C event reader before releasing the window.
        Ok(Self {
            display: display.display.as_ptr() as usize,
            surface: surface.surface.as_ptr() as usize,
            started: false,
            _window: Box::new(window),
        })
    }
    pub fn activate(&mut self) {
        if self.started {
            return;
        }
        if unsafe { init_wayland_grab_raw(self.display as *mut _, self.surface as *mut _, cleared) }
            == 0
        {
            eprintln!("Could not activate Dusky click-away focus grab");
            std::process::exit(1);
        }
        self.started = true;
    }
}
impl Drop for Guard {
    fn drop(&mut self) {
        unsafe {
            destroy_wayland_grab();
        }
    }
}
