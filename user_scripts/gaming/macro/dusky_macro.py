#!/usr/bin/env python3
"""
dusky_macro.py — Production Pure-Wayland Macro Recorder, Player, Profile Engine & Interactive TUI.

Target Environment:
  - Arch Linux (Linux Kernel 7.4+)
  - Hyprland 0.56+ / 0.57+ (Wayland exclusively; zero X11 / XWayland dependencies)
  - Python 3.15+ (Stdlib tomllib + ctypes libxkbcommon.so + rich Interactive TUI)
"""

from __future__ import annotations

import atexit
import collections
import contextlib
import ctypes
import ctypes.util
import errno
import fcntl
import json
import os
import re
import select
import selectors
import signal
import socket
import struct
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__version__ = "2.2.0"

# Suppress SIGPIPE globally so broken Wayland/Hyprland sockets raise EPIPE instead of killing the process
if hasattr(signal, "SIGPIPE"):
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)

# Default root directory: directory containing dusky_macro.py (e.g. ~/user_scripts/gaming/macro)
SCRIPT_ROOT = Path(__file__).resolve().parent

_CLOCK_MONOTONIC = getattr(time, "CLOCK_MONOTONIC", 1)


def _monotonic_ns() -> int:
    """Strict CLOCK_MONOTONIC nanosecond clock aligned with kernel EVIOCSCLOCKID timestamps."""
    return time.clock_gettime_ns(_CLOCK_MONOTONIC)


# =============================================================================
# SECTION 1: KERNEL EVDEV, WAYLAND WIRE, AND KEYCODE CONSTANTS
# =============================================================================

# Linux kernel 64-bit input_event struct: struct timeval (tv_sec: q, tv_usec: q), type: H, code: H, value: i
EVDEV_EVENT_STRUCT = struct.Struct("<qqHHi")
EVDEV_EVENT_SIZE = EVDEV_EVENT_STRUCT.size  # 24 bytes

# Event types
EV_SYN = 0x00
EV_KEY = 0x01
EV_REL = 0x02
EV_ABS = 0x03
EV_MSC = 0x04

# Synchronization codes
SYN_REPORT = 0x00
SYN_DROPPED = 0x03

# Relative axes
REL_X = 0x00
REL_Y = 0x01
REL_HWHEEL = 0x06
REL_WHEEL = 0x08
REL_WHEEL_HI_RES = 0x0B
REL_HWHEEL_HI_RES = 0x0C
HI_RES_UNITS_PER_NOTCH = 120.0

# Key / Button states
KEY_RELEASED = 0
KEY_PRESSED = 1
KEY_REPEAT = 2

# Mouse button evdev codes
BTN_LEFT = 0x110
BTN_RIGHT = 0x111
BTN_MIDDLE = 0x112
BTN_SIDE = 0x113
BTN_EXTRA = 0x114

BUTTON_CODE_TO_NAME: dict[int, str] = {
    BTN_LEFT: "left",
    BTN_RIGHT: "right",
    BTN_MIDDLE: "middle",
    BTN_SIDE: "back",
    BTN_EXTRA: "forward",
}
BUTTON_NAME_TO_CODE: dict[str, int] = {
    "left": BTN_LEFT,
    "right": BTN_RIGHT,
    "middle": BTN_MIDDLE,
    "back": BTN_SIDE,
    "side": BTN_SIDE,
    "mouse4": BTN_SIDE,
    "forward": BTN_EXTRA,
    "extra": BTN_EXTRA,
    "mouse5": BTN_EXTRA,
}

# Standard Linux input-event-codes for hotkeys, keyboard keys, and mouse buttons
KEY_NAMES_TO_CODES: dict[str, int] = {
    "esc": 1, "escape": 1,
    "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9, "9": 10, "0": 11,
    "minus": 12, "equal": 13, "backspace": 14, "tab": 15,
    "q": 16, "w": 17, "e": 18, "r": 19, "t": 20, "y": 21, "u": 22, "i": 23, "o": 24, "p": 25,
    "leftbrace": 26, "rightbrace": 27, "enter": 28, "return": 28,
    "leftctrl": 29, "ctrl": 29, "control": 29,
    "a": 30, "s": 31, "d": 32, "f": 33, "g": 34, "h": 35, "j": 36, "k": 37, "l": 38,
    "semicolon": 39, "apostrophe": 40, "grave": 41,
    "leftshift": 42, "shift": 42, "backslash": 43,
    "z": 44, "x": 45, "c": 46, "v": 47, "b": 48, "n": 49, "m": 50,
    "comma": 51, "dot": 52, "slash": 53, "rightshift": 54,
    "kpasterisk": 55, "leftalt": 56, "alt": 56, "space": 57, "capslock": 58,
    "f1": 59, "f2": 60, "f3": 61, "f4": 62, "f5": 63, "f6": 64,
    "f7": 65, "f8": 66, "f9": 67, "f10": 68, "f11": 87, "f12": 88,
    "numlock": 69, "scrolllock": 70,
    "kp7": 71, "kp8": 72, "kp9": 73, "kpminus": 74,
    "kp4": 75, "kp5": 76, "kp6": 77, "kpplus": 78,
    "kp1": 79, "kp2": 80, "kp3": 81, "kp0": 82, "kpdot": 83,
    "kpenter": 96, "rightctrl": 97, "kpslash": 98, "rightalt": 100,
    "home": 102, "up": 103, "pageup": 104, "pgup": 104,
    "left": 105, "right": 106, "end": 107, "down": 108, "pagedown": 109, "pgdn": 109,
    "insert": 110, "delete": 111, "del": 111,
    "leftmeta": 125, "super": 125, "meta": 125, "win": 125, "rightmeta": 126,
    # Mouse buttons usable as hotkey triggers or chords
    "mouse1": BTN_LEFT, "mouseleft": BTN_LEFT,
    "mouse2": BTN_RIGHT, "mouseright": BTN_RIGHT,
    "mouse3": BTN_MIDDLE, "mousemiddle": BTN_MIDDLE,
    "mouse4": BTN_SIDE, "side": BTN_SIDE, "back": BTN_SIDE,
    "mouse5": BTN_EXTRA, "extra": BTN_EXTRA, "forward": BTN_EXTRA,
}

MODIFIER_EQUIVALENTS: dict[int, set[int]] = {
    29: {29, 97},    # Left/Right Ctrl
    97: {29, 97},
    42: {42, 54},    # Left/Right Shift
    54: {42, 54},
    56: {56, 100},   # Left/Right Alt
    100: {56, 100},
    125: {125, 126}, # Left/Right Super/Meta
    126: {125, 126},
}

# Kernel ioctl helpers for /dev/input/event* interrogation, CLOCK_MONOTONIC, & EVIOCGRAB probe
_IOC_WRITE = 1
_IOC_READ = 2


def _IOC(direction: int, ioc_type: str, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord(ioc_type) << 8) | nr


def EVIOCGNAME(length: int = 256) -> int:
    return _IOC(_IOC_READ, "E", 0x06, length)


def EVIOCGBIT(ev_type: int, length: int = 128) -> int:
    return _IOC(_IOC_READ, "E", 0x20 + ev_type, length)


EVIOCSCLOCKID = _IOC(_IOC_WRITE, "E", 0xA0, 4)
EVIOCGRAB = _IOC(_IOC_WRITE, "E", 0x90, 4)

# Wayland wire protocol constants
WL_DISPLAY_ID = 1
WL_DISPLAY_SYNC = 0
WL_DISPLAY_GET_REGISTRY = 1
WL_DISPLAY_EV_ERROR = 0
WL_DISPLAY_EV_DELETE_ID = 1

WL_REGISTRY_BIND = 0
WL_REGISTRY_EV_GLOBAL = 0

ZWLR_VIRTUAL_POINTER_MGR_INTERFACE = "zwlr_virtual_pointer_manager_v1"
MGR_CREATE_POINTER = 0
MGR_DESTROY = 1

PTR_MOTION = 0
PTR_MOTION_ABSOLUTE = 1
PTR_BUTTON = 2
PTR_AXIS = 3
PTR_FRAME = 4
PTR_AXIS_SOURCE = 5
PTR_AXIS_STOP = 6
PTR_AXIS_DISCRETE = 7
PTR_DESTROY = 8

AXIS_VERTICAL = 0
AXIS_HORIZONTAL = 1
AXIS_SOURCE_WHEEL = 0
SCROLL_UNITS_PER_NOTCH = 15.0

ZWP_VIRTUAL_KEYBOARD_MGR_INTERFACE = "zwp_virtual_keyboard_manager_v1"
VK_CREATE_KEYBOARD = 0
VK_KEYMAP = 0
VK_KEY = 1
VK_MODIFIERS = 2
VK_DESTROY = 3
XKB_KEYMAP_FORMAT_TEXT_V1 = 1
XKB_KEYCODE_OFFSET = 8  # XKB keycode = evdev keycode + 8

# Pre-compiled composite Wayland wire structs (single-syscall packet packing)
_WL_HDR_STRUCT = struct.Struct("<II")
_WL_MOTION_FRAME_STRUCT = struct.Struct("<IIIiiII")       # PTR_MOTION (20B) + PTR_FRAME (8B) = 28B
_WL_BUTTON_FRAME_STRUCT = struct.Struct("<IIIIIII")       # PTR_BUTTON (20B) + PTR_FRAME (8B) = 28B
_WL_KEY_MODS_STRUCT = struct.Struct("<IIIIIIIIIII")       # VK_KEY (20B) + VK_MODIFIERS (24B) = 44B
_WL_KEY_ONLY_STRUCT = struct.Struct("<IIIII")             # VK_KEY (20B)


class DuskyMacroError(RuntimeError):
    """Base error for DuskyMacro."""


class SafetyAbortError(DuskyMacroError):
    """Raised when a safety guard, session lock, or emergency abort stops execution."""


# =============================================================================
# SECTION 2: TOML SETTINGS, RUNTIME BEACON & PROFILE MANAGEMENT
# =============================================================================

DEFAULT_SETTINGS_TOML = """\
schema_version = 1

[paths]
profiles_dir = "profiles"
recordings_dir = "~/.config/dusky/settings/dusky_macros"
default_macro_file = "~/.config/dusky/settings/dusky_macros/quick.macro.json"

[hotkeys]
record_toggle = "f8"
play_toggle = "f9"
pause_toggle = "f10"
emergency_abort = "ctrl+shift+backspace"
profile_cycle_next = "ctrl+shift+f12"
profile_cycle_prev = "ctrl+shift+f11"

[timing]
spin_threshold_us = 1200
max_coarse_sleep_ms = 12.0
epoll_poll_timeout_ms = 10.0
hotplug_rescan_interval_s = 1.0
use_kernel_monotonic_ts = true
coalesce_mouse_move_us = 1000
pre_click_settle_us = 1500

[recording]
capture_mouse = true
capture_keyboard = true
strip_trigger_chords = true
ignore_virtual_devices = true
extra_ignored_device_keywords = []

[playback]
default_coordinate_mode = "window_relative"
scale_window_relative = false
click_at_current_cursor = false
background_window_keys = false
default_speed_multiplier = 1.0
default_repeat_count = 1
strict_window_match = false
auto_focus_target_window = true
abort_on_focus_loss = false
sync_every_button = true
sync_every_key = false

[safety]
abort_on_session_lock = true
block_on_auth_layer = true
block_on_launcher_layer = true
safety_check_interval_events = 16
notify_on_state_change = true
notification_timeout_ms = 2000

[profiles]
default_profile = "01_default"
auto_switch_by_window = true
"""

DEFAULT_PROFILE_01_TOML = """\
schema_version = 1

[profile]
id = "01_default"
name = "Default Desktop"
description = "Standard window-relative desktop & productivity macro profile"
enabled = true
priority = 0

[match]
window_class_regex = ""
window_title_regex = ""

[overrides.hotkeys]
record_toggle = "f8"
play_toggle = "f9"
pause_toggle = "f10"
emergency_abort = "ctrl+shift+backspace"

[overrides.playback]
coordinate_mode = "window_relative"
scale_window_relative = false
click_at_current_cursor = false
background_window_keys = false
speed_multiplier = 1.0
repeat_count = 1
strict_window_match = false
auto_focus_target_window = true
abort_on_focus_loss = false

[overrides.recording]
capture_mouse = true
capture_keyboard = true
coalesce_mouse_move_us = 1000

[[bindings]]
name = "Quick Slot 1"
hotkey = "f9"
macro_file = "quick.macro.json"
coordinate_mode = "window_relative"
speed_multiplier = 1.0
repeat_count = 1
"""


class RuntimeBeacon:
    """Manages cross-process fcntl.flock seat serialization ($XDG_RUNTIME_DIR/dusky_macro/seat.lock)
    and a live Waybar/status beacon ($XDG_RUNTIME_DIR/dusky_macro/state.json)."""

    @staticmethod
    def runtime_dir() -> Path:
        base = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}") / "dusky_macro"
        base.mkdir(parents=True, exist_ok=True)
        return base

    @classmethod
    def lock_path(cls) -> Path:
        return cls.runtime_dir() / "seat.lock"

    @classmethod
    def state_path(cls) -> Path:
        return cls.runtime_dir() / "state.json"

    @classmethod
    @contextlib.contextmanager
    def acquire_seat_lock(cls, blocking: bool = False) -> Any:
        fh = open(cls.lock_path(), "a+", encoding="utf-8")  # noqa: SIM115
        flags = fcntl.LOCK_EX if blocking else (fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            fcntl.flock(fh, flags)
        except BlockingIOError as exc:
            fh.close()
            raise DuskyMacroError("Another dusky_macro action is already active on the seat") from exc
        try:
            fh.seek(0)
            fh.truncate()
            fh.write(str(os.getpid()))
            fh.flush()
            yield fh
        finally:
            with contextlib.suppress(Exception):
                fcntl.flock(fh, fcntl.LOCK_UN)
            fh.close()

    @classmethod
    def write_state(
        cls,
        state: str,
        profile_id: str = "",
        macro_name: str = "",
    ) -> None:
        payload = {
            "pid": os.getpid(),
            "state": state,
            "profile": profile_id,
            "macro": macro_name,
            "updated_ns": _monotonic_ns(),
        }
        with contextlib.suppress(OSError):
            tmp = cls.state_path().with_suffix(".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            tmp.replace(cls.state_path())

    @classmethod
    def clear_state(cls) -> None:
        with contextlib.suppress(OSError):
            st = cls.read_state()
            if not st or st.get("pid") == os.getpid():
                cls.state_path().unlink(missing_ok=True)

    @classmethod
    def read_state(cls) -> dict[str, Any] | None:
        p = cls.state_path()
        if not p.is_file():
            return None
        with contextlib.suppress(Exception):
            return json.loads(p.read_text(encoding="utf-8"))
        return None


@dataclass(slots=True)
class MacroBinding:
    name: str
    hotkey: str
    macro_file: Path
    coordinate_mode: str | None = None
    speed_multiplier: float | None = None
    repeat_count: int | None = None
    click_at_current_cursor: bool | None = None
    background_window_keys: bool | None = None


@dataclass(slots=True)
class ProfileConfig:
    id: str
    name: str
    description: str
    enabled: bool
    priority: int
    source_path: Path
    window_class_regex: str = ""
    window_title_regex: str = ""
    _class_pattern: re.Pattern[str] | None = None
    _title_pattern: re.Pattern[str] | None = None
    hotkey_overrides: dict[str, Any] = field(default_factory=dict)
    playback_overrides: dict[str, Any] = field(default_factory=dict)
    recording_overrides: dict[str, Any] = field(default_factory=dict)
    bindings: list[MacroBinding] = field(default_factory=list)

    def compile_patterns(self) -> None:
        self._class_pattern = (
            re.compile(self.window_class_regex, re.IGNORECASE) if self.window_class_regex else None
        )
        self._title_pattern = (
            re.compile(self.window_title_regex, re.IGNORECASE) if self.window_title_regex else None
        )

    def matches_window(self, win_class: str, win_title: str) -> bool:
        if not self.enabled:
            return False
        if not self._class_pattern and not self._title_pattern:
            return False
        if self._class_pattern and not self._class_pattern.search(win_class or ""):
            return False
        if self._title_pattern and not self._title_pattern.search(win_title or ""):
            return False
        return True


@dataclass(slots=True)
class EffectiveConfig:
    """Resolved configuration after layering ProfileConfig overrides on top of SettingsManager."""
    profile_id: str
    profile_name: str
    record_hotkey: str
    play_hotkey: str
    pause_hotkey: str
    abort_hotkey: str
    cycle_next_hotkey: str
    cycle_prev_hotkey: str
    spin_threshold_ns: int
    max_coarse_sleep_s: float
    epoll_poll_timeout_s: float
    hotplug_rescan_interval_s: float
    use_kernel_monotonic_ts: bool
    coalesce_mouse_move_us: int
    pre_click_settle_ns: int
    capture_mouse: bool
    capture_keyboard: bool
    swap_mouse_buttons: str
    strip_trigger_chords: bool
    ignore_virtual_devices: bool
    extra_ignored_keywords: tuple[str, ...]
    coordinate_mode: str
    scale_window_relative: bool
    click_at_current_cursor: bool
    background_window_keys: bool
    speed_multiplier: float
    repeat_count: int
    strict_window_match: bool
    auto_focus_target_window: bool
    abort_on_focus_loss: bool
    sync_every_button: bool
    sync_every_key: bool
    abort_on_session_lock: bool
    block_on_auth_layer: bool
    block_on_launcher_layer: bool
    safety_check_interval_events: int
    notify_on_state_change: bool
    notification_timeout_ms: int
    default_macro_file: Path
    bindings: list[MacroBinding]


class SettingsManager:
    """Loads settings/settings.toml with hot-reload tracking and computes EffectiveConfig."""

    def __init__(self, root_dir: Path = SCRIPT_ROOT, settings_path: Path | None = None) -> None:
        self.root_dir = root_dir.expanduser().resolve()
        self.settings_path = (
            settings_path.expanduser().resolve()
            if settings_path
            else self.root_dir / "settings" / "settings.toml"
        )
        self._last_mtime_ns: int = 0
        self.raw: dict[str, Any] = {}
        self.profiles_dir: Path = self.root_dir / "profiles"
        self.recordings_dir: Path = Path("~/.config/dusky/settings/dusky_macros").expanduser()
        self.default_macro_file: Path = self.recordings_dir / "quick.macro.json"
        self.reload()

    def reload(self) -> None:
        import tomllib

        if self.settings_path.is_file():
            st = self.settings_path.stat()
            self._last_mtime_ns = st.st_mtime_ns
            self.raw = tomllib.loads(self.settings_path.read_text(encoding="utf-8"))
        else:
            self._last_mtime_ns = 0
            self.raw = tomllib.loads(DEFAULT_SETTINGS_TOML)

        paths_cfg = self.raw.get("paths", {})
        self.profiles_dir = self.resolve_path(paths_cfg.get("profiles_dir", "profiles"))
        rec_dir_raw = paths_cfg.get("recordings_dir", "~/.config/dusky/settings/dusky_macros")
        if rec_dir_raw == "recordings":
            rec_dir_raw = "~/.config/dusky/settings/dusky_macros"
        self.recordings_dir = self.resolve_path(rec_dir_raw)
        self.default_macro_file = self.resolve_macro_path(
            paths_cfg.get("default_macro_file", "quick.macro.json")
        )

    def reload_if_modified(self) -> bool:
        try:
            if self.settings_path.is_file():
                mtime = self.settings_path.stat().st_mtime_ns
                if mtime != self._last_mtime_ns:
                    self.reload()
                    return True
        except OSError:
            pass
        return False

    def resolve_path(self, path_str: str | Path) -> Path:
        p = Path(path_str).expanduser()
        if p.is_absolute():
            return p
        return (self.root_dir / p).resolve()

    def resolve_macro_path(self, path_str: str | Path) -> Path:
        """Resolves macro file paths so relative filenames or legacy 'recordings/...'
        paths always map cleanly inside self.recordings_dir (~/.config/dusky/settings/dusky_macros)."""
        p = Path(path_str).expanduser()
        if p.is_absolute():
            return p
        parts = p.parts
        if parts and parts[0] == "recordings":
            p = Path(*parts[1:]) if len(parts) > 1 else Path("quick.macro.json")
        return (self.recordings_dir / p).resolve()

    def ensure_directories_and_defaults(self) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        if not self.settings_path.is_file():
            self.settings_path.write_text(DEFAULT_SETTINGS_TOML, encoding="utf-8")
            self.reload()
        default_prof_path = self.profiles_dir / "01_default.toml"
        if not default_prof_path.is_file():
            default_prof_path.write_text(DEFAULT_PROFILE_01_TOML, encoding="utf-8")

    def build_effective(self, profile: ProfileConfig | None = None) -> EffectiveConfig:
        hk = dict(self.raw.get("hotkeys", {}))
        tm = dict(self.raw.get("timing", {}))
        rec = dict(self.raw.get("recording", {}))
        pb = dict(self.raw.get("playback", {}))
        sf = dict(self.raw.get("safety", {}))

        prof_id = "01_default"
        prof_name = "Default Desktop"
        bindings: list[MacroBinding] = []

        if profile is not None:
            prof_id = profile.id
            prof_name = profile.name
            hk.update({k: v for k, v in profile.hotkey_overrides.items() if v is not None})
            rec.update({k: v for k, v in profile.recording_overrides.items() if v is not None})
            pb_ov = profile.playback_overrides
            for key_map in (
                ("coordinate_mode", "default_coordinate_mode"),
                ("scale_window_relative", "scale_window_relative"),
                ("click_at_current_cursor", "click_at_current_cursor"),
                ("background_window_keys", "background_window_keys"),
                ("speed_multiplier", "default_speed_multiplier"),
                ("repeat_count", "default_repeat_count"),
                ("strict_window_match", "strict_window_match"),
                ("auto_focus_target_window", "auto_focus_target_window"),
                ("abort_on_focus_loss", "abort_on_focus_loss"),
            ):
                if key_map[0] in pb_ov:
                    pb[key_map[1]] = pb_ov[key_map[0]]
            if "coalesce_mouse_move_us" in rec:
                tm["coalesce_mouse_move_us"] = rec["coalesce_mouse_move_us"]
            bindings = list(profile.bindings)

        if not bindings:
            bindings = [
                MacroBinding(
                    name="Default Quick Slot",
                    hotkey=str(hk.get("play_toggle", "f9")),
                    macro_file=self.default_macro_file,
                )
            ]

        return EffectiveConfig(
            profile_id=prof_id,
            profile_name=prof_name,
            record_hotkey=str(hk.get("record_toggle", "f8")),
            play_hotkey=str(hk.get("play_toggle", "f9")),
            pause_hotkey=str(hk.get("pause_toggle", "f10")),
            abort_hotkey=str(hk.get("emergency_abort", "ctrl+shift+backspace")),
            cycle_next_hotkey=str(hk.get("profile_cycle_next", "ctrl+shift+f12")),
            cycle_prev_hotkey=str(hk.get("profile_cycle_prev", "ctrl+shift+f11")),
            spin_threshold_ns=int(tm.get("spin_threshold_us", 1200)) * 1000,
            max_coarse_sleep_s=float(tm.get("max_coarse_sleep_ms", 12.0)) / 1000.0,
            epoll_poll_timeout_s=float(tm.get("epoll_poll_timeout_ms", 10.0)) / 1000.0,
            hotplug_rescan_interval_s=float(tm.get("hotplug_rescan_interval_s", 1.0)),
            use_kernel_monotonic_ts=bool(tm.get("use_kernel_monotonic_ts", True)),
            coalesce_mouse_move_us=int(tm.get("coalesce_mouse_move_us", 1000)),
            pre_click_settle_ns=int(tm.get("pre_click_settle_us", 1500)) * 1000,
            capture_mouse=bool(rec.get("capture_mouse", True)),
            capture_keyboard=bool(rec.get("capture_keyboard", True)),
            swap_mouse_buttons=str(rec.get("swap_mouse_buttons", "auto")),
            strip_trigger_chords=bool(rec.get("strip_trigger_chords", True)),
            ignore_virtual_devices=bool(rec.get("ignore_virtual_devices", True)),
            extra_ignored_keywords=tuple(str(x).lower() for x in rec.get("extra_ignored_device_keywords", [])),
            coordinate_mode=str(pb.get("default_coordinate_mode", "window_relative")),
            scale_window_relative=bool(pb.get("scale_window_relative", False)),
            click_at_current_cursor=bool(pb.get("click_at_current_cursor", False)),
            background_window_keys=bool(pb.get("background_window_keys", False)),
            speed_multiplier=float(pb.get("default_speed_multiplier", 1.0)),
            repeat_count=int(pb.get("default_repeat_count", 1)),
            strict_window_match=bool(pb.get("strict_window_match", False)),
            auto_focus_target_window=bool(pb.get("auto_focus_target_window", True)),
            abort_on_focus_loss=bool(pb.get("abort_on_focus_loss", False)),
            sync_every_button=bool(pb.get("sync_every_button", True)),
            sync_every_key=bool(pb.get("sync_every_key", False)),
            abort_on_session_lock=bool(sf.get("abort_on_session_lock", True)),
            block_on_auth_layer=bool(sf.get("block_on_auth_layer", True)),
            block_on_launcher_layer=bool(sf.get("block_on_launcher_layer", True)),
            safety_check_interval_events=max(1, int(sf.get("safety_check_interval_events", 16))),
            notify_on_state_change=bool(sf.get("notify_on_state_change", True)),
            notification_timeout_ms=int(sf.get("notification_timeout_ms", 2000)),
            default_macro_file=self.default_macro_file,
            bindings=bindings,
        )


class ProfileManager:
    """Loads, validates, matches, hot-reloads, and cycles profiles from profiles/*.toml."""

    def __init__(self, settings: SettingsManager) -> None:
        self.settings = settings
        self.profiles: dict[str, ProfileConfig] = {}
        self.ordered_ids: list[str] = []
        self._mtimes: dict[Path, int] = {}
        self.reload()

    def reload(self) -> None:
        import tomllib

        self.profiles.clear()
        self.ordered_ids.clear()
        self._mtimes.clear()
        if not self.settings.profiles_dir.is_dir():
            return

        loaded: list[ProfileConfig] = []
        for path in sorted(self.settings.profiles_dir.glob("*.toml")):
            try:
                st = path.stat()
                self._mtimes[path] = st.st_mtime_ns
                data = tomllib.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                print(f"dusky_macro: warning: failed to parse profile {path}: {exc}", file=sys.stderr)
                continue
            p_sec = data.get("profile", {})
            prof_id = str(p_sec.get("id") or path.stem)
            prof_name = str(p_sec.get("name") or prof_id)
            desc = str(p_sec.get("description") or "")
            enabled = bool(p_sec.get("enabled", True))
            priority = int(p_sec.get("priority", 0))

            m_sec = data.get("match", {})
            cls_rx = str(m_sec.get("window_class_regex") or "")
            ttl_rx = str(m_sec.get("window_title_regex") or "")

            ov_sec = data.get("overrides", {})
            hk_ov = dict(ov_sec.get("hotkeys", {}))
            pb_ov = dict(ov_sec.get("playback", {}))
            rec_ov = dict(ov_sec.get("recording", {}))

            bindings: list[MacroBinding] = []
            for b_raw in data.get("bindings", []):
                if not isinstance(b_raw, dict) or not b_raw.get("hotkey") or not b_raw.get("macro_file"):
                    continue
                bindings.append(
                    MacroBinding(
                        name=str(b_raw.get("name") or b_raw["hotkey"]),
                        hotkey=str(b_raw["hotkey"]),
                        macro_file=self.settings.resolve_macro_path(str(b_raw["macro_file"])),
                        coordinate_mode=b_raw.get("coordinate_mode"),
                        speed_multiplier=float(b_raw["speed_multiplier"]) if "speed_multiplier" in b_raw else None,
                        repeat_count=int(b_raw["repeat_count"]) if "repeat_count" in b_raw else None,
                        click_at_current_cursor=(
                            bool(b_raw["click_at_current_cursor"]) if "click_at_current_cursor" in b_raw else None
                        ),
                        background_window_keys=(
                            bool(b_raw["background_window_keys"]) if "background_window_keys" in b_raw else None
                        ),
                    )
                )

            prof = ProfileConfig(
                id=prof_id,
                name=prof_name,
                description=desc,
                enabled=enabled,
                priority=priority,
                source_path=path,
                window_class_regex=cls_rx,
                window_title_regex=ttl_rx,
                hotkey_overrides=hk_ov,
                playback_overrides=pb_ov,
                recording_overrides=rec_ov,
                bindings=bindings,
            )
            prof.compile_patterns()
            loaded.append(prof)

        loaded.sort(key=lambda p: (-p.priority, p.source_path.name))
        for p in loaded:
            self.profiles[p.id] = p
            self.ordered_ids.append(p.id)

    def reload_if_modified(self) -> bool:
        changed = self.settings.reload_if_modified()
        if not self.settings.profiles_dir.is_dir():
            return changed
        current_files = sorted(self.settings.profiles_dir.glob("*.toml"))
        if set(current_files) != set(self._mtimes.keys()):
            self.reload()
            return True
        for p in current_files:
            try:
                if p.stat().st_mtime_ns != self._mtimes.get(p):
                    self.reload()
                    return True
            except OSError:
                continue
        if changed:
            self.reload()
        return changed

    def get(self, name_or_id: str | None) -> ProfileConfig | None:
        if not name_or_id:
            default_id = str(self.settings.raw.get("profiles", {}).get("default_profile", "01_default"))
            return self.profiles.get(default_id) or (
                self.profiles[self.ordered_ids[0]] if self.ordered_ids else None
            )
        if name_or_id in self.profiles:
            return self.profiles[name_or_id]
        for p in self.profiles.values():
            if p.source_path.stem == name_or_id or p.name.lower() == name_or_id.lower():
                return p
        raise DuskyMacroError(
            f"Profile {name_or_id!r} not found in {self.settings.profiles_dir} "
            f"(available: {list(self.profiles.keys())})"
        )

    def resolve_for_window(self, win_class: str, win_title: str) -> ProfileConfig | None:
        for pid in self.ordered_ids:
            prof = self.profiles[pid]
            if prof.matches_window(win_class, win_title):
                return prof
        return self.get(None)

    def enabled_profiles_sorted_by_id(self) -> list[ProfileConfig]:
        return sorted((p for p in self.profiles.values() if p.enabled), key=lambda p: p.id)


# =============================================================================
# SECTION 3: LIBXKBCOMMON CTYPES BRIDGE (XkbBridge)
# =============================================================================

class _XkbRuleNames(ctypes.Structure):
    _fields_ = [
        ("rules", ctypes.c_char_p),
        ("model", ctypes.c_char_p),
        ("layout", ctypes.c_char_p),
        ("variant", ctypes.c_char_p),
        ("options", ctypes.c_char_p),
    ]


_LEVEL_MODS = ("", "SHIFT", "MOD5", "SHIFT MOD5")
_CONTROL_CHORDS: dict[str, str] = {
    "\n": "Return",
    "\r": "Return",
    "\t": "Tab",
    "\b": "BackSpace",
    "\x1b": "Escape",
    "\x7f": "Delete",
}


class XkbBridge:
    """Interfaces with libxkbcommon.so via ctypes to compile full hardware keymaps,
    track depressed/latched/locked modifier masks for zwp_virtual_keyboard_v1,
    resolve keycodes and characters to level-0 base keysyms for Hyprland's sendshortcut,
    and build dynamic Unicode keymaps."""

    XKB_KEY_UP = 0
    XKB_KEY_DOWN = 1
    XKB_STATE_MODS_DEPRESSED = 1
    XKB_STATE_MODS_LATCHED = 2
    XKB_STATE_MODS_LOCKED = 4
    XKB_STATE_LAYOUT_EFFECTIVE = 64

    def __init__(self, layout: str = "us", variant: str = "", options: str = "") -> None:
        self.layout = layout or "us"
        self.variant = variant or ""
        self.options = options or ""
        self._lib = self._load_xkb()
        self._libc = self._load_libc()
        self._ctx: int | None = None
        self._keymap: int | None = None
        self._state: int | None = None
        self._keymap_bytes: bytes = b""
        self._evdev_to_base_sym: dict[int, str] = {}
        self._char_to_chord: dict[str, tuple[str, str]] = {}
        self._init_keymap()

    @staticmethod
    def _load_xkb() -> ctypes.CDLL:
        path = ctypes.util.find_library("xkbcommon")
        if not path:
            raise DuskyMacroError("libxkbcommon.so not found; ensure libxkbcommon is installed")
        lib = ctypes.CDLL(path)
        lib.xkb_context_new.restype = ctypes.c_void_p
        lib.xkb_context_new.argtypes = [ctypes.c_int]
        lib.xkb_context_unref.restype = None
        lib.xkb_context_unref.argtypes = [ctypes.c_void_p]

        lib.xkb_keymap_new_from_names.restype = ctypes.c_void_p
        lib.xkb_keymap_new_from_names.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(_XkbRuleNames), ctypes.c_int
        ]
        lib.xkb_keymap_get_as_string.restype = ctypes.c_void_p
        lib.xkb_keymap_get_as_string.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.xkb_keymap_unref.restype = None
        lib.xkb_keymap_unref.argtypes = [ctypes.c_void_p]
        lib.xkb_keymap_min_keycode.restype = ctypes.c_uint32
        lib.xkb_keymap_min_keycode.argtypes = [ctypes.c_void_p]
        lib.xkb_keymap_max_keycode.restype = ctypes.c_uint32
        lib.xkb_keymap_max_keycode.argtypes = [ctypes.c_void_p]
        lib.xkb_keymap_num_levels_for_key.restype = ctypes.c_uint32
        lib.xkb_keymap_num_levels_for_key.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]
        lib.xkb_keymap_key_get_syms_by_level.restype = ctypes.c_int
        lib.xkb_keymap_key_get_syms_by_level.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.POINTER(ctypes.c_uint32)),
        ]

        lib.xkb_state_new.restype = ctypes.c_void_p
        lib.xkb_state_new.argtypes = [ctypes.c_void_p]
        lib.xkb_state_unref.restype = None
        lib.xkb_state_unref.argtypes = [ctypes.c_void_p]
        lib.xkb_state_update_key.restype = ctypes.c_int
        lib.xkb_state_update_key.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int]
        lib.xkb_state_serialize_mods.restype = ctypes.c_uint32
        lib.xkb_state_serialize_mods.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.xkb_state_serialize_layout.restype = ctypes.c_uint32
        lib.xkb_state_serialize_layout.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.xkb_state_key_get_one_sym.restype = ctypes.c_uint32
        lib.xkb_state_key_get_one_sym.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        lib.xkb_keysym_get_name.restype = ctypes.c_int
        lib.xkb_keysym_get_name.argtypes = [ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t]
        lib.xkb_keysym_to_utf32.restype = ctypes.c_uint32
        lib.xkb_keysym_to_utf32.argtypes = [ctypes.c_uint32]
        return lib

    @staticmethod
    def _load_libc() -> ctypes.CDLL:
        path = ctypes.util.find_library("c") or "libc.so.6"
        libc = ctypes.CDLL(path)
        libc.free.restype = None
        libc.free.argtypes = [ctypes.c_void_p]
        return libc

    def _init_keymap(self) -> None:
        self._ctx = self._lib.xkb_context_new(0)
        if not self._ctx:
            raise DuskyMacroError("xkb_context_new failed")
        names = _XkbRuleNames(
            b"evdev",
            b"pc105",
            self.layout.encode("utf-8"),
            self.variant.encode("utf-8"),
            self.options.encode("utf-8") if self.options else None,
        )
        self._keymap = self._lib.xkb_keymap_new_from_names(self._ctx, ctypes.byref(names), 0)
        if not self._keymap:
            raise DuskyMacroError(f"xkb_keymap_new_from_names failed for layout={self.layout!r}")
        ptr = self._lib.xkb_keymap_get_as_string(self._keymap, XKB_KEYMAP_FORMAT_TEXT_V1)
        if not ptr:
            raise DuskyMacroError("xkb_keymap_get_as_string returned NULL")
        try:
            self._keymap_bytes = ctypes.string_at(ptr) + b"\x00"
        finally:
            self._libc.free(ptr)
        self._state = self._lib.xkb_state_new(self._keymap)
        if not self._state:
            raise DuskyMacroError("xkb_state_new failed")

        # Build level-0 base key table and character->(mods, base_key) chord table for sendshortcut
        min_kc = int(self._lib.xkb_keymap_min_keycode(self._keymap))
        max_kc = int(self._lib.xkb_keymap_max_keycode(self._keymap))
        buf = ctypes.create_string_buffer(64)
        for kc in range(min_kc, max_kc + 1):
            base_sym_name: str | None = None
            num_levels = min(int(self._lib.xkb_keymap_num_levels_for_key(self._keymap, kc, 0)), 4)
            for lvl in range(num_levels):
                syms = ctypes.POINTER(ctypes.c_uint32)()
                if self._lib.xkb_keymap_key_get_syms_by_level(self._keymap, kc, 0, lvl, ctypes.byref(syms)) < 1:
                    continue
                self._lib.xkb_keysym_get_name(syms[0], buf, 64)
                sym_name = buf.value.decode("utf-8", errors="replace")
                if lvl == 0 and sym_name:
                    base_sym_name = sym_name
                    self._evdev_to_base_sym[kc - XKB_KEYCODE_OFFSET] = base_sym_name
                cp = int(self._lib.xkb_keysym_to_utf32(syms[0]))
                if cp and base_sym_name and chr(cp) not in self._char_to_chord:
                    self._char_to_chord[chr(cp)] = (_LEVEL_MODS[lvl], base_sym_name)

    @property
    def keymap_bytes(self) -> bytes:
        return self._keymap_bytes

    def reset_state(self) -> None:
        if self._state:
            self._lib.xkb_state_unref(self._state)
            self._state = None
        if self._keymap:
            self._state = self._lib.xkb_state_new(self._keymap)

    def update_key_and_serialize_mods(self, evdev_code: int, pressed: bool) -> tuple[int, int, int, int]:
        if not self._state:
            return 0, 0, 0, 0
        xkb_code = evdev_code + XKB_KEYCODE_OFFSET
        direction = self.XKB_KEY_DOWN if pressed else self.XKB_KEY_UP
        self._lib.xkb_state_update_key(self._state, xkb_code, direction)
        depressed = int(self._lib.xkb_state_serialize_mods(self._state, self.XKB_STATE_MODS_DEPRESSED))
        latched = int(self._lib.xkb_state_serialize_mods(self._state, self.XKB_STATE_MODS_LATCHED))
        locked = int(self._lib.xkb_state_serialize_mods(self._state, self.XKB_STATE_MODS_LOCKED))
        group = int(self._lib.xkb_state_serialize_layout(self._state, self.XKB_STATE_LAYOUT_EFFECTIVE))
        return depressed, latched, locked, group

    def keycode_to_symbol(self, evdev_code: int) -> str:
        if not self._state:
            return f"code_{evdev_code}"
        sym = self._lib.xkb_state_key_get_one_sym(self._state, evdev_code + XKB_KEYCODE_OFFSET)
        if not sym:
            return f"code_{evdev_code}"
        buf = ctypes.create_string_buffer(64)
        self._lib.xkb_keysym_get_name(sym, buf, 64)
        return buf.value.decode("utf-8", errors="replace") or f"code_{evdev_code}"

    def keycode_to_base_symbol(self, evdev_code: int) -> str | None:
        """Returns the unshifted level-0 XKB symbol name for an evdev keycode (used by sendshortcut)."""
        return self._evdev_to_base_sym.get(evdev_code)

    def char_to_sendshortcut_chord(self, ch: str) -> tuple[str, str] | None:
        """Returns (mods, base_key_name) for Hyprland's sendshortcut, or None if not on layout."""
        if ch in _CONTROL_CHORDS:
            return ("", _CONTROL_CHORDS[ch])
        return self._char_to_chord.get(ch)

    @staticmethod
    def build_unicode_keymap(chars: list[str]) -> bytes:
        keycodes, symbols = [], []
        for i, ch in enumerate(chars):
            kc = XKB_KEYCODE_OFFSET + 1 + i
            keycodes.append(f"    <K{i}> = {kc};")
            if ch in ("\n", "\r"):
                sym = "Return"
            elif ch == "\t":
                sym = "Tab"
            else:
                sym = f"U{ord(ch):04X}"
            symbols.append(f"    key <K{i}> {{ [ {sym} ] }};")
        km = (
            "xkb_keymap {\n"
            "  xkb_keycodes {\n"
            f"    minimum = {XKB_KEYCODE_OFFSET};\n"
            f"    maximum = {XKB_KEYCODE_OFFSET + len(chars) + 1};\n"
            + "\n".join(keycodes)
            + "\n  };\n"
            '  xkb_types { include "complete" };\n'
            '  xkb_compat { include "complete" };\n'
            '  xkb_symbols "(unnamed)" {\n'
            + "\n".join(symbols)
            + "\n  };\n"
            "};\n\x00"
        )
        return km.encode("utf-8")

    def close(self) -> None:
        if self._state:
            self._lib.xkb_state_unref(self._state)
            self._state = None
        if self._keymap:
            self._lib.xkb_keymap_unref(self._keymap)
            self._keymap = None
        if self._ctx:
            self._lib.xkb_context_unref(self._ctx)
            self._ctx = None

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.close()


# =============================================================================
# SECTION 4: DIRECT HYPRLAND IPC (.socket.sock & .socket2.sock) & TOPOLOGY
# =============================================================================

_LUA_SAFE_BYTES = frozenset(range(0x20, 0x7F)) - {0x22, 0x5C}
_LUA_FULLSCREEN_MODES = {"0": "fullscreen", "1": "maximized"}

LAYER_LEVELS = ("background", "bottom", "top", "overlay")
AUTH_LAYER_MARKS = ("polkit", "dusky_polkit", "org.dusky.polkit")
_LAYER_KINDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "launcher",
        (
            "wofi", "rofi", "fuzzel", "tofi", "anyrun", "walker", "launcher",
            "omarchy-menu", "omarchy-clipboard", "omarchy-emojis",
            "omarchy-image-selector", "omarchy-keyboard-panel", "omarchy-reminders",
            "omarchy-network-qr", "omarchy-network-speedtest", "omarchy-disk-speedtest",
        ),
    ),
    ("bar", ("waybar", "hyprpanel", "ags-", "bar", "omarchy-bar")),
    ("notifications", ("mako", "dunst", "swaync", "notification", "omarchy-notifications")),
    ("lock-preview", ("omarchy-lock-preview",)),
    ("lock", ("hyprlock", "swaylock", "lockscreen")),
    ("osk", ("wvkbd", "squeekboard", "osk")),
)

# Client window classes and titles that represent privilege/authentication dialogs
AUTH_WINDOW_CLASSES = (
    "dusky_polkit",
    "org.dusky.polkit",
    "hyprpolkitagent",
    "polkit-gnome-authentication-agent-1",
    "polkit-kde-authentication-agent-1",
    "lxqt-policykit-agent",
    "mate-polkit",
    "gcr-prompter",
    "pinentry",
    "hyprland-dialog",
    "hyprland-share-picker",
)

AUTH_WINDOW_TITLES = (
    "dusky polkit",
    "authenticate",
    "authentication required",
)


def classify_layer_namespace(namespace: str) -> str:
    low = namespace.lower()
    if any(mark in low for mark in AUTH_LAYER_MARKS):
        return "auth"
    for kind, prefixes in _LAYER_KINDS:
        if any(low.startswith(p) for p in prefixes):
            return kind
    return "other"


def lua_escape_str(text: str) -> str:
    if "\0" in text:
        raise DuskyMacroError("NUL byte not permitted in Hyprland IPC payload")
    body = "".join(
        chr(b) if b in _LUA_SAFE_BYTES else f"\\{b:03d}" for b in text.encode("utf-8")
    )
    return f'"{body}"'


def workspace_ref(ws: dict[str, Any] | None) -> int | str | None:
    if not ws:
        return None
    ref = ws["id"] if "id" in ws else ws.get("name")
    return ref or None


def logical_monitor_rect(m: dict[str, Any]) -> tuple[int, int, int, int]:
    scale = float(m.get("scale", 1.0)) or 1.0
    w = m.get("width", 0) / scale
    h = m.get("height", 0) / scale
    if int(m.get("transform", 0)) % 2 == 1:
        w, h = h, w
    return int(m.get("x", 0)), int(m.get("y", 0)), round(w), round(h)


class HyprlandSocketClient:
    """Direct AF_UNIX socket client for Hyprland's .socket.sock with complete 0.56+/0.57+ Lua
    dispatcher translations (including hl.dsp.send_shortcut), fast-path movecursor, and notify."""

    LUA = "lua"
    HYPRLANG = "hyprlang"

    def __init__(self, timeout_s: float = 1.0) -> None:
        self._timeout_s = timeout_s
        self.socket_path, self.socket2_path = self._discover_sockets()
        self._provider: str | None = None
        self._last_notify_ns: int = 0
        self._last_notify_msg: str = ""

    @staticmethod
    def _discover_sockets() -> tuple[Path, Path]:
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "").strip()
        if not sig or Path(sig).name != sig:
            hypr_dir = Path(runtime_dir) / "hypr"
            if hypr_dir.is_dir():
                for candidate in sorted(hypr_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                    s1 = candidate / ".socket.sock"
                    s2 = candidate / ".socket2.sock"
                    if s1.is_socket():
                        return s1, s2
            raise DuskyMacroError(
                "HYPRLAND_INSTANCE_SIGNATURE is not set or valid, and no active Hyprland socket found"
            )
        base = Path(runtime_dir) / "hypr" / sig
        s1 = base / ".socket.sock"
        s2 = base / ".socket2.sock"
        if not s1.exists():
            raise DuskyMacroError(f"Hyprland IPC socket does not exist: {s1}")
        return s1, s2

    def send_raw(self, payload: bytes) -> str:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(self._timeout_s)
            sock.connect(str(self.socket_path))
            sock.sendall(payload)
            chunks: list[bytes] = []
            while True:
                data = sock.recv(16384)
                if not data:
                    break
                chunks.append(data)
        return b"".join(chunks).decode("utf-8", errors="replace").strip()

    def query_json(self, command: str) -> Any:
        out = self.send_raw(f"j/{command}".encode("utf-8"))
        try:
            return json.loads(out)
        except json.JSONDecodeError as exc:
            raise DuskyMacroError(f"Unparseable JSON from j/{command}: {out[:160]!r}") from exc

    def batch_query_json(self, commands: list[str]) -> list[Any]:
        spec = "[[BATCH]]" + " ; ".join(f"j/{c}" for c in commands)
        out = self.send_raw(spec.encode("utf-8"))
        decoder = json.JSONDecoder()
        results: list[Any] = []
        i, n = 0, len(out)
        while i < n:
            while i < n and out[i].isspace():
                i += 1
            if i >= n:
                break
            val, i = decoder.raw_decode(out, i)
            results.append(val)
        return results

    def cursor_pos_fast(self) -> tuple[int, int]:
        raw = self.send_raw(b"cursorpos")
        parts = raw.split(",")
        if len(parts) == 2:
            return int(parts[0].strip()), int(parts[1].strip())
        data = self.query_json("cursorpos")
        return int(data["x"]), int(data["y"])

    def is_locked(self) -> bool:
        with contextlib.suppress(Exception):
            data = self.query_json("locked")
            if isinstance(data, dict):
                return bool(data.get("locked", False))
        return False

    def is_left_handed(self) -> bool:
        """Checks whether Hyprland has mouse handedness reversed (input:left_handed = true)."""
        try:
            res = self.query_json("getoption input:left_handed")
            return bool(res.get("bool", False) or res.get("int", 0) == 1)
        except Exception:
            return False

    def active_keyboard_layout(self) -> tuple[str, str, str]:
        with contextlib.suppress(Exception):
            devices = self.query_json("devices")
            keyboards = devices.get("keyboards", [])
            kb = next((k for k in keyboards if k.get("main")), keyboards[0] if keyboards else {})
            layout = (kb.get("layout") or "us").split(",")[0]
            variant = (kb.get("variant") or "").split(",")[0]
            options = kb.get("options") or ""
            return layout, variant, options
        return "us", "", ""

    def layout_group(self) -> tuple[int, int]:
        """Returns (active_layout_index, total_layout_count) of main keyboard.
        sendshortcut requires index == 0 because Hyprland resolves keys on active layout
        but delivers on layout group 0."""
        with contextlib.suppress(Exception):
            devices = self.query_json("devices")
            keyboards = devices.get("keyboards", [])
            kb = next((k for k in keyboards if k.get("main")), keyboards[0] if keyboards else {})
            count = len((kb.get("layout") or "us").split(","))
            return int(kb.get("active_layout_index") or 0), count
        return 0, 1

    def config_provider(self) -> str:
        if self._provider is None:
            try:
                out = self.send_raw(b"j/status")
                data = json.loads(out)
                self._provider = self.LUA if data.get("configProvider") == self.LUA else self.HYPRLANG
            except Exception:
                return self.HYPRLANG
        return self._provider

    def _format_dispatch(self, prov: str, name: str, *args: str) -> bytes:
        if prov == self.LUA:
            if name == "exec":
                expr = f"hl.dsp.exec_cmd({lua_escape_str(args[0])})"
            elif name == "movecursor":
                expr = f"hl.dsp.cursor.move({{ x = {int(args[0])}, y = {int(args[1])} }})"
            elif name == "focuswindow":
                expr = f"hl.dsp.focus({{ window = {lua_escape_str(args[0])} }})"
            elif name == "workspace":
                expr = f"hl.dsp.focus({{ workspace = {lua_escape_str(args[0])} }})"
            elif name == "closewindow":
                expr = f"hl.dsp.window.close({{ window = {lua_escape_str(args[0])} }})"
            elif name == "movetoworkspacesilent":
                ws, _, win = args[0].partition(",")
                target = f", window = {lua_escape_str(win)}" if win else ""
                expr = f"hl.dsp.window.move({{ workspace = {lua_escape_str(ws)}{target}, follow = false }})"
            elif name == "fullscreen":
                mode_arg = args[0] if args else "0"
                mode_name = _LUA_FULLSCREEN_MODES.get(mode_arg, "fullscreen")
                expr = f'hl.dsp.window.fullscreen({{ mode = "{mode_name}", action = "toggle" }})'
            elif name == "togglefloating":
                win_arg = args[0] if args else ""
                target = f", window = {lua_escape_str(win_arg)}" if win_arg else ""
                expr = f'hl.dsp.window.float({{ action = "toggle"{target} }})'
            elif name == "tagwindow":
                tag = args[0]
                win_arg = args[1] if len(args) > 1 else ""
                target = f", window = {lua_escape_str(win_arg)}" if win_arg else ""
                expr = f"hl.dsp.window.tag({{ tag = {lua_escape_str(tag)}{target} }})"
            elif name == "sendshortcut":
                mods, key, win = (p.strip() for p in args[0].split(",", 2))
                expr = (
                    f"hl.dsp.send_shortcut({{ mods = {lua_escape_str(mods)}, "
                    f"key = {lua_escape_str(key)}, window = {lua_escape_str(win)} }})"
                )
            else:
                raise DuskyMacroError(f"Unsupported Lua dispatcher translation for {name!r}")
            return f"dispatch {expr}".encode("utf-8")
        joined = " ".join([name, *args]).strip()
        return f"dispatch {joined}".encode("utf-8")

    def dispatch(self, name: str, *args: str) -> None:
        prov = self.config_provider()
        payload = self._format_dispatch(prov, name, *args)
        reply = self.send_raw(payload)
        if not reply.lower().startswith("ok"):
            self._provider = None
            now_prov = self.config_provider()
            if now_prov != prov:
                reply = self.send_raw(self._format_dispatch(now_prov, name, *args))
                if reply.lower().startswith("ok"):
                    return
            raise DuskyMacroError(f"Hyprland dispatch {name} {args} failed: {reply}")

    def send_shortcut(self, mods: str, key: str, address: str) -> None:
        """Atomically sends a key with modifiers to a window address without stealing focus.
        Under Hyprland 0.57+ Lua config provider, dispatches:
        hl.dsp.send_shortcut({ mods = "MODS", key = "KEY", window = "address:0x..." })"""
        addr = address if address.startswith("0x") else f"0x{address}"
        self.dispatch("sendshortcut", f"{mods}, {key}, address:{addr}")

    def move_cursor(self, x: int | float, y: int | float) -> None:
        ix, iy = int(round(x)), int(round(y))
        prov = self.config_provider()
        if prov == self.LUA:
            payload = f"dispatch hl.dsp.cursor.move({{ x = {ix}, y = {iy} }})".encode("ascii")
        else:
            payload = f"dispatch movecursor {ix} {iy}".encode("ascii")
        reply = self.send_raw(payload)
        if not reply.lower().startswith("ok"):
            self.dispatch("movecursor", str(ix), str(iy))

    def notify(self, message: str, timeout_ms: int = 2000, icon: int = -1, color: str = "0") -> None:
        now_ns = _monotonic_ns()
        if message == self._last_notify_msg and (now_ns - self._last_notify_ns) < 150_000_000:
            return
        self._last_notify_ns = now_ns
        self._last_notify_msg = message
        with contextlib.suppress(Exception):
            clean = message.replace("\n", " ").strip()
            self.send_raw(f"notify {icon} {int(timeout_ms)} {color} {clean}".encode("utf-8"))


class DesktopTopology:
    """Maintains desktop monitor geometry, normalized client window properties,
    layer-shell & authentication guards (including dusky_polkit / org.dusky.polkit),
    and state-first .socket2.sock event waiting."""

    _SCHEMAS: dict[str, tuple[tuple[str, ...], int]] = {
        "openwindow": (("address", "workspace", "class", "title"), 3),
        "closewindow": (("address",), 0),
        "movewindow": (("address", "workspace"), 1),
        "workspace": (("name",), 0),
        "activespecial": (("name", "monitor"), 1),
        "activewindowv2": (("address",), 0),
        "windowtitlev2": (("address", "title"), 1),
        "openlayer": (("namespace",), 0),
        "closelayer": (("namespace",), 0),
    }

    def __init__(
        self,
        ipc: HyprlandSocketClient,
        on_active_window_change: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.ipc = ipc
        self.on_active_window_change = on_active_window_change
        self._lock = threading.RLock()
        self.monitors: list[dict[str, Any]] = []
        self.active_window: dict[str, Any] = {}
        self.layers: list[dict[str, Any]] = []
        self._clients: list[dict[str, Any]] = []
        self._stop_event = threading.Event()
        self._event_thread: threading.Thread | None = None
        self._event_cond = threading.Condition(self._lock)
        self._recent_events: list[tuple[float, str, dict[str, str]]] = []
        self.refresh_full_snapshot()

    def refresh_full_snapshot(self) -> None:
        try:
            monitors, clients, active, layers_raw = self.ipc.batch_query_json(
                ["monitors", "clients", "activewindow", "layers"]
            )
        except Exception:
            return
        with self._lock:
            self.monitors = [
                {
                    "name": m.get("name", ""),
                    "logical_geometry": list(logical_monitor_rect(m)),
                    "scale": float(m.get("scale", 1.0)),
                    "transform": int(m.get("transform", 0)),
                    "active_workspace": workspace_ref(m.get("activeWorkspace")),
                    "special_workspace": workspace_ref(m.get("specialWorkspace")),
                }
                for m in (monitors or [])
            ]
            self.active_window = (
                self._normalize_window(active)
                if isinstance(active, dict) and active.get("address")
                else {}
            )
            self.layers = self._parse_layers(layers_raw or {})
            self._clients = [self._normalize_window(c) for c in (clients or []) if c.get("mapped", True)]

    def refresh_active_window(self) -> dict[str, Any]:
        changed = False
        win_snapshot: dict[str, Any] = {}
        with contextlib.suppress(Exception):
            active = self.ipc.query_json("activewindow")
            with self._lock:
                prev_addr = self.active_window.get("address")
                prev_class = self.active_window.get("class")
                self.active_window = (
                    self._normalize_window(active)
                    if isinstance(active, dict) and active.get("address")
                    else {}
                )
                win_snapshot = dict(self.active_window)
                if win_snapshot.get("address") != prev_addr or win_snapshot.get("class") != prev_class:
                    changed = True
        if changed and self.on_active_window_change is not None:
            with contextlib.suppress(Exception):
                self.on_active_window_change(win_snapshot)
        with self._lock:
            return dict(self.active_window)

    @staticmethod
    def _normalize_window(w: dict[str, Any]) -> dict[str, Any]:
        addr = str(w.get("address", ""))
        if addr and not addr.startswith("0x"):
            addr = "0x" + addr
        at = w.get("at") or [0, 0]
        size = w.get("size") or [0, 0]
        fs_val = w.get("fullscreen", 0)
        fs_int = int(fs_val) if isinstance(fs_val, int | bool) else 0
        return {
            "address": addr,
            "class": w.get("class", ""),
            "title": w.get("title", ""),
            "workspace": workspace_ref(w.get("workspace")),
            "rect": [int(at[0]), int(at[1]), int(size[0]), int(size[1])],
            "floating": bool(w.get("floating", False)),
            "fullscreen": fs_int == 2 or (isinstance(fs_val, bool) and fs_val),
            "maximized": fs_int == 1,
            "pinned": bool(w.get("pinned", False)),
            "hidden": bool(w.get("hidden", False)),
            "pid": int(w.get("pid", 0) or 0),
            "focus_history_id": int(w.get("focusHistoryID", 9999) if "focusHistoryID" in w else 9999),
            "xwayland": bool(w.get("xwayland", False)),
        }

    @staticmethod
    def _parse_layers(raw: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for mon_name, entry in raw.items():
            for lvl_id, surfaces in (entry.get("levels") or {}).items():
                lvl = int(lvl_id)
                if lvl == 0:
                    continue
                for s in surfaces or []:
                    ns = str(s.get("namespace", ""))
                    out.append({
                        "namespace": ns,
                        "kind": classify_layer_namespace(ns),
                        "level": LAYER_LEVELS[lvl] if lvl < 4 else str(lvl),
                        "monitor": mon_name,
                    })
        return out

    def has_blocking_overlay(self, block_auth: bool = True, block_launcher: bool = True) -> str | None:
        """Checks active window class/title and visible clients/layers for authentication dialogs
        (including dusky_polkit / org.dusky.polkit / 'Dusky Polkit') or launchers."""
        with self._lock:
            if block_auth:
                win_cls = (self.active_window.get("class") or "").lower()
                win_ttl = (self.active_window.get("title") or "").lower()
                if any(auth_cls in win_cls for auth_cls in AUTH_WINDOW_CLASSES):
                    return f"authentication window ({self.active_window.get('class')})"
                if any(auth_ttl in win_ttl for auth_ttl in AUTH_WINDOW_TITLES):
                    return f"authentication window ({self.active_window.get('title')})"
                # Also check if any mapped floating authentication dialog (e.g. Dusky Polkit) is open on the active workspace
                active_ws = self.active_window.get("workspace")
                for c in self._clients:
                    if c.get("hidden", False):
                        continue
                    c_cls = (c.get("class") or "").lower()
                    c_ttl = (c.get("title") or "").lower()
                    if any(a_cls in c_cls for a_cls in AUTH_WINDOW_CLASSES) or any(
                        a_ttl in c_ttl for a_ttl in AUTH_WINDOW_TITLES
                    ):
                        if active_ws is None or c.get("workspace") == active_ws or c.get("pinned", False):
                            return f"authentication dialog ({c.get('class') or c.get('title')})"

            for layer in self.layers:
                kind = layer["kind"]
                if block_auth and kind in ("auth", "lock", "lock-preview"):
                    return f"{kind} layer ({layer['namespace']})"
                if block_launcher and kind in ("launcher", "osk"):
                    return f"{kind} layer ({layer['namespace']})"
        return None

    def find_matching_window(
        self, win_class: str, title_pattern: str = "", include_hidden: bool = False
    ) -> dict[str, Any] | None:
        self.refresh_full_snapshot()
        with self._lock:
            candidates = list(self._clients)
        title_re = re.compile(title_pattern, re.IGNORECASE) if title_pattern else None
        matched: list[dict[str, Any]] = []
        for c in candidates:
            if not include_hidden and c.get("hidden", False):
                continue
            if win_class and c["class"].lower() != win_class.lower():
                continue
            if title_re and not title_re.search(c["title"]):
                continue
            matched.append(c)
        if not matched:
            return None
        matched.sort(key=lambda w: (w.get("hidden", False), w.get("focus_history_id", 9999)))
        return matched[0]

    def clamp_to_monitors(self, x: float, y: float) -> tuple[int, int]:
        ix, iy = int(round(x)), int(round(y))
        with self._lock:
            mons = list(self.monitors)
        if not mons:
            return ix, iy
        for m in mons:
            mx, my, mw, mh = m["logical_geometry"]
            if mx <= ix < mx + mw and my <= iy < my + mh:
                return ix, iy

        best_cx, best_cy = ix, iy
        best_dist_sq: int | None = None
        for m in mons:
            mx, my, mw, mh = m["logical_geometry"]
            if mw <= 0 or mh <= 0:
                continue
            cx = max(mx, min(ix, mx + mw - 1))
            cy = max(my, min(iy, my + mh - 1))
            dist_sq = (cx - ix) ** 2 + (cy - iy) ** 2
            if best_dist_sq is None or dist_sq < best_dist_sq:
                best_dist_sq = dist_sq
                best_cx, best_cy = cx, cy
        return best_cx, best_cy

    def start_event_listener(self) -> None:
        if self._event_thread and self._event_thread.is_alive():
            return
        self._stop_event.clear()
        self._event_thread = threading.Thread(
            target=self._event_loop, name="HyprlandSocket2Listener", daemon=True
        )
        self._event_thread.start()

    def stop_event_listener(self) -> None:
        self._stop_event.set()
        if self._event_thread and self._event_thread.is_alive():
            self._event_thread.join(timeout=1.0)

    @classmethod
    def _parse_event_line(cls, line: str) -> tuple[str, dict[str, str]] | None:
        name, sep, data = line.partition(">>")
        if not sep:
            return None
        schema = cls._SCHEMAS.get(name)
        if not schema:
            return name, {"data": data}
        fields, maxsplit = schema
        parts = data.split(",", maxsplit)
        payload: dict[str, str] = {}
        for f_name, val in zip(fields, parts, strict=False):
            if f_name == "address" and val and not val.startswith("0x"):
                val = "0x" + val
            payload[f_name] = val
        return name, payload

    def _event_loop(self) -> None:
        while not self._stop_event.is_set():
            buf = b""
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                    sock.settimeout(0.5)
                    sock.connect(str(self.ipc.socket2_path))
                    while not self._stop_event.is_set():
                        try:
                            chunk = sock.recv(4096)
                        except TimeoutError:
                            continue
                        if not chunk:
                            break
                        buf += chunk
                        while b"\n" in buf:
                            raw_line, buf = buf.split(b"\n", 1)
                            parsed = self._parse_event_line(raw_line.decode("utf-8", errors="replace"))
                            if not parsed:
                                continue
                            ev_name, payload = parsed
                            if ev_name in ("openwindow", "closewindow", "openlayer", "closelayer", "workspace", "activespecial"):
                                self.refresh_full_snapshot()
                            elif ev_name in ("activewindowv2", "windowtitlev2", "movewindow"):
                                self.refresh_active_window()
                            with self._event_cond:
                                self._recent_events.append((time.monotonic(), ev_name, payload))
                                if len(self._recent_events) > 128:
                                    self._recent_events = self._recent_events[-64:]
                                self._event_cond.notify_all()
            except Exception:
                if not self._stop_event.is_set():
                    time.sleep(0.25)

    def _condition_already_holds(self, ev_name: str, match_substr: str) -> bool:
        """Checks if a waited-for compositor condition already holds in current desktop state
        so wait_for_event never misses an event that arrived just prior to waiting."""
        needle = match_substr.lower()
        with self._lock:
            if ev_name in ("openwindow", "window_open"):
                return any(
                    not needle or needle in f"{c['address']} {c['class']} {c['title']}".lower()
                    for c in self._clients
                )
            if ev_name in ("closewindow", "window_close") and needle:
                return all(
                    needle not in f"{c['address']} {c['class']} {c['title']}".lower()
                    for c in self._clients
                )
            if ev_name == "activewindowv2" and needle:
                addr = (self.active_window.get("address") or "").lower()
                return needle in addr
            if ev_name in ("windowtitlev2", "title_change") and needle:
                ttl = (self.active_window.get("title") or "").lower()
                return needle in ttl
            if ev_name == "workspace" and needle:
                return any(
                    needle == str(m.get("active_workspace", "")).lower()
                    or needle == str(m.get("special_workspace", "")).lower()
                    for m in self.monitors
                )
            if ev_name in ("openlayer", "layer_open") and needle:
                return any(needle in (l.get("namespace") or "").lower() for l in self.layers)
        return False

    def wait_for_event(self, ev_name: str, match_substr: str = "", timeout_s: float = 5.0) -> bool:
        self.refresh_full_snapshot()
        if self._condition_already_holds(ev_name, match_substr):
            return True
        deadline = time.monotonic() + timeout_s
        with self._event_cond:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                for _, name, payload in reversed(self._recent_events):
                    if name == ev_name:
                        joined = " ".join(payload.values())
                        if not match_substr or match_substr.lower() in joined.lower():
                            return True
                self._event_cond.wait(timeout=min(remaining, 0.25))


# =============================================================================
# SECTION 5: RAW WAYLAND WIRE INJECTOR & INPUT STATE TRACKER
# =============================================================================

def _wl_string(s: str) -> bytes:
    raw = s.encode("utf-8") + b"\x00"
    pad = (4 - len(raw) % 4) % 4
    return struct.pack("<I", len(raw)) + raw + (b"\x00" * pad)


def _wl_to_fixed(val: float) -> int:
    return int(round(val * 256.0))


def _wl_encode_msg(obj_id: int, opcode: int, body: bytes = b"") -> bytes:
    size = 8 + len(body)
    return _WL_HDR_STRUCT.pack(obj_id, (size << 16) | (opcode & 0xFFFF)) + body


def _wl_now_ms() -> int:
    return (_monotonic_ns() // 1_000_000) & 0xFFFFFFFF


class InputStateTracker:
    """Thread-safe tracker of currently depressed mouse buttons and keyboard keycodes.
    Uses RLock so nested or signal-adjacent cleanup never deadlocks."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.pressed_buttons: set[int] = set()
        self.pressed_keys: set[int] = set()

    def mark_button(self, code: int, pressed: bool) -> None:
        with self._lock:
            if pressed:
                self.pressed_buttons.add(code)
            else:
                self.pressed_buttons.discard(code)

    def mark_key(self, code: int, pressed: bool) -> None:
        with self._lock:
            if pressed:
                self.pressed_keys.add(code)
            else:
                self.pressed_keys.discard(code)

    def drain_held(self) -> tuple[list[int], list[int]]:
        with self._lock:
            btns = sorted(self.pressed_buttons)
            keys = sorted(self.pressed_keys)
            self.pressed_buttons.clear()
            self.pressed_keys.clear()
            return btns, keys


class WaylandWireInjector:
    """Pure-Python Wayland client speaking zwlr_virtual_pointer_v1 and
    zwp_virtual_keyboard_v1 directly over $XDG_RUNTIME_DIR/$WAYLAND_DISPLAY.
    Uses pre-compiled composite structs, object ID recycling, and os.memfd_create."""

    def __init__(self, xkb: XkbBridge) -> None:
        self.xkb = xkb
        self.tracker = InputStateTracker()
        self._lock = threading.RLock()
        self._sock = self._connect_wayland()
        self._buf = b""
        self._next_id = 2
        self._free_ids: list[int] = []
        self._registry_id = self._alloc_id()
        self._send(WL_DISPLAY_ID, WL_DISPLAY_GET_REGISTRY, struct.pack("<I", self._registry_id))
        globals_list = self._roundtrip(collect_globals=True)

        # Bind wl_seat (required as a non-null object by zwp_virtual_keyboard_manager_v1)
        seat_matches = [(name, ver) for name, iface, ver in globals_list if iface == "wl_seat"]
        if not seat_matches:
            self._sock.close()
            raise DuskyMacroError("Compositor does not advertise wl_seat")
        seat_name, seat_ver = seat_matches[0]
        self._seat_id = self._alloc_id()
        self._send(
            self._registry_id,
            WL_REGISTRY_BIND,
            struct.pack("<I", seat_name)
            + _wl_string("wl_seat")
            + struct.pack("<II", min(seat_ver, 1), self._seat_id),
        )

        # Bind zwlr_virtual_pointer_manager_v1
        ptr_matches = [
            (name, ver) for name, iface, ver in globals_list if iface == ZWLR_VIRTUAL_POINTER_MGR_INTERFACE
        ]
        if not ptr_matches:
            self._sock.close()
            raise DuskyMacroError(f"Compositor does not advertise {ZWLR_VIRTUAL_POINTER_MGR_INTERFACE}")
        ptr_name, ptr_ver = ptr_matches[0]
        self._ptr_version = min(ptr_ver, 2)
        self._ptr_mgr_id = self._alloc_id()
        self._send(
            self._registry_id,
            WL_REGISTRY_BIND,
            struct.pack("<I", ptr_name)
            + _wl_string(ZWLR_VIRTUAL_POINTER_MGR_INTERFACE)
            + struct.pack("<II", self._ptr_version, self._ptr_mgr_id),
        )
        self._pointer_id = self._alloc_id()
        self._send(self._ptr_mgr_id, MGR_CREATE_POINTER, struct.pack("<II", self._seat_id, self._pointer_id))

        # Bind zwp_virtual_keyboard_manager_v1
        vk_matches = [
            (name, ver) for name, iface, ver in globals_list if iface == ZWP_VIRTUAL_KEYBOARD_MGR_INTERFACE
        ]
        if not vk_matches:
            self._sock.close()
            raise DuskyMacroError(f"Compositor does not advertise {ZWP_VIRTUAL_KEYBOARD_MGR_INTERFACE}")
        vk_name, vk_ver = vk_matches[0]
        self._vk_mgr_id = self._alloc_id()
        self._send(
            self._registry_id,
            WL_REGISTRY_BIND,
            struct.pack("<I", vk_name)
            + _wl_string(ZWP_VIRTUAL_KEYBOARD_MGR_INTERFACE)
            + struct.pack("<II", min(vk_ver, 1), self._vk_mgr_id),
        )
        self._keyboard_id = self._alloc_id()
        self._send(self._vk_mgr_id, VK_CREATE_KEYBOARD, struct.pack("<II", self._seat_id, self._keyboard_id))
        self._roundtrip()
        self._upload_keymap(self.xkb.keymap_bytes)
        self._closed = False

    @staticmethod
    def _connect_wayland() -> socket.socket:
        runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        display = os.environ.get("WAYLAND_DISPLAY", "wayland-0")
        path = display if display.startswith("/") else os.path.join(runtime, display)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(3.0)
        try:
            sock.connect(path)
        except OSError as exc:
            sock.close()
            raise DuskyMacroError(f"Cannot connect to Wayland display at {path}: {exc}") from exc
        return sock

    def _alloc_id(self) -> int:
        if self._free_ids:
            return self._free_ids.pop()
        oid = self._next_id
        self._next_id += 1
        return oid

    def _send(self, obj_id: int, opcode: int, body: bytes = b"") -> None:
        self._sock.sendall(_wl_encode_msg(obj_id, opcode, body))

    def _drain_nonblocking(self) -> None:
        while True:
            try:
                chunk = self._sock.recv(16384, socket.MSG_DONTWAIT)
            except (BlockingIOError, OSError):
                break
            if not chunk:
                break
            self._buf += chunk
            self._consume_buffer(collect_globals=False, globals_out=[])

    def _consume_buffer(
        self,
        collect_globals: bool,
        globals_out: list[tuple[int, str, int]],
        target_cb_id: int | None = None,
    ) -> bool:
        done = False
        while len(self._buf) >= 8:
            obj, size_op = _WL_HDR_STRUCT.unpack_from(self._buf, 0)
            size, opcode = size_op >> 16, size_op & 0xFFFF
            if size < 8 or len(self._buf) < size:
                break
            body = self._buf[8:size]
            self._buf = self._buf[size:]
            if obj == WL_DISPLAY_ID:
                if opcode == WL_DISPLAY_EV_ERROR:
                    err_obj, code = _WL_HDR_STRUCT.unpack_from(body, 0)
                    slen = struct.unpack_from("<I", body, 8)[0]
                    msg = body[12 : 12 + slen - 1].decode("utf-8", errors="replace")
                    raise DuskyMacroError(f"wl_display.error(obj={err_obj}, code={code}): {msg}")
                if opcode == WL_DISPLAY_EV_DELETE_ID and len(body) >= 4:
                    deleted_id = struct.unpack_from("<I", body, 0)[0]
                    if deleted_id >= 10:
                        self._free_ids.append(deleted_id)
            elif collect_globals and obj == self._registry_id and opcode == WL_REGISTRY_EV_GLOBAL:
                name = struct.unpack_from("<I", body, 0)[0]
                slen = struct.unpack_from("<I", body, 4)[0]
                iface = body[8 : 8 + slen - 1].decode("utf-8", errors="replace")
                pad = (4 - slen % 4) % 4
                ver = struct.unpack_from("<I", body, 8 + slen + pad)[0]
                globals_out.append((name, iface, ver))
            elif target_cb_id is not None and obj == target_cb_id and opcode == 0:
                done = True
        return done

    def _roundtrip(self, collect_globals: bool = False) -> list[tuple[int, str, int]]:
        cb_id = self._alloc_id()
        self._send(WL_DISPLAY_ID, WL_DISPLAY_SYNC, struct.pack("<I", cb_id))
        globals_seen: list[tuple[int, str, int]] = []
        while True:
            if self._consume_buffer(collect_globals, globals_seen, target_cb_id=cb_id):
                return globals_seen
            chunk = self._sock.recv(65536)
            if not chunk:
                raise DuskyMacroError("Wayland compositor closed the connection")
            self._buf += chunk

    def _upload_keymap(self, keymap_data: bytes) -> None:
        if hasattr(os, "memfd_create"):
            fd = os.memfd_create("dusky_xkb", os.MFD_CLOEXEC)
            try:
                os.write(fd, keymap_data)
                os.lseek(fd, 0, os.SEEK_SET)
                msg = _wl_encode_msg(
                    self._keyboard_id,
                    VK_KEYMAP,
                    struct.pack("<II", XKB_KEYMAP_FORMAT_TEXT_V1, len(keymap_data)),
                )
                self._sock.sendmsg(
                    [msg],
                    [(socket.SOL_SOCKET, socket.SCM_RIGHTS, struct.pack("i", fd))],
                )
                self._roundtrip()
            finally:
                os.close(fd)
        else:
            with tempfile.TemporaryFile() as tf:
                tf.write(keymap_data)
                tf.flush()
                tf.seek(0)
                msg = _wl_encode_msg(
                    self._keyboard_id,
                    VK_KEYMAP,
                    struct.pack("<II", XKB_KEYMAP_FORMAT_TEXT_V1, len(keymap_data)),
                )
                self._sock.sendmsg(
                    [msg],
                    [(socket.SOL_SOCKET, socket.SCM_RIGHTS, struct.pack("i", tf.fileno()))],
                )
                self._roundtrip()

    def move_relative(self, dx: float, dy: float, sync: bool = False) -> None:
        with self._lock:
            pkt = _WL_MOTION_FRAME_STRUCT.pack(
                self._pointer_id,
                (20 << 16) | PTR_MOTION,
                _wl_now_ms(),
                _wl_to_fixed(dx),
                _wl_to_fixed(dy),
                self._pointer_id,
                (8 << 16) | PTR_FRAME,
            )
            self._sock.sendall(pkt)
            if sync:
                self._roundtrip()
            else:
                self._drain_nonblocking()

    def emit_button(self, button_code: int, pressed: bool, sync: bool = True) -> None:
        with self._lock:
            self.tracker.mark_button(button_code, pressed)
            state = KEY_PRESSED if pressed else KEY_RELEASED
            pkt = _WL_BUTTON_FRAME_STRUCT.pack(
                self._pointer_id,
                (20 << 16) | PTR_BUTTON,
                _wl_now_ms(),
                button_code,
                state,
                self._pointer_id,
                (8 << 16) | PTR_FRAME,
            )
            self._sock.sendall(pkt)
            if sync:
                self._roundtrip()
            else:
                self._drain_nonblocking()

    def emit_scroll(self, dy: float = 0.0, dx: float = 0.0, sync: bool = True) -> None:
        with self._lock:
            t = _wl_now_ms()
            self._send(self._pointer_id, PTR_AXIS_SOURCE, struct.pack("<I", AXIS_SOURCE_WHEEL))
            for axis, val in ((AXIS_VERTICAL, dy), (AXIS_HORIZONTAL, dx)):
                if not val:
                    continue
                fixed_val = _wl_to_fixed(val * SCROLL_UNITS_PER_NOTCH)
                notches = int(round(val))
                if self._ptr_version >= 2 and notches and abs(val - notches) < 1e-6:
                    self._send(
                        self._pointer_id,
                        PTR_AXIS_DISCRETE,
                        struct.pack("<IIii", t, axis, fixed_val, notches),
                    )
                else:
                    self._send(
                        self._pointer_id,
                        PTR_AXIS,
                        struct.pack("<IIi", t, axis, fixed_val),
                    )
            self._send(self._pointer_id, PTR_FRAME)
            if sync:
                self._roundtrip()
            else:
                self._drain_nonblocking()

    def emit_key(self, evdev_code: int, pressed: bool, sync: bool = False) -> None:
        with self._lock:
            self.tracker.mark_key(evdev_code, pressed)
            state = KEY_PRESSED if pressed else KEY_RELEASED
            depressed, latched, locked, group = self.xkb.update_key_and_serialize_mods(evdev_code, pressed)
            pkt = _WL_KEY_MODS_STRUCT.pack(
                self._keyboard_id,
                (20 << 16) | VK_KEY,
                _wl_now_ms(),
                evdev_code,
                state,
                self._keyboard_id,
                (24 << 16) | VK_MODIFIERS,
                depressed,
                latched,
                locked,
                group,
            )
            self._sock.sendall(pkt)
            if sync:
                self._roundtrip()
            else:
                self._drain_nonblocking()

    def type_unicode_text(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            unique_chars = list(dict.fromkeys(text))
            temp_km = self.xkb.build_unicode_keymap(unique_chars)
            char_to_code = {ch: idx + 1 for idx, ch in enumerate(unique_chars)}
            self._upload_keymap(temp_km)
            try:
                t = _wl_now_ms()
                buf = bytearray()
                for ch in text:
                    code = char_to_code[ch]
                    buf += _WL_KEY_ONLY_STRUCT.pack(
                        self._keyboard_id, (20 << 16) | VK_KEY, t, code, KEY_PRESSED
                    )
                    buf += _WL_KEY_ONLY_STRUCT.pack(
                        self._keyboard_id, (20 << 16) | VK_KEY, t, code, KEY_RELEASED
                    )
                self._sock.sendall(buf)
                self._roundtrip()
            finally:
                self._upload_keymap(self.xkb.keymap_bytes)

    def release_all(self) -> None:
        """Failsafe release of all tracked keys/buttons plus unconditional release of
        primary mouse buttons and modifier reset."""
        with self._lock:
            if getattr(self, "_closed", True):
                return
            held_buttons, held_keys = self.tracker.drain_held()
            t = _wl_now_ms()
            for btn in held_buttons:
                with contextlib.suppress(Exception):
                    self._sock.sendall(
                        _WL_BUTTON_FRAME_STRUCT.pack(
                            self._pointer_id,
                            (20 << 16) | PTR_BUTTON,
                            t,
                            btn,
                            KEY_RELEASED,
                            self._pointer_id,
                            (8 << 16) | PTR_FRAME,
                        )
                    )
            for fallback_btn in (BTN_LEFT, BTN_RIGHT, BTN_MIDDLE):
                if fallback_btn not in held_buttons:
                    with contextlib.suppress(Exception):
                        self._sock.sendall(
                            _WL_BUTTON_FRAME_STRUCT.pack(
                                self._pointer_id,
                                (20 << 16) | PTR_BUTTON,
                                t,
                                fallback_btn,
                                KEY_RELEASED,
                                self._pointer_id,
                                (8 << 16) | PTR_FRAME,
                            )
                        )

            for key_code in held_keys:
                with contextlib.suppress(Exception):
                    self._sock.sendall(
                        _WL_KEY_ONLY_STRUCT.pack(
                            self._keyboard_id, (20 << 16) | VK_KEY, t, key_code, KEY_RELEASED
                        )
                    )

            with contextlib.suppress(Exception):
                self.xkb.reset_state()
                self._send(self._keyboard_id, VK_MODIFIERS, struct.pack("<IIII", 0, 0, 0, 0))
                self._roundtrip()

    def close(self) -> None:
        with self._lock:
            if getattr(self, "_closed", True):
                return
            self.release_all()
            self._closed = True
            with contextlib.suppress(Exception):
                self._send(self._keyboard_id, VK_DESTROY)
                self._send(self._pointer_id, PTR_DESTROY)
                self._send(self._ptr_mgr_id, MGR_DESTROY)
                self._roundtrip()
            with contextlib.suppress(Exception):
                self._sock.close()

    def __enter__(self) -> WaylandWireInjector:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# =============================================================================
# SECTION 6: KERNEL EVDEV CAPTURE, SYN_REPORT ACCUMULATOR & HOTKEY ENGINE
# =============================================================================

@dataclass(slots=True)
class EvdevDeviceInfo:
    path: str
    name: str
    is_mouse: bool
    is_keyboard: bool


def _test_bit(bitmask: bytes, bit: int) -> bool:
    byte_idx = bit // 8
    if byte_idx >= len(bitmask):
        return False
    return bool(bitmask[byte_idx] & (1 << (bit % 8)))


class EvdevDeviceScanner:
    """Scans /dev/input/event* nodes, classifies mice and keyboards via EVIOCGBIT,
    filters out virtual/synthetic devices, and probes for exclusive EVIOCGRAB conflicts."""

    VIRTUAL_KEYWORDS = (
        "virtual", "wlroots", "hyprland", "uinput", "ydotool", "dusky"
    )

    @classmethod
    def scan(
        cls,
        need_mouse: bool = True,
        need_keyboard: bool = True,
        ignore_virtual: bool = True,
        extra_ignored: tuple[str, ...] = (),
    ) -> list[EvdevDeviceInfo]:
        devices: list[EvdevDeviceInfo] = []
        input_dir = Path("/dev/input")
        if not input_dir.is_dir():
            return devices

        ignored_terms = (cls.VIRTUAL_KEYWORDS + extra_ignored) if ignore_virtual else extra_ignored

        for node in sorted(input_dir.glob("event*")):
            path = str(node)
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                continue
            try:
                name_buf = bytearray(256)
                with contextlib.suppress(OSError):
                    fcntl.ioctl(fd, EVIOCGNAME(256), name_buf)
                name = (
                    bytes(name_buf).split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()
                    or node.name
                )
                low_name = name.lower()
                if ignored_terms and any(kw in low_name for kw in ignored_terms):
                    continue

                ev_bits = bytearray(4)
                fcntl.ioctl(fd, EVIOCGBIT(0, 4), ev_bits)
                has_key = _test_bit(ev_bits, EV_KEY)
                has_rel = _test_bit(ev_bits, EV_REL)

                key_bits = bytearray(96)
                if has_key:
                    with contextlib.suppress(OSError):
                        fcntl.ioctl(fd, EVIOCGBIT(EV_KEY, 96), key_bits)

                rel_bits = bytearray(4)
                if has_rel:
                    with contextlib.suppress(OSError):
                        fcntl.ioctl(fd, EVIOCGBIT(EV_REL, 4), rel_bits)

                is_mouse = (
                    has_rel
                    and _test_bit(rel_bits, REL_X)
                    and _test_bit(rel_bits, REL_Y)
                    and has_key
                    and _test_bit(key_bits, BTN_LEFT)
                )
                is_keyboard = has_key and all(_test_bit(key_bits, k) for k in range(16, 26))

                if (need_mouse and is_mouse) or (need_keyboard and is_keyboard):
                    devices.append(
                        EvdevDeviceInfo(path=path, name=name, is_mouse=is_mouse, is_keyboard=is_keyboard)
                    )
            except OSError:
                continue
            finally:
                os.close(fd)
        return devices

    @classmethod
    def probe_grab_conflicts(cls, devices: list[EvdevDeviceInfo]) -> list[str]:
        """Tests whether any target /dev/input/event* device is exclusively grabbed (EVIOCGRAB / EBUSY)
        by another process (such as gpu-screen-recorder or an input remapper)."""
        grabbed: list[str] = []
        for dev in devices:
            try:
                fd = os.open(dev.path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError as exc:
                if exc.errno == errno.EBUSY:
                    grabbed.append(f"{dev.name} ({dev.path}: EBUSY)")
                continue
            try:
                fcntl.ioctl(fd, EVIOCGRAB, struct.pack("<i", 1))
                fcntl.ioctl(fd, EVIOCGRAB, struct.pack("<i", 0))
            except OSError as exc:
                if exc.errno == errno.EBUSY:
                    grabbed.append(f"{dev.name} ({dev.path}: exclusively grabbed by another process)")
            finally:
                os.close(fd)
        return grabbed


@dataclass(slots=True)
class AtomicInputFrame:
    """Represents a single hardware poll packet flushed upon EV_SYN / SYN_REPORT."""
    timestamp_ns: int
    source_fd: int = -1
    rel_dx: int = 0
    rel_dy: int = 0
    wheel_dy: float = 0.0
    wheel_dx: float = 0.0
    buttons: list[tuple[int, bool]] = field(default_factory=list)
    keys: list[tuple[int, bool]] = field(default_factory=list)

    @property
    def has_mouse_motion(self) -> bool:
        return self.rel_dx != 0 or self.rel_dy != 0

    @property
    def is_empty(self) -> bool:
        return (
            not self.has_mouse_motion
            and not self.wheel_dy
            and not self.wheel_dx
            and not self.buttons
            and not self.keys
        )


class SynReportAccumulator:
    """Accumulates raw kernel input_event items for a single device (including Kernel 7.4+
    REL_WHEEL_HI_RES / REL_HWHEEL_HI_RES) and flushes an AtomicInputFrame on SYN_REPORT."""

    def __init__(self, source_fd: int, capture_mouse: bool = True, capture_keyboard: bool = True) -> None:
        self.source_fd = source_fd
        self.capture_mouse = capture_mouse
        self.capture_keyboard = capture_keyboard
        self._reset()

    def _reset(self) -> None:
        self._dx = 0
        self._dy = 0
        self._wheel_dy = 0.0
        self._wheel_dx = 0.0
        self._hi_res_dy = 0
        self._hi_res_dx = 0
        self._buttons: list[tuple[int, bool]] = []
        self._keys: list[tuple[int, bool]] = []

    def feed(
        self,
        ev_type: int,
        ev_code: int,
        ev_value: int,
        kernel_ts_ns: int = 0,
    ) -> AtomicInputFrame | None:
        if ev_type == EV_SYN:
            if ev_code == SYN_DROPPED:
                self._reset()
                return None
            if ev_code == SYN_REPORT:
                ts_ns = kernel_ts_ns if kernel_ts_ns > 0 else _monotonic_ns()
                final_w_dy = (
                    round(self._hi_res_dy / HI_RES_UNITS_PER_NOTCH, 4)
                    if self._hi_res_dy != 0
                    else self._wheel_dy
                )
                final_w_dx = (
                    round(self._hi_res_dx / HI_RES_UNITS_PER_NOTCH, 4)
                    if self._hi_res_dx != 0
                    else self._wheel_dx
                )
                frame = AtomicInputFrame(
                    timestamp_ns=ts_ns,
                    source_fd=self.source_fd,
                    rel_dx=self._dx,
                    rel_dy=self._dy,
                    wheel_dy=final_w_dy,
                    wheel_dx=final_w_dx,
                    buttons=list(self._buttons),
                    keys=list(self._keys),
                )
                self._reset()
                return None if frame.is_empty else frame
            return None

        if ev_type == EV_REL and self.capture_mouse:
            if ev_code == REL_X:
                self._dx += ev_value
            elif ev_code == REL_Y:
                self._dy += ev_value
            elif ev_code == REL_WHEEL:
                self._wheel_dy += float(-ev_value)
            elif ev_code == REL_HWHEEL:
                self._wheel_dx += float(ev_value)
            elif ev_code == REL_WHEEL_HI_RES:
                self._hi_res_dy += -ev_value
            elif ev_code == REL_HWHEEL_HI_RES:
                self._hi_res_dx += ev_value
        elif ev_type == EV_KEY:
            if ev_value not in (KEY_PRESSED, KEY_RELEASED):
                return None
            pressed = ev_value == KEY_PRESSED
            if ev_code in BUTTON_CODE_TO_NAME:
                if self.capture_mouse:
                    self._buttons.append((ev_code, pressed))
            elif self.capture_keyboard:
                self._keys.append((ev_code, pressed))
        return None


def parse_hotkey_chord(spec: str) -> tuple[list[set[int]], int]:
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not parts:
        raise DuskyMacroError(f"Empty hotkey specification: {spec!r}")
    codes: list[int] = []
    for p in parts:
        if p not in KEY_NAMES_TO_CODES:
            raise DuskyMacroError(f"Unknown key or button name {p!r} in hotkey {spec!r}")
        codes.append(KEY_NAMES_TO_CODES[p])
    primary = codes[-1]
    mod_sets = [MODIFIER_EQUIVALENTS.get(c, {c}) for c in codes[:-1]]
    return mod_sets, primary


class HotkeyMatcher:
    """Tracks physical depressed keys & mouse buttons across devices and detects chord triggers."""

    def __init__(self, spec: str) -> None:
        self.spec = spec
        self.mod_sets, self.primary_code = parse_hotkey_chord(spec)
        self.all_chord_codes: set[int] = {self.primary_code}
        for s in self.mod_sets:
            self.all_chord_codes |= s
        self.needs_mouse_buttons = any(c in BUTTON_CODE_TO_NAME for c in self.all_chord_codes)

    def matches_press(self, code: int, pressed: bool, held_codes: set[int]) -> bool:
        if not pressed or code != self.primary_code:
            return False
        return all(bool(held_codes & mod_set) for mod_set in self.mod_sets)


# =============================================================================
# SECTION 7: PRECISION TIMING, EVDEV SESSION, RECORDER & PLAYBACK ENGINES
# =============================================================================

class PrecisionClock:
    """Achieves microsecond-accurate event pacing using CLOCK_MONOTONIC nanoseconds
    with a two-stage hybrid coarse sleep + busy-spin wait."""

    @staticmethod
    def wait_until_ns(
        target_ns: int,
        abort_event: threading.Event,
        pause_event: threading.Event | None = None,
        spin_threshold_ns: int = 1_200_000,
        max_coarse_sleep_s: float = 0.012,
    ) -> int:
        paused_ns_total = 0
        while True:
            if abort_event.is_set():
                return paused_ns_total
            if pause_event is not None and pause_event.is_set():
                pause_start = _monotonic_ns()
                while pause_event.is_set() and not abort_event.is_set():
                    time.sleep(0.012)
                paused_delta = _monotonic_ns() - pause_start
                paused_ns_total += paused_delta
                target_ns += paused_delta

            now_ns = _monotonic_ns()
            remaining_ns = target_ns - now_ns
            if remaining_ns <= 0:
                return paused_ns_total

            if remaining_ns > spin_threshold_ns:
                sleep_s = min((remaining_ns - spin_threshold_ns) / 1_000_000_000.0, max_coarse_sleep_s)
                time.sleep(sleep_s)
            else:
                while _monotonic_ns() < target_ns:
                    if abort_event.is_set():
                        return paused_ns_total
                return paused_ns_total


class EvdevInputSession:
    """Manages non-blocking epoll reading of /dev/input/event* with EVIOCSCLOCKID
    monotonic timestamps, per-FD held-code state tracking (purged on disconnect),
    full ring-buffer draining until EAGAIN, and 1-second hotplug rescanning."""

    def __init__(
        self,
        capture_mouse: bool = True,
        capture_keyboard: bool = True,
        use_kernel_monotonic_ts: bool = True,
        rescan_interval_s: float = 1.0,
        ignore_virtual: bool = True,
        extra_ignored: tuple[str, ...] = (),
    ) -> None:
        self.capture_mouse = capture_mouse
        self.capture_keyboard = capture_keyboard
        self.use_kernel_monotonic_ts = use_kernel_monotonic_ts
        self.rescan_interval_s = rescan_interval_s
        self.ignore_virtual = ignore_virtual
        self.extra_ignored = extra_ignored
        self._selector = selectors.DefaultSelector()
        self._fds: dict[str, int] = {}
        self._accumulators: dict[int, SynReportAccumulator] = {}
        self._held_by_fd: dict[int, set[int]] = {}

    def open_devices(self) -> int:
        self._reconcile_devices()
        return len(self._fds)

    def update_held(self, source_fd: int, code: int, pressed: bool) -> set[int]:
        fd_set = self._held_by_fd.setdefault(source_fd, set())
        if pressed:
            fd_set.add(code)
        else:
            fd_set.discard(code)
        return self.held_codes

    @property
    def held_codes(self) -> set[int]:
        union_set: set[int] = set()
        for s in self._held_by_fd.values():
            union_set |= s
        return union_set

    def _reconcile_devices(self) -> None:
        target_infos = EvdevDeviceScanner.scan(
            need_mouse=self.capture_mouse,
            need_keyboard=self.capture_keyboard,
            ignore_virtual=self.ignore_virtual,
            extra_ignored=self.extra_ignored,
        )
        target_paths = {info.path for info in target_infos}

        for path in list(self._fds.keys()):
            if path not in target_paths:
                self._remove_path(path)

        clock_mono_arg = struct.pack("<I", _CLOCK_MONOTONIC)
        for info in target_infos:
            if info.path in self._fds:
                continue
            try:
                fd = os.open(info.path, os.O_RDONLY | os.O_NONBLOCK)
                if self.use_kernel_monotonic_ts:
                    with contextlib.suppress(OSError):
                        fcntl.ioctl(fd, EVIOCSCLOCKID, clock_mono_arg)
                self._fds[info.path] = fd
                self._held_by_fd[fd] = set()
                self._accumulators[fd] = SynReportAccumulator(
                    source_fd=fd,
                    capture_mouse=self.capture_mouse and info.is_mouse,
                    capture_keyboard=self.capture_keyboard and info.is_keyboard,
                )
                self._selector.register(fd, selectors.EVENT_READ, data=info.path)
            except OSError:
                continue

    def _remove_path(self, path: str) -> None:
        fd = self._fds.pop(path, None)
        if fd is not None:
            with contextlib.suppress(Exception):
                self._selector.unregister(fd)
            with contextlib.suppress(OSError):
                os.close(fd)
            self._accumulators.pop(fd, None)
            self._held_by_fd.pop(fd, None)

    def poll_frames(self, timeout_s: float = 0.010) -> list[AtomicInputFrame]:
        frames: list[AtomicInputFrame] = []
        events = self._selector.select(timeout=timeout_s)
        for key, _ in events:
            fd = key.fd
            path = str(key.data)
            acc = self._accumulators.get(fd)
            if acc is None:
                continue
            while True:
                try:
                    raw = os.read(fd, EVDEV_EVENT_SIZE * 128)
                except OSError as exc:
                    if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                        self._remove_path(path)
                    break
                if not raw:
                    self._remove_path(path)
                    break
                n_events = len(raw) // EVDEV_EVENT_SIZE
                for i in range(n_events):
                    tv_sec, tv_usec, ev_type, ev_code, ev_val = EVDEV_EVENT_STRUCT.unpack_from(
                        raw, i * EVDEV_EVENT_SIZE
                    )
                    k_ts_ns = (
                        (tv_sec * 1_000_000_000) + (tv_usec * 1_000)
                        if self.use_kernel_monotonic_ts and tv_sec > 0
                        else 0
                    )
                    frame = acc.feed(ev_type, ev_code, ev_val, kernel_ts_ns=k_ts_ns)
                    if frame is not None:
                        frames.append(frame)
                if len(raw) < EVDEV_EVENT_SIZE * 128:
                    break
        if len(frames) > 1:
            frames.sort(key=lambda f: f.timestamp_ns)
        return frames

    def rescan_if_due(self, last_scan_mono: float) -> float:
        now = time.monotonic()
        if now - last_scan_mono >= self.rescan_interval_s:
            self._reconcile_devices()
            return now
        return last_scan_mono

    def close(self) -> None:
        for path in list(self._fds.keys()):
            self._remove_path(path)
        with contextlib.suppress(Exception):
            self._selector.close()

    def __enter__(self) -> EvdevInputSession:
        self.open_devices()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class MacroRecorder:
    """Records physical mouse and keyboard events from /dev/input/event*, correlates
    SYN_REPORT mouse frames with Hyprland's cursorpos and active window geometry,
    coalesces high-frequency motion queries, and strips start/stop hotkey artifacts."""

    def __init__(
        self,
        ipc: HyprlandSocketClient,
        topology: DesktopTopology,
        xkb: XkbBridge,
        cfg: EffectiveConfig,
        stop_hotkey: str | None = None,
        coordinate_mode: str | None = None,
        capture_mouse: bool | None = None,
        capture_keyboard: bool | None = None,
    ) -> None:
        self.ipc = ipc
        self.topology = topology
        self.xkb = xkb
        self.cfg = cfg
        self.stop_matcher = HotkeyMatcher(stop_hotkey or cfg.record_hotkey)
        self.coordinate_mode = coordinate_mode or cfg.coordinate_mode
        self.capture_mouse = cfg.capture_mouse if capture_mouse is None else capture_mouse
        self.capture_keyboard = cfg.capture_keyboard if capture_keyboard is None else capture_keyboard

    def record(
        self,
        name: str = "recorded_macro",
        max_duration_s: float | None = None,
        initial_suppressed_keys: set[int] | None = None,
        abort_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        import datetime

        self.topology.refresh_full_snapshot()
        self.topology.start_event_listener()

        target_win = self.topology.refresh_active_window()
        init_x, init_y = self.ipc.cursor_pos_fast()
        prev_x, prev_y = init_x, init_y

        events: list[dict[str, Any]] = []
        suppressed_up_codes: set[int] = set(initial_suppressed_keys or set())

        t0_ns: int | None = None
        prev_t_us = 0
        last_motion_query_us = -1_000_000
        coalesced_raw_dx = 0
        coalesced_raw_dy = 0
        coalesce_us = max(0, self.cfg.coalesce_mouse_move_us)

        start_mono = time.monotonic()
        last_rescan = start_mono

        need_mouse_open = self.capture_mouse or self.stop_matcher.needs_mouse_buttons

        # Resolve whether to swap mouse buttons so physical right button records as primary (left) click
        should_swap = False
        swap_opt = str(self.cfg.swap_mouse_buttons).strip().lower()
        if swap_opt in ("true", "1", "yes", "on"):
            should_swap = True
        elif swap_opt == "auto":
            should_swap = self.ipc.is_left_handed()

        RuntimeBeacon.write_state("RECORDING", self.cfg.profile_id, name)
        try:
            with EvdevInputSession(
                capture_mouse=need_mouse_open,
                capture_keyboard=True,
                use_kernel_monotonic_ts=self.cfg.use_kernel_monotonic_ts,
                rescan_interval_s=self.cfg.hotplug_rescan_interval_s,
                ignore_virtual=self.cfg.ignore_virtual_devices,
                extra_ignored=self.cfg.extra_ignored_keywords,
            ) as session:
                if len(session._fds) == 0:
                    raise DuskyMacroError(
                        "No readable /dev/input/event* devices found. Ensure your user is in the 'input' group."
                    )

                stop_triggered = False
                while not stop_triggered:
                    if abort_event is not None and abort_event.is_set():
                        break
                    if max_duration_s is not None and (time.monotonic() - start_mono) >= max_duration_s:
                        break

                    last_rescan = session.rescan_if_due(last_rescan)
                    frames = session.poll_frames(timeout_s=self.cfg.epoll_poll_timeout_s)

                    for frame in frames:
                        for code, pressed in frame.keys:
                            held_codes = session.update_held(frame.source_fd, code, pressed)

                            if self.cfg.strip_trigger_chords and not pressed and code in suppressed_up_codes:
                                suppressed_up_codes.discard(code)
                                continue

                            if self.stop_matcher.matches_press(code, pressed, held_codes):
                                stop_triggered = True
                                break

                            if not self.capture_keyboard:
                                continue

                            if t0_ns is None:
                                t0_ns = frame.timestamp_ns
                            t_us = max(prev_t_us, (frame.timestamp_ns - t0_ns) // 1000)
                            d_us = max(0, t_us - prev_t_us)
                            prev_t_us = t_us

                            events.append({
                                "t_us": t_us,
                                "d_us": d_us,
                                "type": "key",
                                "code": code,
                                "symbol": self.xkb.keycode_to_symbol(code),
                                "state": "down" if pressed else "up",
                            })

                        if stop_triggered:
                            break

                        for btn_code, pressed in frame.buttons:
                            held_codes = session.update_held(frame.source_fd, btn_code, pressed)
                            if self.stop_matcher.matches_press(btn_code, pressed, held_codes):
                                stop_triggered = True
                                break

                        if stop_triggered or not self.capture_mouse:
                            if stop_triggered:
                                break
                            continue

                        if frame.has_mouse_motion or frame.buttons:
                            if t0_ns is None:
                                t0_ns = frame.timestamp_ns
                            t_us = max(prev_t_us, (frame.timestamp_ns - t0_ns) // 1000)

                            if frame.has_mouse_motion:
                                coalesced_raw_dx += frame.rel_dx
                                coalesced_raw_dy += frame.rel_dy

                            should_sample_cursor = bool(frame.buttons) or (
                                frame.has_mouse_motion and (t_us - last_motion_query_us) >= coalesce_us
                            )
                            if should_sample_cursor:
                                cur_x, cur_y = self.ipc.cursor_pos_fast()
                                last_motion_query_us = t_us
                                win = self.topology.active_window or target_win
                                wrect = win.get("rect", [0, 0, 0, 0])
                                wx = cur_x - wrect[0]
                                wy = cur_y - wrect[1]
                                log_dx = cur_x - prev_x
                                log_dy = cur_y - prev_y
                                prev_x, prev_y = cur_x, cur_y

                                if coalesced_raw_dx != 0 or coalesced_raw_dy != 0:
                                    d_us = max(0, t_us - prev_t_us)
                                    prev_t_us = t_us
                                    events.append({
                                        "t_us": t_us,
                                        "d_us": d_us,
                                        "type": "mouse_move",
                                        "x": cur_x,
                                        "y": cur_y,
                                        "wx": wx,
                                        "wy": wy,
                                        "dx": log_dx,
                                        "dy": log_dy,
                                        "raw_dx": coalesced_raw_dx,
                                        "raw_dy": coalesced_raw_dy,
                                    })
                                    coalesced_raw_dx = 0
                                    coalesced_raw_dy = 0

                                for btn_code, pressed in frame.buttons:
                                    if (
                                        self.cfg.strip_trigger_chords
                                        and not pressed
                                        and btn_code in suppressed_up_codes
                                    ):
                                        suppressed_up_codes.discard(btn_code)
                                        continue

                                    # When swap is active (left_handed), physical right click (0x111) is the user's primary (left) action
                                    logical_code = btn_code
                                    if should_swap:
                                        if btn_code == BTN_RIGHT:
                                            logical_code = BTN_LEFT
                                        elif btn_code == BTN_LEFT:
                                            logical_code = BTN_RIGHT

                                    d_us = max(0, t_us - prev_t_us)
                                    prev_t_us = t_us
                                    events.append({
                                        "t_us": t_us,
                                        "d_us": d_us,
                                        "type": "mouse_button",
                                        "button": BUTTON_CODE_TO_NAME.get(logical_code, "left"),
                                        "code": logical_code,
                                        "raw_code": btn_code,
                                        "state": "down" if pressed else "up",
                                        "x": cur_x,
                                        "y": cur_y,
                                        "wx": wx,
                                        "wy": wy,
                                    })

                        if frame.wheel_dy or frame.wheel_dx:
                            if t0_ns is None:
                                t0_ns = frame.timestamp_ns
                            t_us = max(prev_t_us, (frame.timestamp_ns - t0_ns) // 1000)
                            d_us = max(0, t_us - prev_t_us)
                            prev_t_us = t_us
                            events.append({
                                "t_us": t_us,
                                "d_us": d_us,
                                "type": "mouse_scroll",
                                "dy": frame.wheel_dy,
                                "dx": frame.wheel_dx,
                            })
        finally:
            RuntimeBeacon.clear_state()

        if self.cfg.strip_trigger_chords:
            while events and events[-1]["type"] in ("key", "mouse_button") and events[-1]["state"] == "down":
                if events[-1]["code"] in self.stop_matcher.all_chord_codes:
                    events.pop()
                else:
                    break

        layout, variant, options = self.ipc.active_keyboard_layout()
        return {
            "schema_version": 1,
            "name": name,
            "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "profile_id": self.cfg.profile_id,
            "environment": {
                "compositor": "Hyprland",
                "config_provider": self.ipc.config_provider(),
                "keyboard_layout": {
                    "layout": layout,
                    "variant": variant,
                    "options": options,
                },
                "monitors": self.topology.monitors,
            },
            "settings": {
                "coordinate_mode": self.coordinate_mode,
                "scale_window_relative": self.cfg.scale_window_relative,
                "click_at_current_cursor": self.cfg.click_at_current_cursor,
                "background_window_keys": self.cfg.background_window_keys,
                "strict_window_match": self.cfg.strict_window_match,
                "auto_focus_target_window": self.cfg.auto_focus_target_window,
                "abort_on_focus_loss": self.cfg.abort_on_focus_loss,
                "speed_multiplier": self.cfg.speed_multiplier,
            },
            "target_context": {
                "class": target_win.get("class", ""),
                "title_pattern": re.escape(target_win.get("title", "")) if target_win.get("title") else "",
                "workspace": target_win.get("workspace"),
                "recorded_window_rect": target_win.get("rect", [0, 0, 0, 0]),
                "floating": target_win.get("floating", False),
            },
            "events": events,
        }


class MacroPlayer:
    """High-precision macro playback engine with Coordinate Mode resolution,
    pre-click cursor settle, click_at_current_cursor, background sendshortcut key delivery,
    dusky_polkit / session-lock safety guards, and guaranteed release_all() cleanup."""

    def __init__(
        self,
        ipc: HyprlandSocketClient,
        topology: DesktopTopology,
        injector: WaylandWireInjector,
        cfg: EffectiveConfig,
        abort_hotkey: str | None = None,
        pause_hotkey: str | None = None,
        allow_auth: bool = False,
    ) -> None:
        self.ipc = ipc
        self.topology = topology
        self.injector = injector
        self.cfg = cfg
        self.abort_matcher = HotkeyMatcher(abort_hotkey or cfg.abort_hotkey)
        self.pause_matcher = HotkeyMatcher(pause_hotkey or cfg.pause_hotkey)
        self.allow_auth = allow_auth
        self.abort_event = threading.Event()
        self.pause_event = threading.Event()

    @staticmethod
    def normalize_timestamps(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not events:
            return []
        normalized: list[dict[str, Any]] = []
        running_t = 0
        for idx, ev in enumerate(events):
            ev_copy = dict(ev)
            if idx == 0:
                running_t = int(ev_copy.get("d_us", ev_copy.get("t_us", 0)))
            else:
                if "d_us" in ev_copy:
                    running_t += int(ev_copy["d_us"])
                else:
                    running_t = int(ev_copy.get("t_us", running_t))
            ev_copy["t_us"] = max(0, running_t)
            normalized.append(ev_copy)
        return normalized

    def _verify_safety_guards(self, expected_win_addr: str | None, abort_on_focus_loss: bool) -> None:
        if self.cfg.abort_on_session_lock and self.ipc.is_locked():
            raise SafetyAbortError("Session is locked (ext-session-lock-v1); aborting playback.")
        if not self.allow_auth:
            overlay = self.topology.has_blocking_overlay(
                block_auth=self.cfg.block_on_auth_layer,
                block_launcher=self.cfg.block_on_launcher_layer,
            )
            if overlay:
                raise SafetyAbortError(f"Blocking {overlay} detected; refusing to inject input.")
        if abort_on_focus_loss and expected_win_addr:
            cur_addr = self.topology.active_window.get("address")
            if cur_addr and cur_addr != expected_win_addr:
                raise SafetyAbortError(
                    f"Focus moved from target window {expected_win_addr} to {cur_addr}; aborting."
                )

    def _hotkey_watchdog_loop(self, stop_flag: threading.Event) -> None:
        need_mouse = self.abort_matcher.needs_mouse_buttons or self.pause_matcher.needs_mouse_buttons
        try:
            with EvdevInputSession(
                capture_mouse=need_mouse,
                capture_keyboard=True,
                use_kernel_monotonic_ts=False,
                rescan_interval_s=self.cfg.hotplug_rescan_interval_s,
                ignore_virtual=self.cfg.ignore_virtual_devices,
                extra_ignored=self.cfg.extra_ignored_keywords,
            ) as session:
                while not stop_flag.is_set() and not self.abort_event.is_set():
                    for frame in session.poll_frames(timeout_s=0.012):
                        for code, pressed in frame.keys + frame.buttons:
                            held = session.update_held(frame.source_fd, code, pressed)
                            if self.abort_matcher.matches_press(code, pressed, held):
                                self.abort_event.set()
                                return
                            if self.pause_matcher.matches_press(code, pressed, held):
                                if self.pause_event.is_set():
                                    self.pause_event.clear()
                                else:
                                    self.pause_event.set()
        except Exception:
            return

    def play(
        self,
        macro: dict[str, Any],
        override_mode: str | None = None,
        override_speed: float | None = None,
        override_repeat: int | None = None,
        override_click_current: bool | None = None,
        override_bg_keys: bool | None = None,
        dry_run: bool = False,
        spawn_watchdog: bool = True,
    ) -> dict[str, Any]:
        m_settings = macro.get("settings", {})
        coord_mode = override_mode or m_settings.get("coordinate_mode") or self.cfg.coordinate_mode
        speed = float(override_speed or m_settings.get("speed_multiplier") or self.cfg.speed_multiplier)
        repeat = int(override_repeat if override_repeat is not None else self.cfg.repeat_count)
        if speed <= 0:
            raise DuskyMacroError("speed_multiplier must be > 0")

        scale_win_rel = bool(m_settings.get("scale_window_relative", self.cfg.scale_window_relative))
        click_cur = (
            override_click_current
            if override_click_current is not None
            else bool(m_settings.get("click_at_current_cursor", self.cfg.click_at_current_cursor))
        )
        bg_keys = (
            override_bg_keys
            if override_bg_keys is not None
            else bool(m_settings.get("background_window_keys", self.cfg.background_window_keys))
        )
        strict_match = bool(m_settings.get("strict_window_match", self.cfg.strict_window_match))
        auto_focus = (
            False
            if bg_keys
            else bool(m_settings.get("auto_focus_target_window", self.cfg.auto_focus_target_window))
        )
        abort_focus_loss = (
            False
            if bg_keys
            else bool(m_settings.get("abort_on_focus_loss", self.cfg.abort_on_focus_loss))
        )

        target_ctx = macro.get("target_context", {})
        target_class = target_ctx.get("class", "")
        target_title = target_ctx.get("title_pattern", "")
        recorded_rect = target_ctx.get("recorded_window_rect", [0, 0, 0, 0])

        events = self.normalize_timestamps(macro.get("events", []))
        if not events:
            return {"status": "ok", "executed_events": 0, "dry_run": dry_run}

        self.topology.refresh_full_snapshot()
        self.topology.start_event_listener()

        expected_addr: str | None = None
        matched_win: dict[str, Any] | None = None
        if target_class:
            matched_win = self.topology.find_matching_window(target_class, target_title)
            if matched_win is None and (strict_match or bg_keys):
                raise DuskyMacroError(
                    f"Target window class={target_class!r} title={target_title!r} not found"
                )
            if matched_win is not None:
                expected_addr = matched_win["address"]
                if bg_keys:
                    if matched_win.get("xwayland", False):
                        raise DuskyMacroError(
                            f"background_window_keys (sendshortcut) is unsupported on XWayland window {expected_addr}"
                        )
                    grp_idx, _ = self.ipc.layout_group()
                    if grp_idx != 0:
                        raise DuskyMacroError(
                            "background_window_keys (sendshortcut) requires the primary keyboard layout (group 0) to be active"
                        )
                elif auto_focus and not dry_run:
                    cur_active = self.topology.refresh_active_window()
                    if cur_active.get("address") != expected_addr:
                        self.ipc.dispatch("focuswindow", f"address:{expected_addr}")
                        self.topology.wait_for_event("activewindowv2", expected_addr[2:], timeout_s=0.4)
                        self.topology.refresh_active_window()

        if dry_run:
            return {
                "status": "dry_run",
                "profile": self.cfg.profile_id,
                "coordinate_mode": coord_mode,
                "background_window_keys": bg_keys,
                "speed": speed,
                "repeat": repeat,
                "planned_events": len(events) * repeat,
                "target_window_address": expected_addr,
            }

        self.pause_event.clear()
        watchdog_stop = threading.Event()
        if spawn_watchdog:
            watchdog = threading.Thread(
                target=self._hotkey_watchdog_loop, args=(watchdog_stop,), name="AbortWatchdog", daemon=True
            )
            watchdog.start()

        check_interval = self.cfg.safety_check_interval_events
        sync_btn = self.cfg.sync_every_button
        sync_key = self.cfg.sync_every_key
        settle_ns = self.cfg.pre_click_settle_ns
        executed = 0

        bg_held_mods: set[str] = set()
        mod_code_to_hypr: dict[int, str] = {
            29: "CTRL", 97: "CTRL",
            42: "SHIFT", 54: "SHIFT",
            56: "ALT", 100: "MOD5",
            125: "SUPER", 126: "SUPER",
        }

        RuntimeBeacon.write_state("PLAYING", self.cfg.profile_id, str(macro.get("name", "")))
        try:
            for _ in range(max(1, repeat)):
                self._verify_safety_guards(expected_addr, abort_focus_loss)
                cur_x, cur_y = self.ipc.cursor_pos_fast()
                log_x, log_y = float(cur_x), float(cur_y)
                t0_ns = _monotonic_ns()

                for idx, ev in enumerate(events):
                    if self.abort_event.is_set():
                        raise SafetyAbortError("Emergency abort or termination signal received.")

                    target_offset_ns = int((ev["t_us"] * 1000) / speed)
                    paused_ns = PrecisionClock.wait_until_ns(
                        t0_ns + target_offset_ns,
                        self.abort_event,
                        self.pause_event,
                        spin_threshold_ns=self.cfg.spin_threshold_ns,
                        max_coarse_sleep_s=self.cfg.max_coarse_sleep_s,
                    )
                    t0_ns += paused_ns

                    if self.abort_event.is_set():
                        raise SafetyAbortError("Emergency abort or termination signal received.")

                    if idx % check_interval == 0:
                        self._verify_safety_guards(expected_addr, abort_focus_loss)

                    match ev.get("type"):
                        case "mouse_move":
                            if not bg_keys and not click_cur and coord_mode != "current_position":
                                log_x, log_y = self._execute_mouse_move(
                                    ev, coord_mode, log_x, log_y, recorded_rect, scale_win_rel
                                )
                        case "mouse_button":
                            if bg_keys:
                                continue
                            use_cur = (
                                click_cur
                                or coord_mode == "current_position"
                                or bool(ev.get("use_current_position", False))
                            )
                            if not use_cur and coord_mode in ("absolute", "window_relative", "logical_relative"):
                                prev_lx, prev_ly = log_x, log_y
                                log_x, log_y = self._execute_mouse_move(
                                    ev, coord_mode, log_x, log_y, recorded_rect, scale_win_rel
                                )
                                if settle_ns > 0 and (abs(log_x - prev_lx) > 1.0 or abs(log_y - prev_ly) > 1.0):
                                    PrecisionClock.wait_until_ns(
                                        _monotonic_ns() + settle_ns,
                                        self.abort_event,
                                        spin_threshold_ns=self.cfg.spin_threshold_ns,
                                    )
                            btn_code = int(
                                ev.get("code") or BUTTON_NAME_TO_CODE.get(ev.get("button", "left"), BTN_LEFT)
                            )
                            self.injector.emit_button(
                                btn_code, pressed=(ev.get("state") == "down"), sync=sync_btn
                            )
                        case "mouse_scroll":
                            if not bg_keys:
                                self.injector.emit_scroll(
                                    dy=float(ev.get("dy", 0.0)), dx=float(ev.get("dx", 0.0)), sync=sync_btn
                                )
                        case "key":
                            code = int(ev["code"])
                            pressed = ev.get("state") == "down"
                            if bg_keys and expected_addr:
                                if code in mod_code_to_hypr:
                                    if pressed:
                                        bg_held_mods.add(mod_code_to_hypr[code])
                                    else:
                                        bg_held_mods.discard(mod_code_to_hypr[code])
                                elif pressed:
                                    base_sym = self.injector.xkb.keycode_to_base_symbol(code) or ev.get("symbol", "")
                                    if base_sym:
                                        mods_str = " ".join(sorted(bg_held_mods))
                                        self.ipc.send_shortcut(mods_str, base_sym, expected_addr)
                            else:
                                self.injector.emit_key(code, pressed=pressed, sync=sync_key)
                        case "text":
                            txt = str(ev.get("text", ""))
                            if bg_keys and expected_addr:
                                for ch in txt:
                                    chord = self.injector.xkb.char_to_sendshortcut_chord(ch)
                                    if chord is not None:
                                        self.ipc.send_shortcut(chord[0], chord[1], expected_addr)
                            else:
                                self.injector.type_unicode_text(txt)
                        case "hypr_dispatch":
                            self.ipc.dispatch(str(ev["dispatcher"]), *(str(a) for a in ev.get("args", [])))
                        case "wait_event":
                            timeout_s = float(ev.get("timeout_ms", 5000)) / 1000.0
                            self.topology.wait_for_event(
                                str(ev["event"]), str(ev.get("match", "")), timeout_s=timeout_s
                            )
                            t0_ns = _monotonic_ns() - target_offset_ns

                    executed += 1
        finally:
            watchdog_stop.set()
            self.injector.release_all()
            RuntimeBeacon.clear_state()

        return {
            "status": "ok",
            "profile": self.cfg.profile_id,
            "executed_events": executed,
            "coordinate_mode": coord_mode,
            "background_window_keys": bg_keys,
        }

    def _execute_mouse_move(
        self,
        ev: dict[str, Any],
        coord_mode: str,
        log_x: float,
        log_y: float,
        recorded_rect: list[int],
        scale_win_rel: bool,
    ) -> tuple[float, float]:
        if coord_mode == "raw_relative":
            rdx = float(ev.get("raw_dx", ev.get("dx", 0)))
            rdy = float(ev.get("raw_dy", ev.get("dy", 0)))
            self.injector.move_relative(rdx, rdy, sync=False)
            return log_x + rdx, log_y + rdy

        if coord_mode == "logical_relative":
            log_x += float(ev.get("dx", 0))
            log_y += float(ev.get("dy", 0))
            cx, cy = self.topology.clamp_to_monitors(log_x, log_y)
            self.ipc.move_cursor(cx, cy)
            return float(cx), float(cy)

        if coord_mode == "window_relative" and "wx" in ev and "wy" in ev:
            win = self.topology.active_window
            wrect = win.get("rect", [0, 0, 0, 0])
            if (len(wrect) < 4 or wrect[2] <= 0 or wrect[3] <= 0) and len(recorded_rect) == 4:
                wrect = recorded_rect
            if len(wrect) == 4 and (wrect[2] > 0 or wrect[3] > 0):
                wx = float(ev["wx"])
                wy = float(ev["wy"])
                if (
                    scale_win_rel
                    and len(recorded_rect) == 4
                    and recorded_rect[2] > 0
                    and recorded_rect[3] > 0
                    and wrect[2] > 0
                    and wrect[3] > 0
                ):
                    wx = wx * (wrect[2] / recorded_rect[2])
                    wy = wy * (wrect[3] / recorded_rect[3])
                tx = wrect[0] + int(round(wx))
                ty = wrect[1] + int(round(wy))
                cx, cy = self.topology.clamp_to_monitors(tx, ty)
                self.ipc.move_cursor(cx, cy)
                return float(cx), float(cy)

        tx = int(ev.get("x", log_x))
        ty = int(ev.get("y", log_y))
        cx, cy = self.topology.clamp_to_monitors(tx, ty)
        self.ipc.move_cursor(cx, cy)
        return float(cx), float(cy)


# =============================================================================
# SECTION 8: RICH INTERACTIVE TERMINAL UI (DuskyMacroTUI)
# =============================================================================

class _CbreakTerminal:
    """Context manager placing stdin into cbreak mode for instant single-key navigation,
    and cleanly restoring canonical terminal settings for modal prompts or exit."""

    def __init__(self) -> None:
        self._fd: int | None = None
        self._old_attrs: Any = None

    def __enter__(self) -> _CbreakTerminal:
        if sys.stdin.isatty():
            import termios
            import tty

            self._fd = sys.stdin.fileno()
            self._old_attrs = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._fd is not None and self._old_attrs is not None:
            import termios

            with contextlib.suppress(Exception):
                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old_attrs)
        self._fd = None
        self._old_attrs = None

    @staticmethod
    def read_key(timeout_s: float = 0.10) -> str | None:
        if not sys.stdin.isatty():
            time.sleep(timeout_s)
            return None
        rlist, _, _ = select.select([sys.stdin], [], [], timeout_s)
        if not rlist:
            return None
        ch = os.read(sys.stdin.fileno(), 1).decode("utf-8", errors="ignore")
        if ch == "\x1b":
            r2, _, _ = select.select([sys.stdin], [], [], 0.015)
            if r2:
                seq = os.read(sys.stdin.fileno(), 2).decode("utf-8", errors="ignore")
                if seq == "[A":
                    return "UP"
                if seq == "[B":
                    return "DOWN"
                if seq == "[C":
                    return "RIGHT"
                if seq == "[D":
                    return "LEFT"
            return "ESC"
        return ch


class DuskyMacroTUI:
    """Full-screen Rich Interactive Terminal UI for DuskyMacro."""

    def __init__(self, settings: SettingsManager, profiles_mgr: ProfileManager) -> None:
        self.settings = settings
        self.profiles_mgr = profiles_mgr
        self.ipc = HyprlandSocketClient()
        self.xkb = XkbBridge(*self.ipc.active_keyboard_layout())

        self._lock = threading.RLock()
        self.auto_switch = bool(settings.raw.get("profiles", {}).get("auto_switch_by_window", True))
        self.locked_profile_id: str | None = None
        self.topology = DesktopTopology(self.ipc, on_active_window_change=self._on_window_change)
        self.topology.start_event_listener()

        self.current_prof = self.profiles_mgr.resolve_for_window(
            self.topology.active_window.get("class", ""),
            self.topology.active_window.get("title", ""),
        )
        self.cfg = self.settings.build_effective(self.current_prof)

        self.macro_files: list[Path] = []
        self.selected_idx: int = 0
        self._macro_summary_cache: dict[tuple[Path, int], dict[str, Any]] = {}

        # Override dials for interactive TUI playback
        self.override_mode: str | None = None
        self.override_speed: float = 1.0
        self.override_repeat: int = 1
        self.override_click_current: bool | None = None
        self.override_bg_keys: bool | None = None
        self.countdown_s: int = 0

        # Background listener state
        self.listener_enabled: bool = True
        self.engine_state: str = "LISTENING"
        self._listener_stop = threading.Event()
        self._listener_rebind = threading.Event()
        self._listener_pause = threading.Event()
        self._listener_thread: threading.Thread | None = None

        self.activity_log: collections.deque[tuple[str, str, str]] = collections.deque(maxlen=10)
        self._devices_summary: tuple[int, int] = (0, 0)
        self._last_dev_scan: float = 0.0

        self.refresh_macro_list()
        self._update_device_counts()
        self.log_event("INFO", f"Initialized DuskyMacro v{__version__} (Profile: {self.cfg.profile_id})")

    def log_event(self, tag: str, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        with self._lock:
            self.activity_log.append((stamp, tag, message))

    def _update_device_counts(self) -> None:
        now = time.monotonic()
        if now - self._last_dev_scan < 2.5:
            return
        self._last_dev_scan = now
        devs = EvdevDeviceScanner.scan(
            need_mouse=True,
            need_keyboard=True,
            ignore_virtual=self.cfg.ignore_virtual_devices,
            extra_ignored=self.cfg.extra_ignored_keywords,
        )
        m_cnt = sum(1 for d in devs if d.is_mouse)
        k_cnt = sum(1 for d in devs if d.is_keyboard)
        self._devices_summary = (m_cnt, k_cnt)

    def _on_window_change(self, win: dict[str, Any]) -> None:
        with self._lock:
            if not self.auto_switch or self.locked_profile_id is not None:
                return
            new_prof = self.profiles_mgr.resolve_for_window(win.get("class", ""), win.get("title", ""))
            if new_prof and (self.current_prof is None or new_prof.id != self.current_prof.id):
                self.current_prof = new_prof
                self.cfg = self.settings.build_effective(self.current_prof)
                self._listener_rebind.set()
                self.log_event("PROF", f"Auto-switched -> {new_prof.name} [{new_prof.id}]")
                if self.cfg.notify_on_state_change:
                    self.ipc.notify(
                        f"DuskyMacro Profile: {new_prof.name} ({new_prof.id})",
                        self.cfg.notification_timeout_ms,
                    )

    def refresh_macro_list(self) -> None:
        with self._lock:
            seen: set[Path] = set()
            ordered: list[Path] = []
            # Only list macro files that actually exist on disk
            for b in self.cfg.bindings:
                p = b.macro_file.resolve()
                if p.is_file() and p not in seen:
                    seen.add(p)
                    ordered.append(p)
            if self.settings.recordings_dir.is_dir():
                for p in sorted(self.settings.recordings_dir.glob("*.json")):
                    rp = p.resolve()
                    if rp.is_file() and rp not in seen:
                        seen.add(rp)
                        ordered.append(rp)
            self.macro_files = ordered
            if self.selected_idx >= len(self.macro_files):
                self.selected_idx = max(0, len(self.macro_files) - 1)

    def get_macro_summary(self, path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {"exists": False, "name": path.stem, "events": 0, "duration_s": 0.0, "mode": "-", "breakdown": {}}
        try:
            st = path.stat()
            cache_key = (path, st.st_mtime_ns)
            if cache_key in self._macro_summary_cache:
                return self._macro_summary_cache[cache_key]
            doc = json.loads(path.read_text(encoding="utf-8"))
            evs = doc.get("events", [])
            dur_s = (evs[-1]["t_us"] / 1_000_000.0) if evs else 0.0
            counts: dict[str, int] = {}
            for e in evs:
                t = e.get("type", "other")
                counts[t] = counts.get(t, 0) + 1
            info = {
                "exists": True,
                "name": doc.get("name", path.stem),
                "created_at": doc.get("created_at", "")[:19].replace("T", " "),
                "mode": doc.get("settings", {}).get("coordinate_mode", "window_relative"),
                "target_class": doc.get("target_context", {}).get("class") or "*",
                "target_ws": doc.get("target_context", {}).get("workspace"),
                "events": len(evs),
                "duration_s": round(dur_s, 2),
                "breakdown": counts,
            }
            self._macro_summary_cache[cache_key] = info
            return info
        except Exception:
            return {"exists": False, "name": path.stem, "events": 0, "duration_s": 0.0, "mode": "err", "breakdown": {}}

    def start_background_listener(self) -> None:
        if self._listener_thread and self._listener_thread.is_alive():
            return
        self._listener_stop.clear()
        self._listener_pause.clear()
        self.listener_enabled = True
        self.engine_state = "LISTENING"
        self._listener_thread = threading.Thread(
            target=self._background_listener_loop, name="TuiHotkeyListener", daemon=True
        )
        self._listener_thread.start()

    def stop_background_listener(self) -> None:
        self.listener_enabled = False
        self._listener_stop.set()
        self._listener_rebind.set()
        if self._listener_thread and self._listener_thread.is_alive():
            self._listener_thread.join(timeout=1.0)
        self.engine_state = "IDLE"
        RuntimeBeacon.clear_state()

    def cycle_profile(self, delta: int = 1) -> None:
        with self._lock:
            enabled = self.profiles_mgr.enabled_profiles_sorted_by_id()
            if not enabled:
                return
            if self.locked_profile_id is None:
                cur_idx = next(
                    (i for i, p in enumerate(enabled) if self.current_prof and p.id == self.current_prof.id),
                    0,
                )
                next_idx = (cur_idx + delta) % (len(enabled) + 1)
            else:
                cur_idx = next((i for i, p in enumerate(enabled) if p.id == self.locked_profile_id), 0)
                next_idx = (cur_idx + delta) % (len(enabled) + 1)

            if next_idx == len(enabled):
                self.locked_profile_id = None
                win = self.topology.active_window
                self.current_prof = self.profiles_mgr.resolve_for_window(
                    win.get("class", ""), win.get("title", "")
                )
                self.cfg = self.settings.build_effective(self.current_prof)
                self.log_event("PROF", f"Switched to AUTO Window-Match ({self.cfg.profile_id})")
            else:
                chosen = enabled[next_idx]
                self.locked_profile_id = chosen.id
                self.current_prof = chosen
                self.cfg = self.settings.build_effective(self.current_prof)
                self.log_event("PROF", f"Locked profile -> {chosen.name} [{chosen.id}]")

            self.refresh_macro_list()
            self._listener_rebind.set()

    def _background_listener_loop(self) -> None:
        while not self._listener_stop.is_set():
            if self._listener_pause.is_set():
                time.sleep(0.05)
                continue

            with self._lock:
                active_cfg = self.cfg
                rec_matcher = HotkeyMatcher(active_cfg.record_hotkey)
                next_matcher = HotkeyMatcher(active_cfg.cycle_next_hotkey)
                prev_matcher = HotkeyMatcher(active_cfg.cycle_prev_hotkey)
                slot_matchers = [(HotkeyMatcher(b.hotkey), b) for b in active_cfg.bindings]

            self._listener_rebind.clear()
            self.engine_state = "LISTENING"
            RuntimeBeacon.write_state("LISTENING", active_cfg.profile_id)

            need_mouse = (
                rec_matcher.needs_mouse_buttons
                or next_matcher.needs_mouse_buttons
                or prev_matcher.needs_mouse_buttons
                or any(m.needs_mouse_buttons for m, _ in slot_matchers)
            )

            triggered_action: str | None = None
            triggered_binding: MacroBinding | None = None
            held_snapshot: set[int] = set()

            try:
                with EvdevInputSession(
                    capture_mouse=need_mouse,
                    capture_keyboard=True,
                    use_kernel_monotonic_ts=False,
                    rescan_interval_s=active_cfg.hotplug_rescan_interval_s,
                    ignore_virtual=active_cfg.ignore_virtual_devices,
                    extra_ignored=active_cfg.extra_ignored_keywords,
                ) as session:
                    last_scan = time.monotonic()
                    while (
                        triggered_action is None
                        and not self._listener_stop.is_set()
                        and not self._listener_rebind.is_set()
                        and not self._listener_pause.is_set()
                    ):
                        last_scan = session.rescan_if_due(last_scan)
                        for frame in session.poll_frames(timeout_s=active_cfg.epoll_poll_timeout_s):
                            for code, pressed in frame.keys + frame.buttons:
                                held = session.update_held(frame.source_fd, code, pressed)
                                if rec_matcher.matches_press(code, pressed, held):
                                    triggered_action = "record"
                                    held_snapshot = set(held)
                                    break
                                if next_matcher.matches_press(code, pressed, held):
                                    triggered_action = "cycle_next"
                                    break
                                if prev_matcher.matches_press(code, pressed, held):
                                    triggered_action = "cycle_prev"
                                    break
                                for s_matcher, binding in slot_matchers:
                                    if s_matcher.matches_press(code, pressed, held):
                                        triggered_action = "play"
                                        triggered_binding = binding
                                        break
                                if triggered_action:
                                    break
                            if triggered_action:
                                break
            except Exception as exc:
                self.log_event("WARN", f"Evdev listener error: {exc}")
                time.sleep(0.5)
                continue

            if self._listener_stop.is_set() or self._listener_pause.is_set():
                continue

            if triggered_action == "cycle_next":
                self.cycle_profile(1)
            elif triggered_action == "cycle_prev":
                self.cycle_profile(-1)
            elif triggered_action == "record":
                target_file = (
                    self.macro_files[self.selected_idx]
                    if self.macro_files
                    else active_cfg.default_macro_file
                )
                target_file.parent.mkdir(parents=True, exist_ok=True)
                self.engine_state = "RECORDING"
                self.log_event("REC", f"Recording started -> {target_file.name} (Press [{rec_matcher.spec.upper()}] to stop)")
                if active_cfg.notify_on_state_change:
                    self.ipc.notify(
                        f"DuskyMacro: Recording -> {target_file.name}",
                        active_cfg.notification_timeout_ms,
                    )
                try:
                    with RuntimeBeacon.acquire_seat_lock():
                        recorder = MacroRecorder(
                            ipc=self.ipc,
                            topology=self.topology,
                            xkb=self.xkb,
                            cfg=active_cfg,
                            stop_hotkey=rec_matcher.spec,
                        )
                        doc = recorder.record(
                            name=target_file.stem,
                            initial_suppressed_keys=held_snapshot,
                            abort_event=self._listener_stop,
                        )
                        target_file.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
                    self.refresh_macro_list()
                    self.log_event("SAVE", f"Saved {len(doc['events'])} events to {target_file.name}")
                    if active_cfg.notify_on_state_change:
                        self.ipc.notify(
                            f"DuskyMacro: Saved {len(doc['events'])} events ({target_file.name})",
                            active_cfg.notification_timeout_ms,
                        )
                except Exception as exc:
                    self.log_event("ERR", f"Record failed: {exc}")
            elif triggered_action == "play" and triggered_binding is not None:
                m_file = triggered_binding.macro_file
                if not m_file.is_file():
                    self.log_event("WARN", f"Binding macro missing: {m_file.name}")
                    continue
                self.engine_state = "PLAYING"
                self.log_event("PLAY", f"Triggered [{triggered_binding.hotkey.upper()}] -> {m_file.name}")
                try:
                    with RuntimeBeacon.acquire_seat_lock():
                        macro = json.loads(m_file.read_text(encoding="utf-8"))
                        with WaylandWireInjector(self.xkb) as injector:
                            player = MacroPlayer(
                                ipc=self.ipc,
                                topology=self.topology,
                                injector=injector,
                                cfg=active_cfg,
                            )
                            res = player.play(
                                macro,
                                override_mode=triggered_binding.coordinate_mode or self.override_mode,
                                override_speed=triggered_binding.speed_multiplier or self.override_speed,
                                override_repeat=triggered_binding.repeat_count or self.override_repeat,
                                override_click_current=(
                                    triggered_binding.click_at_current_cursor
                                    if triggered_binding.click_at_current_cursor is not None
                                    else self.override_click_current
                                ),
                                override_bg_keys=(
                                    triggered_binding.background_window_keys
                                    if triggered_binding.background_window_keys is not None
                                    else self.override_bg_keys
                                ),
                            )
                            self.log_event("DONE", f"Completed {m_file.name} ({res['executed_events']} events)")
                except SafetyAbortError as exc:
                    self.log_event("ABORT", str(exc))
                except Exception as exc:
                    self.log_event("ERR", f"Play error: {exc}")

    def render_dashboard(self) -> Any:
        from rich.console import Group
        from rich.layout import Layout
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text

        self._update_device_counts()
        if self.profiles_mgr.reload_if_modified():
            with self._lock:
                self.current_prof = self.profiles_mgr.get(self.locked_profile_id)
                self.cfg = self.settings.build_effective(self.current_prof)
                self.refresh_macro_list()
                self._listener_rebind.set()
            self.log_event("CONF", "Hot-reloaded modified TOML configuration")

        with self._lock:
            cfg = self.cfg
            win = self.topology.active_window
            mons = self.topology.monitors
            overlay = self.topology.has_blocking_overlay()
            macro_files = list(self.macro_files)
            sel_idx = self.selected_idx
            logs = list(self.activity_log)
            state = self.engine_state if self.listener_enabled else "IDLE"
            sw_mode = f"LOCKED ({self.locked_profile_id})" if self.locked_profile_id else "AUTO (Window-Match)"

        state_colors = {
            "IDLE": "dim white",
            "LISTENING": "bold green",
            "RECORDING": "bold red",
            "PLAYING": "bold cyan",
        }
        badge_style = state_colors.get(state, "bold white")

        prov = self.ipc.config_provider().upper()
        header_text = Text.assemble(
            (" DUSKY MACRO ", "bold black on cyan"),
            (f" v{__version__} ", "bold cyan"),
            ("│ ", "dim"),
            (f"Kernel {os.uname().release.split('-')[0]} ", "white"),
            ("│ ", "dim"),
            (f"Hyprland ({prov}) ", "magenta"),
            ("│ ", "dim"),
            ("Wayland Wire: OK ", "green"),
            ("│ ", "dim"),
            (f"● {state} ", badge_style),
            justify="center",
        )
        header_panel = Panel(header_text, border_style="cyan", padding=(0, 1))

        cur_x, cur_y = 0, 0
        with contextlib.suppress(Exception):
            cur_x, cur_y = self.ipc.cursor_pos_fast()

        win_cls = win.get("class") or "(empty workspace)"
        win_ttl = (win.get("title") or "")[:28]
        win_addr = win.get("address") or "-"
        win_rect = win.get("rect") or [0, 0, 0, 0]
        ws_id = win.get("workspace") or (mons[0]["active_workspace"] if mons else "-")
        mon_desc = f"{mons[0]['name']} @ {mons[0]['scale']}x" if mons else "unknown"
        safety_str = f"[bold red]BLOCKED ({overlay})[/]" if overlay else "[bold green]READY (Unlocked)[/]"
        m_cnt, k_cnt = self._devices_summary

        telemetry_tbl = Table.grid(padding=(0, 1))
        telemetry_tbl.add_column(style="bold cyan", width=14)
        telemetry_tbl.add_column(style="white")
        telemetry_tbl.add_row("Focused Win", f"{win_cls} [dim]{win_ttl!r}[/]")
        telemetry_tbl.add_row("Win Geometry", f"{win_addr} {win_rect}")
        telemetry_tbl.add_row("Workspace", f"{ws_id} [dim](Monitor: {mon_desc})[/]")
        telemetry_tbl.add_row("Cursor Pos", f"({cur_x}, {cur_y})   [dim]Layout: {self.xkb.layout}[/]")
        telemetry_tbl.add_row("Safety Guard", safety_str)
        telemetry_tbl.add_row("Evdev Nodes", f"{m_cnt} Mice, {k_cnt} Keyboards [green](Epoll Ready)[/]")

        prof_tbl = Table.grid(padding=(0, 1))
        prof_tbl.add_column(style="bold magenta", width=14)
        prof_tbl.add_column(style="white")
        prof_tbl.add_row("Profile", f"[bold]{cfg.profile_name}[/] [{cfg.profile_id}]")
        prof_tbl.add_row("Switch Mode", sw_mode)
        prof_tbl.add_row("Default Mode", f"{cfg.coordinate_mode} ({cfg.speed_multiplier}x, rep={cfg.repeat_count})")
        prof_tbl.add_row("Record Key", f"[bold yellow][{cfg.record_hotkey.upper()}][/]   Abort: [bold red][{cfg.abort_hotkey.upper()}][/]")
        prof_tbl.add_row("Cycle Keys", f"[{cfg.cycle_prev_hotkey.upper()}] / [{cfg.cycle_next_hotkey.upper()}]")
        binds_str = ", ".join(f"[bold green][{b.hotkey.upper()}][/]->{b.macro_file.stem}" for b in cfg.bindings)
        prof_tbl.add_row("Bindings", binds_str or "(none)")

        left_group = Group(
            Panel(telemetry_tbl, title="[bold cyan]Desktop & Hardware Telemetry[/]", border_style="blue"),
            Panel(prof_tbl, title="[bold magenta]Active Profile Configuration[/]", border_style="magenta"),
        )

        lib_tbl = Table(expand=True, box=None, pad_edge=False)
        lib_tbl.add_column("#", width=3, style="dim")
        lib_tbl.add_column("Macro File", style="bold")
        lib_tbl.add_column("Mode", width=10)
        lib_tbl.add_column("Events", justify="right", width=7)
        lib_tbl.add_column("Duration", justify="right", width=8)

        bound_files = {b.macro_file.resolve(): b.hotkey.upper() for b in cfg.bindings}
        for idx, m_path in enumerate(macro_files):
            info = self.get_macro_summary(m_path)
            is_sel = idx == sel_idx
            prefix = "▶ " if is_sel else "  "
            hk_tag = f" [{bound_files[m_path]}]" if m_path in bound_files else ""
            row_style = "bold black on bright_cyan" if is_sel else ("white" if info["exists"] else "dim yellow")
            ev_str = str(info["events"]) if info["exists"] else "missing"
            dur_str = f"{info['duration_s']:.2f}s" if info["exists"] else "-"
            lib_tbl.add_row(
                f"{prefix}{idx + 1}",
                f"{m_path.name}{hk_tag}",
                str(info["mode"])[:10],
                ev_str,
                dur_str,
                style=row_style,
            )

        insp_tbl = Table.grid(padding=(0, 1))
        insp_tbl.add_column(style="bold yellow", width=15)
        insp_tbl.add_column(style="white")
        if macro_files:
            sel_path = macro_files[sel_idx]
            s_info = self.get_macro_summary(sel_path)
            bd_str = ", ".join(f"{k}:{v}" for k, v in s_info.get("breakdown", {}).items()) or "none"
            insp_tbl.add_row("Selected File", str(sel_path.name))
            insp_tbl.add_row("Target Context", f"class={s_info.get('target_class', '*')}  ws={s_info.get('target_ws', '*')}")
            insp_tbl.add_row("Event Summary", f"{s_info['events']} total ({bd_str})")
        else:
            insp_tbl.add_row("Selected File", "(no macros found)")
        insp_tbl.add_row(
            "TUI Overrides",
            f"Mode=[bold]{self.override_mode or 'Profile'}[/]  "
            f"Speed=[bold]{self.override_speed:.2f}x[/]  "
            f"Rep=[bold]{self.override_repeat}x[/]  "
            f"CurClick=[bold]{self.override_click_current}[/]  "
            f"BgKeys=[bold]{self.override_bg_keys}[/]",
        )

        log_lines: list[Text] = []
        for stamp, tag, msg in logs[-5:]:
            t_color = "green" if tag in ("DONE", "SAVE") else ("red" if tag in ("ERR", "ABORT") else "cyan")
            log_lines.append(Text.assemble((f"{stamp} ", "dim"), (f"[{tag:5}] ", f"bold {t_color}"), (msg, "white")))
        if not log_lines:
            log_lines.append(Text("No activity yet.", style="dim"))

        right_group = Group(
            Panel(lib_tbl, title=f"[bold green]Macro Library ({self.settings.recordings_dir})[/]", border_style="green"),
            Panel(insp_tbl, title="[bold yellow]Selected Macro Inspector & Playback Dials[/]", border_style="yellow"),
            Panel(Group(*log_lines), title="[bold white]Live Activity Log[/]", border_style="bright_black"),
        )

        body = Table.grid(expand=True, padding=(0, 1))
        body.add_column(ratio=1)
        body.add_column(ratio=1)
        body.add_row(left_group, right_group)

        footer_text = Text.assemble(
            (" [L]", "bold cyan"), (" Listener ON/OFF  ", "white"),
            ("[R]", "bold red"), (" Record Wizard  ", "white"),
            ("[P]", "bold green"), (" Play Selected  ", "white"),
            ("[O]", "bold yellow"), (" Override Dials  ", "white"),
            ("[S]", "bold magenta"), (" Switch Profile\n", "white"),
            (" [↑/↓ or J/K]", "bold cyan"), (" Select Macro  ", "white"),
            ("[N]", "bold cyan"), (" New Macro Slot  ", "white"),
            ("[D]", "bold red"), (" Delete Macro  ", "white"),
            ("[X]", "bold yellow"), (" Doctor Check  ", "white"),
            ("[Q]", "bold red"), (" Quit", "white"),
        )
        footer_panel = Panel(footer_text, border_style="bright_black", padding=(0, 1))

        root = Layout()
        root.split_column(
            Layout(header_panel, size=3),
            Layout(body, ratio=1),
            Layout(footer_panel, size=4),
        )
        return root

    def _interactive_record_wizard(self, console: Any) -> None:
        from rich.prompt import Confirm, Prompt

        self._listener_pause.set()
        try:
            console.clear()
            console.rule("[bold red]DuskyMacro — Guided Recording Wizard[/]")
            default_name = (
                self.macro_files[self.selected_idx].stem
                if self.macro_files
                else "quick.macro"
            )
            if default_name.endswith(".macro"):
                default_name = default_name[:-6]
            raw_name = Prompt.ask(
                f"Macro name (saved in {self.settings.recordings_dir})", default=default_name
            ).strip()
            if not raw_name:
                return
            filename = raw_name if raw_name.endswith(".json") else f"{raw_name}.macro.json"
            out_path = self.settings.resolve_macro_path(filename)
            out_path.parent.mkdir(parents=True, exist_ok=True)

            mode = Prompt.ask(
                "Coordinate mode",
                choices=["window_relative", "raw_relative", "absolute", "logical_relative", "current_position"],
                default=self.cfg.coordinate_mode,
            )
            trigger_style = Prompt.ask(
                "Start method",
                choices=["hotkey", "countdown"],
                default="hotkey",
            )
            stop_hk = self.cfg.record_hotkey
            recorder = MacroRecorder(
                ipc=self.ipc,
                topology=self.topology,
                xkb=self.xkb,
                cfg=self.cfg,
                stop_hotkey=stop_hk,
                coordinate_mode=mode,
            )

            held: set[int] = set()
            if trigger_style == "countdown":
                secs = int(Prompt.ask("Countdown seconds before recording starts", default="3"))
                for remaining in range(max(1, secs), 0, -1):
                    console.print(f"  Starting in [bold yellow]{remaining}[/]s... (Focus your target window now!)")
                    self.ipc.notify(f"DuskyMacro: Recording in {remaining}s...", 950)
                    time.sleep(1.0)
            else:
                start_matcher = HotkeyMatcher(stop_hk)
                console.print(
                    f"\nFocus your target window and press [bold yellow][{stop_hk.upper()}][/] to START recording..."
                )
                with EvdevInputSession(
                    capture_mouse=start_matcher.needs_mouse_buttons,
                    capture_keyboard=True,
                    use_kernel_monotonic_ts=False,
                    rescan_interval_s=self.cfg.hotplug_rescan_interval_s,
                    ignore_virtual=self.cfg.ignore_virtual_devices,
                    extra_ignored=self.cfg.extra_ignored_keywords,
                ) as session:
                    started = False
                    while not started:
                        for frame in session.poll_frames(timeout_s=0.015):
                            for code, pressed in frame.keys + frame.buttons:
                                held = session.update_held(frame.source_fd, code, pressed)
                                if start_matcher.matches_press(code, pressed, held):
                                    started = True
                                    break
                            if started:
                                break

            console.print(f"[bold red]● RECORDING![/] Perform your actions, then press [bold yellow][{stop_hk.upper()}][/] to STOP.")
            self.ipc.notify(f"DuskyMacro: Recording {out_path.name}", self.cfg.notification_timeout_ms)
            with RuntimeBeacon.acquire_seat_lock():
                doc = recorder.record(name=out_path.stem, initial_suppressed_keys=held)
                out_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
            self.refresh_macro_list()
            for idx, p in enumerate(self.macro_files):
                if p.resolve() == out_path.resolve():
                    self.selected_idx = idx
                    break
            self.log_event("SAVE", f"Wizard recorded {len(doc['events'])} events -> {out_path.name}")
            self.ipc.notify(f"DuskyMacro: Saved {len(doc['events'])} events", self.cfg.notification_timeout_ms)
            if Confirm.ask(f"Saved {len(doc['events'])} events to {out_path.name}. Return to dashboard?", default=True):
                return
        except KeyboardInterrupt:
            self.log_event("INFO", "Recording wizard cancelled")
        finally:
            self._listener_pause.clear()
            self._listener_rebind.set()

    def _interactive_override_dials(self, console: Any) -> None:
        from rich.prompt import Prompt

        self._listener_pause.set()
        try:
            console.clear()
            console.rule("[bold yellow]DuskyMacro — Configure Playback Override Dials[/]")
            mode_choice = Prompt.ask(
                "Coordinate Mode Override",
                choices=["profile", "window_relative", "raw_relative", "absolute", "logical_relative", "current_position"],
                default=self.override_mode or "profile",
            )
            self.override_mode = None if mode_choice == "profile" else mode_choice
            spd = Prompt.ask("Playback Speed Multiplier (e.g. 0.5, 1.0, 2.0)", default=str(self.override_speed))
            with contextlib.suppress(ValueError):
                self.override_speed = max(0.05, float(spd))
            rep = Prompt.ask("Repeat Count", default=str(self.override_repeat))
            with contextlib.suppress(ValueError):
                self.override_repeat = max(1, int(rep))
            cur_c = Prompt.ask(
                "Click at current manual cursor position?",
                choices=["profile", "true", "false"],
                default="profile" if self.override_click_current is None else str(self.override_click_current).lower(),
            )
            self.override_click_current = None if cur_c == "profile" else (cur_c == "true")
            bg_k = Prompt.ask(
                "Send keys in background via Hyprland sendshortcut?",
                choices=["profile", "true", "false"],
                default="profile" if self.override_bg_keys is None else str(self.override_bg_keys).lower(),
            )
            self.override_bg_keys = None if bg_k == "profile" else (bg_k == "true")
            cd = Prompt.ask("Pre-Playback Focus Countdown Seconds (0-10)", default=str(self.countdown_s))
            with contextlib.suppress(ValueError):
                self.countdown_s = max(0, min(10, int(cd)))
            self.log_event(
                "DIAL",
                f"Dials -> mode={self.override_mode or 'profile'}, speed={self.override_speed}x, rep={self.override_repeat}",
            )
        except KeyboardInterrupt:
            pass
        finally:
            self._listener_pause.clear()

    def _interactive_new_slot(self, console: Any) -> None:
        from rich.prompt import Prompt

        self._listener_pause.set()
        try:
            console.clear()
            console.rule("[bold cyan]DuskyMacro — Create New Macro Target File[/]")
            name = Prompt.ask("New macro filename stem (e.g. boss_combo)").strip()
            if not name:
                return
            fname = name if name.endswith(".json") else f"{name}.macro.json"
            target = self.settings.resolve_macro_path(fname)
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                empty_doc = {
                    "schema_version": 1,
                    "name": target.stem,
                    "created_at": "",
                    "profile_id": self.cfg.profile_id,
                    "settings": {"coordinate_mode": self.cfg.coordinate_mode, "speed_multiplier": 1.0},
                    "target_context": {"class": "", "title_pattern": "", "workspace": None, "recorded_window_rect": [0, 0, 0, 0]},
                    "events": [],
                }
                target.write_text(json.dumps(empty_doc, indent=2) + "\n", encoding="utf-8")
            self.refresh_macro_list()
            for i, p in enumerate(self.macro_files):
                if p == target:
                    self.selected_idx = i
                    break
            self.log_event("NEW", f"Selected macro target -> {target.name}")
        except KeyboardInterrupt:
            pass
        finally:
            self._listener_pause.clear()

    def _interactive_delete_selected(self, console: Any) -> None:
        from rich.prompt import Confirm

        if not self.macro_files:
            return
        target = self.macro_files[self.selected_idx]
        if not target.is_file():
            return
        self._listener_pause.set()
        try:
            console.clear()
            if Confirm.ask(f"Delete macro file [bold red]{target}[/]?", default=False):
                target.unlink(missing_ok=True)
                self.refresh_macro_list()
                self.log_event("DEL", f"Deleted {target.name}")
        except KeyboardInterrupt:
            pass
        finally:
            self._listener_pause.clear()

    def _play_selected_from_tui(self) -> None:
        if not self.macro_files:
            self.log_event("WARN", "No macro file selected")
            return
        target = self.macro_files[self.selected_idx]
        if not target.is_file():
            self.log_event("WARN", f"Macro file does not exist yet: {target.name} (Press [R] to record)")
            return

        self._listener_pause.set()
        prev_state = self.engine_state
        self.engine_state = "PLAYING"
        try:
            for rem in range(self.countdown_s, 0, -1):
                self.ipc.notify(f"DuskyMacro: Playing {target.name} in {rem}s...", 900)
                time.sleep(1.0)
            with RuntimeBeacon.acquire_seat_lock():
                macro = json.loads(target.read_text(encoding="utf-8"))
                with WaylandWireInjector(self.xkb) as injector:
                    player = MacroPlayer(
                        ipc=self.ipc,
                        topology=self.topology,
                        injector=injector,
                        cfg=self.cfg,
                    )
                    res = player.play(
                        macro,
                        override_mode=self.override_mode,
                        override_speed=self.override_speed,
                        override_repeat=self.override_repeat,
                        override_click_current=self.override_click_current,
                        override_bg_keys=self.override_bg_keys,
                    )
                    self.log_event("PLAY", f"Played {target.name} ({res['executed_events']} events)")
        except SafetyAbortError as exc:
            self.log_event("ABORT", str(exc))
        except Exception as exc:
            self.log_event("ERR", f"Playback failed: {exc}")
        finally:
            self.engine_state = prev_state
            self._listener_pause.clear()

    def run(self) -> int:
        from rich.console import Console
        from rich.live import Live

        console = Console()
        self.start_background_listener()

        stop_ui = False

        def _sig_handler(_signum: int, _frame: object) -> None:
            nonlocal stop_ui
            stop_ui = True

        signal.signal(signal.SIGINT, _sig_handler)
        signal.signal(signal.SIGTERM, _sig_handler)

        try:
            while not stop_ui:
                action_to_run: str | None = None
                with Live(
                    self.render_dashboard(),
                    console=console,
                    screen=True,
                    refresh_per_second=10,
                ) as live:
                    with _CbreakTerminal() as term:
                        while not stop_ui and action_to_run is None:
                            live.update(self.render_dashboard())
                            key = term.read_key(timeout_s=0.10)
                            if key is None:
                                continue
                            low = key.lower()
                            if low == "q":
                                stop_ui = True
                                break
                            if key in ("UP", "k", "K"):
                                if self.macro_files:
                                    self.selected_idx = (self.selected_idx - 1) % len(self.macro_files)
                            elif key in ("DOWN", "j", "J"):
                                if self.macro_files:
                                    self.selected_idx = (self.selected_idx + 1) % len(self.macro_files)
                            elif low == "l":
                                if self.listener_enabled:
                                    self.stop_background_listener()
                                    self.log_event("INFO", "Background hotkey listener stopped")
                                else:
                                    self.start_background_listener()
                                    self.log_event("INFO", "Background hotkey listener started")
                            elif low == "s":
                                self.cycle_profile(1)
                            elif low == "x":
                                self._last_dev_scan = 0.0
                                self._update_device_counts()
                                m_cnt, k_cnt = self._devices_summary
                                self.log_event(
                                    "DIAG",
                                    f"Doctor OK: IPC={self.ipc.config_provider()}, Mice={m_cnt}, Keyboards={k_cnt}",
                                )
                            elif low in ("p", "\r", "\n"):
                                action_to_run = "play"
                            elif low == "r":
                                action_to_run = "record"
                            elif low == "o":
                                action_to_run = "override"
                            elif low == "n":
                                action_to_run = "new_slot"
                            elif low == "d":
                                action_to_run = "delete"

                if action_to_run == "play":
                    self._play_selected_from_tui()
                elif action_to_run == "record":
                    self._interactive_record_wizard(console)
                elif action_to_run == "override":
                    self._interactive_override_dials(console)
                elif action_to_run == "new_slot":
                    self._interactive_new_slot(console)
                elif action_to_run == "delete":
                    self._interactive_delete_selected(console)
        finally:
            self.stop_background_listener()
            self.topology.stop_event_listener()
            self.xkb.close()
        return 0


# =============================================================================
# SECTION 9: CLI VERBS (doctor, stop, record, play, listen, profiles, inspect, tui)
# =============================================================================

def prewarm_realtime_path(ipc: HyprlandSocketClient) -> XkbBridge:
    return XkbBridge(*ipc.active_keyboard_layout())


def cmd_stop() -> int:
    """Emergency stop CLI verb: signals any active dusky_macro
    playback, recording, or daemon process via SIGTERM so it runs release_all() and exits."""
    signaled_pids: list[int] = []
    my_pid = os.getpid()

    st = RuntimeBeacon.read_state()
    if st and isinstance(st.get("pid"), int) and st["pid"] != my_pid:
        pid = int(st["pid"])
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGTERM)
            signaled_pids.append(pid)

    lock_file = RuntimeBeacon.lock_path()
    if lock_file.is_file():
        with contextlib.suppress(ValueError, OSError):
            lock_pid = int(lock_file.read_text(encoding="utf-8").strip())
            if lock_pid != my_pid and lock_pid not in signaled_pids:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.kill(lock_pid, signal.SIGTERM)
                    signaled_pids.append(lock_pid)

    RuntimeBeacon.clear_state()
    print(json.dumps({"status": "stopped", "signaled_pids": signaled_pids}))
    return 0


def cmd_tui(settings: SettingsManager, profiles_mgr: ProfileManager) -> int:
    try:
        import rich  # noqa: F401
    except ImportError:
        print(
            "dusky_macro: note: 'rich' library not found for interactive TUI "
            "(install via: sudo pacman -S python-rich). Showing CLI --help instead:\n",
            file=sys.stderr,
        )
        build_parser().print_help()
        return 1
    tui = DuskyMacroTUI(settings, profiles_mgr)
    return tui.run()


def cmd_doctor(settings: SettingsManager, profiles_mgr: ProfileManager, json_out: bool = False) -> int:
    report: dict[str, Any] = {
        "engine": f"dusky_macro v{__version__}",
        "python_version": sys.version.split()[0],
        "kernel": os.uname().release,
        "root_dir": str(settings.root_dir),
        "recordings_dir": str(settings.recordings_dir),
        "settings_file": str(settings.settings_path),
        "profiles_loaded": list(profiles_mgr.profiles.keys()),
        "runtime_beacon": RuntimeBeacon.read_state(),
        "checks": {},
        "ok": True,
    }

    try:
        ipc = HyprlandSocketClient()
        prov = ipc.config_provider()
        pos = ipc.cursor_pos_fast()
        layout, variant, _ = ipc.active_keyboard_layout()
        grp_idx, grp_cnt = ipc.layout_group()
        report["checks"]["hyprland_ipc"] = {
            "ok": True,
            "socket": str(ipc.socket_path),
            "config_provider": prov,
            "cursor": list(pos),
            "layout": f"{layout}({variant})" if variant else layout,
            "layout_group": f"{grp_idx}/{grp_cnt}",
            "left_handed": ipc.is_left_handed(),
        }
    except Exception as exc:
        report["checks"]["hyprland_ipc"] = {"ok": False, "error": str(exc)}
        report["ok"] = False
        ipc = None

    try:
        xkb = XkbBridge(*(ipc.active_keyboard_layout() if ipc else ("us", "", "")))
        km_len = len(xkb.keymap_bytes)
        base_keys_mapped = len(xkb._evdev_to_base_sym)
        xkb.close()
        report["checks"]["libxkbcommon"] = {
            "ok": True,
            "compiled_keymap_bytes": km_len,
            "sendshortcut_base_keys": base_keys_mapped,
        }
    except Exception as exc:
        report["checks"]["libxkbcommon"] = {"ok": False, "error": str(exc)}
        report["ok"] = False

    try:
        xkb_temp = XkbBridge(*(ipc.active_keyboard_layout() if ipc else ("us", "", "")))
        with WaylandWireInjector(xkb_temp) as inj:
            report["checks"]["wayland_protocols"] = {
                "ok": True,
                "virtual_pointer_v": inj._ptr_version,
                "virtual_keyboard_v": 1,
                "memfd_supported": hasattr(os, "memfd_create"),
            }
        xkb_temp.close()
    except Exception as exc:
        report["checks"]["wayland_protocols"] = {"ok": False, "error": str(exc)}
        report["ok"] = False

    devices = EvdevDeviceScanner.scan(need_mouse=True, need_keyboard=True)
    mice = [d.name for d in devices if d.is_mouse]
    keyboards = [d.name for d in devices if d.is_keyboard]
    grab_conflicts = EvdevDeviceScanner.probe_grab_conflicts(devices)
    evdev_ok = bool(mice and keyboards)
    report["checks"]["evdev_capture"] = {
        "ok": evdev_ok,
        "mice": mice,
        "keyboards": keyboards,
        "grab_conflicts": [f"{g} (libinput/compositor held)" for g in grab_conflicts] if grab_conflicts else [],
        "hint": "" if evdev_ok else "Add your user to the 'input' group: sudo usermod -aG input $USER && re-login",
    }
    if not evdev_ok:
        report["ok"] = False

    if json_out:
        print(json.dumps(report, indent=2))
    else:
        print(f"DuskyMacro v{__version__} Diagnostics (Python {report['python_version']}, Kernel {report['kernel']})")
        print(f"  [INFO] recordings_dir: {settings.recordings_dir}")
        print(f"  [INFO] settings: {settings.settings_path} (profiles: {', '.join(profiles_mgr.profiles.keys()) or 'none'})")
        for name, detail in report["checks"].items():
            badge = "[OK]" if detail.get("ok") else "[FAIL]"
            print(f"  {badge:6} {name}: {json.dumps({k: v for k, v in detail.items() if k != 'ok'})}")
    return 0 if report["ok"] else 1


def cmd_profiles(settings: SettingsManager, profiles_mgr: ProfileManager, args: argparse.Namespace) -> int:
    active_win: dict[str, Any] = {}
    with contextlib.suppress(Exception):
        ipc = HyprlandSocketClient()
        topo = DesktopTopology(ipc)
        active_win = topo.active_window

    matched = profiles_mgr.resolve_for_window(
        active_win.get("class", ""), active_win.get("title", "")
    )
    items = []
    for pid in profiles_mgr.ordered_ids:
        p = profiles_mgr.profiles[pid]
        items.append({
            "id": p.id,
            "name": p.name,
            "enabled": p.enabled,
            "priority": p.priority,
            "file": str(p.source_path),
            "window_class_regex": p.window_class_regex,
            "window_title_regex": p.window_title_regex,
            "active_for_current_window": bool(matched and matched.id == p.id),
            "bindings": [
                {
                    "name": b.name,
                    "hotkey": b.hotkey,
                    "macro_file": str(b.macro_file),
                    "coordinate_mode": b.coordinate_mode,
                }
                for b in p.bindings
            ],
        })
    if args.json:
        print(json.dumps({"active_window": active_win, "profiles": items}, indent=2))
    else:
        for item in items:
            mark = "*" if item["active_for_current_window"] else " "
            print(
                f"{mark} {item['id']} ({item['name']}) [priority={item['priority']}, "
                f"class={item['window_class_regex'] or '*'}, bindings={len(item['bindings'])}]"
            )
    return 0


def cmd_record(settings: SettingsManager, profiles_mgr: ProfileManager, args: argparse.Namespace) -> int:
    ipc = HyprlandSocketClient()
    topology = DesktopTopology(ipc)
    prof = (
        profiles_mgr.get(args.profile)
        if args.profile
        else profiles_mgr.resolve_for_window(
            topology.active_window.get("class", ""), topology.active_window.get("title", "")
        )
    )
    cfg = settings.build_effective(prof)
    xkb = prewarm_realtime_path(ipc)

    stop_hk = args.hotkey or cfg.record_hotkey
    coord_mode = args.mode or cfg.coordinate_mode
    cap_mouse = False if args.no_mouse else cfg.capture_mouse
    cap_kb = False if args.no_keyboard else cfg.capture_keyboard
    out_path = settings.resolve_macro_path(args.output) if args.output else cfg.default_macro_file
    out_path.parent.mkdir(parents=True, exist_ok=True)

    abort_ev = threading.Event()

    def _sig_handler(_signum: int, _frame: object) -> None:
        abort_ev.set()

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    try:
        with RuntimeBeacon.acquire_seat_lock():
            recorder = MacroRecorder(
                ipc=ipc,
                topology=topology,
                xkb=xkb,
                cfg=cfg,
                stop_hotkey=stop_hk,
                coordinate_mode=coord_mode,
                capture_mouse=cap_mouse,
                capture_keyboard=cap_kb,
            )
            held: set[int] = set()
            if not args.immediate:
                start_matcher = HotkeyMatcher(stop_hk)
                print(
                    f"[Profile: {cfg.profile_id}] Waiting for start hotkey [{stop_hk.upper()}]... (Ctrl+C to cancel)",
                    file=sys.stderr,
                )
                with EvdevInputSession(
                    capture_mouse=start_matcher.needs_mouse_buttons,
                    capture_keyboard=True,
                    use_kernel_monotonic_ts=False,
                    rescan_interval_s=cfg.hotplug_rescan_interval_s,
                    ignore_virtual=cfg.ignore_virtual_devices,
                    extra_ignored=cfg.extra_ignored_keywords,
                ) as session:
                    started = False
                    while not started and not abort_ev.is_set():
                        for frame in session.poll_frames(timeout_s=0.015):
                            for code, pressed in frame.keys + frame.buttons:
                                held = session.update_held(frame.source_fd, code, pressed)
                                if start_matcher.matches_press(code, pressed, held):
                                    started = True
                                    break
                            if started:
                                break
                if abort_ev.is_set():
                    return 130

            if cfg.notify_on_state_change:
                ipc.notify(f"DuskyMacro [{cfg.profile_id}]: Recording Started", cfg.notification_timeout_ms)
            print(f"Recording started! Press [{stop_hk.upper()}] to stop...", file=sys.stderr)
            doc = recorder.record(
                name=out_path.stem,
                max_duration_s=args.duration,
                initial_suppressed_keys=held,
                abort_event=abort_ev,
            )
            out_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
            if cfg.notify_on_state_change:
                ipc.notify(
                    f"DuskyMacro: Saved {len(doc['events'])} events to {out_path.name}",
                    cfg.notification_timeout_ms,
                )
            print(f"Saved {len(doc['events'])} events to {out_path}", file=sys.stderr)
            return 0
    finally:
        topology.stop_event_listener()
        xkb.close()


def cmd_play(settings: SettingsManager, profiles_mgr: ProfileManager, args: argparse.Namespace) -> int:
    ipc = HyprlandSocketClient()
    topology = DesktopTopology(ipc)
    prof = (
        profiles_mgr.get(args.profile)
        if args.profile
        else profiles_mgr.resolve_for_window(
            topology.active_window.get("class", ""), topology.active_window.get("title", "")
        )
    )
    cfg = settings.build_effective(prof)
    macro_path = settings.resolve_macro_path(args.macro_file) if args.macro_file else cfg.default_macro_file
    if not macro_path.is_file():
        print(f"dusky_macro: error: macro file not found: {macro_path}", file=sys.stderr)
        return 1
    macro = json.loads(macro_path.read_text(encoding="utf-8"))

    xkb = prewarm_realtime_path(ipc)
    injector = WaylandWireInjector(xkb)
    player = MacroPlayer(
        ipc=ipc,
        topology=topology,
        injector=injector,
        cfg=cfg,
        abort_hotkey=args.abort_hotkey,
        pause_hotkey=args.pause_hotkey,
        allow_auth=args.allow_auth,
    )

    signal_num: list[int] = []

    def _decoupled_signal_handler(signum: int, _frame: object) -> None:
        signal_num.append(signum)
        player.abort_event.set()

    signal.signal(signal.SIGINT, _decoupled_signal_handler)
    signal.signal(signal.SIGTERM, _decoupled_signal_handler)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, _decoupled_signal_handler)
    atexit.register(injector.release_all)

    try:
        with RuntimeBeacon.acquire_seat_lock():
            result = player.play(
                macro=macro,
                override_mode=args.mode,
                override_speed=args.speed,
                override_repeat=args.repeat,
                override_click_current=args.current_cursor,
                override_bg_keys=args.background_keys,
                dry_run=args.dry_run,
            )
            print(json.dumps(result))
            return 0
    except SafetyAbortError as exc:
        if cfg.notify_on_state_change:
            ipc.notify(f"DuskyMacro Aborted: {exc}", cfg.notification_timeout_ms)
        print(f"dusky_macro: aborted: {exc}", file=sys.stderr)
        return 128 + signal_num[0] if signal_num else 3
    finally:
        topology.stop_event_listener()
        injector.close()
        xkb.close()


def cmd_listen(settings: SettingsManager, profiles_mgr: ProfileManager, args: argparse.Namespace) -> int:
    ipc = HyprlandSocketClient()
    state_lock = threading.RLock()
    profile_changed_ev = threading.Event()
    stop_daemon_ev = threading.Event()

    auto_switch = bool(settings.raw.get("profiles", {}).get("auto_switch_by_window", True)) and not args.profile
    locked_profile_id: str | None = args.profile

    def _on_window_change(win: dict[str, Any]) -> None:
        nonlocal current_prof, cfg
        with state_lock:
            if not auto_switch or locked_profile_id is not None:
                return
            new_prof = profiles_mgr.resolve_for_window(win.get("class", ""), win.get("title", ""))
            if new_prof and (current_prof is None or new_prof.id != current_prof.id):
                current_prof = new_prof
                cfg = settings.build_effective(current_prof)
                profile_changed_ev.set()
                if cfg.notify_on_state_change:
                    ipc.notify(
                        f"DuskyMacro Profile: {current_prof.name} ({current_prof.id})",
                        cfg.notification_timeout_ms,
                    )

    topology = DesktopTopology(ipc, on_active_window_change=_on_window_change)
    topology.start_event_listener()

    current_prof = (
        profiles_mgr.get(locked_profile_id)
        if locked_profile_id
        else profiles_mgr.resolve_for_window(
            topology.active_window.get("class", ""), topology.active_window.get("title", "")
        )
    )
    cfg = settings.build_effective(current_prof)
    xkb = prewarm_realtime_path(ipc)

    def _sig_handler(_signum: int, _frame: object) -> None:
        stop_daemon_ev.set()
        profile_changed_ev.set()

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    try:
        while not stop_daemon_ev.is_set():
            if profiles_mgr.reload_if_modified():
                with state_lock:
                    current_prof = profiles_mgr.get(locked_profile_id)
                    cfg = settings.build_effective(current_prof)

            with state_lock:
                active_cfg = cfg
                rec_matcher = HotkeyMatcher(args.record_hotkey or active_cfg.record_hotkey)
                next_matcher = HotkeyMatcher(active_cfg.cycle_next_hotkey)
                prev_matcher = HotkeyMatcher(active_cfg.cycle_prev_hotkey)
                slot_matchers: list[tuple[HotkeyMatcher, MacroBinding]] = []
                if args.macro_file:
                    slot_matchers.append((
                        HotkeyMatcher(args.play_hotkey or active_cfg.play_hotkey),
                        MacroBinding(
                            name="CLI Macro",
                            hotkey=args.play_hotkey or active_cfg.play_hotkey,
                            macro_file=settings.resolve_macro_path(args.macro_file),
                        ),
                    ))
                else:
                    for b in active_cfg.bindings:
                        slot_matchers.append((HotkeyMatcher(b.hotkey), b))

            profile_changed_ev.clear()
            RuntimeBeacon.write_state("LISTENING", active_cfg.profile_id)
            mode_label = f"Locked ({locked_profile_id})" if locked_profile_id else "Auto-Window"
            print(
                f"DuskyMacro Active — Profile: {active_cfg.profile_name} [{active_cfg.profile_id}] ({mode_label})\n"
                f"  • Record Start/Stop : [{rec_matcher.spec.upper()}]\n"
                f"  • Cycle Profile     : [{prev_matcher.spec.upper()}] / [{next_matcher.spec.upper()}]\n"
                f"  • Emergency Abort   : [{(args.abort_hotkey or active_cfg.abort_hotkey).upper()}]\n"
                f"  • Active Bindings   : "
                + ", ".join(f"[{m.spec.upper()}] -> {b.macro_file.name}" for m, b in slot_matchers),
                file=sys.stderr,
            )

            need_mouse = (
                rec_matcher.needs_mouse_buttons
                or next_matcher.needs_mouse_buttons
                or prev_matcher.needs_mouse_buttons
                or any(m.needs_mouse_buttons for m, _ in slot_matchers)
            )

            held_snapshot: set[int] = set()
            triggered_action: str | None = None
            triggered_binding: MacroBinding | None = None

            with EvdevInputSession(
                capture_mouse=need_mouse,
                capture_keyboard=True,
                use_kernel_monotonic_ts=False,
                rescan_interval_s=active_cfg.hotplug_rescan_interval_s,
                ignore_virtual=active_cfg.ignore_virtual_devices,
                extra_ignored=active_cfg.extra_ignored_keywords,
            ) as session:
                last_scan = time.monotonic()
                while (
                    triggered_action is None
                    and not profile_changed_ev.is_set()
                    and not stop_daemon_ev.is_set()
                ):
                    last_scan = session.rescan_if_due(last_scan)
                    for frame in session.poll_frames(timeout_s=active_cfg.epoll_poll_timeout_s):
                        for code, pressed in frame.keys + frame.buttons:
                            held = session.update_held(frame.source_fd, code, pressed)
                            if rec_matcher.matches_press(code, pressed, held):
                                triggered_action = "record"
                                held_snapshot = set(held)
                                break
                            if next_matcher.matches_press(code, pressed, held):
                                triggered_action = "cycle_next"
                                break
                            if prev_matcher.matches_press(code, pressed, held):
                                triggered_action = "cycle_prev"
                                break
                            for s_matcher, binding in slot_matchers:
                                if s_matcher.matches_press(code, pressed, held):
                                    triggered_action = "play"
                                    triggered_binding = binding
                                    break
                            if triggered_action:
                                break
                        if triggered_action:
                            break

            if stop_daemon_ev.is_set():
                break
            if profile_changed_ev.is_set() and triggered_action is None:
                continue

            if triggered_action in ("cycle_next", "cycle_prev"):
                enabled_list = profiles_mgr.enabled_profiles_sorted_by_id()
                if enabled_list:
                    with state_lock:
                        cur_idx = next(
                            (i for i, p in enumerate(enabled_list) if current_prof and p.id == current_prof.id),
                            0,
                        )
                        delta = 1 if triggered_action == "cycle_next" else -1
                        new_prof = enabled_list[(cur_idx + delta) % len(enabled_list)]
                        locked_profile_id = new_prof.id
                        current_prof = new_prof
                        cfg = settings.build_effective(current_prof)
                        if cfg.notify_on_state_change:
                            ipc.notify(
                                f"DuskyMacro Profile Locked: {current_prof.name} ({current_prof.id})",
                                cfg.notification_timeout_ms,
                            )
                continue

            if triggered_action == "record":
                target_file = (
                    settings.resolve_macro_path(args.macro_file)
                    if args.macro_file
                    else (slot_matchers[0][1].macro_file if slot_matchers else active_cfg.default_macro_file)
                )
                target_file.parent.mkdir(parents=True, exist_ok=True)
                if active_cfg.notify_on_state_change:
                    ipc.notify(
                        f"DuskyMacro [{active_cfg.profile_id}]: Recording -> {target_file.name}",
                        active_cfg.notification_timeout_ms,
                    )
                with RuntimeBeacon.acquire_seat_lock():
                    recorder = MacroRecorder(
                        ipc=ipc,
                        topology=topology,
                        xkb=xkb,
                        cfg=active_cfg,
                        stop_hotkey=rec_matcher.spec,
                    )
                    doc = recorder.record(
                        name=target_file.stem,
                        initial_suppressed_keys=held_snapshot,
                        abort_event=stop_daemon_ev,
                    )
                    target_file.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
                if active_cfg.notify_on_state_change:
                    ipc.notify(
                        f"DuskyMacro: Saved {len(doc['events'])} events ({target_file.name})",
                        active_cfg.notification_timeout_ms,
                    )

            elif triggered_action == "play" and triggered_binding is not None:
                m_file = triggered_binding.macro_file
                if not m_file.is_file():
                    if active_cfg.notify_on_state_change:
                        ipc.notify(
                            f"DuskyMacro: Missing {m_file.name}", active_cfg.notification_timeout_ms
                        )
                    continue
                macro = json.loads(m_file.read_text(encoding="utf-8"))
                with RuntimeBeacon.acquire_seat_lock():
                    with WaylandWireInjector(xkb) as injector:
                        player = MacroPlayer(
                            ipc=ipc,
                            topology=topology,
                            injector=injector,
                            cfg=active_cfg,
                            abort_hotkey=args.abort_hotkey or active_cfg.abort_hotkey,
                        )
                        try:
                            player.play(
                                macro,
                                override_mode=triggered_binding.coordinate_mode,
                                override_speed=triggered_binding.speed_multiplier,
                                override_repeat=triggered_binding.repeat_count,
                                override_click_current=triggered_binding.click_at_current_cursor,
                                override_bg_keys=triggered_binding.background_window_keys,
                            )
                        except SafetyAbortError as exc:
                            if active_cfg.notify_on_state_change:
                                ipc.notify(f"DuskyMacro Aborted: {exc}", active_cfg.notification_timeout_ms)
    finally:
        RuntimeBeacon.clear_state()
        topology.stop_event_listener()
        xkb.close()
    return 0


def cmd_inspect(settings: SettingsManager, args: argparse.Namespace) -> int:
    macro_path = settings.resolve_macro_path(args.macro_file)
    doc = json.loads(macro_path.read_text(encoding="utf-8"))
    events = doc.get("events", [])
    duration_ms = (events[-1]["t_us"] / 1000.0) if events else 0.0
    counts: dict[str, int] = {}
    for ev in events:
        t = ev.get("type", "unknown")
        counts[t] = counts.get(t, 0) + 1
    summary = {
        "name": doc.get("name"),
        "file": str(macro_path),
        "profile_id": doc.get("profile_id"),
        "created_at": doc.get("created_at"),
        "coordinate_mode": doc.get("settings", {}).get("coordinate_mode"),
        "target_context": doc.get("target_context"),
        "total_events": len(events),
        "duration_ms": round(duration_ms, 2),
        "event_breakdown": counts,
    }
    print(json.dumps(summary, indent=2))
    return 0


def build_parser() -> Any:
    import argparse

    p = argparse.ArgumentParser(
        prog="dusky_macro",
        description="Standalone Pure-Wayland Macro Recorder, Player, Profile Engine & Rich TUI for Hyprland",
    )
    p.add_argument(
        "--settings",
        default=None,
        help="Optional path to settings.toml (default: ~/user_scripts/gaming/macro/settings/settings.toml)",
    )
    p.add_argument(
        "--tui",
        action="store_true",
        help="Launch the interactive Rich Terminal UI (default when invoked with no subcommand)",
    )
    sub = p.add_subparsers(dest="cmd", required=False)

    sub.add_parser("tui", help="Launch the interactive Rich Terminal UI")
    sub.add_parser("stop", help="Emergency stop of any running dusky_macro playback or recording")

    doc_p = sub.add_parser("doctor", help="Verify Hyprland IPC, Wayland protocols, libxkbcommon, evdev, and TOML configs")
    doc_p.add_argument("--json", action="store_true", help="Output diagnostics as JSON")

    prof_p = sub.add_parser("profiles", help="List loaded TOML profiles and show which matches the active window")
    prof_p.add_argument("--json", action="store_true", help="Output profile list as JSON")

    rec_p = sub.add_parser("record", help="Record mouse and keyboard events to a macro JSON file")
    rec_p.add_argument("output", nargs="?", default=None, help="Output .macro.json path (default inside ~/.config/dusky/settings/dusky_macros)")
    rec_p.add_argument("--profile", default=None, help="Profile ID or filename stem to use")
    rec_p.add_argument("--hotkey", default=None, help="Override start/stop hotkey chord")
    rec_p.add_argument(
        "--mode",
        choices=("absolute", "window_relative", "logical_relative", "raw_relative", "current_position"),
        default=None,
        help="Override default coordinate mode saved in macro settings",
    )
    rec_p.add_argument("--immediate", action="store_true", help="Start recording immediately without waiting for hotkey")
    rec_p.add_argument("--duration", type=float, default=None, help="Optional max duration in seconds")
    rec_p.add_argument("--no-mouse", action="store_true", help="Do not record mouse events")
    rec_p.add_argument("--no-keyboard", action="store_true", help="Do not record keyboard events")

    play_p = sub.add_parser("play", help="Replay a recorded macro JSON file")
    play_p.add_argument("macro_file", nargs="?", default=None, help="Path or filename in ~/.config/dusky/settings/dusky_macros")
    play_p.add_argument("--profile", default=None, help="Profile ID or filename stem to use")
    play_p.add_argument(
        "--mode",
        choices=("absolute", "window_relative", "logical_relative", "raw_relative", "current_position"),
        default=None,
        help="Override coordinate mode during playback",
    )
    play_p.add_argument("--speed", type=float, default=None, help="Playback speed multiplier (e.g. 2.0)")
    play_p.add_argument("--repeat", type=int, default=None, help="Number of times to repeat macro")
    play_p.add_argument("--current-cursor", action="store_true", default=None, help="Click at current cursor position without moving mouse")
    play_p.add_argument("--background-keys", action="store_true", default=None, help="Send keys to unfocused target window via Hyprland sendshortcut")
    play_p.add_argument("--abort-hotkey", default=None, help="Override emergency abort hotkey")
    play_p.add_argument("--pause-hotkey", default=None, help="Override pause/resume hotkey")
    play_p.add_argument("--allow-auth", action="store_true", help="Allow playback over polkit/launcher layers")
    play_p.add_argument("--dry-run", action="store_true", help="Validate target and guards without injecting input")

    listen_p = sub.add_parser("listen", help="Run profile-aware hotkey daemon in CLI mode")
    listen_p.add_argument("macro_file", nargs="?", default=None, help="Optional single macro file override")
    listen_p.add_argument("--profile", default=None, help="Lock listener to a specific profile initially")
    listen_p.add_argument("--record-hotkey", default=None, help="Override record toggle hotkey")
    listen_p.add_argument("--play-hotkey", default=None, help="Override play hotkey when macro_file is given")
    listen_p.add_argument("--abort-hotkey", default=None, help="Override emergency abort hotkey")

    insp_p = sub.add_parser("inspect", help="Inspect metadata and event breakdown of a macro file")
    insp_p.add_argument("macro_file", help="Path or filename in ~/.config/dusky/settings/dusky_macros")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == "stop":
            return cmd_stop()

        settings_path = Path(args.settings) if args.settings else None
        settings = SettingsManager(root_dir=SCRIPT_ROOT, settings_path=settings_path)
        settings.ensure_directories_and_defaults()
        profiles_mgr = ProfileManager(settings)

        if args.tui or args.cmd in (None, "tui"):
            return cmd_tui(settings, profiles_mgr)

        match args.cmd:
            case "doctor":
                return cmd_doctor(settings, profiles_mgr, json_out=args.json)
            case "profiles":
                return cmd_profiles(settings, profiles_mgr, args)
            case "record":
                return cmd_record(settings, profiles_mgr, args)
            case "play":
                return cmd_play(settings, profiles_mgr, args)
            case "listen":
                return cmd_listen(settings, profiles_mgr, args)
            case "inspect":
                return cmd_inspect(settings, args)
    except DuskyMacroError as exc:
        print(f"dusky_macro: error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
