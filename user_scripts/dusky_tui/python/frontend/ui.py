#!/usr/bin/env python3
import os
import re
lazy import json
lazy import subprocess
lazy import shlex
lazy import shutil
import asyncio
import sys
lazy import signal
lazy import tempfile
import logging
lazy import termios
lazy import inspect
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, override
from collections import deque, defaultdict
from collections.abc import Mapping

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
lazy from python.frontend.colors import parse_literal_color, adjust_color_hue
lazy from python.frontend.modals import (
    ConfirmDialog,
    AlertDialog,
    PasswordScreen,
    UnsavedChangesDialog,
    HybridInputScreen,
    PickerScreen,
    SearchScreen,
    DiffScreen,
    ShortcutsInfoScreen,
)

from python.frontend.core_types import (
    ConfigItem,
    BaseEngine,
    color_preview_hex,
    is_trigger_item,
    clone_value,
)

_color_preview_hex = color_preview_hex


LOGGER = logging.getLogger(__name__)
_TARGET_UNREADABLE = sentinel("_TARGET_UNREADABLE")
_STATE_MISSING = sentinel("_STATE_MISSING")


# =============================================================================
# GLOBAL CACHES & CONSTANTS
# =============================================================================
_AUDIO_PLAYER_CACHE: str | None = None
_SOUND_PATH_CACHE: str | None = None
_CAPS_LED_DIR = Path("/sys/class/leds")
_CAPS_LED_CACHE: tuple[int, tuple[Path, ...]] | None = None

_ICON_WARNING = "\uf071"   # nf-fa-warning
_ICON_PENCIL = "\uf040"    # nf-fa-pencil

_ACTION_OUTPUT_LIMIT = 8192
_ACTION_TIMEOUT = 15.0
_ACTION_DRAIN_TIMEOUT = 3.0
_ACTION_KILL_GRACE = 1.0


def _xdg_config_home() -> Path:
    raw = os.environ.get("XDG_CONFIG_HOME", "").strip()
    return Path(raw).expanduser().resolve() if raw else (Path.home() / ".config").resolve()


def _discover_notification_sound() -> str:
    global _SOUND_PATH_CACHE
    if _SOUND_PATH_CACHE is not None:
        return _SOUND_PATH_CACHE
    rel = Path("sounds/freedesktop/stereo/dialog-information.oga")
    raw_dirs = os.environ.get("XDG_DATA_DIRS", "").strip()
    data_dirs = (raw_dirs or "/usr/local/share:/usr/share").split(os.pathsep)
    for base in data_dirs:
        if not base.strip():
            continue
        candidate = Path(base) / rel
        if candidate.is_file():
            _SOUND_PATH_CACHE = str(candidate)
            return _SOUND_PATH_CACHE
    _SOUND_PATH_CACHE = ""
    return ""


def _read_caps_lock_sysfs() -> bool:
    """Return True if any kernel input Caps Lock LED reports active brightness."""
    global _CAPS_LED_CACHE
    try:
        dir_mtime = _CAPS_LED_DIR.stat().st_mtime_ns
    except OSError:
        _CAPS_LED_CACHE = None
        return False

    if _CAPS_LED_CACHE is None or _CAPS_LED_CACHE[0] != dir_mtime:
        paths: list[Path] = []
        try:
            with os.scandir(_CAPS_LED_DIR) as entries:
                for entry in entries:
                    if entry.name.lower().endswith("capslock"):
                        paths.append(Path(entry.path) / "brightness")
        except OSError:
            _CAPS_LED_CACHE = None
            return False
        _CAPS_LED_CACHE = (dir_mtime, tuple(paths))

    for brightness_path in _CAPS_LED_CACHE[1]:
        try:
            fd = os.open(brightness_path, os.O_RDONLY)
            try:
                raw = os.pread(fd, 16, 0).strip()
            finally:
                os.close(fd)
            if int(raw or b"0") > 0:
                return True
        except FileNotFoundError:
            _CAPS_LED_CACHE = None
        except (OSError, ValueError):
            continue
    return False


def _md_escape(text: str) -> str:
    return re.sub(r"([*_`#\[\]])", r"\\\1", str(text))


def _sanitize_preset_name(raw_name: str) -> str:
    """Strip unsafe/control characters and reject dot-only or hidden names."""
    cleaned = re.sub(r'[\x00-\x1f\x7f\\/*?:"<>|]', "", raw_name.strip()).strip(". ")
    if not cleaned or cleaned in (".", ".."):
        return ""
    return cleaned


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
        self._data: dict[OptionCacheKey, tuple[Text, dict[str, tuple[int, int]]]] = {}
        self._uid_index: defaultdict[str, set[OptionCacheKey]] = defaultdict(set)
        self._kind_index: defaultdict[str, set[OptionCacheKey]] = defaultdict(set)
        self.hits = 0
        self.misses = 0

    def get(self, key: OptionCacheKey) -> tuple[Text, dict[str, tuple[int, int]]] | None:
        entry = self._data.pop(key, None)
        if entry is None:
            self.misses += 1
            return None
        self._data[key] = entry
        self.hits += 1
        return entry[0].copy(), entry[1]

    def put(
        self,
        key: OptionCacheKey,
        txt: Text,
        spans: dict[str, tuple[int, int]],
    ) -> Text:
        if key in self._data:
            self._drop(key)
        elif len(self._data) >= self._maxsize:
            self._drop(next(iter(self._data)))
        self._data[key] = (txt.copy(), spans)
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
        "_all_defaults", "_partial", "_matches", "_totals", "_preset_uids",
        "_configurable_uids", "_uid_set",
    )

    def __init__(self, app: Any) -> None:
        self._app = app
        self._current: dict[str, str] = {}
        self._exists: dict[str, bool] = {}
        self._defaults: dict[str, str] = {}
        self._expected: dict[str, dict[str, str]] = {}
        self._all_defaults: dict[str, bool] = {}
        self._partial: set[str] = set()
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
        self._partial.clear()
        self._matches.clear()
        self._totals.clear()
        self._preset_uids.clear()
        self._configurable_uids.clear()
        self._uid_set.clear()

        items: list[Any] = []
        presets: list[Any] = []
        for _t, _i, item in configurable_items:
            if is_trigger_item(item):
                continue
            match item.type_:
                case "preset":
                    presets.append(item)
                case "action" | "menu":
                    continue
                case _:
                    if getattr(item, "read_only", False):
                        continue
                    items.append(item)

        for item in items:
            uid = item.uid
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
            if p.preset_partial and not all_def:
                self._partial.add(puid)
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
            if (
                it.type_ in ("preset", "action", "menu")
                or is_trigger_item(it)
                or getattr(it, "read_only", False)
            ):
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
        if (
            item.type_ in ("preset", "action", "menu")
            or is_trigger_item(item)
            or getattr(item, "read_only", False)
        ):
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

        if not old_exists and not new_exists:
            return
        for puid in self._preset_uids:
            if puid in self._partial and uid not in self._expected[puid]:
                continue
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
        items_for_uid = (getattr(self._app, "_items_by_uid", None) or {}).get(uid)
        if items_for_uid:
            try:
                item = None
                for candidate in items_for_uid:
                    candidate_item = (
                        candidate[2]
                        if isinstance(candidate, tuple) and len(candidate) == 3
                        else candidate
                    )
                    try:
                        if self._app._get_item_engine_info(candidate_item) == self._app.default_engine_key:
                            item = candidate_item
                            break
                    except Exception:
                        item = item or candidate_item
                if item is None:
                    first = items_for_uid[0]
                    item = first[2] if isinstance(first, tuple) and len(first) == 3 else first
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
            if puid in self._partial and uid not in self._expected[puid]:
                continue
            total += 1
            if self._current.get(uid) == self._expected_for(puid, uid):
                matches += 1
        self._matches[puid] = matches
        self._totals[puid] = total


# =============================================================================
# COLOR & CELL UTILITIES
# =============================================================================
def _append_value_preview(
    text: Text,
    value: str,
    style: str = "",
    *,
    prefix: str = "",
    color_field: bool = False,
    swatch_style: str | None = None,
) -> None:
    """Render a swatch or the unchanged text, without changing stored values."""
    color = color_preview_hex(value.strip(), color_field)
    if color is None:
        text.append(prefix + value.rstrip(), style=style)
    else:
        if prefix:
            text.append(prefix, style=style)
        text.append("⬤", style=swatch_style or color)


def load_matugen_json(file_path: Path) -> dict[str, str] | None:
    if not file_path.exists():
        return None
    try:
        with file_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else None
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _fit_cells(s: str, width: int) -> str:
    """Truncate with ellipsis or right-pad string to an exact terminal cell width."""
    width = max(4, width)
    cur = cell_len(s)
    if cur <= width:
        return s + " " * (width - cur)
    target = width - 1
    out: list[str] = []
    used = 0
    for ch in s:
        ch_w = cell_len(ch)
        if used + ch_w > target:
            break
        out.append(ch)
        used += ch_w
    return "".join(out) + "…" + " " * max(0, width - used - 1)


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
# INTERACTIVE COMPONENTS
# =============================================================================
class ConfigOptionList(OptionList):
    BINDINGS = [
        Binding("enter", "app.submit_or_repeat_search", "Action / Next Match"),
        Binding("space", "app.submit_current", "Action"),
        Binding("n", "app.repeat_search(1)", "Next Match", show=False),
        Binding("N", "app.repeat_search(-1)", "Previous Match", show=False),
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
    _last_click_x: int | None = None
    _last_click_button: int = 1
    _last_hover_idx: int | None = None

    @property
    def current_option_id(self) -> str | None:
        """Resolve the live highlighted option ID directly from OptionList state."""
        idx = self.highlighted
        if idx is not None and 0 <= idx < self.option_count:
            try:
                return self.get_option_at_index(idx).id
            except OptionDoesNotExist:
                return None
        return self.last_highlighted_id

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
                self.scroll_to(y=self.max_scroll_y, animate=False)
                break

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self._last_click_x = getattr(event, "x", 0)
        self._last_click_button = getattr(event, "button", 1)
        self._mouse_down_highlight = self.highlighted

    def on_click(self, event: events.Click) -> None:
        if getattr(event, "button", 1) != 3:
            return
        line_idx = event.style.meta.get("option")
        if line_idx is None or not (0 <= line_idx < self.option_count):
            return
        opt = self.get_option_at_index(line_idx)
        parsed = self.app._get_item_from_id(opt.id)
        if not parsed:
            return
        _, _, item = parsed
        if item.type_ == "preset" and item.key.startswith("__user_preset_"):
            name = item.label.replace("User: ", "", 1)
            path = self.app.user_presets_dir / f"{name}.json"
            if path.exists():
                event.stop()
                self.app.open_file_externally(path, button=3, touch_first=False)

    def on_mouse_move(self, event: events.MouseMove) -> None:
        try:
            line_idx = event.style.meta.get("option")
            if line_idx == self._last_hover_idx:
                return
            self._last_hover_idx = line_idx
            new_tooltip = None

            if line_idx is not None and 0 <= line_idx < self.option_count:
                opt = self.get_option_at_index(line_idx)
                parsed = self.app._get_item_from_id(opt.id)
                if parsed:
                    _, _, item = parsed
                    if item.type_ == "preset" and item.key.startswith("__user_preset_"):
                        name = item.label.replace("User: ", "", 1)
                        path = self.app.user_presets_dir / f"{name}.json"
                        new_tooltip = (
                            f"Preset Path: {path}\n"
                            "Left/Right Click to open externally"
                        )

            if self.tooltip != new_tooltip:
                self.tooltip = new_tooltip
        except Exception:
            self._last_hover_idx = None
            if self.tooltip is not None:
                self.tooltip = None

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        self.app._update_scroll_indicators()

    def watch_max_scroll_y(self, old_value: float, new_value: float) -> None:
        self.app._update_scroll_indicators()

    def on_resize(self, event: events.Resize) -> None:
        self.app._update_scroll_indicators()
        self.app._schedule_width_refresh(self)


class ScrollIndicator(Label):
    _dragging: bool = False
    _max_scroll_y: float = 0
    _track_height: int = 0

    def update_scroll(
        self,
        scroll_y: float,
        max_scroll_y: float,
        viewport_height: float,
        virtual_height: float,
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

        thumb_size = max(1, round(self._track_height * (viewport_height / virtual_height)))
        max_pos = max(0, self._track_height - thumb_size)
        pos = round((scroll_y / max_scroll_y) * max_pos) if max_scroll_y > 0 else 0

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
        ol.scroll_y = round(ratio * self._max_scroll_y)


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

        def _unblink() -> None:
            self.remove_class("-active")
            self.refresh()

        self.set_timer(0.2, _unblink)


class FileLink(Label):
    path = reactive("")

    def render(self) -> Text:
        txt = Text()
        txt.append("󰈔 Edit File", style=self.app.theme_colors["accent"] + " bold underline")
        return txt

    def watch_path(self, new_val: str) -> None:
        self.display = bool(new_val)
        if new_val:
            self.tooltip = f"Edit externally:\n{new_val}"

    def on_click(self, event: events.Click) -> None:
        if not self.path or self.app._modal_active():
            return
        button = getattr(event, "button", 1)
        self.app.open_file_externally(self.path, 1 if button == 0 else button, touch_first=True)


class ModeButton(Label):
    def on_mount(self) -> None:
        self.update_mode()

    def update_mode(self) -> None:
        txt = Text()
        txt.append("Mode: ", style=self.app.theme_colors["fg"])
        mode_str = "AUTO" if self.app.auto_save else "BATCH"
        color = self.app.theme_colors["success"] if self.app.auto_save else self.app.theme_colors["warning"]
        txt.append(mode_str, style=color + " bold")

        if not self.app.auto_save and self.app.pending_commits:
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
            width = min(
                size.width,
                child.get_content_width(size, parent.screen.size) + child.styles.gutter.width,
            )
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
            with Horizontal(id="footer-file-target"):
                yield FileLink(id="file-link")
                yield Label("│", classes="footer-sep")
            yield ModeButton(id="footer-legend", classes="mode-btn")
            yield Label("", id="caps-indicator")
            yield Label("", id="pos-counter", classes="pos-counter-btn")
            yield Label("", id="status-bar")

    def _render_status_bar(self) -> None:
        try:
            for bar in self.query("#status-bar"):
                if self.status_msg:
                    txt = Text()
                    txt.append(" │ Status: ", style=self.app.theme_colors["accent"])
                    color = self.app.theme_colors.get(self.status_level, self.app.theme_colors["fg"])
                    txt.append(self.status_msg, style=color)
                    bar.update(txt)
                    bar.display = True
                else:
                    bar.display = False
        except Exception:
            pass

    def watch_status_msg(self, new_val: str) -> None:
        self._render_status_bar()

    def watch_status_level(self, new_val: str) -> None:
        if self.status_msg:
            self._render_status_bar()


class TabContainer(Horizontal):
    """Notifies the App to re-evaluate tab overflow when scrolled or resized."""

    def watch_scroll_x(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_x(old_value, new_value)
        self.app._schedule_tab_overflow_check()

    def watch_virtual_size(self, old_value: Size, new_value: Size) -> None:
        self.app._schedule_tab_overflow_check()

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        event.stop()
        if self.max_scroll_x > 0:
            target = min(float(self.max_scroll_x), self.scroll_x + 10)
            self.scroll_to(x=target, animate=False)
            self.app._schedule_tab_overflow_check()

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        event.stop()
        if self.scroll_x > 0:
            target = max(0.0, self.scroll_x - 10)
            self.scroll_to(x=target, animate=False)
            self.app._schedule_tab_overflow_check()


class CustomViewScroll(VerticalScroll):
    """Focusable scroll viewport for Rich custom views with Vim/page bindings."""

    can_focus = True

    BINDINGS = [
        Binding("j,down", "scroll_down", "Scroll Down", show=False),
        Binding("k,up", "scroll_up", "Scroll Up", show=False),
        Binding("page_down,ctrl+d", "page_down", "Page Down", show=False),
        Binding("page_up,ctrl+u", "page_up", "Page Up", show=False),
        Binding("g", "scroll_home", "Top", show=False),
        Binding("G", "scroll_end", "Bottom", show=False),
    ]


class CustomRichTabWidget(Static):
    """Widget slot for rendering custom Rich renderables within a DuskyTUI tab."""

    can_focus = False

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
        self,
        renderable_or_factory: Any,
        app_ref: Any = None,
        refresh_interval: float | None = None,
        *,
        collector=None,
        prepare=None,
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

    def _collector_engine_keys(self) -> set[tuple[str, str]]:
        if self.app_ref is None:
            return set()
        pool = getattr(self.app_ref, "engine_pool", None)
        if pool:
            return set(pool)
        default_key = getattr(self.app_ref, "default_engine_key", None)
        return {default_key} if default_key is not None else set()

    def _timer_tick(self) -> None:
        if not self._active or self._refresh_inflight:
            return
        if self.app_ref is not None:
            if getattr(self.app_ref, "_active_save_count", 0) > 0:
                return
            engine_locks = getattr(self.app_ref, "_engine_locks", {})
            if any(lock.locked() for lock in engine_locks.values()):
                return
            if (save_lock := getattr(self.app_ref, "_save_lock", None)) is not None and save_lock.locked():
                return
        self._request_refresh()

    async def _async_refresh(self) -> None:
        self._refresh_inflight = True
        try:
            while self._active and not self._is_unmounted:
                self._refresh_pending = False
                generation = self._generation
                try:
                    async with AsyncExitStack() as stack:
                        if self.app_ref is not None:
                            acquire = getattr(self.app_ref, "_acquire_engine_locks", None)
                            keys = self._collector_engine_keys()
                            if callable(acquire) and keys:
                                await acquire(stack, keys)
                            elif (save_lock := getattr(self.app_ref, "_save_lock", None)) is not None:
                                await stack.enter_async_context(save_lock)
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
                        self._apply_rendered_content(
                            Text(f"Error rendering custom view: {exc}", style="bold red")
                        )
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
        parent = self.parent
        saved_y = parent.scroll_y if isinstance(parent, VerticalScroll) else None
        self.update(res)
        if saved_y is not None and saved_y > 0:
            parent.scroll_to(y=saved_y, animate=False, immediate=True)


# =============================================================================
# MAIN APPLICATION
# =============================================================================
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
#footer-file-target { width: auto; height: 1; display: none; }
.footer-sep { color: $secondary; }

.footer-shortcut { padding: 0 1; background: transparent; }
.footer-shortcut:hover { text-style: bold; color: $foreground; background: $primary 25%; }
.footer-shortcut.-active { text-style: bold; color: $background; background: $primary; }

#caps-indicator { padding: 0 1 0 0; display: none; }
#status-bar { padding: 0 1; }

.mode-btn { padding: 0 1; background: transparent; }
.mode-btn:hover { text-style: bold; color: $foreground; background: $primary 25%; }

.pos-counter-btn { padding: 0 1 0 0; background: transparent; }

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
#password-error { color: $error; height: auto; margin-top: 1; display: none; }

#picker-list, #search-list, #diff-list, #shortcuts-list {
    height: 1fr; scrollbar-size: 0 0; background: transparent; border: none;
}

#search-list > .option-list--option { padding: 0 1; background: transparent; }
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
#hybrid-option-list > .option-list--option { padding: 0 1; background: transparent; }
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

        Binding("alt+1", "switch_tab_pos(0)", "Tab 1", show=False),
        Binding("alt+2", "switch_tab_pos(1)", "Tab 2", show=False),
        Binding("alt+3", "switch_tab_pos(2)", "Tab 3", show=False),
        Binding("alt+4", "switch_tab_pos(3)", "Tab 4", show=False),
        Binding("alt+5", "switch_tab_pos(4)", "Tab 5", show=False),
        Binding("alt+6", "switch_tab_pos(5)", "Tab 6", show=False),
        Binding("alt+7", "switch_tab_pos(6)", "Tab 7", show=False),
        Binding("alt+8", "switch_tab_pos(7)", "Tab 8", show=False),
        Binding("alt+9", "switch_tab_pos(8)", "Tab 9", show=False),
    ]

    auto_save = reactive(True)
    caps_lock = reactive(False)

    def watch_caps_lock(self, old: bool, new: bool) -> None:
        self._update_caps_indicator()

    def _update_caps_indicator(self) -> None:
        if not self.is_mounted:
            return
        try:
            badge = self.query_one("#caps-indicator", Label)
            if self.caps_lock:
                txt = Text()
                txt.append("│ ", style=self.theme_colors.get("fg") or "")
                bg = self.theme_colors.get("bg") or "#000000"
                warn = self.theme_colors.get("warning") or "yellow"
                txt.append(" 󰪛 CAPS ", style=f"bold {bg} on {warn}")
                badge.update(txt)
                badge.display = True
            else:
                badge.display = False
        except Exception:
            pass

    def _poll_caps_lock(self) -> None:
        active = _read_caps_lock_sysfs()
        if self.caps_lock != active:
            self.caps_lock = active

    def on_key(self, event: events.Key) -> None:
        if event.key in ("caps_lock", "capslock"):
            self._poll_caps_lock()
            return

        char = event.character
        if (
            char
            and len(char) == 1
            and char.isascii()
            and not event.key.startswith(("ctrl+", "alt+"))
            and (char.isupper() or (self.caps_lock and char.islower()))
        ):
            self._poll_caps_lock()

    def on_app_focus(self, event: events.AppFocus) -> None:
        self._poll_caps_lock()

    def __init__(
        self,
        engine_pool: Mapping[tuple[str, str], BaseEngine],
        default_engine_key: tuple[str, str],
        schema: dict[int, list[ConfigItem]],
        tabs: list[str] | dict[int, str],
        title: str = "Dusky Editor",
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
        **kwargs,
    ) -> None:
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
        self.editor_title = title
        self.schema_name = schema_name

        if theme_path:
            self.theme_path: Path | None = Path(theme_path).expanduser().resolve()
        else:
            self.theme_path = _xdg_config_home() / "matugen" / "generated" / "dusky.json"

        if isinstance(tabs, (list, tuple)):
            self.tabs: dict[int, str] = dict(enumerate(tabs))
        elif isinstance(tabs, dict):
            self.tabs = dict(tabs)
        else:
            self.tabs = {0: "General"}

        self._initial_tab = next(iter(self.tabs), None)
        self.enable_user_presets = bool(enable_user_presets and self.tabs)
        self.user_presets_tab_name = user_presets_tab
        self.user_presets_tab_idx = self._initial_tab if self._initial_tab is not None else 0

        if self.enable_user_presets:
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

        self.user_presets_dir = (_xdg_config_home() / "dusky" / "tui" / self.schema_name).resolve()

        self.pending_commits: set[tuple[int, int]] = set()
        self.undo_stack: deque[list[tuple[int, int, Any, Any]]] = deque(maxlen=50)
        self.redo_stack: deque[list[tuple[int, int, Any, Any]]] = deque(maxlen=50)

        self._option_cache = OptionTextCache(maxsize=2048)
        self._preset_matrix = PresetMatchMatrix(app=self)

        self._committed: dict[tuple[int, int], Any] = {}
        for t_idx, items in self.schema.items():
            for i_idx, item in enumerate(items):
                self._committed[(t_idx, i_idx)] = clone_value(item.value)

        self._engine_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._quiescent_event: asyncio.Event | None = None
        self._save_timers: dict[tuple[int, int], Timer] = {}
        self._pending_autosave_args: dict[tuple[int, int], tuple[ConfigItem, str, Any]] = {}
        self._indent_cache: dict[str, str] = {}
        self._row_hit_spans: dict[str, dict[str, tuple[int, int]]] = {}
        self._overflow_scheduled = False
        self._width_refresh_timer: Timer | None = None
        self._last_list_widths: dict[int, int] = {}

        self._preset_refresh_timer: Timer | None = None
        self._flashed_actions: set[str] = set()
        self._flashed_action_timers: dict[str, Timer] = {}
        self._deferred_apply_timers: set[Timer] = set()

        palette = self.get_css_variables()
        self.theme_colors = {
            "bg": palette["background"],
            "fg": palette["foreground"],
            "accent": palette["primary"],
            "error": palette["error"],
            "warning": palette["warning"],
            "success": palette["success"],
            "muted": palette["surface-lighten-2"],
            "info": palette["primary"],
        }

        self._last_theme_fingerprint: tuple[int, int, int] | None = None
        if self.theme_path:
            loaded_theme = load_matugen_json(self.theme_path)
            if loaded_theme:
                self.theme_colors.update(loaded_theme)
            try:
                st = self.theme_path.stat()
                self._last_theme_fingerprint = (st.st_mtime_ns, st.st_size, st.st_ino)
            except OSError:
                pass

        self._status_timer: Timer | None = None
        self._caps_poll_timer: Timer | None = None
        self._external_watch_timer: Timer | None = None
        self._external_watch_inflight = False
        self._preset_mtimes: dict[str, tuple[int, int, int]] = {}
        self._initial_presets_mtime_set = False
        self._telemetry_timer: Timer | None = None

        self._search_results: list[tuple[int, ConfigItem]] = []
        self._search_position = -1
        self._search_active = False

        self._cached_tab_bar: Horizontal | None = None
        self._cached_tabs_container: Horizontal | None = None
        self._cached_tab_left: Label | None = None
        self._cached_tab_right: Label | None = None

        self.auto_save = (default_mode.lower() == "auto")

        self.last_target_mtimes: dict[tuple[str, str], tuple[int, int, int] | None] = {}
        self.telemetry_engine: BaseEngine | None = None

        self._tab_populated: set[int] = set()
        self._tab_dirty: set[int] = set()
        self._tab_warmup_queue: deque[int] = deque()
        self._tab_warmup_queued: set[int] = set()
        self._tab_warmup_scheduled = False

        self._items_by_uid: dict[str, list[tuple[int, int, ConfigItem]]] = {}
        self._item_refs: dict[int, tuple[int, int]] = {}
        self._items_by_engine: dict[tuple[str, str], list[tuple[int, int, ConfigItem]]] = {}
        self._children_by_parent: defaultdict[tuple[int, str], list[ConfigItem]] = defaultdict(list)
        self._tab_item_index: dict[tuple[int, str], tuple[int, ConfigItem]] = {}
        self._configurable_items: list[tuple[int, int, ConfigItem]] = []
        self._preset_items: list[tuple[int, int, ConfigItem]] = []

        self._write_generation: dict[str, int] = {}
        self._active_save_count = 0
        self._save_tasks: set[asyncio.Task[Any]] = set()
        self._io_workers: set[asyncio.Task[Any]] = set()
        self._save_task_keys: dict[asyncio.Task[Any], set[str]] = {}
        self._save_auth_pending = 0
        self._save_failure_pending = False
        self._quit_after_save = False
        self._sudo_keepalive: Timer | None = None
        self._sudo_keepalive_task: asyncio.Task[Any] | None = None
        self._tty_action_busy = False

        self._preset_io_tasks: set[asyncio.Task[Any]] = set()
        self._preset_hot_reload_task: asyncio.Task[Any] | None = None
        self._action_tasks: set[asyncio.Task[Any]] = set()
        self._action_procs: set[Any] = set()
        self._action_cleanup_tasks: set[asyncio.Task[Any]] = set()
        self._action_shutdown_started = False
        self._action_shutdown_done = False
        self._action_shutdown_lock: asyncio.Lock | None = None

        self._deferred_started = False
        self._inventory_refreshing = False
        self._engine_info_cache: dict[tuple[str | None, str | None], tuple[str, str]] = {}
        self._init_boot_state()
        self._rebuild_indexes()
        self.apply_theme_to_engine()

    # =========================================================================
    # PER-ENGINE LOCKS & QUIESCENCE BARRIER
    # =========================================================================
    def _lock_for_engine(self, engine_key: tuple[str, str]) -> asyncio.Lock:
        lock = self._engine_locks.get(engine_key)
        if lock is None:
            lock = self._engine_locks[engine_key] = asyncio.Lock()
        return lock

    async def _acquire_engine_locks(
        self,
        stack: AsyncExitStack,
        engine_keys: set[tuple[str, str]] | list[tuple[str, str]],
    ) -> None:
        """Acquire per-engine locks in deterministic sorted order to prevent deadlocks."""
        for key in sorted(set(engine_keys)):
            await stack.enter_async_context(self._lock_for_engine(key))

    def _is_ui_quiescent(self) -> bool:
        return not (
            self._save_tasks
            or self._save_timers
            or self._save_auth_pending
            or self._modal_active()
        )

    def _notify_quiescent(self) -> None:
        if self._quiescent_event is not None and self._is_ui_quiescent():
            self._quiescent_event.set()

    async def _wait_for_ui_quiescent(self) -> None:
        if self._quiescent_event is None:
            self._quiescent_event = asyncio.Event()
        while not self._is_ui_quiescent():
            self._quiescent_event.clear()
            if self._is_ui_quiescent():
                break
            try:
                await asyncio.wait_for(self._quiescent_event.wait(), timeout=0.25)
            except TimeoutError:
                pass

    # =========================================================================
    # QUIT / MODAL GUARDS
    # =========================================================================
    def _promote_autosave_timers_to_pending(self) -> None:
        for (ti, ii), timer in list(self._save_timers.items()):
            timer.stop()
            item = self._get_schema_item(ti, ii)
            if item is None:
                continue
            src_eng = self._get_item_engine_info(item)
            self._bump_write_generation(self._uid_engine_key(item))
            for dup_t, dup_i, dup_item in self._items_by_uid.get(item.uid, ()):
                if self._get_item_engine_info(dup_item) == src_eng:
                    self._sync_pending(dup_t, dup_i, dup_item)
        self._save_timers.clear()
        self._pending_autosave_args.clear()
        self._notify_quiescent()

    def on_screen_resume(self, event: events.ScreenResume) -> None:
        self._notify_quiescent()

    def _track_preset_io_task(self, task: asyncio.Task[Any]) -> asyncio.Task[Any]:
        self._preset_io_tasks.add(task)

        def done(completed: asyncio.Task[Any]) -> None:
            self._preset_io_tasks.discard(completed)
            if not completed.cancelled() and (error := completed.exception()) is not None:
                LOGGER.error("Preset I/O task failed", exc_info=error)
            self._maybe_finish_quit()

        task.add_done_callback(done)
        return task

    def action_quit(self) -> None:
        if self._modal_active():
            if isinstance(self.screen, UnsavedChangesDialog):
                self.screen.dismiss("cancel")
            elif isinstance(self.screen, ConfirmDialog):
                self.screen.dismiss(False)
            else:
                self.screen.dismiss(None)
            return

        if self._quit_after_save:
            return

        if (
            self.pending_commits
            and not self._save_tasks
            and not self._save_auth_pending
            and (not self.auto_save or not self._save_timers)
        ):
            def on_reply(reply: str) -> None:
                if reply == "save":
                    self._quit_after_save = True
                    self.call_later(self.action_save_batch)
                elif reply == "discard":
                    try:
                        self._cancel_background_actions()
                    except Exception:
                        pass
                    self.exit()

            self.push_screen(
                UnsavedChangesDialog(self._pending_setting_count()),
                on_reply,
            )
            return

        if self.auto_save and self._save_timers:
            self._promote_autosave_timers_to_pending()
            self._quit_after_save = True
            self.action_save_batch()
            return

        if self._save_tasks or self._save_auth_pending or self._preset_io_tasks:
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
            view,
            app_ref=self,
            refresh_interval=settings.get("interval"),
            collector=settings.get("collect"),
            prepare=settings.get("prepare"),
            classes="custom-rich-content",
            id=f"custom-view-{tab_idx}",
        )
        return [CustomViewScroll(rich_widget, classes="custom-view-scroll", id=f"custom-scroll-{tab_idx}")]

    def _notice_widgets(self, tab_idx: int, *, bottom: bool = False) -> list[Widget]:
        notices = self.tab_notices.get(tab_idx, [])
        if isinstance(notices, dict):
            notices = [notices]
        return [
            NoticeBox(
                notice.get("message", ""),
                level=notice.get("level", "info"),
                id=f"notice-{tab_idx}-{index}" + ("-bot" if bottom else ""),
            )
            for index, notice in enumerate(notices)
            if (notice.get("position", "top") == "bottom") == bottom
        ]

    def _option_list_widgets(self, tab_idx: int) -> list[Widget]:
        return [
            ConfigOptionList(id=f"list-{tab_idx}"),
            Vertical(ScrollIndicator("", id=f"indicator-{tab_idx}"), classes="indicator-column"),
        ]

    async def _ensure_option_list(self, tab_idx: int) -> None:
        if task := self._option_mount_tasks.get(tab_idx):
            await asyncio.shield(task)
            return
        spec = self._custom_spec(tab_idx)
        if spec is not None and not (isinstance(spec, dict) and spec.get("show_options", False)):
            return
        if self.query(f"#list-{tab_idx}"):
            return

        async def mount_list() -> None:
            nodes = []
            try:
                pane = self.query_one(f"#tab-{tab_idx}")
                wrapper = Horizontal(
                    *self._option_list_widgets(tab_idx),
                    id=f"list-wrapper-{tab_idx}",
                    classes="list-wrapper custom-options" if spec is not None else "list-wrapper",
                )
                nodes.append(wrapper)
                bottom = next(
                    (
                        child for child in pane.children
                        if child.id and child.id.startswith(f"notice-{tab_idx}-") and child.id.endswith("-bot")
                    ),
                    None,
                )
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
            async def mount_body() -> None:
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
                view.set_active(
                    tab_idx == current and self._engines_for_tab(tab_idx).issubset(self._loaded_engines)
                )

    def compose(self) -> ComposeResult:
        with Vertical(id="main-box"):
            with Horizontal(id="tab-bar"):
                yield Label("   ", id="tab-left", classes="tab-arrow")
                with TabContainer(id="tabs-container"):
                    yield Tabs(
                        *[Tab(name, id=f"tab-id-{i}") for i, name in self.tabs.items()],
                        id="tabs",
                    )
                yield Label("   ", id="tab-right", classes="tab-arrow")

            yield Label("", id="telemetry-banner")

            with Horizontal(id="content-area"):
                with ContentSwitcher(
                    initial=f"tab-{self._initial_tab}" if self._initial_tab is not None else None,
                    id="content-switcher",
                ):
                    for i in self.tabs:
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
                                yield Vertical(
                                    *body,
                                    id=f"custom-body-{i}",
                                    classes="custom-body-with-options" if settings.get("show_options") else "custom-body",
                                )
                            if i == self._initial_tab and (custom_view is None or settings.get("show_options", False)):
                                with Horizontal(
                                    id=f"list-wrapper-{i}",
                                    classes="list-wrapper custom-options" if custom_view is not None else "list-wrapper",
                                ):
                                    yield from self._option_list_widgets(i)

                            if eager_notices:
                                yield from self._notice_widgets(i, bottom=True)

                yield Vertical(id="help-panel")

            with Vertical(id="bottom-dock"):
                yield Input(
                    id="local-search",
                    placeholder=" Jump to option... (Enter: accept, n/N: next/previous, Esc: clear)",
                )
                yield AppFooter(id="footer")

    # =========================================================================
    # ENGINE / STATE HELPERS
    # =========================================================================
    def _sync_pending(self, tab_idx: int, item_idx: int, item: ConfigItem) -> None:
        key = (tab_idx, item_idx)
        baseline = self._committed.get(key, item.default)
        if (
            item.values_equal(item.value, baseline)
            and not is_trigger_item(item)
            and not self._active_save_count
        ):
            self.pending_commits.discard(key)
        else:
            self.pending_commits.add(key)

    def _pending_setting_count(self) -> int:
        return len({
            self._uid_engine_key(item)
            for tab_idx, item_idx in self.pending_commits
            if (item := self._get_schema_item(tab_idx, item_idx)) is not None
        })

    def _item_is_pending(self, item: ConfigItem) -> bool:
        ref = self._item_refs.get(id(item))
        baseline = self._committed.get(ref, item.initial_value)
        return not item.values_equal(item.value, baseline)

    def _current_tab_index(self) -> int | None:
        try:
            switcher = self.query_one(ContentSwitcher)
            if switcher.current and isinstance(switcher.current, str) and switcher.current.startswith("tab-"):
                return int(switcher.current.split("-")[1])
        except Exception:
            return None
        return None

    def _flash_action_ui(self, item: ConfigItem, duration: float = 0.8) -> None:
        uid = item.uid
        self._flashed_actions.add(uid)
        self._option_cache.invalidate_uid(uid, include_presets=False)
        for t_idx, i_idx, itm in self._items_by_uid.get(uid, ()):
            self._refresh_single_ui(t_idx, i_idx, itm)

        if timer := self._flashed_action_timers.get(uid):
            timer.stop()

        def unflash() -> None:
            self._flashed_actions.discard(uid)
            self._flashed_action_timers.pop(uid, None)
            self._option_cache.invalidate_uid(uid, include_presets=False)
            for t_idx, i_idx, itm in self._items_by_uid.get(uid, ()):
                self._refresh_single_ui(t_idx, i_idx, itm)

        self._flashed_action_timers[uid] = self.set_timer(duration, unflash)

    def _on_item_value_changed(self, item: ConfigItem) -> None:
        is_stateless = item.type_ in ("preset", "action", "menu") or is_trigger_item(item)
        self._option_cache.invalidate_uid(item.uid, include_presets=not is_stateless)
        if not is_stateless:
            try:
                if self._get_item_engine_info(item) == self.default_engine_key:
                    self._preset_matrix.on_item_changed(item)
            except Exception:
                self._preset_matrix.on_item_changed(item)
        cur = self._current_tab_index()
        if cur is not None:
            self._tab_dirty.add(cur)

    def _has_pending_save_for_key(self, uek: str) -> bool:
        for item, _value, _old in self._pending_autosave_args.values():
            if self._uid_engine_key(item) == uek:
                return True
        for tab_idx, item_idx in self.pending_commits:
            item = self._get_schema_item(tab_idx, item_idx)
            if item is not None and self._uid_engine_key(item) == uek:
                return True
        return any(uek in keys for keys in self._save_task_keys.values())

    def _get_item_engine_info(self, item: ConfigItem) -> tuple[str, str]:
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

    def _lookup_state(self, state: dict, item: ConfigItem) -> Any:
        """Canonical state lookup without leaking DEFAULT keys into explicitly scoped items."""
        if not state:
            return _STATE_MISSING

        scope = item.scope or "DEFAULT"
        if scope == "DEFAULT":
            candidates = (
                f"DEFAULT/{item.key}",
                f"DEFAULT.{item.key}",
                item.key,
            )
        else:
            candidates = (
                f"{scope}/{item.key}",
                f"{scope}.{item.key}",
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
    def _rebuild_indexes(self, *, rebuild_presets: bool = True) -> None:
        self._engine_info_cache.clear()
        self._items_by_uid.clear()
        self._item_refs.clear()
        self._items_by_engine.clear()
        self._children_by_parent.clear()
        self._tab_item_index.clear()
        self._configurable_items.clear()
        self._preset_items.clear()
        register_engine = getattr(self.engine_pool, "register", None)

        for t_idx, items in self.schema.items():
            for i_idx, item in enumerate(items):
                self._prepare_color_preview(item)
                self._item_refs[id(item)] = (t_idx, i_idx)
                uid = item.uid

                self._items_by_uid.setdefault(uid, []).append((t_idx, i_idx, item))
                if item.is_parent or item.type_ == "menu":
                    self._tab_item_index[(t_idx, uid)] = (i_idx, item)
                    if item.key:
                        self._tab_item_index.setdefault((t_idx, item.key), (i_idx, item))

                if item.parent_ref:
                    self._children_by_parent[(t_idx, str(item.parent_ref))].append(item)

                try:
                    ekey = self._get_item_engine_info(item)
                except Exception:
                    ekey = self.default_engine_key

                self._items_by_engine.setdefault(ekey, []).append((t_idx, i_idx, item))
                if callable(register_engine) and ekey not in self.engine_pool:
                    register_engine(*ekey)

                if (
                    item.type_ not in ("action", "preset", "menu")
                    and not item.read_only
                    and not is_trigger_item(item)
                ):
                    self._configurable_items.append((t_idx, i_idx, item))

                if item.type_ == "preset":
                    self._preset_items.append((t_idx, i_idx, item))

        if rebuild_presets:
            global_cfg = [
                c for c in self._configurable_items
                if self._get_item_engine_info(c[2]) == self.default_engine_key
            ]
            self._preset_matrix.rebuild(global_cfg + self._preset_items)

    # =========================================================================
    # PRESET MATCHING
    # =========================================================================
    def _get_preset_match_ratio(self, preset_item: ConfigItem) -> float:
        return self._preset_matrix.ratio(preset_item)

    def _is_preset_active(self, preset_item: ConfigItem) -> bool:
        return self._get_preset_match_ratio(preset_item) == 1.0

    def _refresh_presets_ui(self) -> None:
        for t_idx, i_idx, itm in self._preset_items:
            self._refresh_single_ui(t_idx, i_idx, itm)

    def _get_parent_children_status(
        self,
        parent_item: ConfigItem,
        tab_idx: int | None = None,
    ) -> tuple[bool, bool]:
        any_modified = False
        any_pending = False
        if parent_item.type_ not in ("menu", "action", "preset") and not is_trigger_item(parent_item):
            any_modified = not parent_item.values_equal(parent_item.value, parent_item.default)
            any_pending = self._item_is_pending(parent_item)

        if tab_idx is None:
            tab_idx = self._current_tab_index()

        def _settled() -> bool:
            return any_modified and (any_pending or self.auto_save)

        if _settled() or tab_idx is None:
            return any_modified, any_pending

        seen_items: set[int] = set()
        stack = [ref for ref in (parent_item.key, parent_item.uid) if ref]

        while stack:
            curr = str(stack.pop())
            for itm in self._children_by_parent.get((tab_idx, curr), ()):
                marker = id(itm)
                if marker in seen_items:
                    continue
                seen_items.add(marker)
                if itm.type_ not in ("menu", "action", "preset") and not is_trigger_item(itm):
                    if not itm.values_equal(itm.value, itm.default):
                        any_modified = True
                    if self._item_is_pending(itm):
                        any_pending = True
                    if _settled():
                        return any_modified, any_pending
                if itm.is_parent or itm.type_ == "menu":
                    if itm.key:
                        stack.append(itm.key)
                    stack.append(itm.uid)

        return any_modified, any_pending

    # =========================================================================
    # OPTION RENDERING
    # =========================================================================
    def _label_column_width(self, tab_idx: int | None) -> int:
        width = 0
        if tab_idx is not None:
            width = self._last_list_widths.get(tab_idx, 0)
            if width <= 0:
                try:
                    width = self.query_one(f"#list-{tab_idx}", ConfigOptionList).size.width
                except Exception:
                    width = 0
        if width <= 0:
            width = self.size.width
        if width <= 0:
            return 35
        return max(16, min(35, width - 28))

    def _schedule_width_refresh(self, ol: ConfigOptionList) -> None:
        try:
            tab_idx = int(ol.id.split("-")[1])
        except (AttributeError, IndexError, ValueError):
            return
        new_w = ol.size.width
        if new_w <= 0 or self._last_list_widths.get(tab_idx) == new_w:
            return
        self._last_list_widths[tab_idx] = new_w
        if tab_idx == self._current_tab_index() and tab_idx in self._tab_populated:
            if self._width_refresh_timer is not None:
                self._width_refresh_timer.stop()
            self._width_refresh_timer = self.set_timer(
                0.05, lambda tid=tab_idx: self._populate_option_list(tid)
            )
        elif tab_idx in self._tab_populated:
            self._tab_dirty.add(tab_idx)

    def _build_option(
        self,
        item: ConfigItem,
        is_highlighted: bool = False,
        indent_prefix: str = "",
        tab_idx: int | None = None,
        opt_id: str | None = None,
    ) -> Text:
        val_ser = item.serialize(item.value)
        def_ser = item.serialize(item.default)
        ratio_bucket = int(self._get_preset_match_ratio(item) * 10) if item.type_ == "preset" else -1
        is_flashed = item.uid in self._flashed_actions
        label_col = self._label_column_width(tab_idx)

        if item.is_parent or item.type_ == "menu":
            is_modified, is_pending = self._get_parent_children_status(item, tab_idx)
        elif is_trigger_item(item):
            is_pending = False
            is_modified = False
        else:
            is_pending = self._item_is_pending(item)
            is_modified = (val_ser != def_ser)

        engine_identity = self._get_item_engine_info(item)
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
                label_col,
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
                is_flashed,
            ),
        )

        hit = self._option_cache.get(cache_key)
        if hit is not None:
            cached_txt, cached_spans = hit
            if opt_id is not None:
                self._row_hit_spans[opt_id] = cached_spans
            return cached_txt

        txt = Text()
        spans: dict[str, tuple[int, int]] = {}
        exists = item.exists_in_target

        cursor = "❯ " if is_highlighted else "  "
        txt.append(cursor, style=f"{self.theme_colors['accent']} bold" if is_highlighted else "")

        ratio = 0.0
        is_active_preset = False
        is_deviated_preset = False
        if item.type_ == "preset":
            ratio = self._get_preset_match_ratio(item)
            is_active_preset = (ratio == 1.0)
            is_deviated_preset = (0.9 <= ratio < 1.0)

        # Tree indentation + fold chevron hit span.
        is_foldable = item.is_parent or item.type_ == "menu"
        chevron_start = txt.cell_len
        if indent_prefix:
            txt.append(indent_prefix, style=self.theme_colors["muted"])
            if is_foldable:
                txt.append("▼ " if item.expanded else "▶ ", style=f"{self.theme_colors['accent']} bold")
                spans["fold"] = (chevron_start + 1, txt.cell_len)
        elif is_foldable:
            txt.append("▼ " if item.expanded else "▶ ", style=f"{self.theme_colors['accent']} bold")
            spans["fold"] = (chevron_start + 1, txt.cell_len)
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
        elif not self.auto_save and is_pending:
            txt.append("[+] ", style=self.theme_colors["warning"])
        elif is_modified and exists:
            txt.append("✦  ", style=self.theme_colors["warning"])
        else:
            txt.append("·  ", style=self.theme_colors["muted"])

        # Tree-aware aligned label column.
        warning_marker = f"{_ICON_WARNING} " if item.warning_msg else ""
        indent_cells = cell_len(indent_prefix)
        warn_cells = cell_len(warning_marker)
        chevron_slack = 2 if (indent_prefix and not is_foldable) else 0
        avail_label = max(10, label_col - indent_cells + chevron_slack - warn_cells)
        label_start = txt.cell_len

        if exists:
            if item.type_ == "preset" and is_active_preset:
                label_style = f"{self.theme_colors['success']} bold"
            elif item.type_ == "preset" and is_deviated_preset:
                label_style = f"{self.theme_colors['warning']} bold" if is_highlighted else self.theme_colors["fg"]
            elif is_modified:
                label_style = f"{self.theme_colors['warning']} bold" if is_highlighted else self.theme_colors["warning"]
            elif item.is_parent or item.type_ == "menu":
                label_style = f"{self.theme_colors['accent']} bold" if is_highlighted else self.theme_colors["accent"]
            else:
                label_style = f"{self.theme_colors['fg']} bold" if is_highlighted else self.theme_colors["fg"]

            if warning_marker:
                txt.append(warning_marker, style=f"bold {self.theme_colors['warning']}")
            txt.append(_fit_cells(item.label, avail_label), style=label_style)
        else:
            label_style = (
                f"{self.theme_colors['muted']} strike bold"
                if is_highlighted
                else f"{self.theme_colors['muted']} strike"
            )
            raw_label = f"{warning_marker}{item.label} [Missing]"
            txt.append(
                _fit_cells(raw_label, max(10, label_col - indent_cells + chevron_slack)),
                style=label_style,
            )

        is_preset_action = item.key in ("__save_new_preset", "__import_new_preset")
        val_str = item.to_str(item.value)
        tail_start = txt.cell_len

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
                        style=f"bold {self.theme_colors['accent']}" if exists else f"{self.theme_colors['muted']} italic",
                    )
                spans["instant"] = (tail_start + 1, txt.cell_len)
            elif item.type_ == "action":
                txt.append(
                    "󰄬 Run" if is_flashed else "󰐊 Run",
                    style=(
                        f"bold {self.theme_colors['success']}"
                        if (exists and is_flashed)
                        else f"bold {self.theme_colors['accent']}"
                        if exists
                        else f"{self.theme_colors['muted']} italic"
                    ),
                )
                instant_start = label_start if is_preset_action else tail_start
                spans["instant"] = (instant_start + 1, txt.cell_len)
        else:
            accent = self.theme_colors["accent"] if exists else self.theme_colors["muted"]
            fg = self.theme_colors["fg"] if exists else self.theme_colors["muted"]
            match item.type_:
                case "bool":
                    if is_trigger_item(item):
                        opt0 = str(item.options[0]) if item.options else ""
                        opt0_lower = opt0.lower()
                        if opt0_lower.startswith("trigger:"):
                            btn_label = f"󰐊 {opt0[8:]}"
                        elif opt0_lower.startswith("copy:"):
                            btn_label = f"󰈔 {opt0[5:]}"
                        elif opt0_lower == "copy":
                            btn_label = "󰈔 Copy"
                        else:
                            btn_label = "󰐊 Apply"
                        is_triggered = bool(item.value) or is_flashed
                        btn_prefix = "󰄬 " if is_triggered else btn_label[:2]
                        btn_style = (
                            f"bold {self.theme_colors['success']}"
                            if is_triggered
                            else f"bold {self.theme_colors['accent']}"
                        )
                        _append_value_preview(
                            txt,
                            btn_label[2:],
                            f"{self.theme_colors['muted']} italic" if not exists else btn_style,
                            prefix=btn_prefix,
                            swatch_style=self.theme_colors["muted"] if not exists else None,
                        )
                        spans["instant"] = (tail_start + 1, txt.cell_len)
                    elif not exists:
                        txt.append("◉" if item.value else "◯", style=f"{self.theme_colors['muted']} italic")
                    elif item.value:
                        txt.append("◉", style=f"bold {self.theme_colors['success']}")
                    else:
                        txt.append("◯", style=f"dim {self.theme_colors['fg']}")
                case "string":
                    if val_str == "":
                        txt.append(f"[{_ICON_PENCIL}] Unset", style=f"italic {self.theme_colors['muted']}")
                    else:
                        _append_value_preview(
                            txt, val_str, accent, prefix=f"[{_ICON_PENCIL}] ",
                            swatch_style=None if exists else self.theme_colors["muted"],
                        )
                case "picker":
                    _append_value_preview(
                        txt, val_str, accent, prefix="[+] ",
                        swatch_style=None if exists else self.theme_colors["muted"],
                    )
                case "color":
                    _append_value_preview(
                        txt, val_str, accent, color_field=True,
                        swatch_style=None if exists else self.theme_colors["muted"],
                    )
                case _:
                    _append_value_preview(
                        txt, val_str, fg,
                        swatch_style=None if exists else self.theme_colors["muted"],
                    )

        if is_modified and is_highlighted and exists and item.type_ not in ("action", "preset"):
            txt.append("   ")
            reset_start = txt.cell_len
            txt.append("↩ Reset", style=f"italic {self.theme_colors['error']}")
            spans["reset"] = (reset_start + 1, txt.cell_len)

        if opt_id is not None:
            self._row_hit_spans[opt_id] = spans
        return self._option_cache.put(cache_key, txt, spans)

    def _invalidate_theme_cache(self) -> None:
        self._theme_version = getattr(self, "_theme_version", 0) + 1
        self._option_cache.clear()

    # =========================================================================
    # USER PRESETS
    # =========================================================================
    def _scan_preset_mtimes(self) -> dict[str, tuple[int, int, int]]:
        result: dict[str, tuple[int, int, int]] = {}
        if not self.user_presets_dir.exists():
            return result
        try:
            with os.scandir(self.user_presets_dir) as entries:
                for entry in entries:
                    if not entry.name.endswith(".json"):
                        continue
                    st = entry.stat()
                    result[entry.name] = (st.st_mtime_ns, st.st_size, st.st_ino)
        except OSError:
            pass
        return result

    def _read_user_presets_with_mtimes(
        self,
    ) -> tuple[list[tuple[str, dict[str, Any], str | None]], dict[str, tuple[int, int, int]]]:
        if not self.enable_user_presets:
            return [], {}

        self.user_presets_dir.mkdir(parents=True, exist_ok=True)
        records: list[tuple[str, dict[str, Any], str | None]] = []
        mtimes: dict[str, tuple[int, int, int]] = {}
        for file_path in sorted(
            (p for p in self.user_presets_dir.iterdir() if p.name.endswith(".json")),
            key=lambda p: p.stem.lower(),
        ):
            name = file_path.stem
            warning = None
            try:
                st = file_path.stat()
                mtimes[file_path.name] = (st.st_mtime_ns, st.st_size, st.st_ino)
                with file_path.open("r", encoding="utf-8") as stream:
                    payload = json.load(stream)
                if not isinstance(payload, dict):
                    payload = {"__INVALID__": True, "__ERROR__": "Expected JSON object"}
                    warning = "Invalid preset payload: expected JSON object"
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                payload = {"__INVALID__": True, "__ERROR__": str(exc)}
                warning = f"Unable to read preset: {exc}"
            records.append((name, payload, warning))
        return records, mtimes

    def _read_user_presets(self) -> list[tuple[str, dict[str, Any], str | None]]:
        return self._read_user_presets_with_mtimes()[0]

    def _load_user_presets(self) -> None:
        """Reload preset records synchronously for callers already on the UI thread."""
        preset_records, preset_mtimes = self._read_user_presets_with_mtimes()
        self._apply_user_presets(preset_records)
        self._preset_mtimes = preset_mtimes
        self._initial_presets_mtime_set = True

    def _apply_user_presets(
        self,
        preset_records: list[tuple[str, dict[str, Any], str | None]],
    ) -> None:
        if not self.enable_user_presets or not self.tabs:
            return

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
            preset_payload={"__ALL_DEFAULTS__": True},
            confirm_message="Are you sure you want to reset ALL settings to their initial factory defaults?",
            extended_help="Resets all configuration options across all tabs back to their original factory defaults.",
        )
        reset_btn.exists_in_target = True

        save_btn = ConfigItem(
            label="󰆓 Save as Preset",
            key="__save_new_preset",
            scope="DEFAULT",
            type_="action",
            default=None,
            group="User Presets",
            extended_help="Click here to save the current configuration state as a new reusable preset.",
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
            ),
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
                warning_msg=warning,
            )
            new_item.exists_in_target = True
            user_preset_items.append(new_item)

        if self.user_presets_tab_idx not in self.schema:
            self.schema[self.user_presets_tab_idx] = []

        self.schema[self.user_presets_tab_idx].extend(user_preset_items)

    # =========================================================================
    # EXTERNAL EDITING & INTERACTIVE TTY
    # =========================================================================
    def open_file_externally(
        self,
        file_path: Path | str,
        button: int = 1,
        touch_first: bool = False,
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
                if shutil.which("xdg-open"):
                    subprocess.Popen(
                        ["xdg-open", str(expanded_path)],
                        start_new_session=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    self.notify_status("xdg-open not found in PATH.", level="warning")
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

    def run_suspended_interactive(
        self,
        cmd: list[str] | str,
        shell: bool = False,
    ) -> subprocess.CompletedProcess | None:
        if self._tty_action_busy:
            self.notify_status("Another TTY action is already running.", level="warning")
            return None

        self._tty_action_busy = True
        stdin_fd: int | None = None
        saved_termios = None
        if sys.stdin.isatty():
            try:
                stdin_fd = sys.stdin.fileno()
                saved_termios = termios.tcgetattr(stdin_fd)
                working = termios.tcgetattr(stdin_fd)
                working[0] &= ~termios.IXON
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
            self._tty_action_busy = False
            self.refresh(layout=True)

    # =========================================================================
    # MOUNT / UNMOUNT
    # =========================================================================
    @override
    async def on_mount(self) -> None:
        self._quiescent_event = asyncio.Event()
        self._quiescent_event.set()

        self.query_one("#main-box").border_title = f" {self.editor_title} "

        self._cached_tab_bar = self.query_one("#tab-bar", Horizontal)
        self._cached_tabs_container = self.query_one("#tabs-container", Horizontal)
        self._cached_tab_left = self.query_one("#tab-left", Label)
        self._cached_tab_right = self.query_one("#tab-right", Label)

        try:
            self.query_one("#shortcut-ctrl-s").display = not self.auto_save
            self.query_one("#shortcut-R").display = True
        except Exception:
            pass

        self.run_deferred_boot(initial_tab=self._initial_tab)

        if first_ol := self.current_option_list:
            first_ol.focus()
            self._update_pagination(first_ol)

        self._poll_caps_lock()
        self._update_caps_indicator()
        self._caps_poll_timer = self.set_interval(2.0, self._poll_caps_lock)
        self._external_watch_timer = self.set_interval(1.0, self.watch_external_changes)

        self.call_after_refresh(self.check_tab_overflow)
        self.call_after_refresh(self._update_scroll_indicators)
        self._update_footer_legend()

        if self.global_popup:
            def show_popup() -> None:
                if isinstance(self.global_popup, dict):
                    msg = self.global_popup.get("message", "")
                    title = self.global_popup.get("title", "System Notice")
                    level = self.global_popup.get("level", "info")
                    btn_text = self.global_popup.get("btn_text", " I Understand ")
                    if self.global_popup.get("require_confirm", False):
                        def on_confirm(confirmed: bool) -> None:
                            if not confirmed and self.global_popup.get("cancel_quits", False):
                                self.action_quit()
                        self.push_screen(ConfirmDialog(msg, title=title, level=level), on_confirm)
                    else:
                        self.push_screen(AlertDialog(msg, title=title, level=level, btn_text=btn_text))
                else:
                    self.push_screen(AlertDialog(str(self.global_popup), title="System Notice", level="warning"))
            self.call_after_refresh(show_popup)

    async def on_unmount(self) -> None:
        for timer in (
            self._caps_poll_timer,
            self._external_watch_timer,
            self._telemetry_timer,
            self._status_timer,
            self._preset_refresh_timer,
            self._width_refresh_timer,
            self._sudo_keepalive,
        ):
            if timer is not None:
                timer.stop()
        for timer in self._save_timers.values():
            timer.stop()
        self._save_timers.clear()
        for timer in self._flashed_action_timers.values():
            timer.stop()
        self._flashed_action_timers.clear()
        for timer in self._deferred_apply_timers:
            timer.stop()
        self._deferred_apply_timers.clear()

        if self._sudo_keepalive_task is not None and not self._sudo_keepalive_task.done():
            self._sudo_keepalive_task.cancel()
            await asyncio.gather(self._sudo_keepalive_task, return_exceptions=True)

        if callable(detach := getattr(self.engine_pool, "detach_app", None)):
            detach()

        self.workers.cancel_all()
        try:
            await self.workers.wait_for_complete()
        except Exception:
            pass

        tasks = [
            *self._save_tasks,
            *self._preset_io_tasks,
            *self._custom_refresh_tasks,
            *self._custom_mount_tasks.values(),
            *self._option_mount_tasks.values(),
        ]
        if self._preset_hot_reload_task is not None:
            tasks.append(self._preset_hot_reload_task)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._io_workers:
            await asyncio.wait(tuple(self._io_workers))
        initialized_values = getattr(self.engine_pool, "initialized_values", None)
        engines = initialized_values() if callable(initialized_values) else self.engine_pool.values()
        for engine in engines:
            if callable(shutdown := getattr(engine, "shutdown", None)):
                shutdown()
        await self._shutdown_background_actions()

    # =========================================================================
    # BOOT & DEFERRED DISCOVERY
    # =========================================================================
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

    def _prepare_color_preview(self, item: ConfigItem, state: dict | None = None) -> None:
        if item.read_only:
            return
        if is_trigger_item(item):
            option = str(item.options[0])
            if option.lower().startswith(("trigger:", "copy:")):
                color_preview_hex(option.split(":", 1)[1], False)
        elif item.type_ in ("string", "cycle", "picker", "color"):
            raw = self._lookup_state(state, item) if state is not None else _STATE_MISSING
            value = item.value if raw is _STATE_MISSING else item.deserialize(raw)
            color_preview_hex(str(value), item.type_ == "color")

    def _load_one_engine_sync(self, ekey: tuple[str, str]) -> Any:
        eng = self.engine_pool[ekey]
        if self.deferred_load and hasattr(eng, "load_state_for_units"):
            items = [
                item for _tab, _index, item in self._items_by_engine.get(ekey, ())
                if item.type_ not in ("menu", "action", "preset")
            ]
            state = eng.load_state_for_units(
                [item.key for item in items if item.scope == "user"],
                [item.key for item in items if item.scope == "system"],
            )
        else:
            state = eng.load_state()
        for _tab, _index, item in self._items_by_engine.get(ekey, ()):
            self._prepare_color_preview(item, state)
        return state

    async def _load_engine_states(
        self,
        keys: set[tuple[str, str]],
        loader: Any = None,
    ) -> tuple[dict[tuple[str, str], Any], dict[tuple[str, str], str]]:
        states: dict[tuple[str, str], Any] = {}
        errors: dict[tuple[str, str], str] = {}
        if not keys:
            return states, errors
        loader = self._load_one_engine_sync if loader is None else loader

        async def load(ekey: tuple[str, str]) -> None:
            try:
                states[ekey] = await self._run_save_io(loader, ekey)
            except Exception as exc:
                errors[ekey] = f"{type(exc).__name__}: {exc}"

        if len(keys) == 1:
            await load(next(iter(keys)))
        else:
            async with asyncio.TaskGroup() as group:
                for ekey in keys:
                    group.create_task(load(ekey))
        return states, errors

    def _apply_states_to_tab(
        self,
        tab_idx: int,
        states: dict[tuple[str, str], Any],
        *,
        update_presets: bool = True,
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
        if update_presets and freshly:
            global_fresh = [
                it for it in freshly
                if self._get_item_engine_info(it) == self.default_engine_key
            ]
            if global_fresh:
                self._preset_matrix.ingest_items(global_fresh)

    def _mark_boot_complete_if_done(self) -> None:
        was_complete = self._boot_complete
        self._boot_complete = (
            not self._pending_engine_loads
            and set(self.engine_pool).issubset(self._loaded_engines | set(self._failed_engines))
        )
        if self._boot_complete and not was_complete:
            for tab_idx in self.tabs:
                if (
                    tab_idx not in self._tab_data_ready
                    and self._engines_for_tab(tab_idx).issubset(self._loaded_engines)
                ):
                    self._apply_states_to_tab(tab_idx, self._states, update_presets=False)

            global_cfg = [
                c for c in self._configurable_items
                if self._get_item_engine_info(c[2]) == self.default_engine_key
            ]
            self._preset_matrix.rebuild(global_cfg + self._preset_items)
            self._option_cache.invalidate_presets()
            self._refresh_presets_ui()
            self._update_file_link()

            for ekey in self.engine_pool:
                if ekey not in self._loaded_engines:
                    continue
                engine = self.engine_pool[ekey]
                if hasattr(engine, "get_telemetry"):
                    self.telemetry_engine = engine
                    break

            if self.telemetry_engine and self._telemetry_timer is None:
                self.query_one("#telemetry-banner").display = True
                self._telemetry_timer = self.set_interval(1.0, self.update_telemetry)

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

            prospective_schema = self.schema | (new_items or {})
            if new_items and callable(register := getattr(self.engine_pool, "register", None)):
                for rows in new_items.values():
                    for item in rows:
                        register(*self._get_item_engine_info(item))

            while True:
                if new_items:
                    await self._wait_for_ui_quiescent()

                engine_keys = set(self.engine_pool)
                async with AsyncExitStack() as stack:
                    await self._acquire_engine_locks(stack, engine_keys)
                    use_prefetched = default_state is not None and self._write_generation == writes_before

                    def load_one(key, engine):
                        if use_prefetched and key == self.default_engine_key:
                            return default_state
                        if new_items and hasattr(engine, "load_state_for_units"):
                            items = [
                                item for rows in prospective_schema.values() for item in rows
                                if item.type_ not in ("menu", "action", "preset")
                                and self._get_item_engine_info(item) == key
                            ]
                            return engine.load_state_for_units(
                                [item.key for item in items if item.scope == "user"],
                                [item.key for item in items if item.scope == "system"],
                            )
                        return engine.load_state()

                    has_schema_override = hasattr(self.engine_pool, "_schema_override")
                    if has_schema_override:
                        self.engine_pool._schema_override = prospective_schema
                    try:
                        states, errors = await self._load_engine_states(
                            engine_keys, lambda key: load_one(key, self.engine_pool[key])
                        )
                        if errors:
                            raise RuntimeError(f"Deferred engine reads failed: {errors}")
                        if use_prefetched and self._write_generation != writes_before:
                            use_prefetched = False
                            states[self.default_engine_key] = await self._run_save_io(
                                load_one, self.default_engine_key, self.engine_pool[self.default_engine_key]
                            )
                    finally:
                        if has_schema_override:
                            self.engine_pool._schema_override = None

                if not new_items or self._is_ui_quiescent():
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
        if self._modal_active() or not self._boot_complete or self._inventory_refreshing:
            return
        self._inventory_refreshing = True
        if self.deferred_load:
            self._run_deferred_load(manual_refresh=True)
        else:
            self._run_state_refresh()

    @work(exclusive=True, group="manual-state-refresh", exit_on_error=False)
    async def _run_state_refresh(self) -> None:
        try:
            engine_keys = set(self.engine_pool)
            async with AsyncExitStack() as stack:
                await self._acquire_engine_locks(stack, engine_keys)
                states, errors = await self._load_engine_states(engine_keys)
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

    def _reconcile_engine_state(
        self,
        engine_key: tuple[str, str],
        state: dict[str, Any] | None,
        *,
        expected_generations: dict[str, int] | None = None,
        require_initial_loaded: bool = True,
    ) -> tuple[bool, bool]:
        """Reconcile an engine snapshot into RAM; returns (changed_any, all_accepted)."""
        items_by_uek: defaultdict[str, list[tuple[int, int, ConfigItem]]] = defaultdict(list)
        for t_idx, i_idx, item in self._items_by_engine.get(engine_key, ()):
            if item.type_ in ("action", "preset", "menu"):
                continue
            if require_initial_loaded and not item._initial_loaded:
                continue
            items_by_uek[self._uid_engine_key(item)].append((t_idx, i_idx, item))

        changed_any = False
        all_accepted = True
        for uek, grouped_items in items_by_uek.items():
            if self._has_pending_save_for_key(uek):
                all_accepted = False
                continue
            if (
                expected_generations is not None
                and self._write_generation.get(uek, 0) != expected_generations.get(uek, 0)
            ):
                all_accepted = False
                continue

            group_changed = False
            for t_idx, i_idx, item in grouped_items:
                if state is None:
                    if item.exists_in_target:
                        item.exists_in_target = False
                        self._on_item_value_changed(item)
                        group_changed = True
                    self._committed[(t_idx, i_idx)] = clone_value(item.value)
                    continue

                raw = self._lookup_state(state, item)
                if raw is not _STATE_MISSING:
                    new_val = item.deserialize(raw)
                    expected_exists = True
                else:
                    expected_exists = not self.hide_missing_items and item.default != "nil"
                    new_val = clone_value(item.default) if expected_exists else item.value

                if not item.values_equal(item.value, new_val) or item.exists_in_target != expected_exists:
                    item.value = clone_value(new_val)
                    item.exists_in_target = expected_exists
                    self._on_item_value_changed(item)
                    group_changed = True
                self._committed[(t_idx, i_idx)] = clone_value(new_val)

            if group_changed:
                self._bump_write_generation(uek)
                changed_any = True

        return changed_any, all_accepted

    def _apply_refreshed_states(self, states: dict) -> None:
        self._states.update(states)
        self._loaded_engines.update(states)
        for key in states:
            self._failed_engines.pop(key, None)

        for tab_idx in self.tabs:
            if (
                tab_idx not in self._tab_data_ready
                and self._engines_for_tab(tab_idx).issubset(self._loaded_engines)
            ):
                self._apply_states_to_tab(tab_idx, self._states)

        for engine_key, state in states.items():
            self._reconcile_engine_state(engine_key, state)

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
            preset_records, preset_mtimes = await asyncio.to_thread(self._read_user_presets_with_mtimes)
            self._preset_mtimes = preset_mtimes
            self._initial_presets_mtime_set = True
        except OSError as exc:
            preset_records = []
            self.notify_status(f"Unable to load user presets: {exc}", level="error")
        self._apply_user_presets(preset_records)
        self._rebuild_indexes(rebuild_presets=True)

        need_now = self._engines_for_tab(initial_tab) if (self.tabs and initial_tab is not None) else set()
        deferred = set(self.engine_pool) - need_now

        self._pending_engine_loads |= deferred
        async with asyncio.TaskGroup() as group:
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

            if self.tabs and initial_tab is not None:
                await asyncio.sleep(0)
                self._populate_option_list(initial_tab)
                self._tab_populated.add(initial_tab)
                self._activate_custom_views()
                self.call_after_refresh(self._queue_ready_tabs_for_warmup)

            self._mark_boot_complete_if_done()

    async def _load_engines_async(self, engine_keys: set[tuple[str, str]]) -> None:
        if not engine_keys:
            return
        states, errors = await self._load_engine_states(engine_keys)
        self.post_message(EnginesLoaded(states=states, attempted=engine_keys, errors=errors))

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
            if cur not in self._tab_populated or cur in self._tab_dirty:
                self._populate_option_list(cur)
                self._tab_populated.add(cur)
                self._tab_dirty.discard(cur)
            self._focus_search_target()

        self._mark_boot_complete_if_done()
        self._activate_custom_views()
        self._queue_ready_tabs_for_warmup()

    def _queue_ready_tabs_for_warmup(self) -> None:
        current = self._current_tab_index()
        for tab_idx in self.tabs:
            if tab_idx == current or not self.schema.get(tab_idx):
                continue
            if not self._engines_for_tab(tab_idx).issubset(self._loaded_engines):
                continue
            if tab_idx in self._tab_populated and tab_idx not in self._tab_dirty:
                continue
            if tab_idx in self._tab_warmup_queued:
                continue
            if not self.query(f"#list-{tab_idx}"):
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
            if tab_idx in self._tab_populated and tab_idx not in self._tab_dirty:
                continue
            if tab_idx not in self._tab_data_ready:
                self._apply_states_to_tab(tab_idx, self._states)
            self._populate_option_list(tab_idx)
            self._tab_populated.add(tab_idx)
            self._tab_dirty.discard(tab_idx)
            break
        if self._tab_warmup_queue:
            self._tab_warmup_scheduled = True
            self.call_after_refresh(self._warm_next_tab)

    def require_boot_complete(self) -> bool:
        if self._boot_complete:
            if self._failed_engines:
                self.notify_status("A configuration backend failed to load; restart after fixing it.", level="error")
                return False
            return True
        self.notify_status("Still loading configuration backends — try again in a moment.", level="warning")
        return False

    # =========================================================================
    # TAB POPULATION & UNIFIED RECONCILIATION
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

            # Bottom-up O(N) visibility calculation for nested menus.
            visible = {
                idx for idx, item in enumerate(items)
                if (not option_groups or item.group in option_groups)
                and (
                    item.type_ in ("menu", "action", "preset")
                    or not self.hide_missing_items
                    or item.exists_in_target
                )
            }
            if self.hide_missing_items:
                menu_refs: dict[str, int] = {}
                for idx, item in enumerate(items):
                    if idx in visible and item.type_ == "menu":
                        menu_refs[item.uid] = idx
                        menu_refs.setdefault(item.key, idx)
                has_visible_leaf: set[int] = set()
                for idx, item in enumerate(items):
                    if idx not in visible or item.type_ == "menu":
                        continue
                    curr_ref = item.parent_ref
                    seen_p: set[int] = set()
                    while curr_ref and (p_idx := menu_refs.get(curr_ref)) is not None and p_idx not in seen_p:
                        seen_p.add(p_idx)
                        has_visible_leaf.add(p_idx)
                        curr_ref = items[p_idx].parent_ref
                for idx in list(visible):
                    if items[idx].type_ == "menu" and idx not in has_visible_leaf:
                        visible.discard(idx)

            options: list[Option] = []
            current_group = None
            first_item_id = None

            parents = {
                itm.uid: idx for idx, itm in enumerate(items)
                if idx in visible and (itm.is_parent or itm.type_ == "menu")
            }
            for idx, itm in enumerate(items):
                if idx in visible and (itm.is_parent or itm.type_ == "menu"):
                    parents.setdefault(itm.key, idx)
            children_map: defaultdict[int, list[tuple[int, ConfigItem]]] = defaultdict(list)
            root_items: list[tuple[int, ConfigItem]] = []
            parent_indices = {
                idx: parents[itm.parent_ref]
                for idx, itm in enumerate(items)
                if idx in visible and itm.parent_ref in parents and parents[itm.parent_ref] != idx
            }

            checked: set[int] = set()
            for start in tuple(parent_indices):
                chain: set[int] = set()
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

            prefix_key = f"item_{tab_idx}_"
            self._indent_cache = {k: v for k, v in self._indent_cache.items() if not k.startswith(prefix_key)}
            self._row_hit_spans = {k: v for k, v in self._row_hit_spans.items() if not k.startswith(prefix_key)}

            rows: list[tuple[str, str, ConfigItem | None, str]] = []
            visible_opt_ids: set[str] = set()
            visited: set[int] = set()
            stack = [
                (idx, itm, [i == len(root_items) - 1])
                for i, (idx, itm) in reversed(list(enumerate(root_items)))
            ]

            def traverse(node_idx: int, node_item: ConfigItem, is_last_sibling_list: list[bool]) -> None:
                nonlocal current_group, first_item_id
                if node_idx in visited:
                    return
                visited.add(node_idx)

                if node_item.group and node_item.group != current_group:
                    current_group = node_item.group
                    rows.append(("header", f"header_{tab_idx}_{node_idx}", None, current_group))

                opt_id = f"item_{tab_idx}_{node_idx}"
                if first_item_id is None:
                    first_item_id = opt_id
                visible_opt_ids.add(opt_id)

                prefix = ""
                depth = len(is_last_sibling_list) - 1
                if depth > 0:
                    prefix = "  "
                    for is_last in is_last_sibling_list[1:-1]:
                        prefix += "  " if is_last else "│ "
                    prefix += "└─" if is_last_sibling_list[-1] else "├─"

                self._indent_cache[opt_id] = prefix
                rows.append(("item", opt_id, node_item, prefix))

                if (node_item.is_parent or node_item.type_ == "menu") and node_item.expanded:
                    children = children_map.get(node_idx, [])
                    for i, (child_idx, child_item) in reversed(list(enumerate(children))):
                        stack.append((child_idx, child_item, is_last_sibling_list + [i == len(children) - 1]))

            while stack:
                traverse(*stack.pop())

            old_options = list(ol.options)
            if selected_key is not None:
                visible_ids = {
                    option_keys[opt_id]: opt_id
                    for kind, opt_id, _item, _extra in rows
                    if kind == "item"
                }
                maintain_highlight_id = visible_ids.get(selected_key)
                if maintain_highlight_id is None and selected_key[2]:
                    found_parent = self._tab_item_index.get((tab_idx, str(selected_key[2])))
                    if found_parent is not None:
                        p_item = found_parent[1]
                        maintain_highlight_id = visible_ids.get((p_item.scope, p_item.key, p_item.parent_ref))
                if maintain_highlight_id is None:
                    previous_index = ol.highlighted or 0
                    neighbors = sorted(enumerate(old_options), key=lambda pair: abs(pair[0] - previous_index))
                    maintain_highlight_id = next(
                        (visible_ids[old_keys[option.id]] for _, option in neighbors if old_keys.get(option.id) in visible_ids),
                        first_item_id,
                    )

            maintain_highlight_id = (
                maintain_highlight_id
                if maintain_highlight_id in visible_opt_ids
                else first_item_id
            )

            for kind, opt_id, node_item, extra in rows:
                if kind == "header":
                    header_txt = Text(f" {extra.upper()}", style=f"bold {self.theme_colors['accent']}")
                    options.append(Option(header_txt, id=opt_id, disabled=True))
                else:
                    assert node_item is not None
                    options.append(
                        Option(
                            self._build_option(
                                node_item,
                                is_highlighted=(opt_id == maintain_highlight_id),
                                indent_prefix=extra,
                                tab_idx=tab_idx,
                                opt_id=opt_id,
                            ),
                            id=opt_id,
                        )
                    )

            same_structure = (
                len(old_options) == len(options)
                and all(
                    old.id == new.id and old_keys.get(old.id) == option_keys.get(new.id)
                    for old, new in zip(old_options, options)
                )
            )
            if same_structure:
                for index, (old, new) in enumerate(zip(old_options, options)):
                    if old.prompt != new.prompt:
                        ol.replace_option_prompt_at_index(index, new.prompt)
            else:
                ol._restoring_options = True
                ol._last_hover_idx = None
                if ol.tooltip is not None:
                    ol.tooltip = None
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

    @staticmethod
    def _is_managed_user_preset(item: ConfigItem) -> bool:
        return item.group == "User Presets" and (
            item.key.startswith("__user_preset_")
            or item.key in ("__save_new_preset", "__import_new_preset", "__reset_all_defaults")
        )

    def _reconcile_tab_items(
        self,
        replacements: dict[int, list[ConfigItem]],
        *,
        preserve_metadata: bool = False,
        rebuild_presets: bool = True,
    ) -> set[int]:
        """Unified tab reconciliation preserving pending edits, baselines, undo history, and user presets."""
        remapped: dict[tuple[int, int], tuple[int, int]] = {}
        observed: dict[tuple[int, int], Any] = {}
        replaced_tabs = set(replacements)
        removed_ueks: set[str] = set()
        bumped_any = False

        for tab_idx, incoming in replacements.items():
            existing = self.schema.get(tab_idx, [])
            incoming_items = list(incoming)
            if (
                self.enable_user_presets
                and tab_idx == self.user_presets_tab_idx
                and not any(self._is_managed_user_preset(it) for it in incoming_items)
            ):
                managed_presets = [it for it in existing if self._is_managed_user_preset(it)]
                if managed_presets:
                    incoming_items.extend(managed_presets)

            old_by_exact: defaultdict[tuple[str, str | None], deque[tuple[int, ConfigItem]]] = defaultdict(deque)
            old_by_uek: defaultdict[str, deque[tuple[int, ConfigItem]]] = defaultdict(deque)
            for old_idx, old_item in enumerate(existing):
                uek = self._uid_engine_key(old_item)
                old_by_exact[(uek, old_item.parent_ref)].append((old_idx, old_item))
                old_by_uek[uek].append((old_idx, old_item))
                self._write_generation[uek] = self._write_generation.get(uek, 0) + 1
                bumped_any = True

            consumed_old: set[int] = set()
            matched_old: list[tuple[int, ConfigItem] | None] = [None] * len(incoming_items)

            # Pass 1: exact (uid+engine, parent_ref) match.
            for new_idx, fresh in enumerate(incoming_items):
                exact_queue = old_by_exact.get((self._uid_engine_key(fresh), fresh.parent_ref))
                while exact_queue:
                    old_idx, old_item = exact_queue.popleft()
                    if old_idx not in consumed_old:
                        consumed_old.add(old_idx)
                        matched_old[new_idx] = (old_idx, old_item)
                        break

            # Pass 2: fallback match by uid+engine for reparented items.
            for new_idx, fresh in enumerate(incoming_items):
                if matched_old[new_idx] is not None:
                    continue
                uek_queue = old_by_uek.get(self._uid_engine_key(fresh))
                while uek_queue:
                    old_idx, old_item = uek_queue.popleft()
                    if old_idx not in consumed_old:
                        consumed_old.add(old_idx)
                        matched_old[new_idx] = (old_idx, old_item)
                        break

            merged: list[ConfigItem] = []
            for new_idx, fresh in enumerate(incoming_items):
                found = matched_old[new_idx]
                if found is None:
                    merged.append(fresh)
                    continue
                old_idx, old_item = found
                remapped[(tab_idx, old_idx)] = (tab_idx, new_idx)
                is_pending = (tab_idx, old_idx) in self.pending_commits

                if old_item is fresh:
                    merged.append(old_item)
                elif preserve_metadata:
                    if not is_pending:
                        old_item.value = clone_value(fresh.value)
                        old_item.initial_value = clone_value(fresh.initial_value)
                        old_item._initial_loaded = fresh._initial_loaded
                        old_item.exists_in_target = fresh.exists_in_target
                        observed[(tab_idx, new_idx)] = clone_value(fresh.value)
                    old_item.label = fresh.label
                    old_item.parent_ref = fresh.parent_ref
                    old_item.default = clone_value(fresh.default)
                    old_item.options = list(fresh.options)
                    old_item.hints = list(fresh.hints)
                    old_item.group = fresh.group
                    old_item.extended_help = fresh.extended_help
                    old_item.confirm_message = fresh.confirm_message
                    old_item.warning_msg = fresh.warning_msg
                    old_item.popup_message = fresh.popup_message
                    old_item.read_only = fresh.read_only
                    old_item.expanded = (
                        old_item.expanded
                        if (old_item.is_parent or old_item.type_ == "menu")
                        else fresh.expanded
                    )
                    merged.append(old_item)
                else:
                    keep_value = (not self.hide_missing_items) or is_pending
                    if old_item._initial_loaded and keep_value and not fresh.read_only:
                        fresh.value = clone_value(old_item.value)
                        fresh.initial_value = clone_value(old_item.initial_value)
                        fresh.exists_in_target = old_item.exists_in_target
                        fresh._initial_loaded = True
                    fresh.expanded = old_item.expanded
                    merged.append(fresh)

            for old_idx, dead_item in enumerate(existing):
                if old_idx not in consumed_old:
                    removed_ueks.add(self._uid_engine_key(dead_item))
            self.schema[tab_idx] = merged

        def remap(ref: tuple[int, int]) -> tuple[int, int] | None:
            return remapped.get(ref) if ref[0] in replaced_tabs else ref

        self._committed = {
            new_ref: value for ref, value in self._committed.items()
            if (new_ref := remap(ref)) is not None
        }
        self._committed.update(observed)
        for tab_idx in replaced_tabs:
            for idx, item in enumerate(self.schema[tab_idx]):
                self._committed.setdefault((tab_idx, idx), clone_value(item.value))

        self.pending_commits = {
            new_ref for ref in self.pending_commits
            if (new_ref := remap(ref)) is not None
            and not self.schema[new_ref[0]][new_ref[1]].read_only
        }
        for history in (self.undo_stack, self.redo_stack):
            transactions = [
                [(*new_ref, old, new) for ti, ii, old, new in tx if (new_ref := remap((ti, ii))) is not None]
                for tx in history
            ]
            history.clear()
            history.extend(tx for tx in transactions if tx)

        self._rebuild_indexes(rebuild_presets=rebuild_presets)
        live_ueks = {
            self._uid_engine_key(item)
            for items in self.schema.values()
            for item in items
        }
        for stale in removed_ueks - live_ueks:
            self._write_generation.pop(stale, None)
        if bumped_any and self.custom_views and self.is_mounted:
            for view in self.query(CustomRichTabWidget):
                view.invalidate_content()
        self._option_cache.clear()
        return replaced_tabs

    def _apply_deferred_tabs(
        self,
        tab_indices: list[int],
        states: dict,
        new_items: dict[int, list[ConfigItem]] | None = None,
    ) -> None:
        if new_items and not self._is_ui_quiescent():
            # Save and modal callbacks hold positional row references. Defer
            # replacing schema lists until active saves/timers/modals finish.
            timer_box: list[Timer] = []

            def _retry() -> None:
                if timer_box:
                    self._deferred_apply_timers.discard(timer_box[0])
                self._apply_deferred_tabs(tab_indices, states, new_items)

            t = self.set_timer(0.05, _retry)
            timer_box.append(t)
            self._deferred_apply_timers.add(t)
            return

        if new_items:
            self._reconcile_tab_items(
                {t: new_items[t] for t in tab_indices if t in new_items},
                preserve_metadata=False,
                rebuild_presets=False,
            )

        current_idx = self._current_tab_index()
        for tab_idx in tab_indices:
            for idx, item in enumerate(self.schema.get(tab_idx, [])):
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
                item.initial_value = clone_value(item.value)
                item._initial_loaded = True
                self._committed[(tab_idx, idx)] = clone_value(item.value)

            self._tab_data_ready.add(tab_idx)
            if tab_idx == current_idx:
                self._populate_option_list(tab_idx)
                self._tab_populated.add(tab_idx)
                self._tab_dirty.discard(tab_idx)
            else:
                self._tab_dirty.add(tab_idx)

        global_cfg = [
            c for c in self._configurable_items
            if self._get_item_engine_info(c[2]) == self.default_engine_key
        ]
        self._preset_matrix.rebuild(global_cfg + self._preset_items)
        if current_idx in tab_indices and (ol := self.current_option_list):
            self._update_pagination(ol)
        self._queue_ready_tabs_for_warmup()

    def _replace_dynamic_tabs(self, replacements: dict[int, list[ConfigItem]]) -> bool:
        if not self._is_ui_quiescent():
            return False
        replaced = self._reconcile_tab_items(replacements, preserve_metadata=True)
        current_idx = self._current_tab_index()
        for tab_idx in replaced:
            if tab_idx == current_idx and tab_idx in self._tab_populated:
                self._populate_option_list(tab_idx)
            else:
                self._tab_dirty.add(tab_idx)
        return True

    def _refresh_single_ui(self, tab_idx: int, item_idx: int, item: ConfigItem) -> None:
        if tab_idx not in self._tab_populated or tab_idx != self._current_tab_index():
            self._tab_dirty.add(tab_idx)
            return

        try:
            ol = self.query_one(f"#list-{tab_idx}", ConfigOptionList)
            opt_id = f"item_{tab_idx}_{item_idx}"
            idx = ol.get_option_index(opt_id)
            is_hl = (ol.current_option_id == opt_id)
            prefix = self._indent_cache.get(opt_id, "")

            ol.replace_option_prompt_at_index(
                idx,
                self._build_option(item, is_hl, prefix, tab_idx=tab_idx, opt_id=opt_id),
            )

            p_ref = item.parent_ref
            visited_parents: set[str] = set()
            while p_ref and p_ref not in visited_parents:
                visited_parents.add(p_ref)
                found = self._tab_item_index.get((tab_idx, str(p_ref)))
                if found is None:
                    break
                p_i_idx, parent_found = found
                p_opt_id = f"item_{tab_idx}_{p_i_idx}"
                try:
                    p_idx = ol.get_option_index(p_opt_id)
                    p_is_hl = (ol.current_option_id == p_opt_id)
                    p_prefix = self._indent_cache.get(p_opt_id, "")
                    ol.replace_option_prompt_at_index(
                        p_idx,
                        self._build_option(
                            parent_found, p_is_hl, p_prefix, tab_idx=tab_idx, opt_id=p_opt_id
                        ),
                    )
                except OptionDoesNotExist:
                    pass
                p_ref = parent_found.parent_ref

        except OptionDoesNotExist:
            self._tab_dirty.add(tab_idx)
            if self._current_tab_index() == tab_idx:
                self._populate_option_list(tab_idx)
        except Exception:
            pass

    def _refresh_all_ui(self) -> None:
        current_tab = self._current_tab_index()
        for tab_idx in self.schema:
            if tab_idx == current_tab and tab_idx in self._tab_populated:
                self._populate_option_list(tab_idx)
            else:
                self._tab_dirty.add(tab_idx)
        self._queue_ready_tabs_for_warmup()

    # =========================================================================
    # SAVE MODE / FOOTER / TAB OVERFLOW
    # =========================================================================
    def watch_auto_save(self, old: bool, new: bool) -> None:
        if not self.is_mounted:
            return

        self._update_footer_legend()
        try:
            self.query_one("#shortcut-ctrl-s").display = not new
            self.query_one("#shortcut-R").display = True
        except Exception:
            pass

        if not new:
            self._promote_autosave_timers_to_pending()
            self._update_footer_legend()
            return

        if new and self.pending_commits:
            def on_toggle_save(success: bool) -> None:
                if not success and self.pending_commits:
                    self.notify_status("Pending commits failed. Reverting to BATCH mode.", level="warning")
                    self.auto_save = False

            self.action_save_batch(on_complete=on_toggle_save)

    def _update_footer_legend(self) -> None:
        if not self.is_mounted:
            return
        try:
            self.query_one("#footer-legend", ModeButton).update_mode()
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
            return sum(
                tab.region.width if tab.region.width > 0 else len(str(tab.label)) + 2
                for tab in tab_list
            )
        except Exception:
            return 0

    def _schedule_tab_overflow_check(self) -> None:
        if not self._overflow_scheduled:
            self._overflow_scheduled = True
            self.call_after_refresh(self.check_tab_overflow)

    def check_tab_overflow(self) -> None:
        self._overflow_scheduled = False
        if not self._cached_tabs_container or not self._cached_tab_left or not self._cached_tab_right:
            return

        try:
            container = self._cached_tabs_container
            bar = self._cached_tab_bar
            left = self._cached_tab_left
            right = self._cached_tab_right

            tabs_w = self._get_tabs_total_width()
            bar_w = bar.size.width if bar else 0
            cont_w = container.size.width

            has_overflow = (tabs_w > cont_w) if (bar_w > 0 and tabs_w > 0) else (container.max_scroll_x > 0)

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

            if tab_offset_x < current_scroll:
                container.scroll_to(x=max(0.0, float(tab_offset_x - 1)), animate=False)
            elif tab_offset_x + tab_width > current_scroll + container_width:
                target_x = min(float(container.max_scroll_x), float(tab_offset_x + tab_width - container_width + 1))
                container.scroll_to(x=target_x, animate=False)

            self.check_tab_overflow()
        except Exception:
            pass

    def on_resize(self, event: events.Resize) -> None:
        self._schedule_tab_overflow_check()
        try:
            tabs = self.query_one(Tabs)
            if tabs.active_tab:
                self.scroll_tab_into_view(tabs.active_tab)
        except Exception:
            pass

    # =========================================================================
    # CONSOLIDATED EXTERNAL WATCHER & THEMING
    # =========================================================================
    @staticmethod
    def _target_fingerprint(path: Path) -> tuple[int, int, int] | None | _TARGET_UNREADABLE:
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            LOGGER.warning("Unable to stat target file %s: %s", path, exc)
            return _TARGET_UNREADABLE
        return (stat.st_mtime_ns, stat.st_size, stat.st_ino)

    def _watchable_target_paths(self) -> dict[tuple[str, str], Path]:
        """Collect target paths from initialized engines without triggering lazy imports."""
        target_paths: dict[tuple[str, str], Path] = {}
        initialized_items = getattr(self.engine_pool, "initialized_items", None)
        entries = initialized_items() if callable(initialized_items) else self.engine_pool.items()
        for e_key, eng in entries:
            if e_key in self._failed_engines:
                continue
            if eng.target_path:
                target_paths[e_key] = Path(eng.target_path).expanduser().resolve()
        return target_paths

    async def _apply_theme_watch_result(
        self,
        theme_exists: bool,
        theme_fp: tuple[int, int, int] | None,
    ) -> None:
        if not theme_exists or theme_fp == self._last_theme_fingerprint or not self.theme_path:
            return
        new_theme = await asyncio.to_thread(load_matugen_json, self.theme_path)
        if new_theme is not None:
            self._last_theme_fingerprint = theme_fp
            self.theme_colors.update(new_theme)
            self.apply_theme_to_engine()
            self._refresh_all_ui()
            if self.is_mounted:
                for shortcut in self.query(Shortcut):
                    shortcut.refresh()
                for file_link in self.query(FileLink):
                    file_link.refresh()
                self._update_footer_legend()
                self._update_caps_indicator()

    async def _apply_target_watch_results(
        self,
        target_paths: dict[tuple[str, str], Path],
        target_fps: dict[tuple[str, str], Any],
        *,
        skip_if_locked: bool = True,
    ) -> None:
        changed_any = False
        for e_key, fp in target_fps.items():
            if fp is _TARGET_UNREADABLE:
                continue
            if e_key not in self.last_target_mtimes:
                self.last_target_mtimes[e_key] = fp
                continue
            prev = self.last_target_mtimes[e_key]
            if fp == prev:
                continue

            if fp is None:
                if prev is None:
                    continue
                changed, accepted = self._reconcile_engine_state(
                    e_key, None, require_initial_loaded=False
                )
                changed_any |= changed
                if accepted:
                    self.last_target_mtimes[e_key] = None
                continue

            engine = self.engine_pool[e_key]
            lock = self._lock_for_engine(e_key)
            if skip_if_locked and lock.locked():
                continue
            gens = {
                self._uid_engine_key(item): self._write_generation.get(self._uid_engine_key(item), 0)
                for _, _, item in self._items_by_engine.get(e_key, ())
                if item.type_ not in ("action", "preset", "menu")
            }
            try:
                async with lock:
                    live_fp = await self._run_save_io(self._target_fingerprint, target_paths[e_key])
                    if live_fp is _TARGET_UNREADABLE or live_fp is None:
                        continue
                    new_state = await self._run_save_io(engine.load_state)
            except Exception as exc:
                LOGGER.warning("Unable to reload %s: %s", target_paths[e_key], exc)
                continue

            changed, accepted = self._reconcile_engine_state(
                e_key, new_state, expected_generations=gens, require_initial_loaded=False
            )
            changed_any |= changed
            if accepted:
                self.last_target_mtimes[e_key] = live_fp

        if changed_any:
            self._refresh_all_ui()
            self.notify_status("Config modified externally. Refreshed UI.")

    async def _apply_presets_watch_result(
        self,
        preset_fps: dict[str, tuple[int, int, int]] | None,
    ) -> None:
        if preset_fps is None:
            return
        if (not self._initial_presets_mtime_set) or (preset_fps != self._preset_mtimes):
            preset_records, actual_mtimes = await asyncio.to_thread(
                self._read_user_presets_with_mtimes
            )
            if self._is_ui_quiescent():
                self._apply_user_presets(preset_records)
                self._rebuild_indexes(rebuild_presets=True)
                self._preset_mtimes = actual_mtimes
                self._initial_presets_mtime_set = True
                self._refresh_all_ui()

    async def watch_theme_file(self) -> None:
        if not self.theme_path:
            return
        try:
            def _stat_theme() -> tuple[bool, tuple[int, int, int] | None]:
                try:
                    st = self.theme_path.stat()
                    return True, (st.st_mtime_ns, st.st_size, st.st_ino)
                except OSError:
                    return False, None

            theme_exists, theme_fp = await asyncio.to_thread(_stat_theme)
            await self._apply_theme_watch_result(theme_exists, theme_fp)
        except Exception:
            LOGGER.exception("Unexpected error while watching theme file")

    async def watch_target_file(self) -> None:
        if not self._boot_complete:
            return
        try:
            target_paths = self._watchable_target_paths()
            target_fps = await asyncio.to_thread(
                lambda: {
                    ekey: self._target_fingerprint(path)
                    for ekey, path in target_paths.items()
                }
            )
            await self._apply_target_watch_results(
                target_paths, target_fps, skip_if_locked=False
            )
        except Exception:
            LOGGER.exception("Unexpected error while watching target files")

    async def watch_presets_dir(self) -> None:
        if (
            not self._boot_complete
            or not self.enable_user_presets
            or self._modal_active()
        ):
            return
        try:
            preset_fps = await asyncio.to_thread(self._scan_preset_mtimes)
            await self._apply_presets_watch_result(preset_fps)
        except Exception:
            LOGGER.exception("Unexpected error while watching preset files")

    async def watch_external_changes(self) -> None:
        """Single 1.0s batched watcher covering theme file, target configs, and user presets."""
        if self._external_watch_inflight:
            return
        self._external_watch_inflight = True
        try:
            boot_done = self._boot_complete
            check_presets = boot_done and self.enable_user_presets and not self._modal_active()
            target_paths = self._watchable_target_paths() if boot_done else {}

            def _batch_stat():
                theme_fp = None
                theme_exists = False
                if self.theme_path:
                    try:
                        st = self.theme_path.stat()
                        theme_fp = (st.st_mtime_ns, st.st_size, st.st_ino)
                        theme_exists = True
                    except OSError:
                        pass

                target_fps = {
                    ekey: self._target_fingerprint(path)
                    for ekey, path in target_paths.items()
                }
                preset_fps = self._scan_preset_mtimes() if check_presets else None
                return theme_exists, theme_fp, target_fps, preset_fps

            theme_exists, theme_fp, target_fps, preset_fps = await asyncio.to_thread(_batch_stat)

            await self._apply_theme_watch_result(theme_exists, theme_fp)
            if boot_done:
                await self._apply_target_watch_results(
                    target_paths, target_fps, skip_if_locked=True
                )
            if check_presets:
                await self._apply_presets_watch_result(preset_fps)
        except Exception:
            LOGGER.exception("Unexpected error during external file watch")
        finally:
            self._external_watch_inflight = False

    async def update_telemetry(self) -> None:
        if self.telemetry_engine:
            try:
                msg = await self._run_save_io(self.telemetry_engine.get_telemetry)
                self.query_one("#telemetry-banner", Label).update(msg)
            except Exception:
                LOGGER.exception("Telemetry update failed")

    def apply_theme_to_engine(self) -> None:
        self._theme_toggle = not getattr(self, "_theme_toggle", False)
        theme_name = "dusky_matugen_A" if self._theme_toggle else "dusky_matugen_B"

        bg = self.theme_colors.get("background", self.theme_colors["bg"])
        fg = self.theme_colors.get("on_background", self.theme_colors["fg"])
        accent = self.theme_colors.get("primary", self.theme_colors["accent"])
        muted = self.theme_colors.get("surface_variant", self.theme_colors["muted"])
        err = self.theme_colors["error"]
        warn = self.theme_colors.get("tertiary", self.theme_colors["warning"])
        succ = self.theme_colors.get("secondary", self.theme_colors["success"])
        info = self.theme_colors.get("info", accent)

        self.theme_colors.update({
            "bg": bg, "fg": fg, "accent": accent, "muted": muted,
            "error": err, "warning": warn, "success": succ, "info": info,
        })

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
        self._invalidate_theme_cache()

    def _update_file_link(self, item: ConfigItem | None = None) -> None:
        try:
            if item is None:
                cur_tab = self._current_tab_index()
                if cur_tab is not None and (ol := self.current_option_list):
                    if opt_id := ol.current_option_id:
                        if parsed := self._get_item_from_id(opt_id):
                            item = parsed[2]
                    if item is None and (items := self.schema.get(cur_tab, [])):
                        item = items[0]

            engine_key = self._get_item_engine_info(item) if item is not None else self.default_engine_key
            engine = self.engine_pool[engine_key] if engine_key in self._loaded_engines else None
            path = engine.target_path if engine is not None and getattr(engine, "editable_target", True) else ""
            link = self.query_one("#file-link", FileLink)
            link.path = path
            link.display = bool(path)
            self.query_one("#footer-file-target").display = bool(path)
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

                if idx not in self._tab_populated or idx in self._tab_dirty:
                    self._populate_option_list(idx)
                    self._tab_populated.add(idx)
                    self._tab_dirty.discard(idx)

                if ol := self.current_option_list:
                    ol.focus()
                    if ol.highlighted is None and ol.option_count > 0:
                        for i in range(ol.option_count):
                            if not getattr(ol.get_option_at_index(i), "disabled", False):
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
            self._cached_tabs_container.scroll_to(
                x=max(0.0, float(self._cached_tabs_container.scroll_x - 20)),
                animate=False,
            )
            self._schedule_tab_overflow_check()

    @on(events.Click, "#tab-right")
    def scroll_tabs_right(self, event: events.Click) -> None:
        event.stop()
        if (
            self._cached_tabs_container
            and self._cached_tabs_container.scroll_x < self._cached_tabs_container.max_scroll_x
        ):
            target = min(
                float(self._cached_tabs_container.max_scroll_x),
                float(self._cached_tabs_container.scroll_x + 20),
            )
            self._cached_tabs_container.scroll_to(x=target, animate=False)
            self._schedule_tab_overflow_check()

    # =========================================================================
    # SHORTCUT VISUALS / HELP / HIGHLIGHT
    # =========================================================================
    def trigger_shortcut_blink(self, key_id: str) -> None:
        try:
            self.query_one(f"#shortcut-{key_id}", Shortcut).blink()
        except Exception:
            pass

    def toggle_shortcut_active(self, key_id: str, active: bool) -> None:
        try:
            sc = self.query_one(f"#shortcut-{key_id}", Shortcut)
            sc.set_class(active, "-active")
            sc.refresh()
        except Exception:
            pass

    def _get_item_from_id(self, opt_id: str | None) -> tuple[int, int, ConfigItem] | None:
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
            if not self.query_one("#content-area").has_class("-show-help"):
                return
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
            if ol and (opt_id := ol.current_option_id):
                if parsed := self._get_item_from_id(opt_id):
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
                ol is not self.current_option_list
                or ol.highlighted != event.option_index
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
            if old_parsed := self._get_item_from_id(last_id):
                try:
                    old_idx = ol.get_option_index(last_id)
                    old_prefix = self._indent_cache.get(last_id, "")
                    ol.replace_option_prompt_at_index(
                        old_idx,
                        self._build_option(
                            old_parsed[2], False, old_prefix, tab_idx=old_parsed[0], opt_id=last_id
                        ),
                    )
                except OptionDoesNotExist:
                    pass

        if parsed and not restored:
            try:
                curr_idx = ol.get_option_index(event.option_id)
                curr_prefix = self._indent_cache.get(event.option_id, "")
                ol.replace_option_prompt_at_index(
                    curr_idx,
                    self._build_option(
                        parsed[2], True, curr_prefix, tab_idx=parsed[0], opt_id=event.option_id
                    ),
                )
                ol.last_highlighted_id = event.option_id
                ol.scroll_to_highlight()
                self._ensure_header_visible(ol, curr_idx)
            except OptionDoesNotExist:
                pass
        elif parsed:
            ol.last_highlighted_id = event.option_id

        self._update_pagination(ol)

    def _ensure_header_visible(self, ol: ConfigOptionList, curr_idx: int | None) -> None:
        if ol is None or curr_idx is None or ol.option_count == 0:
            return
        if curr_idx > 0 and int(ol.scroll_y) == curr_idx:
            try:
                if getattr(ol.get_option_at_index(curr_idx - 1), "disabled", False):
                    ol.scroll_y = curr_idx - 1
            except Exception:
                pass

    def _update_pagination(self, ol: ConfigOptionList | None) -> None:
        try:
            counter = self.query_one("#pos-counter", Label)
            if ol and ol.option_count > 0:
                curr_idx = ol.highlighted if ol.highlighted is not None else 0
                positions = getattr(ol, "_selectable_positions", ())
                total_selectable = positions[-1] if positions else 0
                selectable_idx = positions[curr_idx] if positions and curr_idx < len(positions) else 0
                if total_selectable > 0 and selectable_idx > 0:
                    txt = Text()
                    txt.append("│ ", style=self.theme_colors.get("fg", ""))
                    txt.append(f"{selectable_idx}/{total_selectable}", style=self.theme_colors.get("accent", "") + " bold")
                    counter.update(txt)
                    counter.display = True
                    return
            counter.display = False
        except Exception:
            pass

    def _update_scroll_indicators(self) -> None:
        try:
            tab_idx = self._current_tab_index()
            if tab_idx is None:
                return
            ol = self.query_one(f"#list-{tab_idx}", ConfigOptionList)
            indicator = self.query_one(f"#indicator-{tab_idx}", ScrollIndicator)
            if ol.max_scroll_y > 0 and ol.size.height > 2:
                indicator.update_scroll(ol.scroll_y, ol.max_scroll_y, ol.size.height, ol.virtual_size.height)
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

        sound_path = _discover_notification_sound()
        if not sound_path:
            return

        if _AUDIO_PLAYER_CACHE is None:
            _AUDIO_PLAYER_CACHE = (
                shutil.which("pw-play")
                or shutil.which("paplay")
                or shutil.which("mpv")
                or ""
            )

        player = _AUDIO_PLAYER_CACHE
        if not player:
            return

        cmd = [player, sound_path]
        if player.endswith("mpv"):
            cmd.extend(["--no-video", "--really-quiet"])

        async def play() -> None:
            proc = None
            spawn = asyncio.create_task(
                asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            )
            try:
                proc = await asyncio.shield(spawn)
                self._action_procs.add(proc)
                await proc.wait()
            except OSError as error:
                LOGGER.debug("Unable to play notification sound: %s", error)
            finally:
                self._track_action_cleanup(
                    asyncio.create_task(self._cleanup_action_resources(proc, [], spawn))
                )

        self._track_action_task(asyncio.create_task(play()))

    # =========================================================================
    # WRITE GENERATION / TRANSACTIONS
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
        if timer := self._save_timers.pop(k, None):
            timer.stop()
        self._pending_autosave_args.pop(k, None)

    def _cancel_autosave_for_transaction(self, transaction: list[tuple[int, int, Any, Any]]) -> None:
        uid_engines: set[str] = set()
        seen: set[tuple[int, int]] = set()
        for t, i, _o, _n in transaction:
            self._cancel_autosave_ref(t, i)
            src_item = self._get_schema_item(t, i)
            if not src_item:
                continue
            src_eng = self._get_item_engine_info(src_item)
            uid_engines.add(self._uid_engine_key(src_item))
            for dup_t, dup_i, dup_itm in self._items_by_uid.get(src_item.uid, ()):
                if (dup_t, dup_i) in seen or self._get_item_engine_info(dup_itm) != src_eng:
                    continue
                seen.add((dup_t, dup_i))
                self._cancel_autosave_ref(dup_t, dup_i)

        for uek in uid_engines:
            self._bump_write_generation(uek)
        self._notify_quiescent()

    def _apply_transaction_to_ram(self, transaction: list[tuple[int, int, Any, Any]], undo: bool = False) -> None:
        uid_engines: set[str] = set()
        for t, i, o, n in transaction:
            item = self._get_schema_item(t, i)
            if not item:
                continue
            item.value = clone_value(o if undo else n)
            item.exists_in_target = True
            self._on_item_value_changed(item)
            self._refresh_single_ui(t, i, item)
            uid_engines.add(self._uid_engine_key(item))

        for uek in uid_engines:
            self._bump_write_generation(uek)

    def _revert_transaction(self, transaction: list[tuple[int, int, Any, Any]]) -> None:
        self._apply_transaction_to_ram(transaction, undo=True)
        for tab_idx, item_idx, _, _ in transaction:
            self._sync_pending(tab_idx, item_idx, self.schema[tab_idx][item_idx])

        for idx in range(len(self.undo_stack) - 1, -1, -1):
            if self.undo_stack[idx] is transaction:
                del self.undo_stack[idx]
                break

        self._refresh_presets_ui()

    def _reset_trigger_ui(self, item: ConfigItem) -> None:
        src_eng = self._get_item_engine_info(item)
        for t_idx, i_idx, other_item in self._items_by_uid.get(item.uid, ()):
            if self._get_item_engine_info(other_item) != src_eng:
                continue
            other_item.value = clone_value(item.default)
            self._committed[(t_idx, i_idx)] = clone_value(item.default)
            self.pending_commits.discard((t_idx, i_idx))
            self._on_item_value_changed(other_item)
            self._refresh_single_ui(t_idx, i_idx, other_item)

        self._bump_write_generation(self._uid_engine_key(item))
        self._refresh_presets_ui()

    def _apply_observed_systemd_state(self, item: ConfigItem, actual: str, expected: str) -> None:
        engine_key = self._get_item_engine_info(item)
        for tab_idx, item_idx, other in self._items_by_uid.get(item.uid, ()):
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

    def _apply_transaction(
        self,
        transaction: list[tuple[int, int, Any, Any]],
        action_type: str = "new",
        success_msg: str = "",
        immediate_commit: bool = False,
    ) -> None:
        if not self.require_boot_complete():
            return

        expanded = {(t, i): (t, i, clone_value(old), clone_value(new)) for t, i, old, new in transaction}
        for t, i, old, new in transaction:
            item = self.schema[t][i]
            engine_key = self._get_item_engine_info(item)
            for dt, di, duplicate in self._items_by_uid.get(item.uid, ()):
                if self._get_item_engine_info(duplicate) == engine_key and (dt, di) not in expanded:
                    if action_type == "undo":
                        expanded[(dt, di)] = (dt, di, clone_value(old), clone_value(duplicate.value))
                    else:
                        expanded[(dt, di)] = (dt, di, clone_value(duplicate.value), clone_value(new))
        transaction = list(expanded.values())
        tx_keys = {(t, i) for t, i, _, _ in transaction}
        self._cancel_autosave_for_transaction(transaction)

        for t, i, o, n in transaction:
            item = self.schema[t][i]
            item.value = clone_value(o if action_type == "undo" else n)
            item.exists_in_target = True
            self._on_item_value_changed(item)
            self._sync_pending(t, i, item)
            self._refresh_single_ui(t, i, item)

        uid_engines: set[str] = set()
        for t, i, _o, _n in transaction:
            if item := self._get_schema_item(t, i):
                uid_engines.add(self._uid_engine_key(item))

        for uek in uid_engines:
            self._bump_write_generation(uek)

        transaction_generations = {uek: self._write_generation.get(uek, 0) for uek in uid_engines}

        if self.auto_save or immediate_commit:
            def finalize_transaction(batch_success: bool) -> None:
                successful_parts = []
                failed_parts = []

                for t, i, o, n in transaction:
                    if (t, i) in self.pending_commits:
                        failed_parts.append((t, i, o, n))
                        item = self.schema[t][i]
                        expected = o if action_type == "undo" else n
                        uek = self._uid_engine_key(item)
                        if (
                            self._write_generation.get(uek, 0) == transaction_generations.get(uek, -1)
                            and item.values_equal(item.value, expected)
                        ):
                            item.value = clone_value(n if action_type == "undo" else o)
                            self._on_item_value_changed(item)
                            self._refresh_single_ui(t, i, item)
                            self._sync_pending(t, i, item)
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

                if self._preset_refresh_timer is not None:
                    self._preset_refresh_timer.stop()
                    self._preset_refresh_timer = None

                self._update_footer_legend()
                self._refresh_presets_ui()

            scoped_keys = None if self.auto_save else tx_keys
            self.action_save_batch(on_complete=finalize_transaction, target_keys=scoped_keys)
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
            if self._preset_refresh_timer is not None:
                self._preset_refresh_timer.stop()
                self._preset_refresh_timer = None
            self._refresh_presets_ui()

    def _is_unchanged_submission(self, item: ConfigItem, new_val: Any) -> bool:
        if item.type_ in ("action", "preset", "menu") or is_trigger_item(item) or not item.exists_in_target:
            return False
        if not item.values_equal(item.value, new_val):
            return False
        src_eng = self._get_item_engine_info(item)
        for _, _, other in self._items_by_uid.get(item.uid, ()):
            if other is item or self._get_item_engine_info(other) != src_eng:
                continue
            if not other.values_equal(other.value, new_val):
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
        record_undo: bool = True,
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
                    level="warning",
                ),
                on_confirm,
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
        record_undo: bool = True,
    ) -> bool:
        if item.read_only:
            return False
        old_val = clone_value(item.value)
        src_eng = self._get_item_engine_info(item)
        transaction = [(tab_idx, item_idx, old_val, clone_value(new_val))]

        for t_idx, i_idx, other in self._items_by_uid.get(item.uid, ()):
            if other is item or self._get_item_engine_info(other) != src_eng:
                continue
            transaction.append((t_idx, i_idx, clone_value(other.value), clone_value(new_val)))

        if not is_undo and record_undo:
            self.undo_stack.append(transaction)
            self.redo_stack.clear()

        self._cancel_autosave_for_transaction(transaction)

        item.value = clone_value(new_val)
        item.exists_in_target = True
        self._on_item_value_changed(item)

        for t_idx, i_idx, other_item in self._items_by_uid.get(item.uid, ()):
            if other_item is item or self._get_item_engine_info(other_item) != src_eng:
                continue
            other_item.value = clone_value(new_val)
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
                    self._start_save_task(self._do_auto_save_async(ti, ii, it, vs, ov, g, tx, False)),
            )
            self._pending_autosave_args[k] = (item, val_str, old_val)
        else:
            for ti, ii, _, _ in transaction:
                self._sync_pending(ti, ii, self.schema[ti][ii])

        if not batch_mode:
            self._update_footer_legend()

        self._refresh_single_ui(tab_idx, item_idx, item)

        if not batch_mode:
            if self._preset_refresh_timer is not None:
                self._preset_refresh_timer.stop()
            self._preset_refresh_timer = self.set_timer(0.15, self._refresh_presets_ui)

        if item.popup_message and not is_undo and not batch_mode:
            self.push_screen(
                AlertDialog(
                    item.popup_message,
                    title=f"Notice: {item.label}",
                    level="info",
                )
            )
        return True

    # =========================================================================
    # ASYNC AUTO SAVE & BATCH SAVE
    # =========================================================================
    def _start_save_task(self, coroutine: Any) -> asyncio.Task[Any] | None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            if hasattr(coroutine, "close"):
                coroutine.close()
            return None
        task = loop.create_task(coroutine)
        self._save_tasks.add(task)
        task.add_done_callback(self._on_save_task_done)
        return task

    def _on_save_task_done(self, task: asyncio.Task[Any]) -> None:
        self._save_tasks.discard(task)
        self._save_task_keys.pop(task, None)
        if task.cancelled():
            self._save_failure_pending = True
        else:
            try:
                if task.exception() is not None:
                    self._save_failure_pending = True
            except Exception:
                self._save_failure_pending = True
            initialized_items = getattr(self.engine_pool, "initialized_items", None)
            engines = initialized_items() if callable(initialized_items) else self.engine_pool.items()
            states = {
                key: engine.cache for key, engine in engines
                if getattr(engine, "refresh_after_write", False)
            }
            if states:
                self._apply_refreshed_states(states)
        self._notify_quiescent()
        self._maybe_finish_quit()

    async def _run_save_io(self, func: Any, /, *args: Any, **kwargs: Any) -> Any:
        worker = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))
        workers = getattr(self, "_io_workers", None)
        if workers is not None:
            workers.add(worker)
            worker.add_done_callback(workers.discard)
        try:
            await asyncio.wait({worker})
            return worker.result()
        except asyncio.CancelledError:
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
            and not self._preset_io_tasks
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
        force: bool = False,
    ) -> None:
        uek = self._uid_engine_key(item)
        if self._write_generation.get(uek) != generation:
            return
        self._save_timers.pop((tab_idx, item_idx), None)
        self._pending_autosave_args.pop((tab_idx, item_idx), None)
        if current_task := asyncio.current_task():
            self._save_task_keys.setdefault(current_task, set()).add(uek)

        if force:
            if (
                self._write_generation.get(uek) != generation
                or not item.values_equal(item.value, old_val)
            ):
                return
            self._apply_transaction_to_ram(transaction, undo=False)
            if not is_trigger_item(item) and not any(tx is transaction for tx in self.undo_stack):
                self.undo_stack.append(transaction)
                self.redo_stack.clear()
            generation = self._write_generation.get(uek, generation)
        else:
            if self._write_generation.get(uek) != generation or item.serialize(item.value) != val_str:
                return

        try:
            ekey = self._get_item_engine_info(item)
            engine = self._get_engine_for_item(item)
        except Exception as e:
            self._save_failure_pending = True
            self.notify_status(f"Engine Error: {e}", level="error")
            self._revert_transaction(transaction)
            self._maybe_finish_quit()
            return

        async with self._lock_for_engine(ekey):
            if self._write_generation.get(uek) != generation or item.serialize(item.value) != val_str:
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
                            engine.write_value, item.key, item.scope, val_str, item_type=item.type_
                        )
                except Exception as e:
                    success, msg = False, f"Engine Error: {e}"
            finally:
                self._active_save_count -= 1

            if success and engine.target_path:
                try:
                    fp = await self._run_save_io(
                        self._target_fingerprint, Path(engine.target_path).expanduser().resolve()
                    )
                    if isinstance(fp, tuple):
                        self.last_target_mtimes[ekey] = fp
                except (OSError, TypeError):
                    pass

        if success:
            for ti, ii, _, new_value in transaction:
                self._committed[(ti, ii)] = clone_value(new_value)
                other = self.schema[ti][ii]
                if other.values_equal(other.value, new_value):
                    self.pending_commits.discard((ti, ii))
                else:
                    self._sync_pending(ti, ii, other)
            if self._write_generation.get(uek) != generation:
                return
            self._bump_write_generation(uek)

            if is_trigger_item(item):
                self._reset_trigger_ui(item)
                self._flash_action_ui(item, duration=0.75)
            message = msg if ekey[0] == "network" and msg else f"Updated {item.label}"
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
            if self._write_generation.get(uek) != generation or item.serialize(item.value) != val_str:
                return
            if self._modal_active() and isinstance(self.screen, PasswordScreen):
                self.notify_status("Another authorization is already in progress.", level="warning")
                self._save_failure_pending = True
                self._revert_transaction(transaction)
                return

            self._revert_transaction(transaction)
            generation = self._write_generation.get(uek, generation)

            def on_pwd(pwd: str | None) -> None:
                self._start_save_task(
                    self._on_auto_password(pwd, tab_idx, item_idx, item, val_str, old_val, generation, transaction)
                )

            self._save_auth_pending += 1
            self.push_screen(PasswordScreen(), on_pwd)
            return

        if self._write_generation.get(uek) != generation or item.serialize(item.value) != val_str:
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
        transaction: list[tuple[int, int, Any, Any]],
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
                self._notify_quiescent()
                self._maybe_finish_quit()
                return
            except Exception as exc:
                self.notify_status(f"Sudo authentication failed: {exc}", level="error")
                self._save_failure_pending = True
                self._notify_quiescent()
                self._maybe_finish_quit()
                return
            finally:
                pwd = None

            if auth_res.returncode == 0:
                self.notify_status("Sudo authenticated. Retrying...", level="info")
                self._start_sudo_keepalive()
                await self._do_auto_save_async(
                    tab_idx, item_idx, item, val_str, old_val, generation, transaction, True
                )
            else:
                self.notify_status("Incorrect sudo password.", level="error")
                self._save_failure_pending = True
                self.play_reset_sound()
        else:
            self.notify_status("Sudo authentication cancelled.", level="warning")
            self._save_failure_pending = True
        self._notify_quiescent()
        self._maybe_finish_quit()

    def _start_sudo_keepalive(self) -> None:
        if self._sudo_keepalive is None:
            self._sudo_keepalive = self.set_interval(60.0, self._sudo_keepalive_tick)

    def _sudo_keepalive_tick(self) -> None:
        if self._sudo_keepalive_task is not None and not self._sudo_keepalive_task.done():
            return
        self._sudo_keepalive_task = asyncio.create_task(self._sudo_keepalive_async())

    async def _sudo_keepalive_async(self) -> None:
        try:
            res = await asyncio.to_thread(subprocess.run, ["sudo", "-n", "-v"], capture_output=True)
            if res.returncode != 0 and self._sudo_keepalive is not None:
                self._sudo_keepalive.stop()
                self._sudo_keepalive = None
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    def action_save_batch(
        self,
        on_complete=None,
        target_keys: set[tuple[int, int]] | None = None,
    ) -> bool:
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

        commits_to_save = (
            (self.pending_commits & target_keys)
            if target_keys is not None
            else set(self.pending_commits)
        )

        if not commits_to_save:
            self.notify_status("No pending changes.", level="info")
            if on_complete:
                on_complete(True)
            self._maybe_finish_quit()
            return True

        self._start_save_task(self._save_batch_async(on_complete, target_keys=target_keys))
        return True

    async def _save_batch_async(
        self,
        on_complete=None,
        target_keys: set[tuple[int, int]] | None = None,
        *,
        _exclude_keys: set[tuple[int, int]] | None = None,
        _prior_success: int = 0,
        _prior_errors: list[str] | None = None,
    ) -> None:
        excluded = _exclude_keys or set()
        commits_preview = (
            tuple(k for k in self.pending_commits if k in target_keys and k not in excluded)
            if target_keys is not None
            else tuple(k for k in self.pending_commits if k not in excluded)
        )
        if not commits_preview:
            if _prior_errors:
                self._save_failure_pending = True
                if _prior_success > 0:
                    self.notify_status(
                        f"Partial success ({_prior_success} applied). Error: {_prior_errors[0]}",
                        level="warning",
                    )
                    self.play_reset_sound()
                else:
                    self.notify_status(f"Batch Error: {_prior_errors[0]}", level="error")
                if on_complete:
                    on_complete(False)
                return
            self.notify_status("No pending changes.", level="info")
            if on_complete:
                on_complete(True)
            return

        engine_keys = {
            self._get_item_engine_info(self.schema[ti][ii])
            for ti, ii in commits_preview
        }

        requeue_needed = False
        touched_refs: set[tuple[int, int]] = set()
        failed_keys: set[tuple[int, int]] = set(excluded)
        error_msgs: list[str] = list(_prior_errors or ())
        final_success = not error_msgs
        success_count = _prior_success
        auth_required = False
        current_task = asyncio.current_task()

        async with AsyncExitStack() as stack:
            await self._acquire_engine_locks(stack, engine_keys)

            all_pending = (
                tuple(k for k in self.pending_commits if k in target_keys and k not in excluded)
                if target_keys is not None
                else tuple(k for k in self.pending_commits if k not in excluded)
            )
            commits_snapshot = tuple(
                k for k in all_pending
                if self._get_item_engine_info(self.schema[k[0]][k[1]]) in engine_keys
            )
            if len(commits_snapshot) != len(all_pending):
                requeue_needed = True
            if not commits_snapshot:
                if not requeue_needed:
                    if error_msgs:
                        self._save_failure_pending = True
                        self.notify_status(f"Batch Error: {error_msgs[0]}", level="error")
                        if on_complete:
                            on_complete(False)
                    else:
                        self.notify_status("No pending changes.", level="info")
                        if on_complete:
                            on_complete(True)
                    return

            touched_refs.update(commits_snapshot)
            type FrozenItem = tuple[tuple[str, str, str, str], tuple[int, int], str, Any, ConfigItem]
            batches: dict[tuple[tuple[str, str], str | None], list[FrozenItem]] = {}

            for tab_idx, item_idx in commits_snapshot:
                item = self.schema[tab_idx][item_idx]
                key = (tab_idx, item_idx)
                frozen_val = clone_value(item.value)
                val_str = item.serialize(frozen_val)
                ekey = self._get_item_engine_info(item)
                uek = self._uid_engine_key(item)
                if current_task is not None:
                    self._save_task_keys.setdefault(current_task, set()).add(uek)
                change = (item.key, item.scope, val_str, str(item.type_))
                trigger_key = uek if is_trigger_item(item) else None
                batches.setdefault((ekey, trigger_key), []).append((change, key, val_str, frozen_val, item))

            def mark_success(key: tuple[int, int], frozen_val_str: str, frozen_val: Any, itm: ConfigItem) -> bool:
                nonlocal requeue_needed
                self._committed[key] = clone_value(frozen_val)
                if itm.serialize(itm.value) != frozen_val_str:
                    requeue_needed = True
                    return False
                self.pending_commits.discard(key)
                return True

            def mark_failure(key: tuple[int, int], frozen_val_str: str, itm: ConfigItem) -> bool:
                nonlocal requeue_needed
                if itm.serialize(itm.value) != frozen_val_str:
                    requeue_needed = True
                    return False
                failed_keys.add(key)
                return True

            for (ekey, trigger_key), batch in batches.items():
                engine = self.engine_pool[ekey]
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

                    reconciled: set[tuple[str, str]] = set()
                    for change, key, frozen_str, frozen_val, itm in batch:
                        identity = (change[0], change[1])
                        result = results.get(identity)
                        if result is None:
                            if mark_failure(key, frozen_str, itm):
                                final_success = False
                                error_msgs.append(f"Missing write result for {identity[1]}.{identity[0]}")
                            continue
                        if result.ok:
                            if mark_success(key, frozen_str, frozen_val, itm):
                                success_count += 1
                                self._bump_write_generation(self._uid_engine_key(itm))
                            continue
                        if result.message == "AUTH_REQUIRED":
                            final_success = False
                            auth_required = True
                            continue
                        if identity not in reconciled and result.actual is not None:
                            self._apply_observed_systemd_state(itm, result.actual, frozen_str)
                            reconciled.add(identity)
                        if result.actual == frozen_str:
                            success_count += 1
                            final_success = False
                            error_msgs.append(result.message)
                        elif mark_failure(key, frozen_str, itm):
                            final_success = False
                            error_msgs.append(result.message)
                    if auth_required:
                        break
                    continue

                self._active_save_count += 1
                try:
                    if trigger_key is not None:
                        key_s, scope, value, kind = changes[0]
                        success, msg, _ = await self._run_save_io(
                            engine.write_value, key_s, scope, value, item_type=kind
                        )
                    else:
                        success, msg, _ = await self._run_save_io(engine.write_batch, changes)
                except Exception as e:
                    success, msg = False, f"Engine Error: {e}"
                finally:
                    self._active_save_count -= 1

                if success:
                    committed_ueks: set[str] = set()
                    completed_triggers: dict[str, ConfigItem] = {}
                    for _change, key, frozen_str, frozen_val, itm in batch:
                        if mark_success(key, frozen_str, frozen_val, itm):
                            success_count += 1
                            uek = self._uid_engine_key(itm)
                            committed_ueks.add(uek)
                            if is_trigger_item(itm):
                                completed_triggers[uek] = itm

                    for trigger in completed_triggers.values():
                        self._reset_trigger_ui(trigger)
                    for uek in committed_ueks:
                        self._bump_write_generation(uek)

                    if engine.target_path:
                        try:
                            fp = await self._run_save_io(
                                self._target_fingerprint, Path(engine.target_path).expanduser().resolve()
                            )
                            if isinstance(fp, tuple):
                                self.last_target_mtimes[ekey] = fp
                        except (OSError, TypeError):
                            pass
                else:
                    if "AUTH_REQUIRED" in msg:
                        auth_required = True
                        break
                    if getattr(engine, "atomic_batches", False):
                        batch_failed = False
                        for _change, key, frozen_str, _frozen_val, itm in batch:
                            if mark_failure(key, frozen_str, itm):
                                batch_failed = True
                        if batch_failed:
                            final_success = False
                            error_msgs.append(msg)
                        continue

                    engine_failed = False
                    committed_ueks = set()
                    completed_triggers_gen: dict[str, tuple[ConfigItem, int]] = {}
                    fallback_results: dict[tuple[str, str], tuple[bool, str]] = {}
                    if trigger_key is not None:
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
                                uek = self._uid_engine_key(itm)
                                committed_ueks.add(uek)
                                if is_trigger_item(itm):
                                    completed_triggers_gen[uek] = (itm, self._write_generation.get(uek, 0))
                            if engine.target_path:
                                try:
                                    fp = await self._run_save_io(
                                        self._target_fingerprint, Path(engine.target_path).expanduser().resolve()
                                    )
                                    if isinstance(fp, tuple):
                                        self.last_target_mtimes[ekey] = fp
                                except (OSError, TypeError):
                                    pass
                        else:
                            if "AUTH_REQUIRED" in item_msg:
                                auth_required = True
                                break
                            if is_trigger_item(itm):
                                engine_failed = True
                                error_msgs.append(item_msg)
                                self.pending_commits.discard(key)
                                self._reset_trigger_ui(itm)
                            elif mark_failure(key, frozen_str, itm):
                                engine_failed = True
                                error_msgs.append(item_msg)

                    for uek, (trigger, gen) in completed_triggers_gen.items():
                        if self._write_generation.get(uek, 0) == gen:
                            self._reset_trigger_ui(trigger)
                    for uek in committed_ueks:
                        self._bump_write_generation(uek)

                    if auth_required:
                        break
                    if engine_failed:
                        final_success = False

        for t_idx, i_idx in touched_refs:
            if item := self._get_schema_item(t_idx, i_idx):
                self._refresh_single_ui(t_idx, i_idx, item)
        self._refresh_presets_ui()
        self._update_footer_legend()

        if requeue_needed and not auth_required:
            remaining = (
                (self.pending_commits & target_keys) - failed_keys
                if target_keys is not None
                else self.pending_commits - failed_keys
            )
            if remaining:
                self._start_save_task(
                    self._save_batch_async(
                        on_complete,
                        target_keys=target_keys,
                        _exclude_keys=failed_keys,
                        _prior_success=success_count,
                        _prior_errors=error_msgs,
                    )
                )
                return

        if auth_required:
            if self._modal_active() and isinstance(self.screen, PasswordScreen):
                self.notify_status("Another authorization is already in progress.", level="warning")
                self._save_failure_pending = True
                if on_complete:
                    on_complete(False)
                return

            def on_pwd_batch(pwd: str | None) -> None:
                self._start_save_task(self._on_batch_password(pwd, on_complete, target_keys=target_keys))

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

        if on_complete:
            on_complete(final_success)

    async def _on_batch_password(
        self,
        pwd: str | None,
        on_complete=None,
        target_keys: set[tuple[int, int]] | None = None,
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
                if on_complete:
                    on_complete(False)
                self._notify_quiescent()
                self._maybe_finish_quit()
                return
            except Exception as exc:
                self.notify_status(f"Sudo authentication failed: {exc}", level="error")
                self._save_failure_pending = True
                if on_complete:
                    on_complete(False)
                self._notify_quiescent()
                self._maybe_finish_quit()
                return
            finally:
                pwd = None

            if auth_res.returncode == 0:
                self.notify_status("Sudo authenticated. Retrying batch...", level="info")
                self._start_sudo_keepalive()
                self._save_auth_pending += 1

                def retry_after_dialog() -> None:
                    self._save_auth_pending -= 1
                    self.action_save_batch(on_complete=on_complete, target_keys=target_keys)
                    self._notify_quiescent()

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
        self._notify_quiescent()
        self._maybe_finish_quit()

    # =========================================================================
    # GLOBAL ACTIONS & SEARCH
    # =========================================================================
    def action_show_diff(self) -> None:
        if self._modal_active():
            if isinstance(self.screen, DiffScreen):
                self.screen.dismiss(None)
            return
        self.toggle_shortcut_active("d", True)
        self.push_screen(DiffScreen(), lambda _: self.toggle_shortcut_active("d", False))

    def action_show_shortcuts(self) -> None:
        if self._modal_active():
            if isinstance(self.screen, ShortcutsInfoScreen):
                self.screen.dismiss(None)
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
        if self._modal_active():
            return
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
            return self._modal_active() or self._local_search_is_open() or self._search_active
        return True

    def _item_is_searchable(self, tab_idx: int, item: ConfigItem) -> bool:
        if tab_idx not in self.tabs:
            return False
        spec = self._custom_spec(tab_idx)
        groups = None
        if spec is not None:
            if not isinstance(spec, dict) or not spec.get("show_options", False):
                return False
            groups = spec.get("option_groups")
            if groups and item.group not in groups:
                return False
        if item.type_ == "menu" and self.hide_missing_items:
            seen: set[int] = set()
            stack = [ref for ref in (item.uid, item.key) if ref]
            while stack:
                curr = str(stack.pop())
                for child in self._children_by_parent.get((tab_idx, curr), ()):
                    if id(child) in seen:
                        continue
                    seen.add(id(child))
                    if groups and child.group not in groups:
                        continue
                    if child.type_ == "menu":
                        if child.key:
                            stack.append(child.key)
                        stack.append(child.uid)
                    elif child.type_ in ("action", "preset") or child.exists_in_target:
                        return True
            return False
        return (
            item.type_ in ("menu", "action", "preset")
            or not self.hide_missing_items
            or item.exists_in_target
        )

    def _remember_search(self, results: list[tuple[int, int]], position: int = -1) -> None:
        self._search_results = [(tab, self.schema[tab][index]) for tab, index in results]
        self._search_position = position
        self._search_active = True

    def _locate_search_item(self, tab_idx: int, item: ConfigItem) -> tuple[int, int] | None:
        """Resolve a remembered ConfigItem object to its live (tab_idx, item_idx)."""
        items = self.schema.get(tab_idx)
        if not items:
            return None
        ref = self._item_refs.get(id(item))
        if ref is not None and ref[0] == tab_idx and 0 <= ref[1] < len(items) and items[ref[1]] is item:
            return ref
        for idx, candidate in enumerate(items):
            if candidate is item:
                self._item_refs[id(item)] = (tab_idx, idx)
                return (tab_idx, idx)
        target_uek = self._uid_engine_key(item)
        for idx, candidate in enumerate(items):
            if self._uid_engine_key(candidate) == target_uek:
                return (tab_idx, idx)
        return None

    def _reveal_search_result(self, tab_idx: int, item_idx: int) -> None:
        """Expand ancestors before locating a result; only populate if already mounted."""
        items = self.schema[tab_idx]
        current_pref = items[item_idx].parent_ref
        seen_prefs: set[str] = set()
        expanded_any = False

        while current_pref and current_pref not in seen_prefs:
            seen_prefs.add(current_pref)
            found = self._tab_item_index.get((tab_idx, str(current_pref)))
            if found is None:
                break
            _, parent = found
            if parent.is_parent or parent.type_ == "menu":
                expanded_any |= not parent.expanded
                parent.expanded = True
                current_pref = parent.parent_ref
            else:
                break

        if expanded_any:
            self._tab_dirty.add(tab_idx)

        if self.query(f"#list-{tab_idx}") and (
            tab_idx not in self._tab_populated or tab_idx in self._tab_dirty
        ):
            self._populate_option_list(tab_idx, maintain_highlight_id=f"item_{tab_idx}_{item_idx}")

    def _jump_to_search_result(self, tab_idx: int, item_idx: int) -> None:
        inp = self._local_search_input()
        inp.display = False
        self.toggle_shortcut_active("slash", False)
        self._reveal_search_result(tab_idx, item_idx)
        self._pending_search_target = (tab_idx, item_idx)
        if self._current_tab_index() == tab_idx and tab_idx in self._tab_populated:
            self._focus_search_target()
        else:
            self.action_switch_tab(tab_idx)
            self.call_after_refresh(self._focus_search_target)

    def action_submit_or_repeat_search(self) -> None:
        if self._search_active:
            self.action_repeat_search(1)
        else:
            self.action_submit_current()

    def action_repeat_search(self, direction: int) -> None:
        if self._modal_active() or not self._search_active:
            return
        total = len(self._search_results)
        if total <= 0:
            self.notify_status("No available search matches. Search again or press Esc to clear.", level="warning")
            return
        for step in range(1, total + 1):
            position = (self._search_position + direction * step) % total
            tab_idx, item = self._search_results[position]
            ref = self._locate_search_item(tab_idx, item)
            if ref is None:
                continue
            live_item = self.schema[ref[0]][ref[1]]
            if not self._item_is_searchable(ref[0], live_item):
                continue
            self._search_results[position] = (ref[0], live_item)
            self._search_position = position
            self._jump_to_search_result(*ref)
            self.notify_status(f"Search match {position + 1}/{total} · n/N: next/previous · Esc: clear")
            return
        self.notify_status("No available search matches. Search again or press Esc to clear.", level="warning")

    def _local_search_is_open(self) -> bool:
        try:
            return bool(self.query_one("#local-search", Input).display)
        except Exception:
            return False

    def _local_search_input(self) -> Input:
        return self.query_one("#local-search", Input)

    def action_focus_local_search(self) -> None:
        if self._modal_active():
            return

        inp = self._local_search_input()
        self._search_results.clear()
        self._search_position = -1
        self._search_active = False
        self._pending_search_target = None

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
        if not inp.display and not self._search_active:
            return

        self._search_results.clear()
        self._search_position = -1
        self._search_active = False
        self._pending_search_target = None

        with inp.prevent(Input.Changed):
            inp.value = ""

        inp.display = False
        self.toggle_shortcut_active("slash", False)

        if ol := self.current_option_list:
            self.call_after_refresh(ol.focus)

    @on(Input.Changed, "#local-search")
    def handle_local_search(self, event: Input.Changed) -> None:
        if not event.input.display or event.value != event.input.value:
            return

        query = event.value.casefold().replace(" ", "")
        self._search_results.clear()
        self._search_position = -1
        self._search_active = bool(query)
        if not query:
            return

        ol = self.current_option_list
        if ol is None:
            return

        tab_idx = int(ol.id.split("-")[1])
        results = [
            (tab_idx, index)
            for index, item in enumerate(self.schema.get(tab_idx, []))
            if query in item.label.casefold().replace(" ", "") and self._item_is_searchable(tab_idx, item)
        ]
        self._remember_search(results)
        for position, (_, item_idx) in enumerate(results):
            self._reveal_search_result(tab_idx, item_idx)
            opt_id = f"item_{tab_idx}_{item_idx}"
            try:
                target_idx = ol.get_option_index(opt_id)
            except OptionDoesNotExist:
                continue
            if ol.highlighted == target_idx:
                ol.last_highlighted_id = opt_id
            else:
                ol.highlighted = target_idx
            self._search_position = position
            ol.scroll_to_highlight()
            break

    @on(Input.Submitted, "#local-search")
    def submit_local_search(self, event: Input.Submitted) -> None:
        event.stop()
        event.input.display = False
        self.toggle_shortcut_active("slash", False)
        if ol := self.current_option_list:
            ol.focus()
        if self._search_active:
            if self._search_results:
                self.notify_status("Enter/n: next match · N: previous match · Esc: clear")
            else:
                self.notify_status("No matches found.", level="warning")

    def _focus_search_target(self) -> None:
        target = self._pending_search_target
        if target is None or self._current_tab_index() != target[0]:
            return
        tab_idx, item_idx = target
        if tab_idx not in self._tab_populated or tab_idx not in self._tab_data_ready:
            # Wait for lazy mount or deferred engine load to finish.
            return
        opt_id = f"item_{tab_idx}_{item_idx}"
        try:
            ol = self.query_one(f"#list-{tab_idx}", ConfigOptionList)
            target_idx = ol.get_option_index(opt_id)
            if ol.highlighted == target_idx:
                ol.last_highlighted_id = opt_id
            else:
                ol.highlighted = target_idx
            ol.focus()
            ol.scroll_to_highlight()
        except (NoMatches, OptionDoesNotExist):
            self.notify_status("Search result is no longer available.", level="warning")
        finally:
            self._pending_search_target = None

    def action_search(self) -> None:
        if self._modal_active():
            if isinstance(self.screen, SearchScreen):
                self.screen.dismiss(None)
            return

        self.toggle_shortcut_active("ctrl-f", True)
        search_screen = SearchScreen()

        def check_reply(result: tuple[int, int] | None) -> None:
            self.toggle_shortcut_active("ctrl-f", False)
            if result is not None:
                results = search_screen.results
                pos = results.index(result) if result in results else 0
                self._remember_search(results, pos)
                self.call_later(self._jump_to_search_result, *result)
                self.call_later(self.notify_status, "Enter/n: next match · N: previous match · Esc: clear")

        self.push_screen(search_screen, check_reply)

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

    def action_switch_tab_pos(self, position: int) -> None:
        if self._modal_active():
            return
        tab_keys = list(self.tabs.keys())
        if 0 <= position < len(tab_keys):
            self.action_switch_tab(tab_keys[position])

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
        if not ol or not (opt_id := ol.current_option_id):
            return

        parsed = self._get_item_from_id(opt_id)
        if not parsed:
            return

        tab_idx, item_idx, item = parsed

        if item.confirm_message and not bypass_lock:
            self.notify_status(
                f"Protected value: Press Enter to explicitly modify '{item.label}'.",
                level="warning",
            )
            return

        new_val = item.value

        if item.options and item.type_ != "bool":
            try:
                idx = item.options.index(item.value)
            except ValueError:
                idx = 0

            new_val = item.options[(idx + direction) % len(item.options)]
            if not item.values_equal(new_val, item.value):
                self._safe_apply_value(tab_idx, item_idx, item, new_val)
            return

        match item.type_:
            case "bool":
                if is_trigger_item(item):
                    self._safe_apply_value(tab_idx, item_idx, item, True, record_undo=False)
                    return
                new_val = not item.value

            case "int" | "float":
                base_val = item.value if item.value is not None else item.default
                if base_val is None or isinstance(base_val, bool):
                    return
                step = item.step or 1
                new_val = base_val + (direction * step)
                if item.min_val is not None:
                    new_val = max(item.min_val, new_val)
                if item.max_val is not None:
                    new_val = min(item.max_val, new_val)
                new_val = round(new_val, 6) if item.type_ == "float" else int(new_val)

            case "cycle":
                return

            case "color":
                step = item.step if item.step is not None else 15.0
                new_val = adjust_color_hue(item.to_str(item.value), direction * step)
                if new_val is None:
                    return

            case _:
                return

        if not item.values_equal(new_val, item.value):
            self._safe_apply_value(tab_idx, item_idx, item, new_val)

    def action_reset_item(self) -> None:
        if self._modal_active():
            return

        self.trigger_shortcut_blink("r")

        ol = self.current_option_list
        if not ol or not (opt_id := ol.current_option_id):
            return

        parsed = self._get_item_from_id(opt_id)
        if not parsed:
            return

        tab_idx, item_idx, item = parsed
        if item.read_only or is_trigger_item(item):
            return

        if item.is_parent or item.type_ == "menu":
            transaction: list[tuple[int, int, Any, Any]] = []
            seen_ids: set[int] = {id(item)}

            if (
                item.type_ not in ("menu", "action", "preset")
                and not item.values_equal(item.value, item.default)
            ):
                transaction.append((tab_idx, item_idx, item.value, item.default))

            stack = [ref for ref in (item.key, item.uid) if ref]
            while stack:
                curr = str(stack.pop())
                for child in self._children_by_parent.get((tab_idx, curr), ()):
                    marker = id(child)
                    if marker in seen_ids:
                        continue
                    seen_ids.add(marker)

                    if child.is_parent or child.type_ == "menu":
                        if child.key:
                            stack.append(child.key)
                        stack.append(child.uid)

                    if (
                        child.type_ in ("menu", "action", "preset")
                        or child.read_only
                        or is_trigger_item(child)
                    ):
                        continue
                    if not (child.exists_in_target or not self.hide_missing_items):
                        continue
                    if not child.values_equal(child.value, child.default):
                        ref = self._item_refs.get(marker)
                        if ref is not None and ref[0] == tab_idx:
                            transaction.append((tab_idx, ref[1], child.value, child.default))

            if transaction:
                self._apply_transaction(
                    transaction,
                    action_type="new",
                    success_msg=f"Reset settings under '{item.label}' to default.",
                )

        elif not item.values_equal(item.value, item.default):
            self._safe_apply_value(tab_idx, item_idx, item, item.default)

    def action_reset_all(self) -> None:
        if self._modal_active():
            return

        self.trigger_shortcut_blink("R")

        try:
            tab_idx = self._current_tab_index()
            if tab_idx is None:
                tab_idx = self._initial_tab if self._initial_tab is not None else 0

            items = self.schema.get(tab_idx, [])
            configurable_items = [
                (idx, item)
                for idx, item in enumerate(items)
                if item.type_ not in ("action", "menu", "preset")
                and not item.read_only
                and not is_trigger_item(item)
                and (item.exists_in_target or not self.hide_missing_items)
            ]

            has_changes = any(
                not item.values_equal(item.value, item.default)
                for _, item in configurable_items
            )

            tab_name = self.tabs.get(tab_idx, f"Tab {tab_idx}")
            if not has_changes:
                self.notify_status(f"All items in {tab_name} are already at default values.", level="info")
                return

            def on_confirm(confirmed: bool) -> None:
                if not confirmed:
                    return

                def apply_reset() -> None:
                    transaction = [
                        (tab_idx, item_idx, item.value, item.default)
                        for item_idx, item in configurable_items
                        if not item.values_equal(item.value, item.default)
                    ]
                    if transaction:
                        verb = "Reset" if self.auto_save else "Queued reset of"
                        msg = f"{verb} {len(transaction)} items in {tab_name}"
                        self._apply_transaction(transaction, action_type="new", success_msg=msg)
                        self._populate_option_list(tab_idx)
                    else:
                        self.notify_status(f"No items to reset in {tab_name}", level="info")

                self.call_later(apply_reset)

            safe_tab = _md_escape(str(tab_name))
            self.push_screen(
                ConfirmDialog(
                    f"Are you sure you want to reset all items in **{safe_tab}** to their factory defaults?",
                    title="Reset Page",
                    level="warning",
                ),
                on_confirm,
            )
        except Exception:
            LOGGER.exception("Reset page error")
            self.notify_status("Unable to reset page.", level="error")

    # =========================================================================
    # PRESET ACTIONS (OFF-EVENT-LOOP I/O)
    # =========================================================================
    def _write_preset_atomically(self, file_path: Path, payload: dict, *, exclusive: bool) -> None:
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

    async def _reload_user_presets_async(self) -> None:
        preset_records, preset_mtimes = await self._run_save_io(self._read_user_presets_with_mtimes)
        await self._wait_for_ui_quiescent()
        self._apply_user_presets(preset_records)
        self._rebuild_indexes(rebuild_presets=True)
        self._preset_mtimes = preset_mtimes
        self._initial_presets_mtime_set = True
        self._refresh_all_ui()

    def action_save_preset(self) -> None:
        if self._modal_active():
            return
        if not self.enable_user_presets or not self.require_boot_complete():
            return

        def check_reply(raw_name: str | None) -> None:
            if not raw_name:
                return
            name = _sanitize_preset_name(raw_name)
            if not name:
                self.notify_status("Invalid preset name.", level="error")
                return

            payload: dict[str, Any] = {}
            for items in self.schema.values():
                for item in items:
                    if (
                        item.type_ in ("action", "preset", "menu")
                        or item.read_only
                        or is_trigger_item(item)
                        or not item.exists_in_target
                        or self._get_item_engine_info(item) != self.default_engine_key
                    ):
                        continue
                    payload[item.uid] = clone_value(item.value)

            file_path = self.user_presets_dir / f"{name}.json"

            async def _save_worker(exclusive: bool) -> None:
                try:
                    await self._run_save_io(
                        self._write_preset_atomically, file_path, payload, exclusive=exclusive
                    )
                except FileExistsError:
                    self.notify_status(f"Preset already exists: {name}", level="error")
                    return
                except Exception as exc:
                    self.notify_status(f"Error saving preset: {exc}", level="error")
                    return

                self.notify_status(f"Successfully saved preset: {name}", level="success")
                await self._reload_user_presets_async()

            def start_save(*, exclusive: bool) -> None:
                self._track_preset_io_task(asyncio.create_task(_save_worker(exclusive)))

            if file_path.exists():
                safe_name = _md_escape(name)
                self.push_screen(
                    ConfirmDialog(
                        f"Preset **{safe_name}** already exists. Overwrite?",
                        title="Overwrite Preset",
                        level="warning",
                        default_confirm=False,
                    ),
                    lambda confirmed: start_save(exclusive=False) if confirmed else None,
                )
                return

            start_save(exclusive=True)

        self.push_screen(
            HybridInputScreen("Save Current State as Preset (Name):", ""),
            check_reply,
        )

    def action_import_preset(self) -> None:
        if self._modal_active() or not self.enable_user_presets:
            return

        def check_reply(raw_name: str | None) -> None:
            if not raw_name:
                return
            name = _sanitize_preset_name(raw_name)
            if not name:
                self.notify_status("Invalid preset name.", level="error")
                return

            file_path = self.user_presets_dir / f"{name}.json"

            async def _import_worker() -> None:
                try:
                    await self._run_save_io(
                        self._write_preset_atomically, file_path, {}, exclusive=True
                    )
                except FileExistsError:
                    self.notify_status(
                        f"Preset already exists: {name}. Choose a new name.",
                        level="error",
                    )
                    return
                except Exception as exc:
                    self.notify_status(f"Error importing preset: {exc}", level="error")
                    return

                self.notify_status(f"Created import template: {name}", level="success")
                await self._reload_user_presets_async()
                self.open_file_externally(file_path, button=1, touch_first=False)

            self._track_preset_io_task(asyncio.create_task(_import_worker()))

        self.push_screen(
            HybridInputScreen("Import Preset (Enter new name):", ""),
            check_reply,
        )

    def action_delete_user_preset(self) -> None:
        if self._modal_active():
            return

        ol = self.current_option_list
        if not ol or not (opt_id := ol.current_option_id):
            return

        parsed = self._get_item_from_id(opt_id)
        if not parsed:
            return

        _, _, item = parsed
        if item.type_ != "preset" or not item.key.startswith("__user_preset_"):
            return

        name = item.label.replace("User: ", "", 1)
        file_path = self.user_presets_dir / f"{name}.json"
        if not file_path.exists():
            return

        def do_delete(confirmed: bool) -> None:
            if not confirmed:
                return

            async def _delete_worker() -> None:
                try:
                    await self._run_save_io(file_path.unlink, missing_ok=True)
                except Exception as exc:
                    self.notify_status(f"Error deleting preset: {exc}", level="error")
                    return

                self.notify_status(f"Deleted preset: {name}", level="success")
                await self._reload_user_presets_async()

            self._track_preset_io_task(asyncio.create_task(_delete_worker()))

        safe_name = _md_escape(name)
        self.push_screen(
            ConfirmDialog(
                f"Are you sure you want to permanently delete the preset **{safe_name}**?",
                title="Delete Preset",
                level="danger",
            ),
            do_delete,
        )

    # =========================================================================
    # ITEM ACTIVATION
    # =========================================================================
    def action_submit_current(self) -> None:
        if self._modal_active():
            return

        ol = self.current_option_list
        if ol and (opt_id := ol.current_option_id):
            ol._last_click_x = None
            ol._mouse_down_highlight = None
            self._handle_item_action(
                ol,
                opt_id,
                click_x=None,
                was_already_selected=True,
                button=1,
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

            click_x = ol._last_click_x
            button = ol._last_click_button
            was_already_selected = ol._mouse_down_highlight == event.option_index

            self._handle_item_action(ol, event.option_id, click_x, was_already_selected, button)
            ol._last_click_x = None
            ol._mouse_down_highlight = None

    def _handle_item_action(
        self,
        ol: ConfigOptionList,
        opt_id: str | None,
        click_x: int | None = None,
        was_already_selected: bool = False,
        button: int = 1,
    ) -> None:
        if not opt_id:
            return

        parsed = self._get_item_from_id(opt_id)
        if not parsed:
            return

        tab_idx, item_idx, item = parsed
        if item.read_only:
            self.notify_status(f"{item.label} is read-only.", level="warning")
            return

        is_keyboard = (click_x is None)
        spans = self._row_hit_spans.get(opt_id, {})
        fold_span = spans.get("fold")
        instant_span = spans.get("instant")
        reset_span = spans.get("reset")

        in_fold = bool(not is_keyboard and fold_span and fold_span[0] <= click_x <= fold_span[1])
        in_instant = bool(not is_keyboard and instant_span and instant_span[0] <= click_x <= instant_span[1])
        in_reset = bool(not is_keyboard and reset_span and reset_span[0] <= click_x <= reset_span[1])

        if not is_keyboard and not in_fold and not in_instant and not was_already_selected:
            return

        if not is_keyboard and in_reset and was_already_selected:
            self.action_reset_item()
            return

        if (item.is_parent or item.type_ == "menu") and in_fold:
            self.action_toggle_expand()
            return

        if (
            not is_keyboard
            and not in_instant
            and item.type_ == "preset"
            and item.key.startswith("__user_preset_")
        ):
            name = item.label.replace("User: ", "", 1)
            path = self.user_presets_dir / f"{name}.json"
            if path.exists():
                self.open_file_externally(path, 1 if button == 0 else button, touch_first=False)
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
        if not ol or not (opt_id := ol.current_option_id):
            return

        parsed = self._get_item_from_id(opt_id)
        if not parsed:
            return

        tab_idx, _item_idx, item = parsed
        if item.is_parent or item.type_ == "menu":
            item.expanded = not item.expanded
            self._populate_option_list(tab_idx, maintain_highlight_id=opt_id)

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
            "python", "python3",
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
            if not base or base in wrappers:
                continue

            if base in shells:
                j = i + 1
                while j < len(tokens):
                    c = tokens[j].strip()
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
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
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
        if proc is not None and (transport := getattr(proc, "_transport", None)) is not None:
            transport.close()

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
        except (ProcessLookupError, PermissionError):
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
            self._action_procs.add(proc)
            try:
                await self._terminate_and_reap_action(proc)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
        for pending in drains:
            if pending is not None and not pending.done():
                pending.cancel()
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
            self._action_procs.discard(proc)

    @staticmethod
    def _track_background_task(
        task: asyncio.Task[Any],
        tasks: set[asyncio.Task[Any]],
    ) -> asyncio.Task[Any]:
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
        for proc in list(self._action_procs):
            try:
                self._kill_action_tree(proc)
            except Exception:
                pass
        for task in list(self._action_tasks):
            if not task.done():
                task.cancel()

    async def _shutdown_background_actions(self) -> None:
        if self._action_shutdown_done:
            return
        if self._action_shutdown_lock is None:
            self._action_shutdown_lock = asyncio.Lock()
        async with self._action_shutdown_lock:
            if self._action_shutdown_done:
                return
            self._action_shutdown_started = True
            self._cancel_background_actions()

            tasks = [t for t in list(self._action_tasks) if not t.done()]
            if tasks:
                try:
                    await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=5.0)
                except TimeoutError:
                    pass

            cleanup_tasks = [t for t in list(self._action_cleanup_tasks) if not t.done()]
            if cleanup_tasks:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*cleanup_tasks, return_exceptions=True),
                        timeout=5.0,
                    )
                except TimeoutError:
                    pass

            for proc in list(self._action_procs):
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
                self._action_procs.discard(proc)

            if (
                all(t.done() for t in tasks)
                and all(t.done() for t in cleanup_tasks)
                and not self._action_procs
            ):
                self._action_shutdown_done = True

    def execute_action(self, item: ConfigItem) -> None:
        if item.read_only:
            return
        if item.key == "__save_new_preset":
            self.action_save_preset()
            return
        if item.key == "__import_new_preset":
            self.action_import_preset()
            return

        command = str(item.default) if item.default else ""
        if not command:
            self.notify_status(f"No command defined for: {item.label}", level="error")
            return

        def do_execute() -> None:
            if self._action_shutdown_started:
                self.notify_status("Shutting down; action not started.", level="warning")
                return
            self._flash_action_ui(item)
            self.notify_status(f"Executing: {item.label}...", level="info")

            forced = item.force_interactive
            is_interactive = forced if isinstance(forced, bool) else self._check_interactive(command)

            if is_interactive:
                try:
                    completed = self.run_suspended_interactive(command, shell=True)
                    if completed is None:
                        return
                    if completed.returncode == 0:
                        self.notify_status(f"Action '{item.label}' completed.", level="success")
                    else:
                        self.notify_status(
                            f"Action '{item.label}' returned code {completed.returncode}.",
                            level="warning",
                        )
                except Exception as exc:
                    self.notify_status(f"Execution error: {str(exc)[:60]}", level="error")
                return

            async def run_noninteractive() -> None:
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
                        self._action_procs.add(p)
                        return p

                    spawn_task = asyncio.create_task(_spawn())
                    self._track_action_cleanup(spawn_task)
                    try:
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
                                        self._action_procs.add(proc)
                            except Exception:
                                pass
                        raise

                    assert proc.stdout is not None and proc.stderr is not None
                    drains = [
                        asyncio.create_task(self._drain_action_stream(proc.stdout, _ACTION_OUTPUT_LIMIT)),
                        asyncio.create_task(self._drain_action_stream(proc.stderr, _ACTION_OUTPUT_LIMIT)),
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
                            self.notify_status(f"Success: {out.splitlines()[0][:60]}", level="success")
                        else:
                            self.notify_status(f"Action '{item.label}' completed.", level="success")
                    else:
                        err = stderr.decode("utf-8", errors="replace").strip().splitlines()
                        first_err = err[0] if err else "Unknown execution error"
                        self.notify_status(f"Action failed: {first_err[:60]}", level="error")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.notify_status(f"Execution error: {str(exc)[:60]}", level="error")
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
                    level="warning",
                ),
                lambda confirm: do_execute() if confirm else None,
            )
        else:
            do_execute()

    def apply_preset(self, preset_item: ConfigItem) -> None:
        if not self.require_boot_complete():
            return

        if preset_item.key.startswith("__user_preset_"):
            if self._preset_hot_reload_task is not None and not self._preset_hot_reload_task.done():
                self.notify_status("A preset load is already in progress.", level="warning")
                return

            name = preset_item.label.replace("User: ", "", 1)
            file_path = self.user_presets_dir / f"{name}.json"

            async def _hot_reload_and_apply() -> None:
                def _load_json() -> dict[str, Any] | _STATE_MISSING:
                    if not file_path.exists():
                        return _STATE_MISSING
                    try:
                        with file_path.open("r", encoding="utf-8") as f:
                            loaded = json.load(f)
                        return loaded if isinstance(loaded, dict) else {"__INVALID__": True}
                    except Exception:
                        return {"__INVALID__": True}

                try:
                    reloaded = await self._run_save_io(_load_json)
                    if self._action_shutdown_started or self._modal_active():
                        return
                    if reloaded is _STATE_MISSING:
                        self.notify_status(
                            f"Preset '{name}' no longer exists on disk.",
                            level="error",
                        )
                        await self._reload_user_presets_async()
                        return
                    live_preset = next(
                        (itm for _, _, itm in self._preset_items if itm.key == preset_item.key),
                        preset_item,
                    )
                    live_preset.preset_payload = reloaded
                    self._apply_preset_payload(live_preset)
                finally:
                    self._preset_hot_reload_task = None

            self._preset_hot_reload_task = self._track_preset_io_task(
                asyncio.create_task(_hot_reload_and_apply())
            )
            return

        self._apply_preset_payload(preset_item)

    def _apply_preset_payload(self, preset_item: ConfigItem) -> None:
        if preset_item.preset_payload is None:
            self.notify_status("Preset contains no payload.", level="error")
            return
        if not isinstance(preset_item.preset_payload, dict) or preset_item.preset_payload.get("__INVALID__", False):
            self.notify_status("Preset payload is invalid.", level="error")
            return

        global_items = [
            entry for entry in self._configurable_items
            if self._get_item_engine_info(entry[2]) == self.default_engine_key
        ]
        self._preset_matrix.rebuild(global_items + self._preset_items)
        self._option_cache.invalidate_presets()

        def do_apply() -> None:
            transaction: list[tuple[int, int, Any, Any]] = []
            skipped = 0
            payload = preset_item.preset_payload
            assert payload is not None
            is_all_defaults = bool(payload.get("__ALL_DEFAULTS__", False))

            for t_idx, i_idx, target_item in self._configurable_items:
                if (
                    self._get_item_engine_info(target_item) != self.default_engine_key
                    or is_trigger_item(target_item)
                ):
                    continue
                key_path = target_item.uid
                if preset_item.preset_partial and not is_all_defaults and key_path not in payload:
                    continue
                if not target_item.exists_in_target:
                    skipped += 1
                    continue

                if is_all_defaults:
                    target_val = clone_value(target_item.default)
                elif key_path in payload:
                    target_val = payload[key_path]
                else:
                    target_val = clone_value(target_item.default)

                try:
                    validator = getattr(target_item, "validate_preset_value", None)
                    if callable(validator):
                        target_val = validator(target_val)
                    elif target_val is not None:
                        target_val = target_item.parse_user_input(target_val)
                    elif target_item.default is not None:
                        raise ValueError("Null value is not allowed for this setting.")
                except Exception as exc:
                    self.notify_status(
                        f"Cannot apply preset value for {key_path}: {exc}",
                        level="error",
                    )
                    return

                if not target_item.values_equal(target_item.value, target_val):
                    transaction.append((t_idx, i_idx, target_item.value, target_val))

            if not transaction:
                if skipped > 0:
                    self.notify_status(
                        f"Preset applied, but {skipped} items were missing/invalid.",
                        level="warning",
                    )
                else:
                    self.notify_status("Preset already active (no changes needed).", level="info")
                return

            msg = f"Preset '{preset_item.label}' applied."
            if skipped > 0:
                msg += f" ({skipped} skipped)"

            self._apply_transaction(
                transaction,
                action_type="new",
                success_msg=msg,
                immediate_commit=True,
            )

        if preset_item.confirm_message:
            self.push_screen(
                ConfirmDialog(
                    preset_item.confirm_message,
                    title=f"Apply Preset: {preset_item.label}",
                    level="warning",
                ),
                lambda confirm: self.call_later(do_apply) if confirm else None,
            )
        else:
            do_apply()

    def prompt_string(self, tab_idx: int, item_idx: int, item: ConfigItem) -> None:
        def check_reply(new_val: str | None) -> None:
            if new_val is None:
                return
            try:
                parsed_val = item.parse_user_input(new_val)
            except ValueError as exc:
                self.notify_status(f"Error: {exc}", level="error")
                return
            if self._is_unchanged_submission(item, parsed_val):
                return
            self._safe_apply_value(tab_idx, item_idx, item, parsed_val)

        self.push_screen(
            HybridInputScreen(
                f"Enter new {item.label}:",
                "" if item.value is None else item.to_str(item.value),
                item.options,
            ),
            check_reply,
        )

    def prompt_picker(self, tab_idx: int, item_idx: int, item: ConfigItem) -> None:
        def check_reply(new_val: str | None) -> None:
            if new_val is None:
                return
            if self._is_unchanged_submission(item, new_val):
                return
            self._safe_apply_value(tab_idx, item_idx, item, new_val)

        self.push_screen(
            PickerScreen(
                item.label,
                item.options,
                item.hints,
                current=item.to_str(item.value),
            ),
            check_reply,
        )
