#!/usr/bin/env python3
import os
import re
lazy import json
lazy import subprocess
lazy import colorsys
lazy import shlex
lazy import shutil
import asyncio
lazy import math
import sys
lazy import signal
lazy import tempfile
import logging
lazy import termios
lazy import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any, override
from collections import deque, defaultdict
from collections.abc import Mapping
from functools import lru_cache

from textual import on, events, work
from textual.message import Message
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal, VerticalScroll
from textual.css.query import NoMatches
from textual.geometry import Size, Region, Offset, Spacing
from textual.layout import Layout, WidgetPlacement
lazy from textual.widgets import Label, Input, Tabs, Tab, ContentSwitcher, OptionList, Markdown, Static
from textual.widgets.option_list import Option, OptionDoesNotExist
from textual.screen import ModalScreen
from textual.reactive import reactive
from textual.theme import Theme
from textual.timer import Timer
from textual.widget import Widget

from rich.text import Text
from rich.cells import cell_len


LOGGER = logging.getLogger(__name__)
_TARGET_UNREADABLE = sentinel("_TARGET_UNREADABLE")
_STATE_MISSING = sentinel("_STATE_MISSING")

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

# Nerd Font glyphs (render cleanly in terminals with a Nerd Font installed).
_ICON_WARNING = "\uf071"   # nf-fa-warning  (exclamation triangle)
_ICON_PENCIL = "\uf040"    # nf-fa-pencil
_ICON_ARROW  = "\uf061"    # nf-fa-arrow-right

_RE_RGB = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)")
_COLOR_NUMBER = r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))"
_RE_HSL = re.compile(rf"hsla?\(\s*{_COLOR_NUMBER}\s*,\s*{_COLOR_NUMBER}%?\s*,\s*{_COLOR_NUMBER}%?")
_RE_OKLCH = re.compile(rf"oklch\(\s*{_COLOR_NUMBER}(%)?\s+{_COLOR_NUMBER}\s+{_COLOR_NUMBER}")
_RE_RGBA_ALPHA = re.compile(r"rgba\([^,]+,[^,]+,[^,]+,\s*([0-9.]+)\)")
_RE_HSLA_ALPHA = re.compile(r"hsla\([^,]+,[^,]+,[^,]+,\s*([0-9.]+)\)")

# Bounded background-action execution (execute_action non-interactive path).
_ACTION_OUTPUT_LIMIT = 8192
_ACTION_TIMEOUT = 15.0
_ACTION_DRAIN_TIMEOUT = 3.0
_ACTION_KILL_GRACE = 1.0


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
@dataclass(frozen=True, slots=True)
class OptionCacheKey:
    uid: str
    kind: str
    presentation: tuple[Any, ...]
    state: tuple[Any, ...]


class OptionTextCache:
    __slots__ = ("_maxsize", "_data", "_uid_index", "_kind_index", "hits", "misses")

    def __init__(self, maxsize: int = 2048) -> None:
        self._maxsize = max(64, maxsize)
        self._data: dict[OptionCacheKey, Text] = {}
        self._uid_index: defaultdict[str, set[OptionCacheKey]] = defaultdict(set)
        self._kind_index: defaultdict[str, set[OptionCacheKey]] = defaultdict(set)
        self.hits = 0
        self.misses = 0

    def get(self, key: OptionCacheKey) -> Text | None:
        txt = self._data.pop(key, None)
        if txt is None:
            self.misses += 1
            return None
        self._data[key] = txt
        self.hits += 1
        return txt.copy()

    def put(self, key: OptionCacheKey, txt: Text) -> Text:
        if key in self._data:
            self._drop(key)
        elif len(self._data) >= self._maxsize:
            self._drop(next(iter(self._data)))
        self._data[key] = txt.copy()
        self._uid_index[key.uid].add(key)
        self._kind_index[key.kind].add(key)
        return txt

    def invalidate_uid(self, uid: str, *, include_presets: bool = False) -> None:
        kill = set(self._uid_index.get(uid, ()))
        if include_presets:
            kill.update(self._kind_index.get("preset", ()))
        for key in kill:
            self._drop(key)

    def invalidate_presets(self) -> None:
        for key in tuple(self._kind_index.get("preset", ())):
            self._drop(key)

    def _drop(self, key: OptionCacheKey) -> None:
        self._data.pop(key, None)
        uid_keys = self._uid_index.get(key.uid)
        if uid_keys is not None:
            uid_keys.discard(key)
            if not uid_keys:
                self._uid_index.pop(key.uid, None)
        kind_keys = self._kind_index.get(key.kind)
        if kind_keys is not None:
            kind_keys.discard(key)
            if not kind_keys:
                self._kind_index.pop(key.kind, None)

    def clear(self) -> None:
        self._data.clear()
        self._uid_index.clear()
        self._kind_index.clear()


class PresetMatchMatrix:
    """
    Structural index built once; current serialized values updated incrementally.
    ratio() is O(1). Changes update the preset counters directly.
    """
    __slots__ = (
        "_app", "_current", "_exists", "_defaults", "_expected",
        "_all_defaults", "_matches", "_totals", "_preset_uids",
        "_configurable_uids", "_uid_set"
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
            # A UID is a logical setting.  Duplicate presentations of it must
            # contribute once to a preset ratio.
            if uid in self._uid_set:
                continue
            self._configurable_uids.append(uid)
            self._uid_set.add(uid)
            self._current[uid] = item.serialize(item.value)
            self._defaults[uid] = item.serialize(item.default)
            self._exists[uid] = bool(item.exists_in_target)

        for p in presets:
            puid = p.uid
            if puid in self._expected:
                continue
            self._preset_uids.append(puid)
            payload = p.preset_payload or {}
            all_def = bool(payload.get("__ALL_DEFAULTS__", False))
            self._all_defaults[puid] = all_def
            exp: dict[str, str] = {}
            for key_path, raw in payload.items():
                if key_path == "__ALL_DEFAULTS__":
                    continue
                exp[key_path] = self._serialize_payload(key_path, raw)
            self._expected[puid] = exp
            self._recompute_preset(puid)

    def ingest_items(self, items: Any) -> None:
        touched = False
        seen: set[str] = set()
        for it in items:
            if it.type_ in ("preset", "action", "menu"):
                continue
            uid = it.uid
            if uid in seen:
                continue
            seen.add(uid)
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

        # Settings omitted from a payload are matched against their defaults,
        # so every setting can affect every preset's ratio.
        affected_presets = self._preset_uids
        if not old_exists and not new_exists:
            return
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
        # Canonical index is _items_by_uid (list of duplicate presentations);
        # serialize with the default-target presentation when available.
        items_for_uid = getattr(self._app, "_items_by_uid", {}).get(uid)
        if items_for_uid:
            try:
                # Prefer the canonical/default target when a UID is shown in
                # several per-target presentations.
                item = None
                for candidate in items_for_uid:
                    candidate_item = candidate[2] if isinstance(candidate, tuple) and len(candidate) == 3 else candidate
                    try:
                        if self._app._get_item_engine_info(candidate_item) == self._app.default_engine_key:
                            item = candidate_item
                            break
                    except Exception:
                        item = item or candidate_item
                if item is None:
                    item = items_for_uid[0]
                    item = item[2] if isinstance(item, tuple) and len(item) == 3 else item
                return item.serialize(raw)
            except Exception:
                pass
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


@lru_cache(maxsize=1024)
def parse_color_format(val: str) -> str:
    val = str(val).strip().lower()

    if val.startswith("0x"):
        return "0xhex"

    if val.startswith("#"):
        return "hex"

    if _RE_HYPR_HEX.prefixmatch(val):
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
    if hypr_m := _RE_HYPR_HEX.prefixmatch(val):
        v = hypr_m.group(1)
        if len(v) >= 6:
            try:
                return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
            except ValueError:
                pass

    # Functional rgb/rgba.
    if m_rgb := _RE_RGB.prefixmatch(val):
        return tuple(int(min(255, float(component))) for component in m_rgb.groups())

    # Functional hsl/hsla.
    if m_hsl := _RE_HSL.prefixmatch(val):
        hue, saturation, lightness = map(float, m_hsl.groups())
        if not all(map(math.isfinite, (hue, saturation, lightness))):
            return (128, 128, 128)
        h = (hue % 360.0) / 360.0
        s = max(0.0, min(1.0, saturation / 100.0))
        l_ = max(0.0, min(1.0, lightness / 100.0))
        r, g, b = colorsys.hls_to_rgb(h, l_, s)
        return (int(r * 255), int(g * 255), int(b * 255))

    # OKLCH.
    if m_oklch := _RE_OKLCH.prefixmatch(val):
        lightness = float(m_oklch.group(1)) / (100 if m_oklch.group(2) else 1)
        chroma, hue = float(m_oklch.group(3)), float(m_oklch.group(4))
        if not all(map(math.isfinite, (lightness, chroma, hue))):
            return (128, 128, 128)
        r, g, b = _oklch_to_rgb(max(0.0, min(1.0, lightness)), max(0.0, min(1.0, chroma)), hue % 360)
        return (
            max(0, min(255, int(r))),
            max(0, min(255, int(g))),
            max(0, min(255, int(b)))
        )

    return KNOWN_COLORS_LOWER.get(val, (128, 128, 128))


@lru_cache(maxsize=1024)
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
    original_val = original_val.strip()
    r, g, b = KNOWN_COLORS.get(color_name, (128, 128, 128))

    if fmt == "hypr_hex":
        alpha = "ff"
        hypr_m = re.prefixmatch(r"rgba?\([0-9a-fA-F]{6}([0-9a-fA-F]{2})?\)", original_val.strip())
        if hypr_m and hypr_m.group(1):
            alpha = hypr_m.group(1)

        is_rgba = original_val.strip().lower().startswith("rgba")
        prefix = "rgba" if is_rgba else "rgb"
        suffix = alpha if is_rgba else ""
        return f"{prefix}({r:02x}{g:02x}{b:02x}{suffix})"

    if fmt == "hex":
        if len(original_val) == 5 and original_val.startswith("#"):
            return f"#{r:02x}{g:02x}{b:02x}{original_val[-1] * 2}"
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
    except (OSError, UnicodeError, json.JSONDecodeError):
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
        Binding("left,h,up,k", "nav_prev", "Previous Option", priority=True),
        Binding("right,l,down,j", "nav_next", "Next Option", priority=True),
        Binding("tab", "nav_next", "Next Option", priority=True),
        Binding("shift+tab", "nav_prev", "Previous Option", priority=True),
        Binding("enter,space", "select_current", "Confirm", priority=True),
        Binding("y", "dismiss_true", "Confirm", priority=True, show=False),
    ]

    selected_index: reactive[int] = reactive(1)

    def __init__(self, message: str, title: str = "CONFIRM", level: str = "warning", default_confirm: bool = True) -> None:
        super().__init__()
        self.message = message
        self.title_text = title
        self.level = level
        self.selected_index = 1 if default_confirm else 0

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog", classes=f"-{self.level}"):
            yield Label(self.title_text, id="modal-title")
            yield Markdown(self.message, id="confirm-message")

            with Horizontal(classes="modal-btn-container"):
                yield Label(" Cancel ", classes="modal-cancel-btn", id="btn-cancel")
                yield Label(" Confirm ", classes="modal-close-btn", id="btn-confirm")

    def on_mount(self) -> None:
        self._update_btn_styles()

    def watch_selected_index(self, old_val: int, new_val: int) -> None:
        self._update_btn_styles()

    def _update_btn_styles(self) -> None:
        btn_ids = ["#btn-cancel", "#btn-confirm"]
        for idx, b_id in enumerate(btn_ids):
            try:
                lbl = self.query_one(b_id, Label)
                if idx == self.selected_index:
                    lbl.add_class("-focused")
                    lbl.remove_class("-unfocused")
                else:
                    lbl.remove_class("-focused")
                    lbl.add_class("-unfocused")
            except Exception:
                pass

    def action_nav_prev(self) -> None:
        self.selected_index = (self.selected_index - 1) % 2

    def action_nav_next(self) -> None:
        self.selected_index = (self.selected_index + 1) % 2

    def action_select_current(self) -> None:
        self.dismiss(self.selected_index == 1)

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
        Binding("left,right,up,down,tab", "dismiss_modal", "Dismiss", priority=True),
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
                yield Label(f" {self.btn_text} ", classes="modal-close-btn -focused")

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
                yield Label(" Authenticate ", classes="modal-close-btn -focused", id="btn-authenticate")

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
        Binding("left,h,up,k", "nav_prev", "Previous Option", priority=True),
        Binding("right,l,down,j", "nav_next", "Next Option", priority=True),
        Binding("tab", "nav_next", "Next Option", priority=True),
        Binding("shift+tab", "nav_prev", "Previous Option", priority=True),
        Binding("enter,space", "select_current", "Select", priority=True),
    ]

    selected_index: reactive[int] = reactive(2)

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

    def on_mount(self) -> None:
        self._update_btn_styles()

    def watch_selected_index(self, old_val: int, new_val: int) -> None:
        self._update_btn_styles()

    def _update_btn_styles(self) -> None:
        btn_ids = ["#btn-cancel", "#btn-discard", "#btn-save"]
        for idx, b_id in enumerate(btn_ids):
            try:
                lbl = self.query_one(b_id, Label)
                if idx == self.selected_index:
                    lbl.add_class("-focused")
                    lbl.remove_class("-unfocused")
                else:
                    lbl.remove_class("-focused")
                    lbl.add_class("-unfocused")
            except Exception:
                pass

    def action_nav_prev(self) -> None:
        self.selected_index = (self.selected_index - 1) % 3

    def action_nav_next(self) -> None:
        self.selected_index = (self.selected_index + 1) % 3

    def action_select_current(self) -> None:
        match self.selected_index:
            case 0:
                self.dismiss("cancel")
            case 1:
                self.dismiss("discard")
            case 2:
                self.dismiss("save")

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
        Binding("escape", "dismiss_modal", "Cancel", priority=True),
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
                yield Label(" Ok ", classes="modal-close-btn -focused", id="btn-confirm")

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

    def on_key(self, event: events.Key) -> None:
        if not self.options:
            return

        inp = self.query_one(Input)
        ol = self.query_one(OptionList)

        if inp.has_focus:
            if event.key == "down":
                event.stop()
                ol.focus()
                if ol.highlighted is None:
                    ol.highlighted = 0
                elif ol.highlighted < len(self.options) - 1:
                    ol.action_cursor_down()
                if ol.highlighted is not None:
                    inp.value = str(self.options[ol.highlighted])
        elif ol.has_focus:
            if event.key in ("down", "j"):
                event.stop()
                if ol.highlighted is None:
                    ol.highlighted = 0
                else:
                    ol.action_cursor_down()
                if ol.highlighted is not None:
                    inp.value = str(self.options[ol.highlighted])
            elif event.key in ("up", "k"):
                event.stop()
                if ol.highlighted is None or ol.highlighted <= 0:
                    inp.focus()
                else:
                    ol.action_cursor_up()
                    if ol.highlighted is not None:
                        inp.value = str(self.options[ol.highlighted])
            elif event.key == "enter":
                event.stop()
                if ol.highlighted is not None and 0 <= ol.highlighted < len(self.options):
                    self.dismiss(str(self.options[ol.highlighted]))
                else:
                    self.dismiss(inp.value)

    @on(OptionList.OptionHighlighted)
    def handle_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list.has_focus and self.options and event.option_index is not None and 0 <= event.option_index < len(self.options):
            val = str(self.options[event.option_index])
            inp = self.query_one(Input)
            if inp.value != val:
                inp.value = val

    @on(Input.Submitted)
    def handle_submit(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(event.value)

    @on(OptionList.OptionSelected)
    def handle_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if self.options and event.option_index is not None and 0 <= event.option_index < len(self.options):
            self.dismiss(str(self.options[event.option_index]))
        else:
            self.dismiss(str(event.option.prompt))

    def action_focus_list(self) -> None:
        if self.options:
            self.query_one(OptionList).focus()

    def action_focus_input(self) -> None:
        self.query_one(Input).focus()

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

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
        Binding("escape", "dismiss_modal", "Cancel", priority=True),
        Binding("up,k", "cursor_up", "Up", priority=True),
        Binding("down,j", "cursor_down", "Down", priority=True),
        Binding("page_up,ctrl+u", "page_up", "Page Up", priority=True),
        Binding("page_down,ctrl+d", "page_down", "Page Down", priority=True),
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
                yield Label(" Cancel ", classes="modal-close-btn -focused")

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

    def action_page_up(self) -> None:
        self.query_one(OptionList).action_page_up()

    def action_page_down(self) -> None:
        self.query_one(OptionList).action_page_down()

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    @on(events.Click, ".modal-close-btn")
    def on_close_click(self) -> None:
        self.dismiss(None)

    @on(events.Click)
    def on_background_click(self, event: events.Click) -> None:
        if event.control is self:
            self.dismiss(None)


class SearchScreen(ModalScreen[tuple[int, int] | None]):
    BINDINGS = [
        Binding("escape", "dismiss_modal", "Cancel", priority=True),
        Binding("down", "cursor_down", "Down", priority=True),
        Binding("up", "cursor_up", "Up", priority=True),
        Binding("page_up,ctrl+u", "page_up", "Page Up", priority=True),
        Binding("page_down,ctrl+d", "page_down", "Page Down", priority=True),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="search-dialog"):
            yield Label("FUZZY FIND (Ctrl+F)", id="modal-title")
            yield Input(placeholder="Type to filter configurations...", id="search-input")
            yield OptionList(id="search-list")

            with Horizontal(classes="modal-btn-container"):
                yield Label(" Cancel ", classes="modal-close-btn -focused")

    def on_mount(self) -> None:
        self.query_one(Input).focus()
        self._search_cache = []
        self._search_timer: Timer | None = None
        self._last_query = None

        for tab_idx, tab_items in self.app.schema.items():
            tab_name = self.app.tabs.get(tab_idx, f"Tab {tab_idx}")
            for item_idx, item in enumerate(tab_items):
                label_norm = item.label.casefold()
                haystack = f"{tab_name} {item.label} {item.key} {item.type_}".casefold()
                haystack_compact = "".join(haystack.split())
                self._search_cache.append(
                    (tab_idx, item_idx, item, tab_name, label_norm, haystack, haystack_compact)
                )

        self._populate_list("")

    def on_unmount(self) -> None:
        if self._search_timer is not None:
            self._search_timer.stop()
            self._search_timer = None

    @on(Input.Changed)
    def handle_input(self, event: Input.Changed) -> None:
        if self._search_timer is not None:
            self._search_timer.stop()
        self._search_timer = self.set_timer(
            0.05,
            lambda query=event.value: self._populate_list(query),
        )

    def _populate_list(self, query: str, *, force: bool = False) -> None:
        query_key = query.casefold().strip()
        if not force and query_key == self._last_query:
            return
        self._last_query = query_key
        ol = self.query_one(OptionList)
        ol.clear_options()
        self.results = []

        query_lower = query_key
        query_no_space = "".join(query_key.split())
        scored_results = []

        for tab_idx, item_idx, item, tab_name, label_norm, haystack, haystack_compact in self._search_cache:
            if not query_no_space:
                scored_results.append((100, tab_idx, item_idx, item, tab_name))
                continue

            score = 0
            lbl = label_norm

            if query_lower == lbl:
                score += 100
            elif lbl.startswith(query_lower):
                score += 50
            elif query_lower in lbl:
                score += 20

            # Subsequence / fuzzy match.
            q_idx, s_idx = 0, 0
            match_positions = []

            while q_idx < len(query_no_space) and s_idx < len(haystack_compact):
                if query_no_space[q_idx] == haystack_compact[s_idx]:
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

        if self._search_timer is not None:
            self._search_timer.stop()
            self._search_timer = None
        self._populate_list(event.value, force=True)

        ol = self.query_one(OptionList)
        if ol.highlighted is not None and ol.highlighted < len(self.results):
            self.dismiss(self.results[ol.highlighted])
        elif self.results:
            self.dismiss(self.results[0])

    def action_cursor_down(self) -> None:
        self.query_one(OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one(OptionList).action_cursor_up()

    def action_page_up(self) -> None:
        self.query_one(OptionList).action_page_up()

    def action_page_down(self) -> None:
        self.query_one(OptionList).action_page_down()

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    @on(events.Click, ".modal-close-btn")
    def on_close_click(self) -> None:
        self.dismiss(None)

    @on(events.Click)
    def on_background_click(self, event: events.Click) -> None:
        if event.control is self:
            self.dismiss(None)


class DiffScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape,enter,space,q", "dismiss_modal", "Dismiss", priority=True),
        Binding("up,k", "cursor_up", "Up", priority=True),
        Binding("down,j", "cursor_down", "Down", priority=True),
        Binding("page_up,ctrl+u", "page_up", "Page Up", priority=True),
        Binding("page_down,ctrl+d", "page_down", "Page Down", priority=True),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="diff-dialog"):
            yield Label("MODIFICATIONS (From Launch)", id="modal-title")
            yield OptionList(id="diff-list")

            with Horizontal(classes="modal-btn-container"):
                yield Label(" Close ", classes="modal-close-btn -focused")

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
                    txt.append(f"{_ICON_ARROW} ", style=self.app.theme_colors["muted"])
                    txt.append(f"{item.value}", style=f"bold {self.app.theme_colors['success']}")

                    ol.add_option(Option(txt, disabled=True))

        if not added_any:
            ol.add_option(Option("No changes detected from initial load state.", disabled=True))

    def action_cursor_up(self) -> None:
        self.query_one(OptionList).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one(OptionList).action_cursor_down()

    def action_page_up(self) -> None:
        self.query_one(OptionList).action_page_up()

    def action_page_down(self) -> None:
        self.query_one(OptionList).action_page_down()

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
        Binding("escape,enter,space,q,f1", "dismiss_modal", "Dismiss", priority=True),
        Binding("up,k", "cursor_up", "Up", priority=True),
        Binding("down,j", "cursor_down", "Down", priority=True),
        Binding("page_up,ctrl+u", "page_up", "Page Up", priority=True),
        Binding("page_down,ctrl+d", "page_down", "Page Down", priority=True),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="shortcuts-dialog"):
            yield Label("KEYBOARD SHORTCUTS", id="modal-title")
            yield OptionList(id="shortcuts-list")

            with Horizontal(classes="modal-btn-container"):
                yield Label(" Close ", classes="modal-close-btn -focused")

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
        bindings_info.insert(2, ("f5", "Refresh current TUI state"))

        for keys, desc in bindings_info:
            txt = Text()
            txt.append(f"{keys:<20}", style=self.app.theme_colors["accent"] + " bold")
            txt.append(f" {_ICON_ARROW} ", style=self.app.theme_colors["muted"])
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

    def watch_highlighted(self, highlighted: int | None) -> None:
        if getattr(self, "_restoring_options", False):
            if highlighted is not None and not self.get_option_at_index(highlighted).disabled:
                option = self.get_option_at_index(highlighted)
                self._restored_option = option
                self.post_message(self.OptionHighlighted(self, option, highlighted))
            return
        super().watch_highlighted(highlighted)

    def action_scroll_top(self) -> None:
        for i in range(self.option_count):
            if not self.get_option_at_index(i).disabled:
                self.highlighted = i
                self.scroll_y = 0
                break

    def action_scroll_bottom(self) -> None:
        for i in range(self.option_count - 1, -1, -1):
            if not self.get_option_at_index(i).disabled:
                self.highlighted = i
                break

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self._last_click_x = getattr(event, "x", 0)
        self._last_click_button = getattr(event, "button", 1)

        self._mouse_down_highlight = self.highlighted

    def on_mouse_move(self, event: events.MouseMove) -> None:
        try:
            line_idx = event.style.meta.get("option")
            new_tooltip = None

            if line_idx is not None and 0 <= line_idx < self.option_count:
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
        super().watch_scroll_y(old_value, new_value)

        if hasattr(self.app, "_update_scroll_indicators"):
            self.app._update_scroll_indicators()

    def watch_max_scroll_y(self, old_value: float, new_value: float) -> None:
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

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        try:
            tab_idx = int(self.id.split("-")[1])
            ol = self.app.query_one(f"#list-{tab_idx}", ConfigOptionList)
            ol.scroll_down(animate=False)
            event.stop()
        except Exception:
            pass

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        try:
            tab_idx = int(self.id.split("-")[1])
            ol = self.app.query_one(f"#list-{tab_idx}", ConfigOptionList)
            ol.scroll_up(animate=False)
            event.stop()
        except Exception:
            pass

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
            txt.append(f" │ Pending: {self.app._pending_setting_count()}", style=self.app.theme_colors["fg"])

        self.update(txt)

    async def on_click(self) -> None:
        await self.app.run_action("toggle_save_mode")


class ShortcutFlowLayout(Layout):
    """Arrange shortcut rows during layout, before the frame is painted."""
    name = "shortcut_flow"

    def arrange(self, parent, children, size, greedy=True):
        placements = []
        x = y = 0
        for child in children:
            width = min(size.width, child.get_content_width(size, parent.screen.size)
                        + child.styles.gutter.width)
            if x and x + width > size.width:
                x = 0
                y += 1
            placements.append(WidgetPlacement(Region(x, y, width, 1), Offset(), Spacing(), child))
            x += width
        return placements


class FlowContainer(Widget):
    DEFAULT_CSS = "FlowContainer { height: auto; width: 100%; }"

    @property
    def layout(self):
        return self._flow_layout

    def __init__(self, *args, **kwargs):
        self._flow_layout = ShortcutFlowLayout()
        super().__init__(*args, **kwargs)


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
    A custom container that tells the App to re-evaluate tab overflow when scrolled or resized.
    """

    def watch_scroll_x(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_x(old_value, new_value)
        if hasattr(self.app, "check_tab_overflow"):
            self.app.check_tab_overflow()

    def watch_virtual_size(self, old_value: Size, new_value: Size) -> None:
        if hasattr(self.app, "check_tab_overflow"):
            self.app.check_tab_overflow()

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        event.stop()
        if self.max_scroll_x > 0:
            target = min(float(self.max_scroll_x), self.scroll_x + 10)
            self.scroll_to(x=target, animate=False)
            if hasattr(self.app, "check_tab_overflow"):
                self.app.check_tab_overflow()

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        event.stop()
        if self.scroll_x > 0:
            target = max(0.0, self.scroll_x - 10)
            self.scroll_to(x=target, animate=False)
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
        height: auto;
        background: transparent;
        padding: 0 1;
        overflow: hidden hidden;
    }
    """

    def __init__(
        self, renderable_or_factory: Any, app_ref: Any = None,
        refresh_interval: float | None = None, *, collector=None, prepare=None,
        **kwargs: Any,
    ) -> None:
        super().__init__(Text("Loading…", style="dim italic"), **kwargs)
        self.renderable_or_factory = renderable_or_factory
        self.app_ref = app_ref
        self.refresh_interval = refresh_interval
        self.collector = collector
        self.prepare = prepare
        self._refresh_timer: Timer | None = None
        self._refresh_inflight = False
        self._refresh_task: asyncio.Task | None = None
        self._refresh_pending = False
        self._active = False
        self._dirty = True
        self._generation = 0
        self._factory_source = None
        self._factory_takes_app = False
        self._last_rendered_repr: str | None = None
        self._is_unmounted = False
        self._collected_snapshot = None

    def on_unmount(self) -> None:
        self._is_unmounted = True
        self.set_active(False)
        if self._refresh_task is not None:
            self._refresh_task.cancel()

    def set_active(self, active: bool) -> None:
        if active == self._active:
            return
        self._active = active
        if active:
            if self._dirty or self.collector is None:
                self.update_content()
            self._start_timer()
        else:
            self._generation += 1
            self._stop_timer()

    def invalidate_content(self) -> None:
        """Invalidate a retained snapshot after a model change or explicit refresh."""
        self._dirty = True
        self._generation += 1
        if self._refresh_task is not None:
            self._refresh_pending = True

    def _start_timer(self) -> None:
        if self._refresh_timer is None and self._active:
            interval = self.refresh_interval
            if interval is not None and interval > 0:
                self._refresh_timer = self.set_interval(interval, self._timer_tick)

    def _stop_timer(self) -> None:
        if self._refresh_timer is not None:
            self._refresh_timer.stop()
            self._refresh_timer = None

    def _timer_tick(self) -> None:
        if self._active and not self._refresh_inflight:
            self._request_refresh()

    async def _async_refresh(self) -> None:
        self._refresh_inflight = True
        try:
            while self._active and not self._is_unmounted:
                self._refresh_pending = False
                generation = self._generation
                try:
                    # Wait for engine writes before capturing selection and reading.
                    async with self.app_ref._save_lock:
                        if not self._active or generation != self._generation:
                            continue
                        prepared = self.prepare(self.app_ref) if self.prepare else None
                        snapshot = await self.app_ref._run_save_io(self.collector, prepared)
                    if self._active and generation == self._generation and not self._is_unmounted:
                        self._collected_snapshot = snapshot
                        self._apply_rendered_content(self._invoke_factory())
                        self._dirty = False
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    LOGGER.exception("Custom view collection failed")
                    if self._active and generation == self._generation and not self._is_unmounted:
                        self._apply_rendered_content(Text(f"Error rendering custom view: {exc}", style="bold red"))
                if not self._refresh_pending:
                    break
        finally:
            self._refresh_inflight = False
            self._refresh_task = None

    def _invoke_factory(self) -> Any:
        factory = self.renderable_or_factory
        if self.collector is not None:
            return factory(self._collected_snapshot)
        if not callable(factory):
            return factory

        if factory is not self._factory_source:
            try:
                parameters = inspect.signature(factory).parameters.values()
            except (TypeError, ValueError):
                self._factory_takes_app = True
            else:
                self._factory_takes_app = any(
                    p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
                    and p.default is inspect.Parameter.empty
                    for p in parameters
                )
            self._factory_source = factory
        return factory(self.app_ref) if self._factory_takes_app else factory()

    def update_content(self) -> None:
        self.invalidate_content()
        self._request_refresh()

    def _request_refresh(self) -> None:
        if not self._active or self._is_unmounted:
            return
        self._generation += 1
        if self.collector is not None:
            self._refresh_pending = True
            if self._refresh_task is None:
                task = self._refresh_task = asyncio.create_task(self._async_refresh())
                self.app_ref._custom_refresh_tasks.add(task)
                task.add_done_callback(self.app_ref._custom_refresh_tasks.discard)
            return
        # Legacy factories execute on their owning UI thread.
        try:
            res = self._invoke_factory()
            if res is not None:
                self._apply_rendered_content(res)
                self._dirty = False
        except Exception as exc:
            LOGGER.exception("Custom view rendering failed")
            self._apply_rendered_content(Text(f"Error rendering custom view: {exc}", style="bold red"))

    def _apply_rendered_content(self, res: Any) -> None:
        if self._is_unmounted:
            return

        res_repr = repr(res)
        self._last_rendered_repr = res_repr

        parent = self.parent
        saved_y = None
        if isinstance(parent, VerticalScroll):
            saved_y = parent.scroll_y

        self.update(res)

        if saved_y is not None and isinstance(parent, VerticalScroll) and saved_y > 0:
            parent.scroll_to(y=saved_y, animate=False, immediate=True)


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
    background: $background; color: $primary; text-style: bold;
}
.tab-arrow:hover { color: $foreground; background: $primary 25%; }

#content-area { height: 1fr; layout: horizontal; }
ContentSwitcher { width: 1fr; height: 1fr; background: transparent; }
ContentSwitcher > Vertical { width: 100%; height: 100%; background: transparent; }

#help-panel {
    width: 35%; height: 100%; min-width: 25; border-left: solid $primary;
    display: none; background: $background; padding: 1 2; overflow-y: auto;
}
#content-area.-show-help ContentSwitcher { width: 65%; }
#content-area.-show-help #help-panel { display: block; }

Tabs { width: auto; height: 1; background: transparent; }
Tabs #tabs-scroll { width: auto; }
Tabs #tabs-list-bar { width: auto; height: 1; min-width: 0; }
Tabs #tabs-list { width: auto; height: 1; min-width: 0; }
Tabs Underline { display: none; }

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
.custom-view-scroll { height: 1fr; overflow-x: hidden; overflow-y: auto; scrollbar-size: 1 1; }
.custom-rich-content { height: auto; overflow: hidden hidden; }
.custom-body { height: 1fr; }
.custom-body-with-options { height: 2fr; min-height: 3; }
.custom-options { height: 1fr; min-height: 3; }

ConfigOptionList {
    min-width: 20; width: 1fr; height: 1fr; scrollbar-size: 0 0;
    background: transparent; border: none;
}
ConfigOptionList > .option-list--option {
    padding: 0 1; background: transparent;
}
ConfigOptionList > .option-list--option-hover { background: $primary 10%; }
ConfigOptionList > .option-list--option-highlighted { background: $primary 20%; }
ConfigOptionList > .option-list--option-disabled { background: transparent; color: $primary; }

.indicator-column { width: 2; height: 1fr; background: transparent; align: right top; }

ScrollIndicator { width: 1; height: 1fr; color: $primary; }
ScrollIndicator:hover { color: $foreground; }

#bottom-dock {
    dock: bottom;
    width: 100%;
    height: auto;
    layout: vertical;
}

#local-search {
    width: 100%;
    height: 1;
    min-height: 1;
    margin: 0;
    padding: 0 1;
    display: none;
    border: none;
    background: $primary 20%;
    color: $accent;
}

#local-search:focus {
    border: none;
    background: $primary 30%;
    color: $accent;
    padding: 0 1;
}

#local-search > .input--value {
    color: $accent;
    text-style: bold;
}

#local-search > .input--placeholder {
    color: $foreground 50%;
    text-style: italic;
}

#local-search > .input--cursor {
    background: $accent;
    color: $background;
    text-style: bold;
}

#footer {
    width: 100%;
    height: auto;
    min-height: 2;
    border-top: solid $secondary;
    padding: 0 2;
    background: transparent;
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
.modal-close-btn:hover, .modal-close-btn.-focused {
    background: $primary; color: $background; text-style: bold;
}

.modal-cancel-btn {
    background: $secondary; color: $foreground; text-style: bold;
    padding: 0 2; width: auto; height: 1; margin: 0 1;
}
.modal-cancel-btn:hover, .modal-cancel-btn.-focused {
    background: $primary; color: $background; text-style: bold;
}

.modal-cancel-btn.-unfocused, .modal-close-btn.-unfocused {
    background: $secondary 40%; color: $foreground 70%; text-style: none;
}

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
        Binding("f5", "refresh_state", "Refresh", priority=True, show=False),
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
        engine_pool: Mapping[tuple[str, str], BaseEngine],
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
        hide_missing_items: bool = False,
        custom_views: dict[int | str, Any] | None = None,
        **kwargs
    ):
        super().__init__(**kwargs)

        self.supports_smooth_scrolling = True
        self.scroll_sensitivity_y = 2.0

        self.deferred_load = deferred_load
        self.hide_missing_items = hide_missing_items
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

        self._initial_tab = next(iter(self.tabs), None)
        self.user_presets_tab_idx = self._initial_tab if self._initial_tab is not None else 0

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
        self._last_theme_fingerprint = None
        if self.theme_path:
            loaded_theme = load_matugen_json(self.theme_path)
            if loaded_theme:
                self.theme_colors.update(loaded_theme)

            try:
                theme_stat = self.theme_path.stat()
                self.last_theme_mtime = theme_stat.st_mtime
                self._last_theme_fingerprint = (theme_stat.st_mtime_ns, theme_stat.st_size, theme_stat.st_ino)
            except OSError:
                pass

        self._status_timer: Timer | None = None

        self._cached_tab_bar: Horizontal | None = None
        self._cached_tabs_container: Horizontal | None = None
        self._cached_tab_left: Label | None = None
        self._cached_tab_right: Label | None = None

        self.auto_save = (default_mode.lower() == "auto")

        # External target modification tracking.
        self.last_target_mtimes: dict[tuple[str, str], tuple[int, int, int] | None] = {}
        self._initial_target_mtimes_set: bool = False
        self.telemetry_engine: BaseEngine | None = None

        # Lazy tab population state.
        self._tab_populated: set[int] = set()
        self._tab_dirty: set[int] = set()
        self._tab_warmup_queue: deque[int] = deque()
        self._tab_warmup_queued: set[int] = set()
        self._tab_warmup_scheduled = False

        # Schema indexes.
        self._items_by_uid: dict[str, list[tuple[int, int, ConfigItem]]] = {}
        self._item_refs: dict[int, tuple[int, int]] = {}
        self._items_by_engine: dict[tuple[str, str], list[tuple[int, int, ConfigItem]]] = {}
        self._children_by_parent: defaultdict[tuple[int, str], list[ConfigItem]] = defaultdict(list)
        self._configurable_items: list[tuple[int, int, ConfigItem]] = []
        self._preset_items: list[tuple[int, int, ConfigItem]] = []

        # Async save / stale-write protection.
        self._write_generation: dict[str, int] = {}
        self._active_save_count = 0
        self._save_tasks: set[asyncio.Task[Any]] = set()
        self._io_workers: set[asyncio.Task[Any]] = set()
        self._save_task_keys: dict[asyncio.Task[Any], set[str]] = {}
        self._save_auth_pending = 0
        self._save_failure_pending = False
        self._quit_after_save = False
        # _save_lock is already declared above (line ~2005); do not re-declare.
        self._sudo_keepalive: Timer | None = None

        # Background (non-interactive) action execution tracking.
        self._action_tasks: set[asyncio.Task[Any]] = set()
        self._action_procs: set[Any] = set()
        self._action_cleanup_tasks: set[asyncio.Task[Any]] = set()
        self._action_shutdown_started = False
        self._action_shutdown_done = False
        self._action_shutdown_lock: asyncio.Lock | None = None

        # Color variable registry.
        self._color_var_registry: dict[str, str] = {}
        self._color_var_counter: int = 1
        self._deferred_started = False

        self._engine_info_cache: dict[tuple[str | None, str | None], tuple[str, str]] = {}
        self._init_boot_state()
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

        if self._quit_after_save:
            return

        # BATCH mode: don't silently throw away queued writes.
        if self.pending_commits and not self._save_tasks and not self._save_auth_pending and (not self.auto_save or not self._save_timers):
            def on_reply(reply: str) -> None:
                if reply == "save":
                    self._quit_after_save = True
                    # Textual invokes the result callback before popping the
                    # modal. Save on the next message-pump turn, once the
                    # dialog is gone and modal guards allow the write.
                    self.call_later(self.action_save_batch)

                elif reply == "discard":
                    try:
                        self._cancel_background_actions()
                    except Exception:
                        pass
                    self.exit()

            self.push_screen(UnsavedChangesDialog(self._pending_setting_count()), on_reply)
            return

        # AUTO mode: flush debounced writes safely.
        if self.auto_save and self._save_timers:
            for (ti, ii), timer in list(self._save_timers.items()):
                timer.stop()
                self._bump_write_generation_for_item(self.schema[ti][ii])
                self.pending_commits.add((ti, ii))

            self._save_timers.clear()
            self._pending_autosave_args.clear()

            self._quit_after_save = True
            self.action_save_batch()
            return

        if self._save_tasks or self._save_auth_pending:
            self._quit_after_save = True
            self.notify_status("Waiting for the current save to finish.", level="info")
            return

        try:
            self._cancel_background_actions()
        except Exception:
            pass
        self.exit()

    def _modal_active(self) -> bool:
        try:
            return isinstance(self.screen, ModalScreen)
        except Exception:
            return False

    # =========================================================================
    # COMPOSE
    # =========================================================================
    def _custom_spec(self, tab_idx: int) -> Any:
        spec = self.custom_views.get(tab_idx)
        return self.custom_views.get(self.tabs.get(tab_idx)) if spec is None else spec

    def _custom_body_widgets(self, tab_idx: int) -> list[Widget]:
        spec = self._custom_spec(tab_idx)
        settings = spec if isinstance(spec, dict) and "view" in spec else {}
        view = settings.get("view", spec)
        if isinstance(view, type) and issubclass(view, Widget):
            return [view()]
        if isinstance(view, Widget):
            return [view]
        rich_widget = CustomRichTabWidget(
            view, app_ref=self, refresh_interval=settings.get("interval"),
            collector=settings.get("collect"), prepare=settings.get("prepare"),
            classes="custom-rich-content", id=f"custom-view-{tab_idx}",
        )
        rich_widget.can_focus = False
        return [VerticalScroll(rich_widget, classes="custom-view-scroll", id=f"custom-scroll-{tab_idx}")]

    def _notice_widgets(self, tab_idx: int, *, bottom: bool = False) -> list[Widget]:
        notices = self.tab_notices.get(tab_idx, [])
        if isinstance(notices, dict):
            notices = [notices]
        return [NoticeBox(notice.get("message", ""), level=notice.get("level", "info"),
                          id=f"notice-{tab_idx}-{index}" + ("-bot" if bottom else ""))
                for index, notice in enumerate(notices)
                if (notice.get("position", "top") == "bottom") == bottom]

    def _option_list_widgets(self, tab_idx: int) -> list[Widget]:
        return [ConfigOptionList(id=f"list-{tab_idx}"),
                Vertical(ScrollIndicator("", id=f"indicator-{tab_idx}"), classes="indicator-column")]

    async def _ensure_option_list(self, tab_idx: int) -> None:
        """Mount a hidden list on demand, sharing concurrent activation requests."""
        if task := self._option_mount_tasks.get(tab_idx):
            await asyncio.shield(task)
            return
        spec = self._custom_spec(tab_idx)
        if spec is not None and not (isinstance(spec, dict) and spec.get("show_options", False)):
            return
        if self.query(f"#list-{tab_idx}"):
            return
        async def mount_list():
            nodes = []
            try:
                pane = self.query_one(f"#tab-{tab_idx}")
                wrapper = Horizontal(*self._option_list_widgets(tab_idx), id=f"list-wrapper-{tab_idx}",
                                     classes="list-wrapper custom-options" if spec is not None else "list-wrapper")
                nodes.append(wrapper)
                bottom = next((child for child in pane.children
                               if child.id and child.id.startswith(f"notice-{tab_idx}-")
                               and child.id.endswith("-bot")), None)
                await pane.mount(wrapper, before=bottom)
            except BaseException:
                for node in nodes:
                    if node.parent is not None:
                        await node.remove()
                raise
            finally:
                self._option_mount_tasks.pop(tab_idx, None)
        task = self._option_mount_tasks[tab_idx] = asyncio.create_task(mount_list())
        await asyncio.shield(task)

    async def _ensure_custom_body(self, tab_idx: int) -> None:
        if self._custom_spec(tab_idx) is None or tab_idx in self._mounted_tabs:
            return
        task = self._custom_mount_tasks.get(tab_idx)
        if task is None:
            async def mount_body():
                nodes = []
                try:
                    host = self.query_one(f"#custom-body-{tab_idx}")
                    body = self._custom_body_widgets(tab_idx)
                    nodes.extend(body)
                    await host.mount(*body)
                    pane = self.query_one(f"#tab-{tab_idx}")
                    if top := self._notice_widgets(tab_idx):
                        nodes.extend(top)
                        await pane.mount(*top, before=host)
                    if bottom := self._notice_widgets(tab_idx, bottom=True):
                        nodes.extend(bottom)
                        await pane.mount(*bottom)
                    self._mounted_tabs.add(tab_idx)
                except BaseException:
                    for node in nodes:
                        if node.parent is not None:
                            await node.remove()
                    raise
                finally:
                    self._custom_mount_tasks.pop(tab_idx, None)
            task = self._custom_mount_tasks[tab_idx] = asyncio.create_task(mount_body())
        await asyncio.shield(task)

    def _activate_custom_views(self) -> None:
        current = self._current_tab_index()
        for tab_idx in self._mounted_tabs:
            for view in self.query_one(f"#custom-body-{tab_idx}").query(CustomRichTabWidget):
                view.set_active(tab_idx == current and self._engines_for_tab(tab_idx).issubset(self._loaded_engines))

    def compose(self) -> ComposeResult:
        with Vertical(id="main-box"):
            with Horizontal(id="tab-bar"):
                yield Label("   ", id="tab-left", classes="tab-arrow")

                with TabContainer(id="tabs-container"):
                    tabs_widget = Tabs(
                        *[Tab(name, id=f"tab-id-{i}") for i, name in self.tabs.items()],
                        id="tabs"
                    )
                    yield tabs_widget

                yield Label("   ", id="tab-right", classes="tab-arrow")

            yield Label("", id="telemetry-banner")

            with Horizontal(id="content-area"):
                with ContentSwitcher(initial=f"tab-{self._initial_tab}" if self._initial_tab is not None else None, id="content-switcher"):
                    for i, name in self.tabs.items():
                        with Vertical(id=f"tab-{i}"):
                            custom_view = self._custom_spec(i)
                            eager_notices = custom_view is None or i == self._initial_tab
                            if eager_notices:
                                yield from self._notice_widgets(i)

                            settings = custom_view if isinstance(custom_view, dict) else {}
                            if custom_view is not None:
                                initial = i == self._initial_tab
                                body = self._custom_body_widgets(i) if initial else []
                                if initial:
                                    self._mounted_tabs.add(i)
                                yield Vertical(*body, id=f"custom-body-{i}", classes="custom-body-with-options" if settings.get("show_options") else "custom-body")
                            if i == self._initial_tab and (custom_view is None or settings.get("show_options", False)):
                                with Horizontal(id=f"list-wrapper-{i}", classes="list-wrapper custom-options" if custom_view is not None else "list-wrapper"):
                                    yield from self._option_list_widgets(i)

                            if eager_notices:
                                yield from self._notice_widgets(i, bottom=True)

                yield Vertical(id="help-panel")

            with Vertical(id="bottom-dock"):
                yield Input(id="local-search", placeholder=" Jump to option... (Enter/Esc to close)")
                yield AppFooter(id="footer")

    # =========================================================================
    # ENGINE / STATE HELPERS
    # =========================================================================
    def _sync_pending(self, tab_idx: int, item_idx: int, item: ConfigItem) -> None:
        key = (tab_idx, item_idx)
        baseline = self._committed.get(key, item.default)
        if (
            item.serialize(item.value) == item.serialize(baseline)
            and not is_trigger_item(item)
            and not self._active_save_count
        ):
            self.pending_commits.discard(key)
        else:
            self.pending_commits.add(key)

    def _pending_setting_count(self) -> int:
        """Count settings once even when they appear in several tabs."""
        return len({
            self._uid_engine_key(item)
            for tab_idx, item_idx in self.pending_commits
            if (item := self._get_schema_item(tab_idx, item_idx)) is not None
        })

    def _item_is_pending(self, item: ConfigItem) -> bool:
        ref = self._item_refs.get(id(item))
        baseline = self._committed.get(ref, item.initial_value)
        return item.serialize(item.value) != item.serialize(baseline)

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
            self._option_cache.invalidate_uid(
                item.uid,
                include_presets=item.type_ not in ("preset", "action", "menu"),
            )
        if item.type_ not in ("preset", "action", "menu"):
            # Preset matching is global-only: per-file overrides (e.g. per-game GPU) should NOT
            # pollute the global preset ratio. Only default-engine items participate.
            try:
                if self._get_item_engine_info(item) == self.default_engine_key:
                    if hasattr(self, "_preset_matrix"):
                        self._preset_matrix.on_item_changed(item)
            except Exception:
                if hasattr(self, "_preset_matrix"):
                    self._preset_matrix.on_item_changed(item)
        self._schema_dirty_counter += 1
        cur = self._current_tab_index()
        if cur is not None:
            self._tab_dirty.add(cur)

    def _has_pending_save_for_key(self, uek: str) -> bool:
        for item, _value, _old in getattr(self, "_pending_autosave_args", {}).values():
            if self._uid_engine_key(item) == uek:
                return True
        for tab_idx, item_idx in getattr(self, "pending_commits", set()):
            item = self._get_schema_item(tab_idx, item_idx)
            if item is not None and self._uid_engine_key(item) == uek:
                return True
        return any(uek in keys for keys in getattr(self, "_save_task_keys", {}).values())

    def _get_item_engine_info(self, item: ConfigItem) -> tuple[str, str]:
        """
        Resolves target engine and file config dynamically via overrides.
        """
        overrides = (item.engine_type_override, item.target_file_override)
        if overrides == (None, None):
            return self.default_engine_key
        if cached := self._engine_info_cache.get(overrides):
            return cached
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

        result = (e_type, t_file)
        self._engine_info_cache[overrides] = result
        return result

    def _uid_engine_key(self, item: ConfigItem) -> str:
        """Composite key for per-file isolation: UID + engine."""
        engine_type, target_file = self._get_item_engine_info(item)
        return f"{item.uid}@@{engine_type}@@{target_file}"

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
          - DEFAULT/key
          - DEFAULT.key
          - key
        """
        if not state:
            return _STATE_MISSING

        scope = item.scope or "DEFAULT"
        candidates = (
            f"{scope}/{item.key}",
            f"{scope}.{item.key}",
            f"DEFAULT/{item.key}",
            f"DEFAULT.{item.key}",
            item.key,
        )

        for candidate in candidates:
            if candidate in state:
                return state[candidate]

        return _STATE_MISSING

    def _get_schema_item(self, tab_idx: int, item_idx: int) -> ConfigItem | None:
        try:
            return self.schema[tab_idx][item_idx]
        except Exception:
            return None

    # =========================================================================
    # SCHEMA INDEXES
    # =========================================================================
    def _rebuild_indexes(self) -> None:
        self._engine_info_cache.clear()
        self._key_map.clear()
        self._items_by_uid.clear()
        self._item_refs.clear()
        self._items_by_engine.clear()
        self._children_by_parent.clear()
        self._configurable_items.clear()
        self._preset_items.clear()
        register_engine = getattr(self.engine_pool, "register", None)

        for t_idx, items in self.schema.items():
            for i_idx, item in enumerate(items):
                self._item_refs[id(item)] = (t_idx, i_idx)
                uid = self._get_item_uid(item)

                self._key_map[uid] = (t_idx, i_idx)
                self._items_by_uid.setdefault(uid, []).append((t_idx, i_idx, item))

                if item.parent_ref:
                    self._children_by_parent[(t_idx, str(item.parent_ref))].append(item)

                try:
                    ekey = self._get_item_engine_info(item)
                except Exception:
                    ekey = self.default_engine_key

                self._items_by_engine.setdefault(ekey, []).append((t_idx, i_idx, item))
                if callable(register_engine):
                    register_engine(*ekey)

                if item.type_ not in ("action", "preset", "menu") and not item.read_only:
                    self._configurable_items.append((t_idx, i_idx, item))

                if item.type_ == "preset":
                    self._preset_items.append((t_idx, i_idx, item))

        if hasattr(self, "_preset_matrix"):
            # Presets are global snapshots (default file only). Per-game duplicates sharing same UID
            # but different target_file_override must NOT pollute the preset ratio. Filter to default engine.
            try:
                global_cfg = [c for c in self._configurable_items if self._get_item_engine_info(c[2]) == self.default_engine_key]
            except Exception:
                global_cfg = self._configurable_items
            self._preset_matrix.rebuild(global_cfg + self._preset_items)

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

    def _get_parent_children_status(self, parent_item: ConfigItem, tab_idx: int | None = None) -> tuple[bool, bool]:
        """
        Recursively inspects all child items under `parent_item` AND parent_item itself to determine if:
        - is_child_modified: Parent or any descendant item value differs from its schema default.
        - is_child_pending: Parent or any descendant item value differs from its initial value.
        """
        parent_modified = False
        parent_pending = False
        if parent_item.type_ not in ("menu", "action", "preset"):
            v_ser = parent_item.serialize(parent_item.value)
            d_ser = parent_item.serialize(parent_item.default)
            parent_modified = (v_ser != d_ser)
            parent_pending = self._item_is_pending(parent_item)

        parent_key = parent_item.key
        parent_uid = self._get_item_uid(parent_item)

        if tab_idx is None:
            tab_idx = self._current_tab_index()

        seen_items: set[int] = set()
        stack = [ref for ref in (parent_key, parent_uid) if ref]

        any_modified = parent_modified
        any_pending = parent_pending

        def _settled() -> bool:
            return any_modified and (any_pending or self.auto_save)

        if _settled():
            return any_modified, any_pending

        while stack:
            curr = str(stack.pop())
            for itm in self._children_by_parent.get((tab_idx, curr), ()):
                marker = id(itm)
                if marker in seen_items:
                    continue
                seen_items.add(marker)
                if itm.type_ not in ("menu", "action", "preset"):
                    v_ser = itm.serialize(itm.value)
                    d_ser = itm.serialize(itm.default)

                    if v_ser != d_ser:
                        any_modified = True
                    if self._item_is_pending(itm):
                        any_pending = True

                    if _settled():
                        return any_modified, any_pending
                if getattr(itm, "is_parent", False) or getattr(itm, "type_", None) == "menu":
                    if itm.key:
                        stack.append(itm.key)
                    stack.append(self._get_item_uid(itm))

        return any_modified, any_pending

    # =========================================================================
    # OPTION RENDERING
    # =========================================================================
    def _build_option(
        self,
        item: ConfigItem,
        is_highlighted: bool = False,
        indent_prefix: str = "",
        tab_idx: int | None = None
    ) -> Text:
        val_ser = item.serialize(item.value)
        def_ser = item.serialize(item.default)
        ratio_bucket = int(self._get_preset_match_ratio(item) * 10) if item.type_ == "preset" else -1

        if item.is_parent or item.type_ == "menu":
            is_modified, is_pending = self._get_parent_children_status(item, tab_idx)
        else:
            is_pending = self._item_is_pending(item)
            is_modified = (val_ser != def_ser)

        try:
            engine_identity = self._get_item_engine_info(item)
        except Exception:
            engine_identity = self.default_engine_key
        cache_key = OptionCacheKey(
            uid=item.uid,
            kind=item.type_,
            presentation=(
                tab_idx,
                item.key,
                item.label,
                tuple(item.options or ()),
                tuple(item.hints or ()),
                engine_identity,
            ),
            state=(
                val_ser,
                item.exists_in_target,
                is_pending,
                is_modified,
                is_highlighted,
                indent_prefix,
                item.expanded,
                bool(item.warning_msg),
                item.read_only,
                item.is_parent,
                ratio_bucket,
                self.auto_save,
                getattr(self, "_theme_version", 0),
            ),
        )

        if hasattr(self, "_option_cache"):
            hit = self._option_cache.get(cache_key)
            if hit is not None:
                return hit

        txt = Text()

        exists = item.exists_in_target

        CURSOR_CHAR = "❯"
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
                exp_char = "▼ " if item.expanded else "▶ "
                txt.append(exp_char, style=f"{self.theme_colors['accent']} bold")
        elif item.is_parent:
            exp_char = "▼ " if item.expanded else "▶ "
            txt.append(exp_char, style=f"{self.theme_colors['accent']} bold")
        else:
            txt.append("  ")

        # Status indicator.
        if item.type_ == "preset":
            if is_active_preset:
                txt.append("✦  ", style=self.theme_colors["success"])
            elif is_deviated_preset:
                txt.append("✦  ", style=self.theme_colors["warning"])
            else:
                txt.append("·  ", style=self.theme_colors["muted"])

        elif item.type_ == "action":
            txt.append("·  ", style=self.theme_colors["muted"])

        else:
            if not self.auto_save and is_pending:
                txt.append("[+] ", style=self.theme_colors["warning"])
            else:
                if is_modified and exists:
                    txt.append("✦  ", style=self.theme_colors["warning"])
                else:
                    txt.append("·  ", style=self.theme_colors["muted"])

        # Label rendering.
        warning_marker = f"{_ICON_WARNING} " if item.warning_msg else ""

        if exists:
            if item.type_ == "preset" and is_active_preset:
                label_style = f"{self.theme_colors['success']} bold"
            elif item.type_ == "preset" and is_deviated_preset:
                label_style = f"{self.theme_colors['warning']} bold" if is_highlighted else f"{self.theme_colors['fg']}"
            elif is_modified:
                label_style = f"{self.theme_colors['warning']} bold" if is_highlighted else self.theme_colors["warning"]
            elif item.is_parent or item.type_ == "menu":
                label_style = f"{self.theme_colors['accent']} bold" if is_highlighted else self.theme_colors["accent"]
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
        if item.read_only:
            txt.append("Read only", style=self.theme_colors["muted"])
        elif item.type_ in ("action", "preset", "menu"):
            if item.type_ == "preset":
                if is_active_preset:
                    txt.append("󰄬 Active", style=f"bold {self.theme_colors['success']}")
                elif is_deviated_preset:
                    txt.append("󰐊 Apply", style=f"bold {self.theme_colors['warning']}")
                else:
                    txt.append(
                        "󰐊 Apply",
                        style=f"bold {self.theme_colors['accent']}" if exists else f"{self.theme_colors['muted']} italic"
                    )

            elif item.type_ == "action":
                txt.append(
                    "󰐊 Run",
                    style=f"bold {self.theme_colors['accent']}" if exists else f"{self.theme_colors['muted']} italic"
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
                            btn_label = f"󰐊 {opt0[8:]}"
                        elif opt0_lower.startswith("copy:"):
                            btn_label = f"󰈔 {opt0[5:]}"
                        elif opt0_lower == "trigger":
                            btn_label = "󰐊 Apply"
                        elif opt0_lower == "copy":
                            btn_label = "󰈔 Copy"
                        else:
                            btn_label = "󰐊 Apply"

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
                            f"{'◉' if item.value else '◯'}",
                            style=f"{self.theme_colors['muted']} italic"
                        )

                    elif item.value:
                        txt.append("◉", style=f"bold {self.theme_colors['success']}")
                    else:
                        txt.append("◯", style=f"dim {self.theme_colors['fg']}")

                case "string":
                    if val_str == "":
                        txt.append(f"[{_ICON_PENCIL}] Unset", style=f"italic {self.theme_colors['muted']}")
                    else:
                        txt.append(f"[{_ICON_PENCIL}] {val_str}", style=accent)

                case "picker":
                    txt.append(f"[+] {val_str}", style=accent)

                case "color":
                    resolved_color = self.theme_colors.get(val_str, val_str)
                    r, g, b = color_to_rgb(resolved_color)
                    hex_color = f"#{r:02x}{g:02x}{b:02x}"

                    is_variable = is_theme_variable(val_str)
                    if not is_variable:
                        txt.append("⬤ ", style=hex_color if exists else self.theme_colors["muted"])

                    if is_variable:
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

                                elif re.fullmatch(r"[a-zA-Z0-9_-]+", norm_val):
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

                        txt.append(display_name, style=accent)

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

    def _invalidate_theme_cache(self) -> None:
        self._theme_version = getattr(self, "_theme_version", 0) + 1
        if hasattr(self, "_option_cache"):
            self._option_cache.clear()

    # =========================================================================
    # USER PRESETS
    # =========================================================================
    def _read_user_presets(self) -> list[tuple[str, dict[str, Any], str | None]]:
        if not self.enable_user_presets:
            return []

        self.user_presets_dir.mkdir(parents=True, exist_ok=True)
        records: list[tuple[str, dict[str, Any], str | None]] = []
        for file_path in sorted(
            (p for p in self.user_presets_dir.iterdir() if p.name.endswith(".json")),
            key=lambda p: p.stem.lower(),
        ):
            name = file_path.stem
            warning = None
            try:
                with file_path.open("r", encoding="utf-8") as stream:
                    payload = json.load(stream)
                if not isinstance(payload, dict):
                    payload = {"__INVALID__": True, "__ERROR__": "Expected JSON object"}
                    warning = "Invalid preset payload: expected JSON object"
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                payload = {"__INVALID__": True, "__ERROR__": str(exc)}
                warning = f"Unable to read preset: {exc}"
            records.append((name, payload, warning))
        return records

    def _apply_user_presets(
        self,
        preset_records: list[tuple[str, dict[str, Any], str | None]],
    ) -> None:
        if not self.enable_user_presets:
            return

        # Remove dynamically added User Presets from previous loads.
        for t_idx, items in self.schema.items():
            self.schema[t_idx] = [
                itm for itm in items
                if not (
                    itm.group == "User Presets"
                    and (
                        itm.key.startswith("__user_preset_")
                        or itm.key in ("__save_new_preset", "__import_new_preset", "__reset_all_defaults")
                    )
                )
            ]

        reset_btn = ConfigItem(
            label="󰁯 Reset to Defaults",
            key="__reset_all_defaults",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="User Presets",
            preset_payload={
                "__ALL_DEFAULTS__": True
            },
            confirm_message="Are you sure you want to reset ALL settings to their initial factory defaults?",
            extended_help="Resets all configuration options across all tabs back to their original factory defaults."
        )
        reset_btn.exists_in_target = True

        save_btn = ConfigItem(
            label="󰆓 Save as Preset",
            key="__save_new_preset",
            scope="DEFAULT",
            type_="action",
            default=None,
            group="User Presets",
            extended_help="Click here to save the current configuration state as a new reusable preset."
        )
        save_btn.exists_in_target = True

        import_btn = ConfigItem(
            label="󰏔 Import Preset",
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

        user_preset_items = [reset_btn, save_btn, import_btn]

        for name, payload, warning in preset_records:
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

    def _load_user_presets(self) -> None:
        """Reload preset records for callers already on the UI thread."""
        self._apply_user_presets(self._read_user_presets())

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
            try:
                expanded_path.parent.mkdir(parents=True, exist_ok=True)
                expanded_path.touch(exist_ok=True)
            except OSError:
                self.notify_status("Unable to create file for external editing.", level="error")
                return

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
                editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
                if editor:
                    editor_cmd = shlex.split(editor)
                else:
                    executable = shutil.which("nvim") or shutil.which("nano")
                    editor_cmd = [executable] if executable else []
                if not editor_cmd:
                    self.notify_status("Set VISUAL or EDITOR, or install nvim/nano.", level="warning")
                    return
                self.run_suspended_interactive([*editor_cmd, str(expanded_path)])

        except (OSError, ValueError):
            self.notify_status("Error resolving path or launching external editor.", level="error")

    def run_suspended_interactive(self, cmd: list[str] | str, shell: bool = False) -> subprocess.CompletedProcess:
        """
        Runs an interactive CLI application (editor, curses tool, fzf, etc.)
        while cleanly suspending Textual, managing termios attributes, disabling
        software flow control (IXON/Ctrl+S) so the TTY never locks up, flushing
        residual input escapes, and forcing a full screen redraw upon return.
        """
        stdin_fd: int | None = None
        saved_termios = None
        if sys.stdin.isatty():
            try:
                stdin_fd = sys.stdin.fileno()
                saved_termios = termios.tcgetattr(stdin_fd)
                working = termios.tcgetattr(stdin_fd)
                working[0] &= ~termios.IXON  # Disable software flow control
                termios.tcsetattr(stdin_fd, termios.TCSANOW, working)
                termios.tcflow(stdin_fd, termios.TCOON)
            except Exception:
                pass

        try:
            with self.suspend():
                return subprocess.run(cmd, shell=shell)
        finally:
            if stdin_fd is not None:
                try:
                    termios.tcflush(stdin_fd, termios.TCIFLUSH)
                    termios.tcflow(stdin_fd, termios.TCOON)
                    if saved_termios is not None:
                        termios.tcsetattr(stdin_fd, termios.TCSANOW, saved_termios)
                except Exception:
                    pass
            self.refresh(layout=True)

    # =========================================================================
    # MOUNT
    # =========================================================================
    @override
    async def on_mount(self) -> None:
        self._save_lock = asyncio.Lock()

        self.query_one("#main-box").border_title = f" {self.editor_title} "
        self.apply_theme_to_engine()

        self._cached_tab_bar = self.query_one("#tab-bar", Horizontal)
        self._cached_tabs_container = self.query_one("#tabs-container", Horizontal)
        self._cached_tab_left = self.query_one("#tab-left", Label)
        self._cached_tab_right = self.query_one("#tab-right", Label)

        try:
            self.query_one("#shortcut-ctrl-s").display = not self.auto_save
            self.query_one("#shortcut-R").display = self.auto_save
        except Exception:
            pass

        # Engine imports and constructors belong to the background boot path;
        # mounting must not wait for a backend before the first frame.
        self.run_deferred_boot(initial_tab=self._initial_tab)

        if first_ol := self.current_option_list:
            first_ol.focus()
            self._update_pagination(first_ol)

        if self.theme_path:
            self.set_interval(1.0, self.watch_theme_file)

        # External file & preset dir polling (previously dead code – now scheduled).
        self.set_interval(1.0, self.watch_target_file)
        self.set_interval(1.5, self.watch_presets_dir)

        self.call_after_refresh(self.check_tab_overflow)
        self.call_after_refresh(self._update_scroll_indicators)
        self._update_footer_legend()

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
        self._populated_tabs = self._tab_populated
        self._custom_mount_tasks: dict[int, asyncio.Task] = {}
        self._option_mount_tasks: dict[int, asyncio.Task] = {}
        self._custom_refresh_tasks: set[asyncio.Task] = set()
        self._pending_search_target: tuple[int, int] | None = None
        self._tab_data_ready: set[int] = set()
        self._boot_complete: bool = False

    def _engines_for_tab(self, tab_idx: int) -> set[tuple[str, str]]:
        keys: set[tuple[str, str]] = set()
        if self._custom_spec(tab_idx) is not None:
            keys.add(self.default_engine_key)
        for item in self.schema.get(tab_idx, []):
            if item.type_ in ("action", "preset", "menu"):
                continue
            keys.add(self._get_item_engine_info(item))
        return keys

    def _load_one_engine_sync(self, ekey: tuple[str, str]) -> Any:
        eng = self.engine_pool[ekey]
        if self.deferred_load and hasattr(eng, "load_state_for_units"):
            items = [item for _tab, _index, item in self._items_by_engine.get(ekey, ())
                     if item.type_ not in ("menu", "action", "preset")]
            return eng.load_state_for_units(
                [item.key for item in items if item.scope == "user"],
                [item.key for item in items if item.scope == "system"],
            )
        return eng.load_state()

    async def _load_engine_states(
        self, keys: set[tuple[str, str]], loader: Any = None
    ) -> tuple[dict[tuple[str, str], Any], dict[tuple[str, str], str]]:
        states: dict[tuple[str, str], Any] = {}
        errors: dict[tuple[str, str], str] = {}
        if not keys:
            return states, errors
        loader = self._load_one_engine_sync if loader is None else loader
        async def load(ekey):
            try:
                states[ekey] = await self._run_save_io(loader, ekey)
            except Exception as exc:
                errors[ekey] = f"{type(exc).__name__}: {exc}"
        if len(keys) == 1:
            await load(next(iter(keys)))
        else:
            # The default executor sizes its bounded pool from available CPUs.
            # TaskGroup waits for cancellation to drain every blocking read.
            async with asyncio.TaskGroup() as group:
                for ekey in keys:
                    group.create_task(load(ekey))
        return states, errors

    def _apply_states_to_tab(
        self, tab_idx: int, states: dict[tuple[str, str], Any], *, update_presets: bool = True
    ) -> None:
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

            if raw is not _STATE_MISSING:
                item.exists_in_target = True
                new_val = item.deserialize(raw)
            else:
                item.exists_in_target = not self.hide_missing_items and item.default != "nil"
                new_val = item.value

            if not item._initial_loaded:
                item.value = new_val
                item.initial_value = clone_value(item.value)
                item._initial_loaded = True
                self._committed[(tab_idx, i_idx)] = clone_value(item.value)
                freshly.append(item)

        self._tab_data_ready.add(tab_idx)
        if update_presets and freshly and hasattr(self, "_preset_matrix"):
            try:
                global_fresh = [it for it in freshly if self._get_item_engine_info(it) == self.default_engine_key]
            except Exception:
                global_fresh = freshly
            if global_fresh:
                self._preset_matrix.ingest_items(global_fresh)

    def _mark_boot_complete_if_done(self) -> None:
        was_complete = self._boot_complete
        self._boot_complete = (
            not self._pending_engine_loads
            and set(self.engine_pool).issubset(
                self._loaded_engines | set(self._failed_engines)
            )
        )
        if self._boot_complete and not was_complete:
            # Boot readiness concerns the model, not which tabs were painted.
            # Global presets must see cached values in hidden tabs immediately.
            # Rebuild the preset matrix once after all ready models are loaded.
            for tab_idx in self.tabs:
                if tab_idx not in self._tab_data_ready and self._engines_for_tab(tab_idx).issubset(self._loaded_engines):
                    self._apply_states_to_tab(tab_idx, self._states, update_presets=False)
            try:
                global_cfg = [c for c in self._configurable_items if self._get_item_engine_info(c[2]) == self.default_engine_key]
            except Exception:
                global_cfg = self._configurable_items
            self._preset_matrix.rebuild(global_cfg + self._preset_items)
            self._option_cache.invalidate_presets()
            self._schema_dirty_counter += 1
            self._refresh_presets_ui()
            self._update_file_link()
            for ekey in self.engine_pool:
                if ekey not in self._loaded_engines:
                    continue
                engine = self.engine_pool[ekey]
                if hasattr(engine, "get_telemetry"):
                    self.telemetry_engine = engine
                    break

            if self.telemetry_engine:
                self.query_one("#telemetry-banner").display = True
                self.set_interval(1.0, self.update_telemetry)

        if self._boot_complete and self.deferred_load and not self._deferred_started:
            self._deferred_started = True
            self._inventory_refreshing = True
            self._run_deferred_load()

    @work(exclusive=True, group="deferred-tabs", exit_on_error=False)
    async def _run_deferred_load(self, *, manual_refresh: bool = False) -> None:
        self._inventory_refreshing = True
        try:
            writes_before = dict(self._write_generation)
            result = await asyncio.to_thread(self.deferred_load)
            if isinstance(result, tuple) and len(result) == 3:
                updated_tabs, new_items, default_state = result
            elif isinstance(result, tuple) and len(result) == 2:
                updated_tabs, new_items = result
                default_state = None
            else:
                updated_tabs, new_items = result, None
                default_state = None
            # Discovery can introduce a backend or target absent from the
            # original schema. Register it before collecting its first state.
            if new_items and callable(register := getattr(self.engine_pool, "register", None)):
                for rows in new_items.values():
                    for item in rows:
                        register(*self._get_item_engine_info(item))
            while True:
                # Save and modal callbacks hold positional row references. Finish them
                # before reading/replacing rows or publishing discovery state.
                while new_items and (self._save_tasks or self._save_timers or self._save_auth_pending or self._modal_active()):
                    await asyncio.sleep(0.05)
                async with self._save_lock:
                    # A schema can return the state collected during discovery.
                    # Re-read it if an edit happened while discovery was running.
                    use_prefetched = default_state is not None and self._write_generation == writes_before
                    prospective_schema = self.schema | (new_items or {})
                    def load_one(key, engine):
                        if use_prefetched and key == self.default_engine_key:
                            return default_state
                        if new_items and hasattr(engine, "load_state_for_units"):
                            items = [item for rows in prospective_schema.values() for item in rows
                                     if item.type_ not in ("menu", "action", "preset")
                                     and self._get_item_engine_info(item) == key]
                            return engine.load_state_for_units(
                                [item.key for item in items if item.scope == "user"],
                                [item.key for item in items if item.scope == "system"],
                            )
                        return engine.load_state()
                    states, errors = await self._load_engine_states(
                        set(self.engine_pool), lambda key: load_one(key, self.engine_pool[key])
                    )
                    if errors:
                        raise RuntimeError(f"Deferred engine reads failed: {errors}")
                    if use_prefetched and self._write_generation != writes_before:
                        use_prefetched = False
                        states[self.default_engine_key] = await self._run_save_io(
                            load_one, self.default_engine_key, self.engine_pool[self.default_engine_key]
                        )
                if not new_items or not (self._save_tasks or self._save_timers or self._save_auth_pending or self._modal_active()):
                    break
            self._apply_deferred_tabs(updated_tabs, states, new_items)
            self._states.update(states)
            self._loaded_engines.update(states)
            for key in states:
                self._failed_engines.pop(key, None)
            self._mark_boot_complete_if_done()
            self._activate_custom_views()
            if manual_refresh:
                self._apply_refreshed_states(states)
                self._refresh_custom_views()
                self.notify_status("Refreshed current TUI state.")
        except Exception:
            LOGGER.exception("Deferred tab loading failed")
            self.notify_status("Deferred discovery failed; existing rows were kept.", level="error")
        finally:
            self._inventory_refreshing = False

    def action_refresh_state(self) -> None:
        if not self._boot_complete or getattr(self, "_inventory_refreshing", False):
            return
        self._inventory_refreshing = True
        if self.deferred_load:
            self._run_deferred_load(manual_refresh=True)
        else:
            self._run_state_refresh()

    @work(exclusive=True, group="manual-state-refresh", exit_on_error=False)
    async def _run_state_refresh(self) -> None:
        try:
            async with self._save_lock:
                states, errors = await self._load_engine_states(set(self.engine_pool))
            for key, error in errors.items():
                self.notify_status(f"Failed to refresh {key}: {error}", level="error")
            self._apply_refreshed_states(states)
            self._refresh_custom_views()
            if states and not errors:
                self.notify_status("Refreshed current TUI state.")
        except Exception:
            LOGGER.exception("State refresh failed")
            self.notify_status("State refresh failed; existing values were kept.", level="error")
        finally:
            self._inventory_refreshing = False

    def _apply_refreshed_states(self, states: dict) -> None:
        self._states.update(states)
        self._loaded_engines.update(states)
        for key in states:
            self._failed_engines.pop(key, None)

        for tab_idx in self.tabs:
            if tab_idx not in self._tab_data_ready and self._engines_for_tab(tab_idx).issubset(self._loaded_engines):
                self._apply_states_to_tab(tab_idx, self._states)

        changed_keys = set()
        for engine_key, refs in self._items_by_engine.items():
            if engine_key not in states:
                continue
            state = states[engine_key]
            for tab_idx, item_idx, item in refs:
                if item.type_ in ("action", "preset", "menu") or not item._initial_loaded:
                    continue
                uid = self._uid_engine_key(item)
                if self._has_pending_save_for_key(uid):
                    continue
                raw = self._lookup_state(state, item)
                if raw is not _STATE_MISSING:
                    value = item.deserialize(raw)
                    exists = True
                else:
                    exists = not self.hide_missing_items and item.default != "nil"
                    value = item.default if exists else item.value
                if item.serialize(item.value) != item.serialize(value) or item.exists_in_target != exists:
                    item.value = clone_value(value)
                    item.exists_in_target = exists
                    self._on_item_value_changed(item)
                    changed_keys.add(uid)
                self._committed[(tab_idx, item_idx)] = clone_value(value)

        for uid in changed_keys:
            self._bump_write_generation(uid)
        if states:
            self._refresh_all_ui()
            self._refresh_presets_ui()

    def _refresh_custom_views(self) -> None:
        for tab_idx in self._mounted_tabs:
            for view in self.query_one(f"#custom-body-{tab_idx}").children:
                try:
                    rich_views = list(view.query(CustomRichTabWidget))
                    if isinstance(view, CustomRichTabWidget):
                        rich_views.append(view)
                    if rich_views:
                        for rich_view in rich_views:
                            rich_view.update_content()
                    elif tab_idx == self._current_tab_index():
                        if callable(update := getattr(view, "update_content", None)):
                            update()
                        else:
                            view.refresh()
                except Exception:
                    LOGGER.exception("Unable to refresh custom view in tab %s", tab_idx)
                    self.notify_status(f"Could not refresh custom view in {self.tabs[tab_idx]}.", level="error")

    @work(exclusive=True, group="engine-boot", exit_on_error=False)
    async def run_deferred_boot(self, *, initial_tab: int | None = 0) -> None:
        try:
            preset_records = await asyncio.to_thread(self._read_user_presets)
        except OSError as exc:
            preset_records = []
            self.notify_status(f"Unable to load user presets: {exc}", level="error")
        self._apply_user_presets(preset_records)
        self._rebuild_indexes()

        need_now = self._engines_for_tab(initial_tab) if self.tabs else set()
        deferred = set(self.engine_pool) - need_now

        self._pending_engine_loads |= deferred
        async with asyncio.TaskGroup() as group:
            # Submit visible-tab work first; other targets need not wait for it.
            initial = group.create_task(self._load_engine_states(need_now))
            if deferred:
                group.create_task(self._load_engines_async(deferred))
            states, errors = await initial
            self._states.update(states)
            self._loaded_engines.update(states)
            self._failed_engines.update(errors)
            for ekey, error in errors.items():
                self.notify_status(f"Failed to load {ekey}: {error}", level="error")

            if initial_tab in self.tabs and self._engines_for_tab(initial_tab).issubset(self._loaded_engines):
                self._apply_states_to_tab(initial_tab, self._states)

            if self.tabs:
                await asyncio.sleep(0)
                self._populate_option_list(initial_tab)
                self._populated_tabs.add(initial_tab)
                self._activate_custom_views()
                self.call_after_refresh(self._queue_ready_tabs_for_warmup)

            self._mark_boot_complete_if_done()

    async def _load_engines_async(self, engine_keys: set[tuple[str, str]]) -> None:
        if not engine_keys:
            return
        states, errors = await self._load_engine_states(engine_keys)
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
        self._activate_custom_views()
        self._queue_ready_tabs_for_warmup()

    def _queue_ready_tabs_for_warmup(self) -> None:
        """Prepare hidden lists after first paint so a later tab switch is cheap."""
        current = self._current_tab_index()
        for tab_idx in self.tabs:
            if tab_idx == current:
                continue
            if not self.schema.get(tab_idx):
                continue
            if not self._engines_for_tab(tab_idx).issubset(self._loaded_engines):
                continue
            if tab_idx in self._populated_tabs and tab_idx not in self._tab_dirty:
                continue
            if tab_idx in self._tab_warmup_queued:
                continue
            try:
                self.query_one(f"#list-{tab_idx}", ConfigOptionList)
            except Exception:
                continue
            self._tab_warmup_queue.append(tab_idx)
            self._tab_warmup_queued.add(tab_idx)

        if self._tab_warmup_queue and not self._tab_warmup_scheduled:
            self._tab_warmup_scheduled = True
            self.call_after_refresh(self._warm_next_tab)

    def _warm_next_tab(self) -> None:
        self._tab_warmup_scheduled = False
        while self._tab_warmup_queue:
            tab_idx = self._tab_warmup_queue.popleft()
            self._tab_warmup_queued.discard(tab_idx)
            if tab_idx == self._current_tab_index():
                continue
            if not self._engines_for_tab(tab_idx).issubset(self._loaded_engines):
                continue
            if tab_idx in self._populated_tabs and tab_idx not in self._tab_dirty:
                continue
            if tab_idx not in self._tab_data_ready:
                self._apply_states_to_tab(tab_idx, self._states)
            self._populate_option_list(tab_idx)
            self._populated_tabs.add(tab_idx)
            self._tab_dirty.discard(tab_idx)
            break
        if self._tab_warmup_queue:
            self._tab_warmup_scheduled = True
            self.call_after_refresh(self._warm_next_tab)

    def require_boot_complete(self) -> bool:
        if getattr(self, "_boot_complete", True):
            if self._failed_engines:
                self.notify_status("A configuration backend failed to load; restart after fixing it.", level="error")
                return False
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
        with self.batch_update():
            try:
                ol = self.query_one(f"#list-{tab_idx}", ConfigOptionList)
            except Exception:
                return

            scroll_y = ol.scroll_y
            old_keys = getattr(ol, "_rendered_option_keys", {})

            if not maintain_highlight_id and ol.highlighted is not None:
                try:
                    maintain_highlight_id = ol.get_option_at_index(ol.highlighted).id
                except OptionDoesNotExist:
                    pass

            items = self.schema.get(tab_idx, [])
            custom_spec = self._custom_spec(tab_idx)
            option_groups = custom_spec.get("option_groups") if isinstance(custom_spec, dict) else None
            option_keys = {
                f"item_{tab_idx}_{idx}": (item.scope, item.key, item.parent_ref)
                for idx, item in enumerate(items)
            }
            selected_key = old_keys.get(maintain_highlight_id) if maintain_highlight_id else None
            if selected_key is None and ol.highlighted is not None:
                try:
                    selected_key = old_keys.get(ol.get_option_at_index(ol.highlighted).id)
                except OptionDoesNotExist:
                    pass
            # Schemas opt in to hiding missing settings. Other TUIs often show
            # settings whose config file has no entry yet.
            visible = {
                idx for idx, item in enumerate(items)
                if (
                    not option_groups or item.group in option_groups
                ) and (
                    item.type_ in ("menu", "action", "preset")
                    or not self.hide_missing_items
                    or item.exists_in_target
                )
            }
            for idx, item in enumerate(items):
                if item.type_ == "menu" and self.hide_missing_items and not any(
                    child.parent_ref in (item.uid, item.key) and child_idx in visible
                    for child_idx, child in enumerate(items)
                ):
                    visible.discard(idx)
            options = []
            current_group = None
            first_item_id = None

            parents = {itm.uid: idx for idx, itm in enumerate(items) if idx in visible and (itm.is_parent or itm.type_ == "menu")}
            for idx, itm in enumerate(items):
                if idx in visible and (itm.is_parent or itm.type_ == "menu"):
                    parents.setdefault(itm.key, idx)
            children_map = defaultdict(list)
            root_items = []
            parent_indices = {
                idx: parents[itm.parent_ref]
                for idx, itm in enumerate(items)
                if idx in visible and itm.parent_ref in parents and parents[itm.parent_ref] != idx
            }
            # Break malformed cycles so every tree has a visible root.
            checked = set()
            for start in tuple(parent_indices):
                chain = set()
                current = start
                while current in parent_indices and current not in checked:
                    if current in chain:
                        del parent_indices[current]
                        break
                    chain.add(current)
                    current = parent_indices[current]
                checked.update(chain)
            for orig_idx, itm in enumerate(items):
                if orig_idx not in visible:
                    continue
                if orig_idx in parent_indices:
                    children_map[parent_indices[orig_idx]].append((orig_idx, itm))
                else:
                    root_items.append((orig_idx, itm))

            # Clear only this tab's indent cache entries.
            prefix_key = f"item_{tab_idx}_"
            self._indent_cache = {
                k: v for k, v in self._indent_cache.items()
                if not k.startswith(prefix_key)
            }

            visited: set[int] = set()
            stack = [(idx, itm, [i == len(root_items) - 1]) for i, (idx, itm) in reversed(list(enumerate(root_items)))]

            def traverse(node_idx: int, node_item: ConfigItem, is_last_sibling_list: list[bool]):
                nonlocal current_group, first_item_id
                if node_idx in visited:
                    return
                visited.add(node_idx)

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
                    else first_item_id == opt_id
                )

                prefix = ""
                depth = len(is_last_sibling_list) - 1

                if depth > 0:
                    prefix = "  "
                    for is_last in is_last_sibling_list[1:-1]:
                        prefix += "  " if is_last else "│ "
                    prefix += "└─" if is_last_sibling_list[-1] else "├─"

                self._indent_cache[opt_id] = prefix

                options.append(
                    Option(
                        self._build_option(node_item, is_highlighted=is_hl, indent_prefix=prefix, tab_idx=tab_idx),
                        id=opt_id
                    )
                )

                if (node_item.is_parent or node_item.type_ == "menu") and node_item.expanded:
                    children = children_map.get(node_idx, [])

                    for i, (child_idx, child_item) in reversed(list(enumerate(children))):
                        is_last = (i == len(children) - 1)
                        stack.append((child_idx, child_item, is_last_sibling_list + [is_last]))

            while stack:
                traverse(*stack.pop())

            old_options = list(ol.options)
            if selected_key is not None:
                visible_ids = {option_keys[option.id]: option.id for option in options if not option.disabled}
                maintain_highlight_id = visible_ids.get(selected_key)
                if maintain_highlight_id is None and selected_key[2]:
                    parent = next((item for item in items if item.uid == selected_key[2] or item.key == selected_key[2]), None)
                    if parent is not None:
                        maintain_highlight_id = visible_ids.get((parent.scope, parent.key, parent.parent_ref))
                if maintain_highlight_id is None:
                    previous_index = ol.highlighted or 0
                    neighbors = sorted(enumerate(old_options), key=lambda pair: abs(pair[0] - previous_index))
                    maintain_highlight_id = next((visible_ids[old_keys[option.id]] for _, option in neighbors
                                                  if old_keys.get(option.id) in visible_ids), first_item_id)

            same_structure = (
                len(old_options) == len(options)
                and all(old.id == new.id and old_keys.get(old.id) == option_keys.get(new.id)
                        for old, new in zip(old_options, options))
            )
            if same_structure:
                for index, (old, new) in enumerate(zip(old_options, options)):
                    if old.prompt != new.prompt:
                        ol.replace_option_prompt_at_index(index, new.prompt)
            else:
                ol._restoring_options = True
                try:
                    ol.clear_options()
                    ol.add_options(options)
                    if maintain_highlight_id:
                        try:
                            ol.highlighted = ol.get_option_index(maintain_highlight_id)
                        except OptionDoesNotExist:
                            ol.highlighted = ol.get_option_index(first_item_id) if first_item_id else None
                    elif first_item_id:
                        ol.highlighted = ol.get_option_index(first_item_id)
                    ol.last_highlighted_id = (
                        ol.get_option_at_index(ol.highlighted).id if ol.highlighted is not None else None
                    )
                    ol.scroll_y = scroll_y
                finally:
                    ol._restoring_options = False

            positions = []
            count = 0
            for option in options:
                count += not option.disabled
                positions.append(count)
            ol._selectable_positions = positions

            ol._rendered_option_keys = option_keys

            self._tab_populated.add(tab_idx)
            self._tab_dirty.discard(tab_idx)

            if tab_idx == self._current_tab_index():
                self._update_file_link()
                self._update_current_help_panel()

            self.call_after_refresh(self._update_scroll_indicators)

    def _apply_deferred_tabs(
        self,
        tab_indices: list[int],
        states: dict,
        new_items: dict[int, list[ConfigItem]] | None = None
    ) -> None:
        if new_items and (self._save_tasks or self._save_timers or self._save_auth_pending or self._modal_active()):
            # Save and modal callbacks hold positional references. Finish them before
            # replacing lists; ordinary batch edits can be remapped below.
            self.set_timer(0.1, lambda: self._apply_deferred_tabs(tab_indices, states, new_items))
            return
        self._schema_dirty_counter += 1

        remapped = {}
        replaced_tabs = set()
        for tab_idx in tab_indices:
            if new_items and tab_idx in new_items:
                replaced_tabs.add(tab_idx)
                old_items = defaultdict(deque)
                for old_idx, old_item in enumerate(self.schema.get(tab_idx, [])):
                    identity = self._uid_engine_key(old_item)
                    old_items[identity].append((old_idx, old_item))
                    self._bump_write_generation(identity)
                for new_idx, new_item in enumerate(new_items[tab_idx]):
                    matches = old_items.get(self._uid_engine_key(new_item))
                    if not matches:
                        continue
                    old_idx, old_item = matches.popleft()
                    remapped[(tab_idx, old_idx)] = (tab_idx, new_idx)
                    refreshes_inventory = self.hide_missing_items
                    keep_value = not refreshes_inventory or (tab_idx, old_idx) in self.pending_commits
                    if old_item._initial_loaded and keep_value and not new_item.read_only:
                        new_item.value = clone_value(old_item.value)
                        new_item.initial_value = clone_value(old_item.initial_value)
                        new_item.exists_in_target = old_item.exists_in_target
                        new_item._initial_loaded = True
                    new_item.expanded = old_item.expanded
                self.schema[tab_idx] = new_items[tab_idx]

        if replaced_tabs:
            def remap(ref):
                return remapped.get(ref) if ref[0] in replaced_tabs else ref

            self._committed = {new_ref: value for ref, value in self._committed.items() if (new_ref := remap(ref)) is not None}
            self.pending_commits = {
                new_ref for ref in self.pending_commits
                if (new_ref := remap(ref)) is not None
                and not self.schema[new_ref[0]][new_ref[1]].read_only
            }
            for history in (self.undo_stack, self.redo_stack):
                transactions = [
                    [(*new_ref, old, new) for ti, ii, old, new in transaction if (new_ref := remap((ti, ii))) is not None]
                    for transaction in history
                ]
                history.clear()
                history.extend(transaction for transaction in transactions if transaction)

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
                # A late discovery refresh must not overwrite edits or reset
                # launch/committed baselines for already loaded settings.
                if item._initial_loaded:
                    continue
                engine_key = self._get_item_engine_info(item)
                state = states.get(engine_key, {})
                raw = self._lookup_state(state, item)

                if item.type_ in ("action", "preset", "menu"):
                    item.exists_in_target = True
                elif raw is not _STATE_MISSING:
                    item.exists_in_target = True
                    item.value = item.deserialize(raw)
                else:
                    item.exists_in_target = not self.hide_missing_items and item.default != "nil"

                if not item._initial_loaded:
                    item.initial_value = clone_value(item.value)
                    item._initial_loaded = True
                self._committed[(tab_idx, idx)] = clone_value(item.value)

            self._tab_data_ready.add(tab_idx)

            if tab_idx == current_idx:
                self._populate_option_list(tab_idx)
                self._populated_tabs.add(tab_idx)
                self._tab_dirty.discard(tab_idx)
            else:
                self._tab_dirty.add(tab_idx)

        # Deferred values changed after the initial rebuild – refresh the
        # preset match matrix so ratios reflect the newly loaded state.
        if hasattr(self, "_preset_matrix"):
            try:
                try:
                    global_cfg = [c for c in self._configurable_items if self._get_item_engine_info(c[2]) == self.default_engine_key]
                except Exception:
                    global_cfg = self._configurable_items
                self._preset_matrix.rebuild(global_cfg + self._preset_items)
            except Exception:
                pass

        if current_idx in tab_indices:
            if ol := self.current_option_list:
                self._update_pagination(ol)
        self._queue_ready_tabs_for_warmup()

    def _replace_dynamic_tabs(self, replacements: dict[int, list[ConfigItem]]) -> bool:
        """Reconcile a live inventory without invalidating edits and callbacks."""
        if self._save_tasks or self._save_timers or self._save_auth_pending or self._modal_active():
            return False

        remapped: dict[tuple[int, int], tuple[int, int]] = {}
        observed: dict[tuple[int, int], Any] = {}
        changed = False
        for tab_idx, incoming in replacements.items():
            existing = self.schema.get(tab_idx, [])
            old_by_identity = defaultdict(deque)
            for old_idx, item in enumerate(existing):
                old_by_identity[(item.scope, item.key, item.parent_ref)].append((old_idx, item))

            merged = []
            for new_idx, fresh in enumerate(incoming):
                matches = old_by_identity.get((fresh.scope, fresh.key, fresh.parent_ref))
                if not matches:
                    merged.append(fresh)
                    changed = True
                    continue
                old_idx, item = matches.popleft()
                remapped[(tab_idx, old_idx)] = (tab_idx, new_idx)
                if old_idx != new_idx:
                    changed = True
                pending = (tab_idx, old_idx) in self.pending_commits
                if not pending:
                    item.value = clone_value(fresh.value)
                    item.initial_value = clone_value(fresh.initial_value)
                    item._initial_loaded = fresh._initial_loaded
                    item.exists_in_target = fresh.exists_in_target
                    observed[(tab_idx, new_idx)] = clone_value(fresh.value)
                item.label = fresh.label
                item.default = clone_value(fresh.default)
                item.options = list(fresh.options)
                item.hints = list(fresh.hints)
                item.group = fresh.group
                item.extended_help = fresh.extended_help
                item.confirm_message = fresh.confirm_message
                item.warning_msg = fresh.warning_msg
                item.popup_message = fresh.popup_message
                item.read_only = fresh.read_only
                item.expanded = item.expanded if item.is_parent else fresh.expanded
                merged.append(item)

            if any(old_by_identity.values()):
                changed = True
            self.schema[tab_idx] = merged

        replaced_tabs = set(replacements)

        def remap(ref):
            return remapped.get(ref) if ref[0] in replaced_tabs else ref

        self._committed = {
            new_ref: value for ref, value in self._committed.items()
            if (new_ref := remap(ref)) is not None
        }
        self._committed.update(observed)
        for tab_idx, incoming in replacements.items():
            for idx, item in enumerate(self.schema[tab_idx]):
                self._committed.setdefault((tab_idx, idx), clone_value(item.value))
        self.pending_commits = {
            new_ref for ref in self.pending_commits
            if (new_ref := remap(ref)) is not None
        }
        for history in (self.undo_stack, self.redo_stack):
            transactions = [
                [(*new_ref, old, new) for ti, ii, old, new in transaction
                 if (new_ref := remap((ti, ii))) is not None]
                for transaction in history
            ]
            history.clear()
            history.extend(transaction for transaction in transactions if transaction)
        if changed:
            self._schema_dirty_counter += 1
        return True

    def _refresh_single_ui(self, tab_idx: int, item_idx: int, item: ConfigItem) -> None:
        if tab_idx not in self._tab_populated:
            self._tab_dirty.add(tab_idx)
            return
        if tab_idx != self._current_tab_index():
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
                self._build_option(item, is_hl, prefix, tab_idx=tab_idx)
            )

            # Automatically refresh parent subheadings / menu folders up the hierarchy
            p_ref = getattr(item, "parent_ref", None)
            if p_ref:
                items_in_tab = self.schema.get(tab_idx, [])
                visited_parents = set()
                curr_ref = p_ref
                while curr_ref and curr_ref not in visited_parents:
                    visited_parents.add(curr_ref)
                    parent_found = None
                    p_i_idx = -1
                    for i, itm in enumerate(items_in_tab):
                        if itm.key == curr_ref or self._get_item_uid(itm) == curr_ref:
                            parent_found = itm
                            p_i_idx = i
                            break

                    if parent_found and p_i_idx >= 0:
                        p_opt_id = f"item_{tab_idx}_{p_i_idx}"
                        try:
                            p_idx = ol.get_option_index(p_opt_id)
                            p_is_hl = (ol.last_highlighted_id == p_opt_id)
                            p_prefix = self._indent_cache.get(p_opt_id, "")
                            ol.replace_option_prompt_at_index(
                                p_idx,
                                self._build_option(parent_found, p_is_hl, p_prefix, tab_idx=tab_idx)
                            )
                        except OptionDoesNotExist:
                            pass
                        curr_ref = getattr(parent_found, "parent_ref", None)
                    else:
                        break

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
        current_tab = self._current_tab_index()
        for tab_idx in self.schema.keys():
            if tab_idx == current_tab and tab_idx in self._tab_populated:
                self._populate_option_list(tab_idx)
            elif tab_idx in self._tab_populated:
                self._tab_dirty.add(tab_idx)
            else:
                self._tab_dirty.add(tab_idx)
        self._queue_ready_tabs_for_warmup()

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
        except Exception:
            pass

        # Switching AUTO -> BATCH: flush pending debounce timers into batch queue.
        if not new:
            for (ti, ii), timer in list(self._save_timers.items()):
                timer.stop()
                self._bump_write_generation_for_item(self.schema[ti][ii])
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

    def _get_tabs_total_width(self) -> int:
        try:
            tabs_widget = self.query_one(Tabs)
            tab_list = list(tabs_widget.query(Tab))
            if not tab_list:
                return 0
            total = 0
            for tab in tab_list:
                if tab.region.width > 0:
                    total += tab.region.width
                else:
                    total += len(str(tab.label)) + 2
            return total
        except Exception:
            return 0

    def check_tab_overflow(self) -> None:
        if not self._cached_tabs_container or not self._cached_tab_left or not self._cached_tab_right:
            return

        try:
            container = self._cached_tabs_container
            bar = getattr(self, "_cached_tab_bar", None)
            left = self._cached_tab_left
            right = self._cached_tab_right

            tabs_w = self._get_tabs_total_width()
            bar_w = bar.size.width if bar else 0
            cont_w = container.size.width

            if bar_w > 0 and tabs_w > 0:
                has_overflow = tabs_w > cont_w
            else:
                has_overflow = container.max_scroll_x > 0

            if has_overflow:
                left.update(" ◀ " if container.scroll_x > 0.5 else "   ")
                right.update(" ▶ " if container.scroll_x < (container.max_scroll_x - 0.5) else "   ")
                if container.styles.align != ("left", "middle"):
                    container.styles.align = ("left", "middle")
            else:
                left.update("")
                right.update("")
                if container.scroll_x > 0:
                    container.scroll_to(x=0, animate=False)
                if container.styles.align != ("center", "middle"):
                    container.styles.align = ("center", "middle")

        except Exception:
            pass

    def scroll_tab_into_view(self, tab_widget: Tab) -> None:
        if not self._cached_tabs_container:
            return

        try:
            container = self._cached_tabs_container
            tabs = self.query_one(Tabs)

            if tab_widget.region.width == 0:
                return

            tab_offset_x = tab_widget.region.x - tabs.region.x
            tab_width = tab_widget.region.width
            container_width = container.size.width
            current_scroll = container.scroll_x

            # If tab starts before current viewport, scroll left to show it cleanly with margin
            if tab_offset_x < current_scroll:
                target_x = max(0.0, float(tab_offset_x - 1))
                container.scroll_to(x=target_x, animate=False)
            # If tab ends after current viewport, scroll right to show it cleanly with margin
            elif tab_offset_x + tab_width > current_scroll + container_width:
                target_x = min(float(container.max_scroll_x), float(tab_offset_x + tab_width - container_width + 1))
                container.scroll_to(x=target_x, animate=False)

            self.check_tab_overflow()
        except Exception:
            pass

    def on_resize(self, event: events.Resize) -> None:
        self.check_tab_overflow()
        try:
            tabs = self.query_one(Tabs)
            if tabs.active_tab:
                self.scroll_tab_into_view(tabs.active_tab)
        except Exception:
            pass

    # =========================================================================
    # WATCHERS
    # =========================================================================
    @staticmethod
    def _target_fingerprint(path: Path) -> tuple[int, int, int] | None | _TARGET_UNREADABLE:
        """Return a replacement-safe file identity for external-change polling."""
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            LOGGER.warning("Unable to stat target file %s: %s", path, exc)
            return _TARGET_UNREADABLE
        return (stat.st_mtime_ns, stat.st_size, stat.st_ino)

    async def watch_target_file(self) -> None:
        if not self._boot_complete:
            return
        try:
            changed_any = False

            for e_key in self.engine_pool:
                if e_key in self._failed_engines:
                    continue
                engine = self.engine_pool[e_key]
                if not engine.target_path:
                    continue

                path = Path(engine.target_path).expanduser().resolve()

                fingerprint = await asyncio.to_thread(self._target_fingerprint, path)
                previous = self.last_target_mtimes.get(e_key)

                if fingerprint is _TARGET_UNREADABLE:
                    continue

                if not self._initial_target_mtimes_set:
                    self.last_target_mtimes[e_key] = fingerprint
                    continue

                if fingerprint == previous:
                    continue

                items_for_engine = [
                    (t_idx, i_idx, item)
                    for t_idx, i_idx, item in self._items_by_engine.get(e_key, [])
                    if item.type_ not in ("action", "preset", "menu")
                ]
                items_by_uek: defaultdict[str, list[tuple[int, int, ConfigItem]]] = defaultdict(list)
                for t_idx, i_idx, item in items_for_engine:
                    items_by_uek[self._uid_engine_key(item)].append((t_idx, i_idx, item))

                if fingerprint is None:
                    if previous is None:
                        continue
                    # Keep the missing fingerprint so a recreated file is
                    # recognized even if it gets an older timestamp.
                    accepted = True
                    for uek, grouped_items in items_by_uek.items():
                        if self._has_pending_save_for_key(uek):
                            accepted = False
                            continue
                        group_changed = False
                        for _t_idx, _i_idx, item in grouped_items:
                            if item.exists_in_target:
                                item.exists_in_target = False
                                self._on_item_value_changed(item)
                                group_changed = True
                        if group_changed:
                            self._bump_write_generation(uek)
                            changed_any = True
                    if accepted:
                        self.last_target_mtimes[e_key] = None
                    continue

                reload_generations = {
                    uek: self._write_generation.get(uek, 0)
                    for uek in items_by_uek
                }
                try:
                    async with self._save_lock:
                        new_state = await self._run_save_io(engine.load_state)
                except Exception as exc:
                    # Do not consume the fingerprint when parsing/loading
                    # failed; the next poll can retry the same file.
                    LOGGER.warning("Unable to reload %s: %s", path, exc)
                    continue

                accepted = True
                for uek, grouped_items in items_by_uek.items():
                    # A queued or running local save owns this logical setting;
                    # let it finish instead of applying an older disk snapshot.
                    if (
                        self._write_generation.get(uek, 0) != reload_generations[uek]
                        or self._has_pending_save_for_key(uek)
                    ):
                        accepted = False
                        continue

                    group_changed = False
                    for _t_idx, _i_idx, item in grouped_items:
                        raw = self._lookup_state(new_state, item)
                        if raw is not _STATE_MISSING:
                            new_val = item.deserialize(raw)
                            expected_exists = True
                        else:
                            expected_exists = not self.hide_missing_items and item.default != "nil"
                            new_val = item.default if expected_exists else item.value

                        value_changed = item.serialize(item.value) != item.serialize(new_val)
                        existence_changed = item.exists_in_target != expected_exists
                        if value_changed or existence_changed:
                            item.value = new_val
                            item.exists_in_target = expected_exists
                            self._on_item_value_changed(item)
                            group_changed = True
                        self._committed[(_t_idx, _i_idx)] = clone_value(new_val)

                    if group_changed:
                        self._bump_write_generation(uek)
                        changed_any = True

                # Consume the fingerprint only after every item has been
                # reconciled successfully; failures above must be retried.
                if accepted:
                    self.last_target_mtimes[e_key] = fingerprint

            if not self._initial_target_mtimes_set:
                self._initial_target_mtimes_set = True
                return

            if changed_any:
                self._schema_dirty_counter += 1
                self._refresh_all_ui()
                self.notify_status("Config modified externally. Refreshed UI.")

        except Exception:
            LOGGER.exception("Unexpected error while watching target files")

    async def update_telemetry(self) -> None:
        if self.telemetry_engine:
            try:
                msg = await self._run_save_io(self.telemetry_engine.get_telemetry)
                banner = self.query_one("#telemetry-banner", Label)
                banner.update(msg)
            except Exception:
                LOGGER.exception("Telemetry update failed")

    async def watch_presets_dir(self) -> None:
        if (
            not self._boot_complete
            or not self.enable_user_presets
            or not hasattr(self, "user_presets_dir")
            or self._modal_active()
        ):
            return

        try:
            if not hasattr(self, "_preset_mtimes"):
                self._preset_mtimes = {}

            def check_mtimes():
                result = {}
                for file_path in self.user_presets_dir.iterdir():
                    if not file_path.name.endswith(".json"):
                        continue
                    stat = file_path.stat()
                    result[file_path.name] = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
                return result

            current_mtimes = await asyncio.to_thread(check_mtimes)
            changed_any = False

            for fname, mtime in current_mtimes.items():
                if self._preset_mtimes.get(fname) != mtime:
                    changed_any = True
                    break

            if set(self._preset_mtimes.keys()) - set(current_mtimes.keys()):
                changed_any = True

            if changed_any or not getattr(self, "_initial_presets_mtime_set", False):
                preset_records = await asyncio.to_thread(self._read_user_presets)
                if self._modal_active():
                    return
                self._apply_user_presets(preset_records)
                self._rebuild_indexes()
                self._preset_mtimes = current_mtimes
                self._initial_presets_mtime_set = True
                self._schema_dirty_counter += 1
                self._refresh_all_ui()

        except Exception:
            LOGGER.exception("Unexpected error while watching preset files")

    async def watch_theme_file(self) -> None:
        if not self.theme_path:
            return

        try:
            stat_info = await asyncio.to_thread(self.theme_path.stat)
            current_mtime = stat_info.st_mtime
            fingerprint = (stat_info.st_mtime_ns, stat_info.st_size, stat_info.st_ino)

            if fingerprint != self._last_theme_fingerprint:
                new_theme = await asyncio.to_thread(load_matugen_json, self.theme_path)

                if new_theme is not None:
                    self._schema_dirty_counter += 1
                    self.last_theme_mtime = current_mtime
                    self._last_theme_fingerprint = fingerprint

                    self.theme_colors.update(new_theme)
                    self.apply_theme_to_engine()

                    self._refresh_all_ui()

                    for shortcut in self.query(Shortcut):
                        shortcut.refresh()

                    for file_link in self.query(FileLink):
                        file_link.refresh()

                    self._update_footer_legend()

        except Exception:
            LOGGER.exception("Unexpected error while watching theme file")

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
        # Invalidate render cache so next _build_option picks up new palette.
        self._invalidate_theme_cache()

    def _update_file_link(self, item: ConfigItem | None = None) -> None:
        try:
            if item is None:
                cur_tab = self._current_tab_index()
                if cur_tab is not None and (ol := self.current_option_list):
                    if ol.highlighted is not None and ol.highlighted < ol.option_count:
                        opt = ol.get_option_at_index(ol.highlighted)
                        if opt and opt.id:
                            parsed = self._get_item_from_id(opt.id)
                            if parsed:
                                item = parsed[2]
                    if item is None:
                        items = self.schema.get(cur_tab, [])
                        if items:
                            item = items[0]

            engine_key = self._get_item_engine_info(item) if item is not None else self.default_engine_key
            # Tab activation can precede boot. Never construct a backend on
            # the UI thread just to display its file link.
            path = self.engine_pool[engine_key].target_path if engine_key in self._loaded_engines else ""
            self.query_one("#file-link", FileLink).path = path
        except Exception:
            pass

    # =========================================================================
    # TAB HANDLING
    # =========================================================================
    @on(Tabs.TabActivated)
    async def handle_tab_activated(self, event: Tabs.TabActivated) -> None:
        try:
            idx = int(event.tab.id.split("-")[-1])
            await self._ensure_option_list(idx)
            await self._ensure_custom_body(idx)
            if self.query_one(Tabs).active != event.tab.id:
                return
            with self.batch_update():
                self.query_one(ContentSwitcher).current = f"tab-{idx}"
                self._activate_custom_views()
                self.scroll_tab_into_view(event.tab)

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
                        host = self.query_one(f"#custom-body-{idx}")
                        for view in host.children:
                            if not view.query(CustomRichTabWidget) and not isinstance(view, CustomRichTabWidget):
                                if callable(update := getattr(view, "update_content", None)):
                                    update()
                        for widget in host.query(Widget):
                            if widget.can_focus and not widget.disabled:
                                widget.focus()
                                break
                    except Exception:
                        pass

                    self._update_pagination(None)

                self._focus_search_target()
                self._update_scroll_indicators()
                self.check_tab_overflow()
                self._update_file_link()
                self._update_current_help_panel()

        except Exception:
            LOGGER.exception("Unable to activate tab")
            self.notify_status("Unable to open tab.", level="error")

    @on(events.Click, "#tab-left")
    def scroll_tabs_left(self, event: events.Click) -> None:
        event.stop()
        if self._cached_tabs_container and self._cached_tabs_container.scroll_x > 0:
            target = max(0.0, float(self._cached_tabs_container.scroll_x - 20))
            self._cached_tabs_container.scroll_to(x=target, animate=False)
            self.check_tab_overflow()

    @on(events.Click, "#tab-right")
    def scroll_tabs_right(self, event: events.Click) -> None:
        event.stop()
        if self._cached_tabs_container and self._cached_tabs_container.scroll_x < self._cached_tabs_container.max_scroll_x:
            target = min(float(self._cached_tabs_container.max_scroll_x), float(self._cached_tabs_container.scroll_x + 20))
            self._cached_tabs_container.scroll_to(x=target, animate=False)
            self.check_tab_overflow()

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
                    help_text += f"> **{_ICON_WARNING} WARNING:** {item.warning_msg}\n"

                help_text += item.extended_help or f"**{_md_escape(item.label)}**\nNo extended documentation available."

                if getattr(md, "_dusky_help_text", None) != help_text:
                    md.update(help_text)
                    md._dusky_help_text = help_text

        except Exception:
            pass

    def _update_current_help_panel(self) -> None:
        try:
            if not self.query_one("#content-area").has_class("-show-help"):
                return
            ol = self.current_option_list
            if ol and ol.highlighted is not None:
                parsed = self._get_item_from_id(ol.get_option_at_index(ol.highlighted).id)
                if parsed:
                    self._update_help_panel(parsed[2])
                    return
            md = self.query_one("#help-markdown", Markdown)
            neutral = "Select an item to view documentation."
            if getattr(md, "_dusky_help_text", None) != neutral:
                md.update(neutral)
                md._dusky_help_text = neutral
        except (NoMatches, OptionDoesNotExist):
            pass

    @on(OptionList.OptionHighlighted)
    def handle_option_highlight(self, event: OptionList.OptionHighlighted) -> None:
        ol = event.option_list

        if not isinstance(ol, ConfigOptionList) or not event.option_id:
            return

        restored = getattr(ol, "_restored_option", None) is event.option
        if restored:
            ol._restored_option = None

        try:
            if (
                ol is not self.current_option_list or ol.highlighted != event.option_index
                or ol.get_option_at_index(event.option_index) is not event.option
            ):
                return
        except OptionDoesNotExist:
            return

        parsed = self._get_item_from_id(event.option_id)

        if parsed:
            self._update_help_panel(parsed[2])
            self._update_file_link(parsed[2])

        last_id = ol.last_highlighted_id

        if last_id and last_id != event.option_id:
            old_parsed = self._get_item_from_id(last_id)

            if old_parsed:
                try:
                    old_idx = ol.get_option_index(last_id)
                    old_prefix = self._indent_cache.get(last_id, "")
                    ol.replace_option_prompt_at_index(
                        old_idx,
                        self._build_option(old_parsed[2], False, old_prefix, tab_idx=old_parsed[0])
                    )
                except OptionDoesNotExist:
                    pass

        if parsed:
            try:
                curr_idx = ol.get_option_index(event.option_id)
                curr_prefix = self._indent_cache.get(event.option_id, "")

                ol.replace_option_prompt_at_index(
                    curr_idx,
                    self._build_option(parsed[2], True, curr_prefix, tab_idx=parsed[0])
                )

                ol.last_highlighted_id = event.option_id

                if not restored:
                    if hasattr(ol, "scroll_to_highlight"):
                        ol.scroll_to_highlight()
                    elif hasattr(ol, "scroll_to_option") and curr_idx is not None:
                        ol.scroll_to_option(curr_idx)
                    self._ensure_header_visible(ol, curr_idx)

            except OptionDoesNotExist:
                pass

        self._update_pagination(ol)

    def _ensure_header_visible(self, ol: ConfigOptionList, curr_idx: int | None) -> None:
        if ol is None or curr_idx is None or ol.option_count == 0:
            return

        # Only adjust if the option is at the very top of the visible window
        # and has an immediate disabled group header 1 row above it
        if curr_idx > 0 and int(ol.scroll_y) == curr_idx:
            try:
                prev_opt = ol.get_option_at_index(curr_idx - 1)
                if getattr(prev_opt, "disabled", False):
                    ol.scroll_y = curr_idx - 1
            except Exception:
                pass

    def _update_pagination(self, ol: ConfigOptionList) -> None:
        try:
            counter = self.query_one("#pos-counter", Label)
            if ol and ol.option_count > 0:
                curr_idx = ol.highlighted if ol.highlighted is not None else 0
                positions = ol._selectable_positions
                total_selectable = positions[-1] if positions else 0
                selectable_idx = positions[curr_idx] if positions else 0

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
            app_footer.status_level = level
            app_footer.status_msg = msg

            if self._status_timer:
                self._status_timer.stop()

            def _clear_status() -> None:
                try:
                    # Re-query – the footer may have been remounted since the
                    # timer was scheduled (e.g., after a screen change).
                    self.query_one(AppFooter).status_msg = ""
                except Exception:
                    try:
                        app_footer.status_msg = ""
                    except Exception:
                        pass

            self._status_timer = self.set_timer(3, _clear_status)
        except Exception:
            pass

    def play_reset_sound(self) -> None:
        global _AUDIO_PLAYER_CACHE

        if self._action_shutdown_started:
            return

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

                async def play() -> None:
                    proc = None
                    spawn = asyncio.create_task(asyncio.create_subprocess_exec(
                        *cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    ))
                    try:
                        proc = await asyncio.shield(spawn)
                        self._action_procs.add(proc)
                        await proc.wait()
                    except OSError as error:
                        LOGGER.debug("Unable to play notification sound: %s", error)
                    finally:
                        self._track_action_cleanup(asyncio.create_task(
                            self._cleanup_action_resources(proc, [], spawn)
                        ))

                self._track_action_task(asyncio.create_task(play()))

    # =========================================================================
    # WRITE GENERATION / AUTOSAVE SAFETY
    # =========================================================================
    def _bump_write_generation(self, uid: str) -> int:
        gen = self._write_generation.get(uid, 0) + 1
        self._write_generation[uid] = gen
        if self.custom_views and self.is_mounted:
            for view in self.query(CustomRichTabWidget):
                view.invalidate_content()
        return gen

    def _bump_write_generation_for_item(self, item: ConfigItem) -> int:
        return self._bump_write_generation(self._uid_engine_key(item))

    def _cancel_autosave_ref(self, tab_idx: int, item_idx: int) -> None:
        k = (tab_idx, item_idx)

        timer = self._save_timers.pop(k, None)
        if timer:
            timer.stop()

        self._pending_autosave_args.pop(k, None)

    def _cancel_autosave_for_transaction(self, transaction: list[tuple[int, int, Any, Any]]) -> None:
        # Collect per-file UID keys to avoid cross-file cancellation.
        uid_engines: set[str] = set()
        # Also keep simple UID set for legacy, but filter by engine when cancelling duplicates.
        for t, i, o, n in transaction:
            self._cancel_autosave_ref(t, i)
            item = self._get_schema_item(t, i)
            if item:
                uid_engines.add(self._uid_engine_key(item))

        # Also cancel duplicate UID views that share the SAME engine/file.
        # Previously this cancelled ALL duplicates regardless of file, causing per-game cross-contamination.
        seen = set()
        for t, i, o, n in transaction:
            src_item = self._get_schema_item(t, i)
            if not src_item:
                continue
            src_uid = self._get_item_uid(src_item)
            src_eng = self._get_item_engine_info(src_item)
            for dup_t, dup_i, dup_itm in self._items_by_uid.get(src_uid, []):
                if (dup_t, dup_i) in seen:
                    continue
                if self._get_item_engine_info(dup_itm) != src_eng:
                    continue
                seen.add((dup_t, dup_i))
                self._cancel_autosave_ref(dup_t, dup_i)

        for uek in uid_engines:
            self._bump_write_generation(uek)

    def _apply_transaction_to_ram(self, transaction: list[tuple[int, int, Any, Any]], undo: bool = False) -> None:
        uid_engines: set[str] = set()

        for t, i, o, n in transaction:
            item = self._get_schema_item(t, i)
            if not item:
                continue

            item.value = o if undo else n
            item.exists_in_target = True
            self._on_item_value_changed(item)
            self._refresh_single_ui(t, i, item)
            uid_engines.add(self._uid_engine_key(item))

        self._schema_dirty_counter += 1

        for uek in uid_engines:
            self._bump_write_generation(uek)

    def _revert_transaction(self, transaction: list[tuple[int, int, Any, Any]]) -> None:
        self._apply_transaction_to_ram(transaction, undo=True)
        for tab_idx, item_idx, _, _ in transaction:
            self._sync_pending(tab_idx, item_idx, self.schema[tab_idx][item_idx])

        if self.undo_stack and list(self.undo_stack[-1]) == list(transaction):
            self.undo_stack.pop()

        self._refresh_presets_ui()

    def _reset_trigger_ui(self, item: ConfigItem) -> None:
        uid = self._get_item_uid(item)
        src_eng = self._get_item_engine_info(item)
        self._schema_dirty_counter += 1

        for t_idx, i_idx, other_item in self._items_by_uid.get(uid, []):
            if self._get_item_engine_info(other_item) != src_eng:
                continue
            other_item.value = item.default
            self._committed[(t_idx, i_idx)] = clone_value(item.default)
            self.pending_commits.discard((t_idx, i_idx))
            self._on_item_value_changed(other_item)
            self._refresh_single_ui(t_idx, i_idx, other_item)

        self._bump_write_generation(self._uid_engine_key(item))
        self._refresh_presets_ui()

    def _apply_observed_systemd_state(self, item: ConfigItem, actual: str, expected: str) -> None:
        """Reconcile every view of one unit after an uncertain systemctl write."""
        uid = self._get_item_uid(item)
        engine_key = self._get_item_engine_info(item)
        for tab_idx, item_idx, other in self._items_by_uid.get(uid, ()):
            if self._get_item_engine_info(other) != engine_key:
                continue
            observed = other.deserialize(actual)
            self._committed[(tab_idx, item_idx)] = clone_value(observed)
            if other.serialize(other.value) == expected:
                other.value = observed
            other.exists_in_target = True
            self._sync_pending(tab_idx, item_idx, other)
            self._on_item_value_changed(other)
            self._refresh_single_ui(tab_idx, item_idx, other)
        self._bump_write_generation(self._uid_engine_key(item))
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
        if not self.require_boot_complete():
            return
        # Every presentation of the same setting belongs to one transaction.
        # Keep different target files isolated and write each setting once.
        expanded = {(t, i): (t, i, clone_value(old), clone_value(new)) for t, i, old, new in transaction}
        for t, i, old, new in transaction:
            item = self.schema[t][i]
            engine_key = self._get_item_engine_info(item)
            for dt, di, duplicate in self._items_by_uid.get(item.uid, ()):
                if self._get_item_engine_info(duplicate) == engine_key:
                    expanded.setdefault((dt, di), (dt, di, clone_value(duplicate.value), clone_value(new)))
        transaction = list(expanded.values())
        self._schema_dirty_counter += 1
        self._cancel_autosave_for_transaction(transaction)

        for t, i, o, n in transaction:
            item = self.schema[t][i]
            item.value = clone_value(o if action_type == "undo" else n)
            item.exists_in_target = True
            self._on_item_value_changed(item)
            self._sync_pending(t, i, item)
            self._refresh_single_ui(t, i, item)

        uid_engines: set[str] = set()
        for t, i, o, n in transaction:
            item = self._get_schema_item(t, i)
            if item:
                uid_engines.add(self._uid_engine_key(item))

        for uek in uid_engines:
            self._bump_write_generation(uek)

        # Keep the edit generation that belongs to this transaction.  A
        # later edit can legitimately return to the same serialized value;
        # comparing values alone would let an older failed batch roll it back.
        transaction_generations = {
            uek: self._write_generation.get(uek, 0) for uek in uid_engines
        }

        if self.auto_save:
            def finalize_transaction(batch_success: bool):
                successful_parts = []
                failed_parts = []

                for t, i, o, n in transaction:
                    if (t, i) in self.pending_commits:
                        failed_parts.append((t, i, o, n))

                        item = self.schema[t][i]
                        expected = o if action_type == "undo" else n
                        uek = self._uid_engine_key(item)
                        generation_unchanged = (
                            self._write_generation.get(uek, 0)
                            == transaction_generations.get(uek, -1)
                        )
                        if (
                            generation_unchanged
                            and item.serialize(item.value) == item.serialize(expected)
                        ):
                            item.value = n if action_type == "undo" else o
                            self._on_item_value_changed(item)
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

    def _is_unchanged_submission(self, item: ConfigItem, new_val: Any) -> bool:
        """Narrow guard for input submission paths: skip ordinary settings whose
        accepted value already matches, avoiding undo/redo/save churn."""
        if item.type_ in ("action", "preset", "menu"):
            return False
        if is_trigger_item(item):
            return False
        if not bool(getattr(item, "exists_in_target", False)):
            return False
        try:
            if item.serialize(item.value) != item.serialize(new_val):
                return False
        except Exception:
            return False
        try:
            src_eng = self._get_item_engine_info(item)
        except Exception:
            return False
        try:
            for _, _, other in self._items_by_uid.get(self._get_item_uid(item), ()):
                if other is item:
                    continue
                try:
                    if self._get_item_engine_info(other) != src_eng:
                        continue
                except Exception:
                    return False
                try:
                    if other.serialize(other.value) != other.serialize(new_val):
                        return False
                except Exception:
                    return False
        except Exception:
            return False
        return True

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
        if item.read_only:
            self.notify_status(f"{item.label} cannot be enabled or disabled directly.", level="warning")
            return
        if not item.exists_in_target and self.hide_missing_items:
            self.notify_status(f"{item.label} is no longer available. Press F5 to refresh.", level="warning")
            return
        if not self.require_boot_complete():
            return
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
        if item.read_only:
            return False
        old_val = clone_value(item.value)
        self._schema_dirty_counter += 1

        item_uid = self._get_item_uid(item)
        src_eng = self._get_item_engine_info(item)
        transaction = [(tab_idx, item_idx, old_val, new_val)]

        for t_idx, i_idx, other in self._items_by_uid.get(item_uid, []):
            if other is item:
                continue
            if self._get_item_engine_info(other) != src_eng:
                continue
            transaction.append((t_idx, i_idx, other.value, new_val))

        if not is_undo and record_undo:
            self.undo_stack.append(transaction)
            self.redo_stack.clear()

        # Cancel stale autosaves before mutating.
        self._cancel_autosave_for_transaction(transaction)

        item.value = new_val
        item.exists_in_target = True
        self._on_item_value_changed(item)

        # Sync duplicate items that share the SAME file/engine (e.g. same setting shown in two tabs).
        # Previously this synced across ALL files with same UID, which broke per-game isolation.
        for t_idx, i_idx, other_item in self._items_by_uid.get(item_uid, []):
            if other_item is item:
                continue
            if self._get_item_engine_info(other_item) != src_eng:
                continue
            other_item.value = new_val
            other_item.exists_in_target = True
            self._on_item_value_changed(other_item)
            self._refresh_single_ui(t_idx, i_idx, other_item)

        val_str = item.serialize(new_val)

        if self.auto_save and not batch_mode:
            if not self._save_tasks and self._save_auth_pending == 0:
                self._save_failure_pending = False
            k = (tab_idx, item_idx)
            gen = self._bump_write_generation(self._uid_engine_key(item))

            self._save_timers[k] = self.set_timer(
                0.25,
                lambda ti=tab_idx, ii=item_idx, it=item, vs=val_str, ov=old_val, g=gen, tx=transaction:
                    self._start_save_task(self._do_auto_save_async(ti, ii, it, vs, ov, g, tx, False))
            )

            self._pending_autosave_args[k] = (item, val_str, old_val)

        else:
            for ti, ii, _, _ in transaction:
                self._sync_pending(ti, ii, self.schema[ti][ii])

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
    def _start_save_task(self, coroutine: Any) -> asyncio.Task[Any]:
        task = asyncio.create_task(coroutine)
        self._save_tasks.add(task)
        task.add_done_callback(self._on_save_task_done)
        return task

    def _on_save_task_done(self, task: asyncio.Task[Any]) -> None:
        self._save_tasks.discard(task)
        getattr(self, "_save_task_keys", {}).pop(task, None)
        if task.cancelled():
            self._save_failure_pending = True
        else:
            try:
                if task.exception() is not None:
                    self._save_failure_pending = True
            except Exception:
                self._save_failure_pending = True
            # Some engines select a record or normalize dependent values on save.
            # Refresh once this task no longer marks its own setting as pending.
            initialized_items = getattr(self.engine_pool, "initialized_items", None)
            engines = initialized_items() if callable(initialized_items) else self.engine_pool.items()
            states = {
                key: engine.cache for key, engine in engines
                if getattr(engine, "refresh_after_write", False)
            }
            if states:
                self._apply_refreshed_states(states)
        self._maybe_finish_quit()

    async def _run_save_io(self, func: Any, /, *args: Any, **kwargs: Any) -> Any:
        """Run blocking backend I/O; drain it on cancellation and before shutdown."""
        worker = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))
        workers = getattr(self, "_io_workers", None)
        if workers is None:
            workers = self._io_workers = set()
        workers.add(worker)
        worker.add_done_callback(workers.discard)
        try:
            await asyncio.wait({worker})
            return worker.result()
        except asyncio.CancelledError:
            # Repeated cancellation must not cancel the worker or release the
            # caller's save lock while its blocking write is still running.
            while not worker.done():
                try:
                    await asyncio.wait({worker})
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            try:
                worker.result()
            except Exception:
                LOGGER.exception("Save I/O failed while draining cancellation")
            finally:
                raise

    def _maybe_finish_quit(self) -> None:
        if (
            self._quit_after_save
            and not self._save_tasks
            and self._save_auth_pending == 0
            and not self._save_timers
        ):
            self._quit_after_save = False
            if self._save_failure_pending or self.pending_commits:
                self._save_failure_pending = False
                self.notify_status("Quit aborted: pending changes were not fully saved.", level="warning")
            else:
                self.exit()

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
        uek = self._uid_engine_key(item)
        # A superseded task must not remove a newer edit's debounce timer.
        if self._write_generation.get(uek) != generation:
            return
        self._save_timers.pop((tab_idx, item_idx), None)
        self._pending_autosave_args.pop((tab_idx, item_idx), None)
        current_task = asyncio.current_task()
        if current_task is not None:
            task_keys = getattr(self, "_save_task_keys", None)
            if task_keys is None:
                task_keys = self._save_task_keys = {}
            task_keys.setdefault(current_task, set()).add(uek)

        if force:
            if (
                self._write_generation.get(uek) != generation
                or item.serialize(item.value) != item.serialize(old_val)
            ):
                return
            self._apply_transaction_to_ram(transaction, undo=False)
            # Applying the retry transaction advances the generation.  The
            # new value is the one that must survive the lock wait and write.
            generation = self._write_generation.get(uek, generation)
        else:
            if self._write_generation.get(uek) != generation:
                return

            if item.serialize(item.value) != val_str:
                return

        if self._save_lock is None:
            self._save_lock = asyncio.Lock()

        try:
            engine = self._get_engine_for_item(item)
        except Exception as e:
            self._save_failure_pending = True
            self.notify_status(f"Engine Error: {e}", level="error")
            self._revert_transaction(transaction)
            self._maybe_finish_quit()
            return

        async with self._save_lock:
            # A newer edit may have happened while this save waited for the
            # serialized write lock.  Validate again immediately before I/O.
            if (
                self._write_generation.get(uek) != generation
                or item.serialize(item.value) != val_str
            ):
                return

            self._active_save_count += 1
            write_result = None
            try:
                try:
                    if hasattr(engine, "write_value_result"):
                        write_result = await self._run_save_io(
                            engine.write_value_result, item.key, item.scope, val_str
                        )
                        success, msg = write_result.ok, write_result.message
                    else:
                        success, msg, _ = await self._run_save_io(
                            engine.write_value, item.key, item.scope, val_str,
                            item_type=item.type_
                        )
                except Exception as e:
                    success, msg = False, f"Engine Error: {e}"
            finally:
                self._active_save_count -= 1

        if success:
            for ti, ii, _, new_value in transaction:
                self._committed[(ti, ii)] = clone_value(new_value)
                other = self.schema[ti][ii]
                if other.serialize(other.value) == other.serialize(new_value):
                    self.pending_commits.discard((ti, ii))
                else:
                    self._sync_pending(ti, ii, other)
            # The write may have completed after a newer edit was made.  Do
            # not report the old value as current or roll anything back.
            if self._write_generation.get(uek) != generation:
                return
            try:
                ekey = self._get_item_engine_info(item)
                if engine.target_path:
                    fingerprint = self._target_fingerprint(Path(engine.target_path).expanduser().resolve())
                    if isinstance(fingerprint, tuple):
                        self.last_target_mtimes[ekey] = fingerprint
            except (OSError, TypeError):
                pass

            # A completed write is a state transition too.  Advancing the
            # generation prevents an external reload that began earlier from
            # replacing the value just committed.
            self._bump_write_generation(uek)

            if is_trigger_item(item):
                reset_generation = self._write_generation[uek]
                def reset_trigger():
                    if self._write_generation.get(uek) == reset_generation:
                        self._reset_trigger_ui(item)

                self.set_timer(0.15, reset_trigger)

            message = msg if self._get_item_engine_info(item)[0] == "network" and msg else f"Updated {item.label}"
            self.notify_status(message, level="success")
            self._maybe_finish_quit()
            return

        if write_result is not None and "AUTH_REQUIRED" not in msg:
            if self._write_generation.get(uek) != generation or item.serialize(item.value) != val_str:
                return
            if write_result.actual is not None:
                self._apply_observed_systemd_state(item, write_result.actual, val_str)
                self.notify_status(f"Error: {msg}; current enablement was refreshed.", level="error")
            else:
                for ti, ii, _, _ in transaction:
                    self._sync_pending(ti, ii, self.schema[ti][ii])
                self.notify_status(f"Error: {msg}; current enablement is unknown.", level="error")
            self._save_failure_pending = True
            self._maybe_finish_quit()
            return

        if "AUTH_REQUIRED" in msg:
            if (
                self._write_generation.get(uek) != generation
                or item.serialize(item.value) != val_str
            ):
                return
            if isinstance(self.screen, PasswordScreen):
                self.notify_status("Another authorization is already in progress.", level="warning")
                self._save_failure_pending = True
                self._revert_transaction(transaction)
                return

            self._revert_transaction(transaction)
            generation = self._write_generation.get(uek, generation)

            def on_pwd(pwd: str | None) -> None:
                self._start_save_task(self._on_auto_password(pwd, tab_idx, item_idx, item, val_str, old_val, generation, transaction))

            self._save_auth_pending += 1
            self.push_screen(PasswordScreen(), on_pwd)
            return

        if (
            self._write_generation.get(uek) != generation
            or item.serialize(item.value) != val_str
        ):
            return

        self.notify_status(f"Error: {msg}", level="error")
        self._save_failure_pending = True
        self._revert_transaction(transaction)
        self.play_reset_sound()
        self._maybe_finish_quit()

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
        self._save_auth_pending = max(0, self._save_auth_pending - 1)
        if pwd:
            try:
                auth_res = await self._run_save_io(
                    subprocess.run,
                    ["sudo", "-S", "-v"],
                    input=(pwd + "\n").encode(),
                    capture_output=True,
                    timeout=30,
                    check=False,
                    env={**os.environ, "LC_ALL": "C"},
                )
            except subprocess.TimeoutExpired:
                self.notify_status("Sudo authentication timed out.", level="error")
                self._save_failure_pending = True
                self._maybe_finish_quit()
                return
            except Exception as exc:
                self.notify_status(f"Sudo authentication failed: {exc}", level="error")
                self._save_failure_pending = True
                self._maybe_finish_quit()
                return
            finally:
                pwd = None

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
                self._save_failure_pending = True
                self.play_reset_sound()
        else:
            self.notify_status("Sudo authentication cancelled.", level="warning")
            self._save_failure_pending = True
        self._maybe_finish_quit()

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
            if self._quit_after_save:
                self._save_failure_pending = True
                self._maybe_finish_quit()
            return False

        self.trigger_shortcut_blink("ctrl-s")

        if not self._save_tasks and self._save_auth_pending == 0:
            self._save_failure_pending = False

        if not self.pending_commits:
            self.notify_status("No pending changes.", level="info")
            if on_complete:
                on_complete(True)
            self._maybe_finish_quit()
            return True

        if self._save_lock is None:
            self._save_lock = asyncio.Lock()

        self._start_save_task(self._save_batch_async(on_complete))
        return True

    async def _save_batch_async(self, on_complete=None) -> None:
        if self._save_lock is None:
            self._save_lock = asyncio.Lock()

        async with self._save_lock:
            if not self.pending_commits:
                self.notify_status("No pending changes.", level="info")
                if on_complete:
                    on_complete(True)
                return

            # Frozen snapshot: (change_tuple, commit_key, val_str, frozen_val, ConfigItem)
            type FrozenItem = tuple[tuple[str, str, str, str], tuple[int, int], str, Any, ConfigItem]
            batches: dict[tuple[tuple[str, str], str | None], list[FrozenItem]] = {}

            for tab_idx, item_idx in tuple(self.pending_commits):
                item = self.schema[tab_idx][item_idx]
                key = (tab_idx, item_idx)
                frozen_val = clone_value(item.value)
                val_str = item.serialize(frozen_val)
                ekey = self._get_item_engine_info(item)
                change = (item.key, item.scope, val_str, str(item.type_))
                trigger_key = self._uid_engine_key(item) if is_trigger_item(item) else None
                batches.setdefault((ekey, trigger_key), []).append((change, key, val_str, frozen_val, item))

            final_success = True
            success_count = 0
            error_msgs = []
            auth_required = False

            def mark_success(key: tuple[int, int], frozen_val_str: str, frozen_val: Any, itm: ConfigItem) -> bool:
                self._committed[key] = clone_value(frozen_val)
                current_str = itm.serialize(itm.value)
                if current_str != frozen_val_str:
                    self._save_queued_during_run = True
                    return False
                self.pending_commits.discard(key)
                return True

            for (ekey, trigger_key), batch in batches.items():
                engine = self.engine_pool[ekey]
                # Duplicate views must not execute a trigger multiple times.
                changes = list({(b[0][0], b[0][1]): b[0] for b in batch}.values())

                if trigger_key is None and hasattr(engine, "write_batch_results"):
                    self._active_save_count += 1
                    try:
                        results = await self._run_save_io(engine.write_batch_results, changes)
                    except Exception as exc:
                        results = {}
                        error_msgs.append(f"Engine Error: {exc}")
                        final_success = False
                    finally:
                        self._active_save_count -= 1

                    reconciled = set()
                    for change, key, frozen_str, frozen_val, itm in batch:
                        identity = (change[0], change[1])
                        result = results.get(identity)
                        if result is None:
                            # Unknown outcome remains pending; never repeat it
                            # blindly or report the batch as fully successful.
                            final_success = False
                            error_msgs.append(f"Missing write result for {identity[1]}.{identity[0]}")
                            continue
                        if result.ok:
                            if mark_success(key, frozen_str, frozen_val, itm):
                                success_count += 1
                                self._bump_write_generation(self._uid_engine_key(itm))
                            continue
                        final_success = False
                        if result.message == "AUTH_REQUIRED":
                            auth_required = True
                            continue
                        if identity not in reconciled and result.actual is not None:
                            self._apply_observed_systemd_state(itm, result.actual, frozen_str)
                            reconciled.add(identity)
                        if result.actual == frozen_str:
                            success_count += 1
                        error_msgs.append(result.message)
                    if auth_required:
                        break
                    continue

                self._active_save_count += 1
                try:
                    if trigger_key is not None:
                        key_s, scope, value, kind = changes[0]
                        success, msg, _ = await self._run_save_io(engine.write_value, key_s, scope, value, item_type=kind)
                    else:
                        success, msg, _ = await self._run_save_io(engine.write_batch, changes)
                except Exception as e:
                    success, msg = False, f"Engine Error: {e}"
                finally:
                    self._active_save_count -= 1

                if success:
                    committed_ueks: set[str] = set()
                    completed_triggers = {}
                    for _change, key, frozen_str, frozen_val, itm in batch:
                        if mark_success(key, frozen_str, frozen_val, itm):
                            success_count += 1
                            committed_ueks.add(self._uid_engine_key(itm))
                            if is_trigger_item(itm):
                                completed_triggers[self._uid_engine_key(itm)] = itm

                    for trigger in completed_triggers.values():
                        self._reset_trigger_ui(trigger)

                    for uek in committed_ueks:
                        self._bump_write_generation(uek)

                    try:
                        if engine.target_path:
                            fingerprint = self._target_fingerprint(Path(engine.target_path).expanduser().resolve())
                            if isinstance(fingerprint, tuple):
                                self.last_target_mtimes[ekey] = fingerprint
                    except (OSError, TypeError):
                        pass
                else:
                    if "AUTH_REQUIRED" in msg:
                        auth_required = True
                        break

                    if getattr(engine, "atomic_batches", False):
                        final_success = False
                        error_msgs.append(msg)
                        continue

                    engine_success_count = 0
                    committed_ueks = set()
                    completed_triggers = {}

                    fallback_results = {}
                    if trigger_key is not None:
                        # An action may already have had side effects before
                        # reporting failure. Never execute it again as fallback.
                        fallback_results[(changes[0][0], changes[0][1])] = (success, msg)
                    for change, key, frozen_str, frozen_val, itm in batch:
                        key_s, scope, val_str, itype = change

                        identity = (key_s, scope)
                        if identity not in fallback_results:
                            self._active_save_count += 1
                            try:
                                ok, item_msg, _ = await self._run_save_io(
                                    engine.write_value, key_s, scope, val_str, item_type=itype
                                )
                            except Exception as e:
                                ok, item_msg = False, f"Engine Error: {e}"
                            finally:
                                self._active_save_count -= 1
                            fallback_results[identity] = (ok, item_msg)
                        else:
                            ok, item_msg = fallback_results[identity]

                        if ok:
                            if mark_success(key, frozen_str, frozen_val, itm):
                                success_count += 1
                                engine_success_count += 1
                                committed_ueks.add(self._uid_engine_key(itm))
                                if is_trigger_item(itm):
                                    uek = self._uid_engine_key(itm)
                                    completed_triggers[uek] = (itm, self._write_generation.get(uek, 0))

                            try:
                                if engine.target_path:
                                    fingerprint = self._target_fingerprint(Path(engine.target_path).expanduser().resolve())
                                    if isinstance(fingerprint, tuple):
                                        self.last_target_mtimes[ekey] = fingerprint
                            except (OSError, TypeError):
                                pass
                        else:
                            if "AUTH_REQUIRED" in item_msg:
                                auth_required = True
                                break
                            error_msgs.append(item_msg)
                            if is_trigger_item(itm):
                                self.pending_commits.discard(key)
                                self._reset_trigger_ui(itm)

                    for uek, (trigger, generation) in completed_triggers.items():
                        if self._write_generation.get(uek, 0) == generation:
                            self._reset_trigger_ui(trigger)
                    for uek in committed_ueks:
                        self._bump_write_generation(uek)

                    if auth_required:
                        break

                    if engine_success_count != len(batch):
                        final_success = False

        if self._save_queued_during_run:
            self._save_queued_during_run = False
            if self.pending_commits:
                self._start_save_task(self._save_batch_async(on_complete))
                return

        if auth_required:
            self._refresh_all_ui()
            self._refresh_presets_ui()
            if isinstance(self.screen, PasswordScreen):
                self.notify_status("Another authorization is already in progress.", level="warning")
                self._save_failure_pending = True
                if on_complete:
                    on_complete(False)
                return

            def on_pwd_batch(pwd: str | None) -> None:
                self._start_save_task(self._on_batch_password(pwd, on_complete))

            self._save_auth_pending += 1
            self.push_screen(PasswordScreen(), on_pwd_batch)
            return

        if final_success:
            self.notify_status(f"Batched {success_count} commits successfully.", level="success")
            self.play_reset_sound()
        elif success_count > 0:
            self._save_failure_pending = True
            first_err = error_msgs[0] if error_msgs else "Unknown Engine Error"
            self.notify_status(f"Partial success ({success_count} applied). Error: {first_err}", level="warning")
            self.play_reset_sound()
        else:
            self._save_failure_pending = True
            first_err = error_msgs[0] if error_msgs else "Unknown Engine Error"
            self.notify_status(f"Batch Error: {first_err}", level="error")

        self._refresh_all_ui()
        self._update_footer_legend()
        self._refresh_presets_ui()

        if on_complete:
            on_complete(final_success)

    async def _on_batch_password(self, pwd: str | None, on_complete=None) -> None:
        self._save_auth_pending = max(0, self._save_auth_pending - 1)
        if pwd:
            try:
                auth_res = await self._run_save_io(
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
                self._save_failure_pending = True
                if on_complete:
                    on_complete(False)
                self._maybe_finish_quit()
                return
            except Exception as exc:
                self.notify_status(f"Sudo authentication failed: {exc}", level="error")
                self._save_failure_pending = True
                if on_complete:
                    on_complete(False)
                self._maybe_finish_quit()
                return
            finally:
                pwd = None

            if auth_res.returncode == 0:
                self.notify_status("Sudo authenticated. Retrying batch...", level="info")
                self._start_sudo_keepalive()
                # PasswordScreen also calls back before it is popped.
                # Keep quit waiting until the deferred retry owns a save task.
                self._save_auth_pending += 1
                def retry_after_dialog() -> None:
                    self._save_auth_pending -= 1
                    self.action_save_batch(on_complete=on_complete)
                self.call_later(retry_after_dialog)
            else:
                self.notify_status("Incorrect sudo password. Batch aborted.", level="error")
                self._save_failure_pending = True
                if on_complete:
                    on_complete(False)
        else:
            self.notify_status("Sudo authentication cancelled.", level="warning")
            self._save_failure_pending = True
            if on_complete:
                on_complete(False)
        self._maybe_finish_quit()

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

    async def action_toggle_help(self) -> None:
        content_area = self.query_one("#content-area")
        content_area.toggle_class("-show-help")
        self.toggle_shortcut_active("help", content_area.has_class("-show-help"))

        if content_area.has_class("-show-help"):
            panel = self.query_one("#help-panel")
            if not panel.query(Markdown):
                await panel.mount(Markdown("Select an item to view documentation.", id="help-markdown"))
            self._update_current_help_panel()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "clear_local_search":
            return self._modal_active() or self._local_search_is_open()
        return True

    def _local_search_is_open(self) -> bool:
        try:
            return bool(self.query_one("#local-search", Input).display)
        except Exception:
            return False

    def _local_search_input(self) -> Input:
        return self.query_one("#local-search", Input)

    def action_focus_local_search(self) -> None:
        if isinstance(self.screen, SearchScreen):
            return
        if self._modal_active():
            return

        inp = self._local_search_input()

        with inp.prevent(Input.Changed):
            inp.value = ""

        inp.display = True
        self.toggle_shortcut_active("slash", True)
        self.call_after_refresh(self._focus_local_search)

    def _focus_local_search(self) -> None:
        inp = self._local_search_input()
        if not inp.display:
            return
        inp.focus(scroll_visible=True)

    def action_clear_local_search(self) -> None:
        if self._modal_active():
            if isinstance(self.screen, UnsavedChangesDialog):
                self.screen.dismiss("cancel")
            elif isinstance(self.screen, ConfirmDialog):
                self.screen.dismiss(False)
            else:
                self.screen.dismiss(None)
            return

        inp = self._local_search_input()
        if not inp.display:
            return

        with inp.prevent(Input.Changed):
            inp.value = ""

        inp.display = False
        self.toggle_shortcut_active("slash", False)

        if ol := self.current_option_list:
            self.call_after_refresh(ol.focus)

    @on(Input.Changed, "#local-search")
    def handle_local_search(self, event: Input.Changed) -> None:
        if not event.input.display:
            return

        query = event.value.lower().replace(" ", "")
        if not query:
            return

        ol = self.current_option_list
        if ol is None:
            return

        try:
            tab_idx = int(ol.id.split("-")[1])
            items = self.schema.get(tab_idx, [])

            for item_idx, item in enumerate(items):
                label = item.label.lower().replace(" ", "")
                if query not in label:
                    continue

                opt_id = f"item_{tab_idx}_{item_idx}"

                pref = item.parent_ref
                if pref:
                    current_pref = pref
                    expanded_any = False
                    seen_prefs = set()

                    while current_pref and current_pref not in seen_prefs:
                        seen_prefs.add(current_pref)

                        for p_item in items:
                            if current_pref in (p_item.uid, p_item.key) and (p_item.is_parent or p_item.type_ == "menu"):
                                if not p_item.expanded:
                                    p_item.expanded = True
                                    expanded_any = True

                                current_pref = p_item.parent_ref
                                break
                        else:
                            current_pref = None

                    if expanded_any:
                        self._populate_option_list(tab_idx, maintain_highlight_id=opt_id)

                idx = ol.get_option_index(opt_id)
                ol.highlighted = idx
                scroll = getattr(ol, "scroll_to_highlight", None)
                if callable(scroll):
                    scroll()
                break
        except Exception:
            pass

    @on(Input.Submitted, "#local-search")
    def submit_local_search(self, event: Input.Submitted) -> None:
        event.stop()
        self.action_clear_local_search()

    def _focus_search_target(self) -> None:
        target = self._pending_search_target
        if target is None or self._current_tab_index() != target[0]:
            return
        tab_idx, item_idx = target
        try:
            ol = self.query_one(f"#list-{tab_idx}", ConfigOptionList)
            ol.highlighted = ol.get_option_index(f"item_{tab_idx}_{item_idx}")
            ol.focus()
            ol.scroll_to_highlight()
        except (NoMatches, OptionDoesNotExist):
            LOGGER.exception("Unable to focus search result")
            self.notify_status("Search result is no longer available.", level="warning")
        finally:
            self._pending_search_target = None

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
                            if current_pref in (p_item.uid, p_item.key) and (p_item.is_parent or p_item.type_ == "menu"):
                                p_item.expanded = True
                                current_pref = p_item.parent_ref
                                break
                        else:
                            break

                self._pending_search_target = (tab_idx, item_idx)
                self._populate_option_list(tab_idx, maintain_highlight_id=f"item_{tab_idx}_{item_idx}")
                self.action_switch_tab(tab_idx)
                # Same-tab searches need no activation event. Other searches
                # are focused by handle_tab_activated after lazy mount completes.
                self.call_after_refresh(self._focus_search_target)

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

        if index in self.tabs:
            self.query_one(Tabs).active = f"tab-id-{index}"

    def action_toggle_save_mode(self) -> None:
        if self._modal_active():
            return
        self.auto_save = not self.auto_save

    # =========================================================================
    # ITEM ADJUSTMENT / RESET
    # =========================================================================
    def action_adjust(self, direction: int, bypass_lock: bool = False) -> None:
        if self._modal_active():
            return

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
        if self._modal_active():
            return

        self.trigger_shortcut_blink("r")

        ol = self.current_option_list
        if not ol or not ol.last_highlighted_id:
            return

        parsed = self._get_item_from_id(ol.last_highlighted_id)
        if not parsed:
            return

        tab_idx, item_idx, item = parsed
        if item.read_only:
            return

        if item.is_parent or item.type_ == "menu":
            items_in_tab = self.schema.get(tab_idx, [])
            parent_key = item.key
            parent_uid = self._get_item_uid(item)

            child_uids = set()
            child_keys = set()
            stack = []
            if parent_key:
                stack.append(parent_key)
            if parent_uid:
                stack.append(parent_uid)

            while stack:
                curr = stack.pop()
                for itm in items_in_tab:
                    p_ref = getattr(itm, "parent_ref", None)
                    if p_ref and p_ref == curr:
                        u = self._get_item_uid(itm)
                        if u not in child_uids:
                            child_uids.add(u)
                            child_keys.add(itm.key)
                            if getattr(itm, "is_parent", False) or getattr(itm, "type_", None) == "menu":
                                if itm.key:
                                    stack.append(itm.key)
                                stack.append(u)

            transaction = []

            # 1. Reset parent item itself if configurable and modified
            if item.type_ not in ("menu", "action", "preset") and str(item.value) != str(item.default):
                transaction.append((tab_idx, item_idx, item.value, item.default))

            # 2. Reset all descendant child items if modified
            for i_idx, itm in enumerate(items_in_tab):
                if (itm.key in child_keys or self._get_item_uid(itm) in child_uids) and itm.type_ not in ("menu", "action", "preset") and not itm.read_only and (
                    itm.exists_in_target or not self.hide_missing_items
                ):
                    if str(itm.value) != str(itm.default):
                        transaction.append((tab_idx, i_idx, itm.value, itm.default))

            if transaction:
                self._apply_transaction(
                    transaction,
                    action_type="new",
                    success_msg=f"Reset settings under '{item.label}' to default."
                )

        elif str(item.value) != str(item.default):
            self._safe_apply_value(tab_idx, item_idx, item, item.default)

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
                if item.type_ not in ("action", "menu", "preset") and not item.read_only
                and (item.exists_in_target or not self.hide_missing_items)
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

            safe_tab = _md_escape(str(tab_name))
            self.push_screen(
                ConfirmDialog(
                    f"Are you sure you want to reset all items in **{safe_tab}** to their factory defaults?",
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
    def _write_preset_atomically(self, file_path: Path, payload: dict, *, exclusive: bool) -> None:
        """Serialize first, then publish via temp file plus atomic replacement.

        exclusive=True keeps new-name creation race-safe: the destination is
        linked (fails if it already exists) instead of replaced.  Existing
        bytes are never truncated; temporary files are always cleaned up.
        Temporary files use a non-.json suffix so preset readers polling
        for *.json never observe an incomplete file.
        """
        text = json.dumps(payload, indent=4)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path: Path | None = None
        try:
            fd, tmp_name = tempfile.mkstemp(
                dir=str(file_path.parent), prefix=".tmp-preset-", suffix=".tmp"
            )
            tmp_path = Path(tmp_name)
            try:
                stream = os.fdopen(fd, "w", encoding="utf-8")
            except BaseException:
                try:
                    os.close(fd)
                except OSError:
                    pass
                raise
            with stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            if exclusive:
                try:
                    os.link(tmp_path, file_path)
                except FileExistsError:
                    raise FileExistsError(f"Preset already exists: {file_path.stem}")
                finally:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    tmp_path = None
            else:
                os.replace(tmp_path, file_path)
                tmp_path = None
        finally:
            if tmp_path is not None:
                try:
                    if tmp_path.exists():
                        tmp_path.unlink()
                except OSError:
                    pass

    def action_save_preset(self) -> None:
        if self._modal_active():
            return
        if not self.enable_user_presets or not self.require_boot_complete():
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
                    if item.type_ in ("action", "preset", "menu") or self._get_item_engine_info(item) != self.default_engine_key:
                        continue

                    payload[self._get_item_uid(item)] = item.value

            file_path = self.user_presets_dir / f"{name}.json"

            def do_save(*, exclusive: bool) -> None:
                try:
                    self._write_preset_atomically(file_path, payload, exclusive=exclusive)
                except FileExistsError:
                    self.notify_status(f"Preset already exists: {name}", level="error")
                    return
                except Exception as e:
                    self.notify_status(f"Error saving preset: {e}", level="error")
                    return

                self.notify_status(f"Successfully saved preset: {name}", level="success")

                self._load_user_presets()
                self._rebuild_indexes()
                self._refresh_all_ui()

            if file_path.exists():
                safe_name = _md_escape(name)
                self.push_screen(
                    ConfirmDialog(
                        f"Preset **{safe_name}** already exists. Overwrite?",
                        title="Overwrite Preset",
                        level="warning",
                        default_confirm=False,
                    ),
                    lambda confirmed: do_save(exclusive=False) if confirmed else None,
                )
                return

            do_save(exclusive=True)

        self.push_screen(HybridInputScreen("Save Current State as Preset (Name):", ""), check_reply)

    def action_import_preset(self) -> None:
        if self._modal_active():
            return

        def check_reply(name: str | None) -> None:
            if not name:
                return

            name = re.sub(r'[\\/*?:"<>|]', "", name.strip())
            if not name:
                return

            file_path = self.user_presets_dir / f"{name}.json"

            try:
                if file_path.exists():
                    self.notify_status(
                        f"Preset already exists: {name}. Choose a new name.",
                        level="error",
                    )
                    return
                self._write_preset_atomically(file_path, {}, exclusive=True)
            except FileExistsError:
                self.notify_status(
                    f"Preset already exists: {name}. Choose a new name.",
                    level="error",
                )
                return
            except Exception as e:
                self.notify_status(f"Error importing preset: {e}", level="error")
                return

            self.notify_status(f"Created import template: {name}", level="success")

            self._load_user_presets()
            self._rebuild_indexes()
            self._refresh_all_ui()

            self.open_file_externally(file_path, button=1, touch_first=False)

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
        if self._modal_active():
            return

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

        if isinstance(ol, ConfigOptionList) and ol is self.current_option_list:
            try:
                if ol.get_option_at_index(event.option_index) is not event.option:
                    return
            except OptionDoesNotExist:
                return
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

        if (item.type_ in ("preset", "action") or trigger_bool) and click_x >= 40:
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

        if item.is_parent or item.type_ == "menu":
            is_modified, _ = self._get_parent_children_status(item, tab_idx)
        else:
            is_modified = str(item.value) != str(item.default)

        if is_modified and item.type_ not in ("action", "preset"):
            prefix = self._indent_cache.get(opt_id, "")
            rendered_text = self._build_option(item, True, indent_prefix=prefix, tab_idx=tab_idx)
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
        if self._modal_active():
            return

        ol = self.current_option_list
        if not ol or not ol.last_highlighted_id:
            return

        parsed = self._get_item_from_id(ol.last_highlighted_id)
        if not parsed:
            return

        tab_idx, item_idx, item = parsed

        if item.is_parent or item.type_ == "menu":
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
            lexer = shlex.shlex(cmd_str, posix=True, punctuation_chars="|&;")
            lexer.whitespace_split = True
            lexer.commenters = ""
            tokens = list(lexer)
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

    @staticmethod
    async def _drain_action_stream(stream: asyncio.StreamReader, limit: int) -> bytes:
        buf = bytearray()
        try:
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    break
                if len(buf) < limit:
                    buf.extend(chunk[: limit - len(buf)])
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        return buf.take_bytes()

    def _kill_action_tree(self, proc: asyncio.subprocess.Process) -> None:
        # Actions start in their own Linux session, so pgid == pid.
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    @staticmethod
    def _action_group_alive(pgid: int) -> bool:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _close_action_pipes(self, proc: asyncio.subprocess.Process | None) -> None:
        if proc is not None:
            # asyncio exposes no public Process.close(). Closing its transport
            # also releases pipes inherited by detached descendants.
            proc._transport.close()

    async def _terminate_and_reap_action(self, proc: asyncio.subprocess.Process) -> None:
        self._kill_action_tree(proc)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _ACTION_KILL_GRACE
        try:
            await asyncio.wait_for(proc.wait(), timeout=_ACTION_KILL_GRACE)
        except TimeoutError:
            pass
        while self._action_group_alive(proc.pid) and loop.time() < deadline:
            await asyncio.sleep(min(0.05, max(0, deadline - loop.time())))
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=_ACTION_KILL_GRACE)
        except TimeoutError:
            self._close_action_pipes(proc)
            try:
                await asyncio.wait_for(proc.wait(), timeout=_ACTION_KILL_GRACE)
            except TimeoutError:
                LOGGER.warning("Action process %s did not exit after SIGKILL", proc.pid)

    async def _cleanup_action_resources(
        self,
        proc: asyncio.subprocess.Process | None,
        drains: list[asyncio.Task[bytes]],
        spawn_task: asyncio.Task[Any] | None = None,
        proc_box: list[Any] | None = None,
    ) -> None:
        """Single-owner group termination + drain reaping + pipe closing."""
        if proc is None and proc_box:
            for cand in list(proc_box):
                if cand is not None:
                    proc = cand
                    break
        if proc is None and spawn_task is not None and not spawn_task.done():
            try:
                got = await asyncio.wait_for(asyncio.shield(spawn_task), timeout=_ACTION_DRAIN_TIMEOUT)
                if isinstance(got, asyncio.subprocess.Process):
                    proc = got
            except TimeoutError:
                pass
        if proc is None and spawn_task is not None and spawn_task.done() and not spawn_task.cancelled():
            try:
                if spawn_task.exception() is None:
                    got = spawn_task.result()
                    if isinstance(got, asyncio.subprocess.Process):
                        proc = got
            except Exception:
                pass
        if proc is not None:
            try:
                self._action_procs.add(proc)
            except Exception:
                pass
            try:
                await self._terminate_and_reap_action(proc)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
        for pending in drains:
            try:
                if pending is not None and not pending.done():
                    pending.cancel()
            except Exception:
                pass
        if drains:
            try:
                await asyncio.gather(*drains, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
        if proc is not None:
            try:
                self._close_action_pipes(proc)
            except Exception:
                pass
            try:
                self._action_procs.discard(proc)
            except Exception:
                pass

    @staticmethod
    def _track_background_task(task: asyncio.Task[Any], tasks: set[asyncio.Task[Any]]) -> asyncio.Task[Any]:
        tasks.add(task)

        def done(completed: asyncio.Task[Any]) -> None:
            tasks.discard(completed)
            if not completed.cancelled() and (error := completed.exception()) is not None:
                LOGGER.error("Background action failed", exc_info=error)

        task.add_done_callback(done)
        return task

    def _track_action_task(self, task: asyncio.Task[Any]) -> asyncio.Task[Any]:
        return self._track_background_task(task, self._action_tasks)

    def _track_action_cleanup(self, task: asyncio.Task[Any]) -> asyncio.Task[Any]:
        return self._track_background_task(task, self._action_cleanup_tasks)

    def _cancel_background_actions(self) -> None:
        """Synchronous best-effort kill/cancel; awaiting happens in shutdown."""
        for proc in list(getattr(self, "_action_procs", ())):
            try:
                self._kill_action_tree(proc)
            except Exception:
                pass
        for task in list(getattr(self, "_action_tasks", ())):
            try:
                if not task.done():
                    task.cancel()
            except Exception:
                pass

    async def _shutdown_background_actions(self) -> None:
        """Idempotent bounded shutdown; safe to call from any exit path."""
        if getattr(self, "_action_shutdown_done", False):
            return
        lock = getattr(self, "_action_shutdown_lock", None)
        if lock is None:
            lock = self._action_shutdown_lock = asyncio.Lock()
        async with lock:
            if getattr(self, "_action_shutdown_done", False):
                return
            self._action_shutdown_started = True
            self._cancel_background_actions()
            tasks = [t for t in list(getattr(self, "_action_tasks", ())) if not t.done()]
            if tasks:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*tasks, return_exceptions=True),
                        timeout=5.0,
                    )
                except TimeoutError:
                    pass
                for t in tasks:
                    try:
                        if t.done() and not t.cancelled():
                            t.exception()
                    except Exception:
                        pass
            cleanup_tasks = [t for t in list(getattr(self, "_action_cleanup_tasks", ())) if not t.done()]
            if cleanup_tasks:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*cleanup_tasks, return_exceptions=True),
                        timeout=5.0,
                    )
                except TimeoutError:
                    pass
                for t in cleanup_tasks:
                    try:
                        if t.done() and not t.cancelled():
                            t.exception()
                    except Exception:
                        pass
            for proc in list(getattr(self, "_action_procs", ())):
                try:
                    await self._terminate_and_reap_action(proc)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
                try:
                    self._close_action_pipes(proc)
                except Exception:
                    pass
                try:
                    self._action_procs.discard(proc)
                except Exception:
                    pass
            if (
                all(t.done() for t in tasks)
                and all(t.done() for t in cleanup_tasks)
                and not getattr(self, "_action_procs", set())
            ):
                self._action_shutdown_done = True

    async def on_unmount(self) -> None:
        if self._sudo_keepalive:
            self._sudo_keepalive.stop()
            self._sudo_keepalive = None
        if callable(detach := getattr(self.engine_pool, "detach_app", None)):
            detach()
        # Blocking collectors drain before engine resources are shut down.
        tasks = [*self._custom_refresh_tasks, *self._custom_mount_tasks.values(),
                 *self._option_mount_tasks.values()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # A cancelled boot/save coroutine cannot stop its executor thread.
        # Let constructors and reads/writes finish before closing their engines.
        if self._io_workers:
            await asyncio.wait(tuple(self._io_workers))
        initialized_values = getattr(self.engine_pool, "initialized_values", None)
        engines = initialized_values() if callable(initialized_values) else self.engine_pool.values()
        for engine in engines:
            if callable(shutdown := getattr(engine, "shutdown", None)):
                shutdown()
        await self._shutdown_background_actions()

    def execute_action(self, item: ConfigItem) -> None:
        if item.read_only:
            return
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
            if getattr(self, "_action_shutdown_started", False):
                self.notify_status("Shutting down; action not started.", level="warning")
                return
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
                    completed = self.run_suspended_interactive(command, shell=True)
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
                drains: list[asyncio.Task[bytes]] = []
                spawn_task: asyncio.Task[Any] | None = None
                proc_box: list[Any] = []
                stdout = b""
                stderr = b""
                timed_out = False
                try:
                    async def _spawn() -> asyncio.subprocess.Process:
                        p = await asyncio.create_subprocess_shell(
                            command,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                            start_new_session=True,
                        )
                        proc_box.append(p)
                        try:
                            self._action_procs.add(p)
                        except Exception:
                            pass
                        return p

                    spawn_task = asyncio.create_task(_spawn())
                    self._track_action_cleanup(spawn_task)
                    try:
                        # Shield the await (not the task): outer cancellation
                        # must not propagate into the owned spawn task.
                        proc = await asyncio.shield(spawn_task)
                    except asyncio.CancelledError:
                        for cand in list(proc_box):
                            if cand is not None:
                                proc = cand
                                break
                        if proc is None and spawn_task.done() and not spawn_task.cancelled():
                            try:
                                if spawn_task.exception() is None:
                                    cand = spawn_task.result()
                                    if isinstance(cand, asyncio.subprocess.Process):
                                        proc = cand
                                        try:
                                            self._action_procs.add(proc)
                                        except Exception:
                                            pass
                            except Exception:
                                pass
                        raise
                    assert proc.stdout is not None and proc.stderr is not None
                    drains = [
                        asyncio.create_task(
                            self._drain_action_stream(proc.stdout, _ACTION_OUTPUT_LIMIT)
                        ),
                        asyncio.create_task(
                            self._drain_action_stream(proc.stderr, _ACTION_OUTPUT_LIMIT)
                        ),
                    ]

                    try:
                        await asyncio.wait_for(proc.wait(), timeout=_ACTION_TIMEOUT)
                    except TimeoutError:
                        timed_out = True

                    if not timed_out:
                        try:
                            parts = await asyncio.wait_for(
                                asyncio.gather(*drains),
                                timeout=_ACTION_DRAIN_TIMEOUT,
                            )
                            stdout, stderr = parts[0], parts[1]
                        except TimeoutError:
                            # Shell exited but descendants still hold pipes.
                            timed_out = True
                            stdout, stderr = b"", b""

                    if timed_out:
                        self.notify_status(
                            f"Action timed out after {_ACTION_TIMEOUT:g} seconds.",
                            level="error",
                        )
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

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self.notify_status(f"Execution error: {str(e)[:60]}", level="error")
                finally:
                    if proc is not None or drains or (spawn_task is not None and not spawn_task.done()):
                        cleanup = asyncio.create_task(
                            self._cleanup_action_resources(proc, drains, spawn_task, proc_box)
                        )
                        self._track_action_cleanup(cleanup)

            self._track_action_task(asyncio.create_task(run_noninteractive()))

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
        if not self.require_boot_complete():
            return
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

        global_items = [entry for entry in self._configurable_items if self._get_item_engine_info(entry[2]) == self.default_engine_key]
        self._preset_matrix.rebuild(global_items + self._preset_items)
        self._option_cache.invalidate_presets()

        def do_apply():
            transaction = []
            skipped = 0

            payload = preset_item.preset_payload
            is_all_defaults = payload.get("__ALL_DEFAULTS__", False)

            for t_idx, i_idx, target_item in self._configurable_items:
                # Presets are snapshots of the default target.  Per-target
                # overrides sharing the same UID must not be changed by one.
                if self._get_item_engine_info(target_item) != self.default_engine_key:
                    continue
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

                if target_item.serialize(target_item.value) != target_item.serialize(target_val):
                    if target_val is not None:
                        target_val = target_item.deserialize(target_val)
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
                        text = str(new_val).strip()
                        try:
                            parsed_val = int(text, 0)
                        except ValueError:
                            try:
                                parsed_val = int(text, 10)
                            except ValueError:
                                float_val = float(text)
                                if not math.isfinite(float_val):
                                    self.notify_status("Error: Value must be a finite integer.", level="error")
                                    return
                                parsed_val = int(float_val)

                        if item.min_val is not None:
                            parsed_val = max(int(item.min_val), parsed_val)

                        if item.max_val is not None:
                            parsed_val = min(int(item.max_val), parsed_val)

                        new_val = parsed_val

                    except (ValueError, OverflowError):
                        self.notify_status("Error: Value must be a finite integer.", level="error")
                        return

                elif item.type_ == "float":
                    try:
                        parsed_val = float(str(new_val).strip())

                        if not math.isfinite(parsed_val):
                            self.notify_status("Error: Value must be a finite float.", level="error")
                            return

                        if item.min_val is not None:
                            parsed_val = max(float(item.min_val), parsed_val)

                        if item.max_val is not None:
                            parsed_val = min(float(item.max_val), parsed_val)

                        new_val = parsed_val

                    except (ValueError, OverflowError):
                        self.notify_status("Error: Value must be a finite float.", level="error")
                        return

                if self._is_unchanged_submission(item, new_val):
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
                if self._is_unchanged_submission(item, new_val):
                    return
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
