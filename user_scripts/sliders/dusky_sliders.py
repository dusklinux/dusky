#!/usr/bin/env python3
"""
Master Slider Widget for Hyprland (Dusky Sliders)
Native GTK4 + Libadwaita Custom Card Implementation.
Hyper-Optimized for Python 3.14+ (Daemonized, Borderless, Atomic I/O)
"""

import sys
import os
import subprocess
import threading
import tempfile
import gc

# ==============================================================================
# 1. HEAVY IMPORTS
# ==============================================================================
try:
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Adw', '1')
    from gi.repository import Gtk, Adw, Gdk, GLib, Gio
except ImportError as e:
    sys.exit(f"Failed to load GTK4/Libadwaita: {e}")

# This defines the Hyprland Window Class: class:^(org.dusky.sliders)$
APP_ID = "org.dusky.sliders"
RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
STATE_FILE = f"{RUNTIME_DIR}/hyprsunset_state.txt"

# Ensure daemon is running immediately to accept IPC commands
try:
    subprocess.run(["pgrep", "-u", str(os.getuid()), "-x", "hyprsunset"], check=True, stdout=subprocess.DEVNULL)
except subprocess.CalledProcessError:
    subprocess.Popen(["hyprsunset"], start_new_session=True, close_fds=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ==============================================================================
# 2. ASYNC ATOMIC I/O & BACKEND INTERFACES
# ==============================================================================
def get_volume_fast() -> float:
    try:
        res = subprocess.run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], capture_output=True, text=True).stdout
        parts = res.split()
        if len(parts) >= 2:
            return float(parts[1]) * 100
    except Exception:
        pass
    return 50.0

def set_volume(val: float) -> None:
    subprocess.Popen(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{int(val)}%"], close_fds=True, stdout=subprocess.DEVNULL)
    if val > 0:
        subprocess.Popen(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"], close_fds=True, stderr=subprocess.DEVNULL)

def get_brightness_native() -> float:
    try:
        with os.scandir("/sys/class/backlight") as it:
            for entry in it:
                if entry.is_dir():
                    with open(f"{entry.path}/brightness", "r") as f_cur, open(f"{entry.path}/max_brightness", "r") as f_max:
                        return (float(f_cur.read()) / float(f_max.read())) * 100
    except Exception:
        pass
    return 50.0

def set_brightness(val: float) -> None:
    subprocess.Popen(["brightnessctl", "set", f"{int(val)}%", "-q"], close_fds=True, stdout=subprocess.DEVNULL)

def get_hyprsunset() -> float:
    try:
        with open(STATE_FILE, "r") as f:
            return float(f.read().strip())
    except Exception:
        pass
    return 4500.0

# --- Atomic State Save Mechanics ---
_write_timer_id = 0

def _atomic_write_state(val: float) -> None:
    """Thread-safe, atomic file replacement to prevent data corruption."""
    try:
        temp_fd, temp_path = tempfile.mkstemp(dir=RUNTIME_DIR, prefix=".sunset_", suffix=".tmp")
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            f.write(str(int(val)))
            f.flush()
            os.fsync(f.fileno())  # Ensure bytes are physically on disk
        os.replace(temp_path, STATE_FILE) # POSIX atomic rename
    except OSError:
        pass

def _debounced_state_save(val: float) -> bool:
    """Spins up background thread for file I/O so GTK never blocks."""
    threading.Thread(target=_atomic_write_state, args=(val,), daemon=True).start()
    global _write_timer_id
    _write_timer_id = 0
    return GLib.SOURCE_REMOVE

def set_hyprsunset(val: float) -> None:
    v = int(val)
    
    # 1. Non-blocking debounce for disk I/O
    global _write_timer_id
    if _write_timer_id:
        GLib.source_remove(_write_timer_id)
    _write_timer_id = GLib.timeout_add(500, _debounced_state_save, v)
    
    # 2. Instant Native IPC
    subprocess.Popen(
        ["hyprctl", "hyprsunset", "temperature", str(v)], 
        close_fds=True, 
        stdout=subprocess.DEVNULL, 
        stderr=subprocess.DEVNULL
    )

# ==============================================================================
# 3. SLEEK GTK4 + LIBADWAITA UI
# ==============================================================================
class CompactSliderRow(Gtk.Box):
    def __init__(self, icon_text: str, css_class: str, min_v: float, max_v: float, step: float, fetch_cb, apply_cb):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        self.apply_cb = apply_cb
        
        self.add_css_class("slider-row")
        
        self.icon = Gtk.Label(label=icon_text)
        self.icon.add_css_class("icon-label")
        self.icon.add_css_class(f"icon-{css_class}")
        self.append(self.icon)
        
        self.adj = Gtk.Adjustment(value=min_v, lower=min_v, upper=max_v, step_increment=step, page_increment=step * 10)
        self.scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.adj)
        self.scale.set_hexpand(True)
        self.scale.set_draw_value(False)
        self.scale.add_css_class("pill-scale")
        self.scale.add_css_class(css_class)  
        self.append(self.scale)
        
        self.val_label = Gtk.Label(label="")
        self.val_label.set_width_chars(4)
        self.val_label.set_xalign(1.0)
        self.val_label.add_css_class("value-label")
        self.append(self.val_label)

        GLib.idle_add(self._lazy_init, fetch_cb)

    def _lazy_init(self, fetch_cb) -> bool:
        real_val = fetch_cb()
        self.adj.set_value(real_val)
        self.val_label.set_label(str(int(real_val)))
        self.scale.connect("value-changed", self._on_value_changed)
        return GLib.SOURCE_REMOVE

    def _on_value_changed(self, scale):
        val = scale.get_value()
        self.val_label.set_label(str(int(val)))
        self.apply_cb(val) 

class SliderWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_default_size(340, -1) 
        self.set_resizable(False)
        self.set_show_menubar(False)
        
        # Strip all Wayland Window Decorations (Titlebar, borders, shadows)
        self.set_decorated(False)
        
        # Intercept close to act as a daemon
        self.connect("close-request", self._on_close_request)
        
        # Bind Escape Key to hide window natively
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_ctrl)
        
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_box)
        
        # Pure Widget Look: HeaderBar completely removed
        card_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4) 
        card_box.set_margin_start(14)
        card_box.set_margin_end(14)
        card_box.set_margin_top(14)
        card_box.set_margin_bottom(14)
        main_box.append(card_box)
        
        card_box.append(CompactSliderRow("", "volume", 0, 100, 1, get_volume_fast, set_volume))
        card_box.append(CompactSliderRow("󰃠", "brightness", 1, 100, 1, get_brightness_native, set_brightness))
        card_box.append(CompactSliderRow("󰡬", "sunset", 1000, 6000, 50, get_hyprsunset, set_hyprsunset))

    def _on_close_request(self, window) -> bool:
        self.set_visible(False)
        gc.collect() # Drop RAM footprint while hidden
        return True

    def _on_key_pressed(self, controller, keyval, keycode, state) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.set_visible(False)
            gc.collect()
            return True
        return False

class SliderApp(Adw.Application):
    def __init__(self):
        # Native D-Bus Single Instance Management
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self._window = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        self.hold() # DAEMON MODE: Prevent process timeout
        
        style_manager = Adw.StyleManager.get_default()
        style_manager.set_color_scheme(Adw.ColorScheme.PREFER_DARK)
        
        css_provider = Gtk.CssProvider()
        css_provider.load_from_string("""
            window {
                background-color: alpha(@window_bg_color, 0.95);
                border-radius: 12px;
            }
            .slider-row { background-color: transparent; padding: 10px 12px; }
            scale.pill-scale trough { min-height: 16px; border-radius: 8px; background-color: rgba(255, 255, 255, 0.08); }
            scale.pill-scale highlight { min-height: 16px; border-radius: 8px; }
            scale.pill-scale slider { min-width: 0px; min-height: 0px; margin: 0px; padding: 0px; background: transparent; border: none; box-shadow: none; }
            scale.volume highlight { background-color: #89b4fa; }
            scale.brightness highlight { background-color: #f9e2af; }
            scale.sunset highlight { background-color: #fab387; }
            .icon-volume { color: #89b4fa; }
            .icon-brightness { color: #f9e2af; }
            .icon-sunset { color: #fab387; }
            .icon-label { font-size: 18px; font-family: 'Symbols Nerd Font', 'JetBrainsMono Nerd Font', monospace; }
            .value-label { 
                font-size: 14px; 
                font-weight: 700; 
                color: alpha(currentColor, 0.8); 
                font-family: 'JetBrainsMono Nerd Font', monospace;
                font-variant-numeric: tabular-nums; /* STOPS NUMBER JITTER */
            }
        """)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), 
            css_provider, 
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        
        # Build UI silently during startup mapping
        self._window = SliderWindow(self)
        self._window.realize()
        self._window.set_visible(False)

    def do_activate(self):
        if self._window:
            self._window.present()

if __name__ == "__main__":
    app = SliderApp()
    sys.exit(app.run(sys.argv))
