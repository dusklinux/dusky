#!/usr/bin/env python3
"""
===============================================================================
DUSKY DISPLAY LAYOUT DESIGNER: INTERACTIVE 2D SPATIAL CANVAS
===============================================================================
Interactive terminal-based 2D spatial canvas for multi-monitor placement in
Hyprland / Wayland. Features magnetic edge-snapping, aspect-ratio corrected
rendering, live compositor preview, and direct Lua AST persistence.
Incorporates 1/120 quantum scale laddering and decoupled rotation transform algebra.
"""

import os
import sys
import math
import json
import curses
import shutil
import argparse
import subprocess
from pathlib import Path
from dataclasses import dataclass
from typing import Any

# Connect to Dusky TUI engine framework
_DUSKY_ROOT = Path.home() / "user_scripts" / "dusky_tui"
if str(_DUSKY_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_ROOT))

try:
    from python.engines.monitor_engine import MonitorLuaEngine, closest_sharp
except ImportError:
    MonitorLuaEngine = None


def compute_new_transform(current: int, delta: int) -> int:
    """Rotates within quadrant (0-3) preserving flip bit (4)."""
    flip_bit = current & 4
    rot_bits = current & 3
    new_rot = (rot_bits + delta + 4) % 4
    return flip_bit | new_rot


def toggle_flip(current: int) -> int:
    """Toggles flip bit (4) preserving rotation (0-3)."""
    return current ^ 4


def get_scale_ladder(w: int, h: int, current_scale: float | None = None) -> list[float]:
    """
    Calculates sharp scale ladder for a given mode on Hyprland's 1/120 quantum grid.
    Preserves active current_scale and enforces minimum usable logical boundaries (640x360).
    """
    MIN_LOGICAL_LONG = 640
    MIN_LOGICAL_SHORT = 360
    long_, short = max(w, h), min(w, h)

    g = math.gcd(120 * w, 120 * h)
    scales_set: set[float] = set()
    for n in range(60, 481):  # 0.50x to 4.00x
        if g % n == 0:
            s = round(n / 120.0, 5)
            if (long_ / s) >= MIN_LOGICAL_LONG and (short / s) >= MIN_LOGICAL_SHORT:
                scales_set.add(s)

    if current_scale and current_scale > 0:
        cs = round(current_scale, 5)
        if (long_ / cs) >= MIN_LOGICAL_LONG and (short / cs) >= MIN_LOGICAL_SHORT:
            scales_set.add(cs)

    scales = sorted(list(scales_set))
    return scales if scales else [1.0]


@dataclass
class MonitorCanvasItem:
    output_id: int
    name: str
    description: str
    width: int
    height: int
    refresh_rate: float
    scale: float
    transform: int
    x: int
    y: int
    initial_x: int
    initial_y: int
    initial_scale: float
    initial_transform: int
    focused: bool
    disabled: bool
    current_mode: str

    @property
    def logical_width(self) -> int:
        eff_w = self.height if (self.transform % 2 == 1) else self.width
        s = self.scale if self.scale > 0 else 1.0
        n = max(1, round(s * 120))
        return max(1, int(round((eff_w * 120) / n)))

    @property
    def logical_height(self) -> int:
        eff_h = self.width if (self.transform % 2 == 1) else self.height
        s = self.scale if self.scale > 0 else 1.0
        n = max(1, round(s * 120))
        return max(1, int(round((eff_h * 120) / n)))


def spans_overlap(a1: int, a2: int, b1: int, b2: int) -> bool:
    """Returns True if 1D intervals [a1, a2] and [b1, b2] overlap."""
    return a1 < b2 and a2 > b1


def monitors_overlap(m1: MonitorCanvasItem, m2: MonitorCanvasItem) -> bool:
    """Returns True if two active monitors' logical bounding boxes intersect."""
    if m1.disabled or m2.disabled:
        return False
    w1, h1 = m1.logical_width, m1.logical_height
    w2, h2 = m2.logical_width, m2.logical_height
    return spans_overlap(m1.x, m1.x + w1, m2.x, m2.x + w2) and \
           spans_overlap(m1.y, m1.y + h1, m2.y, m2.y + h2)


def has_overlaps(monitors: list[MonitorCanvasItem]) -> bool:
    """Reports if any active monitors in the layout overlap each other."""
    active = [m for m in monitors if not m.disabled]
    for i in range(len(active)):
        for j in range(i + 1, len(active)):
            if monitors_overlap(active[i], active[j]):
                return True
    return False


def output_overlaps_any(monitors: list[MonitorCanvasItem], selected_idx: int, x: int, y: int) -> bool:
    """Tests if placing selected monitor at (x, y) collides with any other active display."""
    if selected_idx < 0 or selected_idx >= len(monitors):
        return False
    sel = monitors[selected_idx]
    w, h = sel.logical_width, sel.logical_height
    for idx, other in enumerate(monitors):
        if idx == selected_idx or other.disabled:
            continue
        ow, oh = other.logical_width, other.logical_height
        if spans_overlap(x, x + w, other.x, other.x + ow) and \
           spans_overlap(y, y + h, other.y, other.y + oh):
            return True
    return False


def place_outside_overlaps(monitors: list[MonitorCanvasItem], selected_idx: int) -> bool:
    """
    Moves an overlapping display to the nearest clear outside edge.
    Evaluates candidates on adjoining clear borders and picks the one with minimum Euclidean distance.
    """
    if selected_idx < 0 or selected_idx >= len(monitors):
        return False
    sel = monitors[selected_idx]
    if sel.disabled or not output_overlaps_any(monitors, selected_idx, sel.x, sel.y):
        return False

    w, h = sel.logical_width, sel.logical_height
    candidates: list[tuple[int, int, int]] = []

    def add_candidate(cx: int, cy: int) -> None:
        if not output_overlaps_any(monitors, selected_idx, cx, cy):
            dx = cx - sel.x
            dy = cy - sel.y
            candidates.append((cx, cy, dx * dx + dy * dy))

    for idx, other in enumerate(monitors):
        if idx == selected_idx or other.disabled:
            continue
        ow, oh = other.logical_width, other.logical_height
        # Flush outside edges along current coordinate
        add_candidate(other.x - w, sel.y)
        add_candidate(other.x + ow, sel.y)
        add_candidate(sel.x, other.y - h)
        add_candidate(sel.x, other.y + oh)
        # Flush corners / aligned edges
        add_candidate(other.x - w, other.y)
        add_candidate(other.x + ow, other.y)
        add_candidate(other.x, other.y - h)
        add_candidate(other.x, other.y + oh)
        add_candidate(other.x + ow - w, other.y - h)
        add_candidate(other.x + ow - w, other.y + oh)

    if not candidates:
        return False

    best_x, best_y, _ = min(candidates, key=lambda c: c[2])
    sel.x = best_x
    sel.y = best_y
    return True


def resolve_layout_overlaps(monitors: list[MonitorCanvasItem], selected_idx: int = 0) -> bool:
    """Resolves collisions across all active displays in the arrangement."""
    moved = place_outside_overlaps(monitors, selected_idx)
    for idx in range(len(monitors)):
        if idx != selected_idx:
            if place_outside_overlaps(monitors, idx):
                moved = True
    return moved


def wake_displays(outputs: list[str] | None = None) -> bool:
    """Wakes displays from DPMS sleep in Hyprland 0.55+ (evaluating Lua dispatcher with fallback)."""
    try:
        if outputs:
            for out in outputs:
                clean = out.strip().removeprefix("desc:")
                if not clean:
                    continue
                cmd = f'hl.dsp.dpms({{ action = "enable", monitor = {json.dumps(clean)} }})'
                res = subprocess.run(["hyprctl", "dispatch", cmd], capture_output=True, text=True, timeout=1)
                if res.returncode != 0:
                    subprocess.run(["hyprctl", "dispatch", "dpms", "on", clean], capture_output=True, timeout=1)
        else:
            cmd = 'hl.dsp.dpms({ action = "enable" })'
            res = subprocess.run(["hyprctl", "dispatch", cmd], capture_output=True, text=True, timeout=1)
            if res.returncode != 0:
                subprocess.run(["hyprctl", "dispatch", "dpms", "on"], capture_output=True, timeout=1)
        return True
    except Exception:
        return False


class MonitorLayoutCanvas:
    """Curses-driven visual spatial layout tool for Hyprland monitors."""

    SNAP_THRESHOLD = 40  # Magnetic pixel snap distance
    NORMAL_STEP = 20     # Step size for arrow keys
    LARGE_STEP = 120     # Step size for Shift+arrows (Hyprland 1/120 quantum)

    def __init__(self, config_path: str | None = None, live_preview_default: bool = False):
        self.config_path = config_path or self._detect_config_path()
        self.live_preview = live_preview_default
        self.monitors: list[MonitorCanvasItem] = []
        self.selected_idx = 0
        self.status_message = "Ready. [Arrows] Move  [R] Rotate  [F] Flip  [+/-] Scale  [Enter] Save."
        self.status_is_error = False
        self.has_unsaved_changes = False
        self.saved_successfully = False

        self._load_monitors()

    def _detect_config_path(self) -> str:
        candidates = [
            Path.home() / ".config" / "hypr" / "edit_here" / "source" / "monitors.lua",
            Path.home() / ".config" / "hypr" / "source" / "monitors.lua",
            Path.home() / "Documents" / "monitors.lua",
        ]
        for c in candidates:
            if c.exists():
                return str(c)
        return str(candidates[0])

    def _load_monitors(self) -> None:
        """Queries live hardware status from Hyprland and matches with config."""
        try:
            res = subprocess.run(
                ["hyprctl", "-j", "monitors", "all"],
                capture_output=True,
                text=True,
                timeout=3,
                check=True
            )
            data = json.loads(res.stdout.strip())
        except Exception as e:
            data = []
            self.status_message = f"Failed to query hyprctl: {e}"
            self.status_is_error = True

        # Filter out Hyprland synthetic FALLBACK placeholder heads
        data = [m for m in data if m.get("name", "").strip().upper() != "FALLBACK"]

        self.monitors = []
        for idx, m in enumerate(data):
            name = m.get("name", f"Unknown-{idx}")
            w = int(m.get("width", 1920))
            h = int(m.get("height", 1080))
            rr = float(m.get("refreshRate", 60.0))
            s = float(m.get("scale", 1.0))
            t = int(m.get("transform", 0))
            x = int(m.get("x", 0))
            y = int(m.get("y", 0))
            foc = bool(m.get("focused", False))
            dis = bool(m.get("disabled", False))
            desc = m.get("description", "")
            mode = f"{w}x{h}@{rr:.2f}"

            self.monitors.append(
                MonitorCanvasItem(
                    output_id=idx,
                    name=name,
                    description=desc,
                    width=w,
                    height=h,
                    refresh_rate=rr,
                    scale=s,
                    transform=t,
                    x=x,
                    y=y,
                    initial_x=x,
                    initial_y=y,
                    initial_scale=s,
                    initial_transform=t,
                    focused=foc,
                    disabled=dis,
                    current_mode=mode
                )
            )

        if not self.monitors:
            # Fallback mock for testing
            self.monitors = [
                MonitorCanvasItem(
                    output_id=0, name="eDP-1", description="Primary Display",
                    width=1920, height=1080, refresh_rate=60.0, scale=1.0,
                    transform=0, x=0, y=0, initial_x=0, initial_y=0,
                    initial_scale=1.0, initial_transform=0,
                    focused=True, disabled=False, current_mode="1920x1080@60.00"
                )
            ]

    def _normalize_origin(self) -> None:
        """Shifts all displays so the top-left coordinate begins at 0, 0."""
        if not self.monitors:
            return
        active = [m for m in self.monitors if not m.disabled]
        if not active:
            return
        min_x = min(m.x for m in active)
        min_y = min(m.y for m in active)
        if min_x != 0 or min_y != 0:
            for m in active:
                m.x -= min_x
                m.y -= min_y
            self.has_unsaved_changes = True
            self.status_message = f"Normalized layout origin to (0, 0) [Shifted by {-min_x}, {-min_y}]."

    def _rotate_selected(self, delta: int = 1) -> None:
        """Rotates the selected monitor by 90-degree step preserving flips."""
        if not self.monitors:
            return
        cur = self.monitors[self.selected_idx]
        cur.transform = compute_new_transform(cur.transform, delta)
        self.has_unsaved_changes = True
        rot_deg = (cur.transform & 3) * 90
        flip_str = " (flipped)" if (cur.transform & 4) else ""
        self.status_message = f"Rotated {cur.name} to {rot_deg}°{flip_str} [Logical: {cur.logical_width}x{cur.logical_height}]."
        if self.live_preview:
            self._dispatch_live_preview()

    def _flip_selected(self) -> None:
        """Toggles horizontal/vertical flip on the selected monitor."""
        if not self.monitors:
            return
        cur = self.monitors[self.selected_idx]
        cur.transform = toggle_flip(cur.transform)
        self.has_unsaved_changes = True
        flip_state = "Flipped" if (cur.transform & 4) else "Unflipped"
        self.status_message = f"{flip_state} {cur.name} [Transform: {cur.transform}]."
        if self.live_preview:
            self._dispatch_live_preview()

    def _step_scale_selected(self, direction: int) -> None:
        """Steps scale up (+1) or down (-1) along the sharp scale ladder."""
        if not self.monitors:
            return
        cur = self.monitors[self.selected_idx]
        ladder = get_scale_ladder(cur.width, cur.height, cur.scale)
        if not ladder:
            return

        cur_s = round(cur.scale, 5)
        closest_idx = min(range(len(ladder)), key=lambda i: abs(ladder[i] - cur_s))
        new_idx = max(0, min(len(ladder) - 1, closest_idx + direction))

        if new_idx == closest_idx and (
            (direction > 0 and ladder[closest_idx] <= cur_s) or 
            (direction < 0 and ladder[closest_idx] >= cur_s)
        ):
            self.status_message = f"{cur.name}: Scale limit reached at {ladder[closest_idx]:g}x."
            return

        cur.scale = ladder[new_idx]
        self.has_unsaved_changes = True
        self.status_message = f"Scaled {cur.name} to {cur.scale:g}x [Logical: {cur.logical_width}x{cur.logical_height}]."
        if self.live_preview:
            self._dispatch_live_preview()

    def _snap_selected(self, direction: str) -> None:
        """Magnetically snaps the selected monitor flush to its neighbor."""
        if len(self.monitors) < 2:
            self.status_message = "Only one monitor connected; snapping requires at least 2 displays."
            return

        cur = self.monitors[self.selected_idx]
        peers = [m for idx, m in enumerate(self.monitors) if idx != self.selected_idx and not m.disabled]
        if not peers:
            return

        cur_cx = cur.x + cur.logical_width / 2
        cur_cy = cur.y + cur.logical_height / 2

        def dist(p: MonitorCanvasItem) -> float:
            pcx = p.x + p.logical_width / 2
            pcy = p.y + p.logical_height / 2
            return math.hypot(cur_cx - pcx, cur_cy - pcy)

        peer = min(peers, key=dist)

        old_pos = (cur.x, cur.y)

        if direction == "left":
            cur.x = peer.x - cur.logical_width
            cur.y = peer.y + (peer.logical_height - cur.logical_height) // 2
            self.status_message = f"Snapped {cur.name} to the LEFT of {peer.name} (Centered)."
        elif direction == "right":
            cur.x = peer.x + peer.logical_width
            cur.y = peer.y + (peer.logical_height - cur.logical_height) // 2
            self.status_message = f"Snapped {cur.name} to the RIGHT of {peer.name} (Centered)."
        elif direction == "above":
            cur.y = peer.y - cur.logical_height
            cur.x = peer.x + (peer.logical_width - cur.logical_width) // 2
            self.status_message = f"Snapped {cur.name} ABOVE {peer.name} (Centered)."
        elif direction == "below":
            cur.y = peer.y + peer.logical_height
            cur.x = peer.x + (peer.logical_width - cur.logical_width) // 2
            self.status_message = f"Snapped {cur.name} BELOW {peer.name} (Centered)."
        elif direction == "center":
            if abs(cur_cx - (peer.x + peer.logical_width / 2)) >= abs(cur_cy - (peer.y + peer.logical_height / 2)):
                cur.y = peer.y + (peer.logical_height - cur.logical_height) // 2
                self.status_message = f"Aligned vertical center of {cur.name} with {peer.name}."
            else:
                cur.x = peer.x + (peer.logical_width - cur.logical_width) // 2
                self.status_message = f"Aligned horizontal center of {cur.name} with {peer.name}."
        elif direction == "top":
            cur.y = peer.y
            self.status_message = f"Aligned top edge of {cur.name} with {peer.name}."
        elif direction == "bottom":
            cur.y = peer.y + peer.logical_height - cur.logical_height
            self.status_message = f"Aligned bottom edge of {cur.name} with {peer.name}."

        if (cur.x, cur.y) != old_pos:
            # Clear any collateral collision against other displays
            place_outside_overlaps(self.monitors, self.selected_idx)
            self.has_unsaved_changes = True
            if self.live_preview:
                self._dispatch_live_preview()

    def _move_selected(self, dx: int, dy: int) -> None:
        """Moves the selected monitor with span-checked magnetic snapping and collision detection."""
        if not self.monitors:
            return
        cur = self.monitors[self.selected_idx]
        new_x = cur.x + dx
        new_y = cur.y + dy

        peers = [m for idx, m in enumerate(self.monitors) if idx != self.selected_idx and not m.disabled]

        # Calculate candidate snaps along X
        cand_x = [0]  # Snap to origin
        for p in peers:
            # Flush snap only if Y spans overlap
            if spans_overlap(new_y, new_y + cur.logical_height, p.y, p.y + p.logical_height):
                cand_x.append(p.x + p.logical_width)   # Flush right of peer
                cand_x.append(p.x - cur.logical_width)  # Flush left of peer
            # Alignment snap
            cand_x.append(p.x)                         # Flush left edges
            cand_x.append(p.x + p.logical_width - cur.logical_width) # Flush right edges

        valid_x = [pos for pos in cand_x if abs(new_x - pos) <= self.SNAP_THRESHOLD]
        if valid_x:
            new_x = min(valid_x, key=lambda pos: abs(new_x - pos))

        # Calculate candidate snaps along Y
        cand_y = [0]  # Snap to origin
        for p in peers:
            # Flush snap only if X spans overlap
            if spans_overlap(new_x, new_x + cur.logical_width, p.x, p.x + p.logical_width):
                cand_y.append(p.y + p.logical_height)   # Flush below peer
                cand_y.append(p.y - cur.logical_height)  # Flush above peer
            # Alignment snap
            cand_y.append(p.y)                         # Flush top edges
            cand_y.append(p.y + p.logical_height - cur.logical_height) # Flush bottom edges

        valid_y = [pos for pos in cand_y if abs(new_y - pos) <= self.SNAP_THRESHOLD]
        if valid_y:
            new_y = min(valid_y, key=lambda pos: abs(new_y - pos))

        cur.x = new_x
        cur.y = new_y
        self.has_unsaved_changes = True

        if output_overlaps_any(self.monitors, self.selected_idx, cur.x, cur.y):
            self.status_message = f"Moved {cur.name} to ({cur.x}, {cur.y}) [COLLISION DETECTED - Press 'O' to resolve]."
        else:
            self.status_message = f"Moved {cur.name} to ({cur.x}, {cur.y})."

        if self.live_preview:
            self._dispatch_live_preview()

    def _dispatch_live_preview(self) -> None:
        """Applies monitor placements live via Hyprland Lua eval (with batch keyword fallback)."""
        lua_calls = []
        batch_cmds = []
        for m in self.monitors:
            if m.disabled:
                lua_calls.append(f'hl.monitor({{ output = {json.dumps(m.name)}, disabled = true }})')
                batch_cmds.append(f"keyword monitor {m.name},disable")
            else:
                scale_str = f"{m.scale:.5f}".rstrip("0").rstrip(".") if m.scale % 1 != 0 else str(int(m.scale))
                lua_calls.append(f'hl.monitor({{ output = {json.dumps(m.name)}, mode = {json.dumps(m.current_mode)}, position = "{m.x}x{m.y}", scale = {scale_str}, transform = {m.transform}, disabled = false }})')
                batch_cmds.append(f"keyword monitor {m.name},{m.current_mode},{m.x}x{m.y},{scale_str},transform,{m.transform}")

        if not lua_calls:
            return

        # Attempt atomic single-eval via Hyprland 0.55+ Lua interface
        try:
            eval_code = " ".join(lua_calls)
            res = subprocess.run(["hyprctl", "eval", eval_code], capture_output=True, text=True, timeout=1)
            if res.returncode == 0 and "ok" in res.stdout:
                return
        except Exception:
            pass

        # Fallback to batch keyword execution
        try:
            subprocess.run(["hyprctl", "--batch", " ; ".join(batch_cmds)], capture_output=True, timeout=1)
        except Exception:
            pass

    def _revert_live(self) -> None:
        """Restores monitors to their initial positions, scales, and transforms upon cancel."""
        for m in self.monitors:
            m.x = m.initial_x
            m.y = m.initial_y
            m.scale = m.initial_scale
            m.transform = m.initial_transform
        self._dispatch_live_preview()

    def save_and_apply(self) -> bool:
        """Writes coordinates, scales, and transforms to monitors.lua and dispatches reload and DPMS wake."""
        if has_overlaps(self.monitors):
            resolve_layout_overlaps(self.monitors, self.selected_idx)
            self.status_message = "Automatically resolved collisions before saving."

        self._normalize_origin()

        if MonitorLuaEngine:
            try:
                engine = MonitorLuaEngine(self.config_path)
                engine.load_state()
                changes = []
                for m in self.monitors:
                    scope = f"monitor/{m.name}"
                    scale_str = f"{m.scale:.5f}".rstrip("0").rstrip(".") if m.scale % 1 != 0 else str(int(m.scale))
                    changes.append(("position", scope, f"{m.x}x{m.y}", "string"))
                    changes.append(("scale", scope, scale_str, "float"))
                    changes.append(("transform", scope, str(int(m.transform)), "int"))
                engine.write_batch(changes)
            except Exception as e:
                self.status_message = f"Failed to write Lua AST: {e}"
                self.status_is_error = True
                return False

        # Live dispatch and reload
        self._dispatch_live_preview()
        subprocess.run(["hyprctl", "reload"], capture_output=True, timeout=2)

        # Wake active displays from DPMS sleep
        wake_displays([m.name for m in self.monitors if not m.disabled])

        self.has_unsaved_changes = False
        self.saved_successfully = True
        self.status_message = f"Saved {len(self.monitors)} monitors to {Path(self.config_path).name} and reloaded."
        return True

    def run(self, stdscr: curses.window) -> int:
        curses.curs_set(0)
        curses.raw()  # Disables flow control (IXON / Ctrl+S / Ctrl+Q) so terminal I/O never blocks
        curses.start_color()
        curses.use_default_colors()

        # Initialize color pairs
        curses.init_pair(1, curses.COLOR_CYAN, -1)     # Selected highlight / header
        curses.init_pair(2, curses.COLOR_WHITE, -1)    # Inactive borders / normal text
        curses.init_pair(3, curses.COLOR_GREEN, -1)    # Success / focused
        curses.init_pair(4, curses.COLOR_YELLOW, -1)   # Warning / uncommitted
        curses.init_pair(5, curses.COLOR_RED, -1)      # Error
        curses.init_pair(6, curses.COLOR_BLACK, curses.COLOR_CYAN) # Inverted selected tag
        curses.init_pair(7, curses.COLOR_MAGENTA, -1)  # Accent dimensions

        stdscr.keypad(True)
        curses.mousemask(curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED)

        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()

            if h < 16 or w < 60:
                stdscr.addstr(0, 0, "Terminal window too small (minimum 60x16 required).", curses.color_pair(5))
                stdscr.refresh()
                key = stdscr.getch()
                if key in (ord('q'), ord('Q'), 27):
                    break
                continue

            self._draw_header(stdscr, w)
            boxes = self._draw_canvas(stdscr, 3, 1, h - 9, w - 2)
            self._draw_status(stdscr, h - 5, w)
            self._draw_footer(stdscr, h - 3, w)
            stdscr.refresh()

            key = stdscr.getch()

            if key in (ord('q'), ord('Q'), 27):  # Quit / Esc
                if self.has_unsaved_changes and self.live_preview:
                    self._revert_live()
                return 0 if self.saved_successfully else 1

            elif key in (10, 13, ord('s'), ord('S')):  # Enter / s = Save
                if self.save_and_apply():
                    curses.napms(700)
                    return 0

            elif key in (9, ord('\t')):  # Tab = cycle monitors
                if self.monitors:
                    self.selected_idx = (self.selected_idx + 1) % len(self.monitors)

            elif key == curses.KEY_BTAB:  # Shift+Tab
                if self.monitors:
                    self.selected_idx = (self.selected_idx - 1) % len(self.monitors)

            elif ord('1') <= key <= ord('9'):  # Number jump
                idx = key - ord('1')
                if idx < len(self.monitors):
                    self.selected_idx = idx

            # Movement (fine step)
            elif key in (curses.KEY_LEFT, ord('h')):
                self._move_selected(-self.NORMAL_STEP, 0)
            elif key in (curses.KEY_RIGHT, ord('l')):
                self._move_selected(self.NORMAL_STEP, 0)
            elif key in (curses.KEY_UP, ord('k')):
                self._move_selected(0, -self.NORMAL_STEP)
            elif key in (curses.KEY_DOWN, ord('j')):
                self._move_selected(0, self.NORMAL_STEP)

            # Movement (large quantum step: Hyprland 120px)
            elif key in (curses.KEY_SLEFT, ord('H')):
                self._move_selected(-self.LARGE_STEP, 0)
            elif key in (curses.KEY_SRIGHT, ord('L')):
                self._move_selected(self.LARGE_STEP, 0)
            elif key in (curses.KEY_SR, ord('K')):
                self._move_selected(0, -self.LARGE_STEP)
            elif key in (curses.KEY_SF, ord('J')):
                self._move_selected(0, self.LARGE_STEP)

            # Rotation (90-deg clockwise) & Flip
            elif key in (ord('r'), ord('R')):
                self._rotate_selected(1)
            elif key in (ord('f'), ord('F')):
                self._flip_selected()

            # Scaling (+ / - along sharp quantum ladder)
            elif key in (ord('+'), ord('=')):
                self._step_scale_selected(1)
            elif key in (ord('-'), ord('_')):
                self._step_scale_selected(-1)

            # Magnetic Snap Hotkeys
            elif key == ord('['):
                self._snap_selected("left")
            elif key == ord(']'):
                self._snap_selected("right")
            elif key in (ord('{'), ord('(')):
                self._snap_selected("above")
            elif key in (ord('}'), ord(')')):
                self._snap_selected("below")
            elif key in (ord('c'), ord('C')):
                self._snap_selected("center")
            elif key in (ord('t'), ord('T')):
                self._snap_selected("top")
            elif key in (ord('b'), ord('B')):
                self._snap_selected("bottom")

            # Origin normalization
            elif key == ord('0'):
                self._normalize_origin()

            # Overlap resolution (flush to outside edges)
            elif key in (ord('o'), ord('O')):
                if resolve_layout_overlaps(self.monitors, self.selected_idx):
                    self.has_unsaved_changes = True
                    self.status_message = "Resolved display overlaps to clear outside edges."
                    if self.live_preview:
                        self._dispatch_live_preview()
                else:
                    self.status_message = "Layout is already collision-free."

            # Live preview toggle
            elif key in (ord('p'), ord('P')):
                self.live_preview = not self.live_preview
                if self.live_preview:
                    self.status_message = "Live preview ENABLED: Displays adjust on-the-fly."
                    self._dispatch_live_preview()
                else:
                    self.status_message = "Live preview DISABLED."

            # Reset layout (Backspace or X)
            elif key in (curses.KEY_BACKSPACE, 127, ord('x'), ord('X')):
                for m in self.monitors:
                    m.x = m.initial_x
                    m.y = m.initial_y
                    m.scale = m.initial_scale
                    m.transform = m.initial_transform
                if self.live_preview:
                    self._dispatch_live_preview()
                self.has_unsaved_changes = False
                self.status_message = "Reset layout to current live coordinates, scale, and transform."

            # Mouse click selection
            elif key == curses.KEY_MOUSE:
                try:
                    _, mx, my, _, _ = curses.getmouse()
                    for idx, (bx, by, bw, bh) in boxes.items():
                        if bx <= mx < bx + bw and by <= my < by + bh:
                            self.selected_idx = idx
                            break
                except Exception:
                    pass

        return 0

    def _draw_header(self, win: curses.window, width: int) -> None:
        title = " DUSKY DISPLAY LAYOUT DESIGNER "
        sep = "─" * (width - 2)
        win.addstr(0, 1, sep, curses.color_pair(1) | curses.A_DIM)
        win.addstr(0, max(2, (width - len(title)) // 2), title, curses.color_pair(1) | curses.A_BOLD)

        tabs_str = " Displays: "
        win.addstr(1, 1, tabs_str, curses.color_pair(2) | curses.A_DIM)
        cur_x = 1 + len(tabs_str)
        for idx, m in enumerate(self.monitors):
            label = f" [{idx + 1}] {m.name} "
            if idx == self.selected_idx:
                win.addstr(1, cur_x, label, curses.color_pair(6) | curses.A_BOLD)
            else:
                win.addstr(1, cur_x, label, curses.color_pair(2))
            cur_x += len(label) + 1

    def _draw_canvas(self, win: curses.window, top: int, left: int, height: int, width: int) -> dict[int, tuple[int, int, int, int]]:
        """
        Renders an aspect-ratio-corrected 2D viewport representing the desktop canvas.
        Returns a mapping of monitor index to screen bounding box (col, row, width, height) for mouse picking.
        """
        for y in range(top, top + height):
            win.addch(y, left, "│", curses.color_pair(2) | curses.A_DIM)
            win.addch(y, left + width - 1, "│", curses.color_pair(2) | curses.A_DIM)
        win.addstr(top - 1, left, "┌" + "─" * (width - 2) + "┐", curses.color_pair(2) | curses.A_DIM)
        win.addstr(top + height, left, "└" + "─" * (width - 2) + "┘", curses.color_pair(2) | curses.A_DIM)

        if not self.monitors:
            return {}

        active = [m for m in self.monitors if not m.disabled]
        if not active:
            win.addstr(top + height // 2, left + max(2, (width - 22) // 2), "No active monitors.", curses.color_pair(4))
            return {}

        min_vx = min(m.x for m in active)
        max_vx = max(m.x + m.logical_width for m in active)
        min_vy = min(m.y for m in active)
        max_vy = max(m.y + m.logical_height for m in active)

        span_vx = max(1, max_vx - min_vx)
        span_vy = max(1, max_vy - min_vy)

        draw_w = width - 4
        draw_h = height - 2
        char_aspect = 2.0

        scale_x = draw_w / span_vx
        scale_y = (draw_h * char_aspect) / span_vy
        uniform_scale = min(scale_x, scale_y) * 0.82

        center_col = left + width // 2
        center_row = top + height // 2

        center_vx = (min_vx + max_vx) / 2.0
        center_vy = (min_vy + max_vy) / 2.0

        screen_boxes: dict[int, tuple[int, int, int, int]] = {}

        for idx, m in enumerate(self.monitors):
            if m.disabled:
                continue

            box_w = max(16, int(round(m.logical_width * uniform_scale)))
            box_h = max(5, int(round((m.logical_height * uniform_scale) / char_aspect)))

            offset_vx = m.x - center_vx
            offset_vy = m.y - center_vy

            box_x = int(round(center_col + (offset_vx * uniform_scale)))
            box_y = int(round(center_row + ((offset_vy * uniform_scale) / char_aspect)))

            box_x = max(left + 1, min(left + width - box_w - 1, box_x))
            box_y = max(top, min(top + height - box_h, box_y))

            screen_boxes[idx] = (box_x, box_y, box_w, box_h)
            is_sel = (idx == self.selected_idx)
            is_colliding = any(
                idx != other_idx and monitors_overlap(m, other)
                for other_idx, other in enumerate(self.monitors)
            )
            self._render_monitor_box(win, box_x, box_y, box_w, box_h, m, is_sel, is_colliding)

        return screen_boxes

    def _render_monitor_box(
        self,
        win: curses.window,
        x: int,
        y: int,
        w: int,
        h: int,
        m: MonitorCanvasItem,
        is_selected: bool,
        is_colliding: bool = False
    ) -> None:
        """Renders an individual display box with borders and information."""
        if is_colliding:
            color = curses.color_pair(5)  # Error / Red
            style = curses.A_BOLD
        else:
            color = curses.color_pair(1 if is_selected else 2)
            style = curses.A_BOLD if is_selected else curses.A_NORMAL

        tl, tr, bl, br, hl, vl = ("╔", "╗", "╚", "╝", "═", "║") if (is_selected or is_colliding) else ("┌", "┐", "└", "┘", "─", "│")

        try:
            win.addstr(y, x, tl + hl * (w - 2) + tr, color | style)
            for r in range(y + 1, y + h - 1):
                win.addstr(r, x, vl, color | style)
                win.addstr(r, x + 1, " " * (w - 2), curses.color_pair(2))
                win.addstr(r, x + w - 1, vl, color | style)
            win.addstr(y + h - 1, x, bl + hl * (w - 2) + br, color | style)
        except curses.error:
            pass

        try:
            name_str = f" {m.name} "
            if is_colliding:
                name_str += "![OVERLAP] "
            rot_tag = f"({(m.transform & 3)*90}°)" if m.transform != 0 else ""
            if rot_tag:
                name_str += rot_tag
            if is_colliding:
                win.addstr(y, x + max(1, (w - len(name_str)) // 2), name_str, curses.color_pair(5) | curses.A_BOLD)
            elif is_selected:
                win.addstr(y, x + max(1, (w - len(name_str)) // 2), name_str, curses.color_pair(6) | curses.A_BOLD)
            else:
                win.addstr(y, x + max(1, (w - len(name_str)) // 2), name_str, curses.color_pair(2) | curses.A_BOLD)

            line1 = f"{m.width}x{m.height} @{m.scale:g}x"
            if h >= 4 and len(line1) <= w - 2:
                win.addstr(y + 1, x + max(1, (w - len(line1)) // 2), line1, curses.color_pair(5 if is_colliding else (7 if is_selected else 2)))

            line2 = f"Pos: {m.x}, {m.y}"
            if h >= 5 and len(line2) <= w - 2:
                win.addstr(y + 2, x + max(1, (w - len(line2)) // 2), line2, curses.color_pair(5 if is_colliding else (1 if is_selected else 2)) | curses.A_BOLD)

            if h >= 6:
                line3 = f"({m.logical_width}x{m.logical_height}L)"
                if len(line3) <= w - 2:
                    win.addstr(y + 3, x + max(1, (w - len(line3)) // 2), line3, curses.color_pair(2) | curses.A_DIM)

        except curses.error:
            pass

    def _draw_status(self, win: curses.window, top: int, width: int) -> None:
        """Renders the dynamic info and error notification bar."""
        colliding = has_overlaps(self.monitors)
        color = curses.color_pair(5 if (self.status_is_error or colliding) else (4 if self.has_unsaved_changes else 3))
        prefix = " ● " if (not self.has_unsaved_changes and not colliding) else " ✦ "
        msg = f"{prefix}{self.status_message}"
        if colliding and "COLLISION" not in self.status_message and "OVERLAP" not in self.status_message:
            msg += " [OVERLAP DETECTED - Press 'O' to resolve]"
        try:
            win.addstr(top, 1, msg[:width - 2], color | curses.A_BOLD)
            cur = self.monitors[self.selected_idx] if self.monitors else None
            if cur:
                rot_lbl = f"{(cur.transform & 3)*90}°" + (" (flipped)" if (cur.transform & 4) else "")
                sub = f" Active: {cur.name} ({cur.width}x{cur.height}) | Rot: {rot_lbl} | Scale: {cur.scale:g}x | Logical: {cur.logical_width}x{cur.logical_height} | Pos: {cur.x}x{cur.y} | Live: {'[ON]' if self.live_preview else '[OFF]'} "
                win.addstr(top + 1, 1, sub[:width - 2], curses.color_pair(2) | curses.A_DIM)
        except curses.error:
            pass

    def _draw_footer(self, win: curses.window, top: int, width: int) -> None:
        """Renders the keyboard shortcuts legend."""
        sep = "─" * (width - 2)
        try:
            win.addstr(top, 1, sep, curses.color_pair(2) | curses.A_DIM)
            shortcuts_1 = " [Tab] Cycle  [Arrows/hjkl] Move  [Shift+Arrows] 120px  [R] Rotate 90°  [F] Flip  [+/-] Scale "
            shortcuts_2 = " [[] Snap Left  []] Snap Right  [{] Snap Above  [}] Snap Below  [C] Center  [O] Resolve  [0] Origin  [Enter/S] Save  [Q] Exit "
            win.addstr(top + 1, 1, shortcuts_1[:width - 2], curses.color_pair(1))
            win.addstr(top + 2, 1, shortcuts_2[:width - 2], curses.color_pair(2))
        except curses.error:
            pass


def launch_in_floating_terminal(script_path: Path) -> int:
    """Spawns this visual layout designer inside a centered floating terminal window."""
    term = None
    for cand in ["kitty", "foot", "alacritty", "ghostty"]:
        if shutil.which(cand):
            term = cand
            break

    if not term:
        return curses.wrapper(lambda scr: MonitorLayoutCanvas().run(scr))

    if term == "kitty":
        app_cmd = f"kitty --class dusky-monitor-layout python3 {script_path}"
    elif term == "foot":
        app_cmd = f"foot -a dusky-monitor-layout python3 {script_path}"
    else:
        app_cmd = f"{term} -e python3 {script_path}"

    rule_cmd = f"[float; size 1050 680; center] {app_cmd}"

    # In Hyprland 0.56+ (Lua dispatcher CLI), call hl.dsp.exec_cmd
    res = subprocess.run(
        ["hyprctl", "dispatch", f"hl.dsp.exec_cmd({json.dumps(rule_cmd)})"],
        capture_output=True, text=True
    )
    if res.returncode == 0:
        print("Floating layout canvas launched.")
        return 0

    # Fallback to standard hyprctl dispatch exec
    res = subprocess.run(
        ["hyprctl", "dispatch", "exec", rule_cmd],
        capture_output=True, text=True
    )
    if res.returncode == 0:
        print("Floating layout canvas launched.")
        return 0

    # Direct launch fallback
    try:
        subprocess.Popen(app_cmd, shell=True, start_new_session=True)
        print("Floating layout canvas launched.")
        return 0
    except Exception as e:
        sys.stderr.write(f"Failed to spawn floating terminal: {e}\n")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Dusky Display Layout Designer")
    parser.add_argument("--config", "-c", type=str, default=None, help="Path to monitors.lua")
    parser.add_argument("--live", "-l", action="store_true", help="Enable live compositor preview by default")
    parser.add_argument("--floating", "-f", action="store_true", help="Launch in a centered floating terminal window")
    args = parser.parse_args()

    if args.floating:
        return launch_in_floating_terminal(Path(__file__).resolve())

    canvas = MonitorLayoutCanvas(config_path=args.config, live_preview_default=args.live)
    return curses.wrapper(canvas.run)


if __name__ == "__main__":
    sys.exit(main())
