# 🎯 Dynamic GTK3/GTK4 Wayland Focus-Grab Integration Guide

> [!NOTE]
> Wayland's strict security model sandboxes applications and prevents windows from observing input events, such as mouse clicks, that occur outside their boundaries. This document outlines a unified native C extension (`libwaylandgrab.so`) and corresponding Python integration that requests the Wayland compositor (Hyprland) to handle "outside clicks" and dismiss popups or panels automatically.

## 🧠 1. Architectural Concept: Dynamic Runtime Symbol Resolution & Concurrency

this is a static path and will always be this, with this exact name!
btw the path to the file generated is this ~/user_scripts/dusky_system/click_away_to_dismiss/libwaylandgrab.so

In traditional setups, separate libraries are built for GTK3 and GTK4 because they use different APIs to extract the underlying Wayland surface pointer:
* **GTK3:** `gtk_widget_get_window` -> `gdk_window_get_display` -> `gdk_wayland_window_get_wl_surface`
* **GTK4:** `gtk_native_get_surface` -> `gdk_surface_get_display` -> `gdk_wayland_surface_get_wl_surface`

Linking directly to GTK at compile time creates rigid dependencies and separate binaries. To avoid this, our unified C extension utilizes **Dynamic Loading (`dlfcn.h`)** alongside a robust polling and threading architecture.

### How it Works:
1. **Strict Probing (`RTLD_NOLOAD`):** Instead of hard-linking GTK headers, the extension uses `dlopen(..., RTLD_LAZY | RTLD_NOLOAD)`. `RTLD_NOLOAD` is critical—it ensures we only interact with the GTK version (3 or 4) *already running* in the Python process, averting catastrophic toolkit co-loading crashes.
2. **Backend Validation:** Uses GObject's `g_type_check_instance_is_a` dynamically to strictly verify the extracted GDK display is genuinely running under a Wayland backend.
3. **Thread-Safe Dispatching:** Wayland events are monitored via a background C thread using `poll()` and an `eventfd` for non-blocking shutdown signaling. Wayland buffer backpressure (`EAGAIN`) is safely handled via `POLLOUT`.
4. **GIL-Safe Callbacks:** When a click-away event occurs, the C library dynamically resolves GLib's `g_idle_add` to hand execution back to the GTK main thread before firing the Python callback. This guarantees Python Global Interpreter Lock (GIL) safety.

> [!WARNING]
> **Singleton Limitation:** Because the C extension manages the Wayland socket queue using static global pointers (`active_grab`, `grab_manager`), this library supports exactly **ONE** active focus grab at a time per Python process. 

---

## 📦 2. Source Code Reference (`dusky.c`)

The maintained implementation is [dusky.c](../dusky.c). Compile it together with
`hyprland-focus-grab-v1-client-protocol.c`, linking `wayland-client`, `dl`, and
`pthread`. The protocol header is included from this directory.

GTK callers retain `init_wayland_grab(gtk_window, callback)` and
`destroy_wayland_grab()`; callbacks are dispatched through GLib.

Native Wayland callers use:

```c
int init_wayland_grab_raw(struct wl_display *display,
                          struct wl_surface *surface,
                          void (*callback)(void));
void destroy_wayland_grab(void);
```

Initialize after the surface is mapped. A return value of 1 means the grab
started; 0 means initialization failed. Keep the borrowed display and surface
alive until destruction finishes. Native callbacks run on the helper's reader
thread and must only enqueue UI work; do not destroy the grab from that callback.
The native path does not load GTK or GLib. Dusky Tray compiles these shared C
sources directly into its binary; GTK consumers may continue using the shared
library. Each process supports one active grab.
