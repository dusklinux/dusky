#!/usr/bin/env python3
"""
Dusky Quick Panal: Configurable Main Execution
Dynamically parses config.toml and applies it to the GTK components via tomllib.
"""

from __future__ import annotations
import sys
import os
import json
import gc
import signal
import tomllib
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, override

# CRITICAL FIX 1: Prevent Python from writing __pycache__ folders for local imports.
# This flawlessly replicates the clean directory behavior of the old monolithic script.
sys.dont_write_bytecode = True

# CRITICAL FIX 2: Restore native, instantaneous, and silent termination on Ctrl-C.
# This prevents GTK3 and background threads from throwing messy teardown tracebacks.
signal.signal(signal.SIGINT, signal.SIG_DFL)

if sys.version_info < (3, 14, 5):
    raise SystemExit("Dusky Quick Panal requires Python 3.14.5 or newer.")

try:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("Pango", "1.0")
    from gi.repository import Gdk, Gio, GLib, GLibUnix, Gtk, Pango
except (ImportError, ValueError) as exc:
    raise SystemExit(f"Failed to load GTK3: {exc}") from exc

# Import from our custom modules
from dusky_backend import (
    APP_ID, HOME, execute_cmd, run_command, fetch_json_output, _reclaim_idle_memory,
    LatestValueWorker, RefreshPool, HyprsunsetController, LOG,
    HAS_VOLUME, HAS_BRIGHTNESS, HAS_LOCAL_BRIGHTNESS, HAS_SUNSET, DDC_MANAGER,
    get_volume, apply_volume, get_brightness, apply_local_brightness, 
    get_hyprsunset_state, _RE_MAKO_BADGE, _RE_UPDATES_TOTAL,
    BRIGHTNESS_POST_SUBMIT_REFRESH_GRACE_SECONDS, SUNSET_STATE_WRITE_DEBOUNCE_SECONDS
)

from dusky_ui import (
    CSS, _add_css_class, _remove_css_class,
    QuickIconToggle, MetricPill, CompactSliderRow, NotificationsPanel
)

try:
    import ctypes
    _grab_lib_path = os.path.expanduser("~/user_scripts/dusky_system/click_away_to_dismiss/libwaylandgrab.so")
    LIBGRAB = ctypes.CDLL(_grab_lib_path)
    CB_TYPE = ctypes.CFUNCTYPE(None)
except OSError:
    LIBGRAB = None

# ==============================================================================
# CONFIGURATION SYSTEM
# ==============================================================================
CONFIG_DIR = Path(HOME) / ".config" / "dusky" / "quickpanal"
CONFIG_FILE = CONFIG_DIR / "config.toml"

# Indestructible fallback string in case the file gets corrupted again
DEFAULT_TOML_CONFIG = """[layout]
show_weather = true
show_metrics = true
show_quick_toggles = true
show_power_profiles = true
show_sliders = true
show_notifications = true
show_media = false

[[toggles]]
id = "wifi"
icon = "network-wireless-symbolic"
label = "Wi-Fi"
tooltip = "Wi-Fi\\nLMB: Network Manager"
on_left = "kitty --class dusky_network.sh ~/user_scripts/network_manager/dusky_network.sh"

[[toggles]]
id = "idle"
icon = "timer-symbolic"
label = "Hypridle"
tooltip = "Hypridle\\nLMB: Toggle | RMB: Lock Screen"
on_left = "~/user_scripts/waybar/toggle_hypridle.sh"
on_right = "~/user_scripts/hyprlock/lock.sh"

[[toggles]]
id = "blur"
icon = "edit-opacity-symbolic"
label = "Visuals"
tooltip = "Visuals\\nLMB: Toggle Blur/Shadow"
on_left = "~/user_scripts/hypr/hypr_blur_opacity_shadow_toggle.sh toggle"

[[toggles]]
id = "updates"
icon = "folder-download-symbolic"
label = "Updates"
tooltip = "Updates\\nLMB: System Update | RMB: Dusky Update"
on_left = "kitty --class system_update.sh --hold sh -c '~/user_scripts/update_dusky/system_update.sh --all'"
on_right = "kitty --class update_dusky.sh --hold sh -c '~/user_scripts/update_dusky/update_dusky.sh'"

[[toggles]]
id = "dnd"
icon = "notification-symbolic"
label = "Notifications"
tooltip = "Notifications\\nLMB: Rofi Menu | MMB: Clear | RMB: Toggle DND"
on_left = "~/user_scripts/rofi/rofi_mako.sh"
on_middle = "~/user_scripts/waybar/mako.sh --clear && pkill -RTMIN+8 waybar"
on_right = "makoctl mode -t do-not-disturb && pkill -RTMIN+8 waybar"
"""

def load_or_create_config() -> dict:
    if not CONFIG_FILE.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(DEFAULT_TOML_CONFIG, encoding="utf-8")
        
    try:
        with CONFIG_FILE.open("rb") as f: 
            return tomllib.load(f)
    except Exception as e:
        LOG.error(f"Error loading {CONFIG_FILE}: {e}")
        # Bulletproof fallback: If the file is broken, load the hardcoded 5-module string
        return tomllib.loads(DEFAULT_TOML_CONFIG)

def _get_active_monitor_scaled_height() -> float:
    try:
        r = run_command(["hyprctl", "-j", "monitors"], timeout=1.0, capture_stdout=True)
        if r is not None and r.returncode == 0 and r.stdout:
            for m in json.loads(r.stdout):
                if m.get("focused"): return float(m["height"]) / float(m.get("scale", 1.0))
    except Exception: pass
    return 1080.0

class QuickPanalWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, pool: RefreshPool, config: dict, volume_submit: Any, brightness_submit: Any, sunset_submit: Any):
        super().__init__(application=app)
        self.pool = pool
        self.config = config
        self.layout_cfg = self.config.get("layout", {})
        
        self._timer_id: int | None = None
        self._cpu_last = (0, 0)
        self._updating_power = False
        self._slider_rows: list[CompactSliderRow] = []
        self.dynamic_toggles: dict[str, QuickIconToggle] = {}
        self._grab_active = False
        self._wifi_pending = False
        self._bt_pending = False

        # Power Profile Revision Tracking
        self._power_pending_revision = 0
        self._power_pending_profile: str | None = None

        # CRITICAL UI FIX: Exact 15% physical reduction mapped out flawlessly (380 -> 320)
        self.set_default_size(320, -1)
        self.set_size_request(320, -1) 
        self.set_resizable(False)
        self.set_decorated(False)
        _add_css_class(self, "panel-window")

        self.connect("delete-event", self._on_delete_event)
        self.connect("show", self._on_show)
        self.connect("hide", self._on_hide)
        self.connect("map", self._on_map)
        self.connect("unmap", self._on_unmap)
        self.connect("key-press-event", self._on_key_pressed)

        self._grab_cb = CB_TYPE(self._on_grab_cleared) if LIBGRAB else None

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        # Scaled margins by ~15% (18px -> 12px) to preserve internal layout room
        main_box.set_margin_start(12); main_box.set_margin_end(12)
        main_box.set_margin_top(12); main_box.set_margin_bottom(12)

        # Global scrolling to support endless notifications gracefully
        self.scrolled_main = Gtk.ScrolledWindow()
        self.scrolled_main.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scrolled_main.set_overlay_scrolling(True) # Ensure scrollbar floats rather than pushing content
        self.scrolled_main.add(main_box)
        
        # CRITICAL UI FIX: Absolutely DO NOT let natural widths propagate and bloat the parent.
        self.scrolled_main.set_propagate_natural_width(False)
        self.scrolled_main.set_propagate_natural_height(True)
        max_h = _get_active_monitor_scaled_height() * 0.85 
        self.scrolled_main.set_max_content_height(int(max_h))
        
        # --- Dynamic Opacity Gradient Overlay ---
        self.bottom_fade = Gtk.EventBox()
        self.bottom_fade.set_valign(Gtk.Align.END)
        self.bottom_fade.set_size_request(-1, 48) # Generous height for a smooth gradient
        self.bottom_fade.set_no_show_all(True)
        self.bottom_fade.hide()
        _add_css_class(self.bottom_fade, "bottom-fade")
        
        self.overlay = Gtk.Overlay()
        self.overlay.add(self.scrolled_main)
        self.overlay.add_overlay(self.bottom_fade)
        self.overlay.set_overlay_pass_through(self.bottom_fade, True) # Clicks pass right through it!
        
        self.add(self.overlay)
        
        self.v_adj = self.scrolled_main.get_vadjustment()
        self.v_adj.connect("value-changed", self._update_fade_visibility)
        self.v_adj.connect("changed", self._update_fade_visibility)

        # --- Base Header ---
        self.header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        
        self.weather_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        _add_css_class(self.weather_box, "weather-pill")
        self.weather_icon = Gtk.Image.new_from_icon_name("weather-few-clouds-symbolic", Gtk.IconSize.MENU)
        self.weather_icon.set_pixel_size(16)
        
        self.weather_lbl = Gtk.Label()
        # CRITICAL UI FIX: Weather text can't physically inflate header
        self.weather_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self.weather_lbl.set_width_chars(1)
        _add_css_class(self.weather_lbl, "weather-text")
        
        self.weather_box.pack_start(self.weather_icon, False, False, 0)
        self.weather_box.pack_start(self.weather_lbl, False, False, 0)
        self.weather_box.set_no_show_all(True)
        self.weather_box.hide()

        self.power_btn = Gtk.Button()
        self.power_btn.set_image(Gtk.Image.new_from_icon_name("system-shutdown-symbolic", Gtk.IconSize.BUTTON))
        _add_css_class(self.power_btn, "power-header-btn")
        self.power_btn.set_valign(Gtk.Align.CENTER) 
        self.power_btn.set_halign(Gtk.Align.CENTER) 
        self.power_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.power_btn.connect("clicked", lambda _: execute_cmd(f"{HOME}/user_scripts/wlogout/wlogout_scale.sh"))

        self.clock_event_box = Gtk.EventBox()
        self.clock_event_box.set_visible_window(False)
        self.clock_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.lbl_time = Gtk.Label()
        _add_css_class(self.lbl_time, "header-time")
        
        self.lbl_date = Gtk.Label()
        # CRITICAL UI FIX: Lock down date minimum width
        self.lbl_date.set_ellipsize(Pango.EllipsizeMode.END)
        self.lbl_date.set_width_chars(1)
        _add_css_class(self.lbl_date, "header-date")
        
        self.clock_box.pack_start(self.lbl_time, False, False, 0)
        self.clock_box.pack_start(self.lbl_date, False, False, 0)
        self.clock_event_box.add(self.clock_box)
        self.clock_event_box.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.clock_event_box.connect("button-press-event", lambda *args: (execute_cmd("gnome-clocks"), True)[1])

        if self.layout_cfg.get("show_weather", True): self.header_box.pack_start(self.weather_box, False, False, 0)
        self.header_box.pack_end(self.power_btn, False, False, 0)
        self.header_box.set_center_widget(self.clock_event_box) 
        main_box.pack_start(self.header_box, False, False, 0)

        # --- Metrics ---
        if self.layout_cfg.get("show_metrics", True):
            self.metrics_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            self.metrics_row.set_homogeneous(True)
            self.pill_net = MetricPill(None, "Network Usage", small_text=True)
            # Memory icon fixed to user preference
            self.pill_ram = MetricPill("drive-harddisk-symbolic", "RAM Usage\nLMB: Open zramctl", on_click="kitty --class zramctl --hold zramctl")
            self.pill_cpu = MetricPill("cpu-symbolic", "CPU Usage\nLMB: Open btop", on_click="kitty --class btop btop")
            self.metrics_row.pack_start(self.pill_net, True, True, 0)
            self.metrics_row.pack_start(self.pill_ram, True, True, 0)
            self.metrics_row.pack_start(self.pill_cpu, True, True, 0)
            main_box.pack_start(self.metrics_row, False, False, 0)

        # --- Quick Toggles ---
        if self.layout_cfg.get("show_quick_toggles", True):
            self.flow = Gtk.FlowBox()
            self.flow.set_selection_mode(Gtk.SelectionMode.NONE)
            self.flow.set_max_children_per_line(5)
            self.flow.set_min_children_per_line(5)
            # CRITICAL UI FIX: Shrink spacing to fit 5 items safely below 320px
            self.flow.set_column_spacing(10)
            self.flow.set_row_spacing(10)

            for t_conf in self.config.get("toggles", []):
                tg = QuickIconToggle(
                    icon_name=t_conf.get("icon", "applications-system-symbolic"),
                    tooltip=t_conf.get("tooltip", ""),
                    on_left=t_conf.get("on_left", ""),
                    on_middle=t_conf.get("on_middle", ""),
                    on_right=t_conf.get("on_right", "")
                )
                self.flow.add(tg)
                if t_id := t_conf.get("id"):
                    self.dynamic_toggles[t_id] = tg
            main_box.pack_start(self.flow, False, False, 0)

        # --- Power Profiles ---
        if self.layout_cfg.get("show_power_profiles", True):
            self.power_container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
            _add_css_class(self.power_container, "power-profile-row")

            # Wi-Fi toggle (Static Icon + Switch)
            self.wifi_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            self.wifi_icon = Gtk.Image.new_from_icon_name("network-wireless-symbolic", Gtk.IconSize.BUTTON)
            _add_css_class(self.wifi_icon, "accent-icon")
            self.wifi_box.pack_start(self.wifi_icon, False, False, 0)
            
            self.wifi_switch = Gtk.Switch()
            self.wifi_switch.set_valign(Gtk.Align.CENTER)
            self.wifi_switch.set_can_focus(False)
            _add_css_class(self.wifi_switch, "compact-switch")
            self.wifi_switch.connect("state-set", self._on_wifi_state_set)
            self.wifi_box.pack_start(self.wifi_switch, False, False, 0)
            self.power_container.pack_start(self.wifi_box, False, False, 0)

            # Tiny separator/spacer between Wifi and BT
            spacer_wbt = Gtk.Box()
            spacer_wbt.set_size_request(12, -1)
            self.power_container.pack_start(spacer_wbt, False, False, 0)

            # Bluetooth toggle (Static Icon + Switch)
            self.bt_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            self.bt_icon = Gtk.Image.new_from_icon_name("bluetooth-active-symbolic", Gtk.IconSize.BUTTON)
            _add_css_class(self.bt_icon, "accent-icon")
            self.bt_box.pack_start(self.bt_icon, False, False, 0)
            
            self.bt_switch = Gtk.Switch()
            self.bt_switch.set_valign(Gtk.Align.CENTER)
            self.bt_switch.set_can_focus(False)
            _add_css_class(self.bt_switch, "compact-switch")
            self.bt_switch.connect("state-set", self._on_bt_state_set)
            self.bt_box.pack_start(self.bt_switch, False, False, 0)
            self.power_container.pack_start(self.bt_box, False, False, 0)

            # Expanding Spacer to dynamically push Power Profiles to the right edge
            expand_spacer = Gtk.Box()
            self.power_container.pack_start(expand_spacer, True, True, 0)

            # Vertical separator to define zones beautifully
            sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
            sep.set_margin_start(4)
            sep.set_margin_end(6)
            sep.set_margin_top(4)
            sep.set_margin_bottom(4)
            self.power_container.pack_start(sep, False, False, 0)

            # Restored TLP Power Logic Exactly as you had it
            self.power_cmds = { "Balanced": "~/user_scripts/battery/tlp/tlp_mode_toggle.sh balanced", "Performance": "~/user_scripts/battery/tlp/tlp_mode_toggle.sh performance", "Power Saver": "~/user_scripts/battery/tlp/tlp_mode_toggle.sh power-saver" }
            self.power_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            self.btn_save = Gtk.RadioButton(); self.btn_save.set_mode(False); self.btn_save.set_image(Gtk.Image.new_from_icon_name("power-profile-power-saver-symbolic", Gtk.IconSize.BUTTON)); _add_css_class(self.btn_save, "power-ring-btn")
            self.btn_bal = Gtk.RadioButton.new_from_widget(self.btn_save); self.btn_bal.set_mode(False); self.btn_bal.set_image(Gtk.Image.new_from_icon_name("power-profile-balanced-symbolic", Gtk.IconSize.BUTTON)); _add_css_class(self.btn_bal, "power-ring-btn")
            self.btn_perf = Gtk.RadioButton.new_from_widget(self.btn_save); self.btn_perf.set_mode(False); self.btn_perf.set_image(Gtk.Image.new_from_icon_name("power-profile-performance-symbolic", Gtk.IconSize.BUTTON)); _add_css_class(self.btn_perf, "power-ring-btn")
            
            self.btn_save.connect("toggled", self._on_power_toggled, "Power Saver")
            self.btn_bal.connect("toggled", self._on_power_toggled, "Balanced")
            self.btn_perf.connect("toggled", self._on_power_toggled, "Performance")
            for btn in (self.btn_save, self.btn_bal, self.btn_perf): self.power_box.pack_start(btn, False, False, 0)
            self.power_container.pack_end(self.power_box, False, False, 0)
            main_box.pack_start(self.power_container, False, False, 0)

        # --- Sliders ---
        # Sliders restored as requested
        if self.layout_cfg.get("show_sliders", True):
            self.sliders_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            _add_css_class(self.sliders_box, "sliders-container")
            if HAS_VOLUME:
                row = CompactSliderRow("", "volume", 0.0, 100.0, 1.0, get_volume, volume_submit, self.pool)
                self._slider_rows.append(row)
                self.sliders_box.pack_start(row, False, False, 0)
            if HAS_BRIGHTNESS:
                row = CompactSliderRow("󰃠", "brightness", 1.0, 100.0, 1.0, get_brightness, brightness_submit, self.pool, post_submit_refresh_grace_seconds=BRIGHTNESS_POST_SUBMIT_REFRESH_GRACE_SECONDS)
                self._slider_rows.append(row)
                self.sliders_box.pack_start(row, False, False, 0)
            if HAS_SUNSET:
                row = CompactSliderRow("󰡬", "sunset", 1000.0, 6000.0, 50.0, get_hyprsunset_state, sunset_submit, self.pool, post_submit_refresh_grace_seconds=BRIGHTNESS_POST_SUBMIT_REFRESH_GRACE_SECONDS)
                self._slider_rows.append(row)
                self.sliders_box.pack_start(row, False, False, 0)
            if self._slider_rows: main_box.pack_start(self.sliders_box, False, False, 0)

        # --- Borderless Integrated Notification Module ---
        if self.layout_cfg.get("show_notifications", True):
            self.notifications_module = NotificationsPanel(self.pool)
            main_box.pack_start(self.notifications_module, True, True, 0)

    def _update_fade_visibility(self, *args):
        # Calculate if there is hidden scrollable content below our current viewport
        max_val = self.v_adj.get_upper() - self.v_adj.get_page_size()
        if max_val > 0.5 and self.v_adj.get_value() < (max_val - 2.0):
            self.bottom_fade.show()
        else:
            self.bottom_fade.hide()

    # --- UI Updaters ---
    def _update_ui_state(self):
        now = datetime.now()
        self.lbl_time.set_label(now.strftime("%I:%M"))
        self.lbl_date.set_label(now.strftime("%A, %B %d"))

        self.pool.submit(self._fetch_weather)
        self.pool.submit(self._fetch_mako)
        self.pool.submit(self._fetch_idle)
        self.pool.submit(self._fetch_blur)
        self.pool.submit(self._fetch_net_bt_state)
        self.pool.submit(self._fetch_power_profile)
        self.pool.submit(self._fetch_hardware_metrics)
        self.pool.submit(self._fetch_network)
        self.pool.submit(self._fetch_updates)

        for row in self._slider_rows: row.refresh_async()
        if hasattr(self, "notifications_module"): self.notifications_module.refresh_async()

        return GLib.SOURCE_CONTINUE

    def _fetch_weather(self):
        if not self.layout_cfg.get("show_weather", True): return
        try:
            data = fetch_json_output(f"python3 {HOME}/user_scripts/waybar/weather.py")
            if data and data.get("text"): GLib.idle_add(self._apply_weather, data.get("text").strip())
            else: GLib.idle_add(self.weather_box.hide)
        except Exception: GLib.idle_add(self.weather_box.hide)

    def _apply_weather(self, text: str):
        self.weather_lbl.set_label(text)
        self.weather_icon.show()
        self.weather_lbl.show()
        self.weather_box.show()

    def _fetch_mako(self):
        if not self.dynamic_toggles.get("dnd"): return
        data = fetch_json_output(f"{HOME}/user_scripts/waybar/mako.sh --horizontal")
        if data: GLib.idle_add(self._apply_mako, data)

    def _apply_mako(self, data: dict):
        tg = self.dynamic_toggles.get("dnd")
        if not tg: return
        text = data.get("text", "")
        css = data.get("class", "empty")
        badge_match = _RE_MAKO_BADGE.search(text)
        badge = badge_match.group(0) if badge_match else ""
        final_tt = data.get("tooltip", "Notifications") + "\nLMB: Open | MMB: Clear | RMB: Toggle DND"
        if css in ("dnd", "dnd-pending"): tg.update_state(icon="notifications-disabled-symbolic", css_class="dnd-active", tooltip=final_tt, badge=badge)
        else: tg.update_state(icon="notification-symbolic", css_class="normal", tooltip=final_tt, badge=badge)

    def _fetch_idle(self):
        if not self.dynamic_toggles.get("idle"): return
        r = run_command(["pgrep", "-x", "hypridle"], timeout=0.8, capture_stdout=True)
        GLib.idle_add(self._apply_idle, r is not None and r.returncode == 0)

    def _apply_idle(self, is_active: bool):
        tg = self.dynamic_toggles.get("idle")
        if not tg: return
        if is_active: tg.update_state(icon="timer-symbolic", css_class="normal", tooltip="Idle Allowed (Timer Active)\nLMB: Toggle | RMB: Lock Screen")
        else: tg.update_state(icon="view-reveal-symbolic", css_class="active", tooltip="Idle Inhibited (Awake)\nLMB: Toggle | RMB: Lock Screen")

    def _fetch_blur(self):
        if not self.dynamic_toggles.get("blur"): return
        try:
            with open(f"{HOME}/.config/dusky/settings/opacity_blur", "r") as f: state = f.read().strip().lower()
            GLib.idle_add(self._apply_blur, state == "true")
        except Exception: pass

    def _apply_blur(self, is_active: bool):
        tg = self.dynamic_toggles.get("blur")
        if not tg: return
        if is_active: tg.update_state(icon="applications-graphics-symbolic", css_class="active", tooltip="Visuals: Blur & Shadow ON\nLMB: Toggle")
        else: tg.update_state(icon="edit-opacity-symbolic", css_class="normal", tooltip="Visuals: Performance Mode\nLMB: Toggle")

    @staticmethod
    def _is_bt_rfkill_blocked() -> bool:
        r = run_command(["rfkill", "list", "bluetooth"], timeout=0.5, capture_stdout=True)
        if r is not None and r.returncode == 0 and r.stdout:
            return "Soft blocked: yes" in r.stdout
        return False

    def _fetch_net_bt_state(self):
        if not hasattr(self, "wifi_switch"): return
        try:
            wifi_r = run_command(
                ["busctl", "get-property", "org.freedesktop.NetworkManager",
                 "/org/freedesktop/NetworkManager", "org.freedesktop.NetworkManager",
                 "WirelessEnabled"],
                timeout=1.0, capture_stdout=True)
            wifi_on = wifi_r is not None and wifi_r.returncode == 0 and "true" in wifi_r.stdout

            bt_r = run_command(
                ["busctl", "get-property", "org.bluez", "/org/bluez/hci0",
                 "org.bluez.Adapter1", "Powered"],
                timeout=1.0, capture_stdout=True)
            bt_powered = bt_r is not None and bt_r.returncode == 0 and "true" in bt_r.stdout
            bt_rfkill_blocked = self._is_bt_rfkill_blocked()
            bt_on = bt_powered and not bt_rfkill_blocked

            GLib.idle_add(self._apply_net_bt_state, wifi_on, bt_on)
        except Exception: pass

    def _apply_net_bt_state(self, wifi_on: bool, bt_on: bool):
        wifi_icon = "network-wireless-symbolic" if wifi_on else "network-wireless-disconnected-symbolic"
        self.wifi_icon.set_from_icon_name(wifi_icon, Gtk.IconSize.BUTTON)
        bt_icon = "bluetooth-active-symbolic" if bt_on else "bluetooth-disabled-symbolic"
        self.bt_icon.set_from_icon_name(bt_icon, Gtk.IconSize.BUTTON)

        if self._wifi_pending: return
        if self.wifi_switch.get_active() != wifi_on:
            self.wifi_switch.set_active(wifi_on)
        if self._bt_pending: return
        if self.bt_switch.get_active() != bt_on:
            self.bt_switch.set_active(bt_on)

    def _on_wifi_state_set(self, switch, state):
        val = "true" if state else "false"
        execute_cmd(f"busctl set-property org.freedesktop.NetworkManager /org/freedesktop/NetworkManager org.freedesktop.NetworkManager WirelessEnabled b {val}")
        icon = "network-wireless-symbolic" if state else "network-wireless-disconnected-symbolic"
        self.wifi_icon.set_from_icon_name(icon, Gtk.IconSize.BUTTON)
        self._wifi_pending = True
        GLib.timeout_add(800, self._clear_wifi_pending)
        return False

    def _clear_wifi_pending(self):
        self._wifi_pending = False
        self.pool.submit(self._fetch_net_bt_state)
        return GLib.SOURCE_REMOVE

    def _on_bt_state_set(self, switch, state):
        if state and self._is_bt_rfkill_blocked():
            execute_cmd("sudo -n /usr/bin/rfkill unblock bluetooth")
        val = "true" if state else "false"
        execute_cmd(f"busctl set-property org.bluez /org/bluez/hci0 org.bluez.Adapter1 Powered b {val}")
        icon = "bluetooth-active-symbolic" if state else "bluetooth-disabled-symbolic"
        self.bt_icon.set_from_icon_name(icon, Gtk.IconSize.BUTTON)
        self._bt_pending = True
        GLib.timeout_add(800, self._clear_bt_pending)
        return False

    def _clear_bt_pending(self):
        self._bt_pending = False
        self.pool.submit(self._fetch_net_bt_state)
        return GLib.SOURCE_REMOVE

    def _fetch_power_profile(self):
        if not hasattr(self, "power_container"): return
        
        # CRITICAL GUARD: Prevent reading old file states while actively processing
        if getattr(self, "_power_pending_profile", None) is not None: return

        try:
            path = Path(HOME) / ".config" / "dusky" / "settings" / "tlp_state"
            if path.exists():
                state = path.read_text(encoding="utf-8").strip().lower()
                GLib.idle_add(self._apply_power_profile, state)
            else:
                LOG.warning(f"Power profile state file does not exist: {path}")
        except Exception as e:
            LOG.exception("Failed to fetch power profile")

    def _apply_power_profile(self, profile: str) -> bool:
        # Double-check pending state in the main thread to be completely bulletproof
        if getattr(self, "_power_pending_profile", None) is not None: return GLib.SOURCE_REMOVE

        mapping = {"balanced": self.btn_bal, "performance": self.btn_perf, "power-saver": self.btn_save}
        target_btn = mapping.get(profile)
        if target_btn and not target_btn.get_active():
            LOG.info(f"Applying power profile: {profile}")
            self._updating_power = True
            target_btn.set_active(True)
            for btn in (self.btn_save, self.btn_bal, self.btn_perf):
                _remove_css_class(btn, "applying") # Clean up stray UI anomalies just in case
            self._updating_power = False
            
        return GLib.SOURCE_REMOVE

    def _on_power_toggled(self, button: Gtk.RadioButton, profile_name: str):
        if not button.get_active() or self._updating_power: return
        if cmd := self.power_cmds.get(profile_name):
            # Establish the key expected by UI and Backend
            profile_key = profile_name.lower().replace(" ", "-")

            # 1. Lock state and increment revision counter
            self._power_pending_revision += 1
            current_rev = self._power_pending_revision
            self._power_pending_profile = profile_key

            # 2. Visually indicate the "applying" state
            for btn in (self.btn_save, self.btn_bal, self.btn_perf):
                _remove_css_class(btn, "applying")
            _add_css_class(button, "applying")

            # 3. Spawn a temporary, on-demand thread that dies when finished
            threading.Thread(target=self._run_power_cmd_worker, args=(cmd, current_rev), daemon=True).start()

    def _run_power_cmd_worker(self, cmd: str, revision: int):
        try:
            # run_command gracefully handles timeouts and blocking natively
            run_command(["/usr/bin/bash", "-c", cmd], timeout=5.0)
        except Exception as e:
            LOG.error(f"Failed to apply power profile: {e}")
        
        # Dispatch UI clearing back to the GTK Main Thread
        GLib.idle_add(self._power_cmd_finished, revision)

    def _power_cmd_finished(self, revision: int) -> bool:
        # If the user clicked another profile while we were working, ignore this completion entirely!
        if revision != self._power_pending_revision:
            return GLib.SOURCE_REMOVE
        
        # This was the absolute latest click. Unlock the state machine.
        self._power_pending_profile = None
        for btn in (self.btn_save, self.btn_bal, self.btn_perf):
            _remove_css_class(btn, "applying")
        
        # Request an immediate live refresh to fully confirm the new state
        self.pool.submit(self._fetch_power_profile)
        return GLib.SOURCE_REMOVE

    def _fetch_hardware_metrics(self):
        if not hasattr(self, "metrics_row"): return
        try:
            with open("/proc/stat", "r") as f: parts = [int(p) for p in f.readline().split()[1:]]
            idle = parts[3] + parts[4]; total = sum(parts)
            last_idle, last_total = self._cpu_last
            d_idle, d_total = idle - last_idle, total - last_total
            cpu_usage = 100 * (1.0 - d_idle / d_total) if d_total > 0 else 0
            self._cpu_last = (idle, total)

            mem_tot = mem_av = 0
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if line.startswith("MemTotal:"): mem_tot = int(line.split()[1])
                    elif line.startswith("MemAvailable:"): mem_av = int(line.split()[1])
                    if mem_tot and mem_av: break
            ram_used = (mem_tot - mem_av) / 1048576
            GLib.idle_add(self.pill_cpu.set_value, f"{cpu_usage:.0f}%")
            GLib.idle_add(self.pill_ram.set_value, f"{ram_used:.1f} GB")
        except Exception: pass

    def _fetch_network(self):
        if not hasattr(self, "metrics_row"): return
        data = fetch_json_output(f"{HOME}/user_scripts/waybar/network/network_meter_calling.sh --horizontal")
        GLib.idle_add(self.pill_net.apply_json, data, "network-disconnected")

    def _fetch_updates(self):
        if not self.dynamic_toggles.get("updates"): return
        try:
            with open(f"{HOME}/.config/dusky/settings/waybar_update_counter_h", "r") as f: data = json.load(f)
            GLib.idle_add(self._apply_updates, data)
        except Exception: pass

    def _apply_updates(self, data: dict):
        tg = self.dynamic_toggles.get("updates")
        if not tg: return
        css = data.get("class", "updated")
        final_tt = f"{data.get('tooltip', 'Updates')}\n\nLMB: System Update | RMB: Dusky Update"
        if css == "pending":
            match = _RE_UPDATES_TOTAL.search(data.get('tooltip', ''))
            tg.update_state(icon="folder-download-symbolic", css_class="normal", tooltip=final_tt, badge=match.group(1) if match else "!")
        else: tg.update_state(icon="folder-download-symbolic", css_class="normal", tooltip=final_tt, badge="")

    def _on_map(self, *args):
        if LIBGRAB and self.get_visible() and self._grab_cb and not self._grab_active:
            self._grab_active = True
            ptr_val = hash(self)
            if ptr_val < 0: ptr_val += 1 << (ctypes.sizeof(ctypes.c_void_p) * 8)
            LIBGRAB.init_wayland_grab(ctypes.c_void_p(ptr_val), self._grab_cb)

    def _on_unmap(self, *args):
        if LIBGRAB and self._grab_active:
            LIBGRAB.destroy_wayland_grab()
            self._grab_active = False

    def _on_grab_cleared(self): 
        def safe_hide():
            self.hide()
            return GLib.SOURCE_REMOVE
        GLib.idle_add(safe_hide)

    def _on_delete_event(self, _window, _event): self.hide(); return True 
    def _on_key_pressed(self, widget, event):
        if event.keyval == Gdk.KEY_Escape: self.hide(); return True
        return False

    def _reposition_to_corner(self):
        try:
            r = run_command(["hyprctl", "-j", "monitors"], timeout=1.0, capture_stdout=True)
            if r is None or r.returncode != 0 or not r.stdout: return
            monitors = json.loads(r.stdout)
            mon = next((m for m in monitors if m.get("focused")), monitors[0])
            scale = float(mon.get("scale", 1.0))
            mon_w = float(mon["width"]) / scale
            mon_h = float(mon["height"]) / scale

            r = run_command(["hyprctl", "-j", "clients"], timeout=1.0, capture_stdout=True)
            if r is None or r.returncode != 0 or not r.stdout: return
            clients = json.loads(r.stdout)
            win = next((c for c in clients if c.get("class") == "dusky_quickpanal.py"), None)
            if not win: return
            win_w = float(win["size"][0])
            win_h = float(win["size"][1])

            target_x = int(mon_w - win_w - 20)
            target_y = int(mon_h - win_h - 20)

            run_command(["hyprctl", "dispatch", f"hl.dsp.window.move({{ x = {target_x}, y = {target_y} }})"],
                        timeout=1.0, capture_stdout=True)
        except Exception:
            pass

    def _on_show(self, *args):
        app = self.get_application()
        if app and hasattr(app, "resume_workers"): app.resume_workers()
        if self._timer_id is None:
            self._update_ui_state()
            self._timer_id = GLib.timeout_add(2000, self._update_ui_state)
        GLib.timeout_add(150, self._reposition_to_corner)

    def _on_hide(self, *args):
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id); self._timer_id = None
            
        app = self.get_application()
        if app and hasattr(app, "suspend_workers"): app.suspend_workers()
        GLib.timeout_add(500, lambda: (self.get_visible() or _reclaim_idle_memory(), GLib.SOURCE_REMOVE)[1])


class QuickPanalApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.window, self.pool, self._volume_worker, self._local_brightness_worker, self._sunset_controller = None, None, None, None, None

    def submit_volume(self, value: float):
        if self._volume_worker: self._volume_worker.submit(value)
        
    def _submit_brightness(self, value: float):
        if self._local_brightness_worker: self._local_brightness_worker.submit(value)
        if DDC_MANAGER: DDC_MANAGER.submit(value)
        
    def submit_sunset(self, value: float):
        if self._sunset_controller: self._sunset_controller.submit(value)

    def suspend_workers(self):
        if self.pool: self.pool.shutdown()
        if self._sunset_controller: self._sunset_controller.stop()
        if self._local_brightness_worker: self._local_brightness_worker.stop()
        if DDC_MANAGER: DDC_MANAGER.stop()
        if self._volume_worker: self._volume_worker.stop()

    def resume_workers(self):
        gc.unfreeze()
        if self._volume_worker: self._volume_worker.start()
        if self._local_brightness_worker: self._local_brightness_worker.start()
        if DDC_MANAGER: DDC_MANAGER.start()
        if self._sunset_controller: self._sunset_controller.start()

    @override
    def do_startup(self):
        Gtk.Application.do_startup(self)
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda *_: self.quit() or GLib.SOURCE_REMOVE)
        self.hold()

        config_data = load_or_create_config()

        if DDC_MANAGER: DDC_MANAGER.start()

        self.pool = RefreshPool(max_workers=4)
        self._volume_worker = LatestValueWorker("volume", apply_volume) if HAS_VOLUME else None
        self._local_brightness_worker = LatestValueWorker("local-brightness", apply_local_brightness) if HAS_LOCAL_BRIGHTNESS else None
        self._sunset_controller = HyprsunsetController() if HAS_SUNSET else None

        settings = Gtk.Settings.get_default()
        if settings: settings.set_property("gtk-application-prefer-dark-theme", True)
        
        # Inject custom compact switch styling on top of the imported CSS
        provider = Gtk.CssProvider()
        extended_css = CSS + """
        switch.compact-switch { min-width: 38px; min-height: 20px; }
        switch.compact-switch slider { min-width: 18px; min-height: 18px; }
        """
        provider.load_from_data(extended_css.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.window = QuickPanalWindow(self, self.pool, config_data,
            volume_submit=self.submit_volume if HAS_VOLUME else None,
            brightness_submit=self._submit_brightness if HAS_BRIGHTNESS else None,
            sunset_submit=self.submit_sunset if HAS_SUNSET else None
        )
        self.suspend_workers()
        _reclaim_idle_memory()

    @override
    def do_activate(self):
        if self.window: self.window.show_all(); self.window.present()

    @override
    def do_shutdown(self):
        if self.window and self.window._timer_id is not None:
            GLib.source_remove(self.window._timer_id); self.window._timer_id = None
        self.suspend_workers()
        Gtk.Application.do_shutdown(self)

if __name__ == "__main__":
    app = QuickPanalApp()
    try: sys.exit(app.run(sys.argv))
    except KeyboardInterrupt: sys.exit(0)
