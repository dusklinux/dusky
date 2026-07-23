#!/usr/bin/env python3
import os
import re
import json
import subprocess
import colorsys
import shlex
import shutil
import asyncio
import math
import copy
import sys
import threading
from pathlib import Path
from typing import Any, override
from collections import deque, defaultdict
from functools import lru_cache

from textual import on, events, work
from textual.message import Message
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal
from textual.widgets import Label, Input, Tabs, Tab, ContentSwitcher, OptionList, Markdown, Static
from textual.widgets.option_list import Option, OptionDoesNotExist
from textual.screen import ModalScreen
from textual.reactive import reactive
from textual.theme import Theme
from textual.timer import Timer
from textual.widget import Widget

from rich.text import Text
from rich.cells import cell_len

from python.frontend.core_types import (
    ConfigItem,
    BaseEngine,
    KNOWN_COLORS,
    KNOWN_COLORS_LOWER,
    is_theme_variable,
    is_trigger_item,
    clone_value,
)


# =============================================================================
# GLOBAL CACHE & REGEX COMPILE
# =============================================================================
_AUDIO_PLAYER_CACHE: str | None = None

_RE_RGB = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)")
_RE_HSL = re.compile(r"hsla?\(\s*([\d.]+)\s*,\s*([\d.]+)%?\s*,\s*([\d.]+)%?")
_RE_OKLCH = re.compile(r"oklch\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)")
_RE_RGBA_ALPHA = re.compile(r"rgba\([^,]+,[^,]+,[^,]+,\s*([0-9.]+)\)")
_RE_HSLA_ALPHA = re.compile(r"hsla\([^,]+,[^,]+,[^,]+,\s*([0-9.]+)\)")


def _md_escape(text: str) -> str:
    """
    Minimal Markdown escaping for dynamic strings inserted into dialogs.
    """
    return re.sub(r"([*_`#\[\]])", r"\\\1", str(text))


class EnginesLoaded(Message):
    """UI-thread delivery of a finished background load batch."""
    def __init__(
        self,
        *,
        states: dict[tuple[str, str], Any],
        attempted: set[tuple[str, str]],
        errors: dict[tuple[str, str], str],
    ) -> None:
        super().__init__()
        self.states = states
        self.attempted = attempted
        self.errors = errors


# =============================================================================
# RENDERABLE CACHE & PRESET MATRIX
# =============================================================================
class OptionTextCache:
    __slots__ = ("_maxsize", "_data", "hits", "misses")

    def __init__(self, maxsize: int = 2048) -> None:
        self._maxsize = max(64, maxsize)
        self._data: dict[tuple, Text] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: tuple) -> Text | None:
        txt = self._data.pop(key, None)
        if txt is None:
            self.misses += 1
            return None
        self._data[key] = txt
        self.hits += 1
        return txt.copy()

    def put(self, key: tuple, txt: Text) -> Text:
        if key in self._data:
            del self._data[key]
        elif len(self._data) >= self._maxsize:
            del self._data[next(iter(self._data))]
        self._data[key] = txt.copy()
        return txt

    def invalidate_uid(self, uid: str) -> None:
        kill = [k for k in self._data if k[0] == uid]
        for k in kill:
            del self._data[k]

    def invalidate_presets(self) -> None:
        kill = [k for k in self._data if len(k) > 1 and k[1] == "preset"]
        for k in kill:
            del self._data[k]

    def clear(self) -> None:
        self._data.clear()


class PresetMatchMatrix:
    """
    Structural index built once; current serialized values updated incrementally.
    ratio() is O(1). on_item_changed is O(affected) via inverted dependency index.
    """
    __slots__ = (
        "_app", "_current", "_exists", "_defaults", "_expected",
        "_all_defaults", "_matches", "_totals", "_preset_uids",
        "_configurable_uids", "_uid_set", "_item_to_presets"
    )

    def __init__(self, app: Any) -> None:
        self._app = app
        self._current: dict[str, str] = {}
        self._exists: dict[str, bool] = {}
        self._defaults: dict[str, str] = {}
        self._expected: dict[str, dict[str, str]] = {}
        self._all_defaults: dict[str, bool] = {}
        self._matches: dict[str, int] = {}
        self._totals: dict[str, int] = {}
        self._preset_uids: list[str] = []
        self._configurable_uids: list[str] = []
        self._uid_set: set[str] = set()
        self._item_to_presets: defaultdict[str, set[str]] = defaultdict(set)

    def rebuild(self, configurable_items: Any) -> None:
        self._current.clear()
        self._exists.clear()
        self._defaults.clear()
        self._expected.clear()
        self._all_defaults.clear()
        self._matches.clear()
        self._totals.clear()
        self._preset_uids.clear()
        self._configurable_uids.clear()
        self._uid_set.clear()
        self._item_to_presets.clear()

        items: list[Any] = []
        presets: list[Any] = []
        for _t, _i, item in configurable_items:
            match item.type_:
                case "preset":
                    presets.append(item)
                case "action" | "menu":
                    continue
                case _:
                    items.append(item)

        for item in items:
            uid = item.uid
            self._configurable_uids.append(uid)
            self._uid_set.add(uid)
            self._current[uid] = item.serialize(item.value)
            self._defaults[uid] = item.serialize(item.default)
            self._exists[uid] = bool(item.exists_in_target)

        for p in presets:
            puid = p.uid
            self._preset_uids.append(puid)
            payload = p.preset_payload or {}
            all_def = bool(payload.get("__ALL_DEFAULTS__", False))
            self._all_defaults[puid] = all_def
            exp: dict[str, str] = {}
            for key_path, raw in payload.items():
                if key_path == "__ALL_DEFAULTS__":
                    continue
                exp[key_path] = self._serialize_payload(key_path, raw)
                self._item_to_presets[key_path].add(puid)
            self._expected[puid] = exp
            if all_def:
                for uid in self._configurable_uids:
                    self._item_to_presets[uid].add(puid)
            self._recompute_preset(puid)

    def ingest_items(self, items: Any) -> None:
        touched = False
        for it in items:
            if it.type_ in ("preset", "action", "menu"):
                continue
            uid = it.uid
            self._current[uid] = it.serialize(it.value)
            self._defaults[uid] = it.serialize(it.default)
            self._exists[uid] = bool(it.exists_in_target)
            if uid not in self._uid_set:
                self._configurable_uids.append(uid)
                self._uid_set.add(uid)
            touched = True
        if touched:
            for puid in self._preset_uids:
                self._recompute_preset(puid)

    def on_item_changed(self, item: Any) -> None:
        match item.type_:
            case "preset" | "action" | "menu":
                return

        uid = item.uid
        new_ser = item.serialize(item.value)
        new_exists = bool(item.exists_in_target)
        old_ser = self._current.get(uid)
        old_exists = self._exists.get(uid, False)

        if old_ser == new_ser and old_exists == new_exists:
            return

        if uid not in self._uid_set:
            self._configurable_uids.append(uid)
            self._uid_set.add(uid)
            self._defaults[uid] = item.serialize(item.default)
            self._current[uid] = new_ser
            self._exists[uid] = new_exists
            for puid in self._preset_uids:
                self._recompute_preset(puid)
            return

        self._current[uid] = new_ser
        self._exists[uid] = new_exists

        affected_presets = self._item_to_presets.get(uid) or self._preset_uids
        for puid in affected_presets:
            exp = self._expected_for(puid, uid)
            if old_exists and not new_exists:
                self._totals[puid] = max(0, self._totals.get(puid, 0) - 1)
                if old_ser == exp:
                    self._matches[puid] = max(0, self._matches.get(puid, 0) - 1)
                continue

            if not old_exists and new_exists:
                self._totals[puid] = self._totals.get(puid, 0) + 1
                if new_ser == exp:
                    self._matches[puid] = self._matches.get(puid, 0) + 1
                continue

            old_match = old_ser == exp
            new_match = new_ser == exp
            if old_match is new_match:
                continue
            if old_match and not new_match:
                self._matches[puid] = max(0, self._matches.get(puid, 0) - 1)
            else:
                self._matches[puid] = self._matches.get(puid, 0) + 1

    def ratio(self, preset_item: Any) -> float:
        puid = preset_item.uid
        total = self._totals.get(puid, 0)
        if total <= 0:
            return 0.0
        return self._matches.get(puid, 0) / total

    def _serialize_payload(self, uid: str, raw: Any) -> str:
        item = getattr(self._app, "_item_by_uid", {}).get(uid)
        if item is not None:
            return item.serialize(raw)
        match raw:
            case None:
                return "nil"
            case bool() as b:
                return "true" if b else "false"
            case _:
                return str(raw)

    def _expected_for(self, puid: str, uid: str) -> str:
        if self._all_defaults.get(puid, False):
            return self._defaults.get(uid, "nil")
        exp_map = self._expected.get(puid, {})
        if uid in exp_map:
            return exp_map[uid]
        return self._defaults.get(uid, "nil")

    def _recompute_preset(self, puid: str) -> None:
        matches = 0
        total = 0
        for uid in self._configurable_uids:
            if not self._exists.get(uid, False):
                continue
            total += 1
            if self._current.get(uid) == self._expected_for(puid, uid):
                matches += 1
        self._matches[puid] = matches
        self._totals[puid] = total


# =============================================================================
# COLOR UTILITIES
# =============================================================================
CYCLE_COLORS = [
    "Red", "Lime", "Blue", "Yellow", "Cyan", "Magenta", "White", "Black"
]


def _oklch_to_rgb(L: float, C: float, H: float) -> tuple[int, int, int]:
    h = math.radians(H)
    a, b = C * math.cos(h), C * math.sin(h)

    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b

    l, m, s = l_**3, m_**3, s_**3

    r = +4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    b2 = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s

    def gam(c: float) -> int:
        c = max(0.0, min(1.0, c))
        c = 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055
        return round(c * 255)

    return (gam(r), gam(g), gam(b2))


_RE_HYPR_HEX = re.compile(r"^rgba?\(([0-9a-fA-F]+)\)$")
_RE_VAR_CSS = re.compile(r"^var\(--([^)]+)\)$")
_RE_VAR_MAT = re.compile(r"^\{\{([^}]+)\}\}$")


@lru_cache(maxsize=1024)
def parse_color_format(val: str) -> str:
    val = str(val).strip().lower()

    if val.startswith("0x"):
        return "0xhex"

    if val.startswith("#"):
        return "hex"

    if _RE_HYPR_HEX.match(val):
        return "hypr_hex"

    if val.startswith("rgba"):
        return "rgba"

    if val.startswith("rgb"):
        return "rgb"

    if val.startswith("hsla"):
        return "hsla"

    if val.startswith("hsl"):
        return "hsl"

    if val.startswith("oklch"):
        return "oklch"

    return "hex"


@lru_cache(maxsize=1024)
def color_to_rgb(val: str) -> tuple[int, int, int]:
    val = str(val).strip().lower()

    # 0x hex.
    if val.startswith("0x"):
        v = val[2:]
        if len(v) == 8:
            v = v[2:]
        if len(v) >= 6:
            try:
                return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
            except ValueError:
                pass

    # Standard hex.
    elif val.startswith("#"):
        v = val[1:]
        if len(v) in (3, 4):
            try:
                return (int(v[0] * 2, 16), int(v[1] * 2, 16), int(v[2] * 2, 16))
            except ValueError:
                pass
        if len(v) >= 6:
            try:
                return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
            except ValueError:
                pass

    # Hyprland-style rgb/rgba hex.
    if hypr_m := _RE_HYPR_HEX.match(val):
        v = hypr_m.group(1)
        if len(v) >= 6:
            try:
                return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
            except ValueError:
                pass

    # Functional rgb/rgba.
    if m_rgb := _RE_RGB.match(val):
        return (int(m_rgb.group(1)), int(m_rgb.group(2)), int(m_rgb.group(3)))

    # Functional hsl/hsla.
    if m_hsl := _RE_HSL.match(val):
        h = float(m_hsl.group(1)) / 360.0
        s = float(m_hsl.group(2)) / 100.0
        l_ = float(m_hsl.group(3)) / 100.0
        r, g, b = colorsys.hls_to_rgb(h, l_, s)
        return (int(r * 255), int(g * 255), int(b * 255))

    # OKLCH.
    if m_oklch := _RE_OKLCH.match(val):
        r, g, b = _oklch_to_rgb(float(m_oklch.group(1)), float(m_oklch.group(2)), float(m_oklch.group(3)))
        return (
            max(0, min(255, int(r))),
            max(0, min(255, int(g))),
            max(0, min(255, int(b)))
        )

    return KNOWN_COLORS_LOWER.get(val, (128, 128, 128))


def get_color_name(r: int, g: int, b: int) -> str:
    best_name = "Unknown"
    best_dist = float("inf")

    for name, color in KNOWN_COLORS.items():
        d = (r - color[0]) ** 2 + (g - color[1]) ** 2 + (b - color[2]) ** 2
        if d < best_dist:
            best_dist = d
            best_name = name

    return best_name


def format_rgb(color_name: str, fmt: str, original_val: str) -> str:
    r, g, b = KNOWN_COLORS.get(color_name, (128, 128, 128))

    if fmt == "hypr_hex":
        alpha = "ff"
        hypr_m = re.match(r"rgba?\([0-9a-fA-F]{6}([0-9a-fA-F]{2})?\)", original_val.strip())
        if hypr_m and hypr_m.group(1):
            alpha = hypr_m.group(1)

        is_rgba = original_val.strip().lower().startswith("rgba")
        prefix = "rgba" if is_rgba else "rgb"
        suffix = alpha if is_rgba else ""
        return f"{prefix}({r:02x}{g:02x}{b:02x}{suffix})"

    if fmt == "hex":
        if len(original_val) == 9 and original_val.startswith("#"):
            return f"#{r:02x}{g:02x}{b:02x}{original_val[7:9]}"
        return f"#{r:02x}{g:02x}{b:02x}"

    if fmt == "0xhex":
        alpha = "ff"
        if original_val.startswith("0x") and len(original_val) == 10:
            alpha = original_val[2:4]
        return f"0x{alpha}{r:02x}{g:02x}{b:02x}"

    if fmt == "rgb":
        return f"rgb({r}, {g}, {b})"

    if fmt == "rgba":
        alpha = "1.0"
        m = _RE_RGBA_ALPHA.search(original_val)
        if m:
            alpha = m.group(1)
        return f"rgba({r}, {g}, {b}, {alpha})"

    if fmt in ("hsl", "hsla"):
        h, l, s = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
        h_deg, s_pct, l_pct = int(h * 360), int(s * 100), int(l * 100)

        if fmt == "hsl":
            return f"hsl({h_deg}, {s_pct}%, {l_pct}%)"

        alpha = "1.0"
        m = _RE_HSLA_ALPHA.search(original_val)
        if m:
            alpha = m.group(1)
        return f"hsla({h_deg}, {s_pct}%, {l_pct}%, {alpha})"

    if fmt == "oklch":
        oklch_map = {
            "Red": "oklch(0.628 0.258 29.23)",
            "Lime": "oklch(0.866 0.295 142.5)",
            "Blue": "oklch(0.452 0.313 264.05)",
            "Yellow": "oklch(0.968 0.211 109.77)",
            "Cyan": "oklch(0.905 0.183 195.58)",
            "Magenta": "oklch(0.702 0.322 328.36)",
            "White": "oklch(1.0 0 0)",
            "Black": "oklch(0.0 0 0)",
        }
        return oklch_map.get(color_name, "oklch(0.5 0.2 180)")

    return f"#{r:02x}{g:02x}{b:02x}"


def load_matugen_json(file_path: Path) -> dict[str, str] | None:
    if not file_path.exists():
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            return None
    except (OSError, json.JSONDecodeError):
        return None


def _pad_cells(s: str, width: int) -> str:
    """
    Pad string to specific cell width considering unicode/emoji character width.
    """
    return s + " " * max(0, width - cell_len(s))


# =============================================================================
# NOTICES & DISCLAIMERS
# =============================================================================
class NoticeBox(Vertical):
    def __init__(self, message: str, level: str = "info", **kwargs) -> None:
        super().__init__(**kwargs)
        self.message = message
        self.level = level
        self.add_class(f"-{level}")

    def compose(self) -> ComposeResult:
        yield Markdown(self.message)


# =============================================================================
# MODALS & OVERLAYS
# =============================================================================
class ConfirmDialog(ModalScreen[bool]):
    BINDINGS = [
        Binding("escape", "dismiss_false", "Cancel"),
        Binding("enter,space", "dismiss_true", "Confirm"),
    ]

    def __init__(self, message: str, title: str = "CONFIRM", level: str = "warning") -> None:
        super().__init__()
        self.message = message
        self.title_text = title
        self.level = level

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog", classes=f"-{self.level}"):
            yield Label(self.title_text, id="modal-title")
            yield Markdown(self.message, id="confirm-message")

            with Horizontal(classes="modal-btn-container"):
                yield Label(" Cancel ", classes="modal-cancel-btn", id="btn-cancel")
                yield Label(" Confirm ", classes="modal-close-btn", id="btn-confirm")

    def action_dismiss_false(self) -> None:
        self.dismiss(False)

    def action_dismiss_true(self) -> None:
        self.dismiss(True)

    @on(events.Click, "#btn-cancel")
    def on_cancel_click(self) -> None:
        self.dismiss(False)

    @on(events.Click, "#btn-confirm")
    def on_confirm_click(self) -> None:
        self.dismiss(True)

    @on(events.Click)
    def on_background_click(self, event: events.Click) -> None:
        if event.control is self:
            self.dismiss(False)


class AlertDialog(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "dismiss_modal", "Dismiss"),
        Binding("enter,space", "dismiss_modal", "Dismiss"),
    ]

    def __init__(
        self,
        message: str,
        title: str = "NOTICE",
        level: str = "warning",
        btn_text: str = " OK "
    ) -> None:
        super().__init__()
        self.message = message
        self.title_text = title
        self.level = level
        self.btn_text = btn_text

    def compose(self) -> ComposeResult:
        with Vertical(id="alert-dialog", classes=f"-{self.level}"):
            yield Label(self.title_text, id="modal-title")
            yield Markdown(self.message, id="alert-message")

            with Horizontal(classes="modal-btn-container"):
                yield Label(f" {self.btn_text} ", classes="modal-close-btn")

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    @on(events.Click, ".modal-close-btn")
    def on_close_click(self) -> None:
        self.dismiss(None)

    @on(events.Click)
    def on_background_click(self, event: events.Click) -> None:
        if event.control is self:
            self.dismiss(None)


class PasswordScreen(ModalScreen[str | None]):
    BINDINGS = [
        Binding("escape", "dismiss_modal", "Cancel"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Label("SUDO AUTHENTICATION REQUIRED", id="modal-title", classes="-warning")
            yield Markdown(
                "Enter your sudo password to execute system-level actions. "
                "The session will be kept alive automatically.",
                id="alert-message"
            )
            yield Input(placeholder="Password...", password=True, id="password-input")

            with Horizontal(classes="modal-btn-container"):
                yield Label(" Cancel ", classes="modal-cancel-btn", id="btn-cancel")
                yield Label(" Authenticate ", classes="modal-close-btn", id="btn-authenticate")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    @on(Input.Submitted)
    def handle_submit(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(event.value)

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    @on(events.Click, "#btn-cancel")
    def on_cancel_click(self) -> None:
        self.dismiss(None)

    @on(events.Click, "#btn-authenticate")
    def on_authenticate_click(self) -> None:
        inp = self.query_one(Input)
        if inp.value:
            self.dismiss(inp.value)

    @on(events.Click)
    def on_background_click(self, event: events.Click) -> None:
        if event.control is self:
            self.dismiss(None)


class UnsavedChangesDialog(ModalScreen[str]):
    BINDINGS = [
        Binding("escape", "dismiss_cancel", "Cancel"),
    ]

    def __init__(self, count: int) -> None:
        super().__init__()
        self.count = count

    def compose(self) -> ComposeResult:
        with Vertical(id="unsaved-dialog", classes="-warning"):
            yield Label("UNSAVED CHANGES", id="modal-title")
            yield Markdown(
                f"You have **{self.count}** unsaved batch changes.\n"
                "Do you want to save them before quitting?",
                id="confirm-message"
            )

            with Horizontal(classes="modal-btn-container"):
                yield Label(" Cancel ", classes="modal-cancel-btn", id="btn-cancel")
                yield Label(" Discard ", classes="modal-cancel-btn", id="btn-discard")
                yield Label(" Save ", classes="modal-close-btn", id="btn-save")

    def action_dismiss_cancel(self) -> None:
        self.dismiss("cancel")

    @on(events.Click, "#btn-cancel")
    def on_cancel_click(self) -> None:
        self.dismiss("cancel")

    @on(events.Click, "#btn-discard")
    def on_discard_click(self) -> None:
        self.dismiss("discard")

    @on(events.Click, "#btn-save")
    def on_save_click(self) -> None:
        self.dismiss("save")

    @on(events.Click)
    def on_background_click(self, event: events.Click) -> None:
        if event.control is self:
            self.dismiss("cancel")


class HybridInputScreen(ModalScreen[str | None]):
    BINDINGS = [
        Binding("down,j", "focus_list", "Focus List"),
        Binding("up,k", "focus_input", "Focus Input"),
    ]

    def __init__(self, prompt: str, default: str, options: list[Any] | None = None) -> None:
        super().__init__()
        self.prompt_text = prompt
        self.default_text = default
        self.options = options or []

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Label(self.prompt_text, id="modal-title")
            yield Input(value=self.default_text, id="modal-input")

            if self.options:
                yield Label(" Pre-configured Options:", id="modal-hint")
                yield OptionList(id="hybrid-option-list")
            else:
                yield Label("Press Enter to save • Esc to cancel", id="modal-hint")

            with Horizontal(classes="modal-btn-container"):
                yield Label(" Cancel ", classes="modal-cancel-btn", id="btn-cancel")
                yield Label(" Ok ", classes="modal-close-btn", id="btn-confirm")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

        if self.options:
            ol = self.query_one(OptionList)
            for opt in self.options:
                ol.add_option(Option(str(opt)))

            # Try to highlight the current value if it matches an option.
            for idx, opt in enumerate(self.options):
                if str(opt) == self.default_text:
                    ol.highlighted = idx
                    break

    @on(Input.Submitted)
    def handle_submit(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(event.value)

    @on(OptionList.OptionSelected)
    def handle_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(str(event.option.prompt))

    def action_focus_list(self) -> None:
        if self.options:
            self.query_one(OptionList).focus()

    def action_focus_input(self) -> None:
        self.query_one(Input).focus()

    @on(events.Click, "#btn-cancel")
    def on_cancel_click(self) -> None:
        self.dismiss(None)

    @on(events.Click, "#btn-confirm")
    def on_confirm_click(self) -> None:
        inp = self.query_one(Input)
        if inp.value is not None:
            self.dismiss(inp.value)

    @on(events.Click)
    def on_background_click(self, event: events.Click) -> None:
        if event.control is self:
            self.dismiss(None)


class PickerScreen(ModalScreen[str | None]):
    BINDINGS = [
        Binding("up,k", "cursor_up", "Up"),
        Binding("down,j", "cursor_down", "Down"),
    ]

    def __init__(self, title: str, options: list[str], hints: list[str], current: str | None = None) -> None:
        super().__init__()
        self.picker_title = title
        self.options = options
        self.hints = hints
        self.current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-dialog"):
            yield Label(f"PICKER: {self.picker_title}", id="picker-title")
            yield OptionList(id="picker-list")

            with Horizontal(classes="modal-btn-container"):
                yield Label(" Cancel ", classes="modal-close-btn")

    def on_mount(self) -> None:
        ol = self.query_one(OptionList)
        options_to_add = []

        for i, opt in enumerate(self.options):
            hint = self.hints[i] if i < len(self.hints) else ""

            txt = Text()
            txt.append(f" {opt} ", style="bold")
            if hint:
                txt.append(" - ")
                txt.append(hint, style=f"italic {self.app.theme_colors['muted']}")

            options_to_add.append(Option(txt))

        ol.add_options(options_to_add)
        ol.focus()

        # Start on current value when possible.
        if self.current is not None:
            for idx, opt in enumerate(self.options):
                if str(opt) == str(self.current):
                    ol.highlighted = idx
                    break

    @on(OptionList.OptionSelected)
    def on_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self.options[event.option_index])

    def action_cursor_up(self) -> None:
        self.query_one(OptionList).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one(OptionList).action_cursor_down()

    @on(events.Click, ".modal-close-btn")
    def on_close_click(self) -> None:
        self.dismiss(None)

    @on(events.Click)
    def on_background_click(self, event: events.Click) -> None:
        if event.control is self:
            self.dismiss(None)


class SearchScreen(ModalScreen[tuple[int, int] | None]):
    BINDINGS = [
        Binding("down,j", "cursor_down", "Down"),
        Binding("up,k", "cursor_up", "Up"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="search-dialog"):
            yield Label("FUZZY FIND (Ctrl+F)", id="modal-title")
            yield Input(placeholder="Type to filter configurations...", id="search-input")
            yield OptionList(id="search-list")

            with Horizontal(classes="modal-btn-container"):
                yield Label(" Cancel ", classes="modal-close-btn")

    def on_mount(self) -> None:
        self.query_one(Input).focus()
        self._search_cache = []

        for tab_idx, tab_items in self.app.schema.items():
            tab_name = self.app.tabs[tab_idx] if tab_idx < len(self.app.tabs) else f"Tab {tab_idx}"
            for item_idx, item in enumerate(tab_items):
                haystack = f"{tab_name} {item.label} {item.key} {item.type_}".lower().replace(" ", "")
                self._search_cache.append((tab_idx, item_idx, item, tab_name, haystack))

        self._populate_list("")

    @on(Input.Changed)
    def handle_input(self, event: Input.Changed) -> None:
        self._populate_list(event.value)

    def _populate_list(self, query: str) -> None:
        ol = self.query_one(OptionList)
        ol.clear_options()
        self.results = []

        query_lower = query.lower().strip()
        query_no_space = query_lower.replace(" ", "")
        scored_results = []

        for tab_idx, item_idx, item, tab_name, haystack in self._search_cache:
            if not query_no_space:
                scored_results.append((100, tab_idx, item_idx, item, tab_name))
                continue

            score = 0
            lbl = item.label.lower()

            if query_lower == lbl:
                score += 100
            elif lbl.startswith(query_lower):
                score += 50
            elif query_lower in lbl:
                score += 20

            # Subsequence / fuzzy match.
            q_idx, s_idx = 0, 0
            match_positions = []

            while q_idx < len(query_no_space) and s_idx < len(haystack):
                if query_no_space[q_idx] == haystack[s_idx]:
                    match_positions.append(s_idx)
                    q_idx += 1
                s_idx += 1

            is_match = (q_idx == len(query_no_space))
            if is_match:
                if len(match_positions) > 1:
                    spread = (match_positions[-1] - match_positions[0]) - (len(match_positions) - 1)
                    bonus = max(0, 15 - spread)
                    score += bonus
                else:
                    score += 15
                score += 5

            if score > 0:
                scored_results.append((score, tab_idx, item_idx, item, tab_name))

        scored_results.sort(key=lambda x: (-x[0], x[4], x[3].label))

        options_to_add = []
        for score, tab_idx, item_idx, item, tab_name in scored_results:
            txt = Text()
            txt.append(f"[{tab_name}] ", style=self.app.theme_colors["accent"])
            txt.append(item.label, style="bold")

            if item.hints:
                txt.append(f" - {item.hints[0]}", style=f"italic {self.app.theme_colors['muted']}")

            options_to_add.append(Option(txt, id=f"search_{tab_idx}_{item_idx}"))
            self.results.append((tab_idx, item_idx))

        ol.add_options(options_to_add)

    @on(OptionList.OptionSelected)
    def on_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_index is not None and event.option_index < len(self.results):
            self.dismiss(self.results[event.option_index])

    @on(Input.Submitted)
    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()

        ol = self.query_one(OptionList)
        if ol.highlighted is not None and ol.highlighted < len(self.results):
            self.dismiss(self.results[ol.highlighted])
        elif self.results:
            self.dismiss(self.results[0])

    def action_cursor_down(self) -> None:
        self.query_one(OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one(OptionList).action_cursor_up()

    @on(events.Click, ".modal-close-btn")
    def on_close_click(self) -> None:
        self.dismiss(None)

    @on(events.Click)
    def on_background_click(self, event: events.Click) -> None:
        if event.control is self:
            self.dismiss(None)


class DiffScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape,enter,space", "dismiss_modal", "Dismiss"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="diff-dialog"):
            yield Label("MODIFICATIONS (From Launch)", id="modal-title")
            yield OptionList(id="diff-list")

            with Horizontal(classes="modal-btn-container"):
                yield Label(" Close ", classes="modal-close-btn")

    def on_mount(self) -> None:
        ol = self.query_one(OptionList)
        added_any = False

        for tab_idx, tab_items in self.app.schema.items():
            for item in tab_items:
                val_serialized = item.serialize(item.value)
                init_serialized = item.serialize(item.initial_value)

                if val_serialized != init_serialized:
                    added_any = True

                    txt = Text()
                    txt.append(f"[{self.app.tabs[tab_idx]}] ", style=self.app.theme_colors["accent"])
                    txt.append(f"{item.label}: ", style="bold")
                    txt.append(f"{item.initial_value} ", style=f"strike {self.app.theme_colors['error']}")
                    txt.append("➜ ", style=self.app.theme_colors["muted"])
                    txt.append(f"{item.value}", style=f"bold {self.app.theme_colors['success']}")

                    ol.add_option(Option(txt, disabled=True))

        if not added_any:
            ol.add_option(Option("No changes detected from initial load state.", disabled=True))

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    @on(events.Click, ".modal-close-btn")
    def on_close_click(self) -> None:
        self.dismiss(None)

    @on(events.Click)
    def on_background_click(self, event: events.Click) -> None:
        if event.control is self:
            self.dismiss(None)


class ShortcutsInfoScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape,enter,space", "dismiss_modal", "Dismiss"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="shortcuts-dialog"):
            yield Label("KEYBOARD SHORTCUTS", id="modal-title")
            yield OptionList(id="shortcuts-list")

            with Horizontal(classes="modal-btn-container"):
                yield Label(" Close ", classes="modal-close-btn")

    def on_mount(self) -> None:
        ol = self.query_one(OptionList)

        bindings_info = [
            ("q, ctrl+c", "Quit the application"),
            ("f1", "Show this shortcuts page"),
            ("?", "Toggle item documentation panel"),
            ("ctrl+f", "Fuzzy search all options"),
            ("/", "Inline search in current tab"),
            ("escape", "Clear inline search / Close modals"),
            ("tab", "Switch to Next Tab"),
            ("shift+tab", "Switch to Previous Tab"),
            ("alt+1..7", "Jump directly to tab N"),
            ("d", "Show pending or modified items (Diff)"),
            ("u", "Undo last change (or batch change)"),
            ("ctrl+r", "Redo last undone change"),
            ("ctrl+t", "Toggle between Auto and Batch save modes"),
            ("ctrl+s", "Commit all pending changes (only available in Batch mode)"),
            ("ctrl+p", "Save current state as a user preset"),
            ("D", "Delete highlighted user preset"),
            ("enter, space", "Trigger action / Toggle boolean / Open Picker / Expand Folder"),
            ("e", "Expand / Collapse nested option menus"),
            ("j, down", "Move cursor down"),
            ("k, up", "Move cursor up"),
            ("h, left", "Adjust value down / Cycle previous option"),
            ("l, right", "Adjust value up / Cycle next option"),
            ("g", "Scroll to top of list"),
            ("G", "Scroll to bottom of list"),
            ("ctrl+u, page_up", "Page up"),
            ("ctrl+d, page_down", "Page down"),
            ("r", "Reset highlighted item to default"),
            ("R", "Reset entire page to defaults"),
        ]

        for keys, desc in bindings_info:
            txt = Text()
            txt.append(f"{keys:<20}", style=self.app.theme_colors["accent"] + " bold")
            txt.append(" ➜ ", style=self.app.theme_colors["muted"])
            txt.append(desc, style=self.app.theme_colors["fg"])
            ol.add_option(Option(txt, disabled=True))

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    @on(events.Click, ".modal-close-btn")
    def on_close_click(self) -> None:
        self.dismiss(None)

    @on(events.Click)
    def on_background_click(self, event: events.Click) -> None:
        if event.control is self:
            self.dismiss(None)


# =============================================================================
# INTERACTIVE COMPONENTS
# =============================================================================
class ConfigOptionList(OptionList):
    BINDINGS = [
        Binding("enter,space", "app.submit_current", "Action"),
        Binding("e", "app.toggle_expand", "Expand/Collapse"),
        Binding("j,down", "cursor_down", "Down"),
        Binding("k,up", "cursor_up", "Up"),
        Binding("g", "scroll_top", "Top"),
        Binding("G", "scroll_bottom", "Bottom"),
        Binding("h,left,backspace", "app.adjust(-1)", "Adjust Down"),
        Binding("l,right", "app.adjust(1)", "Adjust Up"),
        Binding("r", "app.reset_item", "Reset"),
        Binding("R", "app.reset_all", "Reset Page"),
        Binding("ctrl+d,page_down", "page_down", "Page Down"),
        Binding("ctrl+u,page_up", "page_up", "Page Up"),
    ]

    last_highlighted_id: str | None = None
    _mouse_down_highlight: int | None = None
    _last_click_x: int = 0
    _last_click_button: int = 1

    def action_scroll_top(self) -> None:
        for i in range(self.option_count):
            if not self.get_option_at_index(i).disabled:
                self.highlighted = i
                break

    def action_scroll_bottom(self) -> None:
        for i in range(self.option_count - 1, -1, -1):
            if not self.get_option_at_index(i).disabled:
                self.highlighted = i
                break

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self._last_click_x = getattr(event, "x", 0)
        self._last_click_button = getattr(event, "button", 1)

        if hasattr(super(), "on_mouse_down"):
            super().on_mouse_down(event)

        self._mouse_down_highlight = self.highlighted

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if hasattr(super(), "on_mouse_move"):
            super().on_mouse_move(event)

        try:
            line_idx = int(self.scroll_y) + int(event.y)
            new_tooltip = None

            if 0 <= line_idx < self.option_count:
                opt = self.get_option_at_index(line_idx)
                parsed = self.app._get_item_from_id(opt.id)

                if parsed:
                    tab_idx, item_idx, item = parsed

                    if (
                        item.type_ == "preset"
                        and item.group == "User Presets"
                        and item.key not in ("__save_new_preset", "__import_new_preset")
                    ):
                        name = item.label.replace("User: ", "", 1)
                        path = self.app.user_presets_dir / f"{name}.json"
                        new_tooltip = (
                            f"Preset Path: {path}\n"
                            "Left/Right Click to open externally"
                        )

            if self.tooltip != new_tooltip:
                self.tooltip = new_tooltip

        except Exception:
            if self.tooltip is not None:
                self.tooltip = None

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        if hasattr(super(), "watch_scroll_y"):
            super().watch_scroll_y(old_value, new_value)

        if hasattr(self.app, "_update_scroll_indicators"):
            self.app._update_scroll_indicators()

    def watch_max_scroll_y(self, old_value: float, new_value: float) -> None:
        if hasattr(super(), "watch_max_scroll_y"):
            super().watch_max_scroll_y(old_value, new_value)

        if hasattr(self.app, "_update_scroll_indicators"):
            self.app._update_scroll_indicators()

    def on_resize(self, event: events.Resize) -> None:
        if hasattr(self.app, "_update_scroll_indicators"):
            self.app._update_scroll_indicators()


class ScrollIndicator(Label):
    _dragging: bool = False
    _max_scroll_y: float = 0
    _track_height: int = 0

    def update_scroll(
        self,
        scroll_y: float,
        max_scroll_y: float,
        viewport_height: float,
        virtual_height: float
    ) -> None:
        if max_scroll_y <= 0 or virtual_height <= 0 or viewport_height <= 2:
            self.display = False
            return

        self.display = True
        self._max_scroll_y = max_scroll_y
        self._track_height = int(viewport_height) - 2

        if self._track_height < 1:
            self.display = False
            return

        thumb_size = max(1, int(self._track_height * (viewport_height / virtual_height)))
        max_pos = self._track_height - thumb_size
        pos = int((scroll_y / max_scroll_y) * max_pos) if max_scroll_y > 0 else 0

        txt = Text()
        txt.append("▲\n", style="bold")

        if pos > 0:
            txt.append("│\n" * pos, style="dim")

        txt.append("┃\n" * thumb_size)

        remainder = self._track_height - pos - thumb_size
        if remainder > 0:
            txt.append("│\n" * remainder, style="dim")

        txt.append("▼", style="bold")

        self.update(txt)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if self._max_scroll_y <= 0:
            return

        try:
            tab_idx = int(self.id.split("-")[1])
        except (AttributeError, IndexError, ValueError):
            return

        ol = self.app.query_one(f"#list-{tab_idx}", ConfigOptionList)

        if event.y == 0:
            ol.scroll_y -= 1
        elif event.y == self.size.height - 1:
            ol.scroll_y += 1
        else:
            self._dragging = True
            self.capture_mouse()
            self._jump_to_y(event.y, ol)

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self._dragging:
            try:
                tab_idx = int(self.id.split("-")[1])
            except (AttributeError, IndexError, ValueError):
                return

            ol = self.app.query_one(f"#list-{tab_idx}", ConfigOptionList)
            self._jump_to_y(event.y, ol)

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._dragging:
            self._dragging = False
            self.release_mouse()

    def _jump_to_y(self, y: float, ol: ConfigOptionList) -> None:
        if self._track_height < 1:
            return

        relative_y = max(0, min(self._track_height - 1, y - 1))
        ratio = relative_y / (self._track_height - 1) if self._track_height > 1 else 0
        ol.scroll_y = int(ratio * self._max_scroll_y)


class Shortcut(Label):
    def __init__(self, key_text: str, label: str, action_name: str | None = None, **kwargs) -> None:
        super().__init__(classes="footer-shortcut", **kwargs)
        self.key_text = key_text
        self.label_text = label
        self.action_name = action_name

    def render(self) -> Text:
        txt = Text()

        if self.has_class("-active"):
            contrast_color = self.app.theme_colors["bg"]
            txt.append(f"[{self.key_text}] ", style=f"bold {contrast_color}")
            txt.append(self.label_text, style=f"bold {contrast_color}")
        else:
            txt.append(f"[{self.key_text}] ", style=self.app.theme_colors["accent"])
            txt.append(self.label_text, style=self.app.theme_colors["fg"])

        return txt

    async def on_click(self) -> None:
        if self.action_name:
            await self.app.run_action(self.action_name)

    def blink(self) -> None:
        self.add_class("-active")
        self.refresh()

        def _unblink():
            self.remove_class("-active")
            self.refresh()

        self.set_timer(0.2, _unblink)


class FileLink(Label):
    path = reactive("")

    def render(self) -> Text:
        txt = Text()
        txt.append(" 󰈔 Edit File ", style=self.app.theme_colors["accent"] + " bold underline")
        return txt

    def watch_path(self, new_val: str) -> None:
        if new_val:
            self.tooltip = f"Edit externally:\n{new_val}"

    def on_click(self, event: events.Click) -> None:
        if not self.path:
            return

        button = getattr(event, "button", 1)
        if button == 0:
            button = 1

        self.app.open_file_externally(self.path, button, touch_first=True)


class ModeButton(Label):
    def on_mount(self) -> None:
        self.update_mode()

    def update_mode(self) -> None:
        txt = Text()
        txt.append(" Mode: ", style=self.app.theme_colors["fg"])

        mode_str = "AUTO" if self.app.auto_save else "BATCH"
        color = self.app.theme_colors["success"] if self.app.auto_save else self.app.theme_colors["warning"]
        txt.append(mode_str, style=color + " bold")

        pending = getattr(self.app, "pending_commits", set())
        if not self.app.auto_save and pending:
            txt.append(f" │ Pending: {len(pending)}", style=self.app.theme_colors["fg"])

        self.update(txt)

    async def on_click(self) -> None:
        await self.app.run_action("toggle_save_mode")


class FlowContainer(Widget):
    def on_mount(self) -> None:
        self.styles.height = "auto"
        self.styles.width = "100%"
        self.call_after_refresh(self.reflow)

    def on_resize(self, event: events.Resize) -> None:
        self.reflow()

    def reflow(self) -> None:
        if not self.is_mounted:
            return

        width = self.size.width
        if width <= 0:
            self.call_after_refresh(self.reflow)
            return

        visible_children = []

        for child in self.children:
            if not child.display:
                continue

            child.styles.position = "absolute"

            cw = child.size.width
            if cw <= 0:
                rendered = child.render()
                plain = rendered.plain if hasattr(rendered, "plain") else str(rendered)
                cw = cell_len(plain) + 2

            ch = child.size.height
            if ch <= 0:
                ch = 1

            visible_children.append((child, cw, ch))

        if not visible_children:
            self.styles.height = 0
            return

        max_item_h = 1
        for _, _, ch in visible_children:
            max_item_h = max(max_item_h, ch)

        x_offset = 0
        y_offset = 0
        gap = 2

        for child, cw, ch in visible_children:
            if x_offset + cw > width and x_offset > 0:
                x_offset = 0
                y_offset += max_item_h

            child.styles.offset = (x_offset, y_offset)
            x_offset += cw + gap

        target_height = y_offset + max_item_h
        if self.styles.height != target_height:
            self.styles.height = target_height


class AppFooter(Vertical):
    status_msg = reactive("")
    status_level = reactive("info")

    def compose(self) -> ComposeResult:
        with FlowContainer(id="footer-shortcuts-container"):
            # --- ACTIVE SHORTCUTS ---
            yield Shortcut("ctrl+s", "Batch Save", "save_batch", id="shortcut-ctrl-s")
            yield Shortcut("/", "Jump", "focus_local_search", id="shortcut-slash")
            yield Shortcut("ctrl+f", "Search", "search", id="shortcut-ctrl-f")
            yield Shortcut("f1", "Shortcuts", "show_shortcuts", id="shortcut-f1")
            yield Shortcut("R", "Reset Page", "reset_all", id="shortcut-R")
            yield Shortcut("q", "Quit", "quit", id="shortcut-q")

            # --- AVAILABLE INACTIVE SHORTCUTS (Uncomment to enable) --- for LLM (DO NOT DELETE THIS COMMENT SECTION)
            # yield Shortcut("r", "Reset Item", "reset_item", id="shortcut-r")
            # yield Shortcut("?", "Doc Help", "toggle_help", id="shortcut-help")
            # yield Shortcut("d", "Show Diff", "show_diff", id="shortcut-d")
            # yield Shortcut("u", "Undo", "undo", id="shortcut-u")
            # yield Shortcut("ctrl+r", "Redo", "redo", id="shortcut-redo")
            # yield Shortcut("ctrl+p", "Save Preset", "save_preset", id="shortcut-ctrl-p")
            # yield Shortcut("D", "Delete Preset", "delete_user_preset", id="shortcut-D")
            # yield Shortcut("ctrl+t", "Toggle Mode", "toggle_save_mode", id="shortcut-ctrl-t")

        with Horizontal(id="footer-bottom-row"):
            yield FileLink(id="file-link")
            yield Label(" │ ", classes="footer-sep")
            yield ModeButton(id="footer-legend", classes="mode-btn")
            yield Label("", id="pos-counter", classes="pos-counter-btn")
            yield Label("", id="status-bar")

    def on_resize(self, event: events.Resize) -> None:
        try:
            self.query_one(FlowContainer).reflow()
        except Exception:
            pass

    def watch_status_msg(self, new_val: str) -> None:
        try:
            for bar in self.query("#status-bar"):
                if new_val:
                    txt = Text()
                    txt.append(" │ Status: ", style=self.app.theme_colors["accent"])
                    color = self.app.theme_colors.get(self.status_level, self.app.theme_colors["fg"])
                    txt.append(new_val, style=color)
                    bar.update(txt)
                    bar.display = True
                else:
                    bar.display = False
        except Exception:
            pass


# =============================================================================
# MAIN APPLICATION
# =============================================================================
class TabContainer(Horizontal):
    """
    A custom container that tells the App to re-evaluate tab overflow when scrolled.
    """

    def watch_scroll_x(self, old_value: float, new_value: float) -> None:
        if hasattr(self.app, "check_tab_overflow"):
            self.app.check_tab_overflow()

    def watch_max_scroll_x(self, old_value: float, new_value: float) -> None:
        if hasattr(self.app, "check_tab_overflow"):
            self.app.check_tab_overflow()


class CustomRichTabWidget(Static):
    """
    A widget slot for rendering custom Python Rich renderables or custom UI components
    within a DuskyTUI tab.
    """

    can_focus = True

    BINDINGS = [
        Binding("j,down", "scroll_down", "Scroll Down", show=False),
        Binding("k,up", "scroll_up", "Scroll Up", show=False),
        Binding("page_down,ctrl+d", "page_down", "Page Down", show=False),
        Binding("page_up,ctrl+u", "page_up", "Page Up", show=False),
        Binding("g", "scroll_home", "Top", show=False),
        Binding("G", "scroll_end", "Bottom", show=False),
    ]

    DEFAULT_CSS = """
    CustomRichTabWidget {
        width: 100%;
        height: 100%;
        background: transparent;
        padding: 0 1;
        overflow-x: auto;
        overflow-y: auto;
        scrollbar-size: 1 1;
    }
    """

    def __init__(
        self,
        renderable_or_factory: Any,
        app_ref: Any = None,
        refresh_interval: float | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.renderable_or_factory = renderable_or_factory
        self.app_ref = app_ref
        self.refresh_interval = refresh_interval
        self._refresh_timer: Timer | None = None
        self._refresh_inflight = False

    def on_mount(self) -> None:
        self.update_content()
        if self.display:
            self._start_timer()

    def on_unmount(self) -> None:
        self._stop_timer()

    def on_show(self) -> None:
        self.update_content()
        self._start_timer()

    def on_hide(self) -> None:
        self._stop_timer()

    def _start_timer(self) -> None:
        if self._refresh_timer is not None:
            return
        interval = self.refresh_interval
        if interval is not None and interval > 0:
            self._refresh_timer = self.set_interval(interval, self.update_content)

    def _stop_timer(self) -> None:
        if self._refresh_timer is not None:
            self._refresh_timer.stop()
            self._refresh_timer = None

    def _invoke_factory(self) -> Any:
        factory = self.renderable_or_factory
        if not callable(factory):
            return factory

        import inspect
        try:
            sig = inspect.signature(factory)
        except (TypeError, ValueError):
            try:
                return factory(self.app_ref)
            except TypeError:
                return factory()

        required_positional = [
            p for p in sig.parameters.values()
            if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
            and p.default is inspect.Parameter.empty
        ]

        if not required_positional:
            return factory()
        return factory(self.app_ref)

    def update_content(self) -> None:
        if self._refresh_inflight:
            return
        self._refresh_inflight = True
        try:
            res = self._invoke_factory()
            if res is not None:
                self.update(res)
        except Exception as e:
            self.update(Text(f"Error rendering custom view: {e}", style="bold red"))
        finally:
            self._refresh_inflight = False


class DuskyTUI(App):
    CSS = """
Screen { background: $background; }

#telemetry-banner {
    width: 100%; height: 1;
    background: transparent;
    color: $primary;
    text-style: bold;
    text-align: center;
    content-align: center middle;
    text-wrap: nowrap;
    margin-top: 1;
    margin-bottom: 2;
    display: none;
}

#main-box {
    width: 100%; height: 100%;
    border: solid $primary 50%;
    border-title-color: $primary;
    border-title-style: bold;
    border-title-align: center;
    background: transparent;
    padding: 0 1 0 1;
}

#tab-bar { width: 100%; height: 1; margin-bottom: 1; background: transparent; }
#tabs-container { width: 1fr; height: 1; overflow-x: auto; scrollbar-size: 0 0; align: center middle; }

.tab-arrow {
    width: 3; height: 1; content-align: center middle;
    background: $background; color: $primary; text-style: bold; display: none;
}
.tab-arrow:hover { color: $foreground; background: $primary 25%; }

#content-area { height: 1fr; layout: horizontal; }
ContentSwitcher { width: 1fr; height: 1fr; background: transparent; }

#help-panel {
    width: 35%; height: 100%; min-width: 25; border-left: solid $primary;
    display: none; background: $background; padding: 1 2; overflow-y: auto;
}
#content-area.-show-help ContentSwitcher { width: 65%; }
#content-area.-show-help #help-panel { display: block; }

Tabs { width: auto; height: 1; background: transparent; }
Tabs > Underline { display: none; }

Tab { height: 1; padding: 0 1; color: $primary 60%; background: transparent; border: none; }
Tab:hover { color: $foreground; background: $primary 25%; }
Tab.-active { color: $background; background: $primary; text-style: bold; border: none; }

NoticeBox {
    width: 100%; height: auto; padding: 0 1; margin: 1 1 1 1; background: transparent;
}
NoticeBox > Markdown { background: transparent; color: $foreground; margin: 0; padding: 0; }
NoticeBox > Markdown > * { margin: 0; padding: 0; }

NoticeBox.-info { border-left: solid $primary; background: $primary 10%; }
NoticeBox.-warning { border-left: solid $warning; background: $warning 10%; }
NoticeBox.-danger { border-left: solid $error; background: $error 10%; }
NoticeBox.-success { border-left: solid $success; background: $success 10%; }

.list-wrapper { height: 1fr; }

ConfigOptionList {
    min-width: 20; width: 1fr; height: 1fr; scrollbar-size: 0 0;
    background: transparent; border: none;
}
ConfigOptionList > .option-list--option {
    padding: 0 1; background: transparent; transition: background 150ms linear;
}
ConfigOptionList > .option-list--option-hover { background: $primary 10%; }
ConfigOptionList > .option-list--option-highlighted { background: $primary 20%; }
ConfigOptionList > .option-list--option-disabled { background: transparent; color: $primary; }

.indicator-column { width: 2; height: 1fr; background: transparent; align: right top; }

ScrollIndicator { width: 1; height: 1fr; color: $primary; }
ScrollIndicator:hover { color: $foreground; }

#local-search {
    dock: bottom; border: none; border-top: solid $primary 50%;
    background: $primary 10%; color: $foreground;
    display: none; height: 3;
}
#local-search.-active { display: block; }

#footer {
    height: auto; min-height: 2; dock: bottom;
    border-top: solid $secondary; padding: 0 2; background: transparent;
}
#footer-bottom-row { width: 100%; height: 1; margin-top: 0; }

.footer-sep { color: $secondary; }

.footer-shortcut { padding: 0 1; background: transparent; }
.footer-shortcut:hover { text-style: bold; color: $foreground; background: $primary 25%; }
.footer-shortcut.-active { text-style: bold; color: $background; background: $primary; }

#status-bar { padding: 0 1; }

.mode-btn { padding: 0 1; background: transparent; }
.mode-btn:hover { text-style: bold; color: $foreground; background: $primary 25%; }

.pos-counter-btn { padding: 0 1; background: transparent; }

#file-link { padding: 0 1; background: transparent; }
#file-link:hover { text-style: bold; color: $foreground; background: $primary 25%; }

HybridInputScreen, PickerScreen, SearchScreen, DiffScreen, ShortcutsInfoScreen,
ConfirmDialog, AlertDialog, PasswordScreen, UnsavedChangesDialog {
    align: center middle;
    background: rgba(0, 0, 0, 0.75);
}

#picker-dialog { width: 60; height: 70%; background: $background; border: solid $primary; padding: 1 2; }
#search-dialog { width: 60; height: 80%; background: $background; border: solid $primary; padding: 1 2; }
#diff-dialog   { width: 70; height: 80%; background: $background; border: solid $primary; padding: 1 2; }
#shortcuts-dialog { width: 70; height: 80%; background: $background; border: solid $primary; padding: 1 2; }
#modal-dialog { width: 50; height: auto; background: $background; border: solid $primary; padding: 1 2; }

#alert-dialog, #confirm-dialog, #unsaved-dialog {
    width: 50; height: auto; max-height: 80%; background: $background; padding: 1 2;
}

#alert-dialog.-info, #confirm-dialog.-info, #unsaved-dialog.-info { border: solid $primary; }
#alert-dialog.-warning, #confirm-dialog.-warning, #unsaved-dialog.-warning { border: solid $warning; }
#alert-dialog.-danger, #confirm-dialog.-danger, #unsaved-dialog.-danger { border: solid $error; }
#alert-dialog.-success, #confirm-dialog.-success, #unsaved-dialog.-success { border: solid $success; }

#alert-message, #confirm-message { color: $foreground; margin-bottom: 1; }

#picker-list, #search-list, #diff-list, #shortcuts-list {
    height: 1fr; scrollbar-size: 0 0; background: transparent; border: none;
}

#search-list > .option-list--option {
    padding: 0 1; background: transparent; transition: background 100ms linear;
}
#search-list > .option-list--option-hover { background: $primary 10%; }
#search-list > .option-list--option-highlighted {
    background: $primary 20%; color: $foreground; text-style: bold;
}

#hybrid-option-list {
    height: auto; max-height: 10;
    border: solid $primary 50%;
    margin-top: 1; scrollbar-size: 0 0;
    background: transparent;
}
#hybrid-option-list > .option-list--option {
    padding: 0 1; background: transparent; transition: background 100ms linear;
}
#hybrid-option-list > .option-list--option-hover { background: $primary 10%; }
#hybrid-option-list > .option-list--option-highlighted {
    background: $primary 20%; color: $foreground; text-style: bold;
}

#diff-list > .option-list--option { padding: 0 1; background: transparent; }
#shortcuts-list > .option-list--option { padding: 0 1; background: transparent; }

.modal-btn-container {
    width: 100%; height: auto; align: center middle;
    margin-top: 1; background: transparent;
}

.modal-close-btn {
    background: $primary; color: $background; text-style: bold;
    padding: 0 2; width: auto; height: 1; margin: 0 1;
}
.modal-close-btn:hover { background: $foreground; color: $background; }

.modal-cancel-btn {
    background: $secondary; color: $foreground; text-style: bold;
    padding: 0 2; width: auto; height: 1; margin: 0 1;
}
.modal-cancel-btn:hover { background: $primary; color: $background; }

#modal-title, #picker-title {
    color: $primary; margin-bottom: 1; text-style: bold;
    border-bottom: solid $secondary;
    content-align: center middle; width: 100%;
}

#modal-hint {
    color: $secondary; text-style: italic;
    content-align: center middle; width: 100%; margin-top: 1;
}

Input { border: none; background: transparent; color: $foreground; border-bottom: solid $primary; }
Input:focus { border: none; border-bottom: solid $primary; }

Tooltip {
    background: $background;
    color: $foreground;
    border: solid $primary;
    padding: 1 2;
}
"""

    BINDINGS = [
        Binding("q", "quit", "Quit", priority=False),
        Binding("ctrl+c", "quit", "Quit", priority=True),

        Binding("ctrl+f", "search", "Search", priority=True),
        Binding("f1", "show_shortcuts", "Shortcuts", priority=True),
        Binding("ctrl+t", "toggle_save_mode", "Toggle Mode", priority=True),
        Binding("ctrl+s", "save_batch", "Save Batch", priority=True),
        Binding("ctrl+p", "save_preset", "Save Preset", priority=True),

        Binding("d", "show_diff", "Diff", priority=False),
        Binding("D", "delete_user_preset", "Delete Preset", priority=False),
        Binding("u", "undo", "Undo", priority=False),
        Binding("ctrl+r", "redo", "Redo", priority=True),
        Binding("r", "reset_item", "Reset Item", priority=False),
        Binding("R", "reset_all", "Reset Page", priority=True),
        Binding("?", "toggle_help", "Help", priority=False),
        Binding("/", "focus_local_search", "Search Inline", priority=False),

        Binding("tab", "next_tab", "Next Tab", priority=True),
        Binding("shift+tab", "prev_tab", "Prev Tab", priority=True),
        Binding("escape", "clear_local_search", "Clear Search", priority=False),

        Binding("alt+1", "switch_tab(0)", "Tab 1", show=False),
        Binding("alt+2", "switch_tab(1)", "Tab 2", show=False),
        Binding("alt+3", "switch_tab(2)", "Tab 3", show=False),
        Binding("alt+4", "switch_tab(3)", "Tab 4", show=False),
        Binding("alt+5", "switch_tab(4)", "Tab 5", show=False),
        Binding("alt+6", "switch_tab(5)", "Tab 6", show=False),
        Binding("alt+7", "switch_tab(6)", "Tab 7", show=False),
    ]

    auto_save = reactive(True)

    def __init__(
        self,
        engine_pool: dict[tuple[str, str], BaseEngine],
        default_engine_key: tuple[str, str],
        schema: dict[int, list[ConfigItem]],
        tabs: list[str],
        title="Dusky Editor",
        theme_path: str | None = None,
        default_mode: str = "auto",
        schema_name: str = "default",
        enable_user_presets: bool = True,
        user_presets_tab: str | None = None,
        global_popup: Any | None = None,
        tab_notices: dict[int, dict | list[dict]] | None = None,
        deferred_load=None,
        custom_views: dict[int | str, Any] | None = None,
        **kwargs
    ):
        super().__init__(**kwargs)

        self.deferred_load = deferred_load
        self.custom_views = custom_views or {}
        self.engine_pool = engine_pool
        self.default_engine_key = default_engine_key
        self.global_popup = global_popup
        self.tab_notices = tab_notices or {}
        self.schema = schema
        self.tabs = tabs
        self.editor_title = title
        self.schema_name = schema_name
        self.theme_path = Path(theme_path).expanduser().resolve() if theme_path else None
        self.enable_user_presets = enable_user_presets
        self.user_presets_tab_name = user_presets_tab
        self.user_presets_tab_idx = 0

        self._schema_dirty_counter = 0

        # Normalize self.tabs into dict[int, str]
        if isinstance(tabs, (list, tuple)):
            self.tabs = dict(enumerate(tabs))
        elif isinstance(tabs, dict):
            self.tabs = dict(tabs)
        else:
            self.tabs = {0: "General"}

        # Route User Presets to their proper schema tab assignment automatically.
        if self.user_presets_tab_name:
            for idx, name in self.tabs.items():
                if name == self.user_presets_tab_name:
                    self.user_presets_tab_idx = idx
                    break
        else:
            for idx, name in self.tabs.items():
                if str(name).lower() in ("presets", "theme", "themes", "appearance", "profiles"):
                    self.user_presets_tab_idx = idx
                    break

        # XDG-consistent preset storage.
        xdg_config = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser()
        self.user_presets_dir = (xdg_config / "dusky" / "tui" / self.schema_name).resolve()

        self.pending_commits: set[tuple[int, int]] = set()
        self.undo_stack: deque[list[tuple[int, int, Any, Any]]] = deque(maxlen=50)
        self.redo_stack: deque[list[tuple[int, int, Any, Any]]] = deque(maxlen=50)

        self._option_cache = OptionTextCache(maxsize=2048)
        self._preset_matrix = PresetMatchMatrix(app=self)

        self._committed: dict[tuple[int, int], Any] = {}
        for t_idx, items in self.schema.items():
            for i_idx, item in enumerate(items):
                self._committed[(t_idx, i_idx)] = clone_value(item.value)

        self._save_lock: asyncio.Lock | None = None
        self._global_save_timer: Timer | None = None
        self._save_queued_during_run = False

        self._key_map: dict[str, tuple[int, int]] = {}
        self._save_timers: dict[tuple[int, int], Timer] = {}
        self._pending_autosave_args: dict[tuple[int, int], tuple[ConfigItem, str, Any]] = {}
        self._indent_cache: dict[str, str] = {}

        # Debounce timer for preset UI refreshes.
        self._preset_refresh_timer: Timer | None = None

        # Theme colors.
        self.theme_colors = {
            "bg": "#111318",
            "fg": "#e1e2e9",
            "accent": "#a8c8ff",
            "error": "#ffb4ab",
            "warning": "#bdc7dc",
            "success": "#dbbce1",
            "muted": "#43474e",
            "info": "#a8c8ff",
        }

        self.last_theme_mtime: float = 0.0
        if self.theme_path:
            loaded_theme = load_matugen_json(self.theme_path)
            if loaded_theme:
                self.theme_colors.update(loaded_theme)

            try:
                self.last_theme_mtime = self.theme_path.stat().st_mtime
            except OSError:
                pass

        self._status_timer: Timer | None = None

        self._cached_tabs_container: Horizontal | None = None
        self._cached_tab_left: Label | None = None
        self._cached_tab_right: Label | None = None

        self.auto_save = (default_mode.lower() == "auto")

        # External target modification tracking.
        self.last_target_mtimes: dict[tuple[str, str], float] = {}
        self._initial_target_mtimes_set: bool = False

        # Lazy tab population state.
        self._tab_populated: set[int] = set()
        self._tab_dirty: set[int] = set()

        # Schema indexes.
        self._items_by_uid: dict[str, list[tuple[int, int, ConfigItem]]] = {}
        self._items_by_engine: dict[tuple[str, str], list[tuple[int, int, ConfigItem]]] = {}
        self._configurable_items: list[tuple[int, int, ConfigItem]] = []
        self._preset_items: list[tuple[int, int, ConfigItem]] = []

        # Async save / stale-write protection.
        self._write_generation: dict[str, int] = {}
        self._save_lock: asyncio.Lock | None = None
        self._sudo_keepalive: Timer | None = None

        # Color variable registry.
        self._color_var_registry: dict[str, str] = {}
        self._color_var_counter: int = 1

        self._rebuild_indexes()

    # =========================================================================
    # QUIT / MODAL GUARDS
    # =========================================================================
    def action_quit(self) -> None:
        # Do not allow q to stack quit dialogs over active modals.
        if self._modal_active():
            if isinstance(self.screen, UnsavedChangesDialog):
                self.screen.dismiss("cancel")
            else:
                self.screen.dismiss(None)
            return

        if self._sudo_keepalive:
            self._sudo_keepalive.stop()
            self._sudo_keepalive = None

        # BATCH mode: don't silently throw away queued writes.
        if not self.auto_save and self.pending_commits:
            def on_reply(reply: str) -> None:
                if reply == "save":
                    def on_quit_save(success: bool):
                        if success:
                            self.exit()

                    self.action_save_batch(on_complete=on_quit_save)

                elif reply == "discard":
                    self.exit()

            self.push_screen(UnsavedChangesDialog(len(self.pending_commits)), on_reply)
            return

        # AUTO mode: flush debounced writes safely.
        if self.auto_save and self._save_timers:
            for (ti, ii), timer in list(self._save_timers.items()):
                timer.stop()
                self.pending_commits.add((ti, ii))

            self._save_timers.clear()
            self._pending_autosave_args.clear()

            def on_auto_quit_save(success: bool):
                if success:
                    self.exit()
                else:
                    self.notify_status("Quit aborted: Could not save final changes.", level="warning")

            self.action_save_batch(on_complete=on_auto_quit_save)
            return

        self.exit()

    def _modal_active(self) -> bool:
        try:
            return isinstance(self.screen, ModalScreen)
        except Exception:
            return False

    # =========================================================================
    # COMPOSE
    # =========================================================================
    def compose(self) -> ComposeResult:
        with Vertical(id="main-box"):
            with Horizontal(id="tab-bar"):
                yield Label(" ◀ ", id="tab-left", classes="tab-arrow")

                with TabContainer(id="tabs-container"):
                    tabs_widget = Tabs(
                        *[Tab(name, id=f"tab-id-{i}") for i, name in self.tabs.items()],
                        id="tabs"
                    )

                    tabs_width = sum(Text(name).cell_len + 2 for name in self.tabs.values())
                    tabs_widget.styles.width = tabs_width

                    yield tabs_widget

                yield Label(" ▶ ", id="tab-right", classes="tab-arrow")

            yield Label("", id="telemetry-banner")

            with Horizontal(id="content-area"):
                with ContentSwitcher(initial="tab-0", id="content-switcher"):
                    for i, name in self.tabs.items():
                        with Vertical(id=f"tab-{i}"):
                            tab_notices = self.tab_notices.get(i)

                            if tab_notices:
                                if isinstance(tab_notices, dict):
                                    tab_notices = [tab_notices]

                                for n_idx, tab_notice in enumerate(tab_notices):
                                    if tab_notice.get("position", "top") != "bottom":
                                        level = tab_notice.get("level", "info")
                                        message = tab_notice.get("message", "")
                                        yield NoticeBox(message, level=level, id=f"notice-{i}-{n_idx}")

                            custom_view = self.custom_views.get(i)
                            if custom_view is None:
                                custom_view = self.custom_views.get(name)

                            if custom_view is not None:
                                refresh_interval = None
                                if isinstance(custom_view, dict) and "view" in custom_view:
                                    refresh_interval = custom_view.get("interval")
                                    custom_view = custom_view["view"]

                                if isinstance(custom_view, type) and issubclass(custom_view, Widget):
                                    yield custom_view()
                                elif isinstance(custom_view, Widget):
                                    yield custom_view
                                else:
                                    yield CustomRichTabWidget(
                                        renderable_or_factory=custom_view,
                                        app_ref=self,
                                        refresh_interval=refresh_interval,
                                        id=f"custom-view-{i}"
                                    )
                            else:
                                with Horizontal(classes="list-wrapper"):
                                    yield ConfigOptionList(id=f"list-{i}")

                                    with Vertical(classes="indicator-column"):
                                        yield ScrollIndicator("", id=f"indicator-{i}")

                            if tab_notices:
                                for n_idx, tab_notice in enumerate(tab_notices):
                                    if tab_notice.get("position", "top") == "bottom":
                                        level = tab_notice.get("level", "info")
                                        message = tab_notice.get("message", "")
                                        yield NoticeBox(message, level=level, id=f"notice-{i}-{n_idx}-bot")

                with Vertical(id="help-panel"):
                    yield Markdown("Select an item to view documentation.", id="help-markdown")

            yield Input(id="local-search", placeholder="Type to jump... (Enter to close)")
            yield AppFooter(id="footer")

    # =========================================================================
    # ENGINE / STATE HELPERS
    # =========================================================================
    def _sync_pending(self, tab_idx: int, item_idx: int, item: ConfigItem) -> None:
        key = (tab_idx, item_idx)
        baseline = self._committed.get(key, item.default)
        if item.value == baseline:
            self.pending_commits.discard(key)
        else:
            self.pending_commits.add(key)

    def _current_tab_index(self) -> int | None:
        try:
            switcher = self.query_one(ContentSwitcher)
            if switcher.current and isinstance(switcher.current, str) and switcher.current.startswith("tab-"):
                return int(switcher.current.split("-")[1])
        except Exception:
            return None
        return None

    def _on_item_value_changed(self, item: ConfigItem) -> None:
        if hasattr(self, "_option_cache"):
            self._option_cache.invalidate_uid(item.uid)
        if item.type_ not in ("preset", "action", "menu"):
            if hasattr(self, "_preset_matrix"):
                self._preset_matrix.on_item_changed(item)
            if hasattr(self, "_option_cache"):
                self._option_cache.invalidate_presets()
        self._schema_dirty_counter += 1
        cur = self._current_tab_index()
        if cur is not None:
            self._tab_dirty.add(cur)

    def _get_item_engine_info(self, item: ConfigItem) -> tuple[str, str]:
        """
        Resolves target engine and file config dynamically via overrides.
        """
        e_type = (
            item.engine_type_override.lower()
            if item.engine_type_override
            else self.default_engine_key[0]
        )

        t_file = (
            str(Path(item.target_file_override).expanduser().resolve())
            if item.target_file_override
            else self.default_engine_key[1]
        )

        return (e_type, t_file)

    def _get_engine_for_item(self, item: ConfigItem) -> BaseEngine:
        key = self._get_item_engine_info(item)
        engine = self.engine_pool.get(key)

        if engine is None:
            raise KeyError(
                f"No engine registered for {key} (required by item {item.uid!r}). "
                f"Registered: {list(self.engine_pool)}"
            )

        return engine

    def _get_item_uid(self, item: ConfigItem) -> str:
        """
        Robust internal resolver for mapping children to parents safely.
        """
        return f"{item.scope}.{item.key}" if item.scope and item.scope != "DEFAULT" else item.key

    def _lookup_state(self, state: dict, item: ConfigItem) -> Any:
        """
        Canonical state lookup supporting:
          - scope/key
          - scope.key
          - key
        """
        if not state:
            return None

        if item.scope and item.scope != "DEFAULT":
            candidates = (
                f"{item.scope}/{item.key}",
                f"{item.scope}.{item.key}",
                item.key,
            )
        else:
            candidates = (item.key,)

        for candidate in candidates:
            if candidate in state:
                return state[candidate]

        return None

    def _get_schema_item(self, tab_idx: int, item_idx: int) -> ConfigItem | None:
        try:
            return self.schema[tab_idx][item_idx]
        except Exception:
            return None

    # =========================================================================
    # SCHEMA INDEXES
    # =========================================================================
    def _rebuild_indexes(self) -> None:
        self._key_map.clear()
        self._items_by_uid.clear()
        self._items_by_engine.clear()
        self._configurable_items.clear()
        self._preset_items.clear()

        for t_idx, items in self.schema.items():
            for i_idx, item in enumerate(items):
                uid = self._get_item_uid(item)

                self._key_map[uid] = (t_idx, i_idx)
                self._items_by_uid.setdefault(uid, []).append((t_idx, i_idx, item))

                try:
                    ekey = self._get_item_engine_info(item)
                except Exception:
                    ekey = self.default_engine_key

                self._items_by_engine.setdefault(ekey, []).append((t_idx, i_idx, item))

                if item.type_ not in ("action", "preset", "menu"):
                    self._configurable_items.append((t_idx, i_idx, item))

                if item.type_ == "preset":
                    self._preset_items.append((t_idx, i_idx, item))

        if hasattr(self, "_preset_matrix"):
            self._preset_matrix.rebuild(self._configurable_items)

    def _rebuild_key_map(self) -> None:
        # Compatibility wrapper for older call sites.
        self._rebuild_indexes()

    # =========================================================================
    # PRESET MATCHING
    # =========================================================================
    def _get_preset_match_ratio(self, preset_item: ConfigItem) -> float:
        """
        Calculates how much of a preset's payload currently matches reality in O(1) time.
        """
        if hasattr(self, "_preset_matrix"):
            return self._preset_matrix.ratio(preset_item)
        return 0.0

    def _is_preset_active(self, preset_item: ConfigItem) -> bool:
        return self._get_preset_match_ratio(preset_item) == 1.0

    def _refresh_presets_ui(self) -> None:
        """
        Forces an instant visual update of all presets to reflect current active status.
        """
        for t_idx, i_idx, itm in self._preset_items:
            self._refresh_single_ui(t_idx, i_idx, itm)

    # =========================================================================
    # OPTION RENDERING
    # =========================================================================
    def _build_option(
        self,
        item: ConfigItem,
        is_highlighted: bool = False,
        indent_prefix: str = ""
    ) -> Text:
        val_ser = item.serialize(item.value)
        init_ser = item.serialize(item.initial_value)
        def_ser = item.serialize(item.default)
        ratio_bucket = int(self._get_preset_match_ratio(item) * 10) if item.type_ == "preset" else -1

        cache_key = (
            item.uid,
            item.type_,
            val_ser,
            item.exists_in_target,
            val_ser != init_ser,
            val_ser != def_ser,
            is_highlighted,
            indent_prefix,
            item.expanded,
            bool(item.warning_msg),
            item.is_parent,
            ratio_bucket,
            getattr(self, "_theme_version", 0)
        )

        if hasattr(self, "_option_cache"):
            hit = self._option_cache.get(cache_key)
            if hit is not None:
                return hit

        txt = Text()

        exists = item.exists_in_target
        is_pending = (val_ser != init_ser)
        is_modified = (val_ser != def_ser)

        CURSOR_CHAR = "▶"
        cursor = f"{CURSOR_CHAR} " if is_highlighted else "  "
        txt.append(cursor, style=f"{self.theme_colors['accent']} bold" if is_highlighted else "")

        ratio = 0.0
        is_active_preset = False
        is_deviated_preset = False

        if item.type_ == "preset":
            ratio = self._get_preset_match_ratio(item)
            is_active_preset = (ratio == 1.0)
            is_deviated_preset = (0.9 <= ratio < 1.0)

        # Tree indentation.
        if indent_prefix:
            txt.append(indent_prefix, style=self.theme_colors["muted"])

        if item.is_parent:
            exp_char = "[-] " if item.expanded else "[+] "
            txt.append(exp_char, style=f"{self.theme_colors['accent']} bold")
        elif indent_prefix and len(indent_prefix) > 0 and indent_prefix != "    ":
            txt.append("    ")
        else:
            txt.append("    ")

        # Status dot.
        if item.type_ == "preset":
            if is_active_preset:
                txt.append("●  ", style=self.theme_colors["success"])
            elif is_deviated_preset:
                txt.append("●  ", style=self.theme_colors["warning"])
            else:
                txt.append("●  ", style=self.theme_colors["muted"])

        elif item.type_ in ("action", "menu"):
            txt.append("●  ", style=self.theme_colors["muted"])

        else:
            if not self.auto_save and is_pending:
                txt.append("[+] ", style=self.theme_colors["warning"])
            else:
                dot_color = self.theme_colors["error"] if (is_modified and exists) else self.theme_colors["muted"]
                txt.append("●  ", style=dot_color)

        # Label rendering.
        warning_marker = "⚠️ " if item.warning_msg else ""

        if exists:
            if item.type_ == "preset" and is_active_preset:
                label_style = f"{self.theme_colors['success']} bold"
            elif item.type_ == "preset" and is_deviated_preset:
                label_style = f"{self.theme_colors['warning']} bold" if is_highlighted else f"{self.theme_colors['fg']}"
            else:
                label_style = f"{self.theme_colors['fg']} bold" if is_highlighted else self.theme_colors["fg"]

            if warning_marker:
                txt.append(warning_marker, style=f"bold {self.theme_colors['warning']}")
                txt.append(_pad_cells(item.label, 32), style=label_style)
            else:
                txt.append(_pad_cells(item.label, 35), style=label_style)

        else:
            label_style = (
                f"{self.theme_colors['muted']} strike"
                if not is_highlighted
                else f"{self.theme_colors['muted']} strike bold"
            )

            raw_label = f"{warning_marker}{item.label} [Missing]"
            padding_len = max(0, 35 - cell_len(raw_label))

            txt.append(raw_label, style=label_style)
            txt.append(" " * padding_len)

        val_str = str(item.value)

        # Tail rendering.
        if item.type_ in ("action", "preset", "menu"):
            txt.append("   ")

            if item.type_ == "preset":
                if is_active_preset:
                    txt.append("Active", style=f"bold {self.theme_colors['success']}")
                elif is_deviated_preset:
                    txt.append("Apply", style=f"bold {self.theme_colors['warning']}")
                else:
                    txt.append(
                        "Apply",
                        style=f"bold {self.theme_colors['accent']}" if exists else f"{self.theme_colors['muted']} italic"
                    )

            elif item.type_ == "action":
                txt.append(
                    " Run",
                    style=f"bold {self.theme_colors['warning']}" if exists else f"{self.theme_colors['muted']} italic"
                )

        else:
            accent = self.theme_colors["accent"] if exists else self.theme_colors["muted"]
            fg = self.theme_colors["fg"] if exists else self.theme_colors["muted"]

            match item.type_:
                case "bool":
                    trigger = is_trigger_item(item)

                    if trigger:
                        opt0 = str(item.options[0]) if item.options else ""
                        opt0_lower = opt0.lower()

                        if opt0_lower.startswith("trigger:"):
                            btn_label = f" {opt0[8:]} "
                        elif opt0_lower.startswith("copy:"):
                            btn_label = f" {opt0[5:]} "
                        elif opt0_lower == "trigger":
                            btn_label = " Apply "
                        elif opt0_lower == "copy":
                            btn_label = " Copy "
                        else:
                            btn_label = " Apply "

                        if not exists:
                            txt.append(btn_label, style=f"{self.theme_colors['muted']} italic")
                        else:
                            txt.append(
                                btn_label,
                                style=(
                                    f"bold {self.theme_colors['bg']} on {self.theme_colors['accent']}"
                                    if item.value
                                    else f"bold {self.theme_colors['accent']}"
                                )
                            )

                    elif not exists:
                        txt.append(
                            f" {'◉ ON' if item.value else '◯ OFF'} ",
                            style=f"{self.theme_colors['muted']} italic"
                        )

                    elif item.value:
                        txt.append(" ◉ ON  ", style=f"bold {self.theme_colors['bg']} on {self.theme_colors['success']}")
                    else:
                        txt.append(" ◯ OFF ", style=f"{self.theme_colors['muted']} on {self.theme_colors['bg']}")

                case "string":
                    if val_str == "":
                        txt.append("[✎] Unset", style=f"italic {self.theme_colors['muted']}")
                    else:
                        txt.append(f"[✎] {val_str}", style=accent)

                case "picker":
                    txt.append(f"[+] {val_str}", style=accent)

                case "color":
                    resolved_color = self.theme_colors.get(val_str, val_str)
                    r, g, b = color_to_rgb(resolved_color)
                    hex_color = f"#{r:02x}{g:02x}{b:02x}"

                    if not is_theme_variable(val_str):
                        txt.append(" ⬤ ", style=hex_color if exists else self.theme_colors["muted"])

                    if is_theme_variable(val_str):
                        display_name = None

                        # Map to schema hints if possible.
                        if item.options:
                            sorted_opts = sorted(
                                enumerate(item.options),
                                key=lambda x: len(str(x[1])),
                                reverse=True
                            )

                            for idx, opt in sorted_opts:
                                if val_str.startswith(str(opt)):
                                    if idx < len(item.hints) and item.hints[idx]:
                                        base_hint = item.hints[idx]
                                        suffix = val_str[len(str(opt)):].strip()

                                        if suffix:
                                            display_name = f"{base_hint} [{suffix}]"
                                        else:
                                            display_name = base_hint

                                    break

                        # Native variable extraction.
                        if not display_name:
                            norm_val = val_str.strip()
                            extracted_name = None

                            css_match = re.search(r"var\(--([^)]+)\)", norm_val)
                            if css_match:
                                extracted_name = css_match.group(1)

                            elif "{{" in norm_val:
                                mat_match = re.search(r"\{\{([^}]+)\}\}", norm_val)
                                if mat_match:
                                    parts = mat_match.group(1).split(".")
                                    extracted_name = (
                                        parts[1]
                                        if len(parts) > 1 and parts[0] == "colors"
                                        else parts[-1]
                                    )

                            else:
                                prefix_match = re.search(r"[@$]([a-zA-Z0-9_-]+)", norm_val)
                                if prefix_match:
                                    extracted_name = prefix_match.group(1)

                                elif re.match(r"^[a-zA-Z0-9_-]+$", norm_val):
                                    extracted_name = norm_val

                            if extracted_name:
                                display_name = extracted_name.replace("_", " ").replace("-", " ").title()

                        # Fallback unknown variables.
                        if not display_name:
                            norm_val = val_str.strip()
                            if norm_val not in self._color_var_registry:
                                self._color_var_registry[norm_val] = f"Variable {self._color_var_counter}"
                                self._color_var_counter += 1

                            display_name = self._color_var_registry[norm_val]

                        txt.append(f" {display_name}", style=accent)

                    else:
                        color_name = get_color_name(r, g, b)

                        if resolved_color != val_str:
                            txt.append(f"[{val_str}] ", style=self.theme_colors["muted"])

                        txt.append(f"{color_name}", style=accent)

                case _:
                    txt.append(val_str, style=fg)

        if is_modified and is_highlighted and exists:
            txt.append("   ↩ Reset", style=f"italic {self.theme_colors['error']}")

        if hasattr(self, "_option_cache"):
            return self._option_cache.put(cache_key, txt)
        return txt

    def _intern_styles(self) -> None:
        c = getattr(self, "theme_colors", {})
        self._style = {
            "cursor_hl": f"{c.get('accent', '#a8c8ff')} bold",
            "cursor": "",
            "muted": c.get("muted", "#43474e"),
            "accent_bold": f"{c.get('accent', '#a8c8ff')} bold",
            "fg": c.get("fg", "#e1e2e9"),
            "fg_bold": f"{c.get('fg', '#e1e2e9')} bold",
            "success": c.get("success", "#dbbce1"),
            "warning": c.get("warning", "#bdc7dc"),
            "error": c.get("error", "#ffb4ab"),
        }
        self._theme_version = getattr(self, "_theme_version", 0) + 1
        if hasattr(self, "_option_cache"):
            self._option_cache.clear()

    # =========================================================================
    # USER PRESETS
    # =========================================================================
    def _load_user_presets(self) -> None:
        if not self.enable_user_presets:
            return

        self.user_presets_dir.mkdir(parents=True, exist_ok=True)

        # Remove dynamically added User Presets from previous loads.
        for t_idx, items in self.schema.items():
            self.schema[t_idx] = [
                itm for itm in items
                if not (
                    itm.group == "User Presets"
                    and (
                        itm.key.startswith("__user_preset_")
                        or itm.key in ("__save_new_preset", "__import_new_preset")
                    )
                )
            ]

        save_btn = ConfigItem(
            label="[+] Save as Preset",
            key="__save_new_preset",
            scope="DEFAULT",
            type_="action",
            default=None,
            group="User Presets",
            extended_help="Click here to save the current configuration state as a new reusable preset."
        )
        save_btn.exists_in_target = True

        import_btn = ConfigItem(
            label="[+] Import Preset",
            key="__import_new_preset",
            scope="DEFAULT",
            type_="action",
            default=None,
            group="User Presets",
            extended_help=(
                "Click here to create a new empty preset template and instantly open it "
                "so you can paste in an external payload."
            )
        )
        import_btn.exists_in_target = True

        user_preset_items = [save_btn, import_btn]

        for file_path in sorted(self.user_presets_dir.glob("*.json"), key=lambda p: p.stem.lower()):
            name = file_path.stem
            warning = None

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    payload = json.load(f)

                if not isinstance(payload, dict):
                    payload = {"__INVALID__": True, "__ERROR__": "Expected JSON object"}
                    warning = "Invalid preset payload: expected JSON object"

            except Exception as e:
                payload = {"__INVALID__": True, "__ERROR__": str(e)}
                warning = "Invalid preset JSON file"

            new_item = ConfigItem(
                label=f"User: {name}",
                key=f"__user_preset_{name}",
                scope="DEFAULT",
                type_="preset",
                default=None,
                group="User Presets",
                extended_help=(
                    f"**User-defined preset:** {name}\n"
                    "Press `Shift+D` to delete this preset.\n"
                    "Press `Ctrl+P` and use the same name to overwrite/update it."
                ),
                preset_payload=payload,
                warning_msg=warning
            )
            new_item.exists_in_target = True
            user_preset_items.append(new_item)

        if self.user_presets_tab_idx not in self.schema:
            self.schema[self.user_presets_tab_idx] = []

        self.schema[self.user_presets_tab_idx].extend(user_preset_items)
        self._schema_dirty_counter += 1

    # =========================================================================
    # EXTERNAL EDITING
    # =========================================================================
    def open_file_externally(
        self,
        file_path: Path | str,
        button: int = 1,
        touch_first: bool = False
    ) -> None:
        expanded_path = Path(file_path).expanduser().resolve()

        if touch_first:
            expanded_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                expanded_path.touch(exist_ok=True)
            except OSError:
                pass

        if not expanded_path.exists():
            self.notify_status("File does not exist on disk.", level="warning")
            return

        try:
            if button == 1:
                cmd = None

                if shutil.which("xdg-open"):
                    cmd = ["xdg-open", str(expanded_path)]
                elif shutil.which("mousepad"):
                    cmd = ["mousepad", str(expanded_path)]

                if cmd:
                    subprocess.Popen(
                        cmd,
                        start_new_session=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                else:
                    self.notify_status("No suitable external editor found (xdg-open or mousepad).", level="warning")

            elif button == 3:
                editor_env = os.environ.get("VISUAL", os.environ.get("EDITOR", "nano"))
                editor_cmd = shlex.split(editor_env)

                with self.suspend():
                    subprocess.run([*editor_cmd, str(expanded_path)])

        except (FileNotFoundError, OSError):
            self.notify_status("Error resolving path or launching external editor.", level="error")

    # =========================================================================
    # MOUNT
    # =========================================================================
    @override
    async def on_mount(self) -> None:
        self._save_lock = asyncio.Lock()

        self.query_one("#main-box").border_title = f" {self.editor_title} "
        self.apply_theme_to_engine()

        first_engine = self.engine_pool[self.default_engine_key]
        self.query_one("#file-link", FileLink).path = first_engine.target_path

        self._cached_tabs_container = self.query_one("#tabs-container", Horizontal)
        self._cached_tab_left = self.query_one("#tab-left", Label)
        self._cached_tab_right = self.query_one("#tab-right", Label)

        try:
            self.query_one("#shortcut-ctrl-s").display = not self.auto_save
            self.query_one("#shortcut-R").display = self.auto_save
        except Exception:
            pass

        self._intern_styles()
        self.run_deferred_boot(initial_tab=0)

        if first_ol := self.current_option_list:
            first_ol.focus()
            self._update_pagination(first_ol)

        # Telemetry.
        self.telemetry_engine = None
        for engine in self.engine_pool.values():
            if hasattr(engine, "get_telemetry"):
                self.telemetry_engine = engine
                break

        if self.telemetry_engine:
            self.query_one("#telemetry-banner").display = True
            self.set_interval(1.0, self.update_telemetry)

        if self.theme_path:
            self.set_interval(1.0, self.watch_theme_file)

        self.call_after_refresh(self.check_tab_overflow)
        self.call_after_refresh(self._update_scroll_indicators)
        self._update_footer_legend()

        # Deferred loading in background.
        if self.deferred_load:
            def _deferred_worker():
                try:
                    res = self.deferred_load()

                    if isinstance(res, tuple) and len(res) == 2:
                        updated_tabs, new_items = res
                    else:
                        updated_tabs = res
                        new_items = None

                    deferred_states = {ekey: eng.load_state() for ekey, eng in self.engine_pool.items()}
                    self.call_from_thread(self._apply_deferred_tabs, updated_tabs, deferred_states, new_items)

                except Exception as e:
                    print(f"[DuskyTUI] Deferred load error: {e}", file=sys.stderr)

            threading.Thread(target=_deferred_worker, daemon=True).start()

        # Global schema popup.
        if self.global_popup:
            def show_popup():
                if isinstance(self.global_popup, dict):
                    msg = self.global_popup.get("message", "")
                    title = self.global_popup.get("title", "System Notice")
                    level = self.global_popup.get("level", "info")
                    btn_text = self.global_popup.get("btn_text", " I Understand ")

                    if self.global_popup.get("require_confirm", False):
                        def on_confirm(confirmed: bool):
                            if not confirmed and self.global_popup.get("cancel_quits", False):
                                self.action_quit()

                        self.push_screen(ConfirmDialog(msg, title=title, level=level), on_confirm)
                    else:
                        self.push_screen(AlertDialog(msg, title=title, level=level, btn_text=btn_text))
                else:
                    self.push_screen(AlertDialog(str(self.global_popup), title="System Notice", level="warning"))

            self.call_after_refresh(show_popup)

    def _init_boot_state(self) -> None:
        self._states: dict[tuple[str, str], Any] = {}
        self._loaded_engines: set[tuple[str, str]] = set()
        self._pending_engine_loads: set[tuple[str, str]] = set()
        self._failed_engines: dict[tuple[str, str], str] = {}
        self._mounted_tabs: set[int] = set()
        self._populated_tabs: set[int] = set()
        self._tab_data_ready: set[int] = set()
        self._boot_complete: bool = False

        self._all_user_units: list[str] = []
        self._all_sys_units: list[str] = []
        for tab_items in self.schema.values():
            for item in tab_items:
                if item.type_ in ("action", "preset", "menu"):
                    continue
                match item.scope:
                    case "user":
                        self._all_user_units.append(item.key)
                    case "system":
                        self._all_sys_units.append(item.key)

    def _engines_for_tab(self, tab_idx: int) -> set[tuple[str, str]]:
        keys: set[tuple[str, str]] = set()
        for item in self.schema.get(tab_idx, []):
            if item.type_ in ("action", "preset", "menu"):
                continue
            keys.add(self._get_item_engine_info(item))
        return keys

    def _load_one_engine_sync(self, ekey: tuple[str, str]) -> Any:
        eng = self.engine_pool[ekey]
        if self.deferred_load and hasattr(eng, "load_state_for_units"):
            return eng.load_state_for_units(self._all_user_units, self._all_sys_units)
        return eng.load_state()

    def _load_engines_batch_sync(
        self, keys: set[tuple[str, str]]
    ) -> tuple[dict[tuple[str, str], Any], dict[tuple[str, str], str]]:
        states: dict[tuple[str, str], Any] = {}
        errors: dict[tuple[str, str], str] = {}
        for ekey in keys:
            try:
                states[ekey] = self._load_one_engine_sync(ekey)
            except Exception as exc:
                errors[ekey] = f"{type(exc).__name__}: {exc}"
        return states, errors

    def _apply_states_to_tab(self, tab_idx: int, states: dict[tuple[str, str], Any]) -> None:
        items = self.schema.get(tab_idx, [])
        freshly: list[ConfigItem] = []

        for i_idx, item in enumerate(items):
            if item.type_ in ("action", "preset", "menu"):
                item.exists_in_target = True
                if not item._initial_loaded:
                    item.initial_value = clone_value(item.value)
                    item._initial_loaded = True
                    self._committed[(tab_idx, i_idx)] = clone_value(item.value)
                continue

            engine_key = self._get_item_engine_info(item)
            if engine_key not in states and not item._initial_loaded:
                continue

            state = states.get(engine_key, self._states.get(engine_key, {}))
            raw = self._lookup_state(state, item)

            if raw is not None:
                item.exists_in_target = True
                new_val = item.deserialize(raw)
            else:
                item.exists_in_target = (item.default != "nil")
                new_val = item.value

            if not item._initial_loaded:
                item.value = new_val
                item.initial_value = clone_value(item.value)
                item._initial_loaded = True
                self._committed[(tab_idx, i_idx)] = clone_value(item.value)
                freshly.append(item)

        self._tab_data_ready.add(tab_idx)
        if freshly and hasattr(self, "_preset_matrix"):
            self._preset_matrix.ingest_items(freshly)

    def _mark_boot_complete_if_done(self) -> None:
        self._boot_complete = (
            not self._pending_engine_loads
            and set(self.engine_pool).issubset(
                self._loaded_engines | set(self._failed_engines)
            )
        )
        if self._boot_complete and hasattr(self, "_preset_matrix"):
            self._preset_matrix.rebuild(self._configurable_items)
            if hasattr(self, "_option_cache"):
                self._option_cache.invalidate_presets()
            self._schema_dirty_counter += 1

    @work(exclusive=True, group="engine-boot", exit_on_error=False)
    async def run_deferred_boot(self, *, initial_tab: int = 0) -> None:
        self._init_boot_state()
        await asyncio.to_thread(self._load_user_presets)

        need_now = self._engines_for_tab(initial_tab) if self.tabs else set()
        deferred = set(self.engine_pool) - need_now

        for ekey in need_now:
            try:
                self._states[ekey] = await asyncio.to_thread(self._load_one_engine_sync, ekey)
                self._loaded_engines.add(ekey)
            except Exception as exc:
                self._failed_engines[ekey] = f"{type(exc).__name__}: {exc}"
                self.notify_status(f"Failed to load {ekey}: {exc}", level="error")

        for t_idx in self.tabs:
            if self._engines_for_tab(t_idx).issubset(self._loaded_engines):
                self._apply_states_to_tab(t_idx, self._states)

        if self.tabs:
            await asyncio.sleep(0)
            self._populate_option_list(initial_tab)
            self._populated_tabs.add(initial_tab)

        if deferred:
            self._pending_engine_loads |= set(deferred)
            await self._load_engines_async(deferred)
        else:
            self._mark_boot_complete_if_done()

    @work(exclusive=True, group="engine-boot", exit_on_error=False)
    async def _load_engines_async(self, engine_keys: set[tuple[str, str]]) -> None:
        if not engine_keys:
            return
        states, errors = await asyncio.to_thread(self._load_engines_batch_sync, engine_keys)
        self.post_message(
            EnginesLoaded(states=states, attempted=engine_keys, errors=errors)
        )

    def on_engines_loaded(self, event: EnginesLoaded) -> None:
        self._pending_engine_loads -= set(event.attempted)

        for ekey, err in event.errors.items():
            self._failed_engines[ekey] = err
            self.notify_status(f"Failed to load {ekey}: {err}", level="error")

        if event.states:
            self._states.update(event.states)
            self._loaded_engines |= set(event.states)

        for t_idx in self.tabs:
            if t_idx in self._tab_data_ready:
                continue
            if self._engines_for_tab(t_idx).issubset(self._loaded_engines):
                self._apply_states_to_tab(t_idx, self._states)
                self._tab_dirty.add(t_idx)

        cur = self._current_tab_index()
        if cur is not None and cur in self._tab_data_ready:
            if cur not in self._populated_tabs or cur in self._tab_dirty:
                self._populate_option_list(cur)
                self._populated_tabs.add(cur)
                self._tab_dirty.discard(cur)

        self._mark_boot_complete_if_done()

    def require_boot_complete(self) -> bool:
        if getattr(self, "_boot_complete", True):
            return True
        self.notify_status(
            "Still loading configuration backends — try again in a moment.",
            level="warning",
        )
        return False

    # =========================================================================
    # TAB POPULATION / LAZY UI
    # =========================================================================
    def _populate_option_list(self, tab_idx: int, maintain_highlight_id: str | None = None) -> None:
        try:
            ol = self.query_one(f"#list-{tab_idx}", ConfigOptionList)
        except Exception:
            return

        scroll_y = ol.scroll_y

        if not maintain_highlight_id and ol.highlighted is not None:
            try:
                maintain_highlight_id = ol.get_option_at_index(ol.highlighted).id
            except OptionDoesNotExist:
                pass

        items = self.schema.get(tab_idx, [])
        options = []
        current_group = None
        first_item_id = None

        children_map = {self._get_item_uid(itm): [] for itm in items}
        root_items = []

        for orig_idx, itm in enumerate(items):
            pref = itm.parent_ref
            if pref and pref in children_map:
                children_map[pref].append((orig_idx, itm))
            else:
                root_items.append((orig_idx, itm))

        # Clear only this tab's indent cache entries.
        prefix_key = f"item_{tab_idx}_"
        self._indent_cache = {
            k: v for k, v in self._indent_cache.items()
            if not k.startswith(prefix_key)
        }

        def traverse(node_idx: int, node_item: ConfigItem, is_last_sibling_list: list[bool]):
            nonlocal current_group, first_item_id

            if node_item.group and node_item.group != current_group:
                current_group = node_item.group
                header_txt = Text(f" {current_group.upper()}", style=f"bold {self.theme_colors['accent']}")
                options.append(Option(header_txt, id=f"header_{tab_idx}_{node_idx}", disabled=True))

            opt_id = f"item_{tab_idx}_{node_idx}"

            if first_item_id is None:
                first_item_id = opt_id

            is_hl = (
                (maintain_highlight_id == opt_id)
                if maintain_highlight_id
                else (tab_idx == 0 and first_item_id == opt_id)
            )

            prefix = ""
            depth = len(is_last_sibling_list) - 1

            if depth > 0:
                for is_last in is_last_sibling_list[:-1]:
                    prefix += "   " if is_last else " │ "
                prefix += " └─ " if is_last_sibling_list[-1] else " ├─ "

            self._indent_cache[opt_id] = prefix

            options.append(
                Option(
                    self._build_option(node_item, is_highlighted=is_hl, indent_prefix=prefix),
                    id=opt_id
                )
            )

            if node_item.is_parent and node_item.expanded:
                uid = self._get_item_uid(node_item)
                children = children_map.get(uid, [])

                for i, (child_idx, child_item) in enumerate(children):
                    is_last = (i == len(children) - 1)
                    traverse(child_idx, child_item, is_last_sibling_list + [is_last])

        for i, (orig_idx, itm) in enumerate(root_items):
            is_last = (i == len(root_items) - 1)
            traverse(orig_idx, itm, [is_last])

        ol.clear_options()
        ol.add_options(options)

        if maintain_highlight_id:
            try:
                ol.highlighted = ol.get_option_index(maintain_highlight_id)
            except OptionDoesNotExist:
                ol.highlighted = 0 if ol.option_count > 0 else None

        elif first_item_id and tab_idx == 0:
            ol.last_highlighted_id = first_item_id
            try:
                ol.highlighted = ol.get_option_index(first_item_id)
            except OptionDoesNotExist:
                pass

        ol.scroll_y = scroll_y

        self._tab_populated.add(tab_idx)
        self._tab_dirty.discard(tab_idx)

        self.call_after_refresh(self._update_scroll_indicators)

    def _apply_deferred_tabs(
        self,
        tab_indices: list[int],
        states: dict,
        new_items: dict[int, list[ConfigItem]] | None = None
    ) -> None:
        self._schema_dirty_counter += 1

        for tab_idx in tab_indices:
            if new_items and tab_idx in new_items:
                self.schema[tab_idx] = new_items[tab_idx]

        self._rebuild_indexes()

        current_idx = None
        try:
            switcher = self.query_one(ContentSwitcher)
            if switcher.current:
                current_idx = int(switcher.current.split("-")[1])
        except Exception:
            current_idx = None

        for tab_idx in tab_indices:
            for idx, item in enumerate(self.schema.get(tab_idx, [])):
                engine_key = self._get_item_engine_info(item)
                state = states.get(engine_key, {})
                raw = self._lookup_state(state, item)

                if item.type_ in ("action", "preset", "menu"):
                    item.exists_in_target = True
                elif raw is not None:
                    item.exists_in_target = True
                    item.value = item.deserialize(raw)
                else:
                    item.exists_in_target = (item.default != "nil")

                if not item._initial_loaded:
                    item.initial_value = clone_value(item.value)
                    item._initial_loaded = True
                self._committed[(tab_idx, idx)] = clone_value(item.value)

            self._tab_data_ready.add(tab_idx)

            if tab_idx in self._populated_tabs or tab_idx in self._tab_populated or tab_idx == current_idx:
                self._populate_option_list(tab_idx)
                self._populated_tabs.add(tab_idx)
                self._tab_dirty.discard(tab_idx)
            else:
                self._tab_dirty.add(tab_idx)

        if current_idx in tab_indices:
            if ol := self.current_option_list:
                self._update_pagination(ol)

    def _refresh_single_ui(self, tab_idx: int, item_idx: int, item: ConfigItem) -> None:
        if tab_idx not in self._tab_populated:
            self._tab_dirty.add(tab_idx)
            return

        try:
            ol = self.query_one(f"#list-{tab_idx}", ConfigOptionList)
            opt_id = f"item_{tab_idx}_{item_idx}"
            idx = ol.get_option_index(opt_id)
            is_hl = (ol.last_highlighted_id == opt_id)
            prefix = self._indent_cache.get(opt_id, "")

            ol.replace_option_prompt_at_index(
                idx,
                self._build_option(item, is_hl, prefix)
            )

        except OptionDoesNotExist:
            self._tab_dirty.add(tab_idx)

            try:
                switcher = self.query_one(ContentSwitcher)
                if switcher.current:
                    current_idx = int(switcher.current.split("-")[1])
                    if current_idx == tab_idx:
                        self._populate_option_list(tab_idx)
            except Exception:
                pass

        except Exception:
            pass

    def _refresh_all_ui(self) -> None:
        for tab_idx in self.schema.keys():
            if tab_idx in self._tab_populated:
                self._populate_option_list(tab_idx)
            else:
                self._tab_dirty.add(tab_idx)

    # =========================================================================
    # SAVE MODE / FOOTER
    # =========================================================================
    def watch_auto_save(self, old: bool, new: bool) -> None:
        if not getattr(self, "is_mounted", False):
            return

        self._update_footer_legend()

        try:
            self.query_one("#shortcut-ctrl-s").display = not new
            self.query_one("#shortcut-R").display = new
            self.call_after_refresh(self.query_one("#footer-shortcuts-container", FlowContainer).reflow)
        except Exception:
            pass

        # Switching AUTO -> BATCH: flush pending debounce timers into batch queue.
        if not new:
            for (ti, ii), timer in list(self._save_timers.items()):
                timer.stop()
                self.pending_commits.add((ti, ii))

            self._save_timers.clear()
            self._pending_autosave_args.clear()
            self._update_footer_legend()
            return

        # Switching BATCH -> AUTO: commit pending batch changes.
        if new and getattr(self, "pending_commits", None):
            def on_toggle_save(success: bool):
                if not success and getattr(self, "pending_commits", None):
                    self.notify_status("Pending commits failed. Reverting to BATCH mode.", level="warning")
                    self.auto_save = False

            self.action_save_batch(on_complete=on_toggle_save)

    def _update_footer_legend(self) -> None:
        if not getattr(self, "is_mounted", False):
            return

        try:
            legend = self.query_one("#footer-legend", ModeButton)
            legend.update_mode()
        except Exception:
            pass

    @property
    def current_option_list(self) -> ConfigOptionList | None:
        try:
            switcher = self.query_one(ContentSwitcher)
            if switcher.current:
                idx = switcher.current.split("-")[1]
                return self.query_one(f"#list-{idx}", ConfigOptionList)
        except Exception:
            pass

        return None

    def check_tab_overflow(self) -> None:
        if not self._cached_tabs_container or not self._cached_tab_left or not self._cached_tab_right:
            return

        try:
            container = self._cached_tabs_container
            left = self._cached_tab_left
            right = self._cached_tab_right

            has_overflow = container.max_scroll_x > 0

            if has_overflow:
                if container.styles.align != ("left", "middle"):
                    container.styles.align = ("left", "middle")

                left.display = container.scroll_x > 0.5
                right.display = container.scroll_x < (container.max_scroll_x - 0.5)

            else:
                if container.styles.align != ("center", "middle"):
                    container.styles.align = ("center", "middle")

                left.display = False
                right.display = False

        except Exception:
            pass

    # =========================================================================
    # WATCHERS
    # =========================================================================
    async def watch_target_file(self) -> None:
        try:
            changed_any = False

            for e_key, engine in self.engine_pool.items():
                if not engine.target_path:
                    continue

                path = Path(engine.target_path).expanduser().resolve()

                try:
                    stat_info = await asyncio.to_thread(path.stat)
                    current_mtime = stat_info.st_mtime
                except FileNotFoundError:
                    if self._initial_target_mtimes_set:
                        for t_idx, i_idx, item in self._items_by_engine.get(e_key, []):
                            if item.type_ in ("action", "preset", "menu"):
                                continue

                            if item.exists_in_target:
                                item.exists_in_target = False
                                self._bump_write_generation(self._get_item_uid(item))
                                changed_any = True
                    continue
                except OSError:
                    continue

                if not self._initial_target_mtimes_set:
                    self.last_target_mtimes[e_key] = current_mtime
                    continue

                if current_mtime > self.last_target_mtimes.get(e_key, 0.0):
                    self.last_target_mtimes[e_key] = current_mtime

                    try:
                        new_state = await asyncio.to_thread(engine.load_state)
                    except Exception:
                        continue

                    for t_idx, i_idx, item in self._items_by_engine.get(e_key, []):
                        if not self.auto_save and (t_idx, i_idx) in self.pending_commits:
                            continue

                        if item.type_ in ("action", "preset", "menu"):
                            continue

                        raw = self._lookup_state(new_state, item)

                        if raw is not None:
                            new_val = item.deserialize(raw)

                            if str(item.value) != str(new_val):
                                item.value = new_val
                                item.exists_in_target = True
                                self._bump_write_generation(self._get_item_uid(item))
                                changed_any = True

                        else:
                            expected_exists = (item.default != "nil")
                            expected_val = item.default if expected_exists else item.value

                            if item.exists_in_target != expected_exists or str(item.value) != str(expected_val):
                                item.exists_in_target = expected_exists

                                if expected_exists:
                                    item.value = expected_val

                                self._bump_write_generation(self._get_item_uid(item))
                                changed_any = True

            if not self._initial_target_mtimes_set:
                self._initial_target_mtimes_set = True
                return

            if changed_any:
                self._schema_dirty_counter += 1
                self._refresh_all_ui()
                self.notify_status("Config modified externally. Refreshed UI.")

        except Exception:
            pass

    async def update_telemetry(self) -> None:
        if self.telemetry_engine:
            try:
                msg = await asyncio.to_thread(self.telemetry_engine.get_telemetry)
                banner = self.query_one("#telemetry-banner", Label)
                banner.update(msg)
            except Exception:
                pass

    async def watch_presets_dir(self) -> None:
        if (
            not self.enable_user_presets
            or not hasattr(self, "user_presets_dir")
            or not self.user_presets_dir.exists()
        ):
            return

        try:
            if not hasattr(self, "_preset_mtimes"):
                self._preset_mtimes = {}

            def check_mtimes():
                return {f.name: f.stat().st_mtime for f in self.user_presets_dir.glob("*.json")}

            current_mtimes = await asyncio.to_thread(check_mtimes)
            changed_any = False

            for fname, mtime in current_mtimes.items():
                if self._preset_mtimes.get(fname, 0.0) < mtime:
                    changed_any = True
                    break

            if set(self._preset_mtimes.keys()) - set(current_mtimes.keys()):
                changed_any = True

            if not getattr(self, "_initial_presets_mtime_set", False):
                self._preset_mtimes = current_mtimes
                self._initial_presets_mtime_set = True
                return

            if changed_any:
                self._schema_dirty_counter += 1
                self._preset_mtimes = current_mtimes
                self._load_user_presets()
                self._rebuild_indexes()
                self._refresh_all_ui()

        except Exception:
            pass

    async def watch_theme_file(self) -> None:
        if not self.theme_path:
            return

        try:
            stat_info = await asyncio.to_thread(self.theme_path.stat)
            current_mtime = stat_info.st_mtime

            if current_mtime > self.last_theme_mtime:
                new_theme = await asyncio.to_thread(load_matugen_json, self.theme_path)

                if new_theme is not None:
                    self._schema_dirty_counter += 1
                    self.last_theme_mtime = current_mtime

                    self.theme_colors.update(new_theme)
                    self.apply_theme_to_engine()

                    self._refresh_all_ui()

                    for shortcut in self.query(Shortcut):
                        shortcut.refresh()

                    for file_link in self.query(FileLink):
                        file_link.refresh()

                    self._update_footer_legend()

        except Exception:
            pass

    def apply_theme_to_engine(self) -> None:
        self._theme_toggle = not getattr(self, "_theme_toggle", False)
        theme_name = "dusky_matugen_A" if self._theme_toggle else "dusky_matugen_B"

        bg = self.theme_colors.get("background", self.theme_colors.get("bg", "#111318"))
        fg = self.theme_colors.get("on_background", self.theme_colors.get("fg", "#e1e2e9"))
        accent = self.theme_colors.get("primary", self.theme_colors.get("accent", "#a8c8ff"))
        muted = self.theme_colors.get("surface_variant", self.theme_colors.get("muted", "#43474e"))
        err = self.theme_colors.get("error", self.theme_colors.get("error", "#ffb4ab"))
        warn = self.theme_colors.get("tertiary", self.theme_colors.get("warning", "#bdc7dc"))
        succ = self.theme_colors.get("secondary", self.theme_colors.get("success", "#dbbce1"))
        info = self.theme_colors.get("info", accent)

        self.theme_colors["bg"] = bg
        self.theme_colors["fg"] = fg
        self.theme_colors["accent"] = accent
        self.theme_colors["muted"] = muted
        self.theme_colors["error"] = err
        self.theme_colors["warning"] = warn
        self.theme_colors["success"] = succ
        self.theme_colors["info"] = info

        custom_theme = Theme(
            name=theme_name,
            primary=accent,
            secondary=muted,
            background=bg,
            surface=bg,
            warning=warn,
            error=err,
            success=succ,
            variables={"foreground": fg},
        )

        self.register_theme(custom_theme)
        self.theme = theme_name

    # =========================================================================
    # TAB HANDLING
    # =========================================================================
    @on(Tabs.TabActivated)
    def handle_tab_activated(self, event: Tabs.TabActivated) -> None:
        try:
            idx = int(event.tab.id.split("-")[-1])
            self.query_one(ContentSwitcher).current = f"tab-{idx}"
            event.tab.scroll_visible(animate=True, top=False)

            if idx not in self._tab_data_ready and self._engines_for_tab(idx).issubset(self._loaded_engines):
                self._apply_states_to_tab(idx, self._states)

            if idx not in self._populated_tabs or idx in self._tab_dirty:
                self._populate_option_list(idx)
                self._populated_tabs.add(idx)
                self._tab_dirty.discard(idx)

            if ol := self.current_option_list:
                ol.focus()

                if ol.highlighted is None and ol.option_count > 0:
                    for i in range(ol.option_count):
                        opt = ol.get_option_at_index(i)
                        if not getattr(opt, "disabled", False):
                            ol.highlighted = i
                            break

                self._update_pagination(ol)
            else:
                try:
                    for cw in self.query(CustomRichTabWidget):
                        if cw.display:
                            cw.focus()
                            break
                except Exception:
                    pass

                self._update_pagination(None)

            self._update_scroll_indicators()
            self.check_tab_overflow()

        except Exception:
            pass

    @on(events.Click, "#tab-left")
    def scroll_tabs_left(self, event: events.Click) -> None:
        event.stop()
        if self._cached_tabs_container:
            self._cached_tabs_container.scroll_relative(x=-40, animate=True)

    @on(events.Click, "#tab-right")
    def scroll_tabs_right(self, event: events.Click) -> None:
        event.stop()
        if self._cached_tabs_container:
            self._cached_tabs_container.scroll_relative(x=40, animate=True)

    # =========================================================================
    # SHORTCUT VISUALS
    # =========================================================================
    def trigger_shortcut_blink(self, key_id: str) -> None:
        try:
            self.query_one(f"#shortcut-{key_id}", Shortcut).blink()
        except Exception:
            pass

    def toggle_shortcut_active(self, key_id: str, active: bool) -> None:
        try:
            sc = self.query_one(f"#shortcut-{key_id}", Shortcut)
            if active:
                sc.add_class("-active")
            else:
                sc.remove_class("-active")
            sc.refresh()
        except Exception:
            pass

    # =========================================================================
    # ITEM LOOKUP / HELP
    # =========================================================================
    def _get_item_from_id(self, opt_id: str) -> tuple[int, int, ConfigItem] | None:
        if not opt_id or not opt_id.startswith("item_"):
            return None

        try:
            _, t_idx, i_idx = opt_id.split("_")
            tab_idx, item_idx = int(t_idx), int(i_idx)
            return tab_idx, item_idx, self.schema[tab_idx][item_idx]
        except (ValueError, KeyError, IndexError):
            return None

    def _update_help_panel(self, item: ConfigItem) -> None:
        try:
            content_area = self.query_one("#content-area")

            if content_area.has_class("-show-help"):
                md = self.query_one("#help-markdown", Markdown)
                help_text = ""

                if item.warning_msg:
                    help_text += f"> **⚠️ WARNING:** {item.warning_msg}\n"

                help_text += item.extended_help or f"**{item.label}**\nNo extended documentation available."

                md.update(help_text)

        except Exception:
            pass

    @on(OptionList.OptionHighlighted)
    def handle_option_highlight(self, event: OptionList.OptionHighlighted) -> None:
        ol = event.option_list

        if not isinstance(ol, ConfigOptionList) or not event.option_id:
            return

        parsed = self._get_item_from_id(event.option_id)

        if parsed:
            self._update_help_panel(parsed[2])

            try:
                engine = self._get_engine_for_item(parsed[2])
                self.query_one("#file-link", FileLink).path = engine.target_path
            except Exception:
                pass

        last_id = ol.last_highlighted_id

        if last_id and last_id != event.option_id:
            old_parsed = self._get_item_from_id(last_id)

            if old_parsed:
                try:
                    old_idx = ol.get_option_index(last_id)
                    old_prefix = self._indent_cache.get(last_id, "")
                    ol.replace_option_prompt_at_index(
                        old_idx,
                        self._build_option(old_parsed[2], False, old_prefix)
                    )
                except OptionDoesNotExist:
                    pass

        if parsed:
            try:
                curr_idx = ol.get_option_index(event.option_id)
                curr_prefix = self._indent_cache.get(event.option_id, "")

                ol.replace_option_prompt_at_index(
                    curr_idx,
                    self._build_option(parsed[2], True, curr_prefix)
                )

                ol.last_highlighted_id = event.option_id

            except OptionDoesNotExist:
                pass

        self._update_pagination(ol)

    def _update_pagination(self, ol: ConfigOptionList) -> None:
        try:
            counter = self.query_one("#pos-counter", Label)
            if ol and ol.option_count > 0:
                curr_idx = ol.highlighted if ol.highlighted is not None else 0
                total_selectable = 0
                selectable_idx = 0

                for i in range(ol.option_count):
                    opt = ol.get_option_at_index(i)
                    if not getattr(opt, "disabled", False):
                        total_selectable += 1
                        if i <= curr_idx:
                            selectable_idx += 1

                if total_selectable > 0 and selectable_idx > 0:
                    txt = Text()
                    txt.append(" │ ", style=self.theme_colors.get("fg", ""))
                    txt.append(f"{selectable_idx}/{total_selectable}", style=self.theme_colors.get("accent", "") + " bold")
                    counter.update(txt)
                    counter.display = True
                else:
                    counter.display = False
            else:
                counter.display = False
        except Exception:
            pass

    def _update_scroll_indicators(self) -> None:
        try:
            switcher = self.query_one(ContentSwitcher)
            if not switcher.current:
                return

            tab_idx = int(switcher.current.split("-")[1])
            ol = self.query_one(f"#list-{tab_idx}", ConfigOptionList)
            indicator = self.query_one(f"#indicator-{tab_idx}", ScrollIndicator)

            if ol.max_scroll_y > 0 and ol.size.height > 2:
                indicator.update_scroll(
                    ol.scroll_y,
                    ol.max_scroll_y,
                    ol.size.height,
                    ol.virtual_size.height
                )
            else:
                indicator.display = False

        except Exception:
            pass

    # =========================================================================
    # STATUS / SOUND
    # =========================================================================
    def notify_status(self, msg: str, level: str = "info") -> None:
        try:
            app_footer = self.query_one(AppFooter)
            app_footer.status_msg = msg
            app_footer.status_level = level

            if self._status_timer:
                self._status_timer.stop()

            self._status_timer = self.set_timer(3, lambda: setattr(app_footer, "status_msg", ""))
        except Exception:
            pass

    def play_reset_sound(self) -> None:
        global _AUDIO_PLAYER_CACHE

        sound_path = "/usr/share/sounds/freedesktop/stereo/dialog-information.oga"

        if Path(sound_path).exists():
            if _AUDIO_PLAYER_CACHE is None:
                _AUDIO_PLAYER_CACHE = (
                    shutil.which("pw-play")
                    or shutil.which("paplay")
                    or shutil.which("mpv")
                    or ""
                )

            player = _AUDIO_PLAYER_CACHE

            if player:
                cmd = [player, sound_path]
                if player.endswith("mpv"):
                    cmd.extend(["--no-video", "--really-quiet"])

                subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )

    # =========================================================================
    # WRITE GENERATION / AUTOSAVE SAFETY
    # =========================================================================
    def _bump_write_generation(self, uid: str) -> int:
        gen = self._write_generation.get(uid, 0) + 1
        self._write_generation[uid] = gen
        return gen

    def _cancel_autosave_ref(self, tab_idx: int, item_idx: int) -> None:
        k = (tab_idx, item_idx)

        timer = self._save_timers.pop(k, None)
        if timer:
            timer.stop()

        self._pending_autosave_args.pop(k, None)

    def _cancel_autosave_for_transaction(self, transaction: list[tuple[int, int, Any, Any]]) -> None:
        uids: set[str] = set()

        for t, i, o, n in transaction:
            self._cancel_autosave_ref(t, i)

            item = self._get_schema_item(t, i)
            if item:
                uids.add(self._get_item_uid(item))

        # Also cancel duplicate UID views.
        for uid in uids:
            for t, i, itm in self._items_by_uid.get(uid, []):
                self._cancel_autosave_ref(t, i)

        for uid in uids:
            self._bump_write_generation(uid)

    def _apply_transaction_to_ram(self, transaction: list[tuple[int, int, Any, Any]], undo: bool = False) -> None:
        uids: set[str] = set()

        for t, i, o, n in transaction:
            item = self._get_schema_item(t, i)
            if not item:
                continue

            item.value = o if undo else n
            item.exists_in_target = True
            self._refresh_single_ui(t, i, item)
            uids.add(self._get_item_uid(item))

        self._schema_dirty_counter += 1

        for uid in uids:
            self._bump_write_generation(uid)

    def _revert_transaction(self, transaction: list[tuple[int, int, Any, Any]]) -> None:
        self._apply_transaction_to_ram(transaction, undo=True)

        if self.undo_stack and list(self.undo_stack[-1]) == list(transaction):
            self.undo_stack.pop()

        self._refresh_presets_ui()

    def _reset_trigger_ui(self, item: ConfigItem) -> None:
        uid = self._get_item_uid(item)
        self._schema_dirty_counter += 1

        for t_idx, i_idx, other_item in self._items_by_uid.get(uid, []):
            other_item.value = item.default
            self._refresh_single_ui(t_idx, i_idx, other_item)

        self._bump_write_generation(uid)
        self._refresh_presets_ui()

    # =========================================================================
    # TRANSACTION APPLICATION
    # =========================================================================
    def _apply_transaction(
        self,
        transaction: list[tuple[int, int, Any, Any]],
        action_type: str = "new",
        success_msg: str = ""
    ) -> None:
        self._schema_dirty_counter += 1
        self._cancel_autosave_for_transaction(transaction)

        for t, i, o, n in transaction:
            item = self.schema[t][i]
            item.value = o if action_type == "undo" else n
            item.exists_in_target = True
            self.pending_commits.add((t, i))
            self._refresh_single_ui(t, i, item)

        uids: set[str] = set()
        for t, i, o, n in transaction:
            item = self._get_schema_item(t, i)
            if item:
                uids.add(self._get_item_uid(item))

        for uid in uids:
            self._bump_write_generation(uid)

        if self.auto_save:
            def finalize_transaction(batch_success: bool):
                successful_parts = []
                failed_parts = []

                for t, i, o, n in transaction:
                    if (t, i) in self.pending_commits:
                        failed_parts.append((t, i, o, n))

                        item = self.schema[t][i]
                        item.value = n if action_type == "undo" else o
                        self._refresh_single_ui(t, i, item)
                        self.pending_commits.discard((t, i))
                    else:
                        successful_parts.append((t, i, o, n))

                if not failed_parts and success_msg:
                    self.notify_status(success_msg, level="success")

                if successful_parts:
                    if action_type == "undo":
                        self.redo_stack.append(successful_parts)
                    elif action_type == "redo":
                        self.undo_stack.append(successful_parts)
                    elif action_type == "new":
                        self.undo_stack.append(successful_parts)
                        self.redo_stack.clear()

                if failed_parts:
                    if action_type == "undo":
                        self.undo_stack.append(failed_parts)
                    elif action_type == "redo":
                        self.redo_stack.append(failed_parts)

                if getattr(self, "_preset_refresh_timer", None) is not None:
                    self._preset_refresh_timer.stop()
                    self._preset_refresh_timer = None

                self._refresh_presets_ui()

            self.action_save_batch(on_complete=finalize_transaction)

        else:
            self._update_footer_legend()

            if action_type == "undo":
                self.redo_stack.append(transaction)
            elif action_type == "redo":
                self.undo_stack.append(transaction)
            elif action_type == "new":
                self.undo_stack.append(transaction)
                self.redo_stack.clear()

            if success_msg:
                self.notify_status(success_msg, level="success")

            if getattr(self, "_preset_refresh_timer", None) is not None:
                self._preset_refresh_timer.stop()
                self._preset_refresh_timer = None

            self._refresh_presets_ui()

    def _safe_apply_value(
        self,
        tab_idx: int,
        item_idx: int,
        item: ConfigItem,
        new_val: Any,
        is_undo: bool = False,
        batch_mode: bool = False,
        record_undo: bool = True
    ) -> None:
        if item.confirm_message and not is_undo and not batch_mode:
            def on_confirm(confirmed: bool) -> None:
                if confirmed:
                    self._apply_value(tab_idx, item_idx, item, new_val, is_undo, batch_mode, record_undo)

            self.push_screen(
                ConfirmDialog(
                    item.confirm_message,
                    title=f"Confirm Change: {item.label}",
                    level="warning"
                ),
                on_confirm
            )
        else:
            self._apply_value(tab_idx, item_idx, item, new_val, is_undo, batch_mode, record_undo)

    def _apply_value(
        self,
        tab_idx: int,
        item_idx: int,
        item: ConfigItem,
        new_val: Any,
        is_undo: bool = False,
        batch_mode: bool = False,
        record_undo: bool = True
    ) -> bool:
        old_val = item.value
        self._schema_dirty_counter += 1

        item_uid = self._get_item_uid(item)
        transaction = [(tab_idx, item_idx, old_val, new_val)]

        for t_idx, i_idx, other in self._items_by_uid.get(item_uid, []):
            if other is not item:
                transaction.append((t_idx, i_idx, other.value, new_val))

        if not is_undo and record_undo:
            self.undo_stack.append(transaction)
            self.redo_stack.clear()

        # Cancel stale autosaves before mutating.
        self._cancel_autosave_for_transaction(transaction)

        item.value = new_val
        item.exists_in_target = True

        # Sync duplicate items across tabs.
        for t_idx, i_idx, other_item in self._items_by_uid.get(item_uid, []):
            if other_item is not item:
                other_item.value = new_val
                other_item.exists_in_target = True
                self._refresh_single_ui(t_idx, i_idx, other_item)

        val_str = item.serialize(new_val)

        if self.auto_save and not batch_mode:
            k = (tab_idx, item_idx)
            gen = self._bump_write_generation(item_uid)

            self._save_timers[k] = self.set_timer(
                0.25,
                lambda ti=tab_idx, ii=item_idx, it=item, vs=val_str, ov=old_val, g=gen, tx=transaction:
                    asyncio.create_task(self._do_auto_save_async(ti, ii, it, vs, ov, g, tx, False))
            )

            self._pending_autosave_args[k] = (item, val_str, old_val)

        else:
            self.pending_commits.add((tab_idx, item_idx))

        if not batch_mode:
            self._update_footer_legend()

        self._refresh_single_ui(tab_idx, item_idx, item)

        if not batch_mode:
            if getattr(self, "_preset_refresh_timer", None) is not None:
                self._preset_refresh_timer.stop()

            self._preset_refresh_timer = self.set_timer(0.15, self._refresh_presets_ui)

        if item.popup_message and not is_undo and not batch_mode:
            self.push_screen(
                AlertDialog(
                    item.popup_message,
                    title=f"Notice: {item.label}",
                    level="info"
                )
            )

        return True

    # =========================================================================
    # ASYNC AUTO SAVE
    # =========================================================================
    async def _do_auto_save_async(
        self,
        tab_idx: int,
        item_idx: int,
        item: ConfigItem,
        val_str: str,
        old_val: Any,
        generation: int,
        transaction: list[tuple[int, int, Any, Any]],
        force: bool = False
    ) -> None:
        self._save_timers.pop((tab_idx, item_idx), None)
        self._pending_autosave_args.pop((tab_idx, item_idx), None)

        uid = self._get_item_uid(item)

        if force:
            self._apply_transaction_to_ram(transaction, undo=False)
        else:
            if self._write_generation.get(uid) != generation:
                return

            if item.serialize(item.value) != val_str:
                return

        if self._save_lock is None:
            self._save_lock = asyncio.Lock()

        try:
            engine = self._get_engine_for_item(item)
        except Exception as e:
            self.notify_status(f"Engine Error: {e}", level="error")
            self._revert_transaction(transaction)
            return

        async with self._save_lock:
            try:
                success, msg, _ = await asyncio.to_thread(
                    engine.write_value,
                    item.key,
                    item.scope,
                    val_str,
                    item_type=item.type_
                )
            except Exception as e:
                success, msg = False, f"Engine Error: {e}"

        if success:
            try:
                ekey = self._get_item_engine_info(item)
                self.last_target_mtimes[ekey] = Path(engine.target_path).expanduser().resolve().stat().st_mtime
            except OSError:
                pass

            if is_trigger_item(item):
                def reset_trigger():
                    self._reset_trigger_ui(item)

                self.set_timer(0.15, reset_trigger)

            self.notify_status(f"Updated {item.label}", level="success")
            return

        if "AUTH_REQUIRED" in msg:
            if isinstance(self.screen, PasswordScreen):
                self.notify_status("Another authorization is already in progress.", level="warning")
                self._revert_transaction(transaction)
                return

            self._revert_transaction(transaction)

            def on_pwd(pwd: str | None) -> None:
                asyncio.create_task(self._on_auto_password(pwd, tab_idx, item_idx, item, val_str, old_val, generation, transaction))

            self.push_screen(PasswordScreen(), on_pwd)
            return

        self.notify_status(f"Error: {msg}", level="error")
        self._revert_transaction(transaction)
        self.play_reset_sound()

    async def _on_auto_password(
        self,
        pwd: str | None,
        tab_idx: int,
        item_idx: int,
        item: ConfigItem,
        val_str: str,
        old_val: Any,
        generation: int,
        transaction: list[tuple[int, int, Any, Any]]
    ) -> None:
        if pwd:
            auth_res = await asyncio.to_thread(
                subprocess.run,
                ["sudo", "-S", "-v"],
                input=(pwd + "\n").encode(),
                capture_output=True
            )

            if auth_res.returncode == 0:
                self.notify_status("Sudo authenticated. Retrying...", level="info")
                self._start_sudo_keepalive()

                await self._do_auto_save_async(
                    tab_idx,
                    item_idx,
                    item,
                    val_str,
                    old_val,
                    generation,
                    transaction,
                    True
                )
            else:
                self.notify_status("Incorrect sudo password.", level="error")
                self.play_reset_sound()
        else:
            self.notify_status("Sudo authentication cancelled.", level="warning")

    def _start_sudo_keepalive(self) -> None:
        if self._sudo_keepalive is None:
            self._sudo_keepalive = self.set_interval(60.0, self._sudo_keepalive_tick)

    def _sudo_keepalive_tick(self) -> None:
        asyncio.create_task(self._sudo_keepalive_async())

    async def _sudo_keepalive_async(self) -> None:
        try:
            await asyncio.to_thread(
                subprocess.run,
                ["sudo", "-n", "-v"],
                capture_output=True
            )
        except Exception:
            pass

    # =========================================================================
    # ASYNC BATCH SAVE
    # =========================================================================
    def action_save_batch(self, on_complete=None) -> bool:
        if self._modal_active():
            if on_complete:
                on_complete(False)
            return False

        self.trigger_shortcut_blink("ctrl-s")

        if not self.pending_commits:
            self.notify_status("No pending changes.", level="info")
            if on_complete:
                on_complete(True)
            return True

        if self._save_lock is None:
            self._save_lock = asyncio.Lock()

        asyncio.create_task(self._save_batch_async(on_complete))
        return True

    async def _save_batch_async(self, on_complete=None) -> None:
        if self._save_lock is None:
            self._save_lock = asyncio.Lock()

        async with self._save_lock:
            if self._modal_active():
                if on_complete:
                    on_complete(False)
                return

            if not self.pending_commits:
                self.notify_status("No pending changes.", level="info")
                if on_complete:
                    on_complete(True)
                return

            # Frozen snapshot: (change_tuple, commit_key, val_str, frozen_val, ConfigItem)
            type FrozenItem = tuple[tuple[str, str, str, str], tuple[int, int], str, Any, ConfigItem]
            batches: dict[tuple[str, str], list[FrozenItem]] = {}

            for tab_idx, item_idx in tuple(self.pending_commits):
                item = self.schema[tab_idx][item_idx]
                key = (tab_idx, item_idx)
                frozen_val = clone_value(item.value)
                val_str = item.serialize(frozen_val)
                ekey = self._get_item_engine_info(item)
                change = (item.key, item.scope, val_str, str(item.type_))
                batches.setdefault(ekey, []).append((change, key, val_str, frozen_val, item))

            final_success = True
            success_count = 0
            error_msgs = []
            auth_required = False

            def mark_success(key: tuple[int, int], frozen_val_str: str, frozen_val: Any, itm: ConfigItem) -> bool:
                current_str = itm.serialize(itm.value)
                if current_str != frozen_val_str:
                    self._save_queued_during_run = True
                    return False
                self.pending_commits.discard(key)
                self._committed[key] = clone_value(frozen_val)
                return True

            for ekey, batch in batches.items():
                engine = self.engine_pool[ekey]
                changes = [b[0] for b in batch]

                try:
                    success, msg, _ = await asyncio.to_thread(engine.write_batch, changes)
                except Exception as e:
                    success, msg = False, f"Engine Error: {e}"

                if success:
                    for _change, key, frozen_str, frozen_val, itm in batch:
                        if mark_success(key, frozen_str, frozen_val, itm):
                            success_count += 1
                            if is_trigger_item(itm):
                                self._reset_trigger_ui(itm)

                    try:
                        self.last_target_mtimes[ekey] = Path(engine.target_path).expanduser().resolve().stat().st_mtime
                    except OSError:
                        pass
                else:
                    if "AUTH_REQUIRED" in msg:
                        auth_required = True
                        break

                    engine_success_count = 0

                    for change, key, frozen_str, frozen_val, itm in batch:
                        key_s, scope, val_str, itype = change

                        try:
                            ok, item_msg, _ = await asyncio.to_thread(
                                engine.write_value,
                                key_s,
                                scope,
                                val_str,
                                item_type=itype
                            )
                        except Exception as e:
                            ok, item_msg = False, f"Engine Error: {e}"

                        if ok:
                            if mark_success(key, frozen_str, frozen_val, itm):
                                success_count += 1
                                engine_success_count += 1
                                if is_trigger_item(itm):
                                    self._reset_trigger_ui(itm)

                            try:
                                self.last_target_mtimes[ekey] = Path(engine.target_path).expanduser().resolve().stat().st_mtime
                            except OSError:
                                pass
                        else:
                            if "AUTH_REQUIRED" in item_msg:
                                auth_required = True
                                break
                            error_msgs.append(item_msg)
                            if is_trigger_item(itm):
                                self.pending_commits.discard(key)
                                self._reset_trigger_ui(itm)

                    if auth_required:
                        break

                    if engine_success_count != len(batch):
                        final_success = False

        if self._save_queued_during_run:
            self._save_queued_during_run = False
            if self.pending_commits:
                asyncio.create_task(self._save_batch_async(on_complete))
                return

        if auth_required:
            if isinstance(self.screen, PasswordScreen):
                self.notify_status("Another authorization is already in progress.", level="warning")
                if on_complete:
                    on_complete(False)
                return

            def on_pwd_batch(pwd: str | None) -> None:
                asyncio.create_task(self._on_batch_password(pwd, on_complete))

            self.push_screen(PasswordScreen(), on_pwd_batch)
            return

        if final_success:
            self.notify_status(f"Batched {success_count} commits successfully.", level="success")
            self.play_reset_sound()
        elif success_count > 0:
            first_err = error_msgs[0] if error_msgs else "Unknown Engine Error"
            self.notify_status(f"Partial success ({success_count} applied). Error: {first_err}", level="warning")
            self.play_reset_sound()
        else:
            first_err = error_msgs[0] if error_msgs else "Unknown Engine Error"
            self.notify_status(f"Batch Error: {first_err}", level="error")

        self._refresh_all_ui()
        self._update_footer_legend()
        self._refresh_presets_ui()

        if on_complete:
            on_complete(final_success)

    async def _on_batch_password(self, pwd: str | None, on_complete=None) -> None:
        if pwd:
            try:
                auth_res = await asyncio.to_thread(
                    subprocess.run,
                    ["sudo", "-S", "-v"],
                    input=(pwd + "\n").encode(),
                    capture_output=True,
                    timeout=30,
                    check=False,
                    env={**os.environ, "LC_ALL": "C"}
                )
            except subprocess.TimeoutExpired:
                self.notify_status("Sudo authentication timed out.", level="error")
                if on_complete:
                    on_complete(False)
                return
            finally:
                pwd = None

            if auth_res.returncode == 0:
                self.notify_status("Sudo authenticated. Retrying batch...", level="info")
                self._start_sudo_keepalive()
                self.action_save_batch(on_complete=on_complete)
            else:
                self.notify_status("Incorrect sudo password. Batch aborted.", level="error")
                if on_complete:
                    on_complete(False)
        else:
            self.notify_status("Sudo authentication cancelled.", level="warning")
            if on_complete:
                on_complete(False)

    # =========================================================================
    # GLOBAL ACTIONS
    # =========================================================================
    def action_show_diff(self) -> None:
        if isinstance(self.screen, DiffScreen):
            self.screen.dismiss(None)
            return

        if self._modal_active():
            return

        self.toggle_shortcut_active("d", True)
        self.push_screen(DiffScreen(), lambda _: self.toggle_shortcut_active("d", False))

    def action_show_shortcuts(self) -> None:
        if isinstance(self.screen, ShortcutsInfoScreen):
            self.screen.dismiss(None)
            return

        if self._modal_active():
            return

        self.toggle_shortcut_active("f1", True)
        self.push_screen(ShortcutsInfoScreen(), lambda _: self.toggle_shortcut_active("f1", False))

    def action_undo(self) -> None:
        if self._modal_active():
            return

        if not self.undo_stack:
            self.notify_status("Nothing to undo.", level="warning")
            return

        transaction = self.undo_stack.pop()

        if self.auto_save:
            msg = (
                f"Undid batch of {len(transaction)} changes."
                if len(transaction) > 1
                else f"Undid change to {self.schema[transaction[0][0]][transaction[0][1]].label}"
            )
        else:
            msg = (
                f"Queued undo of {len(transaction)} changes."
                if len(transaction) > 1
                else f"Queued undo for {self.schema[transaction[0][0]][transaction[0][1]].label}"
            )

        self._apply_transaction(transaction, action_type="undo", success_msg=msg)

    def action_redo(self) -> None:
        if self._modal_active():
            return

        if not self.redo_stack:
            self.notify_status("Nothing to redo.", level="warning")
            return

        transaction = self.redo_stack.pop()

        if self.auto_save:
            msg = (
                f"Redid batch of {len(transaction)} changes."
                if len(transaction) > 1
                else f"Redid change to {self.schema[transaction[0][0]][transaction[0][1]].label}"
            )
        else:
            msg = (
                f"Queued redo of {len(transaction)} changes."
                if len(transaction) > 1
                else f"Queued redo for {self.schema[transaction[0][0]][transaction[0][1]].label}"
            )

        self._apply_transaction(transaction, action_type="redo", success_msg=msg)

    def action_toggle_help(self) -> None:
        content_area = self.query_one("#content-area")
        content_area.toggle_class("-show-help")
        self.toggle_shortcut_active("help", content_area.has_class("-show-help"))

        if content_area.has_class("-show-help"):
            ol = self.current_option_list

            if ol and ol.last_highlighted_id:
                parsed = self._get_item_from_id(ol.last_highlighted_id)
                if parsed:
                    self._update_help_panel(parsed[2])

    def action_focus_local_search(self) -> None:
        if self._modal_active():
            return

        if isinstance(self.focused, Input):
            return

        inp = self.query_one("#local-search", Input)
        inp.add_class("-active")
        inp.value = ""

        self.toggle_shortcut_active("slash", True)
        self.call_after_refresh(inp.focus)

    def action_clear_local_search(self) -> None:
        inp = self.query_one("#local-search", Input)

        if inp.has_class("-active"):
            inp.remove_class("-active")
            self.toggle_shortcut_active("slash", False)

            if ol := self.current_option_list:
                self.call_after_refresh(ol.focus)

    @on(Input.Changed, "#local-search")
    def handle_local_search(self, event: Input.Changed) -> None:
        query = event.value.lower().replace(" ", "")
        if not query:
            return

        ol = self.current_option_list
        if not ol:
            return

        try:
            tab_idx = int(ol.id.split("-")[1])
            items = self.schema.get(tab_idx, [])

            for item_idx, item in enumerate(items):
                if query in item.label.lower().replace(" ", ""):
                    opt_id = f"item_{tab_idx}_{item_idx}"

                    pref = item.parent_ref
                    if pref:
                        current_pref = pref
                        expanded_any = False
                        seen_prefs = set()

                        while current_pref and current_pref not in seen_prefs:
                            seen_prefs.add(current_pref)

                            for p_item in items:
                                if self._get_item_uid(p_item) == current_pref and p_item.is_parent:
                                    if not p_item.expanded:
                                        p_item.expanded = True
                                        expanded_any = True

                                    current_pref = p_item.parent_ref
                                    break
                            else:
                                current_pref = None

                        if expanded_any:
                            self._populate_option_list(tab_idx, maintain_highlight_id=opt_id)

                    try:
                        idx = ol.get_option_index(opt_id)
                        ol.highlighted = idx

                        if hasattr(ol, "scroll_to_highlight"):
                            ol.scroll_to_highlight()

                        break

                    except OptionDoesNotExist:
                        pass

        except Exception:
            pass

    @on(Input.Submitted, "#local-search")
    def submit_local_search(self, event: Input.Submitted) -> None:
        self.action_clear_local_search()

    def action_search(self) -> None:
        if isinstance(self.screen, SearchScreen):
            self.screen.dismiss(None)
            return

        if self._modal_active():
            return

        self.toggle_shortcut_active("ctrl-f", True)

        def check_reply(result: tuple[int, int] | None) -> None:
            self.toggle_shortcut_active("ctrl-f", False)

            if result is not None:
                tab_idx, item_idx = result
                target_item = self.schema[tab_idx][item_idx]

                pref = target_item.parent_ref
                if pref:
                    current_pref = pref
                    seen_prefs = set()

                    while current_pref and current_pref not in seen_prefs:
                        seen_prefs.add(current_pref)

                        for p_item in self.schema[tab_idx]:
                            if self._get_item_uid(p_item) == current_pref and p_item.is_parent:
                                p_item.expanded = True
                                current_pref = p_item.parent_ref
                                break
                        else:
                            break

                self._populate_option_list(tab_idx, maintain_highlight_id=f"item_{tab_idx}_{item_idx}")
                self.action_switch_tab(tab_idx)

                def _focus_and_highlight():
                    try:
                        ol = self.query_one(f"#list-{tab_idx}", ConfigOptionList)
                        ol.focus()

                        idx = ol.get_option_index(f"item_{tab_idx}_{item_idx}")
                        ol.highlighted = idx

                        if hasattr(ol, "scroll_to_highlight"):
                            ol.scroll_to_highlight()

                    except Exception:
                        pass

                self.call_after_refresh(_focus_and_highlight)

        self.push_screen(SearchScreen(), check_reply)

    def action_next_tab(self) -> None:
        if self._modal_active():
            return

        self.query_one(Tabs).action_next_tab()

    def action_prev_tab(self) -> None:
        if self._modal_active():
            return

        self.query_one(Tabs).action_previous_tab()

    def action_switch_tab(self, index: int) -> None:
        if self._modal_active():
            return

        if 0 <= index < len(self.tabs):
            self.query_one(Tabs).active = f"tab-id-{index}"

    def action_toggle_save_mode(self) -> None:
        if self._modal_active():
            return
        self.auto_save = not self.auto_save

    # =========================================================================
    # ITEM ADJUSTMENT / RESET
    # =========================================================================
    def action_adjust(self, direction: int, bypass_lock: bool = False) -> None:
        ol = self.current_option_list
        if not ol or not ol.last_highlighted_id:
            return

        parsed = self._get_item_from_id(ol.last_highlighted_id)
        if not parsed:
            return

        tab_idx, item_idx, item = parsed

        if item.confirm_message and not bypass_lock:
            self.notify_status(
                f"Protected value: Press Enter to explicitly modify '{item.label}'.",
                level="warning"
            )
            return

        new_val = item.value

        if item.options and item.type_ != "bool":
            try:
                idx = item.options.index(item.value)
            except ValueError:
                idx = 0

            new_val = item.options[(idx + direction) % len(item.options)]

            if new_val != item.value:
                self._safe_apply_value(tab_idx, item_idx, item, new_val)

            return

        match item.type_:
            case "bool":
                new_val = not item.value

            case "int" | "float":
                step = item.step or 1
                new_val = item.value + (direction * step)

                if item.min_val is not None:
                    new_val = max(item.min_val, new_val)

                if item.max_val is not None:
                    new_val = min(item.max_val, new_val)

                new_val = round(new_val, 6) if item.type_ == "float" else int(new_val)

            case "cycle":
                return

            case "color":
                r, g, b = color_to_rgb(str(item.value))
                current_name = get_color_name(r, g, b)

                try:
                    idx = CYCLE_COLORS.index(current_name)
                except ValueError:
                    idx = 0

                next_name = CYCLE_COLORS[(idx + direction) % len(CYCLE_COLORS)]
                fmt = parse_color_format(str(item.value))
                new_val = format_rgb(next_name, fmt, str(item.value))

            case _:
                return

        if new_val != item.value:
            self._safe_apply_value(tab_idx, item_idx, item, new_val)

    def action_reset_item(self) -> None:
        self.trigger_shortcut_blink("r")

        ol = self.current_option_list
        if not ol or not ol.last_highlighted_id:
            return

        parsed = self._get_item_from_id(ol.last_highlighted_id)

        if parsed and str(parsed[2].value) != str(parsed[2].default):
            self._safe_apply_value(parsed[0], parsed[1], parsed[2], parsed[2].default)

    def action_reset_all(self) -> None:
        if self._modal_active():
            return

        self.trigger_shortcut_blink("R")

        try:
            tab_idx = 0
            try:
                switcher = self.query_one(ContentSwitcher)
                if switcher.current:
                    tab_idx = int(switcher.current.split("-")[1])
            except Exception:
                tab_idx = 0

            items = self.schema.get(tab_idx, [])
            configurable_items = [
                (idx, item) for idx, item in enumerate(items)
                if item.type_ not in ("action", "menu", "preset")
            ]

            has_changes = any(
                str(item.value) != str(item.default) or str(item.value) != str(item.initial_value)
                for _, item in configurable_items
            )

            tab_name = self.tabs.get(tab_idx, f"Tab {tab_idx}")

            if not has_changes:
                self.notify_status(f"All items in {tab_name} are already at default values.", level="info")
                return

            def on_confirm(confirmed: bool) -> None:
                if confirmed:
                    transaction = []
                    for item_idx, item in configurable_items:
                        if str(item.value) != str(item.default):
                            transaction.append((tab_idx, item_idx, item.value, item.default))

                    if transaction:
                        verb = "Reset" if self.auto_save else "Queued reset of"
                        msg = f"{verb} {len(transaction)} items in {tab_name}"
                        self._apply_transaction(transaction, action_type="new", success_msg=msg)
                        self._populate_option_list(tab_idx)
                    else:
                        self.notify_status(f"No items to reset in {tab_name}", level="info")

            self.push_screen(
                ConfirmDialog(
                    f"Are you sure you want to reset all items in **{tab_name}** to their factory defaults?",
                    title="Reset Page",
                    level="warning"
                ),
                on_confirm
            )

        except Exception as e:
            print(f"[DuskyTUI] Reset page error: {e}", file=sys.stderr)

    # =========================================================================
    # PRESET ACTIONS
    # =========================================================================
    def action_save_preset(self) -> None:
        if self._modal_active():
            return

        def check_reply(name: str | None) -> None:
            if not name:
                return

            name = re.sub(r'[\\/*?:"<>|]', "", name.strip())
            if not name:
                return

            payload = {}

            for t_idx, items in self.schema.items():
                for item in items:
                    if item.type_ in ("action", "preset", "menu"):
                        continue

                    payload[self._get_item_uid(item)] = item.value

            self.user_presets_dir.mkdir(parents=True, exist_ok=True)
            file_path = self.user_presets_dir / f"{name}.json"

            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=4)

                self.notify_status(f"Successfully saved preset: {name}", level="success")

                self._load_user_presets()
                self._rebuild_indexes()
                self._refresh_all_ui()

            except Exception as e:
                self.notify_status(f"Error saving preset: {e}", level="error")

        self.push_screen(HybridInputScreen("Save Current State as Preset (Name):", ""), check_reply)

    def action_import_preset(self) -> None:
        def check_reply(name: str | None) -> None:
            if not name:
                return

            name = re.sub(r'[\\/*?:"<>|]', "", name.strip())
            if not name:
                return

            self.user_presets_dir.mkdir(parents=True, exist_ok=True)
            file_path = self.user_presets_dir / f"{name}.json"

            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump({}, f, indent=4)

                self.notify_status(f"Created import template: {name}", level="success")

                self._load_user_presets()
                self._rebuild_indexes()
                self._refresh_all_ui()

                self.open_file_externally(file_path, button=1, touch_first=False)

            except Exception as e:
                self.notify_status(f"Error importing preset: {e}", level="error")

        self.push_screen(HybridInputScreen("Import Preset (Enter new name):", ""), check_reply)

    def action_delete_user_preset(self) -> None:
        if self._modal_active():
            return

        ol = self.current_option_list
        if not ol or not ol.last_highlighted_id:
            return

        parsed = self._get_item_from_id(ol.last_highlighted_id)
        if not parsed:
            return

        _, _, item = parsed

        if item.group == "User Presets" and item.type_ == "preset":
            name = item.label.replace("User: ", "", 1)
            file_path = self.user_presets_dir / f"{name}.json"

            if file_path.exists():
                def do_delete(confirmed: bool):
                    if confirmed:
                        try:
                            file_path.unlink()

                            self.notify_status(f"Deleted preset: {name}", level="success")

                            self._load_user_presets()
                            self._rebuild_indexes()
                            self._refresh_all_ui()

                        except Exception as e:
                            self.notify_status(f"Error deleting preset: {e}", level="error")

                safe_name = _md_escape(name)

                self.push_screen(
                    ConfirmDialog(
                        f"Are you sure you want to permanently delete the preset **{safe_name}**?",
                        title="Delete Preset",
                        level="danger"
                    ),
                    do_delete
                )

    # =========================================================================
    # ITEM ACTIVATION
    # =========================================================================
    def action_submit_current(self) -> None:
        ol = self.current_option_list

        if ol and ol.last_highlighted_id:
            ol._last_click_x = 0
            ol._mouse_down_highlight = None

            self._handle_item_action(
                ol,
                ol.last_highlighted_id,
                click_x=0,
                was_already_selected=True,
                button=1
            )

    @on(OptionList.OptionSelected)
    def handle_selection(self, event: OptionList.OptionSelected) -> None:
        ol = event.option_list

        if isinstance(ol, ConfigOptionList):
            click_x = getattr(ol, "_last_click_x", 0)
            button = getattr(ol, "_last_click_button", 1)
            was_already_selected = getattr(ol, "_mouse_down_highlight", None) == event.option_index

            self._handle_item_action(
                ol,
                event.option_id,
                click_x,
                was_already_selected,
                button
            )

            ol._last_click_x = 0
            ol._mouse_down_highlight = None

    def _handle_item_action(
        self,
        ol: ConfigOptionList,
        opt_id: str | None,
        click_x: int = 0,
        was_already_selected: bool = False,
        button: int = 1
    ) -> None:
        if not opt_id:
            return

        parsed = self._get_item_from_id(opt_id)
        if not parsed:
            return

        tab_idx, item_idx, item = parsed

        is_keyboard = (click_x == 0)
        instant_action = False

        indent_prefix = self._indent_cache.get(opt_id, "")
        prefix_len = cell_len(indent_prefix)

        if item.is_parent and (prefix_len <= click_x <= prefix_len + 9):
            instant_action = True

        trigger_bool = is_trigger_item(item)

        if (item.type_ in ("preset", "action") or trigger_bool) and click_x >= 44:
            instant_action = True

        if item.key in ("__save_new_preset", "__import_new_preset") and (1 <= click_x <= 17):
            instant_action = True

        if not is_keyboard and not instant_action and not was_already_selected:
            return

        # User preset external editing.
        if (
            not is_keyboard
            and not instant_action
            and item.type_ == "preset"
            and item.group == "User Presets"
            and item.key not in ("__save_new_preset", "__import_new_preset")
        ):
            name = item.label.replace("User: ", "", 1)
            path = self.user_presets_dir / f"{name}.json"

            if path.exists():
                target_btn = 1 if button == 0 else button
                self.open_file_externally(path, target_btn, touch_first=False)

            return

        if item.is_parent and instant_action:
            self.action_toggle_expand()
            return

        is_modified = str(item.value) != str(item.default)

        if is_modified and item.type_ not in ("action", "preset"):
            prefix = self._indent_cache.get(opt_id, "")
            rendered_text = self._build_option(item, True, indent_prefix=prefix)
            total_width = rendered_text.cell_len
            reset_width = 10
            threshold = total_width - reset_width

            if threshold <= click_x <= total_width + 2 and not is_keyboard:
                self.action_reset_item()
                return

        match item.type_:
            case "bool" | "cycle":
                self.action_adjust(1, bypass_lock=True)

            case "int" | "float" | "string" | "color":
                self.prompt_string(tab_idx, item_idx, item)

            case "action":
                self.execute_action(item)

            case "preset":
                self.apply_preset(item)

            case "picker":
                self.prompt_picker(tab_idx, item_idx, item)

            case "menu":
                self.action_toggle_expand()

    def action_toggle_expand(self) -> None:
        ol = self.current_option_list
        if not ol or not ol.last_highlighted_id:
            return

        parsed = self._get_item_from_id(ol.last_highlighted_id)
        if not parsed:
            return

        tab_idx, item_idx, item = parsed

        if item.is_parent:
            item.expanded = not item.expanded
            self._populate_option_list(tab_idx, maintain_highlight_id=ol.last_highlighted_id)

    # =========================================================================
    # ACTION EXECUTION / PRESET APPLICATION / PROMPTS
    # =========================================================================
    def _check_interactive(self, cmd_str: str) -> bool:
        if not cmd_str or not cmd_str.strip():
            return False

        interactive_apps = {
            "fzf", "nmtui",
            "vi", "vim", "nvim", "neovim", "nano", "micro", "helix", "hx", "emacs",
            "less", "more", "man",
            "top", "htop", "btop",
            "yazi", "ranger", "lf",
            "watch", "screen", "tmux",
            "tig", "gitui", "lazygit",
            "ipython", "bpython",
            "psql", "mysql",
        }
        wrappers = {
            "sudo", "doas", "pkexec",
            "env", "time", "nice", "nohup", "exec", "stdbuf", "xargs",
            "command", "builtin", "proxychains", "proxychains4",
        }
        shells = {"bash", "sh", "zsh", "fish", "dash", "ksh"}
        interpreters = {
            "python", "python2", "python3",
            "node", "ruby", "perl",
            "docker", "podman", "kubectl",
            "ssh", "script",
        }
        control_operators = {"|", "||", "&&", ";", "&"}
        wrapper_value_flags = {"-u", "--user", "-g", "--group", "-C"}

        def extract_subs(token: str) -> list[str]:
            out: list[str] = []
            i, n = 0, len(token)
            while i < n:
                if token.startswith("$(", i):
                    depth = 1
                    j = i + 2
                    while j < n and depth:
                        if token.startswith("$(", j):
                            depth += 1
                            j += 2
                            continue
                        if token[j] == ")":
                            depth -= 1
                            if depth == 0:
                                out.append(token[i + 2 : j])
                                i = j + 1
                                break
                        j += 1
                    else:
                        break
                    continue

                if token[i] == "`":
                    j = token.find("`", i + 1)
                    if j == -1:
                        break
                    out.append(token[i + 1 : j])
                    i = j + 1
                    continue

                if token.startswith("<(", i) or token.startswith(">(", i):
                    j = i + 2
                    depth = 1
                    while j < n and depth:
                        if token[j] == "(":
                            depth += 1
                        elif token[j] == ")":
                            depth -= 1
                            if depth == 0:
                                out.append(token[i + 2 : j])
                                i = j + 1
                                break
                        j += 1
                    else:
                        break
                    continue

                i += 1
            return out

        try:
            tokens = shlex.split(cmd_str)
        except Exception:
            tokens = re.findall(r"[A-Za-z0-9_./+-]+", cmd_str)

        expecting_executable = True
        skip_next = False

        for i, token in enumerate(tokens):
            if skip_next:
                skip_next = False
                continue

            for sub in extract_subs(token):
                if self._check_interactive(sub):
                    return True

            clean = token.strip("()$`\"'\t\n{}[]")

            if not expecting_executable:
                if token in control_operators or clean in control_operators:
                    expecting_executable = True
                continue

            if not clean.startswith("-") and "=" in clean:
                name, _, _ = clean.partition("=")
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                    continue

            if clean.startswith("-"):
                if clean in wrapper_value_flags:
                    skip_next = True
                continue

            base = clean.split("/")[-1].lower()
            if not base:
                continue

            if base in wrappers:
                continue

            if base in shells:
                j = i + 1
                while j < len(tokens):
                    t = tokens[j]
                    c = t.strip()
                    if c in control_operators:
                        break
                    if c == "-c":
                        if j + 1 < len(tokens) and self._check_interactive(tokens[j + 1]):
                            return True
                        skip_next = True
                        break
                    if c.startswith("-") and not c.startswith("--"):
                        body = c[1:]
                        if "i" in body and "c" not in body:
                            return True
                        if "c" in body:
                            if "i" in body:
                                return True
                            if j + 1 < len(tokens) and self._check_interactive(tokens[j + 1]):
                                return True
                            skip_next = True
                            break
                        j += 1
                        continue
                    if c.startswith("--"):
                        j += 1
                        continue
                    break

            if base in interactive_apps:
                return True

            if base in shells or base in interpreters:
                require_t_with_i = base in {"docker", "podman", "kubectl"}
                scan_nested_argv0 = base in {"docker", "podman", "kubectl", "ssh", "script"}

                for j in range(i + 1, len(tokens)):
                    sub_tok = tokens[j]
                    c_sub = sub_tok.strip()

                    if c_sub in control_operators or sub_tok in control_operators:
                        break

                    if c_sub in {"-i", "--interactive", "-t", "-tt", "--tty"}:
                        return True

                    if c_sub.startswith("-") and not c_sub.startswith("--"):
                        flags = c_sub[1:]
                        has_i = "i" in flags
                        has_t = "t" in flags
                        if require_t_with_i:
                            if has_i and has_t:
                                return True
                        elif has_i:
                            return True
                        if has_t and base in {"ssh", "script"}:
                            return True

                    if base.startswith("python") and c_sub in {"-c", "--command", "-m", "--module"}:
                        if j + 1 < len(tokens) and self._check_interactive(tokens[j + 1]):
                            return True

                    if scan_nested_argv0 and not c_sub.startswith("-"):
                        nested = c_sub.strip("()$`\"'\t\n{}[]").split("/")[-1].lower()
                        if nested in interactive_apps or nested in shells:
                            return True

                expecting_executable = False
                continue

            expecting_executable = False

        return False

    def execute_action(self, item: ConfigItem) -> None:
        if item.key == "__save_new_preset":
            self.action_save_preset()
            return

        elif item.key == "__import_new_preset":
            self.action_import_preset()
            return

        command = str(item.default) if item.default else ""

        if not command:
            self.notify_status(f"No command defined for: {item.label}", level="error")
            return

        def do_execute():
            self.notify_status(f"Executing: {item.label}...", level="info")

            forced = getattr(item, "force_interactive", None)
            is_interactive = (
                forced if isinstance(forced, bool) else self._check_interactive(command)
            )

            if is_interactive:
                if getattr(self, "_tty_action_busy", False):
                    self.notify_status("Another TTY action is already running.", level="warning")
                    return

                self._tty_action_busy = True
                try:
                    with self.suspend():
                        completed = subprocess.run(command, shell=True)
                    rc = completed.returncode
                    if rc == 0:
                        self.notify_status(f"Action '{item.label}' completed.", level="success")
                    else:
                        self.notify_status(f"Action '{item.label}' returned code {rc}.", level="warning")
                except Exception as e:
                    self.notify_status(f"Execution error: {str(e)[:60]}", level="error")
                finally:
                    self._tty_action_busy = False
                return

            async def run_noninteractive():
                proc: asyncio.subprocess.Process | None = None
                try:
                    proc = await asyncio.create_subprocess_shell(
                        command,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )

                    try:
                        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15.0)
                    except TimeoutError:
                        if proc is not None:
                            proc.kill()
                            await proc.wait()
                        self.notify_status("Action timed out after 15 seconds.", level="error")
                        return

                    if proc.returncode == 0:
                        out = stdout.decode("utf-8", errors="replace").strip()

                        if out:
                            out_single = out.split("\n")[0]
                            self.notify_status(f"Success: {out_single[:60]}", level="success")
                        else:
                            self.notify_status(f"Action '{item.label}' completed.", level="success")
                    else:
                        err = stderr.decode("utf-8", errors="replace").strip().split("\n")[0]
                        if not err:
                            err = "Unknown execution error"
                        self.notify_status(f"Action failed: {err[:60]}", level="error")

                except Exception as e:
                    self.notify_status(f"Execution error: {str(e)[:60]}", level="error")
                finally:
                    if proc is not None and proc.returncode is None:
                        proc.kill()
                        await proc.wait()

            asyncio.create_task(run_noninteractive())

        if item.confirm_message:
            self.push_screen(
                ConfirmDialog(
                    item.confirm_message,
                    title=f"Run: {item.label}",
                    level="warning"
                ),
                lambda confirm: do_execute() if confirm else None
            )
        else:
            do_execute()

    def apply_preset(self, preset_item: ConfigItem) -> None:
        # Hot-reload user preset payload from disk before applying.
        if preset_item.group == "User Presets" and preset_item.key.startswith("__user_preset_"):
            name = preset_item.label.replace("User: ", "", 1)
            file_path = self.user_presets_dir / f"{name}.json"

            if file_path.exists():
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        payload = json.load(f)

                    if isinstance(payload, dict):
                        preset_item.preset_payload = payload
                        preset_item._ratio_cache = None
                        self._schema_dirty_counter += 1
                    else:
                        preset_item.preset_payload = {"__INVALID__": True}
                        preset_item._ratio_cache = None
                        self._schema_dirty_counter += 1

                except Exception:
                    preset_item.preset_payload = {"__INVALID__": True}
                    preset_item._ratio_cache = None
                    self._schema_dirty_counter += 1

        if preset_item.preset_payload is None:
            self.notify_status("Preset contains no payload.", level="error")
            return

        if not isinstance(preset_item.preset_payload, dict):
            self.notify_status("Preset payload is invalid.", level="error")
            return

        if preset_item.preset_payload.get("__INVALID__", False):
            self.notify_status("Preset payload is invalid.", level="error")
            return

        def do_apply():
            transaction = []
            skipped = 0

            payload = preset_item.preset_payload
            is_all_defaults = payload.get("__ALL_DEFAULTS__", False)

            for t_idx, i_idx, target_item in self._configurable_items:
                if not target_item.exists_in_target:
                    skipped += 1
                    continue

                key_path = self._get_item_uid(target_item)

                if is_all_defaults:
                    target_val = target_item.default
                elif key_path in payload:
                    target_val = payload[key_path]
                else:
                    target_val = target_item.default

                if str(target_item.value) != str(target_val) and target_val is not None:
                    transaction.append((t_idx, i_idx, target_item.value, target_val))

            if not transaction:
                if skipped > 0:
                    self.notify_status(
                        f"Preset applied, but {skipped} items were missing/invalid.",
                        level="warning"
                    )
                else:
                    self.notify_status("Preset already active (no changes needed).", level="info")

                return

            verb = "applied" if self.auto_save else "queued"
            msg = f"Preset '{preset_item.label}' {verb}."

            if skipped > 0:
                msg += f" ({skipped} skipped)"

            self._apply_transaction(transaction, action_type="new", success_msg=msg)

        if preset_item.confirm_message:
            self.push_screen(
                ConfirmDialog(
                    preset_item.confirm_message,
                    title=f"Apply Preset: {preset_item.label}",
                    level="warning"
                ),
                lambda confirm: do_apply() if confirm else None
            )
        else:
            do_apply()

    def prompt_string(self, tab_idx: int, item_idx: int, item: ConfigItem) -> None:
        def check_reply(new_val: str | None) -> None:
            if new_val is not None:
                if item.type_ == "int":
                    try:
                        try:
                            parsed_val = int(new_val, 0)
                        except ValueError:
                            parsed_val = int(float(new_val))

                        if item.min_val is not None:
                            parsed_val = max(int(item.min_val), parsed_val)

                        if item.max_val is not None:
                            parsed_val = min(int(item.max_val), parsed_val)

                        new_val = parsed_val

                    except ValueError:
                        self.notify_status("Error: Value must be an integer.", level="error")
                        return

                elif item.type_ == "float":
                    try:
                        parsed_val = float(new_val)

                        if item.min_val is not None:
                            parsed_val = max(float(item.min_val), parsed_val)

                        if item.max_val is not None:
                            parsed_val = min(float(item.max_val), parsed_val)

                        new_val = parsed_val

                    except ValueError:
                        self.notify_status("Error: Value must be a float.", level="error")
                        return

                self._safe_apply_value(tab_idx, item_idx, item, new_val)

        self.push_screen(
            HybridInputScreen(
                f"Enter new {item.label}:",
                str(item.value),
                item.options
            ),
            check_reply
        )

    def prompt_picker(self, tab_idx: int, item_idx: int, item: ConfigItem) -> None:
        def check_reply(new_val: str | None) -> None:
            if new_val is not None:
                self._safe_apply_value(tab_idx, item_idx, item, new_val)

        self.push_screen(
            PickerScreen(
                item.label,
                item.options,
                item.hints,
                current=str(item.value)
            ),
            check_reply
        )
