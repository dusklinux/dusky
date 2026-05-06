It uses simple analogies to explain the "why" behind the code, making it accessible to any curious learner, regardless of their prior experience with C or Wayland.

***

# 🎯 The Native Wayland Focus Grab Guide (Python GTK4)

> [!info] **What does this do?**
> If you are building a custom pop-up menu, control center, or applet in Python using GTK4 on a Wayland compositor (like Hyprland), you usually want the window to close when the user clicks anywhere outside of it. 
> 
> Under Wayland's strict security rules, applications cannot see your mouse clicks if they happen outside their own window. This guide shows you how to use a tiny, hyper-efficient C-extension to ask the Wayland compositor to handle the "outside click" detection for us natively.

---

## 🧠 The Concept: How It Works (The Bank Vault Analogy)

Imagine your Python GTK application is a friendly robot that paints pretty buttons on the screen. However, the Wayland Compositor (Hyprland) is like a highly secure **Bank Vault**. 

1. **The Problem:** Python doesn't know how to speak the Bank Vault's strict native language. If you want the Vault to tell you when someone touches the floor outside your robot's box, Python can't ask directly.
2. **The Solution:** We write a tiny C script (The Specialist). C speaks the Vault's language perfectly. We compile this C script into a tool (`.so` file).
3. **The Connection:** Python uses a built-in radio called `ctypes` to talk to The Specialist. Python says, *"Hey, I just opened my menu. Tell the Vault to alert us if a click happens outside."* The Specialist sets it up, listens for the Vault's alarm, and radios Python to hide the menu.

---

## 🛠️ Phase 1: Creating the C Specialist

First, we need to create a dedicated folder for our "Specialist" to live in, download the official Wayland dictionary, and write the C code.

### Step 1.1: Set up the workspace
Open your terminal and run these commands to create a folder and download the official Hyprland Focus Grab protocol (the "dictionary" the Vault uses):

```bash
mkdir -p ~/wayland_grab_tool
cd ~/wayland_grab_tool
wget https://raw.githubusercontent.com/hyprwm/hyprland-protocols/main/protocols/hyprland-focus-grab-v1.xml
```

### Step 1.2: The C Script
In that same `~/wayland_grab_tool` folder, create a file named `wayland_grab.c` and paste this code inside. 

> [!abstract] **What is this code doing?**
> This script creates an isolated, invisible communication channel (a `wl_event_queue`) directly to the compositor. When the compositor detects an outside click, this script fires a callback to let Python know.

```c
#include <gtk/gtk.h>
#include <gdk/wayland/gdkwayland.h>
#include <wayland-client.h>
#include <pthread.h>
#include <string.h>
#include <stdio.h>
#include "hyprland-focus-grab-v1-client-protocol.h"

static struct hyprland_focus_grab_manager_v1 *grab_manager = NULL;
static struct hyprland_focus_grab_v1 *active_grab = NULL;
static struct wl_event_queue *custom_queue = NULL;
static struct wl_display *global_display = NULL;

typedef void (*ClearedCallback)(void);
static ClearedCallback py_callback = NULL;

// 1. Listen for the grab manager on our isolated queue
static void registry_handler(void *data, struct wl_registry *registry, uint32_t id, const char *interface, uint32_t version) {
    if (strcmp(interface, "hyprland_focus_grab_manager_v1") == 0) {
        grab_manager = wl_registry_bind(registry, id, &hyprland_focus_grab_manager_v1_interface, 1);
    }
}
static void registry_remover(void *data, struct wl_registry *registry, uint32_t id) {}
static const struct wl_registry_listener registry_listener = { &registry_handler, &registry_remover };

// 2. This fires when Hyprland detects an outside click
static void grab_cleared(void *data, struct hyprland_focus_grab_v1 *grab) {
    if (py_callback) {
        py_callback(); // Radio back to Python!
    }
}
static const struct hyprland_focus_grab_v1_listener grab_listener = { .cleared = grab_cleared };

// 3. Background thread to listen to the Vault without freezing the Python UI
static void* dispatch_thread_func(void* arg) {
    while (1) {
        if (wl_display_dispatch_queue(global_display, custom_queue) == -1) {
            break;
        }
    }
    return NULL;
}

// 4. The function Python will call to start the grab
void init_wayland_grab(void *gtk_window_ptr, ClearedCallback cb) {
    if (!gtk_window_ptr) return;
    py_callback = cb;

    GtkNative *native = GTK_NATIVE(gtk_window_ptr);
    GdkSurface *gdk_surface = gtk_native_get_surface(native);
    GdkDisplay *gdk_display = gdk_surface_get_display(gdk_surface);

    if (!GDK_IS_WAYLAND_DISPLAY(gdk_display)) {
        fprintf(stderr, "[libwaylandgrab] Error: Not running under Wayland.\n");
        return;
    }

    global_display = gdk_wayland_display_get_wl_display(gdk_display);
    struct wl_surface *wl_surface = gdk_wayland_surface_get_wl_surface(gdk_surface);

    // One-time setup of the isolated communication queue
    if (!custom_queue) {
        custom_queue = wl_display_create_queue(global_display);
        struct wl_registry *registry = wl_display_get_registry(global_display);
        
        wl_proxy_set_queue((struct wl_proxy *)registry, custom_queue);
        wl_registry_add_listener(registry, &registry_listener, NULL);
        wl_display_roundtrip_queue(global_display, custom_queue);

        if (!grab_manager) {
            fprintf(stderr, "[libwaylandgrab] Error: focus grab not supported by compositor.\n");
            return;
        }

        // Start listening in the background
        pthread_t thread_id;
        pthread_create(&thread_id, NULL, dispatch_thread_func, NULL);
        pthread_detach(thread_id);
    }

    // Tell the compositor to grab focus for our specific window surface
    if (grab_manager) {
        if (active_grab) {
            hyprland_focus_grab_v1_destroy(active_grab);
        }
        active_grab = hyprland_focus_grab_manager_v1_create_grab(grab_manager);
        wl_proxy_set_queue((struct wl_proxy *)active_grab, custom_queue);
        hyprland_focus_grab_v1_add_listener(active_grab, &grab_listener, NULL);
        hyprland_focus_grab_v1_add_surface(active_grab, wl_surface);
        hyprland_focus_grab_v1_commit(active_grab);
        wl_display_flush(global_display);
    }
}

// 5. The function Python will call to stop the grab when the window hides
void destroy_wayland_grab() {
    if (active_grab) {
        hyprland_focus_grab_v1_destroy(active_grab);
        active_grab = NULL;
        wl_display_flush(global_display);
    }
}
```

---

## ⚙️ Phase 2: The Bootcamp (Compiling)

Now we need to translate the XML dictionary into C-headers using `wayland-scanner`, and then compile everything into our usable `.so` tool. 

Run these three commands in your terminal (make sure you are still in `~/wayland_grab_tool`):

```bash
# 1. Translate the XML into a C Header file
wayland-scanner client-header hyprland-focus-grab-v1.xml hyprland-focus-grab-v1-client-protocol.h

# 2. Translate the XML into C Logic code
wayland-scanner private-code hyprland-focus-grab-v1.xml hyprland-focus-grab-v1-client-protocol.c

# 3. Compile it all together into 'libwaylandgrab.so'
gcc -shared -fPIC -o libwaylandgrab.so wayland_grab.c hyprland-focus-grab-v1-client-protocol.c $(pkg-config --cflags --libs gtk4 wayland-client)
or if the file is called dusky.c
gcc -shared -fPIC -o libwaylandgrab.so dusky.c hyprland-focus-grab-v1-client-protocol.c $(pkg-config --cflags --libs gtk4 wayland-client)

> [!success] **Success!**
> You now have a file named `libwaylandgrab.so`. This is your universal tool. You never need to touch the C code again. 

---

## 🐍 Phase 3: Python Integration

Now, how do we plug this into a Python GTK4 application? It requires two simple steps: loading the library, and attaching it to your window.

### Step 3.1: Load the Library (The Walkie-Talkie)
At the top of your Python script (near your imports), add this block. It uses Python's `ctypes` library to load our `.so` file into memory.

```python
import ctypes
import os
import logging
from gi.repository import GLib, Gtk # (Assuming you already import GTK)

# Point this to exactly where you compiled the .so file!
GRAB_LIB_PATH = os.path.expanduser("~/wayland_grab_tool/libwaylandgrab.so")

try:
    LIBGRAB = ctypes.CDLL(GRAB_LIB_PATH)
    CB_TYPE = ctypes.CFUNCTYPE(None)
except OSError:
    logging.warning("Failed to load libwaylandgrab.so. Outside click dismissal disabled.")
    LIBGRAB = None
```

### Step 3.2: Hook it to your Window
Inside your `Gtk.Window` (or `Adw.ApplicationWindow`) class, you just need to connect the Wayland grab when the window is `mapped` (drawn on screen), and hide the window when the grab fires.

Here is the generic boilerplate you can drop into *any* GTK4 class:

```python
class MyPopupWindow(Gtk.Window):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        
        # 1. Keep a persistent reference to our Python callback so it isn't deleted
        if LIBGRAB:
            self._grab_cb = CB_TYPE(self._on_outside_click)
        else:
            self._grab_cb = None

        # 2. Connect to window visibility and mapping signals
        self.connect("notify::visible", self._on_visible_changed)
        self.connect("map", self._on_map)

    # --- WAYLAND GRAB LOGIC ---

    def _on_map(self, *args):
        """Fires the exact moment GTK actually draws the window on the screen."""
        self._activate_grab()

    def _activate_grab(self):
        """Asks the C extension to tell the compositor to watch this window."""
        if LIBGRAB and self.get_visible() and self._grab_cb:
            # hash(self) gives the C-extension the exact memory address of this GTK window
            window_ptr = ctypes.c_void_p(hash(self))
            LIBGRAB.init_wayland_grab(window_ptr, self._grab_cb)

    def _on_outside_click(self):
        """This is called automatically by C when you click outside!"""
        # We must use GLib.idle_add to safely tell GTK to hide the window from a background thread
        GLib.idle_add(self.set_visible, False)

    def _on_visible_changed(self, *args):
        """Cleans up the grab when the window hides."""
        if self.is_visible():
            # If shown, the 'map' signal will handle activation
            pass
        else:
            # If hidden, tell the C extension to drop the focus grab
            if LIBGRAB:
                LIBGRAB.destroy_wayland_grab()
```

> [!tip] **Why do we use the `map` signal?**
> We have to wait until GTK actually *draws* the window (`map`) before asking Wayland to watch it. If we ask Wayland to grab focus on a window before it has pixels attached, the compositor will reject the request.
