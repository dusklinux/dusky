//! Wayland-only adaptation of window_clipboard 0.5.1 (MIT).
//! Upstream: https://github.com/hecrj/window_clipboard
//! Preserves the clipboard API used by iced_exwlshell without other platforms.

use raw_window_handle::{HasDisplayHandle, RawDisplayHandle};
use std::error::Error;

pub struct Clipboard {
    raw: clipboard_wayland::Clipboard,
}

impl Clipboard {
    /// Safety: the display handle must remain valid for the lifetime of `Clipboard`.
    pub unsafe fn connect<W: HasDisplayHandle>(window: &W) -> Result<Self, Box<dyn Error>> {
        let RawDisplayHandle::Wayland(handle) = window.display_handle()?.as_raw() else {
            return Err("Dusky Papers requires a Wayland display".into());
        };
        Ok(Self {
            // The caller guarantees that the Wayland display outlives this clipboard.
            raw: unsafe { clipboard_wayland::Clipboard::connect(handle.display.as_ptr()) },
        })
    }

    pub fn read(&self) -> Result<String, Box<dyn Error>> {
        self.raw.read()
    }

    pub fn write(&mut self, contents: String) -> Result<(), Box<dyn Error>> {
        self.raw.write(contents)
    }

    pub fn read_primary(&self) -> Option<Result<String, Box<dyn Error>>> {
        Some(self.raw.read_primary())
    }

    pub fn write_primary(&mut self, contents: String) -> Option<Result<(), Box<dyn Error>>> {
        Some(self.raw.write_primary(contents))
    }
}
