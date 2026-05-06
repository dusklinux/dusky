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

// Listen for the grab manager on our isolated queue
static void registry_handler(void *data, struct wl_registry *registry, uint32_t id, const char *interface, uint32_t version) {
    if (strcmp(interface, "hyprland_focus_grab_manager_v1") == 0) {
        grab_manager = wl_registry_bind(registry, id, &hyprland_focus_grab_manager_v1_interface, 1);
    }
}
static void registry_remover(void *data, struct wl_registry *registry, uint32_t id) {}
static const struct wl_registry_listener registry_listener = { &registry_handler, &registry_remover };

// This fires when Hyprland detects an outside click
static void grab_cleared(void *data, struct hyprland_focus_grab_v1 *grab) {
    if (py_callback) {
        py_callback();
    }
}
static const struct hyprland_focus_grab_v1_listener grab_listener = { .cleared = grab_cleared };

// Background thread to dispatch our isolated queue without blocking GTK
static void* dispatch_thread_func(void* arg) {
    while (1) {
        if (wl_display_dispatch_queue(global_display, custom_queue) == -1) {
            break;
        }
    }
    return NULL;
}

void init_wayland_grab(void *gtk_window_ptr, ClearedCallback cb) {
    if (!gtk_window_ptr) return;
    py_callback = cb;

    GtkNative *native = GTK_NATIVE(gtk_window_ptr);
    GdkSurface *gdk_surface = gtk_native_get_surface(native);
    GdkDisplay *gdk_display = gdk_surface_get_display(gdk_surface);

    if (!GDK_IS_WAYLAND_DISPLAY(gdk_display)) {
        fprintf(stderr, "[libduskygrab] Error: Not running under Wayland.\n");
        return;
    }

    global_display = gdk_wayland_display_get_wl_display(gdk_display);
    struct wl_surface *wl_surface = gdk_wayland_surface_get_wl_surface(gdk_surface);

    // One-time initialization of the isolated queue
    if (!custom_queue) {
        custom_queue = wl_display_create_queue(global_display);
        struct wl_registry *registry = wl_display_get_registry(global_display);
        
        // Assign the registry proxy to our custom queue to avoid GTK main loop races
        wl_proxy_set_queue((struct wl_proxy *)registry, custom_queue);
        wl_registry_add_listener(registry, &registry_listener, NULL);
        
        // Roundtrip on OUR queue, leaving GTK's events alone
        wl_display_roundtrip_queue(global_display, custom_queue);

        if (!grab_manager) {
            fprintf(stderr, "[libduskygrab] Error: hyprland_focus_grab_manager_v1 not supported by compositor.\n");
            return;
        }

        // Start background dispatch thread
        pthread_t thread_id;
        pthread_create(&thread_id, NULL, dispatch_thread_func, NULL);
        pthread_detach(thread_id);
    }

    if (grab_manager) {
        if (active_grab) {
            hyprland_focus_grab_v1_destroy(active_grab);
        }
        active_grab = hyprland_focus_grab_manager_v1_create_grab(grab_manager);
        
        // Assign the grab object to our custom queue
        wl_proxy_set_queue((struct wl_proxy *)active_grab, custom_queue);
        
        hyprland_focus_grab_v1_add_listener(active_grab, &grab_listener, NULL);
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
