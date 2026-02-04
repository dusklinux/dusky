"""
Row widget definitions for the Dusky Control Center.
Optimized for stability (Thread Guards), efficiency (Thread Pooling), and Type Safety.

GTK4/Libadwaita compatible with proper lifecycle management (do_unroot).
"""
from __future__ import annotations

import atexit
import logging
import shlex
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final, NotRequired, Protocol, TypedDict, Union

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Pango

import lib.utility as utility

log = logging.getLogger(__name__)


# =============================================================================
# CONSTANTS
# =============================================================================
DEFAULT_ICON: Final[str] = "utilities-terminal-symbolic"
DEFAULT_INTERVAL_SECONDS: Final[int] = 5
MONITOR_INTERVAL_SECONDS: Final[int] = 2
MIN_STEP_VALUE: Final[float] = 1e-9
SLIDER_DEBOUNCE_MS: Final[int] = 150  # Wait ms after drag stops before executing

LABEL_PLACEHOLDER: Final[str] = "..."
LABEL_NA: Final[str] = "N/A"
LABEL_TIMEOUT: Final[str] = "Timeout"
LABEL_ERROR: Final[str] = "Error"
STATE_ON: Final[str] = "On"
STATE_OFF: Final[str] = "Off"

SUBPROCESS_TIMEOUT_SHORT: Final[int] = 2
SUBPROCESS_TIMEOUT_LONG: Final[int] = 5

ICON_PIXEL_SIZE: Final[int] = 42
LABEL_MAX_WIDTH_CHARS: Final[int] = 16

TRUE_VALUES: Final[frozenset[str]] = frozenset(
    {"enabled", "yes", "true", "1", "on", "active", "set", "running", "open", "high"}
)

# =============================================================================
# LAZY THREAD POOL (Startup Optimization)
# =============================================================================
_EXECUTOR: ThreadPoolExecutor | None = None
_EXECUTOR_LOCK = threading.Lock()

def _get_executor() -> ThreadPoolExecutor:
    """Lazily initialize the thread pool to avoid overhead at import time."""
    global _EXECUTOR
    if _EXECUTOR is None:
        with _EXECUTOR_LOCK:
            if _EXECUTOR is None:
                _EXECUTOR = ThreadPoolExecutor(
                    max_workers=4, 
                    thread_name_prefix="dusky-row-"
                )
    return _EXECUTOR

def _shutdown_executor() -> None:
    """Gracefully shut down the thread pool on application exit."""
    global _EXECUTOR
    if _EXECUTOR is not None:
        log.debug("Shutting down row widget thread pool...")
        _EXECUTOR.shutdown(wait=False, cancel_futures=True)
        _EXECUTOR = None

atexit.register(_shutdown_executor)


# =============================================================================
# TYPE DEFINITIONS
# =============================================================================
class IconConfigExec(TypedDict):
    type: str  # "exec"
    command: str
    interval: int
    name: NotRequired[str]


class IconConfigFile(TypedDict):
    type: str  # "file"
    path: str


class IconConfigStatic(TypedDict):
    name: str


IconConfig = Union[str, IconConfigExec, IconConfigFile, IconConfigStatic]


class ActionExec(TypedDict, total=False):
    type: str  # "exec"
    command: str
    terminal: bool


class ActionRedirect(TypedDict):
    type: str  # "redirect"
    page: str


class ActionToggle(TypedDict, total=False):
    enabled: ActionExec
    disabled: ActionExec


# Flexible definition to handle both strictly typed and generic dicts
ActionConfig = Union[ActionExec, ActionRedirect, ActionToggle, dict[str, object]]


class ValueConfigExec(TypedDict):
    type: str  # "exec"
    command: str


class ValueConfigStatic(TypedDict):
    type: str  # "static"
    text: str


class ValueConfigFile(TypedDict):
    type: str  # "file"
    path: str


class ValueConfigSystem(TypedDict):
    type: str  # "system"
    key: str


ValueConfig = Union[
    str, ValueConfigExec, ValueConfigStatic, ValueConfigFile, ValueConfigSystem
]


class RowProperties(TypedDict, total=False):
    title: str
    description: str
    icon: IconConfig
    style: str
    button_text: str
    interval: int
    key: str
    key_inverse: bool
    save_as_int: bool
    state_command: str
    min: float
    max: float
    step: float
    default: float


class RowContext(TypedDict, total=False):
    stack: Adw.ViewStack | None
    config: dict[str, object]
    sidebar: Gtk.ListBox | None
    toast_overlay: Adw.ToastOverlay | None
    nav_view: Adw.NavigationView | None
    builder_func: Callable[..., Adw.NavigationPage] | None


@dataclass
class WidgetState:
    """
    Thread-safe state container for widget async operations.
    All access to mutable fields must hold the lock.
    """
    lock: threading.Lock = field(default_factory=threading.Lock)
    is_destroyed: bool = False
    icon_source_id: int = 0
    monitor_source_id: int = 0
    update_source_id: int = 0
    debounce_source_id: int = 0
    is_icon_updating: bool = False
    is_monitoring: bool = False
    is_value_updating: bool = False


# =============================================================================
# PROTOCOLS FOR MIXINS
# =============================================================================
class DynamicIconHost(Protocol):
    """Protocol defining requirements for classes using DynamicIconMixin."""
    _state: WidgetState
    icon_widget: Gtk.Image


class StateMonitorHost(Protocol):
    """Protocol defining requirements for classes using StateMonitorMixin."""
    _state: WidgetState
    properties: RowProperties
    key_inverse: bool


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================
def _safe_int(value: object, default: int) -> int:
    """Safely convert a value to int, returning default on failure."""
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _safe_float(value: object, default: float) -> float:
    """Safely convert a value to float, returning default on failure."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _is_dynamic_icon(icon_config: object) -> bool:
    """Check if an icon config specifies a dynamic (exec) icon."""
    if not isinstance(icon_config, dict):
        return False
    return (
        icon_config.get("type") == "exec"
        and _safe_int(icon_config.get("interval"), 0) > 0
        and bool(icon_config.get("command", ""))
    )


def _perform_redirect(
    page_id: str, config: dict[str, object], sidebar: Gtk.ListBox | None
) -> None:
    """Navigate to a page by its ID."""
    if not page_id or sidebar is None:
        return

    pages = config.get("pages")
    if not isinstance(pages, list):
        return

    for idx, page in enumerate(pages):
        if isinstance(page, dict) and page.get("id") == page_id:
            row = sidebar.get_row_at_index(idx)
            if row is not None:
                sidebar.select_row(row)
            return


def _expand_path(path: str) -> Path:
    """Expand user home directory in path safely."""
    return Path(path).expanduser()


def _resolve_static_icon_name(icon_config: object) -> str:
    """Extract static icon name from various config formats."""
    if isinstance(icon_config, str):
        return icon_config if icon_config else DEFAULT_ICON
    if isinstance(icon_config, dict):
        return str(icon_config.get("name", DEFAULT_ICON))
    return DEFAULT_ICON


def _safe_source_remove(source_id: int) -> None:
    """Safely remove a GLib source, handling edge cases."""
    if source_id > 0:
        try:
            GLib.source_remove(source_id)
        except GLib.Error:
            pass  # Source already removed or invalid


# =============================================================================
# MIXIN: DYNAMIC ICON UPDATES
# =============================================================================
class DynamicIconMixin:
    """
    Mixin providing thread-safe dynamic icon updates.
    Implementing classes must satisfy DynamicIconHost protocol.
    """

    _state: WidgetState
    icon_widget: Gtk.Image

    def _start_icon_update_loop(self, icon_config: dict[str, object]) -> None:
        """Begin periodic icon updates from a command."""
        interval = _safe_int(icon_config.get("interval"), DEFAULT_INTERVAL_SECONDS)
        command = icon_config.get("command")
        if not isinstance(command, str) or not command.strip():
            return

        cmd = command.strip()
        self._schedule_icon_fetch(cmd)

        with self._state.lock:
            if self._state.is_destroyed:
                return
            self._state.icon_source_id = GLib.timeout_add_seconds(
                interval, self._icon_update_tick, cmd
            )

    def _icon_update_tick(self, command: str) -> int:
        """Timer callback - check if update is needed."""
        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE
            if self._state.is_icon_updating:
                return GLib.SOURCE_CONTINUE
            self._state.is_icon_updating = True

        self._schedule_icon_fetch(command)
        return GLib.SOURCE_CONTINUE

    def _schedule_icon_fetch(self, command: str) -> None:
        """Submit icon fetch to thread pool."""
        with self._state.lock:
            if self._state.is_destroyed:
                return
        _get_executor().submit(self._fetch_icon_async, command)

    def _fetch_icon_async(self, command: str) -> None:
        """Background thread: execute command and fetch icon name."""
        try:
            with self._state.lock:
                if self._state.is_destroyed:
                    return

            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=SUBPROCESS_TIMEOUT_SHORT,
            )
            new_icon = result.stdout.strip()
            if new_icon:
                GLib.idle_add(self._apply_icon_update, new_icon)

        except subprocess.TimeoutExpired:
            log.debug("Icon command timed out: %s", command[:50])
        except subprocess.SubprocessError as e:
            log.warning("Icon fetch failed: %s", e)
        finally:
            with self._state.lock:
                self._state.is_icon_updating = False

    def _apply_icon_update(self, new_icon: str) -> int:
        """Main thread: apply icon update to widget."""
        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE

        # Implicit protocol requirement: self.icon_widget must exist
        current = self.icon_widget.get_icon_name()
        if current != new_icon:
            self.icon_widget.set_from_icon_name(new_icon)
        return GLib.SOURCE_REMOVE

    def _cleanup_icon_source(self) -> None:
        """Clean up icon update timer."""
        with self._state.lock:
            sid = self._state.icon_source_id
            self._state.icon_source_id = 0
        _safe_source_remove(sid)


# =============================================================================
# MIXIN: STATE MONITORING
# =============================================================================
class StateMonitorMixin:
    """
    Mixin providing thread-safe state monitoring for toggles.
    Implementing classes must satisfy StateMonitorHost protocol.
    """

    _state: WidgetState
    properties: RowProperties
    key_inverse: bool

    def _start_state_monitor(self) -> None:
        """Begin periodic state monitoring."""
        has_key = bool(self.properties.get("key", ""))
        has_state_cmd = bool(self.properties.get("state_command", ""))

        if not has_key and not has_state_cmd:
            return

        interval = _safe_int(self.properties.get("interval"), MONITOR_INTERVAL_SECONDS)
        if interval <= 0:
            return

        with self._state.lock:
            if self._state.is_destroyed:
                return
            self._state.monitor_source_id = GLib.timeout_add_seconds(
                interval, self._monitor_state_tick
            )

    def _monitor_state_tick(self) -> int:
        """Timer callback - check if state poll is needed."""
        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE
            if self._state.is_monitoring:
                return GLib.SOURCE_CONTINUE
            self._state.is_monitoring = True

        _get_executor().submit(self._check_state_async)
        return GLib.SOURCE_CONTINUE

    def _check_state_async(self) -> None:
        """Background thread: check toggle state."""
        try:
            with self._state.lock:
                if self._state.is_destroyed:
                    return

            state_cmd = self.properties.get("state_command", "")
            if isinstance(state_cmd, str) and state_cmd.strip():
                result = subprocess.run(
                    state_cmd.strip(),
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=SUBPROCESS_TIMEOUT_SHORT,
                )
                is_on = result.stdout.strip().lower() in TRUE_VALUES
                GLib.idle_add(self._apply_state_update, is_on)
                return

            key = self.properties.get("key", "")
            if isinstance(key, str) and key.strip():
                # FIXED: is_inversed is now a keyword argument in utility.load_setting
                val = utility.load_setting(
                    key.strip(), 
                    default=False, 
                    is_inversed=self.key_inverse
                )
                if isinstance(val, bool):
                    GLib.idle_add(self._apply_state_update, val)

        except (subprocess.TimeoutExpired, OSError, subprocess.SubprocessError):
            log.debug("State check failed for %s", self.properties.get("title", "unknown"))
        finally:
            with self._state.lock:
                self._state.is_monitoring = False

    def _apply_state_update(self, new_state: bool) -> int:
        """Main thread: apply state update - must be implemented by subclass."""
        raise NotImplementedError

    def _cleanup_monitor_source(self) -> None:
        """Clean up monitor timer."""
        with self._state.lock:
            sid = self._state.monitor_source_id
            self._state.monitor_source_id = 0
        _safe_source_remove(sid)


# =============================================================================
# BASE ROW CLASS
# =============================================================================
class BaseActionRow(DynamicIconMixin, Adw.ActionRow):
    """
    Base class for all action row widgets.
    Provides icon handling, lifecycle management, and common properties.
    """
    
    __gtype_name__ = "DuskyBaseActionRow"

    def __init__(
        self,
        properties: RowProperties,
        on_action: ActionConfig | None = None,
        context: RowContext | None = None,
    ) -> None:
        super().__init__()
        self.add_css_class("action-row")

        self._state = WidgetState()
        self.properties = properties
        self.on_action: ActionConfig = on_action or {}
        self.context: RowContext = context or {}

        # Context references
        self.config: dict[str, object] = self.context.get("config") or {}
        self.sidebar: Gtk.ListBox | None = self.context.get("sidebar")
        self.toast_overlay: Adw.ToastOverlay | None = self.context.get("toast_overlay")
        self.nav_view: Adw.NavigationView | None = self.context.get("nav_view")
        self.builder_func = self.context.get("builder_func")

        # UI Setup
        title = str(properties.get("title", "Unnamed"))
        self.set_title(GLib.markup_escape_text(title))

        if sub := properties.get("description", ""):
            self.set_subtitle(GLib.markup_escape_text(str(sub)))

        icon_config = properties.get("icon", DEFAULT_ICON)
        self.icon_widget = self._create_icon_widget(icon_config)
        self.add_prefix(self.icon_widget)

        if _is_dynamic_icon(icon_config) and isinstance(icon_config, dict):
            self._start_icon_update_loop(icon_config)

    def _create_icon_widget(self, icon: object) -> Gtk.Image:
        """Create the appropriate icon widget based on config."""
        if isinstance(icon, dict) and icon.get("type") == "file":
            if path := icon.get("path"):
                p = _expand_path(str(path))
                if p.exists():
                    img = Gtk.Image.new_from_file(str(p))
                    img.add_css_class("action-row-prefix-icon")
                    return img

        icon_name = _resolve_static_icon_name(icon)
        img = Gtk.Image.new_from_icon_name(icon_name)
        img.add_css_class("action-row-prefix-icon")
        return img

    def do_unroot(self) -> None:
        """GTK4 lifecycle: called when widget is removed from tree."""
        self._perform_cleanup()
        # Chain up to parent
        Adw.ActionRow.do_unroot(self)

    def _perform_cleanup(self) -> None:
        """Clean up all resources - called on unroot."""
        with self._state.lock:
            if self._state.is_destroyed:
                return
            self._state.is_destroyed = True
            uid = self._state.update_source_id
            did = self._state.debounce_source_id
            self._state.update_source_id = 0
            self._state.debounce_source_id = 0

        _safe_source_remove(uid)
        _safe_source_remove(did)
        self._cleanup_icon_source()


# =============================================================================
# ROW IMPLEMENTATIONS
# =============================================================================
class ButtonRow(BaseActionRow):
    """A row with a button that executes an action."""

    __gtype_name__ = "DuskyButtonRow"

    def __init__(
        self,
        properties: RowProperties,
        on_press: ActionConfig | None = None,
        context: RowContext | None = None,
    ) -> None:
        super().__init__(properties, on_press, context)

        style = str(properties.get("style", "default")).lower()
        btn = Gtk.Button(label=str(properties.get("button_text", "Run")))
        btn.add_css_class("run-btn")
        btn.set_valign(Gtk.Align.CENTER)

        match style:
            case "destructive":
                btn.add_css_class("destructive-action")
            case "suggested":
                btn.add_css_class("suggested-action")
            case _:
                btn.add_css_class("default-action")

        btn.connect("clicked", self._on_button_clicked)
        self.add_suffix(btn)
        self.set_activatable_widget(btn)

    def _on_button_clicked(self, button: Gtk.Button) -> None:
        """Handle button click."""
        if not isinstance(self.on_action, dict):
            return

        a_type = self.on_action.get("type")
        match a_type:
            case "exec":
                cmd = self.on_action.get("command", "")
                if isinstance(cmd, str) and cmd.strip():
                    title = str(self.properties.get("title", "Command"))
                    term = bool(self.on_action.get("terminal", False))
                    success = utility.execute_command(cmd.strip(), title, term)
                    utility.toast(
                        self.toast_overlay,
                        f"{'▶ Launched' if success else '✖ Failed'}: {title}",
                        2 if success else 4,
                    )
            case "redirect":
                if pid := self.on_action.get("page"):
                    _perform_redirect(str(pid), self.config, self.sidebar)


class ToggleRow(StateMonitorMixin, BaseActionRow):
    """A row with a toggle switch."""

    __gtype_name__ = "DuskyToggleRow"

    def __init__(
        self,
        properties: RowProperties,
        on_toggle: ActionConfig | None = None,
        context: RowContext | None = None,
    ) -> None:
        super().__init__(properties, on_toggle, context)
        self.save_as_int = bool(properties.get("save_as_int", False))
        self.key_inverse = bool(properties.get("key_inverse", False))
        self._programmatic_update = False
        self._update_lock = threading.Lock()

        self.toggle_switch = Gtk.Switch()
        self.toggle_switch.set_valign(Gtk.Align.CENTER)

        # Load initial state
        if key := properties.get("key"):
            # FIXED: is_inversed is now a keyword argument in utility.load_setting
            val = utility.load_setting(
                str(key).strip(), 
                default=False, 
                is_inversed=self.key_inverse
            )
            if isinstance(val, bool):
                self.toggle_switch.set_active(val)

        self.toggle_switch.connect("state-set", self._on_toggle_changed)
        self.add_suffix(self.toggle_switch)
        self.set_activatable_widget(self.toggle_switch)
        self._start_state_monitor()

    def _apply_state_update(self, new_state: bool) -> int:
        """Apply polled state to the toggle switch."""
        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE

        if new_state != self.toggle_switch.get_active():
            with self._update_lock:
                self._programmatic_update = True
                self.toggle_switch.set_active(new_state)
                self._programmatic_update = False
        return GLib.SOURCE_REMOVE

    def _perform_cleanup(self) -> None:
        """Extended cleanup for toggle row."""
        super()._perform_cleanup()
        self._cleanup_monitor_source()

    def _on_toggle_changed(self, switch: Gtk.Switch, state: bool) -> bool:
        """Handle user toggle interaction."""
        with self._update_lock:
            if self._programmatic_update:
                return False

        if isinstance(self.on_action, dict):
            action_key = "enabled" if state else "disabled"
            if action := self.on_action.get(action_key):
                if isinstance(action, dict) and (cmd := action.get("command")):
                    utility.execute_command(
                        str(cmd).strip(), "Toggle", bool(action.get("terminal", False))
                    )

        if key := self.properties.get("key"):
            utility.save_setting(
                str(key).strip(), state ^ self.key_inverse, self.save_as_int
            )
        return False


class LabelRow(BaseActionRow):
    """A row displaying a dynamic or static value."""

    __gtype_name__ = "DuskyLabelRow"

    def __init__(
        self,
        properties: RowProperties,
        value: ValueConfig | None = None,
        context: RowContext | None = None,
    ) -> None:
        super().__init__(properties, None, context)
        self.value_config: ValueConfig = value if value is not None else LABEL_NA

        self.value_label = Gtk.Label(label=LABEL_PLACEHOLDER, css_classes=["dim-label"])
        self.value_label.set_valign(Gtk.Align.CENTER)
        self.value_label.set_halign(Gtk.Align.END)
        self.value_label.set_hexpand(True)
        self.value_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.add_suffix(self.value_label)

        self._trigger_update()

        interval = _safe_int(properties.get("interval"), 0)
        if interval > 0:
            with self._state.lock:
                if not self._state.is_destroyed:
                    self._state.update_source_id = GLib.timeout_add_seconds(
                        interval, self._on_timeout
                    )

    def _on_timeout(self) -> int:
        """Timer callback for periodic updates."""
        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE
        self._trigger_update()
        return GLib.SOURCE_CONTINUE

    def _trigger_update(self) -> None:
        """Trigger an async value update if not already running."""
        with self._state.lock:
            if self._state.is_value_updating or self._state.is_destroyed:
                return
            self._state.is_value_updating = True
        _get_executor().submit(self._load_value_async)

    def _load_value_async(self) -> None:
        """Background thread: load the value."""
        try:
            with self._state.lock:
                if self._state.is_destroyed:
                    return
            res = self._get_value_text(self.value_config)
            GLib.idle_add(self._update_label, res)
        finally:
            with self._state.lock:
                self._state.is_value_updating = False

    def _update_label(self, text: str) -> int:
        """Main thread: update the label widget."""
        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE
        if self.value_label.get_label() != text:
            self.value_label.set_label(text)
            self.value_label.remove_css_class("dim-label")
        return GLib.SOURCE_REMOVE

    def _get_value_text(self, val: ValueConfig) -> str:
        """Resolve a value config to a display string."""
        if isinstance(val, str):
            return val
        if not isinstance(val, dict):
            return LABEL_NA

        match val.get("type"):
            case "exec":
                return self._exec_cmd(str(val.get("command", "")))
            case "static":
                return str(val.get("text", LABEL_NA))
            case "file":
                return self._read_file(str(val.get("path", "")))
            case "system":
                result = utility.get_system_value(str(val.get("key", "")))
                return str(result) if result else LABEL_NA
        return LABEL_NA

    def _exec_cmd(self, cmd: str) -> str:
        """Execute a command and return its output."""
        cmd = cmd.strip()
        if not cmd:
            return LABEL_NA

        # Optimization: handle simple 'cat' commands directly
        if cmd.startswith("cat "):
            try:
                parts = shlex.split(cmd)
                if len(parts) == 2:
                    return self._read_file(parts[1])
            except ValueError:
                pass

        try:
            res = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=SUBPROCESS_TIMEOUT_LONG,
            )
            return res.stdout.strip() or LABEL_NA
        except subprocess.TimeoutExpired:
            return LABEL_TIMEOUT
        except subprocess.SubprocessError:
            return LABEL_ERROR

    def _read_file(self, path: str) -> str:
        """Read a file and return its contents."""
        if not path.strip():
            return LABEL_NA
        try:
            return _expand_path(path.strip()).read_text(encoding="utf-8").strip()
        except OSError:
            return LABEL_NA


class SliderRow(BaseActionRow):
    """A row with a slider for numeric values."""

    __gtype_name__ = "DuskySliderRow"

    def __init__(
        self,
        properties: RowProperties,
        on_change: ActionConfig | None = None,
        context: RowContext | None = None,
    ) -> None:
        super().__init__(properties, on_change, context)
        self.min_val = _safe_float(properties.get("min"), 0.0)
        self.max_val = _safe_float(properties.get("max"), 100.0)
        step = _safe_float(properties.get("step"), 1.0)
        self.step_val = step if step > MIN_STEP_VALUE else 1.0

        self._slider_lock = threading.Lock()
        self._slider_changing = False
        self._last_snapped: float | None = None
        self._pending_value: float | None = None

        default_val = _safe_float(properties.get("default"), self.min_val)
        adj = Gtk.Adjustment(
            value=default_val,
            lower=self.min_val,
            upper=self.max_val,
            step_increment=self.step_val,
            page_increment=self.step_val * 10,
            page_size=0,
        )
        self.slider = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj
        )
        self.slider.set_valign(Gtk.Align.CENTER)
        self.slider.set_hexpand(True)
        self.slider.set_draw_value(False)
        self.slider.connect("value-changed", self._on_value_changed)
        self.add_suffix(self.slider)

    def _on_value_changed(self, scale: Gtk.Scale) -> None:
        """Handle slider value changes with debouncing."""
        with self._slider_lock:
            if self._slider_changing:
                return
            val = scale.get_value()
            snapped = round(val / self.step_val) * self.step_val
            snapped = max(self.min_val, min(snapped, self.max_val))

            # Avoid redundant updates
            if (
                self._last_snapped is not None
                and abs(snapped - self._last_snapped) < MIN_STEP_VALUE
            ):
                return
            self._last_snapped = snapped

            # Snap the slider position if needed
            if abs(snapped - val) > MIN_STEP_VALUE:
                self._slider_changing = True
                try:
                    self.slider.set_value(snapped)
                finally:
                    self._slider_changing = False

            # Debounce command execution
            self._pending_value = snapped

        # Cancel existing debounce and schedule new one
        with self._state.lock:
            if self._state.is_destroyed:
                return
            old_id = self._state.debounce_source_id
            self._state.debounce_source_id = 0

        _safe_source_remove(old_id)

        with self._state.lock:
            if self._state.is_destroyed:
                return
            self._state.debounce_source_id = GLib.timeout_add(
                SLIDER_DEBOUNCE_MS, self._execute_debounced_action
            )

    def _execute_debounced_action(self) -> int:
        """Execute the action after debounce period."""
        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE
            self._state.debounce_source_id = 0

        with self._slider_lock:
            value = self._pending_value
            self._pending_value = None

        if value is None:
            return GLib.SOURCE_REMOVE

        if isinstance(self.on_action, dict) and self.on_action.get("type") == "exec":
            if cmd := self.on_action.get("command"):
                final_cmd = str(cmd).replace("{value}", str(int(value)))
                utility.execute_command(
                    final_cmd, "Slider", bool(self.on_action.get("terminal", False))
                )
        return GLib.SOURCE_REMOVE


class NavigationRow(BaseActionRow):
    """A row that navigates to a subpage."""

    __gtype_name__ = "DuskyNavigationRow"

    def __init__(
        self,
        properties: RowProperties,
        layout_data: list[object] | None = None,
        context: RowContext | None = None,
    ) -> None:
        super().__init__(properties, None, context)
        self.layout_data: list[object] = layout_data or []
        self.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        self.set_activatable(True)
        self.connect("activated", self._on_activated)

    def _on_activated(self, row: Adw.ActionRow) -> None:
        """Handle row activation - navigate to subpage."""
        if self.nav_view and self.builder_func:
            title = str(self.properties.get("title", "Subpage"))
            self.nav_view.push(self.builder_func(title, self.layout_data, self.context))


# =============================================================================
# GRID CARDS
# =============================================================================
class GridCardBase(Gtk.Button):
    """Base class for grid card widgets."""

    __gtype_name__ = "DuskyGridCardBase"

    def __init__(
        self,
        properties: RowProperties,
        on_action: ActionConfig | None = None,
        context: RowContext | None = None,
    ) -> None:
        super().__init__()
        self.add_css_class("hero-card")
        self._state = WidgetState()
        self.properties = properties
        self.on_action: ActionConfig = on_action or {}
        self.context: RowContext = context or {}
        self.toast_overlay: Adw.ToastOverlay | None = self.context.get("toast_overlay")
        self.icon_widget: Gtk.Image | None = None

        match str(properties.get("style", "default")).lower():
            case "destructive":
                self.add_css_class("destructive-card")
            case "suggested":
                self.add_css_class("suggested-card")

    def do_unroot(self) -> None:
        """GTK4 lifecycle: called when widget is removed from tree."""
        self._perform_cleanup()
        Gtk.Button.do_unroot(self)

    def _perform_cleanup(self) -> None:
        """Clean up resources."""
        with self._state.lock:
            self._state.is_destroyed = True

    def _build_content(self, icon: str, title: str) -> Gtk.Box:
        """Build the card content layout."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)

        img = Gtk.Image.new_from_icon_name(icon)
        img.set_pixel_size(ICON_PIXEL_SIZE)
        img.add_css_class("hero-icon")
        self.icon_widget = img

        lbl = Gtk.Label(label=title, css_classes=["hero-title"])
        lbl.set_wrap(True)
        lbl.set_justify(Gtk.Justification.CENTER)
        lbl.set_max_width_chars(LABEL_MAX_WIDTH_CHARS)

        box.append(img)
        box.append(lbl)
        return box


class GridCard(DynamicIconMixin, GridCardBase):
    """A grid card with an icon and action."""

    __gtype_name__ = "DuskyGridCard"

    def __init__(
        self,
        properties: RowProperties,
        on_press: ActionConfig | None = None,
        context: RowContext | None = None,
    ) -> None:
        super().__init__(properties, on_press, context)
        icon_conf = properties.get("icon", DEFAULT_ICON)

        box = self._build_content(
            _resolve_static_icon_name(icon_conf), str(properties.get("title", "Unnamed"))
        )
        self.set_child(box)
        self.connect("clicked", self._on_clicked)

        if _is_dynamic_icon(icon_conf) and isinstance(icon_conf, dict):
            self._start_icon_update_loop(icon_conf)

    def _perform_cleanup(self) -> None:
        """Extended cleanup for grid card."""
        super()._perform_cleanup()
        self._cleanup_icon_source()

    def _on_clicked(self, button: Gtk.Button) -> None:
        """Handle card click."""
        if not isinstance(self.on_action, dict):
            return

        match self.on_action.get("type"):
            case "exec":
                if cmd := self.on_action.get("command"):
                    success = utility.execute_command(
                        str(cmd).strip(),
                        "Command",
                        bool(self.on_action.get("terminal", False)),
                    )
                    utility.toast(
                        self.toast_overlay, "▶ Launched" if success else "✖ Failed"
                    )
            case "redirect":
                if pid := self.on_action.get("page"):
                    _perform_redirect(
                        str(pid),
                        self.context.get("config") or {},
                        self.context.get("sidebar"),
                    )


class GridToggleCard(DynamicIconMixin, StateMonitorMixin, GridCardBase):
    """A grid card with toggle functionality and optional dynamic icon."""

    __gtype_name__ = "DuskyGridToggleCard"

    def __init__(
        self,
        properties: RowProperties,
        on_toggle: ActionConfig | None = None,
        context: RowContext | None = None,
    ) -> None:
        super().__init__(properties, on_toggle, context)
        self.save_as_int = bool(properties.get("save_as_int", False))
        self.key_inverse = bool(properties.get("key_inverse", False))
        self.is_active = False

        icon_conf = properties.get("icon", DEFAULT_ICON)
        box = self._build_content(
            _resolve_static_icon_name(icon_conf), str(properties.get("title", "Toggle"))
        )
        self.status_lbl = Gtk.Label(label=STATE_OFF, css_classes=["hero-subtitle"])
        box.append(self.status_lbl)
        self.set_child(box)

        # Load initial state
        if key := properties.get("key"):
            # FIXED: is_inversed is now a keyword argument in utility.load_setting
            val = utility.load_setting(
                str(key).strip(), 
                default=False, 
                is_inversed=self.key_inverse
            )
            if isinstance(val, bool):
                self._set_visual(val)

        self.connect("clicked", self._on_clicked)
        self._start_state_monitor()

        # Start dynamic icon updates if configured
        if _is_dynamic_icon(icon_conf) and isinstance(icon_conf, dict):
            self._start_icon_update_loop(icon_conf)

    def _apply_state_update(self, new_state: bool) -> int:
        """Apply polled state to the toggle card."""
        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE
        if new_state != self.is_active:
            self._set_visual(new_state)
        return GLib.SOURCE_REMOVE

    def _perform_cleanup(self) -> None:
        """Extended cleanup for toggle card."""
        super()._perform_cleanup()
        self._cleanup_monitor_source()
        self._cleanup_icon_source()

    def _set_visual(self, state: bool) -> None:
        """Update the visual state of the card."""
        self.is_active = state
        self.status_lbl.set_label(STATE_ON if state else STATE_OFF)
        if state:
            self.add_css_class("toggle-active")
        else:
            self.remove_css_class("toggle-active")

    def _on_clicked(self, button: Gtk.Button) -> None:
        """Handle card click - toggle state."""
        new_state = not self.is_active
        self._set_visual(new_state)

        if isinstance(self.on_action, dict):
            action_key = "enabled" if new_state else "disabled"
            if act := self.on_action.get(action_key):
                if isinstance(act, dict) and (cmd := act.get("command")):
                    utility.execute_command(
                        str(cmd).strip(), "Toggle", bool(act.get("terminal", False))
                    )

        if key := self.properties.get("key"):
            utility.save_setting(
                str(key).strip(), new_state ^ self.key_inverse, self.save_as_int
            )
