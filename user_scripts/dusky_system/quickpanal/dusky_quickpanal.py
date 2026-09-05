#!/usr/bin/env python3
"""
Dusky Quick Panal - Main Window & GApplication Orchestrator
Target Specification: Arch Linux (Kernel 7.1+ / August 2026 Spec), Python 3.14.6+
Pure bleeding-edge implementation with zero legacy shims or backwards compatibility shims.
"""
import sys
import os
if not os.environ.get('WAYLAND_DISPLAY') and (not os.environ.get('DISPLAY')):
    sys.stderr.write('dusky-quickpanal: error: WAYLAND_DISPLAY and DISPLAY are not set. Cannot run GUI application.\\n')
    sys.exit(5)
import json
import gc
import signal
import tomllib
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Final, override
sys.dont_write_bytecode = True
signal.signal(signal.SIGINT, signal.SIG_DFL)
try:
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('Gdk', '3.0')
    gi.require_version('Pango', '1.0')
    from gi.repository import Gdk, Gio, GLib, GLibUnix, Gtk, Pango
except (ImportError, ValueError) as exc:
    raise SystemExit(f'Failed to load GTK3 PyGObject libraries: {exc}') from exc
from dusky_backend import (
    APP_ID, HOME, execute_cmd, run_command, fetch_json_output, _reclaim_idle_memory,
    LatestValueWorker, RefreshPool, HyprsunsetController, LOG, start_thread, gi_object_c_pointer,
    HAS_VOLUME, HAS_BRIGHTNESS, HAS_LOCAL_BRIGHTNESS, HAS_SUNSET, DDC_MANAGER,
    get_volume, apply_volume, get_brightness, apply_local_brightness, 
    get_hyprsunset_state, is_hyprsunset_service_enabled, _RE_MAKO_BADGE, _RE_UPDATES_TOTAL,
    BRIGHTNESS_POST_SUBMIT_REFRESH_GRACE_SECONDS, SUNSET_STATE_WRITE_DEBOUNCE_SECONDS
)
WINDOW_CLASS: str = 'dusky_quickpanal.py'
try:
    GLib.set_prgname(WINDOW_CLASS)
except Exception:
    pass
from dusky_ui import CSS, _add_css_class, _remove_css_class, QuickIconToggle, MetricPill, CompactSliderRow, NotificationsPanel
try:
    import ctypes
    _grab_lib_path = os.path.expanduser('~/user_scripts/dusky_system/click_away_to_dismiss/libwaylandgrab.so')
    LIBGRAB = ctypes.CDLL(_grab_lib_path)
    CB_TYPE = ctypes.CFUNCTYPE(None)
    LIBGRAB.init_wayland_grab.argtypes = (ctypes.c_void_p, CB_TYPE)
    LIBGRAB.init_wayland_grab.restype = None
    LIBGRAB.destroy_wayland_grab.argtypes = ()
    LIBGRAB.destroy_wayland_grab.restype = None
except (OSError, AttributeError, ImportError):
    LIBGRAB = None
CONFIG_DIR: Final[Path] = Path(HOME) / '.config' / 'dusky' / 'quickpanal'
CONFIG_FILE: Final[Path] = CONFIG_DIR / 'config.toml'
DEFAULT_TOML_CONFIG: Final[str] = '[layout]\nshow_weather = true\nshow_metrics = true\nshow_quick_toggles = true\nshow_power_profiles = true\nshow_sliders = true\nshow_notifications = true\nshow_media = false\n\n[[toggles]]\nid = "wifi"\nicon = "network-wireless-symbolic"\nlabel = "Wi-Fi"\ntooltip = "Wi-Fi\\nLMB: Network Manager"\non_left = "foot --app-id=dusky_tui python ~/user_scripts/dusky_tui/python/main/main.py ~/user_scripts/network_manager/tui_dusky_network.py"\n\n[[toggles]]\nid = "idle"\nicon = "timer-symbolic"\nlabel = "Hypridle"\ntooltip = "Hypridle\\nLMB: Toggle | RMB: Lock Screen"\non_left = "~/user_scripts/waybar/toggle_hypridle.sh"\non_right = "~/user_scripts/hyprlock/lock.sh"\n\n[[toggles]]\nid = "blur"\nicon = "preferences-desktop-appearance-symbolic"\nlabel = "Visuals"\ntooltip = "Visuals\\nLMB: Toggle Blur/Shadow"\non_left = "~/user_scripts/hypr/hypr_blur_opacity_shadow_toggle.sh toggle"\n\n[[toggles]]\nid = "updates"\nicon = "folder-download-symbolic"\nlabel = "Updates"\ntooltip = "Updates\\nLMB: System Update | RMB: Dusky Update"\non_left = "dusky-run kitty --class system_update.sh --hold sh -c \'~/user_scripts/update_dusky/system_update.sh --all\'"\non_right = "dusky-run kitty --class update_dusky.py --hold sh -c \'~/user_scripts/update_dusky/python/update_dusky.py\'"\n\n[[toggles]]\nid = "audio"\nicon = "audio-input-microphone-symbolic"\nlabel = "Voice DSP"\ntooltip = "Voice DSP & Noise Cancellation\\nLMB: Open Studio | RMB: Toggle ON/OFF"\non_left = "python3 ~/user_scripts/audio/dusky_audio_studio/dusky_audio_studio.py"\non_right = "python3 ~/user_scripts/audio/dusky_audio_studio/dusky_audio_studio.py --toggle"\n'

def load_or_create_config() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(DEFAULT_TOML_CONFIG, encoding='utf-8')
        except OSError as e:
            LOG.error(f'Could not create default config directory/file: {e}')
            return tomllib.loads(DEFAULT_TOML_CONFIG)
    try:
        with CONFIG_FILE.open('rb') as f:
            return tomllib.load(f)
    except Exception as e:
        LOG.error(f'Error loading {CONFIG_FILE}: {e}')
        return tomllib.loads(DEFAULT_TOML_CONFIG)

def _get_active_monitor_scaled_height() -> float:
    try:
        r = run_command(['hyprctl', '-j', 'monitors'], timeout=0.8, capture_stdout=True)
        if r is not None and r.returncode == 0 and r.stdout:
            for m in json.loads(r.stdout):
                if m.get('focused'):
                    return float(m['height']) / float(m.get('scale', 1.0))
    except Exception:
        pass
    return 1080.0

def is_pointer_inside_window(win: Gtk.Widget) -> bool:
    try:
        gdk_win = win.get_window()
        if gdk_win is None:
            return False
        display = gdk_win.get_display()
        seat = display.get_default_seat()
        pointer = seat.get_pointer() if seat else None
        if pointer is None:
            return False
        _win, x, y, _mask = gdk_win.get_device_position(pointer)
        alloc = win.get_allocation()
        return 0 <= x <= alloc.width and 0 <= y <= alloc.height
    except Exception:
        return False

class QuickPanalWindow(Gtk.ApplicationWindow):

    def __init__(self, app: Gtk.Application, pool: RefreshPool, config: dict[str, Any], volume_submit: Any, brightness_submit: Any, sunset_submit: Any) -> None:
        super().__init__(application=app)
        self.app = app
        self.pool = pool
        self.config = config
        self.layout_cfg = self.config.get('layout', {})
        self._timer_id: int | None = None
        self._reposition_scheduled = False
        self._cpu_last = (0, 0)
        self._updating_power = False
        self._slider_rows: list[CompactSliderRow] = []
        self.dynamic_toggles: dict[str, QuickIconToggle] = {}
        self._grab_active = False
        self._wifi_pending = False
        self._bt_pending = False
        self._power_pending_revision = 0
        self._power_pending_profile: str | None = None
        self.set_default_size(320, -1)
        self.set_size_request(320, -1)
        self.set_resizable(False)
        self.set_decorated(False)
        _add_css_class(self, 'panel-window')
        self._ignore_grab_cleared_until: float = 0.0
        self.connect('delete-event', self._on_delete_event)
        self.connect('show', self._on_show)
        self.connect('hide', self._on_hide)
        self.connect('map', self._on_map)
        self.connect('unmap', self._on_unmap)
        self.connect('key-press-event', self._on_key_pressed)
        self.connect('size-allocate', self._on_size_allocate)
        self._grab_cb = CB_TYPE(self._on_grab_cleared) if LIBGRAB else None
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        main_box.set_margin_start(12)
        main_box.set_margin_end(12)
        main_box.set_margin_top(12)
        main_box.set_margin_bottom(12)
        self.scrolled_main = Gtk.ScrolledWindow()
        self.scrolled_main.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scrolled_main.set_overlay_scrolling(True)
        self.scrolled_main.add(main_box)
        self.scrolled_main.set_propagate_natural_width(False)
        self.scrolled_main.set_propagate_natural_height(True)
        max_h = _get_active_monitor_scaled_height() * 0.85
        self.scrolled_main.set_max_content_height(int(max_h))
        self.bottom_fade = Gtk.EventBox()
        self.bottom_fade.set_valign(Gtk.Align.END)
        self.bottom_fade.set_size_request(-1, 48)
        self.bottom_fade.set_no_show_all(True)
        self.bottom_fade.hide()
        _add_css_class(self.bottom_fade, 'bottom-fade')
        self.overlay = Gtk.Overlay()
        self.overlay.add(self.scrolled_main)
        self.overlay.add_overlay(self.bottom_fade)
        self.overlay.set_overlay_pass_through(self.bottom_fade, True)
        self.add(self.overlay)
        self.v_adj = self.scrolled_main.get_vadjustment()
        self.v_adj.connect('value-changed', self._update_fade_visibility)
        self.v_adj.connect('changed', self._update_fade_visibility)
        self.header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.weather_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        _add_css_class(self.weather_box, 'weather-pill')
        self.weather_icon = Gtk.Image.new_from_icon_name('weather-few-clouds-symbolic', Gtk.IconSize.MENU)
        self.weather_icon.set_pixel_size(16)
        self.weather_lbl = Gtk.Label()
        self.weather_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self.weather_lbl.set_width_chars(1)
        _add_css_class(self.weather_lbl, 'weather-text')
        self.weather_box.pack_start(self.weather_icon, False, False, 0)
        self.weather_box.pack_start(self.weather_lbl, False, False, 0)
        self.weather_box.set_no_show_all(True)
        self.weather_box.hide()
        self.power_btn = Gtk.Button()
        self.power_btn.set_image(Gtk.Image.new_from_icon_name('system-shutdown-symbolic', Gtk.IconSize.BUTTON))
        _add_css_class(self.power_btn, 'power-header-btn')
        self.power_btn.set_valign(Gtk.Align.CENTER)
        self.power_btn.set_halign(Gtk.Align.CENTER)
        self.power_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.power_btn.connect('clicked', lambda _: self.hide_and_execute(f'{HOME}/user_scripts/wlogout/wlogout_scale.sh'))
        self.clock_event_box = Gtk.EventBox()
        self.clock_event_box.set_visible_window(False)
        self.clock_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.lbl_time = Gtk.Label()
        _add_css_class(self.lbl_time, 'header-time')
        self.lbl_date = Gtk.Label()
        self.lbl_date.set_ellipsize(Pango.EllipsizeMode.END)
        self.lbl_date.set_width_chars(1)
        _add_css_class(self.lbl_date, 'header-date')
        self.clock_box.pack_start(self.lbl_time, False, False, 0)
        self.clock_box.pack_start(self.lbl_date, False, False, 0)
        self.clock_event_box.add(self.clock_box)
        self.clock_event_box.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.clock_event_box.connect('button-press-event', lambda *_: (self.hide_and_execute('gnome-clocks'), True)[1])
        if self.layout_cfg.get('show_weather', True):
            self.header_box.pack_start(self.weather_box, False, False, 0)
        self.header_box.pack_end(self.power_btn, False, False, 0)
        self.header_box.set_center_widget(self.clock_event_box)
        main_box.pack_start(self.header_box, False, False, 0)
        if self.layout_cfg.get('show_metrics', True):
            self.metrics_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            self.metrics_row.set_homogeneous(True)
            self.pill_net = MetricPill(None, 'Network Activity Speed', small_text=True, on_execute=self.hide_and_execute)
            self.pill_ram = MetricPill('drive-harddisk-symbolic', 'RAM Metrics\nLMB: Open zramctl', on_click='kitty --class zramctl --hold zramctl', on_execute=self.hide_and_execute)
            self.pill_cpu = MetricPill('cpu-symbolic', 'CPU Metrics\nLMB: Open btop', on_click='kitty --class btop btop', on_execute=self.hide_and_execute)
            self.metrics_row.pack_start(self.pill_net, True, True, 0)
            self.metrics_row.pack_start(self.pill_ram, True, True, 0)
            self.metrics_row.pack_start(self.pill_cpu, True, True, 0)
            main_box.pack_start(self.metrics_row, False, False, 0)
        if self.layout_cfg.get('show_quick_toggles', True):
            self.flow = Gtk.FlowBox()
            self.flow.set_can_focus(False)
            self.flow.set_selection_mode(Gtk.SelectionMode.NONE)
            self.flow.set_max_children_per_line(5)
            self.flow.set_min_children_per_line(5)
            self.flow.set_column_spacing(10)
            self.flow.set_row_spacing(10)
            for t_conf in self.config.get('toggles', []):
                tg = QuickIconToggle(icon_name=t_conf.get('icon', 'applications-system-symbolic'), tooltip=t_conf.get('tooltip', ''), on_left=t_conf.get('on_left', ''), on_middle=t_conf.get('on_middle', ''), on_right=t_conf.get('on_right', ''), on_execute=self.handle_toggle_execute)
                self.flow.add(tg)
                if (t_id := t_conf.get('id')):
                    self.dynamic_toggles[t_id] = tg
            main_box.pack_start(self.flow, False, False, 0)
        if self.layout_cfg.get('show_power_profiles', True):
            self.power_container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
            _add_css_class(self.power_container, 'power-profile-row')
            self.wifi_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            self.wifi_icon = Gtk.Image.new_from_icon_name('network-wireless-symbolic', Gtk.IconSize.BUTTON)
            _add_css_class(self.wifi_icon, 'accent-icon')
            self.wifi_box.pack_start(self.wifi_icon, False, False, 0)
            self.wifi_switch = Gtk.Switch()
            self.wifi_switch.set_valign(Gtk.Align.CENTER)
            self.wifi_switch.set_can_focus(False)
            _add_css_class(self.wifi_switch, 'compact-switch')
            self.wifi_switch.connect('state-set', self._on_wifi_state_set)
            self.wifi_box.pack_start(self.wifi_switch, False, False, 0)
            self.power_container.pack_start(self.wifi_box, False, False, 0)
            spacer_wbt = Gtk.Box()
            spacer_wbt.set_size_request(12, -1)
            self.power_container.pack_start(spacer_wbt, False, False, 0)
            self.bt_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            self.bt_icon = Gtk.Image.new_from_icon_name('bluetooth-active-symbolic', Gtk.IconSize.BUTTON)
            _add_css_class(self.bt_icon, 'accent-icon')
            self.bt_box.pack_start(self.bt_icon, False, False, 0)
            self.bt_switch = Gtk.Switch()
            self.bt_switch.set_valign(Gtk.Align.CENTER)
            self.bt_switch.set_can_focus(False)
            _add_css_class(self.bt_switch, 'compact-switch')
            self.bt_switch.connect('state-set', self._on_bt_state_set)
            self.bt_box.pack_start(self.bt_switch, False, False, 0)
            self.power_container.pack_start(self.bt_box, False, False, 0)
            expand_spacer = Gtk.Box()
            self.power_container.pack_start(expand_spacer, True, True, 0)
            sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
            sep.set_margin_start(4)
            sep.set_margin_end(6)
            sep.set_margin_top(4)
            sep.set_margin_bottom(4)
            self.power_container.pack_start(sep, False, False, 0)
            self.power_cmds = {
                'Balanced': f'{HOME}/user_scripts/battery/tlp/tlp_mode_toggle.sh balanced',
                'Performance': f'{HOME}/user_scripts/battery/tlp/tlp_mode_toggle.sh performance',
                'Power Saver': f'{HOME}/user_scripts/battery/tlp/tlp_mode_toggle.sh power-saver'
            }
            self.power_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            self.btn_save = Gtk.RadioButton()
            self.btn_save.set_mode(False)
            self.btn_save.set_image(Gtk.Image.new_from_icon_name('power-profile-power-saver-symbolic', Gtk.IconSize.BUTTON))
            _add_css_class(self.btn_save, 'power-ring-btn')
            _add_css_class(self.btn_save, 'power-saver')
            self.btn_bal = Gtk.RadioButton.new_from_widget(self.btn_save)
            self.btn_bal.set_mode(False)
            self.btn_bal.set_image(Gtk.Image.new_from_icon_name('power-profile-balanced-symbolic', Gtk.IconSize.BUTTON))
            _add_css_class(self.btn_bal, 'power-ring-btn')
            _add_css_class(self.btn_bal, 'balanced')
            self.btn_perf = Gtk.RadioButton.new_from_widget(self.btn_save)
            self.btn_perf.set_mode(False)
            self.btn_perf.set_image(Gtk.Image.new_from_icon_name('power-profile-performance-symbolic', Gtk.IconSize.BUTTON))
            _add_css_class(self.btn_perf, 'power-ring-btn')
            _add_css_class(self.btn_perf, 'performance')
            self.btn_save.connect('toggled', self._on_power_toggled, 'Power Saver')
            self.btn_bal.connect('toggled', self._on_power_toggled, 'Balanced')
            self.btn_perf.connect('toggled', self._on_power_toggled, 'Performance')
            for btn in (self.btn_save, self.btn_bal, self.btn_perf):
                self.power_box.pack_start(btn, False, False, 0)
            self.power_container.pack_end(self.power_box, False, False, 0)
            main_box.pack_start(self.power_container, False, False, 0)
        if self.layout_cfg.get('show_sliders', True):
            self.sliders_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            _add_css_class(self.sliders_box, 'sliders-container')
            if HAS_VOLUME:
                row = CompactSliderRow("󰕾", "volume", 0.0, 100.0, 1.0, get_volume, volume_submit, self.pool)
                self._slider_rows.append(row)
                self.sliders_box.pack_start(row, False, False, 0)
            if HAS_BRIGHTNESS:
                row = CompactSliderRow("󰃠", "brightness", 1.0, 100.0, 1.0, get_brightness, brightness_submit, self.pool, post_submit_refresh_grace_seconds=BRIGHTNESS_POST_SUBMIT_REFRESH_GRACE_SECONDS)
                self._slider_rows.append(row)
                self.sliders_box.pack_start(row, False, False, 0)
            if HAS_SUNSET:
                row = CompactSliderRow("󰡬", "sunset", 1000.0, 6000.0, 50.0, lambda: get_hyprsunset_state(getattr(self.app, "_sunset_controller", None)), sunset_submit, self.pool, post_submit_refresh_grace_seconds=BRIGHTNESS_POST_SUBMIT_REFRESH_GRACE_SECONDS)
                if not is_hyprsunset_service_enabled():
                    row.set_no_show_all(True)
                    row.set_visible(False)
                    row.hide()
                self._slider_rows.append(row)
                self.sliders_box.pack_start(row, False, False, 0)
            if self._slider_rows:
                main_box.pack_start(self.sliders_box, False, False, 0)
        if self.layout_cfg.get('show_notifications', True):
            self.notifications_module = NotificationsPanel(self.pool)
            main_box.pack_start(self.notifications_module, True, True, 0)

    def _update_fade_visibility(self, *args: Any) -> None:
        max_val = self.v_adj.get_upper() - self.v_adj.get_page_size()
        if max_val > 0.5 and self.v_adj.get_value() < max_val - 2.0:
            self.bottom_fade.show()
        else:
            self.bottom_fade.hide()

    def _update_ui_state(self) -> int:
        if not self.get_visible():
            return GLib.SOURCE_REMOVE
        now = datetime.now()
        self.lbl_time.set_label(now.strftime('%I:%M'))
        self.lbl_date.set_label(now.strftime('%A, %B %d'))
        if self.pool:
            self.pool.submit(self._fetch_weather)
            self.pool.submit(self._fetch_audio)
            self.pool.submit(self._fetch_mako)
            self.pool.submit(self._fetch_idle)
            self.pool.submit(self._fetch_blur)
            self.pool.submit(self._fetch_net_bt_state)
            self.pool.submit(self._fetch_power_profile)
            self.pool.submit(self._fetch_hardware_metrics)
            self.pool.submit(self._fetch_network)
            self.pool.submit(self._fetch_updates)
        for row in self._slider_rows:
            row.refresh_async()
        if hasattr(self, 'notifications_module'):
            self.notifications_module.refresh_async()
        return GLib.SOURCE_CONTINUE

    def _fetch_audio(self) -> None:
        if not self.dynamic_toggles.get('audio') or not self.get_visible():
            return
        pid_file = Path(HOME) / '.config' / 'dusky' / 'settings' / 'dusky_studio' / 'daemon.pid'
        if not pid_file.exists():
            pid_file = Path(HOME) / '.config' / 'dusky_audio_studio' / 'daemon.pid'
        is_active = False
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, 0)
                is_active = True
            except (ValueError, OSError):
                is_active = False
        GLib.idle_add(self._apply_audio, is_active)

    def _apply_audio(self, is_active: bool) -> None:
        tg = self.dynamic_toggles.get('audio')
        if not tg:
            return
        if is_active:
            tg.update_state(
                icon='audio-input-microphone-symbolic',
                css_class='active',
                tooltip='Voice DSP: ON (PipeWire RT)\nLMB: Open Studio | RMB: Turn OFF'
            )
        else:
            tg.update_state(
                icon='audio-input-microphone-muted-symbolic',
                css_class='normal',
                tooltip='Voice DSP: OFF (Hardware Bypass)\nLMB: Open Studio | RMB: Turn ON'
            )

    def _fetch_weather(self) -> None:
        if not self.layout_cfg.get('show_weather', True) or not self.get_visible():
            return
        try:
            weather_file = Path(HOME) / '.config' / 'dusky' / 'settings' / 'waybar_weather'
            if weather_file.exists():
                with open(weather_file, 'r', encoding='utf-8') as f:
                    raw = json.load(f)
                data = raw.get('payload') if isinstance(raw, dict) and raw.get('version') == 2 else raw
                if data and data.get('text'):
                    GLib.idle_add(self._apply_weather, data.get('text').strip())
                    return
            data = fetch_json_output(f'python3 {HOME}/user_scripts/waybar/weather.py')
            if data and data.get('text'):
                GLib.idle_add(self._apply_weather, data.get('text').strip())
            else:
                GLib.idle_add(self.weather_box.hide)
        except Exception:
            GLib.idle_add(self.weather_box.hide)

    def _apply_weather(self, text: str) -> None:
        self.weather_lbl.set_label(text)
        self.weather_icon.show()
        self.weather_lbl.show()
        self.weather_box.show()

    def _fetch_mako(self) -> None:
        if not self.dynamic_toggles.get('dnd') or not self.get_visible():
            return
        data = fetch_json_output(f'{HOME}/user_scripts/waybar/mako.sh --horizontal')
        if data:
            GLib.idle_add(self._apply_mako, data)

    def _apply_mako(self, data: dict[str, Any]) -> None:
        tg = self.dynamic_toggles.get('dnd')
        if not tg:
            return
        text = data.get('text', '')
        css = data.get('class', 'empty')
        badge_match = _RE_MAKO_BADGE.search(text)
        badge = badge_match.group(0) if badge_match else ''
        final_tt = data.get('tooltip', 'Notifications') + '\nLMB: Open | MMB: Clear | RMB: Toggle DND'
        if css in ('dnd', 'dnd-pending'):
            tg.update_state(icon='notifications-disabled-symbolic', css_class='dnd-active', tooltip=final_tt, badge=badge)
        else:
            tg.update_state(icon='notification-symbolic', css_class='normal', tooltip=final_tt, badge=badge)

    def _fetch_idle(self) -> None:
        if not self.dynamic_toggles.get('idle') or not self.get_visible():
            return
        r = run_command(['pgrep', '-x', 'hypridle'], timeout=0.8, capture_stdout=True)
        GLib.idle_add(self._apply_idle, r is not None and r.returncode == 0)

    def _apply_idle(self, is_active: bool) -> None:
        tg = self.dynamic_toggles.get('idle')
        if not tg:
            return
        if is_active:
            tg.update_state(icon='timer-symbolic', css_class='normal', tooltip='Idle Allowed (Timer Active)\nLMB: Toggle | RMB: Lock Screen')
        else:
            tg.update_state(icon='view-reveal-symbolic', css_class='active', tooltip='Idle Inhibited (Awake)\nLMB: Toggle | RMB: Lock Screen')

    def _fetch_blur(self) -> None:
        if not self.dynamic_toggles.get('blur') or not self.get_visible():
            return
        try:
            with open(f'{HOME}/.config/dusky/settings/opacity_blur', 'r', encoding='utf-8') as f:
                state = f.read().strip().lower()
            GLib.idle_add(self._apply_blur, state == 'true')
        except Exception:
            pass

    def _apply_blur(self, is_active: bool) -> None:
        tg = self.dynamic_toggles.get('blur')
        if not tg:
            return
        if is_active:
            tg.update_state(icon='applications-graphics-symbolic', css_class='active', tooltip='Visuals: Blur & Shadow ON\nLMB: Toggle')
        else:
            tg.update_state(icon='preferences-desktop-appearance-symbolic', css_class='normal', tooltip='Visuals: Performance Mode\nLMB: Toggle')

    @staticmethod
    def _is_bt_rfkill_blocked() -> bool:
        r = run_command(['rfkill', 'list', 'bluetooth'], timeout=0.5, capture_stdout=True)
        if r is not None and r.returncode == 0 and r.stdout:
            return 'Soft blocked: yes' in r.stdout
        return False

    def _fetch_net_bt_state(self) -> None:
        if not hasattr(self, 'wifi_switch') or not self.get_visible():
            return
        try:
            wifi_r = run_command(['busctl', 'get-property', 'org.freedesktop.NetworkManager', '/org/freedesktop/NetworkManager', 'org.freedesktop.NetworkManager', 'WirelessEnabled'], timeout=0.8, capture_stdout=True)
            wifi_on = wifi_r is not None and wifi_r.returncode == 0 and ('true' in wifi_r.stdout)
            bt_r = run_command(['busctl', 'get-property', 'org.bluez', '/org/bluez/hci0', 'org.bluez.Adapter1', 'Powered'], timeout=0.8, capture_stdout=True)
            bt_powered = bt_r is not None and bt_r.returncode == 0 and ('true' in bt_r.stdout)
            bt_rfkill_blocked = self._is_bt_rfkill_blocked()
            bt_on = bt_powered and (not bt_rfkill_blocked)
            GLib.idle_add(self._apply_net_bt_state, wifi_on, bt_on)
        except Exception:
            pass

    def _apply_net_bt_state(self, wifi_on: bool, bt_on: bool) -> None:
        wifi_icon = 'network-wireless-symbolic' if wifi_on else 'network-wireless-disconnected-symbolic'
        self.wifi_icon.set_from_icon_name(wifi_icon, Gtk.IconSize.BUTTON)
        bt_icon = 'bluetooth-active-symbolic' if bt_on else 'bluetooth-disabled-symbolic'
        self.bt_icon.set_from_icon_name(bt_icon, Gtk.IconSize.BUTTON)
        if self._wifi_pending:
            return
        if self.wifi_switch.get_active() != wifi_on:
            self.wifi_switch.set_active(wifi_on)
        if self._bt_pending:
            return
        if self.bt_switch.get_active() != bt_on:
            self.bt_switch.set_active(bt_on)

    def _on_wifi_state_set(self, switch: Gtk.Switch, state: bool) -> bool:
        val = 'true' if state else 'false'
        execute_cmd(f'busctl set-property org.freedesktop.NetworkManager /org/freedesktop/NetworkManager org.freedesktop.NetworkManager WirelessEnabled b {val}')
        icon = 'network-wireless-symbolic' if state else 'network-wireless-disconnected-symbolic'
        self.wifi_icon.set_from_icon_name(icon, Gtk.IconSize.BUTTON)
        self._wifi_pending = True
        GLib.timeout_add(800, self._clear_wifi_pending)
        return False

    def _clear_wifi_pending(self) -> bool:
        self._wifi_pending = False
        if self.get_visible() and self.pool:
            self.pool.submit(self._fetch_net_bt_state)
        return GLib.SOURCE_REMOVE

    def _on_bt_state_set(self, switch: Gtk.Switch, state: bool) -> bool:
        if state:
            execute_cmd("sudo -n /usr/bin/rfkill unblock bluetooth && busctl set-property org.bluez /org/bluez/hci0 org.bluez.Adapter1 Powered b true")
            icon = "bluetooth-active-symbolic"
        else:
            execute_cmd("busctl set-property org.bluez /org/bluez/hci0 org.bluez.Adapter1 Powered b false && sudo -n /usr/bin/rfkill block bluetooth")
            icon = "bluetooth-disabled-symbolic"
        self.bt_icon.set_from_icon_name(icon, Gtk.IconSize.BUTTON)
        self._bt_pending = True
        GLib.timeout_add(800, self._clear_bt_pending)
        return False

    def _clear_bt_pending(self) -> bool:
        self._bt_pending = False
        if self.get_visible() and self.pool:
            self.pool.submit(self._fetch_net_bt_state)
        return GLib.SOURCE_REMOVE

    def _fetch_power_profile(self) -> None:
        if not hasattr(self, 'power_container') or not self.get_visible():
            return
        if getattr(self, '_power_pending_profile', None) is not None:
            return
        try:
            pwr_file = Path('/run/tlp/last_pwr')
            if pwr_file.exists():
                parts = pwr_file.read_text(encoding='utf-8').strip().split()
                if parts:
                    pp_code = parts[0]
                    mapping = {'0': 'performance', '1': 'balanced', '2': 'power-saver'}
                    state = mapping.get(pp_code)
                    if state:
                        GLib.idle_add(self._apply_power_profile, state)
                        return
            path = Path(HOME) / '.config' / 'dusky' / 'settings' / 'tlp_state'
            if path.exists():
                state = path.read_text(encoding='utf-8').strip().lower()
                GLib.idle_add(self._apply_power_profile, state)
            else:
                LOG.warning(f'Power profile state file does not exist: {path}')
        except Exception as e:
            LOG.exception('Failed to fetch power profile: %s', e)

    def _apply_power_profile(self, profile: str) -> bool:
        if getattr(self, '_power_pending_profile', None) is not None:
            return GLib.SOURCE_REMOVE
        mapping = {'balanced': self.btn_bal, 'performance': self.btn_perf, 'power-saver': self.btn_save}
        target_btn = mapping.get(profile)
        if target_btn and (not target_btn.get_active()):
            LOG.info(f'Applying power profile: {profile}')
            self._updating_power = True
            target_btn.set_active(True)
            for btn in (self.btn_save, self.btn_bal, self.btn_perf):
                _remove_css_class(btn, 'applying')
            self._updating_power = False
        return GLib.SOURCE_REMOVE

    def _on_power_toggled(self, button: Gtk.RadioButton, profile_name: str) -> None:
        if not button.get_active() or self._updating_power:
            return
        if (cmd := self.power_cmds.get(profile_name)):
            profile_key = profile_name.lower().replace(' ', '-')
            self._power_pending_revision += 1
            current_rev = self._power_pending_revision
            self._power_pending_profile = profile_key
            for btn in (self.btn_save, self.btn_bal, self.btn_perf):
                _remove_css_class(btn, 'applying')
            _add_css_class(button, 'applying')
            start_thread('power-profile', self._run_power_cmd_worker, cmd, current_rev)

    def _run_power_cmd_worker(self, cmd: str, revision: int) -> None:
        try:
            run_command(['/usr/bin/bash', '-c', cmd], timeout=4.0)
        except Exception as e:
            LOG.error(f'Failed to apply power profile: {e}')
        GLib.idle_add(self._power_cmd_finished, revision)

    def _power_cmd_finished(self, revision: int) -> bool:
        if revision != self._power_pending_revision:
            return GLib.SOURCE_REMOVE
        self._power_pending_profile = None
        for btn in (self.btn_save, self.btn_bal, self.btn_perf):
            _remove_css_class(btn, 'applying')
        if self.get_visible() and self.pool:
            self.pool.submit(self._fetch_power_profile)
        return GLib.SOURCE_REMOVE

    def _fetch_hardware_metrics(self) -> None:
        if not hasattr(self, 'metrics_row') or not self.get_visible():
            return
        try:
            with open('/proc/stat', 'r', encoding='utf-8') as f:
                parts = [int(p) for p in f.readline().split()[1:]]
            idle = parts[3] + parts[4]
            total = sum(parts)
            last_idle, last_total = self._cpu_last
            d_idle, d_total = (idle - last_idle, total - last_total)
            cpu_usage = 100 * (1.0 - d_idle / d_total) if d_total > 0 else 0
            self._cpu_last = (idle, total)
            mem_tot = mem_av = 0
            with open('/proc/meminfo', 'r', encoding='utf-8') as f:
                for line in f:
                    if line.startswith('MemTotal:'):
                        mem_tot = int(line.split()[1])
                    elif line.startswith('MemAvailable:'):
                        mem_av = int(line.split()[1])
                    if mem_tot and mem_av:
                        break
            ram_used = (mem_tot - mem_av) / 1048576
            GLib.idle_add(self.pill_cpu.set_value, f'{cpu_usage:.0f}%')
            GLib.idle_add(self.pill_ram.set_value, f'{ram_used:.1f} GB')
        except Exception:
            pass

    def _fetch_network(self) -> None:
        if not hasattr(self, 'metrics_row') or not self.get_visible():
            return
        rt = Path(f'/run/user/{os.getuid()}/waybar-net')
        try:
            (rt / 'heartbeat').touch(exist_ok=True)
            pid_txt = (rt / 'daemon.pid').read_text(encoding='utf-8').strip()
            if pid_txt.isdigit():
                with open(f'/proc/{pid_txt}/cmdline', 'rb') as fh:
                    cmd = fh.read().replace(b'\0', b' ').decode('utf-8', 'ignore')
                if 'network_meter_daemon' in cmd:
                    os.kill(int(pid_txt), signal.SIGUSR1)
        except Exception:
            pass
        state_file = rt / 'state'
        if state_file.exists():
            try:
                for _ in range(5):
                    parts = state_file.read_text(encoding='utf-8').strip().split()
                    if len(parts) >= 4 and parts[0] in ('KB', 'MB', 'GB', '-'):
                        unit, up, down, cls = parts[0], parts[1], parts[2], parts[3]
                        txt = f"{up} {unit} {down}"
                        tt = "Disconnected" if cls == "network-disconnected" else f"Upload: {up} {unit}/s\nDownload: {down} {unit}/s"
                        GLib.idle_add(self.pill_net.apply_json, {"text": txt, "class": cls, "tooltip": tt}, 'network-disconnected')
                        return
            except Exception:
                pass
        data = fetch_json_output(f'{HOME}/user_scripts/waybar/network/network_meter_calling.sh --horizontal')
        GLib.idle_add(self.pill_net.apply_json, data, 'network-disconnected')

    def _fetch_updates(self) -> None:
        if not self.dynamic_toggles.get('updates') or not self.get_visible():
            return
        try:
            with open(f'{HOME}/.config/dusky/settings/waybar_update_counter_h', 'r', encoding='utf-8') as f:
                data = json.load(f)
            GLib.idle_add(self._apply_updates, data)
        except Exception:
            pass

    def _apply_updates(self, data: dict[str, Any]) -> None:
        tg = self.dynamic_toggles.get('updates')
        if not tg:
            return
        css = data.get('class', 'updated')
        final_tt = f"{data.get('tooltip', 'Updates')}\n\nLMB: System Update | RMB: Dusky Update"
        if css == 'pending':
            match = _RE_UPDATES_TOTAL.search(data.get('tooltip', ''))
            tg.update_state(icon='folder-download-symbolic', css_class='normal', tooltip=final_tt, badge=match.group(1) if match else '!')
        else:
            tg.update_state(icon='folder-download-symbolic', css_class='normal', tooltip=final_tt, badge='')

    def _on_map(self, *args: Any) -> None:
        if LIBGRAB and self.get_visible() and self._grab_cb and (not self._grab_active):
            self._grab_active = True
            ptr_val = hash(self)
            if ptr_val < 0:
                ptr_val += 1 << (ctypes.sizeof(ctypes.c_void_p) * 8)
            LIBGRAB.init_wayland_grab(ctypes.c_void_p(ptr_val), self._grab_cb)

    def _on_unmap(self, *args: Any) -> None:
        if LIBGRAB and self._grab_active:
            LIBGRAB.destroy_wayland_grab()
            self._grab_active = False

    def handle_toggle_execute(self, cmd: str, button: int = 1) -> None:
        if not cmd:
            return
        if button == 1:
            self.hide()
            execute_cmd(cmd)
        else:
            execute_cmd(cmd)
            def _single_shot_update() -> bool:
                if self.get_visible():
                    self._update_ui_state()
                return GLib.SOURCE_REMOVE
            GLib.timeout_add(150, _single_shot_update)

    def hide_and_execute(self, cmd: str) -> None:
        if not cmd:
            return
        self.hide()
        execute_cmd(cmd)

    def _on_grab_cleared(self) -> None:
        def safe_hide() -> bool:
            self.hide()
            return GLib.SOURCE_REMOVE
        GLib.idle_add(safe_hide)

    def _on_delete_event(self, _window: Gtk.Widget, _event: Gdk.Event) -> bool:
        self.hide()
        return True

    def _on_key_pressed(self, widget: Gtk.Widget, event: Gdk.EventKey) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self.hide()
            return True
        return False

    def request_reposition(self) -> None:
        if not self.get_visible():
            return
        self.resize(320, 1)
        if not self._reposition_scheduled:
            self._reposition_scheduled = True
            GLib.idle_add(self._do_reposition_idle)

    def _do_reposition_idle(self) -> bool:
        self._reposition_scheduled = False
        self._reposition_to_corner()
        return GLib.SOURCE_REMOVE

    def _on_size_allocate(self, widget: Gtk.Widget, allocation: Gdk.Rectangle) -> None:
        if self.get_visible() and (not self._reposition_scheduled):
            self._reposition_scheduled = True
            GLib.idle_add(self._do_reposition_idle)

    def _reposition_to_corner(self) -> None:
        """
        August 2026 Production-Grade Absolute Positioning Strategy:
        Calculates pixel-exact coordinate bounds and dispatches them via the
        canonical Hyprland movewindowpixel exact dispatcher.
        """
        if not self.get_visible():
            return
        try:
            r = run_command(['hyprctl', '-j', 'monitors'], timeout=0.8, capture_stdout=True)
            if r is None or r.returncode != 0 or (not r.stdout):
                return
            monitors = json.loads(r.stdout)
            mon = next((m for m in monitors if m.get('focused')), monitors[0])
            mon_x = float(mon.get('x', 0))
            mon_y = float(mon.get('y', 0))
            scale = float(mon.get('scale', 1.0))
            mon_w = float(mon['width']) / scale
            mon_h = float(mon['height']) / scale
            r = run_command(['hyprctl', '-j', 'clients'], timeout=0.8, capture_stdout=True)
            if r is None or r.returncode != 0 or (not r.stdout):
                return
            clients = json.loads(r.stdout)
            win = next((c for c in clients if c.get('class') == 'dusky_quickpanal.py'), None)
            if not win:
                return
            win_w = float(win['size'][0])
            win_h = float(win['size'][1])
            target_x = int(mon_x + mon_w - win_w - 20)
            target_y = int(mon_y + mon_h - win_h - 20)
            win_target = f"address:{win['address']}" if win.get('address') else 'class:dusky_quickpanal.py'
            run_command(['hyprctl', 'dispatch', f'hl.dsp.window.move({{ window = "{win_target}", x = {target_x}, y = {target_y} }})'], timeout=1.0, capture_stdout=True)
        except Exception as e:
            LOG.debug(f'Reposition dispatch error: {e}')

    def _on_show(self, *args: Any) -> None:
        app = self.get_application()
        if app and hasattr(app, 'resume_workers'):
            app.resume_workers()
        if self.pool:
            self.pool.resume()
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
        self._update_ui_state()
        self._timer_id = GLib.timeout_add(2000, self._update_ui_state)
        self.request_reposition()
        GLib.timeout_add(150, lambda: (self._reposition_to_corner() if self.get_visible() else None, GLib.SOURCE_REMOVE)[1])

    def _on_hide(self, *args: Any) -> None:
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None
        if self.pool:
            self.pool.suspend()
        app = self.get_application()
        if app and hasattr(app, 'suspend_workers'):
            app.suspend_workers()
        GLib.timeout_add(500, lambda: (self.get_visible() or _reclaim_idle_memory(), GLib.SOURCE_REMOVE)[1])

class QuickPanalApp(Gtk.Application):

    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.window: QuickPanalWindow | None = None
        self.pool: RefreshPool | None = None
        self._volume_worker: LatestValueWorker | None = None
        self._local_brightness_worker: LatestValueWorker | None = None
        self._sunset_controller: HyprsunsetController | None = None

    def submit_volume(self, value: float) -> None:
        if self._volume_worker:
            self._volume_worker.submit(value)

    def _submit_brightness(self, value: float) -> None:
        if self._local_brightness_worker:
            self._local_brightness_worker.submit(value)
        if DDC_MANAGER:
            DDC_MANAGER.submit(value)

    def submit_sunset(self, value: float) -> None:
        if self._sunset_controller:
            self._sunset_controller.submit(value)

    def suspend_workers(self) -> None:
        if self.pool:
            self.pool.suspend()
        if self._sunset_controller:
            self._sunset_controller.stop()
        if self._local_brightness_worker:
            self._local_brightness_worker.stop()
        if DDC_MANAGER:
            DDC_MANAGER.stop()
        if self._volume_worker:
            self._volume_worker.stop()

    def resume_workers(self) -> None:
        gc.unfreeze()
        if self.pool:
            self.pool.resume()
        if self._volume_worker:
            self._volume_worker.start()
        if self._local_brightness_worker:
            self._local_brightness_worker.start()
        if DDC_MANAGER:
            DDC_MANAGER.start()
        if self._sunset_controller:
            self._sunset_controller.start()

    @override
    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda *_: self.quit() or GLib.SOURCE_REMOVE)
        self.hold()
        config_data = load_or_create_config()
        if DDC_MANAGER:
            DDC_MANAGER.start()
        self.pool = RefreshPool(max_workers=4)
        self._volume_worker = LatestValueWorker('volume', apply_volume) if HAS_VOLUME else None
        self._local_brightness_worker = LatestValueWorker('local-brightness', apply_local_brightness) if HAS_LOCAL_BRIGHTNESS else None
        self._sunset_controller = HyprsunsetController() if HAS_SUNSET else None
        settings = Gtk.Settings.get_default()
        if settings:
            settings.set_property('gtk-application-prefer-dark-theme', True)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 10)
        self.window = QuickPanalWindow(self, self.pool, config_data, volume_submit=self.submit_volume if HAS_VOLUME else None, brightness_submit=self._submit_brightness if HAS_BRIGHTNESS else None, sunset_submit=self.submit_sunset if HAS_SUNSET else None)
        self.suspend_workers()
        _reclaim_idle_memory()

    @override
    def do_activate(self) -> None:
        if self.window:
            self.window.show_all()
            self.window.present()

    @override
    def do_shutdown(self) -> None:
        if self.window and self.window._timer_id is not None:
            GLib.source_remove(self.window._timer_id)
            self.window._timer_id = None
        self.suspend_workers()
        Gtk.Application.do_shutdown(self)
if __name__ == '__main__':
    app = QuickPanalApp()
    try:
        sys.exit(app.run(sys.argv))
    except KeyboardInterrupt:
        sys.exit(0)