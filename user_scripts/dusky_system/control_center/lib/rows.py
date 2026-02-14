"""
Row widget definitions for the Dusky Control Center.

Optimized for:
- Stability: Thread Guards and cancellation tracking prevent race conditions.
- Efficiency: Gio.Subprocess async I/O eliminates thread pool overhead for shell commands.
- Type Safety: Strict TypedDict definitions and runtime-checkable Protocols.

GTK4/Libadwaita compatible with proper lifecycle management via `do_unroot`.
"""
from __future__ import annotations

import atexit
import logging
import shlex
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Final,
    NotRequired,
    Protocol,
    TypeAlias,
    TypedDict,
    runtime_checkable,
)
from contextlib import suppress, contextmanager

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango

import lib.utility as utility

if TYPE_CHECKING:
    from collections.abc import Mapping

log = logging.getLogger(__name__)


# =============================================================================
# CONSTANTS
# =============================================================================
DEFAULT_ICON: Final[str] = "utilities-terminal-symbolic"
DEFAULT_INTERVAL_SECONDS: Final[int] = 5
MONITOR_INTERVAL_SECONDS: Final[int] = 2
MIN_STEP_VALUE: Final[float] = 1e-9
SLIDER_DEBOUNCE_MS: Final[int] = 150
SUBPROCESS_TIMEOUT_SHORT: Final[int] = 2
SUBPROCESS_TIMEOUT_LONG: Final[int] = 5
ICON_PIXEL_SIZE: Final[int] = 28  # Aggressively reduced for compact cards
LABEL_MAX_WIDTH_CHARS: Final[int] = 16
EXECUTOR_MAX_WORKERS: Final[int] = 4

LABEL_PLACEHOLDER: Final[str] = "..."
LABEL_NA: Final[str] = "N/A"
LABEL_TIMEOUT: Final[str] = "Timeout"
LABEL_ERROR: Final[str] = "Error"
STATE_ON: Final[str] = "On"
STATE_OFF: Final[str] = "Off"

TRUE_VALUES: Final[frozenset[str]] = frozenset(
    {"enabled", "yes", "true", "1", "on", "active", "set", "running", "open", "high"}
)


# =============================================================================
# LAZY THREAD POOL (Singleton for File I/O & Legacy Tasks)
# =============================================================================
class _ExecutorManager:
    """
    Manages a singleton ThreadPoolExecutor with lazy initialization.
    Used for blocking file I/O tasks that cannot use Gio.Subprocess.
    """

    __slots__ = ("_executor", "_lock", "_is_shutdown")
    _instance: _ExecutorManager | None = None

    def __new__(cls) -> _ExecutorManager:
        if cls._instance is None:
            instance = super().__new__(cls)
            instance._executor = None
            instance._lock = threading.Lock()
            instance._is_shutdown = False
            atexit.register(instance.shutdown)
            cls._instance = instance
        return cls._instance

    def get(self) -> ThreadPoolExecutor:
        """Get or lazily create the thread pool executor."""
        # Double-checked locking pattern
        if self._executor is None or self._is_shutdown:
            with self._lock:
                if self._executor is None or self._is_shutdown:
                    self._is_shutdown = False
                    self._executor = ThreadPoolExecutor(
                        max_workers=EXECUTOR_MAX_WORKERS,
                        thread_name_prefix="dusky-io-",
                    )
        return self._executor

    def shutdown(self) -> None:
        """Shut down the executor, cancelling pending futures."""
        with self._lock:
            if self._executor is not None and not self._is_shutdown:
                log.debug("Shutting down row widget thread pool.")
                self._is_shutdown = True
                self._executor.shutdown(wait=False, cancel_futures=True)
                self._executor = None


def _get_executor() -> ThreadPoolExecutor:
    """Module-level accessor for the singleton executor."""
    return _ExecutorManager().get()


# =============================================================================
# TYPE DEFINITIONS
# =============================================================================
class IconConfigExec(TypedDict):
    type: str  # Literal["exec"]
    command: str
    interval: int
    name: NotRequired[str]


class IconConfigFile(TypedDict):
    type: str  # Literal["file"]
    path: str


class IconConfigStatic(TypedDict):
    name: str


IconConfig: TypeAlias = str | IconConfigExec | IconConfigFile | IconConfigStatic


class ActionExec(TypedDict, total=False):
    type: str  # Literal["exec"]
    command: str
    terminal: bool


class ActionRedirect(TypedDict):
    type: str  # Literal["redirect"]
    page: str


class ActionToggle(TypedDict, total=False):
    enabled: ActionExec
    disabled: ActionExec


ActionConfig: TypeAlias = ActionExec | ActionRedirect | ActionToggle | dict[str, object]


class ValueConfigExec(TypedDict):
    type: str  # Literal["exec"]
    command: str


class ValueConfigStatic(TypedDict):
    type: str  # Literal["static"]
    text: str


class ValueConfigFile(TypedDict):
    type: str  # Literal["file"]
    path: str


class ValueConfigSystem(TypedDict):
    type: str  # Literal["system"]
    key: str


ValueConfig: TypeAlias = (
    str | ValueConfigExec | ValueConfigStatic | ValueConfigFile | ValueConfigSystem
)


class RowProperties(TypedDict, total=False):
    title: str
    description: str
    icon: IconConfig
    style: str
    button_text: str
    button_text_file: str
    button_text_map: dict[str, str]
    style_map: dict[str, str]
    interval: int
    key: str
    key_inverse: bool
    save_as_int: bool
    state_command: str
    value_command: str
    min: float
    max: float
    step: float
    default: float
    debounce: bool
    options: list[str]
    options_command: str
    placeholder: str
    badge_file: str
    buttons: list[dict[str, Any]]


class RowContext(TypedDict, total=False):
    stack: Adw.ViewStack | None
    config: dict[str, object]
    sidebar: Gtk.ListBox | None
    toast_overlay: Adw.ToastOverlay | None
    nav_view: Adw.NavigationView | None
    builder_func: Callable[..., Adw.NavigationPage] | None
    path: NotRequired[list[str]]


@dataclass(slots=True)
class WidgetState:
    """
    Thread-safe state container for widget lifecycle and async operation guards.
    Tracks Gio.Cancellable handles for in-flight async operations.
    """

    lock: threading.Lock = field(default_factory=threading.Lock)
    is_destroyed: bool = False
    
    # GLib source IDs for periodic polling loops
    icon_source_id: int = 0
    monitor_source_id: int = 0
    update_source_id: int = 0
    debounce_source_id: int = 0
    
    # Gio.Cancellable handles for in-flight async operations (typed as Any for GI compatibility)
    icon_cancellable: Any = None
    monitor_cancellable: Any = None
    value_cancellable: Any = None

    # Guards to prevent scheduling overlapping operations
    is_icon_updating: bool = False
    is_monitoring: bool = False
    is_value_updating: bool = False

    def mark_destroyed_and_get_sources(self) -> tuple[int, int, int, int]:
        """Atomically marks destroyed, cancels async ops, and returns source IDs."""
        with self.lock:
            self.is_destroyed = True
            
            # Cancel all in-flight Gio.Subprocess operations
            for cancellable in (
                self.icon_cancellable,
                self.monitor_cancellable,
                self.value_cancellable,
            ):
                if cancellable is not None:
                    with suppress(Exception):
                        cancellable.cancel()
            
            self.icon_cancellable = None
            self.monitor_cancellable = None
            self.value_cancellable = None
            
            sources = (
                self.icon_source_id,
                self.monitor_source_id,
                self.update_source_id,
                self.debounce_source_id,
            )
            # Clear them to prevent accidental reuse
            self.icon_source_id = 0
            self.monitor_source_id = 0
            self.update_source_id = 0
            self.debounce_source_id = 0
            return sources


# =============================================================================
# PROTOCOLS FOR MIXINS (Runtime Checkable)
# =============================================================================
@runtime_checkable
class DynamicIconHost(Protocol):
    _state: WidgetState
    icon_widget: Gtk.Image


@runtime_checkable
class StateMonitorHost(Protocol):
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
            pass
    return default


def _safe_float(value: object, default: float) -> float:
    """Safely convert a value to float, returning default on failure."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            pass
    return default


def _is_dynamic_icon(icon_config: object) -> bool:
    """Check if an icon configuration requires periodic updates."""
    if not isinstance(icon_config, dict):
        return False
    return (
        icon_config.get("type") == "exec"
        and _safe_int(icon_config.get("interval"), 0) > 0
        and bool(icon_config.get("command", ""))
    )


def _perform_redirect(
    page_id: str,
    config: Mapping[str, object],
    sidebar: Gtk.ListBox | None,
) -> None:
    """Redirect navigation by selecting a sidebar row."""
    if not page_id or sidebar is None:
        return
    pages = config.get("pages")
    if not isinstance(pages, list):
        return
    for idx, page in enumerate(pages):
        if isinstance(page, dict) and page.get("id") == page_id:
            if row := sidebar.get_row_at_index(idx):
                sidebar.select_row(row)
            return


@lru_cache(maxsize=128)
def _expand_path(path: str) -> Path:
    """Expand user path with caching for repeated accesses."""
    return Path(path).expanduser()


def _resolve_static_icon_name(icon_config: object) -> str:
    """Resolve an icon configuration to a static icon name string."""
    if isinstance(icon_config, str):
        return icon_config or DEFAULT_ICON
    if isinstance(icon_config, dict):
        return str(icon_config.get("name", DEFAULT_ICON))
    return DEFAULT_ICON


def _safe_source_remove(source_id: int) -> None:
    """Safely remove a GLib timeout/idle source."""
    if source_id > 0:
        with suppress(Exception):
            GLib.source_remove(source_id)


def _batch_source_remove(*source_ids: int) -> None:
    """Remove multiple GLib sources at once."""
    for sid in source_ids:
        _safe_source_remove(sid)


def _submit_task_safe(func: Callable[[], None], state: WidgetState) -> bool:
    """
    Submit a task to the executor, handling shutdown gracefully.
    Used for file I/O operations.
    Returns True if submitted, False otherwise.
    """
    try:
        _get_executor().submit(func)
        return True
    except RuntimeError:
        # Executor is shut down (app is exiting)
        return False
    except Exception as e:
        log.error("Failed to submit task: %s", e)
        return False


# =============================================================================
# ASYNC SUBPROCESS INFRASTRUCTURE (Gio-based, Zero Thread Overhead)
# =============================================================================
def _run_shell_async(
    command: str,
    timeout_seconds: int,
    on_complete: Callable[[str | None], None],
) -> Gio.Cancellable | None:
    """
    Execute a shell command asynchronously using Gio.Subprocess.
    Integrates directly with the GLib main loop without spawning Python threads.
    
    Args:
        command: Shell command to execute (run via /bin/sh -c)
        timeout_seconds: Timeout in seconds (0 for no timeout)
        on_complete: Callback with stdout on success, None on failure/timeout.
                     Always called on the main thread.
        
    Returns:
        Gio.Cancellable for operation cancellation, or None if spawn failed
    """
    cancellable = Gio.Cancellable()
    timeout_source_id: int = 0
    
    def on_timeout() -> bool:
        """Handle command timeout by cancelling the operation."""
        nonlocal timeout_source_id
        timeout_source_id = 0  
        if not cancellable.is_cancelled():
            cancellable.cancel()
            # We don't need to force_exit, cancel handles the stream
        return GLib.SOURCE_REMOVE

    def on_communicate_finish(
        proc: Gio.Subprocess, result: Gio.AsyncResult
    ) -> None:
        """Handle subprocess completion."""
        nonlocal timeout_source_id
        if timeout_source_id > 0:
            _safe_source_remove(timeout_source_id)
            timeout_source_id = 0
        
        try:
            success, stdout_data, _ = proc.communicate_utf8_finish(result)
            if success and proc.get_successful() and stdout_data:
                on_complete(stdout_data.strip())
            else:
                on_complete(None)
        except GLib.Error:
            # Cancelled or other GLib error
            on_complete(None)

    try:
        launcher = Gio.SubprocessLauncher.new(
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE
        )
        proc = launcher.spawnv(["/bin/sh", "-c", command])
    except GLib.Error as e:
        log.debug("Failed to spawn command '%.30s...': %s", command, e.message)
        GLib.idle_add(lambda: (on_complete(None), GLib.SOURCE_REMOVE)[1])
        return None

    if timeout_seconds > 0:
        timeout_source_id = GLib.timeout_add_seconds(timeout_seconds, on_timeout)

    proc.communicate_utf8_async(None, cancellable, on_communicate_finish)
    return cancellable


# =============================================================================
# MIXIN: DYNAMIC ICON UPDATES (Optimized with Gio)
# =============================================================================
class DynamicIconMixin:
    """
    Mixin providing dynamic icon updates via periodic command execution.
    Uses Gio.Subprocess for async I/O instead of ThreadPoolExecutor.
    """

    _state: WidgetState
    icon_widget: Gtk.Image

    def _start_icon_update_loop(self, icon_config: dict[str, object]) -> None:
        """Initialize the icon polling loop."""
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

    def _icon_update_tick(self, command: str) -> bool:
        """GLib timeout callback for periodic icon updates."""
        if isinstance(self, Gtk.Widget) and not self.get_mapped():
            return GLib.SOURCE_CONTINUE

        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE
            if self._state.is_icon_updating:
                return GLib.SOURCE_CONTINUE
            self._state.is_icon_updating = True

        self._schedule_icon_fetch(command)
        return GLib.SOURCE_CONTINUE

    def _schedule_icon_fetch(self, command: str) -> None:
        """Execute icon command asynchronously via Gio.Subprocess."""
        with self._state.lock:
            if self._state.is_destroyed:
                self._state.is_icon_updating = False
                return
            
            # Cancel prev if exists
            if self._state.icon_cancellable:
                with suppress(Exception):
                    self._state.icon_cancellable.cancel()
                self._state.icon_cancellable = None

        def on_result(output: str | None) -> None:
            with self._state.lock:
                self._state.is_icon_updating = False
                if self._state.icon_cancellable is cancellable:
                    self._state.icon_cancellable = None
                if self._state.is_destroyed:
                    return
            
            if output:
                self._apply_icon_update(output)

        cancellable = _run_shell_async(command, SUBPROCESS_TIMEOUT_SHORT, on_result)
        
        with self._state.lock:
            if not self._state.is_destroyed and cancellable:
                self._state.icon_cancellable = cancellable
            elif cancellable:
                cancellable.cancel()

    def _apply_icon_update(self, new_icon: str) -> bool:
        """Apply icon update on main thread."""
        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE

        if self.icon_widget.get_icon_name() != new_icon:
            self.icon_widget.set_from_icon_name(new_icon)
        return GLib.SOURCE_REMOVE


# =============================================================================
# MIXIN: STATE MONITORING (Hybrid: Gio for commands, Executor for settings)
# =============================================================================
class StateMonitorMixin:
    """
    Mixin providing external state monitoring via periodic polling.
    Optimized to use Gio.Subprocess for commands, but keeps ThreadPool for file I/O.
    """

    _state: WidgetState
    properties: RowProperties
    key_inverse: bool

    def _start_state_monitor(self) -> None:
        """Initialize state monitoring loop if configured."""
        has_key = bool(self.properties.get("key", ""))
        has_state_cmd = bool(self.properties.get("state_command", ""))
        if not has_key and not has_state_cmd:
            return

        interval = _safe_int(
            self.properties.get("interval"), MONITOR_INTERVAL_SECONDS
        )
        if interval <= 0:
            return

        with self._state.lock:
            if self._state.is_destroyed:
                return
            self._state.monitor_source_id = GLib.timeout_add_seconds(
                interval, self._monitor_state_tick
            )

    def _monitor_state_tick(self) -> bool:
        """GLib timeout callback for periodic state checks."""
        if isinstance(self, Gtk.Widget) and not self.get_mapped():
            return GLib.SOURCE_CONTINUE

        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE
            if self._state.is_monitoring:
                return GLib.SOURCE_CONTINUE
            self._state.is_monitoring = True

        state_cmd = self.properties.get("state_command", "")
        if isinstance(state_cmd, str) and state_cmd.strip():
            # Use Async Gio Path
            self._check_state_via_command(state_cmd.strip())
        else:
            # Use ThreadPool File I/O Path
            if not _submit_task_safe(self._check_state_via_settings, self._state):
                with self._state.lock:
                    self._state.is_monitoring = False

        return GLib.SOURCE_CONTINUE

    def _check_state_via_command(self, command: str) -> None:
        """Check state via shell command using Gio.Subprocess."""
        with self._state.lock:
            if self._state.is_destroyed:
                self._state.is_monitoring = False
                return
            if self._state.monitor_cancellable:
                with suppress(Exception):
                    self._state.monitor_cancellable.cancel()
                self._state.monitor_cancellable = None

        def on_result(output: str | None) -> None:
            with self._state.lock:
                self._state.is_monitoring = False
                if self._state.monitor_cancellable is cancellable:
                    self._state.monitor_cancellable = None
                if self._state.is_destroyed:
                    return
            
            if output is not None:
                new_state = output.lower() in TRUE_VALUES
                self._apply_state_update(new_state)

        cancellable = _run_shell_async(command, SUBPROCESS_TIMEOUT_SHORT, on_result)
        
        with self._state.lock:
            if not self._state.is_destroyed and cancellable:
                self._state.monitor_cancellable = cancellable
            elif cancellable:
                cancellable.cancel()

    def _check_state_via_settings(self) -> None:
        """Check state via settings file (uses thread executor for file I/O)."""
        new_state: bool | None = None
        try:
            with self._state.lock:
                if self._state.is_destroyed:
                    return

            key = self.properties.get("key", "")
            if isinstance(key, str) and key.strip():
                val = utility.load_setting(
                    key.strip(), default=False, is_inversed=self.key_inverse
                )
                if isinstance(val, bool):
                    new_state = val
        except Exception:
            pass
        finally:
            with self._state.lock:
                self._state.is_monitoring = False

        if new_state is not None:
            GLib.idle_add(self._apply_state_update, new_state)

    def _apply_state_update(self, new_state: bool) -> bool:
        """Apply state update on main thread. Must be overridden."""
        raise NotImplementedError


# =============================================================================
# MIXIN: SLIDER MONITORING (Optimized with Gio)
# =============================================================================
class SliderMonitorMixin:
    """
    Mixin providing numeric value monitoring via periodic polling.
    Optimized to use Gio.Subprocess for async I/O.
    """

    _state: WidgetState
    properties: RowProperties

    def _start_value_monitor(self) -> None:
        """Initialize value monitoring loop if configured."""
        if not self.properties.get("value_command"):
            return

        interval = _safe_int(
            self.properties.get("interval"), MONITOR_INTERVAL_SECONDS
        )
        if interval <= 0:
            return

        # Perform initial fetch immediately
        self._check_value_tick()

        with self._state.lock:
            if self._state.is_destroyed:
                return
            self._state.monitor_source_id = GLib.timeout_add_seconds(
                interval, self._check_value_tick
            )

    def _check_value_tick(self) -> bool:
        """GLib timeout callback for periodic value checks."""
        if isinstance(self, Gtk.Widget) and not self.get_mapped():
            return GLib.SOURCE_CONTINUE

        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE
            if self._state.is_monitoring:
                return GLib.SOURCE_CONTINUE
            self._state.is_monitoring = True

        cmd = self.properties.get("value_command", "")
        if not isinstance(cmd, str) or not cmd.strip():
            with self._state.lock:
                self._state.is_monitoring = False
            return GLib.SOURCE_CONTINUE

        self._fetch_value_async(cmd.strip())
        return GLib.SOURCE_CONTINUE

    def _fetch_value_async(self, command: str) -> None:
        """Fetch numeric value via Gio.Subprocess."""
        with self._state.lock:
            if self._state.is_destroyed:
                self._state.is_monitoring = False
                return
            if self._state.value_cancellable:
                with suppress(Exception):
                    self._state.value_cancellable.cancel()
                self._state.value_cancellable = None

        def on_result(output: str | None) -> None:
            with self._state.lock:
                self._state.is_monitoring = False
                if self._state.value_cancellable is cancellable:
                    self._state.value_cancellable = None
                if self._state.is_destroyed:
                    return
            
            if output:
                try:
                    new_value = float(output)
                    self._apply_value_update(new_value)
                except ValueError:
                    pass

        cancellable = _run_shell_async(command, SUBPROCESS_TIMEOUT_SHORT, on_result)
        
        with self._state.lock:
            if not self._state.is_destroyed and cancellable:
                self._state.value_cancellable = cancellable
            elif cancellable:
                cancellable.cancel()

    def _apply_value_update(self, new_value: float) -> bool:
        """Apply value update on main thread. Must be overridden."""
        raise NotImplementedError


# =============================================================================
# BASE ROW CLASS
# =============================================================================
class BaseActionRow(DynamicIconMixin, Adw.ActionRow):
    """Base class for all action row widgets with common setup and cleanup."""

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
        self.config: dict[str, object] = self.context.get("config") or {}
        self.sidebar: Gtk.ListBox | None = self.context.get("sidebar")
        self.toast_overlay: Adw.ToastOverlay | None = self.context.get("toast_overlay")
        self.nav_view: Adw.NavigationView | None = self.context.get("nav_view")
        self.builder_func = self.context.get("builder_func")

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
        """Create the prefix icon widget based on configuration."""
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
        """GTK4 lifecycle hook: clean up when widget is removed from tree."""
        self._perform_cleanup()
        Adw.ActionRow.do_unroot(self)

    def _perform_cleanup(self) -> None:
        """Centralized cleanup for all timers and background tasks."""
        # Atomic retrieval of source IDs guarantees we don't miss any
        sources = self._state.mark_destroyed_and_get_sources()
        _batch_source_remove(*sources)


# =============================================================================
# ROW IMPLEMENTATIONS
# =============================================================================
class ButtonRow(BaseActionRow):
    """Action row with a button suffix for triggering actions."""

    __gtype_name__ = "DuskyButtonRow"

    def __init__(
        self,
        properties: RowProperties,
        on_press: ActionConfig | None = None,
        context: RowContext | None = None,
    ) -> None:
        super().__init__(properties, on_press, context)

        multi_buttons = properties.get("buttons")
        
        if multi_buttons and isinstance(multi_buttons, list):
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
            box.add_css_class("linked")
            box.set_valign(Gtk.Align.CENTER)
            
            for btn_cfg in multi_buttons:
                b = Gtk.Button()
                if icon_name := btn_cfg.get("icon"):
                    b.set_child(Gtk.Image.new_from_icon_name(icon_name))
                    b.set_tooltip_text(str(btn_cfg.get("button_text", "Action")))
                else:
                    b.set_label(str(btn_cfg.get("button_text", "Action")))
                
                b.connect("clicked", self._on_multi_clicked, btn_cfg)
                
                if s := btn_cfg.get("style"):
                    if s == "suggested": b.add_css_class("suggested-action")
                    elif s == "destructive": b.add_css_class("destructive-action")
                
                box.append(b)
            
            self.add_suffix(box)
        else:
            self.btn = Gtk.Button(label=str(properties.get("button_text", "Run")))
            self.btn.set_valign(Gtk.Align.CENTER)
            self.btn.add_css_class("run-btn")
            
            self.base_style = str(properties.get("style", "default")).lower()
            self._apply_base_style(self.base_style)
            
            self.text_file = properties.get("button_text_file")
            if self.text_file:
                self.text_map = properties.get("button_text_map", {})
                self.style_map = properties.get("style_map", {})
                self._start_dynamic_poll()

            self.btn.connect("clicked", self._on_button_clicked)
            self.add_suffix(self.btn)
            self.set_activatable_widget(self.btn)

    def _apply_base_style(self, style: str) -> None:
        for s in ["suggested-action", "destructive-action", "default-action"]:
            self.btn.remove_css_class(s)
        match style:
            case "destructive": self.btn.add_css_class("destructive-action")
            case "suggested": self.btn.add_css_class("suggested-action")
            case _: self.btn.add_css_class("default-action")

    def _start_dynamic_poll(self) -> None:
        self._update_dynamic_state()
        with self._state.lock:
            if not self._state.is_destroyed:
                self._state.update_source_id = GLib.timeout_add_seconds(2, self._update_dynamic_state)

    def _update_dynamic_state(self) -> bool:
        try:
            path = Path(self.text_file).expanduser()
            if not path.exists(): return True
            val = path.read_text().strip()
            new_label = self.text_map.get(val, self.text_map.get("default", self.btn.get_label()))
            if self.btn.get_label() != new_label: self.btn.set_label(new_label)
            new_style = self.style_map.get(val, self.style_map.get("default", self.base_style))
            self._apply_base_style(new_style)
        except Exception: pass
        return True

    def _on_button_clicked(self, _button: Gtk.Button) -> None:
        self._trigger_action(self.on_action)

    def _on_multi_clicked(self, _b: Gtk.Button, cfg: dict[str, Any]) -> None:
        if act := cfg.get("on_press"):
            self._trigger_action(act)

    def _trigger_action(self, act: Any) -> None:
        if not isinstance(act, dict): return
        t = act.get("type")
        if t == "exec":
            cmd = act.get("command", "")
            if isinstance(cmd, str) and cmd.strip():
                title = str(self.properties.get("title", "Command"))
                term = bool(act.get("terminal", False))
                success = utility.execute_command(cmd.strip(), title, term)
                msg = f"{'▶ Launched' if success else '✖ Failed'}: {title}"
                utility.toast(
                    self.toast_overlay, msg, 2 if success else 4
                )
        elif t == "redirect":
            if pid := act.get("page"):
                _perform_redirect(str(pid), self.config, self.sidebar)


class ToggleRow(StateMonitorMixin, BaseActionRow):
    """Action row with a switch suffix for toggling state."""

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

        self._programmatic_update_event = threading.Event()

        self.toggle_switch = Gtk.Switch()
        self.toggle_switch.set_valign(Gtk.Align.CENTER)

        if key := properties.get("key"):
            val = utility.load_setting(
                str(key).strip(), default=False, is_inversed=self.key_inverse
            )
            if isinstance(val, bool):
                self.toggle_switch.set_active(val)

        self.toggle_switch.connect("state-set", self._on_toggle_changed)
        self.add_suffix(self.toggle_switch)
        self.set_activatable_widget(self.toggle_switch)
        self._start_state_monitor()

    def _apply_state_update(self, new_state: bool) -> bool:
        """Apply monitored state to the switch."""
        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE

        if new_state != self.toggle_switch.get_active():
            self._programmatic_update_event.set()
            try:
                self.toggle_switch.set_active(new_state)
            finally:
                self._programmatic_update_event.clear()

        return GLib.SOURCE_REMOVE

    def _on_toggle_changed(self, _switch: Gtk.Switch, state: bool) -> bool:
        """Handle user-initiated toggle changes."""
        if self._programmatic_update_event.is_set():
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
                str(key).strip(), state ^ self.key_inverse, as_int=self.save_as_int
            )

        return False


class LabelRow(BaseActionRow):
    """Action row displaying a dynamically-loaded label value."""

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

    def _on_timeout(self) -> bool:
        """GLib timeout callback for periodic value refresh."""
        if isinstance(self, Gtk.Widget) and not self.get_mapped():
            return GLib.SOURCE_CONTINUE

        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE

        self._trigger_update()
        return GLib.SOURCE_CONTINUE

    def _trigger_update(self) -> None:
        """Initiate a background value fetch."""
        with self._state.lock:
            if self._state.is_value_updating or self._state.is_destroyed:
                return
            self._state.is_value_updating = True

        if not _submit_task_safe(self._load_value_async, self._state):
            with self._state.lock:
                self._state.is_value_updating = False

    def _load_value_async(self) -> None:
        """Fetch the value text in a background thread."""
        result = LABEL_NA
        try:
            with self._state.lock:
                if self._state.is_destroyed:
                    return
            result = self._get_value_text(self.value_config)
        finally:
            with self._state.lock:
                self._state.is_value_updating = False

        GLib.idle_add(self._update_label, result)

    def _update_label(self, text: str) -> bool:
        """Update the label text on the main thread."""
        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE

        if self.value_label.get_label() != text:
            self.value_label.set_label(text)
            self.value_label.remove_css_class("dim-label")

        return GLib.SOURCE_REMOVE

    def _get_value_text(self, val: ValueConfig) -> str:
        """Resolve a ValueConfig to its string representation."""
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
        """Execute a command and return its stdout."""
        cmd = cmd.strip()
        if not cmd:
            return LABEL_NA

        # Optimization: bypass subprocess for simple `cat` commands
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
        """Read and return the contents of a file."""
        if not path.strip():
            return LABEL_NA
        try:
            return _expand_path(path.strip()).read_text(encoding="utf-8").strip()
        except OSError:
            return LABEL_NA


class SliderRow(SliderMonitorMixin, BaseActionRow):
    """Action row with a slider suffix for continuous value adjustment."""

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
        self.debounce_enabled = bool(properties.get("debounce", True))

        self._slider_lock = threading.RLock()
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
        self.slider.set_hexpand(False)
        self.slider.set_draw_value(False)
        self.slider.set_size_request(250, -1)
        self.slider.connect("value-changed", self._on_value_changed)
        self.add_suffix(self.slider)

        self._start_value_monitor()

    def _apply_value_update(self, new_value: float) -> bool:
        """Apply monitored value to the slider without triggering action."""
        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE

        with self._slider_lock:
            current = self.slider.get_value()
            if abs(current - new_value) < self.step_val:
                return GLib.SOURCE_REMOVE

            self._slider_changing = True
            try:
                safe_val = max(self.min_val, min(new_value, self.max_val))
                self.slider.set_value(safe_val)
                self._last_snapped = safe_val
            finally:
                self._slider_changing = False

        return GLib.SOURCE_REMOVE

    def _on_value_changed(self, scale: Gtk.Scale) -> None:
        """Handle slider value changes with snapping and debouncing."""
        with self._slider_lock:
            if self._slider_changing:
                return

            val = scale.get_value()
            snapped = round(val / self.step_val) * self.step_val
            snapped = max(self.min_val, min(snapped, self.max_val))

            if (
                self._last_snapped is not None
                and abs(snapped - self._last_snapped) < MIN_STEP_VALUE
            ):
                return

            self._last_snapped = snapped

            if abs(snapped - val) > MIN_STEP_VALUE:
                self._slider_changing = True
                try:
                    self.slider.set_value(snapped)
                finally:
                    self._slider_changing = False

            self._pending_value = snapped

        if not self.debounce_enabled:
            self._execute_debounced_action()
            return

        with self._state.lock:
            if self._state.is_destroyed:
                return
            old_id = self._state.debounce_source_id
            self._state.debounce_source_id = GLib.timeout_add(
                SLIDER_DEBOUNCE_MS, self._execute_debounced_action
            )

        _safe_source_remove(old_id)

    def _execute_debounced_action(self) -> bool:
        """Execute the slider command after the debounce period."""
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

                is_terminal = bool(self.on_action.get("terminal", False))
                if is_terminal:
                    utility.execute_command(final_cmd, "Slider", True)
                else:
                    subprocess.Popen(
                        final_cmd,
                        shell=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )

        return GLib.SOURCE_REMOVE


class SelectionRow(DynamicIconMixin, Adw.ComboRow):
    """Row with a dropdown selection menu and state monitoring."""

    __gtype_name__ = "DuskySelectionRow"

    def __init__(
        self,
        properties: RowProperties,
        on_change: ActionConfig | None = None,
        context: RowContext | None = None,
    ) -> None:
        super().__init__()
        self.add_css_class("action-row")

        self._state = WidgetState()
        self.properties = properties
        self.on_action: ActionConfig = on_change or {}
        self.context: RowContext = context or {}
        self.toast_overlay: Adw.ToastOverlay | None = self.context.get("toast_overlay")
        
        self._programmatic_update = False

        title = str(properties.get("title", "Unnamed"))
        self.set_title(GLib.markup_escape_text(title))
        if sub := properties.get("description", ""):
            self.set_subtitle(GLib.markup_escape_text(str(sub)))

        icon_config = properties.get("icon", DEFAULT_ICON)
        self.icon_widget = self._create_icon_widget(icon_config)
        self.add_prefix(self.icon_widget)

        self.options_list: list[str] = []
        raw_options = properties.get("options", [])
        if isinstance(raw_options, list) and raw_options:
            self.options_list = [str(x) for x in raw_options]
            self.set_model(Gtk.StringList.new(self.options_list))

        self.connect("notify::selected", self._on_selected)
        self.connect("map", self._on_map)

        if _is_dynamic_icon(icon_config) and isinstance(icon_config, dict):
            self._start_icon_update_loop(icon_config)

        if properties.get("options_command"):
            _submit_task_safe(self._fetch_options_async, self._state)

        if properties.get("value_command"):
            self._start_selection_monitor()

    def _create_icon_widget(self, icon: object) -> Gtk.Image:
        """Create the prefix icon widget based on configuration."""
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

    @contextmanager
    def _suppress_change_signal(self):
        """Context manager to safely toggle the programmatic update flag."""
        self._programmatic_update = True
        try:
            yield
        finally:
            self._programmatic_update = False

    def _fetch_options_async(self) -> None:
        """Fetch options list from a command in the background."""
        cmd = self.properties.get("options_command", "")
        if not cmd:
            return

        try:
            res = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=SUBPROCESS_TIMEOUT_LONG
            )
            if res.returncode == 0:
                lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
                if lines:
                    GLib.idle_add(self._update_options_ui, lines)
        except Exception as e:
            log.error(f"Options fetch failed: {e}")

    def _update_options_ui(self, new_options: list[str]) -> bool:
        """Update the dropdown model with new options."""
        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE

        if new_options != self.options_list:
            self.options_list = new_options
            with self._suppress_change_signal():
                self.set_model(Gtk.StringList.new(self.options_list))
                _submit_task_safe(self._fetch_selection_async, self._state)

        return GLib.SOURCE_REMOVE

    def _start_selection_monitor(self) -> None:
        """Initialize the selection polling loop."""
        interval = _safe_int(self.properties.get("interval"), DEFAULT_INTERVAL_SECONDS)

        with self._state.lock:
            if self._state.is_destroyed:
                return
            self._state.monitor_source_id = GLib.timeout_add_seconds(
                interval, self._check_selection_tick
            )

    def _on_map(self, _widget: Gtk.Widget) -> None:
        """Trigger an update whenever the widget becomes visible."""
        _submit_task_safe(self._fetch_selection_async, self._state)
        if self.properties.get("options_command"):
             _submit_task_safe(self._fetch_options_async, self._state)

    def _check_selection_tick(self) -> bool:
        """GLib timeout to schedule background fetch."""
        if not self.get_mapped():
            return GLib.SOURCE_CONTINUE

        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE

        _submit_task_safe(self._fetch_selection_async, self._state)
        return GLib.SOURCE_CONTINUE

    def _fetch_selection_async(self) -> None:
        """Fetch current status string in background."""
        cmd = self.properties.get("value_command", "")
        if not cmd:
            return
        
        try:
            res = subprocess.run(
                cmd, 
                shell=True, 
                capture_output=True, 
                text=True, 
                timeout=SUBPROCESS_TIMEOUT_SHORT
            )
            
            if res.returncode == 0:
                value = res.stdout.strip()
                if value:
                    GLib.idle_add(self._update_selection_ui, value)
        except Exception:
            pass

    def _update_selection_ui(self, value: str) -> bool:
        """Update the dropdown selection on main thread."""
        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE

        if value not in self.options_list:
            if self.properties.get("options_command"):
                 _submit_task_safe(self._fetch_options_async, self._state)
            return GLib.SOURCE_REMOVE

        idx = self.options_list.index(value)
        
        if self.get_selected() != idx:
            with self._suppress_change_signal():
                self.set_selected(idx)

        return GLib.SOURCE_REMOVE

    def _on_selected(self, _row: Adw.ComboRow, _param: GObject.ParamSpec) -> None:
        """Handle user selection."""
        if self._programmatic_update:
            return

        model = self.get_model()
        if not model:
            return

        idx = self.get_selected()
        if idx >= model.get_n_items(): 
            return

        item = model.get_string(idx)

        if isinstance(self.on_action, dict) and (cmd := self.on_action.get("command")):
            safe_value = shlex.quote(item)
            final_cmd = str(cmd).replace("{value}", safe_value)
            
            utility.execute_command(
                final_cmd,
                "Selection",
                bool(self.on_action.get("terminal", False)),
            )

    def do_unroot(self) -> None:
        sources = self._state.mark_destroyed_and_get_sources()
        _batch_source_remove(*sources)
        Adw.ComboRow.do_unroot(self)


class EntryRow(DynamicIconMixin, Adw.EntryRow):
    """Row with text input and an apply button."""

    __gtype_name__ = "DuskyEntryRow"

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
        self.toast_overlay: Adw.ToastOverlay | None = self.context.get("toast_overlay")

        title = str(properties.get("title", "Unnamed"))
        self.set_title(GLib.markup_escape_text(title))

        icon_config = properties.get("icon", DEFAULT_ICON)
        self.icon_widget = self._create_icon_widget(icon_config)
        self.add_prefix(self.icon_widget)

        self.set_show_apply_button(False)
        btn_text = str(properties.get("button_text", "Apply"))
        btn = Gtk.Button(label=btn_text)
        btn.add_css_class("suggested-action")
        btn.set_valign(Gtk.Align.CENTER)
        btn.connect("clicked", self._on_apply)
        self.add_suffix(btn)

        if _is_dynamic_icon(icon_config) and isinstance(icon_config, dict):
            self._start_icon_update_loop(icon_config)

    def _create_icon_widget(self, icon: object) -> Gtk.Image:
        """Create the prefix icon widget based on configuration."""
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

    def _on_apply(self, _btn: Gtk.Button) -> None:
        text = self.get_text()
        if not text:
            return

        if isinstance(self.on_action, dict) and (cmd := self.on_action.get("command")):
            final_cmd = str(cmd).replace("{value}", text)
            utility.execute_command(
                final_cmd,
                "Entry",
                bool(self.on_action.get("terminal", False)),
            )

    def do_unroot(self) -> None:
        sources = self._state.mark_destroyed_and_get_sources()
        _batch_source_remove(*sources)
        Adw.EntryRow.do_unroot(self)


class NavigationRow(BaseActionRow):
    """Action row that navigates to a subpage when activated."""

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

    def _on_activated(self, _row: Adw.ActionRow) -> None:
        """Handle row activation to push a subpage."""
        if self.nav_view and self.builder_func:
            title = str(self.properties.get("title", "Subpage"))
            
            current_path = self.context.get("path", [])
            new_path = list(current_path) + [title]
            
            new_ctx = self.context.copy()
            new_ctx["path"] = new_path
            
            self.nav_view.push(
                self.builder_func(title, self.layout_data, new_ctx)
            )


class ExpanderRow(DynamicIconMixin, Adw.ExpanderRow):
    """Expandable row that contains nested child rows."""

    __gtype_name__ = "DuskyExpanderRow"

    def __init__(
        self,
        properties: RowProperties,
        items: list[object] | None = None,
        context: RowContext | None = None,
    ) -> None:
        super().__init__()

        self._state = WidgetState()
        self.properties = properties
        self.items_data: list[object] = items or []
        self.context: RowContext = context or {}
        self.toast_overlay: Adw.ToastOverlay | None = self.context.get("toast_overlay")
        self.nav_view: Adw.NavigationView | None = self.context.get("nav_view")
        self.builder_func = self.context.get("builder_func")

        title = str(properties.get("title", "Expander"))
        self.set_title(GLib.markup_escape_text(title))
        if sub := properties.get("description", ""):
            self.set_subtitle(GLib.markup_escape_text(str(sub)))

        icon_config = properties.get("icon", DEFAULT_ICON)
        self.icon_widget = self._create_icon_widget(icon_config)
        self.add_prefix(self.icon_widget)

        self._build_child_rows()

        if _is_dynamic_icon(icon_config) and isinstance(icon_config, dict):
            self._start_icon_update_loop(icon_config)

    def _create_icon_widget(self, icon: object) -> Gtk.Image:
        """Create the prefix icon widget based on configuration."""
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

    def _build_child_rows(self) -> None:
        """Build and add child rows from items data."""
        for item in self.items_data:
            if not isinstance(item, dict):
                continue

            row = self._build_single_row(item)
            if row is not None:
                self.add_row(row)

    def _build_single_row(self, item: dict[str, object]) -> Adw.PreferencesRow | None:
        """Build a single child row from item configuration."""
        item_type = str(item.get("type", "")).lower()
        props = item.get("properties", {})
        if not isinstance(props, dict):
            props = {}

        try:
            match item_type:
                case "button":
                    return ButtonRow(props, item.get("on_press"), self.context)
                case "toggle":
                    return ToggleRow(props, item.get("on_toggle"), self.context)
                case "label":
                    return LabelRow(props, item.get("value"), self.context)
                case "slider":
                    return SliderRow(props, item.get("on_change"), self.context)
                case "selection":
                    return SelectionRow(props, item.get("on_change"), self.context)
                case "entry":
                    return EntryRow(props, item.get("on_action"), self.context)
                case "navigation":
                    return NavigationRow(props, item.get("layout"), self.context)
                case "expander":
                    return ExpanderRow(props, item.get("items"), self.context)
                case _:
                    log.warning(
                        "Unknown item type '%s' in expander, skipping", item_type
                    )
                    return None
        except Exception as e:
            log.error("Failed to build child row for type '%s': %s", item_type, e)
            return None

    def do_unroot(self) -> None:
        """GTK4 lifecycle hook: clean up when widget is removed from tree."""
        self._perform_cleanup()
        Adw.ExpanderRow.do_unroot(self)

    def _perform_cleanup(self) -> None:
        """Centralized cleanup for all timers and background tasks."""
        sources = self._state.mark_destroyed_and_get_sources()
        _batch_source_remove(*sources)


# =============================================================================
# GRID CARDS
# =============================================================================
class GridCardBase(Gtk.Button):
    """Base class for grid-style card widgets."""

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
        self._perform_cleanup()
        Gtk.Button.do_unroot(self)

    def _perform_cleanup(self) -> None:
        """Mark card as destroyed and clean up sources."""
        sources = self._state.mark_destroyed_and_get_sources()
        _batch_source_remove(*sources)

    def _build_content(self, icon: str, title: str) -> Gtk.Box:
        """Build the card's vertical box content."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
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
    """
    Grid card that executes an action when clicked.
    Supports 'badge_file' property to show a notification count.
    """

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
            _resolve_static_icon_name(icon_conf),
            str(properties.get("title", "Unnamed")),
        )
        self.set_child(box)
        self.connect("clicked", self._on_clicked)

        self.badge_label: Gtk.Label | None = None
        if badge_path := properties.get("badge_file"):
            self.set_child(None)
            
            overlay = Gtk.Overlay()
            overlay.set_child(box)
            
            self.badge_label = Gtk.Label(css_classes=["badge-label"])
            self.badge_label.set_halign(Gtk.Align.END)
            self.badge_label.set_valign(Gtk.Align.START)
            self.badge_label.set_margin_top(4)
            self.badge_label.set_margin_end(4)
            self.badge_label.set_visible(False)
            
            overlay.add_overlay(self.badge_label)
            self.set_child(overlay)
            
            self._start_badge_monitor(str(badge_path))

        if _is_dynamic_icon(icon_conf) and isinstance(icon_conf, dict):
            self._start_icon_update_loop(icon_conf)

    def _start_badge_monitor(self, path_str: str) -> None:
        """Start periodic check of the badge file."""
        self._check_badge_tick(path_str)
        
        with self._state.lock:
            if self._state.is_destroyed:
                return
            self._state.update_source_id = GLib.timeout_add_seconds(
                DEFAULT_INTERVAL_SECONDS, self._check_badge_tick, path_str
            )

    def _check_badge_tick(self, path_str: str) -> bool:
        """Timeout callback to schedule background file read."""
        if not self.get_mapped():
            return GLib.SOURCE_CONTINUE

        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE
        
        _submit_task_safe(lambda: self._fetch_badge_async(path_str), self._state)
        return GLib.SOURCE_CONTINUE

    def _fetch_badge_async(self, path_str: str) -> None:
        """Read file in background thread."""
        count_text: str | None = None
        try:
            path = _expand_path(path_str)
            if path.exists():
                content = path.read_text(encoding="utf-8").strip()
                if content.isdigit() and int(content) > 0:
                    count_text = content
        except Exception:
            pass
        
        GLib.idle_add(self._update_badge_ui, count_text)

    def _update_badge_ui(self, text: str | None) -> bool:
        """Update badge visibility on main thread."""
        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE
        
        if self.badge_label:
            if text:
                self.badge_label.set_label(text)
                self.badge_label.set_visible(True)
            else:
                self.badge_label.set_visible(False)
        
        return GLib.SOURCE_REMOVE

    def _on_clicked(self, _button: Gtk.Button) -> None:
        """Handle card click: execute or redirect."""
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
                        self.toast_overlay,
                        "▶ Launched" if success else "✖ Failed",
                    )
            case "redirect":
                if pid := self.on_action.get("page"):
                    _perform_redirect(
                        str(pid),
                        self.context.get("config") or {},
                        self.context.get("sidebar"),
                    )


class GridToggleCard(DynamicIconMixin, StateMonitorMixin, GridCardBase):
    """Grid card with toggle state (on/off)."""

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
            _resolve_static_icon_name(icon_conf),
            str(properties.get("title", "Toggle")),
        )

        self.status_lbl = Gtk.Label(label=STATE_OFF, css_classes=["hero-subtitle"])
        box.append(self.status_lbl)
        self.set_child(box)

        if key := properties.get("key"):
            val = utility.load_setting(
                str(key).strip(), default=False, is_inversed=self.key_inverse
            )
            if isinstance(val, bool):
                self._set_visual(val)

        self.connect("clicked", self._on_clicked)
        self._start_state_monitor()

        if _is_dynamic_icon(icon_conf) and isinstance(icon_conf, dict):
            self._start_icon_update_loop(icon_conf)

    def _apply_state_update(self, new_state: bool) -> bool:
        """Apply monitored state to the toggle card."""
        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE

        if new_state != self.is_active:
            self._set_visual(new_state)

        return GLib.SOURCE_REMOVE

    def _set_visual(self, state: bool) -> None:
        """Update the visual appearance to reflect toggle state."""
        self.is_active = state
        self.status_lbl.set_label(STATE_ON if state else STATE_OFF)
        if state:
            self.add_css_class("toggle-active")
        else:
            self.remove_css_class("toggle-active")

    def _on_clicked(self, _button: Gtk.Button) -> None:
        """Handle card click to toggle state."""
        new_state = not self.is_active
        self._set_visual(new_state)

        if isinstance(self.on_action, dict):
            action_key = "enabled" if new_state else "disabled"
            if act := self.on_action.get(action_key):
                if isinstance(act, dict) and (cmd := act.get("command")):
                    utility.execute_command(
                        str(cmd).strip(),
                        "Toggle",
                        bool(act.get("terminal", False)),
                    )

        if key := self.properties.get("key"):
            utility.save_setting(
                str(key).strip(), new_state ^ self.key_inverse, as_int=self.save_as_int
            )

        return False
