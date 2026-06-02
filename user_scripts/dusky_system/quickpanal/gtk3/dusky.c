/*
 * dusky.c — Unified Wayland Focus-Grab Extension (GTK3 + GTK4)
 *
 * This library dynamically detects the active GTK runtime (GTK4 or GTK3)
 * using dlsym(RTLD_DEFAULT, ...) to resolve the correct surface-extraction
 * symbols at runtime. It compiles with ZERO GTK headers or link flags,
 * needing only wayland-client, pthread, and dl.
 *
 * Compile:
 *   gcc -shared -fPIC -o libwaylandgrab.so dusky.c \
 *       hyprland-focus-grab-v1-client-protocol.c \
 *       -lwayland-client -lpthread -ldl
 */

#define _GNU_SOURCE
#include "hyprland-focus-grab-v1-client-protocol.h"
#include <dlfcn.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>
#include <wayland-client.h>

/* ── Wayland Grab State ─────────────────────────────────────────────── */

static struct hyprland_focus_grab_manager_v1 *grab_manager = NULL;
static struct hyprland_focus_grab_v1 *active_grab = NULL;
static struct wl_event_queue *custom_queue = NULL;
static struct wl_display *global_display = NULL;

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

/*
 * Generic function-pointer type for all the dlsym-resolved GTK calls.
 * Every resolved function takes a single void* and returns a void*.
 */
typedef void *(*gtk_fn_ptr)(void *);
typedef struct wl_display *(*fn_get_wl_display)(void *);
typedef struct wl_surface *(*fn_get_wl_surface)(void *);

/**
 * resolve_wayland_surfaces()
 *
 * Probes the current process for GTK4 symbols first, then falls back
 * to GTK3 symbols.  On success, fills out_display and out_surface
 * and returns 1.  Returns 0 on failure.
 */
static int resolve_wayland_surfaces(void *gtk_ptr,
                                    struct wl_display **out_display,
                                    struct wl_surface **out_surface) {
  /* Shared between GTK3 and GTK4 */
  fn_get_wl_display get_wl_display = (fn_get_wl_display)dlsym(
      RTLD_DEFAULT, "gdk_wayland_display_get_wl_display");
  if (!get_wl_display) {
    fprintf(stderr, "[libwaylandgrab] Error: Not a Wayland session "
                    "(gdk_wayland_display_get_wl_display not found).\n");
    return 0;
  }

  /* ── GTK4 Path ─────────────────────────────────────────────────── */
  gtk_fn_ptr gtk4_get_surface =
      (gtk_fn_ptr)dlsym(RTLD_DEFAULT, "gtk_native_get_surface");

  if (gtk4_get_surface) {
    gtk_fn_ptr surface_get_display =
        (gtk_fn_ptr)dlsym(RTLD_DEFAULT, "gdk_surface_get_display");
    fn_get_wl_surface wayland_get_surface = (fn_get_wl_surface)dlsym(
        RTLD_DEFAULT, "gdk_wayland_surface_get_wl_surface");

    if (surface_get_display && wayland_get_surface) {
      void *gdk_surface = gtk4_get_surface(gtk_ptr);
      if (!gdk_surface) {
        fprintf(stderr, "[libwaylandgrab] GTK4: GdkSurface is NULL.\n");
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
    fn_get_wl_surface wayland_get_surface = (fn_get_wl_surface)dlsym(
        RTLD_DEFAULT, "gdk_wayland_window_get_wl_surface");

    if (window_get_display && wayland_get_surface) {
      void *gdk_window = gtk3_get_window(gtk_ptr);
      if (!gdk_window) {
        fprintf(stderr, "[libwaylandgrab] GTK3: GdkWindow is NULL "
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

  fprintf(stderr, "[libwaylandgrab] Error: Could not resolve GTK3 or GTK4 "
                  "Wayland symbols.\n");
  return 0;
}

/* ── Public API ─────────────────────────────────────────────────────── */

void init_wayland_grab(void *gtk_window_ptr, ClearedCallback cb) {
  if (!gtk_window_ptr)
    return;
  py_callback = cb;

  struct wl_surface *wl_surface = NULL;
  if (!resolve_wayland_surfaces(gtk_window_ptr, &global_display, &wl_surface)) {
    return;
  }

  /* One-time initialisation of the isolated event queue */
  if (!custom_queue) {
    custom_queue = wl_display_create_queue(global_display);
    struct wl_registry *registry = wl_display_get_registry(global_display);

    wl_proxy_set_queue((struct wl_proxy *)registry, custom_queue);
    wl_registry_add_listener(registry, &registry_listener, NULL);
    wl_display_roundtrip_queue(global_display, custom_queue);

    if (!grab_manager) {
      fprintf(stderr, "[libwaylandgrab] Error: "
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
    active_grab = hyprland_focus_grab_manager_v1_create_grab(grab_manager);

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