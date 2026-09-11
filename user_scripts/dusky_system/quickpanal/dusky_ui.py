#!/usr/bin/env python3
"""
Dusky UI - Custom GTK3 Widgets, Compaction Layouts, Defensive Rendering & CSS Specifications
Target Specification: Arch Linux (Kernel 7.1+ / August 2026 Spec), Python 3.14.6+
Pure bleeding-edge implementation with zero legacy shims or backwards compatibility shims.
"""
from __future__ import annotations
from datetime import datetime
import json
import math
import os
import re
import shlex
import time
from concurrent.futures import Future, CancelledError
from pathlib import Path
from typing import Any, Callable, Final
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('Pango', '1.0')
from gi.repository import Gdk, Gio, GLib, Gtk, Pango
from dusky_backend import HOME, LOG, execute_cmd, run_command, snap_to_step, fetch_notifications, NotificationData, RefreshPool, NOTIF_CACHE_FILE, MAKO_BLACKLIST_FILE, atomic_write_json, is_dusky_notif_time_service_enabled

def _add_css_class(widget: Gtk.Widget, cls: str) -> None:
    widget.get_style_context().add_class(cls)

def _remove_css_class(widget: Gtk.Widget, cls: str) -> None:
    widget.get_style_context().remove_class(cls)

class QuickIconToggle(Gtk.Overlay):

    def __init__(self, icon_name: str, tooltip: str, on_left: str='', on_middle: str='', on_right: str='', on_execute: Any = None) -> None:
        super().__init__()
        self.on_execute = on_execute
        self.btn_box = Gtk.Button()
        self.btn_box.set_relief(Gtk.ReliefStyle.NONE)
        _add_css_class(self.btn_box, 'quick-icon-toggle')
        if tooltip:
            self.btn_box.set_tooltip_text(str(tooltip).replace('\\n', '\n'))
        self._icon = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.LARGE_TOOLBAR)
        self._icon.set_pixel_size(20)
        self._icon.set_halign(Gtk.Align.CENTER)
        self._icon.set_valign(Gtk.Align.CENTER)
        self.btn_box.add(self._icon)
        self.add(self.btn_box)
        self.badge_lbl = Gtk.Label()
        _add_css_class(self.badge_lbl, 'notification-badge')
        self.badge_lbl.set_halign(Gtk.Align.END)
        self.badge_lbl.set_valign(Gtk.Align.START)
        self.badge_lbl.set_xalign(0.5)
        self.badge_lbl.set_yalign(0.5)
        self.badge_lbl.set_visible(False)
        self.badge_lbl.set_no_show_all(True)
        self.add_overlay(self.badge_lbl)
        self.btn_box.connect('button-press-event', self._on_clicked)
        self.cmds: dict[int, str] = {1: on_left, 2: on_middle, 3: on_right}
        self.show_all()
        self.badge_lbl.hide()

    def _on_clicked(self, widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if (cmd := self.cmds.get(event.button)):
            if callable(self.on_execute):
                try:
                    self.on_execute(cmd, event.button)
                except TypeError:
                    self.on_execute(cmd)
            else:
                execute_cmd(cmd)
        return True

    def update_state(self, icon: str | None=None, css_class: str | None=None, tooltip: str | None=None, badge: str='') -> None:
        if icon:
            self._icon.set_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR)
            self._icon.set_pixel_size(20)
        if tooltip:
            self.btn_box.set_tooltip_text(str(tooltip).replace('\\n', '\n'))
        if css_class:
            for cls in ['normal', 'active', 'dnd-active', 'power-saver-active']:
                _remove_css_class(self.btn_box, cls)
            _add_css_class(self.btn_box, css_class)
        if badge and badge.strip() and (badge != '0'):
            self.badge_lbl.set_label(badge)
            self.badge_lbl.show()
        else:
            self.badge_lbl.hide()

class MetricPill(Gtk.EventBox):

    def __init__(self, icon: str | None, tooltip: str, on_click: str='', small_text: bool=False, on_execute: Any = None) -> None:
        super().__init__()
        self.on_execute = on_execute
        self._on_click_cmd = on_click
        if tooltip:
            self.set_tooltip_text(str(tooltip).replace('\\n', '\n'))
        self.set_hexpand(True)
        self.set_visible_window(False)
        if on_click:
            _add_css_class(self, 'clickable-pill')
            self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            self.connect('button-press-event', self._on_clicked)

        self._box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        _add_css_class(self._box, 'metric-pill')
        self._box.set_hexpand(True)
        self._inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._inner.set_halign(Gtk.Align.CENTER)
        self._inner.set_hexpand(True)
        if icon:
            self._icon = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.MENU)
            self._icon.set_pixel_size(16)
            self._inner.pack_start(self._icon, False, False, 0)
        self._val_lbl = Gtk.Label(label='--')
        _add_css_class(self._val_lbl, 'metric-value-small' if small_text else 'metric-value')
        self._val_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self._val_lbl.set_max_width_chars(16)
        self._val_lbl.set_width_chars(1)
        self._inner.pack_start(self._val_lbl, False, False, 0)
        self._box.pack_start(self._inner, True, True, 0)
        self.add(self._box)
        self.show_all()

    def _on_clicked(self, *args: Any) -> bool:
        if self._on_click_cmd:
            if callable(self.on_execute):
                self.on_execute(self._on_click_cmd)
            else:
                execute_cmd(self._on_click_cmd)
        return True

    def set_value(self, text: str) -> None:
        self._val_lbl.set_label(text)

    def apply_json(self, data: dict[str, Any] | None, hide_class: str='empty') -> None:
        if not data or data.get('class', '') == hide_class:
            self._val_lbl.set_label('--')
        else:
            text = str(data.get('text', '')).replace('\\n', ' ').replace('\n', ' ').strip()
            try:
                self._val_lbl.set_markup(text)
            except Exception:
                self._val_lbl.set_text(text)

class CompactSliderRow(Gtk.Box):

    def __init__(self, icon_text: str, css_class: str, min_value: float, max_value: float, step: float, fetch_cb: Callable[[], float | None], submit_cb: Callable[[float], None], refresh_pool: RefreshPool, *, post_submit_refresh_grace_seconds: float=0.0) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self._fetch_cb = fetch_cb
        self._submit_cb = submit_cb
        self._refresh_pool = refresh_pool
        self._refresh_future: Future[float | None] | None = None
        self._refresh_token = 0
        self._user_revision = 0
        self._suppress_apply = False
        self._has_value = False
        self._post_submit_refresh_grace_seconds = max(0.0, post_submit_refresh_grace_seconds)
        self._pending_local_value: float | None = None
        self._pending_local_deadline = 0.0
        _add_css_class(self, 'slider-row')
        self.icon = Gtk.Label(label=icon_text)
        _add_css_class(self.icon, 'icon-label')
        _add_css_class(self.icon, f'icon-{css_class}')
        self.pack_start(self.icon, False, False, 0)
        self.adjustment = Gtk.Adjustment(value=min_value, lower=min_value, upper=max_value, step_increment=step, page_increment=step * 10.0)
        self.scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.adjustment)
        self.scale.set_hexpand(True)
        self.scale.set_size_request(50, -1)
        self.scale.set_draw_value(False)
        self.scale.set_digits(0)
        self.scale.set_sensitive(False)
        _add_css_class(self.scale, 'pill-scale')
        _add_css_class(self.scale, css_class)
        self.scale.connect('value-changed', self._on_value_changed)
        self.scale.connect('scroll-event', self._on_scroll_event)
        self.pack_start(self.scale, True, True, 0)
        self.value_label = Gtk.Label(label='…')
        self.value_label.set_width_chars(3)
        self.value_label.set_xalign(1.0)
        _add_css_class(self.value_label, 'value-label')
        self.pack_start(self.value_label, False, False, 0)
        self.show_all()

    def _on_scroll_event(self, widget: Gtk.Widget, event: Gdk.EventScroll) -> bool:
        widget.stop_emission_by_name('scroll-event')
        return False

    def refresh_async(self) -> None:
        if self._pending_local_value is not None and time.monotonic() < self._pending_local_deadline:
            return
        if self._refresh_future is not None and (not self._refresh_future.done()):
            return
        self._refresh_token += 1
        token = self._refresh_token
        user_rev = self._user_revision
        future = self._refresh_pool.submit(self._fetch_cb)
        if future is None:
            return
        self._refresh_future = future
        future.add_done_callback(lambda f: self._refresh_done(f, token, user_rev))

    def _refresh_done(self, future: Future[float | None], token: int, user_revision: int) -> None:
        try:
            value = future.result()
        except CancelledError:
            return
        except Exception:
            value = None
        GLib.idle_add(self._apply_refresh_result, token, user_revision, value)

    def _apply_refresh_result(self, token: int, user_revision: int, value: float | None) -> bool:
        if token == self._refresh_token:
            self._refresh_future = None
        if token != self._refresh_token or user_revision != self._user_revision:
            return GLib.SOURCE_REMOVE
        if value is None:
            self.set_no_show_all(True)
            self.set_visible(False)
            self.hide()
            self._pending_local_value = None
            return GLib.SOURCE_REMOVE
        self.set_no_show_all(False)
        self.set_visible(True)
        self.show_all()
        clamped = snap_to_step(value, self.adjustment.get_lower(), self.adjustment.get_upper(), self.adjustment.get_step_increment())
        if self._pending_local_value is not None:
            if math.isclose(clamped, self._pending_local_value, rel_tol=0.0, abs_tol=max(self.adjustment.get_step_increment() * 0.5, 1e-09)):
                self._pending_local_value = None
            elif time.monotonic() < self._pending_local_deadline:
                return GLib.SOURCE_REMOVE
            else:
                self._pending_local_value = None
        self._suppress_apply = True
        try:
            self.adjustment.set_value(clamped)
            self.value_label.set_label(str(int(round(clamped))))
            self.scale.set_sensitive(True)
            self._has_value = True
        finally:
            self._suppress_apply = False
        return GLib.SOURCE_REMOVE

    def _on_value_changed(self, scale: Gtk.Scale) -> None:
        value = scale.get_value()
        snapped = snap_to_step(value, self.adjustment.get_lower(), self.adjustment.get_upper(), self.adjustment.get_step_increment())
        if not math.isclose(snapped, value, rel_tol=0.0, abs_tol=1e-09):
            self._suppress_apply = True
            try:
                self.adjustment.set_value(snapped)
            finally:
                self._suppress_apply = False
        self.value_label.set_label(str(int(round(snapped))))
        if self._suppress_apply:
            return
        if self._post_submit_refresh_grace_seconds > 0.0:
            self._pending_local_value = snapped
            self._pending_local_deadline = time.monotonic() + self._post_submit_refresh_grace_seconds
        else:
            self._pending_local_value = None
        self._user_revision += 1
        self._submit_cb(snapped)

class NotificationRow(Gtk.ListBoxRow):

    def __init__(self, notif: NotificationData, on_close: Callable[[NotificationRow], None], show_app_name: bool=True, time_str: str='') -> None:
        super().__init__()
        self.set_can_focus(False)
        self.notif = notif
        self.on_close = on_close
        _add_css_class(self, 'notif-row')
        main_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        if show_app_name and notif.app_name and (notif.app_name != 'notify-send'):
            app_lbl = Gtk.Label(label=notif.app_name.upper())
            app_lbl.set_halign(Gtk.Align.START)
            app_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            app_lbl.set_width_chars(1)
            _add_css_class(app_lbl, 'notif-app-name')
            text_box.pack_start(app_lbl, False, False, 0)
        sum_lbl = Gtk.Label()
        escaped_sum = GLib.markup_escape_text(notif.summary)
        try:
            sum_lbl.set_markup(f'<span>{escaped_sum}</span>')
        except Exception:
            sum_lbl.set_text(notif.summary)
        sum_lbl.set_halign(Gtk.Align.START)
        sum_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        sum_lbl.set_width_chars(1)
        sum_lbl.set_max_width_chars(30)
        _add_css_class(sum_lbl, 'notif-summary')
        text_box.pack_start(sum_lbl, False, False, 0)
        if notif.body:
            clean_body = re.sub('<[^>]+>', '', notif.body).replace('\n', ' ').strip()
            if clean_body:
                body_lbl = Gtk.Label()
                try:
                    escaped_body = GLib.markup_escape_text(clean_body)
                    body_lbl.set_markup(f'<span>{escaped_body}</span>')
                except Exception:
                    body_lbl.set_text(clean_body)
                body_lbl.set_halign(Gtk.Align.START)
                body_lbl.set_ellipsize(Pango.EllipsizeMode.END)
                body_lbl.set_width_chars(1)
                body_lbl.set_max_width_chars(36)
                _add_css_class(body_lbl, 'notif-body')
                text_box.pack_start(body_lbl, False, False, 0)
        main_box.pack_start(text_box, True, True, 0)
        right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        right_box.set_valign(Gtk.Align.START)
        if time_str:
            time_lbl = Gtk.Label(label=time_str)
            time_lbl.set_halign(Gtk.Align.END)
            _add_css_class(time_lbl, 'notif-time')
            right_box.pack_start(time_lbl, False, False, 0)
        close_btn = Gtk.Button()
        close_btn.set_image(Gtk.Image.new_from_icon_name('window-close-symbolic', Gtk.IconSize.MENU))
        _add_css_class(close_btn, 'notif-close-btn')
        close_btn.set_halign(Gtk.Align.END)
        close_btn.set_relief(Gtk.ReliefStyle.NONE)
        close_btn.set_can_focus(False)
        close_btn.connect('clicked', lambda _: self.on_close(self))
        right_box.pack_start(close_btn, False, False, 0)
        main_box.pack_end(right_box, False, False, 0)
        self.add(main_box)

class NotificationStackHeader(Gtk.ListBoxRow):

    def __init__(self, app_name: str, count: int, toggle_cb: Callable[[str, bool], None], on_close_stack: Callable[[str], None]) -> None:
        super().__init__()
        self.set_can_focus(False)
        self.app_name = app_name
        self.expanded = False
        self.toggle_cb = toggle_cb
        self.on_close_stack = on_close_stack
        _add_css_class(self, 'notif-stack-header')
        self.event_box = Gtk.EventBox()
        self.event_box.set_visible_window(False)
        self.event_box.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.event_box.connect('button-press-event', self._on_button_press)
        self.event_box.connect('realize', lambda w: (w.get_window() and w.get_window().set_cursor(Gdk.Cursor.new_from_name(w.get_display(), 'pointer'))))
        main_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        app_lbl = Gtk.Label(label=app_name.upper())
        app_lbl.set_halign(Gtk.Align.START)
        app_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        app_lbl.set_width_chars(1)
        _add_css_class(app_lbl, 'notif-stack-title')
        text_box.pack_start(app_lbl, False, False, 0)
        count_lbl = Gtk.Label(label=f'{count} notifications')
        count_lbl.set_halign(Gtk.Align.START)
        count_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        count_lbl.set_width_chars(1)
        _add_css_class(count_lbl, 'notif-body')
        text_box.pack_start(count_lbl, False, False, 0)
        main_box.pack_start(text_box, True, True, 0)
        right_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        right_box.set_valign(Gtk.Align.CENTER)
        self.close_btn = Gtk.Button()
        self.close_btn.set_image(Gtk.Image.new_from_icon_name('window-close-symbolic', Gtk.IconSize.MENU))
        _add_css_class(self.close_btn, 'notif-close-btn')
        self.close_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.close_btn.set_can_focus(False)
        self.close_btn.connect('clicked', lambda _: self.on_close_stack(self.app_name))
        right_box.pack_start(self.close_btn, False, False, 0)
        self.icon = Gtk.Image.new_from_icon_name('pan-end-symbolic', Gtk.IconSize.MENU)
        right_box.pack_start(self.icon, False, False, 0)
        main_box.pack_end(right_box, False, False, 0)
        self.event_box.add(main_box)
        self.add(self.event_box)

    def set_expanded(self, expanded: bool) -> None:
        self.expanded = expanded
        icon_name = 'pan-down-symbolic' if self.expanded else 'pan-end-symbolic'
        self.icon.set_from_icon_name(icon_name, Gtk.IconSize.MENU)

    def toggle(self) -> None:
        self.expanded = not self.expanded
        self.set_expanded(self.expanded)
        self.toggle_cb(self.app_name, self.expanded)

    def _on_button_press(self, widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button == 1:
            self.toggle()
            return True
        return False

class NotificationsPanel(Gtk.Box):

    def __init__(self, pool: RefreshPool) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._pool = pool
        self._refresh_token = 0
        self.expanded_apps: set[str] = set()
        self.notif_times: dict[str, str] = {}
        self._last_notifs: tuple[NotificationData, ...] | None = None
        _add_css_class(self, 'notifications-panel')
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label='Notifications')
        _add_css_class(title, 'notif-header-title')
        title.set_halign(Gtk.Align.START)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_width_chars(1)
        header.pack_start(title, True, True, 0)
        self.btn_dnd = Gtk.Button()
        self.btn_dnd.set_image(Gtk.Image.new_from_icon_name('notification-symbolic', Gtk.IconSize.BUTTON))
        self.btn_dnd.set_relief(Gtk.ReliefStyle.NONE)
        self.btn_dnd.set_tooltip_text('Toggle Do Not Disturb')
        _add_css_class(self.btn_dnd, 'flat-icon-btn')
        self.btn_dnd.connect('clicked', self._on_dnd_toggle)
        header.pack_start(self.btn_dnd, False, False, 0)
        btn_clear = Gtk.Button()
        btn_clear.set_image(Gtk.Image.new_from_icon_name('edit-clear-all-symbolic', Gtk.IconSize.BUTTON))
        btn_clear.set_relief(Gtk.ReliefStyle.NONE)
        btn_clear.set_tooltip_text('Clear All')
        _add_css_class(btn_clear, 'flat-icon-btn')
        btn_clear.connect('clicked', self._on_clear_all)
        header.pack_start(btn_clear, False, False, 0)
        self.pack_start(header, False, False, 0)
        self.listbox = Gtk.ListBox()
        self.listbox.set_can_focus(False)
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        _add_css_class(self.listbox, 'notif-list')
        self.listbox.connect('row-activated', self._on_row_activated)
        self.pack_start(self.listbox, True, True, 0)
        initial_notifs: list[NotificationData] = []
        try:
            initial_notifs = fetch_notifications()
        except Exception as e:
            LOG.error('Failed executing initial notification collection: %s', e)
        if not initial_notifs:
            self._last_notifs = ()
            self.set_no_show_all(True)
            self.hide()
        else:
            self._last_notifs = tuple(initial_notifs)
            self.set_no_show_all(False)
            self._render_notifs_list(initial_notifs)

    def _render_notifs_list(self, notifs: list[NotificationData]) -> None:
        self.notif_times = {}
        if is_dusky_notif_time_service_enabled():
            cache_file = NOTIF_CACHE_FILE
            if cache_file.is_file():
                try:
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        cached = json.load(f)
                        if isinstance(cached, dict):
                            self.notif_times = {str(k): str(v) for k, v in cached.items()}
                except Exception:
                    pass
        if is_dusky_notif_time_service_enabled():
            now_str = datetime.now().strftime('%I:%M %p').lstrip('0')
            changed = False
            for n in notifs:
                str_id = str(n.id)
                if str_id not in self.notif_times:
                    self.notif_times[str_id] = now_str
                    changed = True
            if len(self.notif_times) > 1000:
                excess = len(self.notif_times) - 1000
                keys_to_remove = list(self.notif_times.keys())[:excess]
                for k in keys_to_remove:
                    del self.notif_times[k]
                changed = True
            if changed:
                atomic_write_json(NOTIF_CACHE_FILE, self.notif_times)
        groups: dict[str, list[NotificationData]] = {}
        for n in notifs[:50]:
            app = n.app_name if n.app_name else 'Unknown'
            if app not in groups:
                groups[app] = []
            groups[app].append(n)
        for app, group_notifs in groups.items():
            if len(group_notifs) > 1:
                is_expanded = app in self.expanded_apps
                header = NotificationStackHeader(app, len(group_notifs), self._on_stack_toggled, self._on_stack_closed)
                header.set_expanded(is_expanded)
                self.listbox.add(header)
                for n in group_notifs:
                    row = NotificationRow(n, self._on_row_closed, show_app_name=False, time_str=self.notif_times.get(str(n.id), ''))
                    if not is_expanded:
                        row.set_no_show_all(True)
                        row.hide()
                    self.listbox.add(row)
            else:
                self.listbox.add(NotificationRow(group_notifs[0], self._on_row_closed, show_app_name=True, time_str=self.notif_times.get(str(group_notifs[0].id), '')))

    def _clear_listbox_safely(self) -> None:
        """
        August 2026 Memory Safe Cleanup:
        Proactively call destroy() on children instead of just letting the GC collect GObjects,
        preventing native PyGObject reference memory leaks in the resident daemon.
        """
        children = self.listbox.get_children()
        for child in children:
            self.listbox.remove(child)
            child.destroy()

    def _request_layout_update(self) -> None:
        self.listbox.invalidate_headers()
        self.listbox.queue_resize()
        win = self.get_toplevel()
        if win and isinstance(win, Gtk.Window):
            def _idle_resize_reposition() -> bool:
                if win.get_visible():
                    win.resize(320, 1)
                    if hasattr(win, 'request_reposition'):
                        win.request_reposition()
                return GLib.SOURCE_REMOVE
            GLib.idle_add(_idle_resize_reposition)

    def _on_stack_toggled(self, app_name: str, expanded: bool) -> None:
        if expanded:
            self.expanded_apps.add(app_name)
        else:
            self.expanded_apps.discard(app_name)
        for child in self.listbox.get_children():
            if isinstance(child, NotificationRow):
                n_app = child.notif.app_name if child.notif.app_name else 'Unknown'
                if n_app == app_name:
                    if expanded:
                        child.set_no_show_all(False)
                        child.show_all()
                    else:
                        child.set_no_show_all(True)
                        child.hide()
        self._request_layout_update()

    def refresh_async(self) -> None:
        self._refresh_token += 1
        token = self._refresh_token
        f = self._pool.submit(fetch_notifications)
        if f:
            f.add_done_callback(lambda fut: self._on_refresh_done(fut, token))
        self._pool.submit(self._fetch_dnd_state)

    def _fetch_dnd_state(self) -> None:
        r = run_command(['makoctl', 'mode'], timeout=0.5, capture_stdout=True)
        is_dnd = r is not None and r.returncode == 0 and ('do-not-disturb' in r.stdout)
        GLib.idle_add(self._apply_dnd_state, is_dnd)

    def _apply_dnd_state(self, is_dnd: bool) -> None:
        if is_dnd:
            self.btn_dnd.set_image(Gtk.Image.new_from_icon_name('notifications-disabled-symbolic', Gtk.IconSize.BUTTON))
            _add_css_class(self.btn_dnd, 'dnd-active-btn')
        else:
            self.btn_dnd.set_image(Gtk.Image.new_from_icon_name('notification-symbolic', Gtk.IconSize.BUTTON))
            _remove_css_class(self.btn_dnd, 'dnd-active-btn')

    def _on_refresh_done(self, fut: Future[list[NotificationData]], token: int) -> None:
        try:
            notifs = fut.result()
        except CancelledError:
            return
        except Exception as e:
            LOG.error('Failed fetching active notification buffer: %s', e)
            notifs = []
        GLib.idle_add(self._apply_notifs, notifs, token)

    def _apply_notifs(self, notifs: list[NotificationData], token: int) -> bool:
        if token != self._refresh_token:
            return GLib.SOURCE_REMOVE
        notifs_tuple = tuple(notifs)
        if notifs_tuple == self._last_notifs:
            return GLib.SOURCE_REMOVE
        self._last_notifs = notifs_tuple
        self._clear_listbox_safely()
        if not notifs:
            self.set_no_show_all(True)
            self.hide()
        else:
            self.set_no_show_all(False)
            self._render_notifs_list(notifs)
            self.show_all()
        self._request_layout_update()
        return GLib.SOURCE_REMOVE

    def _on_row_closed(self, row: NotificationRow) -> None:
        n = row.notif
        bl_path = MAKO_BLACKLIST_FILE
        try:
            with open(bl_path, 'a', encoding='utf-8') as f:
                f.write(f'{n.id}\n')
        except Exception:
            pass
        execute_cmd(f'makoctl dismiss -n {n.id}')
        self.listbox.remove(row)
        row.destroy()
        self._last_notifs = None
        if not self.listbox.get_children():
            self.set_no_show_all(True)
            self.hide()
        else:
            self.refresh_async()
        self._request_layout_update()

    def _on_stack_closed(self, app_name: str) -> None:
        bl_path = MAKO_BLACKLIST_FILE
        for child in self.listbox.get_children():
            if isinstance(child, NotificationRow):
                n = child.notif
                n_app = n.app_name if n.app_name else 'Unknown'
                if n_app == app_name:
                    try:
                        with open(bl_path, 'a', encoding='utf-8') as f:
                            f.write(f'{n.id}\n')
                    except Exception:
                        pass
                    execute_cmd(f'makoctl dismiss -n {n.id}')
                    self.listbox.remove(child)
                    child.destroy()
            elif isinstance(child, NotificationStackHeader) and child.app_name == app_name:
                self.listbox.remove(child)
                child.destroy()
        self.expanded_apps.discard(app_name)
        self._last_notifs = None
        if not self.listbox.get_children():
            self.set_no_show_all(True)
            self.hide()
        else:
            self.refresh_async()
        self._request_layout_update()

    def _on_row_activated(self, listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        if isinstance(row, NotificationStackHeader):
            row.toggle()
            self._request_layout_update()
            return
        if not isinstance(row, NotificationRow):
            return
        n = row.notif
        bl_path = MAKO_BLACKLIST_FILE
        try:
            with open(bl_path, 'a', encoding='utf-8') as f:
                f.write(f'{n.id}\n')
        except Exception:
            pass
        if n.source == 'active':
            execute_cmd(f'makoctl invoke -n {n.id} default')
        else:
            app = n.desktop_entry or n.app_name
            if app and app not in ('notify-send', 'mako'):
                execute_cmd(f'gtk-launch {shlex.quote(app)} || {shlex.quote(app)}')
        execute_cmd(f'makoctl dismiss -n {n.id}')
        self.listbox.remove(row)
        row.destroy()
        self._last_notifs = None
        if not self.listbox.get_children():
            self.set_no_show_all(True)
            self.hide()
        else:
            self.refresh_async()
        self._request_layout_update()

    def _on_dnd_toggle(self, _btn: Gtk.Button) -> None:
        execute_cmd("makoctl mode | grep -qw 'do-not-disturb' && makoctl mode -r do-not-disturb || makoctl mode -a do-not-disturb")
        GLib.timeout_add(150, lambda: (self.refresh_async(), GLib.SOURCE_REMOVE)[1])

    def _on_clear_all(self, _btn: Gtk.Button) -> None:
        bl_path = MAKO_BLACKLIST_FILE
        try:
            bl_path.unlink(missing_ok=True)
        except OSError:
            pass
        cmd = 'if systemctl --user is-active --quiet mako.service; then systemctl --user restart mako.service; else pkill -x mako && dusky-run -- mako & fi'
        execute_cmd(cmd)
        self.expanded_apps.clear()
        self._last_notifs = ()
        self._clear_listbox_safely()
        self.set_no_show_all(True)
        self.hide()
        self.refresh_async()
        self._request_layout_update()
CSS: Final[str] = """
window.panel-window {
    background-color: alpha(@theme_bg_color, 0.95);
    border: 1px solid rgba(255, 255, 255, 0.05);
    border-radius: 10px;
    box-shadow: 0 12px 36px rgba(0, 0, 0, 0.6);
}

/* Hides native GTK3 scrollbar visually without breaking wheel scroll functions */
scrolledwindow { background: transparent; }
scrollbar, scrollbar trough, scrollbar slider, scrollbar button {
    min-width: 0px; min-height: 0px; padding: 0px; margin: 0px;
    background: transparent; background-color: transparent;
    border: none; box-shadow: none; opacity: 0; color: transparent;
}

/* THEME THE OVERSCROLL "RUBBER BAND" GLOW */
scrolledwindow overshoot.top {
    background-image: radial-gradient(farthest-side at top, alpha(@theme_selected_bg_color, 0.2), transparent);
}
scrolledwindow overshoot.bottom {
    background-image: radial-gradient(farthest-side at bottom, alpha(@theme_selected_bg_color, 0.2), transparent);
}

/* REMOVE THE STATIC UNDERSHOOT "DASHED LINE" IF PRESENT */
scrolledwindow undershoot.top,
scrolledwindow undershoot.bottom {
    background-image: none;
    background-color: transparent;
}

* { outline: none; }
*:focus { outline: none; box-shadow: none; }
button { transition: background-color 200ms ease, opacity 200ms ease, box-shadow 200ms ease; }

.header-time { font-size: 38px; font-weight: 800; letter-spacing: -2px; color: @theme_fg_color; }
.header-date { font-size: 12px; font-weight: 600; color: @theme_selected_bg_color; }

box.weather-pill { padding: 4px 4px; }
.weather-text { font-size: 12px; font-weight: 700; color: alpha(@theme_fg_color, 0.9); }

button.power-header-btn,
button.power-header-btn:hover,
button.power-header-btn:active,
button.power-header-btn:checked,
button.power-header-btn:focus,
button.power-header-btn:backdrop {
    min-width: 36px;
    min-height: 36px;
    padding: 0;
    margin: 0;
    border-radius: 9999px;
    -gtk-outline-radius: 9999px;
    background-image: none;
    box-shadow: none;
    -gtk-icon-effect: none;
    outline: none;
}
button.power-header-btn {
    background-color: alpha(#ff453a, 0.6);
    color: white;
    border: 1px solid rgba(255, 255, 255, 0.05);
}
button.power-header-btn:hover {
    background-color: #ff453a;
    color: white;
}

flowbox, flowboxchild, flowbox:focus, flowboxchild:focus {
    background: transparent;
    background-color: transparent;
    border: none;
    outline: none;
    box-shadow: none;
}

button.quick-icon-toggle,
button.quick-icon-toggle:hover,
button.quick-icon-toggle:active,
button.quick-icon-toggle:checked,
button.quick-icon-toggle:focus,
button.quick-icon-toggle:backdrop {
    min-width: 44px;
    min-height: 44px;
    padding: 0;
    margin: 0;
    border-radius: 9999px;
    -gtk-outline-radius: 9999px;
    background-image: none;
    box-shadow: none;
    -gtk-icon-effect: none;
    outline: none;
}
button.quick-icon-toggle {
    background-color: rgba(255, 255, 255, 0.06);
    border: 1px solid rgba(255, 255, 255, 0.05);
    transition: background-color 0.2s cubic-bezier(0.25, 0.46, 0.45, 0.94),
                border-color 0.2s cubic-bezier(0.25, 0.46, 0.45, 0.94);
}
button.quick-icon-toggle:hover { background-color: rgba(255, 255, 255, 0.12); }
button.quick-icon-toggle.active { background-color: alpha(@theme_selected_bg_color, 0.3); border: 1px solid alpha(@theme_selected_bg_color, 0.5); }
button.quick-icon-toggle.active:hover { background-color: alpha(@theme_selected_bg_color, 0.5); }
button.quick-icon-toggle.active image { color: @theme_selected_bg_color; }
button.quick-icon-toggle.power-saver-active { background-color: alpha(#a6e3a1, 0.3); border: 1px solid alpha(#a6e3a1, 0.5); }
button.quick-icon-toggle.power-saver-active image { color: #a6e3a1; }
button.quick-icon-toggle.dnd-active { background-color: alpha(#ff453a, 0.3); border: 1px solid alpha(#ff453a, 0.5); }
button.quick-icon-toggle.dnd-active image { color: #ff453a; }

.notification-badge {
    background-color: @theme_selected_bg_color; color: black; font-size: 8px; font-weight: 800; border-radius: 7px;
    min-width: 14px; min-height: 14px; padding: 0 2px; margin: 1px; border: 1px solid rgba(255, 255, 255, 0.2); box-shadow: 0 2px 4px rgba(0,0,0,0.5);
}

box.metric-pill { background-color: rgba(255, 255, 255, 0.06); border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 14px; padding: 8px 10px; transition: all 0.2s; }
eventbox.clickable-pill:hover box.metric-pill { background-color: rgba(255, 255, 255, 0.12); }
.metric-value, .metric-value-small { font-family: "JetBrainsMono Nerd Font", monospace; color: @theme_fg_color; font-weight: 700; }
.metric-value { font-size: 12px; } .metric-value-small { font-size: 10px; letter-spacing: -0.5px; }

.power-profile-row { background-color: rgba(255, 255, 255, 0.06); border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 14px; padding: 6px 10px; }
.power-label { font-size: 14px; font-weight: 600; color: @theme_fg_color; }
.accent-icon { color: @theme_selected_bg_color; }

button.power-ring-btn,
button.radio.power-ring-btn,
button.power-ring-btn:hover,
button.power-ring-btn:active,
button.power-ring-btn:checked,
button.power-ring-btn:not(:checked),
button.power-ring-btn:focus,
button.power-ring-btn:backdrop {
    min-width: 30px;
    min-height: 30px;
    padding: 0;
    margin: 0;
    border-radius: 9999px;
    -gtk-outline-radius: 9999px;
    background-image: none;
    -gtk-icon-effect: none;
    outline: none;
}
button.power-ring-btn {
    border: 2px solid transparent;
    background-color: transparent;
    color: alpha(@theme_fg_color, 0.7);
}
button.power-ring-btn:hover { background-color: rgba(255, 255, 255, 0.08); }
button.power-ring-btn:checked { background-color: alpha(@theme_selected_bg_color, 0.15); border-color: @theme_selected_bg_color; color: @theme_selected_bg_color; box-shadow: 0 0 8px alpha(@theme_selected_bg_color, 0.25); }
button.power-ring-btn.power-saver:checked { background-color: alpha(#a6e3a1, 0.15); border-color: #a6e3a1; color: #a6e3a1; box-shadow: 0 0 8px alpha(#a6e3a1, 0.25); }
button.power-ring-btn.balanced:checked { background-color: alpha(#89b4fa, 0.15); border-color: #89b4fa; color: #89b4fa; box-shadow: 0 0 8px alpha(#89b4fa, 0.25); }
button.power-ring-btn.performance:checked { background-color: alpha(#f38ba8, 0.15); border-color: #f38ba8; color: #f38ba8; box-shadow: 0 0 8px alpha(#f38ba8, 0.25); }

/* The applying sub-state override for when the script is actively running */
button.power-ring-btn.applying:checked {
    background-color: alpha(@theme_fg_color, 0.05); 
    border-color: alpha(@theme_fg_color, 0.3); 
    color: alpha(@theme_fg_color, 0.5); 
}

switch image,
switch image.on,
switch image.off,
switch image:first-child,
switch image:last-child {
    -gtk-icon-source: none;
    -gtk-icon-transform: scale(0);
    opacity: 0;
    min-width: 0px;
    min-height: 0px;
    margin: 0px;
    padding: 0px;
    color: transparent;
}

switch,
switch:checked,
switch:not(:checked),
switch:hover,
switch:hover:not(:checked),
switch:checked:hover,
switch:checked:hover:active,
switch:checked:active,
switch:checked:disabled,
switch:disabled,
switch:focus,
switch.compact-switch,
switch.compact-switch:checked,
switch.compact-switch:not(:checked),
switch.compact-switch:hover,
switch.compact-switch:active,
switch.compact-switch:disabled {
    color: transparent;
    font-size: 0px;
    text-shadow: none;
    -gtk-icon-source: none;
    -gtk-icon-shadow: none;
    background-image: none;
    outline: none;
    box-shadow: none;
}

switch label,
switch * {
    color: transparent;
    font-size: 0px;
    text-shadow: none;
    -gtk-icon-source: none;
    -gtk-icon-shadow: none;
    background-image: none;
    opacity: 0;
    min-width: 0px;
    min-height: 0px;
    margin: 0px;
    padding: 0px;
}

switch.compact-switch {
    min-width: 38px;
    min-height: 20px;
    border-radius: 999px;
    border: 1px solid rgba(255, 255, 255, 0.05);
    background-color: rgba(255, 255, 255, 0.12);
    box-shadow: none;
    outline: none;
    color: transparent;
}
switch.compact-switch:checked {
    background-color: @theme_selected_bg_color;
    border-color: @theme_selected_bg_color;
    box-shadow: none;
    color: transparent;
}
switch.compact-switch slider {
    min-width: 18px;
    min-height: 18px;
    border-radius: 9999px;
    background-color: #ffffff;
    border: none;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
    outline: none;
}
switch.compact-switch:checked slider {
    background-color: #ffffff;
}

.sliders-container { background-color: rgba(255, 255, 255, 0.06); border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 16px; padding: 6px; }
.slider-row { background-color: transparent; padding: 6px 8px; }

scale.pill-scale trough, scale.pill-scale highlight { min-height: 12px; border-radius: 6px; border: none; box-shadow: none; }
scale.pill-scale trough { background-color: rgba(255, 255, 255, 0.08); }
scale.pill-scale slider { min-width: 0px; min-height: 0px; margin: 0px; padding: 0px; background: transparent; background-color: transparent; border: none; box-shadow: none; opacity: 0; }
scale.volume highlight, scale.brightness highlight, scale.sunset highlight { background-color: @theme_selected_bg_color; }
.icon-volume, .icon-brightness, .icon-sunset { color: @theme_selected_bg_color; }
.icon-label { font-size: 18px; font-family: "Symbols Nerd Font", "JetBrainsMono Nerd Font", monospace; }
.value-label { font-size: 14px; font-weight: 700; opacity: 0.8; color: @theme_selected_bg_color; font-family: "JetBrainsMono Nerd Font", monospace; font-feature-settings: "tnum"; }

/* Notifications Section */
box.notifications-panel { background: transparent; border: none; padding: 8px 4px 0px 4px; }
button.flat-icon-btn { background: transparent; border: none; box-shadow: none; border-radius: 8px; padding: 6px; color: @theme_fg_color; }
button.flat-icon-btn:hover { background-color: rgba(255, 255, 255, 0.1); }
button.dnd-active-btn { color: #ff453a; background-color: alpha(#ff453a, 0.15); }
.notif-header-title { font-size: 14px; font-weight: bold; color: @theme_fg_color; }

list.notif-list,
list,
.notif-list {
    background: transparent;
    background-color: transparent;
    border: none;
    box-shadow: none;
    outline: none;
    padding: 0;
    margin: 0;
}

list.notif-list:focus,
list.notif-list:selected,
list.notif-list:backdrop,
list:focus,
list:selected,
list:backdrop,
.notif-list:focus,
.notif-list:selected,
.notif-list:backdrop {
    background: transparent;
    background-color: transparent;
    border: none;
    box-shadow: none;
    outline: none;
}

list.notif-list > row,
list > row,
row.notif-stack-header,
row.notif-row {
    outline: none;
    box-shadow: none;
}

list.notif-list > row:focus,
list.notif-list > row:selected,
list.notif-list > row:active,
list.notif-list > row:hover,
list > row:focus,
list > row:selected,
row.notif-stack-header:focus,
row.notif-stack-header:selected,
row.notif-stack-header:active,
row.notif-row:focus,
row.notif-row:selected,
row.notif-row:active {
    outline: none;
    box-shadow: none;
}

row.notif-stack-header {
    background-color: rgba(255, 255, 255, 0.05);
    border-radius: 8px;
    padding: 12px 14px;
    margin-bottom: 4px;
    border: 1px solid rgba(255, 255, 255, 0.02);
    outline: none;
    box-shadow: none;
}
row.notif-stack-header:hover {
    background-color: rgba(255, 255, 255, 0.08);
    border-color: rgba(255, 255, 255, 0.1);
}
row.notif-stack-header:selected,
row.notif-stack-header:focus,
row.notif-stack-header:active {
    background-color: rgba(255, 255, 255, 0.05);
    outline: none;
    box-shadow: none;
}
.notif-stack-title { font-weight: bold; font-size: 12px; color: @theme_selected_bg_color; letter-spacing: 0.5px; }

row.notif-row {
    background-color: rgba(255, 255, 255, 0.03);
    border-radius: 8px;
    margin-bottom: 4px;
    padding: 6px;
    border: 1px solid rgba(255, 255, 255, 0.02);
    outline: none;
    box-shadow: none;
}
row.notif-row:hover {
    background-color: rgba(255, 255, 255, 0.08);
    border-color: rgba(255, 255, 255, 0.1);
}
row.notif-row:selected,
row.notif-row:focus,
row.notif-row:active {
    background-color: rgba(255, 255, 255, 0.03);
    outline: none;
    box-shadow: none;
}

.notif-app-name { font-size: 9px; font-weight: bold; color: @theme_selected_bg_color; opacity: 0.9; letter-spacing: 0.5px; }
.notif-summary { font-size: 12px; font-weight: bold; color: @theme_fg_color; }
.notif-body { font-size: 11px; color: @theme_fg_color; opacity: 0.7; }
.notif-time { font-size: 10px; font-weight: bold; color: @theme_fg_color; opacity: 0.4; margin-top: 0px; margin-right: -2px; }

button.notif-close-btn { 
    background: transparent; border: none; box-shadow: none; border-radius: 4px; padding: 2px; 
    min-width: 16px; min-height: 16px; color: alpha(@theme_fg_color, 0.3); margin-right: -4px;
}
button.notif-close-btn:hover { background-color: alpha(#ff453a, 0.2); color: #ff453a; }

/* Bottom Fade Gradient */
.bottom-fade {
    background-image: linear-gradient(to bottom, transparent 0%, alpha(@theme_bg_color, 0.95) 100%);
    border-bottom-left-radius: 24px;
    border-bottom-right-radius: 24px;
}
"""