#!/usr/bin/env python3
"""Click-through workspace indicator for Hyprland (Python 3.15+, Wayland).

Requires python-gobject, python-cairo, gtk3 and gtk-layer-shell.
Launch: python3 ~/user_scripts/waybar/overlay_dock/script.py
Right-click workspace cells to switch, or toggle special:magic (-9).
The clock, counters and space around the cells pass pointer events through.
"""
import argparse
import json
import math
import os
from pathlib import Path
import queue
import re
import signal
import socket
import sys
import threading
import time

# GUI imports are deferred so --help and invalid arguments need no display.
lazy import gi
lazy import cairo
lazy import subprocess
lazy from dataclasses import dataclass

PI = math.pi
SPECIAL_WS_ID = -99  # Internal cell key. Hyprland special workspaces have no ID.
SPECIAL_WS_NAME = 'special:magic'


def _opacity(value: str) -> float:
    alpha = float(value)
    if not math.isfinite(alpha) or not 0.0 <= alpha <= 1.0:
        raise argparse.ArgumentTypeError('opacity must be finite and between 0 and 1')
    return alpha


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--clk', action='store_true', help='hide the clock widget')
    parser.add_argument('--notific', action='store_true', help='hide the notification counter')
    parser.add_argument('--updt', action='store_true', help='hide the update counter')
    parser.add_argument('--bg-alpha', type=_opacity, default=0.9, metavar='FLOAT',
                        help='background pill opacity 0.0–1.0 (default: 0.9)')
    return parser.parse_args(argv)


# Parse before loading GTK, but importing the module must not consume argv.
ARGS = _parse_args() if __name__ == '__main__' else _parse_args([])
BG_ALPHA = ARGS.bg_alpha
os.environ['GDK_BACKEND'] = 'wayland'
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GtkLayerShell', '0.1')
gi.require_version('GLibUnix', '2.0')
from gi.repository import Gdk, Gio, GLib, GLibUnix, Gtk, GtkLayerShell


# ── Hyprland IPC ──────────────────────────────────────────────────────────────
def _find_socket(name: str) -> str:
    signature = os.environ['HYPRLAND_INSTANCE_SIGNATURE']
    runtime = os.environ.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}'
    return f'{runtime}/hypr/{signature}/.{name}.sock'


def hypr_request(command: str) -> bytes:
    """Bound the whole request, including a peer that sends without closing."""
    deadline = time.monotonic() + 3.0
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(3.0)
        sock.connect(_find_socket('socket'))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Hyprland IPC request timed out')
        sock.settimeout(remaining)
        sock.sendall(command.encode('utf-8'))
        chunks = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Hyprland IPC request timed out')
            sock.settimeout(remaining)
            chunk = sock.recv(8192)
            if not chunk:
                return b''.join(chunks)
            chunks.append(chunk)


def hypr_query(command: str):
    return json.loads(hypr_request(f'j/{command}'),
                      object_pairs_hook=frozendict, array_hook=tuple)


def hypr_dispatch(ws_id: int) -> None:
    expression = ('hl.dsp.workspace.toggle_special("magic")' if ws_id == SPECIAL_WS_ID
                  else f'hl.dsp.focus({{ workspace = "{ws_id}" }})')
    reply = hypr_request(f'dispatch {expression}').decode('utf-8')
    if reply.strip() != 'ok':
        raise RuntimeError(f'dispatch failed: {reply.strip()}')


# ── Palette ───────────────────────────────────────────────────────────────────
STYLE_EMPTY   = frozendict(bg=(1,1,1,0.03),  border=(1,1,1,0.05),  fg=(1,1,1,0.22))
STYLE_USED    = frozendict(bg=(1,1,1,0.07),  border=(1,1,1,0.12),  fg=(1,1,1,0.60))
STYLE_ACTIVE  = frozendict(bg=(0.388, 0.702, 0.929, 0.14),
                     border=(0.388, 0.702, 0.929, 0.52),
                     fg=(0.388, 0.702, 0.929, 1.0))
STYLE_URGENT  = frozendict(bg=(0.988, 0.506, 0.290, 0.12),
                     border=(0.988, 0.506, 0.290, 0.58),
                     fg=(0.988, 0.506, 0.290, 1.0))
STYLE_SPECIAL = frozendict(bg=(0.686, 0.459, 0.929, 0.12),
                     border=(0.686, 0.459, 0.929, 0.52),
                     fg=(0.686, 0.459, 0.929, 1.0))

HOVER_BG_BOOST = 0.08
WS_W, WS_H    = 30, 22

# Immutable handoff from the IPC worker to the GTK thread.
@dataclass(frozen=True, slots=True)
class WorkspaceState:
    windows: frozendict[int, int]
    active_id: int | None
    special_active: bool
    special_windows: int
    urgent_ids: frozenset[int]
    origin: tuple[int, int]


_LABEL_STYLE = Gtk.CssProvider()
_LABEL_STYLE.load_from_data(b"""
    label {
        font-family: "DSEG7 Classic", "Digital-7", monospace;
        font-size: 14px;
        letter-spacing: 2px;
    }
""")


def _style_label(label: Gtk.Label) -> None:
    label.get_style_context().add_provider(
        _LABEL_STYLE, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────
def _rrect(cr, x, y, w, h, r=2.0):
    cr.new_sub_path()
    cr.arc(x + r,     y + r,     r, PI,     3*PI/2)
    cr.arc(x + w - r, y + r,     r, 3*PI/2, 0)
    cr.arc(x + w - r, y + h - r, r, 0,      PI/2)
    cr.arc(x + r,     y + h - r, r, PI/2,   PI)
    cr.close_path()

# ── Workspace cell ────────────────────────────────────────────────────────────
class WsBox(Gtk.DrawingArea):
    def __init__(self, ws_id: int, dispatch):
        super().__init__()
        self._dispatch = dispatch
        self.ws_id       = ws_id
        self.active      = False
        self.windows     = 0
        self.urgent      = False
        self._hovered    = False
        self._is_special = (ws_id == SPECIAL_WS_ID)
        self.set_size_request(WS_W, WS_H)
        self.connect('draw', self._on_draw)
        self.set_events(
            Gdk.EventMask.BUTTON_PRESS_MASK |
            Gdk.EventMask.ENTER_NOTIFY_MASK |
            Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self.connect('button-press-event', self._on_click)
        self.connect('enter-notify-event', self._on_enter)
        self.connect('leave-notify-event', self._on_leave)
        self.connect('realize',            self._on_realize)

    def _on_realize(self, _w):
        self.get_window().set_cursor(
            Gdk.Cursor.new_from_name(self.get_display(), 'pointer')
        )

    def refresh(self, active: bool, windows: int, urgent: bool):
        changed = (self.active != active or
                   self.windows != windows or
                   self.urgent != urgent)
        self.active  = active
        self.windows = windows
        self.urgent  = urgent
        if changed:
            self.queue_draw()

    def _on_click(self, _w, event):
        if event.button == 3:
            self._dispatch(self.ws_id)
        return event.button == 3

    def _on_enter(self, *_):
        self._hovered = True
        self.queue_draw()

    def _on_leave(self, *_):
        self._hovered = False
        self.queue_draw()

    def _on_draw(self, _w, cr: cairo.Context):
        w, h, r = WS_W, WS_H, 5
        if self._is_special:
            style = STYLE_SPECIAL if self.active else STYLE_USED if self.windows else STYLE_EMPTY
        else:
            style = (STYLE_ACTIVE if self.active else
                     STYLE_URGENT if self.urgent else
                     STYLE_USED   if self.windows else
                     STYLE_EMPTY)
        bg = style['bg']
        if self._hovered and not self.active:
            bg = (bg[0], bg[1], bg[2], min(1.0, bg[3] + HOVER_BG_BOOST))
        _rrect(cr, 0, 0, w, h, r)
        cr.set_source_rgba(*bg)
        cr.fill_preserve()
        cr.set_source_rgba(*style['border'])
        cr.set_line_width(1.0)
        cr.stroke()
        if self.windows > 0 or self.active:
            self._draw_window_dots(cr, style['fg'], w, h)
        self._draw_label(cr, style['fg'])

    def _draw_label(self, cr, fg):
        cr.set_source_rgba(*fg)
        cr.select_font_face('DSEG7 Classic', cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(20)
        cr.move_to(-2, 21)
        cr.show_text('-9' if self._is_special else str(self.ws_id))

    def _draw_window_dots(self, cr, fg, w, h):
        r, g, b, a = fg
        cr.set_source_rgba(r, g, b, a * 0.65)
        pad = 4.0
        iw  = w - pad * 2
        ih  = h - pad * 2
        n   = min(max(self.windows, 1 if self.active else 0), 4)
        if n == 0:
            return
        elif n == 1:
            _rrect(cr, pad, pad, iw, ih)
            cr.fill()
        elif n == 2:
            gap  = 1.5
            rowh = (ih - gap) / 2
            _rrect(cr, pad, pad,          iw, rowh)
            cr.fill()
            _rrect(cr, pad, pad+rowh+gap, iw, rowh)
            cr.fill()
        elif n == 3:
            gap  = 1.5
            rowh = (ih - gap) / 2
            colw = (iw - gap) / 2
            _rrect(cr, pad,          pad,          iw,   rowh)
            cr.fill()
            _rrect(cr, pad,          pad+rowh+gap, colw, rowh)
            cr.fill()
            _rrect(cr, pad+colw+gap, pad+rowh+gap, colw, rowh)
            cr.fill()
        else:
            gap  = 1.5
            colw = (iw - gap) / 2
            rowh = (ih - gap) / 2
            for col in (pad, pad+colw+gap):
                for row in (pad, pad+rowh+gap):
                    _rrect(cr, col, row, colw, rowh)
                    cr.fill()

# ── Clock ─────────────────────────────────────────────────────────────────────
class Clock(Gtk.Label):
    def __init__(self):
        super().__init__()
        self._blink = True
        _style_label(self)
        self._tick()
        self._timer = GLib.timeout_add(1000, self._tick)
        self.connect('destroy', lambda *_: GLib.source_remove(self._timer))

    def _tick(self):
        now = time.strftime('%I:%M').lstrip('0')
        self.set_text(now if self._blink else now.replace(':', ' '))
        self._blink = not self._blink
        return True

# ── Counters (passive, display-only) ───────────────────────────────────────────
def _counter_text(data: dict | frozendict, *, updates: bool = False) -> str:
    if updates:
        counts = data.get('counts')
        if isinstance(counts, (dict, frozendict)) and counts:
            known = [value for value in counts.values() if isinstance(value, int)]
            total = sum(known)
            return str(total) if total or len(known) == len(counts) else 'n/a'
        if data.get('class') == 'unknown':
            return 'n/a'
        if match := re.search(r'Total:\s*(\d+)', data.get('tooltip', '')):
            return match[1]
    match = re.search(r'(\d+)\s*$', data.get('text', ''))
    return match[1] if match else '0'


class Notification(Gtk.Label):
    """Poll the shared notification renderer with one persistent worker."""
    _MAKO_SH = Path.home() / 'user_scripts/waybar/mako.sh'

    def __init__(self):
        super().__init__(label='…')
        _style_label(self)
        self._stop = threading.Event()
        self.connect('destroy', lambda *_: self._stop.set())
        self.worker = threading.Thread(target=self._poll_loop, daemon=True,
                                       name='overlay-notifications')
        self.worker.start()

    def _poll_loop(self):
        while not self._stop.is_set():
            try:
                output = subprocess.check_output(
                    [str(self._MAKO_SH)], stderr=subprocess.DEVNULL,
                    timeout=4, text=True, encoding='utf-8',
                )
                text = _counter_text(json.loads(output))
            except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError):
                text = 'n/a'
            if not self._stop.is_set():
                GLib.idle_add(self._set_count, text)
            self._stop.wait(5)

    def _set_count(self, text: str) -> bool:
        if not self._stop.is_set() and self.get_text() != text:
            self.set_text(text)
        return False


class Updates(Gtk.Label):
    """Watch the source file by name, including atomic replacement and creation."""
    _WAYBAR_PATH = Path.home() / '.config/dusky/settings/waybar_update_counter_h'

    def __init__(self):
        super().__init__(label='…')
        _style_label(self)
        self._monitor = None
        try:
            self._monitor = Gio.File.new_for_path(str(self._WAYBAR_PATH)).monitor_file(
                Gio.FileMonitorFlags.WATCH_MOVES, None,
            )
        except GLib.Error as error:
            print(f'[updates] file monitor unavailable: {error}', file=sys.stderr)
        if self._monitor is not None:
            self._monitor.connect('changed', self._changed)
        self._signature = None
        # Gio cannot watch a file whose parent directories do not exist yet.
        self._timer = GLib.timeout_add_seconds(5, self._read)
        self.connect('destroy', self._close)
        self._read()

    def _changed(self, *_):
        self._read()

    def _read(self):
        try:
            stat = self._WAYBAR_PATH.stat()
            signature = (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size)
            if signature == self._signature:
                return True
            # The writer publishes one complete JSON object by atomic rename.
            data = json.loads(self._WAYBAR_PATH.read_text(encoding='utf-8'))
            text = _counter_text(data, updates=True)
            self._signature = signature
        except (OSError, ValueError, TypeError, AttributeError):
            self._signature = None
            text = 'n/a'
        if self.get_text() != text:
            self.set_text(text)
        return True

    def _close(self, *_):
        if self._monitor is not None:
            self._monitor.cancel()
        GLib.source_remove(self._timer)


# ── Background pill ───────────────────────────────────────────────────────────
def _draw_pill(cr: cairo.Context, w: int, h: int, alpha: float):
    """
    Drawn onto the window's own Cairo surface before children are composited,
    so it is always visually behind widgets at any alpha value.
    """
    _rrect(cr, 0, 0, w, h, 10.0)
    cr.set_source_rgba(0.08, 0.08, 0.12, alpha)
    cr.fill_preserve()
    if alpha > 0.05:
        cr.set_source_rgba(1.0, 1.0, 1.0, alpha * 0.35)
        cr.set_line_width(0.8)
        cr.stroke()
    else:
        cr.new_path()

# ── Overlay window ────────────────────────────────────────────────────────────
class WorkspaceOverlay(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self._workspaces: frozendict[int, int] = frozendict()
        self._active_id: int | None = None
        self._urgent_ids: frozenset[int] = frozenset()
        self._ws_widgets: dict[int, WsBox] = {}
        self._special_active = False
        self._special_windows = 0
        self._stop = threading.Event()
        self._requests = queue.Queue()
        self._init_layer_shell()
        self._build_static_ui()
        self._threads = [
            threading.Thread(target=self._state_loop, daemon=True, name='overlay-state'),
            threading.Thread(target=self._event_loop, daemon=True, name='overlay-events'),
        ]
        self.connect('destroy', self._close)
        for thread in self._threads:
            thread.start()

    # ── Layer shell ────────────────────────────────────────────────────────────
    def _init_layer_shell(self):
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_app_paintable(True)
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_namespace(self, 'hypr-ws-overlay')
        GtkLayerShell.set_exclusive_zone(self, 0)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.BOTTOM, True)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.BOTTOM, 18)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
        self.connect('size-allocate', self._update_input_region)

    def _update_input_region(self, _widget, _alloc):
        """Only workspace boxes receive pointer events; the rest is click-through."""
        win = self.get_window()
        if not win:
            return
        region = cairo.Region()
        for child in self._ws_widgets.values():
            allocation = child.get_allocation()
            position = child.translate_coordinates(self, 0, 0)
            if position is not None and allocation.width > 0 and allocation.height > 0:
                region.union(cairo.RectangleInt(*position, allocation.width, allocation.height))
        win.input_shape_combine_region(region, 0, 0)

    # ── Static UI ──────────────────────────────────────────────────────────────
    def _build_static_ui(self):
        self.connect('draw', self._on_window_draw)

        self._row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        self._row.set_margin_top(7)
        self._row.set_margin_bottom(7)
        self._row.set_margin_start(10)
        self._row.set_margin_end(10)

        if not ARGS.clk:
            self._clock = Clock()
            self._row.pack_start(self._clock, False, False, 6)

        if not ARGS.updt:
            self._updates = Updates()
            self._row.pack_end(self._updates, False, False, 4)

        if not ARGS.notific:
            self._notif = Notification()
            self._row.pack_end(self._notif, False, False, 4)

        self.add(self._row)
        self.show_all()

    def _on_window_draw(self, _widget, cr: cairo.Context):
        alloc = self.get_allocation()
        _draw_pill(cr, alloc.width, alloc.height, BG_ALPHA)
        return False

    # ── State ownership: IPC in worker; widgets and snapshots on GTK thread ────
    def _request_dispatch(self, ws_id: int):
        self._requests.put(('dispatch', ws_id))

    def _state_loop(self):
        urgent_addresses: set[str] = set()
        retry = False
        while not self._stop.is_set():
            try:
                request = self._requests.get(timeout=2 if retry else None)
            except queue.Empty:
                request = ('refresh', '')
            batch = [request]
            while True:
                try:
                    batch.append(self._requests.get_nowait())
                except queue.Empty:
                    break
            try:
                refresh = retry
                for event, data in batch:
                    if event == 'stop' or self._stop.is_set():
                        return
                    if event == 'activewindowv2':
                        address = '0x' + data.removeprefix('0x')
                        if address in urgent_addresses:
                            urgent_addresses.discard(address)
                            refresh = True
                        continue  # Focus within a workspace needs no state query.
                    refresh = True
                    if event == 'dispatch':
                        try:
                            hypr_dispatch(data)
                        except (OSError, RuntimeError) as error:
                            print(f'[IPC dispatch] {error}', file=sys.stderr)
                    elif event == 'urgent':
                        urgent_addresses.add('0x' + data.removeprefix('0x'))
                    elif event == 'closewindow':
                        urgent_addresses.discard('0x' + data.removeprefix('0x'))
                if self._stop.is_set():
                    return
                if not refresh:
                    continue
                workspaces = hypr_query('workspaces')
                if self._stop.is_set():
                    return
                monitors = hypr_query('monitors')
                if self._stop.is_set():
                    return
                focused = next((monitor for monitor in monitors if monitor.get('focused')), None)
                if focused is None:
                    retry = True
                    continue
                urgent_ids = set()
                if urgent_addresses:
                    clients = hypr_query('clients')
                    urgent_addresses.intersection_update(client['address'] for client in clients)
                    for client in clients:
                        if client['address'] in urgent_addresses:
                            if client.get('focusHistoryID') == 0:
                                urgent_addresses.discard(client['address'])
                            elif (wid := client['workspace'].get('id')) is not None:
                                urgent_ids.add(wid)
                special = next((ws for ws in workspaces
                                if ws.get('name') == SPECIAL_WS_NAME), {})
                # Resolve every required field before touching GTK or publishing state.
                snapshot = WorkspaceState(
                    windows=frozendict((ws['id'], ws['windows']) for ws in workspaces
                                       if ws.get('type') == 'normal'),
                    active_id=focused['activeWorkspace'].get('id'),
                    special_active=focused.get('specialWorkspace', {}).get('name') == SPECIAL_WS_NAME,
                    special_windows=special.get('windows', 0),
                    urgent_ids=frozenset(urgent_ids),
                    origin=(focused['x'], focused['y']),
                )
                if not self._stop.is_set():
                    GLib.idle_add(self._apply_state, snapshot)
                retry = False
            except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError) as error:
                if not self._stop.is_set():
                    print(f'[IPC state] {error}', file=sys.stderr)
                retry = True  # Keep the last good UI and retry without busy looping.

    def _apply_state(self, snapshot: WorkspaceState) -> bool:
        if self._stop.is_set():
            return False
        self._workspaces = snapshot.windows
        self._special_windows = snapshot.special_windows
        self._special_active = snapshot.special_active
        self._active_id = snapshot.active_id
        self._urgent_ids = snapshot.urgent_ids
        # Follow the focused output using its logical origin, without connector assumptions.
        display = self.get_display()
        for index in range(display.get_n_monitors()):
            monitor = display.get_monitor(index)
            geometry = monitor.get_geometry()
            if (geometry.x, geometry.y) == snapshot.origin:
                if GtkLayerShell.get_monitor(self) != monitor:
                    GtkLayerShell.set_monitor(self, monitor)
                break
        self._rebuild()
        return False

    # ── Rebuild UI ─────────────────────────────────────────────────────────────
    def _rebuild(self):
        row = self._row
        regular_ids = [
            i for i in range(1, 11)
            if (i == self._active_id or
                self._workspaces.get(i, 0) > 0 or
                i in self._urgent_ids)
        ]
        show_special = self._special_active or self._special_windows > 0
        visible_ids  = regular_ids + ([SPECIAL_WS_ID] if show_special else [])

        for ws_id in tuple(self._ws_widgets):
            if ws_id not in visible_ids:
                row.remove(self._ws_widgets[ws_id])
                del self._ws_widgets[ws_id]

        clk_offset = 0 if ARGS.clk else 1

        for pos, ws_id in enumerate(regular_ids):
            windows = self._workspaces.get(ws_id, 0)
            active = (ws_id == self._active_id) and not self._special_active
            urgent = ws_id in self._urgent_ids
            if ws_id not in self._ws_widgets:
                box = WsBox(ws_id, self._request_dispatch)
                self._ws_widgets[ws_id] = box
                row.pack_start(box, False, False, 0)
                box.show()
            row.reorder_child(self._ws_widgets[ws_id], pos + clk_offset)
            self._ws_widgets[ws_id].refresh(active, windows, urgent)

        if show_special:
            pos = len(regular_ids) + clk_offset
            if SPECIAL_WS_ID not in self._ws_widgets:
                box = WsBox(SPECIAL_WS_ID, self._request_dispatch)
                self._ws_widgets[SPECIAL_WS_ID] = box
                row.pack_start(box, False, False, 0)
                box.show()
            row.reorder_child(self._ws_widgets[SPECIAL_WS_ID], pos)
            self._ws_widgets[SPECIAL_WS_ID].refresh(
                self._special_active, self._special_windows, False)
        return False

    # ── IPC event loop ────────────────────────────────────────────────────────
    _STATE_EVENTS = frozenset({
        'workspace', 'workspacev2', 'focusedmon', 'focusedmonv2',
        'activespecial', 'activespecialv2', 'openwindow', 'closewindow',
        'movewindow', 'movewindowv2', 'createworkspace', 'createworkspacev2',
        'destroyworkspace', 'destroyworkspacev2', 'moveworkspace', 'moveworkspacev2',
        'renameworkspace', 'monitoradded', 'monitoraddedv2',
        'monitorremoved', 'monitorremovedv2', 'configreloaded', 'urgent', 'activewindowv2',
    })

    def _event_loop(self):
        while not self._stop.is_set():
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                    sock.settimeout(1)
                    sock.connect(_find_socket('socket2'))
                    # Subscribe before fetching, so startup/reconnection cannot lose state changes.
                    self._requests.put(('refresh', ''))
                    buffer = b''
                    while not self._stop.is_set():
                        try:
                            data = sock.recv(4096)
                        except TimeoutError:
                            continue
                        if not data:
                            break
                        buffer += data
                        while b'\n' in buffer:
                            line, buffer = buffer.split(b'\n', 1)
                            event, separator, payload = line.partition(b'>>')
                            name = event.decode('utf-8', errors='replace')
                            if separator and name in self._STATE_EVENTS:
                                self._requests.put((name, payload.decode('utf-8', errors='replace')))
            except (OSError, KeyError) as error:
                if not self._stop.is_set():
                    print(f'[IPC event] {error}', file=sys.stderr)
            # Clean EOF also needs backoff; otherwise a dead compositor spins at 100% CPU.
            self._stop.wait(1)

    def _close(self, *_):
        self._stop.set()
        self._requests.put(('stop', ''))
        Gtk.main_quit()

    def wait_workers(self):
        for thread in self._threads:
            thread.join(timeout=4)
        if not ARGS.notific:
            self._notif.worker.join(timeout=5)


def main() -> int:
    if not os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'):
        print('HYPRLAND_INSTANCE_SIGNATURE is required. Launch inside Hyprland.', file=sys.stderr)
        return 1
    if not GtkLayerShell.is_supported():
        print('The Wayland compositor must support layer-shell.', file=sys.stderr)
        return 1
    overlay = WorkspaceOverlay()

    def quit_overlay(*_):
        overlay.destroy()
        return False

    sources = [GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, sig, quit_overlay)
               for sig in (signal.SIGINT, signal.SIGTERM)]
    try:
        Gtk.main()
    finally:
        for source in sources:
            if GLib.MainContext.default().find_source_by_id(source) is not None:
                GLib.source_remove(source)
        overlay.destroy()
        overlay.wait_workers()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
