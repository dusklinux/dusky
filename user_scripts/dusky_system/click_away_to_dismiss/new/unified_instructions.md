# 🎯 Dynamic GTK3/GTK4 Wayland Focus-Grab Integration Guide

> [!NOTE]
> Wayland's strict security model sandboxes applications and prevents windows from observing input events, such as mouse clicks, that occur outside their boundaries. This document outlines a unified native C extension (`libwaylandgrab.so`) and corresponding Python integration that requests the Wayland compositor (Hyprland) to handle "outside clicks" and dismiss popups or panels automatically.

---

## 🧠 1. Architectural Concept: Dynamic Runtime Symbol Resolution

In traditional setups, separate libraries are built for GTK3 and GTK4 because they use different APIs to extract the underlying Wayland surface pointer:
- **GTK3:** `gtk_widget_get_window` -> `gdk_window_get_display` -> `gdk_wayland_window_get_wl_surface`
- **GTK4:** `gtk_native_get_surface` -> `gdk_surface_get_display` -> `gdk_wayland_surface_get_wl_surface`

Linking directly to GTK at compile time creates rigid dependencies and separate binaries. To avoid this, our unified C extension utilizes **Dynamic Loading** (`dlfcn.h`) with the special handle `RTLD_DEFAULT`.

### How `RTLD_DEFAULT` Works
When the Python process imports PyGObject and initializes GTK (either GTK3 or GTK4), the GObject and GDK libraries are loaded into the process's virtual memory address space. 

By calling `dlsym(RTLD_DEFAULT, "symbol_name")`, the C extension searches the process's loaded libraries sequentially starting from the main executable. This allows `libwaylandgrab.so` to:
1. Probe the loaded process space for GTK4 surface extraction symbols (`gtk_native_get_surface`).
2. If found, extract the `wl_surface` and `wl_display` pointers using the GTK4 GDK library.
3. If not found, fall back to probe for GTK3 window extraction symbols (`gtk_widget_get_window`).
4. Extract the surface and display pointers using the GTK3 GDK library.
5. Compile with **zero compile-time dependencies** on GTK headers or library flags.

---

## 📦 2. Source Code Reference (`dusky.c`)

This is the unified C extension source code. Place this file in `~/user_scripts/dusky_system/click_away_to_dismiss/dusky.c`.

```c
/*
 * dusky.c — Unified Wayland Focus-Grab Extension (GTK3 + GTK4)
 *
 * This library dynamically detects the active GTK runtime (GTK4 or GTK3)
 * using dlsym(RTLD_DEFAULT, ...) to resolve the correct surface-extraction
 * symbols at runtime. It compiles with ZERO GTK headers or link flags,
 * needing only wayland-client, pthread, and dl.
 */

#define _GNU_SOURCE
#include <dlfcn.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>
#include <wayland-client.h>
#include "hyprland-focus-grab-v1-client-protocol.h"

/* ── Wayland Grab State ─────────────────────────────────────────────── */

static struct hyprland_focus_grab_manager_v1 *grab_manager = NULL;
static struct hyprland_focus_grab_v1         *active_grab   = NULL;
static struct wl_event_queue                 *custom_queue   = NULL;
static struct wl_display                     *global_display = NULL;

typedef void (*ClearedCallback)(void);
static ClearedCallback py_callback = NULL;

/* ── Registry Listener ──────────────────────────────────────────────── */

static void registry_handler(void *data, struct wl_registry *registry,
                             uint32_t id, const char *interface,
                             uint32_t version) {
    if (strcmp(interface, "hyprland_focus_grab_manager_v1") == 0) {
        grab_manager = wl_registry_bind(
            registry, id, &hyprland_focus_grab_manager_v1_interface, 1);
    }
}

static void registry_remover(void *data, struct wl_registry *registry,
                              uint32_t id) {}

static const struct wl_registry_listener registry_listener = {
    &registry_handler, &registry_remover};

/* ── Grab Cleared Listener ──────────────────────────────────────────── */

static void grab_cleared(void *data, struct hyprland_focus_grab_v1 *grab) {
    if (py_callback) {
        py_callback();
    }
}

static const struct hyprland_focus_grab_v1_listener grab_listener = {
    .cleared = grab_cleared};

/* ── Background Dispatch Thread ─────────────────────────────────────── */

static void *dispatch_thread_func(void *arg) {
    while (1) {
        if (wl_display_dispatch_queue(global_display, custom_queue) == -1) {
            break;
        }
    }
    return NULL;
}

/* ── Dynamic GTK3/GTK4 Resolution ───────────────────────────────────── */

typedef void *(*gtk_fn_ptr)(void *);
typedef struct wl_display  *(*fn_get_wl_display)(void *);
typedef struct wl_surface  *(*fn_get_wl_surface)(void *);

/**
 * resolve_wayland_surfaces()
 *
 * Probes the current process for GTK4 symbols first, then falls back
 * to GTK3 symbols. On success, fills out_display and out_surface
 * and returns 1. Returns 0 on failure.
 */
static int resolve_wayland_surfaces(void *gtk_ptr,
                                    struct wl_display **out_display,
                                    struct wl_surface **out_surface) {
    /* Shared between GTK3 and GTK4 */
    fn_get_wl_display get_wl_display =
        (fn_get_wl_display)dlsym(RTLD_DEFAULT,
                                 "gdk_wayland_display_get_wl_display");
    if (!get_wl_display) {
        fprintf(stderr,
                "[libwaylandgrab] Error: Not a Wayland session "
                "(gdk_wayland_display_get_wl_display not found).\n");
        return 0;
    }

    /* ── GTK4 Path ─────────────────────────────────────────────────── */
    gtk_fn_ptr gtk4_get_surface =
        (gtk_fn_ptr)dlsym(RTLD_DEFAULT, "gtk_native_get_surface");

    if (gtk4_get_surface) {
        gtk_fn_ptr surface_get_display =
            (gtk_fn_ptr)dlsym(RTLD_DEFAULT, "gdk_surface_get_display");
        fn_get_wl_surface wayland_get_surface =
            (fn_get_wl_surface)dlsym(RTLD_DEFAULT,
                                     "gdk_wayland_surface_get_wl_surface");

        if (surface_get_display && wayland_get_surface) {
            void *gdk_surface = gtk4_get_surface(gtk_ptr);
            if (!gdk_surface) {
                fprintf(stderr,
                        "[libwaylandgrab] GTK4: GdkSurface is NULL.\n");
                return 0;
            }
            void *gdk_display = surface_get_display(gdk_surface);
            *out_display = get_wl_display(gdk_display);
            *out_surface = wayland_get_surface(gdk_surface);
            fprintf(stderr, "[libwaylandgrab] Resolved via GTK4 path.\n");
            return 1;
        }
    }

    /* ── GTK3 Fallback Path ────────────────────────────────────────── */
    gtk_fn_ptr gtk3_get_window =
        (gtk_fn_ptr)dlsym(RTLD_DEFAULT, "gtk_widget_get_window");

    if (gtk3_get_window) {
        gtk_fn_ptr window_get_display =
            (gtk_fn_ptr)dlsym(RTLD_DEFAULT, "gdk_window_get_display");
        fn_get_wl_surface wayland_get_surface =
            (fn_get_wl_surface)dlsym(RTLD_DEFAULT,
                                     "gdk_wayland_window_get_wl_surface");

        if (window_get_display && wayland_get_surface) {
            void *gdk_window = gtk3_get_window(gtk_ptr);
            if (!gdk_window) {
                fprintf(stderr,
                        "[libwaylandgrab] GTK3: GdkWindow is NULL "
                        "(widget not realized?).\n");
                return 0;
            }
            void *gdk_display = window_get_display(gdk_window);
            *out_display = get_wl_display(gdk_display);
            *out_surface = wayland_get_surface(gdk_window);
            fprintf(stderr, "[libwaylandgrab] Resolved via GTK3 path.\n");
            return 1;
        }
    }

    fprintf(stderr,
            "[libwaylandgrab] Error: Could not resolve GTK3 or GTK4 "
            "Wayland symbols.\n");
    return 0;
}

/* ── Public API ─────────────────────────────────────────────────────── */

void init_wayland_grab(void *gtk_window_ptr, ClearedCallback cb) {
    if (!gtk_window_ptr)
        return;
    py_callback = cb;

    struct wl_surface *wl_surface = NULL;
    if (!resolve_wayland_surfaces(gtk_window_ptr, &global_display,
                                  &wl_surface)) {
        return;
    }

    /* One-time initialisation of the isolated event queue */
    if (!custom_queue) {
        custom_queue = wl_display_create_queue(global_display);
        struct wl_registry *registry =
            wl_display_get_registry(global_display);

        wl_proxy_set_queue((struct wl_proxy *)registry, custom_queue);
        wl_registry_add_listener(registry, &registry_listener, NULL);
        wl_display_roundtrip_queue(global_display, custom_queue);

        if (!grab_manager) {
            fprintf(stderr,
                    "[libwaylandgrab] Error: "
                    "hyprland_focus_grab_manager_v1 not supported.\n");
            return;
        }

        pthread_t thread_id;
        pthread_create(&thread_id, NULL, dispatch_thread_func, NULL);
        pthread_detach(thread_id);
    }

    if (grab_manager) {
        if (active_grab) {
            hyprland_focus_grab_v1_destroy(active_grab);
        }
        active_grab =
            hyprland_focus_grab_manager_v1_create_grab(grab_manager);

        wl_proxy_set_queue((struct wl_proxy *)active_grab, custom_queue);

        hyprland_focus_grab_v1_add_listener(active_grab, &grab_listener,
                                            NULL);
        hyprland_focus_grab_v1_add_surface(active_grab, wl_surface);
        hyprland_focus_grab_v1_commit(active_grab);
        wl_display_flush(global_display);
    }
}

void destroy_wayland_grab() {
    if (active_grab) {
        hyprland_focus_grab_v1_destroy(active_grab);
        active_grab = NULL;
        wl_display_flush(global_display);
    }
}
```

---

## 🛠️ 3. Compilation and Code-Generation Commands

To compile this unified extension, first translate the XML definition into C protocol code, and then compile it without any GTK compiler options.

```bash
TARGET="$HOME/user_scripts/dusky_system/click_away_to_dismiss"

# 1. Generate client header protocol
wayland-scanner client-header \
  "$TARGET/hyprland-focus-grab-v1.xml" \
  "$TARGET/hyprland-focus-grab-v1-client-protocol.h"

# 2. Generate client glue code
wayland-scanner private-code \
  "$TARGET/hyprland-focus-grab-v1.xml" \
  "$TARGET/hyprland-focus-grab-v1-client-protocol.c"

# 3. Compile the shared library (Zero GTK flags, requires dynamic loading library -ldl)
gcc -shared -fPIC -o "$TARGET/libwaylandgrab.so" \
  "$TARGET/dusky.c" \
  "$TARGET/hyprland-focus-grab-v1-client-protocol.c" \
  -lwayland-client -lpthread -ldl
```

---

## 🐍 4. Unified Python Gtk3/Gtk4 Integration Boilerplate

This section illustrates how to load and interface with the compiled `.so` extension in Python. The boilerplate logic works identically for GTK3 (`gi.repository.Gtk` version `3.0`) and GTK4 (`gi.repository.Gtk` version `4.0`).

### Step 4.1: Library Loading (Walkie-Talkie Setup)
Near the top of your python script, load the library and map the callback types.

```python
import ctypes
import os
import logging
from gi.repository import GLib

# Define path to the shared library
_grab_lib_path = os.path.expanduser("~/user_scripts/dusky_system/click_away_to_dismiss/libwaylandgrab.so")

try:
    LIBGRAB = ctypes.CDLL(_grab_lib_path)
    # Callback type matches: void callback_name(void)
    CB_TYPE = ctypes.CFUNCTYPE(None)
except OSError:
    logging.warning(f"Failed to load Wayland Grab Library at {_grab_lib_path}. Outside click dismissal is disabled.")
    LIBGRAB = None
```

### Step 4.2: Component Integration

Implement this code in your window implementation class. 

> [!WARNING]
> It is critical to keep a persistent reference to the callback wrapper in python memory (e.g., `self._grab_cb`). If not retained, Python's Garbage Collector will reclaim the callback object, resulting in a segmentation fault when the native C background thread attempts to trigger it.

```python
class QuickPanelWindow(Gtk.Window):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # 1. Prevent garbage collection of our Python callback wrapper
        if LIBGRAB:
            self._grab_cb = CB_TYPE(self._on_grab_cleared)
        else:
            self._grab_cb = None

        # 2. Connect window events
        # "map" fires when the window is mapped to a monitor surface
        self.connect("map", self._on_map)
        
        # Connect to visibility/destruction/hiding handlers to cleanly release focus grab
        self.connect("hide", self._on_hide)
        self.connect("delete-event", self._on_delete_event)

    def _on_map(self, *args):
        """Triggers the moment GTK attaches pixels to the window on screen."""
        self._activate_grab()

    def _activate_grab(self):
        """Registers the window to intercept focus and register click-away."""
        if LIBGRAB and self.get_visible() and self._grab_cb:
            # hash(self) retrieves the native memory address of the GObject instance wrapper
            window_ptr = ctypes.c_void_p(hash(self))
            LIBGRAB.init_wayland_grab(window_ptr, self._grab_cb)

    def _on_grab_cleared(self):
        """Callback invoked by the background thread when a click-away is detected."""
        # Use GLib.idle_add to safely schedule UI hide operations on the GTK main thread
        # GTK3 uses self.hide(), GTK4 applications can use self.set_visible(False)
        GLib.idle_add(self.hide)

    def _on_hide(self, *args):
        """Ensures focus grab is cleanly destroyed when hidden."""
        if LIBGRAB:
            LIBGRAB.destroy_wayland_grab()

    def _on_delete_event(self, _window, _event) -> bool:
        """Hides rather than destroying window on close."""
        self.hide()
        return True
```

---

## 🔍 5. Architectural Considerations

| Feature | GTK3 Support | GTK4 Support |
| :--- | :--- | :--- |
| **Address Reference** | `hash(self)` resolves to the `GObject*` pointer. | `hash(self)` resolves to the `GObject*` pointer. |
| **Window State Signal** | Uses `"map"` to trigger grab when drawn. | Uses `"map"` to trigger grab when drawn. |
| **Interface Resolution** | `gtk_widget_get_window` dynamically loaded. | `gtk_native_get_surface` dynamically loaded. |
| **Thread Invocation** | `GLib.idle_add(self.hide)` is thread-safe. | `GLib.idle_add(self.set_visible, False)` is thread-safe. |

By separating compilation from framework headers and performing runtime resolution, you ensure a single binary deployment that requires zero adjustments when upgrading your control center or applets from GTK3 to GTK4 in the future.
