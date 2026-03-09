"""
Row widget definitions for the Dusky Control Center.

Optimized for:
- Stability: Thread Guards and cancellation tracking prevent race conditions.
- Efficiency: Gio.Subprocess async I/O eliminates thread pool overhead for shell commands.
- Type Safety: Strict TypedDict definitions and runtime-checkable Protocols.
- Architecture: Unified AsyncPollingMixin eliminates boilerplate and ensures consistent lifecycle management.
- Performance: Native Linux inotify (Gio.FileMonitor) eliminates idle polling for state files.

GTK4/Libadwaita compatible with proper lifecycle management via `do_unroot`.
"""
from __future__ import annotations

import atexit
import logging
import math
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
ICON_PIXEL_SIZE: Final[int] = 28
LABEL_MAX_WIDTH_CHARS: Final[int] = 16

# Bumped to 12 to prevent thread starvation on map storms
EXECUTOR_MAX_WORKERS: Final[int] = 12

LABEL_PLACEHOLDER: Final[str] = "..."
LABEL_NA: Final[str] = "N/A"
LABEL_TIMEOUT: Final[str] = "Timeout"
LABEL_ERROR: Final[str] = "Error"
STATE_ON: Final[str] = "On"
STATE_OFF: Final[str] = "Off"

TRUE_VALUES: Final[frozenset[str]] = frozenset(
    {"enabled", "yes", "true", "1", "on", "active", "set", "running", "open", "high"}
)

# Shell metacharacters that mandate /bin/sh -c interpretation.
# Quotes (' ") are intentionally excluded: shlex.split() handles them.
_SHELL_METACHAR: Final[frozenset[str]] = frozenset('|&;<>()$`\\*?#~![]{}=\n')


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
    state_command: str
    value_command: str
    min: float
    max: float
    step: float
    default: float
    debounce: bool
    options: list[str]
    options_map: dict[str, str]
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


# =============================================================================
# STATE MANAGEMENT (Optimized Architecture)
# =============================================================================
@dataclass(slots=True)
class PollSlot:
    """
    Atomic state for one polling channel.
    Encapsulates the lifecycle of a single repeating task (source ID, cancellable, running state).
    """
    source_id: int = 0
    cancellable: Any = None
    is_running: bool = False


@dataclass(slots=True)
class WidgetState:
    """
    Thread-safe state container for widget lifecycle and async operation guards.
    Uses dedicated slots for different polling concerns to prevent state collisions.
    """

    lock: threading.Lock = field(default_factory=threading.Lock)
    is_destroyed: bool = False
    
    # Dedicated slots for concurrent polling operations
    icon: PollSlot = field(default_factory=PollSlot)     # For DynamicIconMixin
    monitor: PollSlot = field(default_factory=PollSlot)  # For StateMonitorMixin (toggles)
    value: PollSlot = field(default_factory=PollSlot)    # For SliderMonitorMixin/LabelRow
    misc: PollSlot = field(default_factory=PollSlot)     # For generic extras (Badge, Button Text)
    
    # Specific ID for slider debounce (separate from generic polling)
    debounce_source_id: int = 0

    @property
    def _slots(self) -> tuple[PollSlot, ...]:
        return (self.icon, self.monitor, self.value, self.misc)

    def mark_destroyed_and_get_sources(self) -> tuple[int, ...]:
        """Atomically marks destroyed, cancels async ops, and returns source IDs for cleanup."""
        with self.lock:
            self.is_destroyed = True
            
            # Cancel all in-flight Gio.Subprocess operations across all slots
            for slot in self._slots:
                if isinstance(slot.cancellable, Gio.FileMonitor):
                    with suppress(Exception):
                        slot.cancellable.cancel()
                elif slot.cancellable is not None:
                    with suppress(Exception):
                        slot.cancellable.cancel()
                slot.cancellable = None
            
            # Harvest source IDs
            sources: list[int] = []
            for slot in self._slots:
                sources.append(slot.source_id)
                slot.source_id = 0
            
            sources.append(self.debounce_source_id)
            self.debounce_source_id = 0
            
            return tuple(sources)


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


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================
def _safe_int(value: object, default: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            pass
    return default


def _safe_float(value: object, default: float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            pass
    return default


def _is_dynamic_icon(icon_config: object) -> bool:
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
    return Path(path).expanduser()


def _resolve_static_icon_name(icon_config: object) -> str:
    if isinstance(icon_config, str):
        return icon_config or DEFAULT_ICON
    if isinstance(icon_config, dict):
        return str(icon_config.get("name", DEFAULT_ICON))
    return DEFAULT_ICON


def _safe_source_remove(source_id: int) -> None:
    if source_id > 0:
        with suppress(Exception):
            GLib.source_remove(source_id)


def _batch_source_remove(*source_ids: int) -> None:
    for sid in source_ids:
        _safe_source_remove(sid)


def _submit_task_safe(func: Callable[[], None], state: WidgetState) -> bool:
    try:
        _get_executor().submit(func)
        return True
    except RuntimeError:
        return False
    except Exception as e:
        log.error("Failed to submit task: %s", e)
        return False


# =============================================================================
# ASYNC SUBPROCESS INFRASTRUCTURE
# =============================================================================
def _parse_simple_argv(command: str) -> list[str] | None:
    """
    Attempt to decompose *command* into a direct-exec argv list.
    Returns the argv list when the command is a straightforward executable
    invocation (e.g. ``brightnessctl get``).
    Returns ``None`` when shell features are detected (pipes, redirections, 
    variable expansion, globs, etc.), signalling that /bin/sh -c is required.
    """
    # Fast O(n) set-intersection check — cheaper than a regex for short
    # polling commands and avoids compiling a pattern at import time.
    if _SHELL_METACHAR.intersection(command):
        return None
    try:
        argv = shlex.split(command)
        return argv if argv else None
    except ValueError:
        # Malformed quoting — let the shell figure it out.
        return None


def _run_shell_async(
    command: str,
    timeout_seconds: int,
    on_complete: Callable[[str | None], None],
) -> Gio.Cancellable | None:
    """
    Asynchronously run *command*, invoking *on_complete* on the main thread.
    **Optimization**: simple commands (no pipes, redirections, variable
    expansion) are exec'd directly, avoiding the fork+exec overhead of
    ``/bin/sh -c`` on every polling tick.
    """
    cancellable = Gio.Cancellable()
    timeout_source_id: int = 0
    
    def on_timeout() -> bool:
        nonlocal timeout_source_id
        timeout_source_id = 0  
        if not cancellable.is_cancelled():
            cancellable.cancel()
        return GLib.SOURCE_REMOVE

    def on_communicate_finish(
        proc: Gio.Subprocess, result: Gio.AsyncResult
    ) -> None:
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
            on_complete(None)

    # Direct exec when possible; shell wrapper only when necessary.
    argv = _parse_simple_argv(command)
    if argv is None:
        argv = ["/bin/sh", "-c", command]

    try:
        launcher = Gio.SubprocessLauncher.new(
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE
        )
        proc = launcher.spawnv(argv)
    except GLib.Error as e:
        log.debug("Failed to spawn command '%.30s...': %s", command, e.message)
        GLib.idle_add(lambda: (on_complete(None), GLib.SOURCE_REMOVE)[1])
        return None

    if timeout_seconds > 0:
        timeout_source_id = GLib.timeout_add_seconds(timeout_seconds, on_timeout)

    proc.communicate_utf8_async(None, cancellable, on_communicate_finish)
    return cancellable


# =============================================================================
# UNIFIED POLLING ENGINE (The "Core")
# =============================================================================
class AsyncPollingMixin:
    """
    Single-responsibility async polling engine.
    Consolidates locking, cancellable lifecycle, guard flags, and
    the map-check / destroy-check logic across all widgets.
    """
    _state: WidgetState

    def _start_poll_loop(
        self,
        slot: PollSlot,
        command: str,
        interval: int,
        on_output: Callable[[str], None],
        timeout: int = 2,
        *,
        immediate: bool = True,
    ) -> None:
        """
        Begin a periodic polling loop bound to a specific state *slot*.
        Safely replaces any existing timer on the same slot.
        """
        if immediate:
            self._poll_command(slot, command, on_output, timeout)

        with self._state.lock:
            if self._state.is_destroyed:
                return
            if slot.source_id > 0:
                GLib.source_remove(slot.source_id)
            slot.source_id = GLib.timeout_add_seconds(
                interval, self._poll_tick, slot, command, on_output, timeout,
            )

    def _poll_tick(
        self,
        slot: PollSlot,
        command: str,
        on_output: Callable[[str], None],
        timeout: int,
    ) -> bool:
        """GLib timeout callback — skips unmapped widgets, guards concurrency."""
        if isinstance(self, Gtk.Widget) and not self.get_mapped():
            return GLib.SOURCE_CONTINUE

        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE
            if slot.is_running:
                return GLib.SOURCE_CONTINUE

        self._poll_command(slot, command, on_output, timeout)
        return GLib.SOURCE_CONTINUE

    def _poll_command(
        self,
        slot: PollSlot,
        command: str,
        on_output: Callable[[str], None],
        timeout: int,
    ) -> None:
        """Cancel any in-flight operation on *slot*, then launch a new one."""
        with self._state.lock:
            if self._state.is_destroyed:
                slot.is_running = False
                return
            slot.is_running = True
            if slot.cancellable is not None:
                with suppress(Exception):
                    slot.cancellable.cancel()
                slot.cancellable = None

        def on_result(output: str | None) -> None:
            with self._state.lock:
                if slot.cancellable is cancellable:
                    slot.cancellable = None
                    slot.is_running = False
                if self._state.is_destroyed:
                    return
            if output is not None:
                on_output(output)

        try:
            cancellable = _run_shell_async(command, timeout, on_result)
        except Exception as e:
            log.error("Failed to execute async shell command: %s", e)
            with self._state.lock:
                slot.is_running = False
            return

        with self._state.lock:
            if not self._state.is_destroyed and cancellable:
                slot.cancellable = cancellable
            else:
                if cancellable:
                    cancellable.cancel()
                slot.is_running = False


# =============================================================================
# REFACTORED MIXINS (Using Unified Engine)
# =============================================================================
class DynamicIconMixin(AsyncPollingMixin):
    """Mixin providing dynamic icon updates via periodic command execution."""
    icon_widget: Gtk.Image

    def _start_icon_update_loop(self, icon_config: dict[str, object]) -> None:
        interval = _safe_int(icon_config.get("interval"), DEFAULT_INTERVAL_SECONDS)
        command = icon_config.get("command")

        if isinstance(command, str) and command.strip():
            self._start_poll_loop(
                self._state.icon,
                command.strip(),
                interval,
                on_output=self._apply_icon_update,
                timeout=SUBPROCESS_TIMEOUT_SHORT,
            )

    def _apply_icon_update(self, new_icon: str) -> None:
        new_icon = new_icon.strip()
        if new_icon and self.icon_widget.get_icon_name() != new_icon:
            self.icon_widget.set_from_icon_name(new_icon)


class StateMonitorMixin(AsyncPollingMixin):
    """Mixin providing external state monitoring via native inotify or polling."""
    properties: RowProperties

    def _start_state_monitor(self) -> None:
        has_key = bool(self.properties.get("key", ""))
        state_cmd = self.properties.get("state_command", "")
        has_state_cmd = isinstance(state_cmd, str) and bool(state_cmd.strip())

        if not has_key and not has_state_cmd:
            return

        if has_state_cmd:
            # Command based states still require polling
            interval = _safe_int(self.properties.get("interval"), MONITOR_INTERVAL_SECONDS)
            self._start_poll_loop(
                self._state.monitor,
                state_cmd.strip(),
                interval,
                on_output=self._handle_state_output,
                timeout=SUBPROCESS_TIMEOUT_SHORT,
                immediate=False,
            )
        else:
            # Native Linux inotify event listener (Zero CPU idle)
            key = str(self.properties.get("key", "")).strip()
            file_path = utility.SETTINGS_DIR / key
            try:
                file_path.parent.mkdir(parents=True, exist_ok=True)
                if not file_path.exists():
                    file_path.touch()
                
                gfile = Gio.File.new_for_path(str(file_path))
                monitor = gfile.monitor_file(Gio.FileMonitorFlags.NONE, None)
                monitor.connect("changed", self._on_file_changed)
                
                # Note: Gio.FileMonitor is stored in the cancellable field for cleanup parity.
                # Both FileMonitor and Cancellable expose .cancel(), enabling shared teardown logic.
                with self._state.lock:
                    self._state.monitor.cancellable = monitor
            except Exception as e:
                log.error(f"File monitor setup failed for {key}: {e}")

    def _handle_state_output(self, output: str) -> None:
        new_state = output.strip().lower() in TRUE_VALUES
        self._apply_state_update(new_state)

    def _on_file_changed(
        self,
        monitor: Gio.FileMonitor,
        file: Gio.File,
        other_file: Gio.File | None,
        event_type: Gio.FileMonitorEvent
    ) -> None:
        with self._state.lock:
            if self._state.is_destroyed:
                return

        if isinstance(self, Gtk.Widget) and not self.get_mapped():
            return

        if event_type in (Gio.FileMonitorEvent.CHANGES_DONE_HINT, Gio.FileMonitorEvent.CREATED):
            key = str(self.properties.get("key", "")).strip()
            val = utility.load_setting(key, default=False)
            if isinstance(val, bool):
                self._apply_state_update(val)

    def _apply_state_update(self, new_state: bool) -> bool:
        raise NotImplementedError


class SliderMonitorMixin(AsyncPollingMixin):
    """Mixin providing numeric value monitoring via periodic polling."""
    properties: RowProperties

    def _start_value_monitor(self) -> None:
        cmd = self.properties.get("value_command", "")
        if not isinstance(cmd, str) or not cmd.strip():
            return

        interval = _safe_int(self.properties.get("interval"), MONITOR_INTERVAL_SECONDS)
        
        self._start_poll_loop(
            self._state.value,
            cmd.strip(),
            interval,
            on_output=self._handle_value_output,
            timeout=SUBPROCESS_TIMEOUT_SHORT,
        )

    def _handle_value_output(self, output: str) -> None:
        try:
            new_value = float(output.strip())
        except (ValueError, OverflowError):
            log.debug("Non-numeric or overflow value_command output: %r", output.strip())
            return

        if not math.isfinite(new_value):
            return

        self._apply_value_update(new_value)

    def _apply_value_update(self, new_value: float) -> bool:
        raise NotImplementedError
# =============================================================================
# BASE ROW CLASS
# =============================================================================
class BaseActionRow(DynamicIconMixin, Adw.ActionRow):
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
        self._perform_cleanup()
        Adw.ActionRow.do_unroot(self)

    def _perform_cleanup(self) -> None:
        sources = self._state.mark_destroyed_and_get_sources()
        _batch_source_remove(*sources)


# =============================================================================
# ROW IMPLEMENTATIONS
# =============================================================================
class ButtonRow(BaseActionRow):
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
        # Use 'misc' slot for button text polling
        self._update_dynamic_state()
        with self._state.lock:
            if not self._state.is_destroyed:
                self._state.misc.source_id = GLib.timeout_add_seconds(2, self._update_dynamic_state)

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
                utility.toast(self.toast_overlay, msg, 2 if success else 4)
        elif t == "redirect":
            if pid := act.get("page"):
                _perform_redirect(str(pid), self.config, self.sidebar)


class ToggleRow(StateMonitorMixin, BaseActionRow):
    __gtype_name__ = "DuskyToggleRow"

    def __init__(
        self,
        properties: RowProperties,
        on_toggle: ActionConfig | None = None,
        context: RowContext | None = None,
    ) -> None:
        super().__init__(properties, on_toggle, context)

        self._programmatic_update_event = threading.Event()

        self.toggle_switch = Gtk.Switch()
        self.toggle_switch.set_valign(Gtk.Align.CENTER)

        if key := properties.get("key"):
            val = utility.load_setting(str(key).strip(), default=False)
            if isinstance(val, bool):
                self.toggle_switch.set_active(val)

        self.toggle_switch.connect("state-set", self._on_toggle_changed)
        self.add_suffix(self.toggle_switch)
        self.set_activatable_widget(self.toggle_switch)
        self._start_state_monitor()

    def _apply_state_update(self, new_state: bool) -> bool:
        with self._state.lock:
            if self._state.is_destroyed: return GLib.SOURCE_REMOVE

        if new_state != self.toggle_switch.get_active():
            self._programmatic_update_event.set()
            try:
                self.toggle_switch.set_active(new_state)
            finally:
                self._programmatic_update_event.clear()

        return GLib.SOURCE_REMOVE

    def _on_toggle_changed(self, _switch: Gtk.Switch, state: bool) -> bool:
        if self._programmatic_update_event.is_set():
            return False

        if isinstance(self.on_action, dict):
            action_key = "enabled" if state else "disabled"
            if action := self.on_action.get(action_key):
                if isinstance(action, dict) and (cmd := action.get("command")):
                    utility.execute_command(str(cmd).strip(), "Toggle", bool(action.get("terminal", False)))

        # Offload file I/O to thread pool to prevent main thread blocking
        if key := self.properties.get("key"):
            key_str = str(key).strip()
            _submit_task_safe(lambda: utility.save_setting(key_str, state), self._state)

        return False


class LabelRow(BaseActionRow):
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

        interval = _safe_int(properties.get("interval"), 0)
        
        # Optimization: Route repetitive shell execs to the native Gio async engine
        # to strictly avoid thread pool starvation.
        is_exec = isinstance(self.value_config, dict) and self.value_config.get("type") == "exec"
        
        if is_exec and interval > 0:
            val_dict = self.value_config
            cmd = ""
            if isinstance(val_dict, dict):
                cmd = str(val_dict.get("command", "")).strip()
                
            if cmd:
                self._start_poll_loop(
                    self._state.value,
                    cmd,
                    interval,
                    on_output=self._handle_async_output,
                    timeout=SUBPROCESS_TIMEOUT_LONG,
                )
            else:
                self._update_label(LABEL_NA)
        else:
            self._trigger_update()
            if interval > 0:
                with self._state.lock:
                    if not self._state.is_destroyed:
                        # LabelRow uses the 'value' slot for updates
                        self._state.value.source_id = GLib.timeout_add_seconds(
                            interval, self._on_timeout
                        )

    def _handle_async_output(self, output: str) -> None:
        """Callback for Gio async loop execution."""
        self._update_label(output.strip() if output else LABEL_NA)

    def _on_timeout(self) -> bool:
        if isinstance(self, Gtk.Widget) and not self.get_mapped():
            return GLib.SOURCE_CONTINUE
        with self._state.lock:
            if self._state.is_destroyed: return GLib.SOURCE_REMOVE
        self._trigger_update()
        return GLib.SOURCE_CONTINUE

    def _trigger_update(self) -> None:
        with self._state.lock:
            if self._state.value.is_running or self._state.is_destroyed:
                return
            self._state.value.is_running = True

        if not _submit_task_safe(self._load_value_async, self._state):
            with self._state.lock:
                self._state.value.is_running = False

    def _load_value_async(self) -> None:
        result = LABEL_NA
        try:
            with self._state.lock:
                if self._state.is_destroyed: return
            result = self._get_value_text(self.value_config)
        finally:
            with self._state.lock:
                self._state.value.is_running = False

        GLib.idle_add(self._update_label, result)

    def _update_label(self, text: str) -> bool:
        with self._state.lock:
            if self._state.is_destroyed: return GLib.SOURCE_REMOVE
        if self.value_label.get_label() != text:
            self.value_label.set_label(text)
            self.value_label.remove_css_class("dim-label")
        return GLib.SOURCE_REMOVE

    def _get_value_text(self, val: ValueConfig) -> str:
        if isinstance(val, str): return val
        if not isinstance(val, dict): return LABEL_NA
        match val.get("type"):
            case "exec": return self._exec_cmd(str(val.get("command", "")))
            case "static": return str(val.get("text", LABEL_NA))
            case "file": return self._read_file(str(val.get("path", "")))
            case "system":
                result = utility.get_system_value(str(val.get("key", "")))
                return str(result) if result else LABEL_NA
        return LABEL_NA

    def _exec_cmd(self, cmd: str) -> str:
        cmd = cmd.strip()
        if not cmd: return LABEL_NA
        if cmd.startswith("cat "):
            try:
                parts = shlex.split(cmd)
                if len(parts) == 2: return self._read_file(parts[1])
            except ValueError: pass
        try:
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_LONG)
            return res.stdout.strip() or LABEL_NA
        except subprocess.TimeoutExpired: return LABEL_TIMEOUT
        except subprocess.SubprocessError: return LABEL_ERROR

    def _read_file(self, path: str) -> str:
        if not path.strip(): return LABEL_NA
        try:
            return _expand_path(path.strip()).read_text(encoding="utf-8").strip()
        except OSError: return LABEL_NA


class SliderRow(SliderMonitorMixin, BaseActionRow):
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

        # ---- feedback-loop guard (no lock needed — main thread only) ----
        self._slider_changing: bool = False
        self._last_snapped: float | None = None
        self._pending_value: float | None = None

        default_val = _safe_float(properties.get("default"), self.min_val)
        adj = Gtk.Adjustment(
            value=default_val, lower=self.min_val, upper=self.max_val,
            step_increment=self.step_val, page_increment=self.step_val * 10, page_size=0,
        )

        self.slider = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj)
        self.slider.set_valign(Gtk.Align.CENTER)
        self.slider.set_hexpand(False)
        self.slider.set_draw_value(False)
        self.slider.set_size_request(250, -1)
        self.slider.connect("value-changed", self._on_value_changed)
        self.add_suffix(self.slider)

        self._start_value_monitor()

    def _apply_value_update(self, new_value: float) -> bool:
        """Push a polled value into the slider, suppressing feedback."""
        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE

        current = self.slider.get_value()
        if abs(current - new_value) < self.step_val:
            return GLib.SOURCE_REMOVE

        # Guard against the synchronous value-changed re-entry.
        self._slider_changing = True
        try:
            safe_val = max(self.min_val, min(new_value, self.max_val))
            self.slider.set_value(safe_val)
            self._last_snapped = safe_val
        finally:
            self._slider_changing = False

        return GLib.SOURCE_REMOVE

    def _on_value_changed(self, scale: Gtk.Scale) -> None:
        # Re-entrant call from set_value() inside this class — ignore.
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

        # Snap the visual handle if it drifted from the grid.
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
        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE
            self._state.debounce_source_id = 0

        value = self._pending_value
        self._pending_value = None

        if value is None:
            return GLib.SOURCE_REMOVE

        if isinstance(self.on_action, dict) and self.on_action.get("type") == "exec":
            if cmd := self.on_action.get("command"):
                final_cmd = str(cmd).replace("{value}", str(int(value)))
                is_term = bool(self.on_action.get("terminal", False))
                if is_term:
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

        raw_map = properties.get("options_map", {})
        self.options_map = {str(k).lower(): str(v) for k, v in raw_map.items()}
        self.reverse_map = {str(v): str(k) for k, v in raw_map.items()}

        self.connect("notify::selected", self._on_selected)
        self.connect("map", self._on_map)

        if _is_dynamic_icon(icon_config) and isinstance(icon_config, dict):
            self._start_icon_update_loop(icon_config)

        if properties.get("options_command"):
            _submit_task_safe(self._fetch_options_async, self._state)

        if key := properties.get("key"):
            val = utility.load_setting(str(key).strip(), default="")
            val_lower = str(val).lower()
            mapped_val = self.options_map.get(val_lower, str(val))
            if mapped_val and mapped_val in self.options_list:
                with self._suppress_change_signal():
                    self.set_selected(self.options_list.index(mapped_val))

        if properties.get("value_command") or properties.get("key"):
            self._start_selection_monitor()

    def _create_icon_widget(self, icon: object) -> Gtk.Image:
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
        self._programmatic_update = True
        try: yield
        finally: self._programmatic_update = False

    def _fetch_options_async(self) -> None:
        cmd = self.properties.get("options_command", "")
        if not cmd: return
        try:
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_LONG)
            if res.returncode == 0:
                lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
                if lines: GLib.idle_add(self._update_options_ui, lines)
        except Exception as e:
            log.error(f"Options fetch failed: {e}")

    def _update_options_ui(self, new_options: list[str]) -> bool:
        with self._state.lock:
            if self._state.is_destroyed: return GLib.SOURCE_REMOVE
        if new_options != self.options_list:
            self.options_list = new_options
            with self._suppress_change_signal():
                self.set_model(Gtk.StringList.new(self.options_list))
                _submit_task_safe(self._fetch_selection_async, self._state)
        return GLib.SOURCE_REMOVE

    def _start_selection_monitor(self) -> None:
        # SelectionRow uses 'value' slot for selection monitoring
        interval = _safe_int(self.properties.get("interval"), DEFAULT_INTERVAL_SECONDS)
        with self._state.lock:
            if self._state.is_destroyed: return
            self._state.value.source_id = GLib.timeout_add_seconds(interval, self._check_selection_tick)

    def _on_map(self, _widget: Gtk.Widget) -> None:
        _submit_task_safe(self._fetch_selection_async, self._state)
        if self.properties.get("options_command"):
             _submit_task_safe(self._fetch_options_async, self._state)

    def _check_selection_tick(self) -> bool:
        if not self.get_mapped(): return GLib.SOURCE_CONTINUE
        with self._state.lock:
            if self._state.is_destroyed: return GLib.SOURCE_REMOVE
        _submit_task_safe(self._fetch_selection_async, self._state)
        return GLib.SOURCE_CONTINUE

    def _fetch_selection_async(self) -> None:
        if key := self.properties.get("key"):
            try:
                val = utility.load_setting(str(key).strip(), default="")
                val_lower = str(val).lower()
                mapped_val = self.options_map.get(val_lower, str(val))
                if mapped_val: GLib.idle_add(self._update_selection_ui, mapped_val)
            except Exception: pass
            return

        cmd = self.properties.get("value_command", "")
        if not cmd: return
        try:
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_SHORT)
            if res.returncode == 0:
                value = res.stdout.strip()
                value_lower = value.lower()
                mapped_val = self.options_map.get(value_lower, value)
                if mapped_val: GLib.idle_add(self._update_selection_ui, mapped_val)
        except Exception: pass

    def _update_selection_ui(self, value: str) -> bool:
        with self._state.lock:
            if self._state.is_destroyed: return GLib.SOURCE_REMOVE
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
        if self._programmatic_update: return
        model = self.get_model()
        if not model: return
        idx = self.get_selected()
        if idx >= model.get_n_items(): return
        item = model.get_string(idx)

        # Offload file I/O to thread pool
        if key := self.properties.get("key"):
            key_str = str(key).strip()
            write_val = self.reverse_map.get(item, item)

            _submit_task_safe(lambda: utility.save_setting(key_str, write_val), self._state)

        if isinstance(self.on_action, dict):
            action = self.on_action.get(item)
            if not isinstance(action, dict) and "command" in self.on_action:
                action = self.on_action

            if isinstance(action, dict) and (cmd := action.get("command")):
                safe_value = shlex.quote(item)
                final_cmd = str(cmd).replace("{value}", safe_value)
                utility.execute_command(final_cmd, "Selection", bool(action.get("terminal", False)))

    def do_unroot(self) -> None:
        sources = self._state.mark_destroyed_and_get_sources()
        _batch_source_remove(*sources)
        Adw.ComboRow.do_unroot(self)


class EntryRow(DynamicIconMixin, Adw.EntryRow):
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
        if not text: return
        if isinstance(self.on_action, dict) and (cmd := self.on_action.get("command")):
            final_cmd = str(cmd).replace("{value}", text)
            utility.execute_command(final_cmd, "Entry", bool(self.on_action.get("terminal", False)))

    def do_unroot(self) -> None:
        sources = self._state.mark_destroyed_and_get_sources()
        _batch_source_remove(*sources)
        Adw.EntryRow.do_unroot(self)


class NavigationRow(BaseActionRow):
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
        if self.nav_view and self.builder_func:
            title = str(self.properties.get("title", "Subpage"))
            current_path = self.context.get("path", [])
            new_path = list(current_path) + [title]
            new_ctx = self.context.copy()
            new_ctx["path"] = new_path
            self.nav_view.push(self.builder_func(title, self.layout_data, new_ctx))


class ExpanderRow(DynamicIconMixin, Adw.ExpanderRow):
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
        for item in self.items_data:
            if not isinstance(item, dict): continue
            row = self._build_single_row(item)
            if row is not None: self.add_row(row)

    def _build_single_row(self, item: dict[str, object]) -> Adw.PreferencesRow | None:
        item_type = str(item.get("type", "")).lower()
        props = item.get("properties", {})
        if not isinstance(props, dict): props = {}

        try:
            match item_type:
                case "button": return ButtonRow(props, item.get("on_press"), self.context)
                case "toggle": return ToggleRow(props, item.get("on_toggle"), self.context)
                case "label": return LabelRow(props, item.get("value"), self.context)
                case "slider": return SliderRow(props, item.get("on_change"), self.context)
                case "selection": return SelectionRow(props, item.get("on_change"), self.context)
                case "entry": return EntryRow(props, item.get("on_action"), self.context)
                case "navigation": return NavigationRow(props, item.get("layout"), self.context)
                case "expander": return ExpanderRow(props, item.get("items"), self.context)
                case _:
                    log.warning("Unknown item type '%s' in expander, skipping", item_type)
                    return None
        except Exception as e:
            log.error("Failed to build child row for type '%s': %s", item_type, e)
            return None

    def do_unroot(self) -> None:
        self._perform_cleanup()
        Adw.ExpanderRow.do_unroot(self)

    def _perform_cleanup(self) -> None:
        sources = self._state.mark_destroyed_and_get_sources()
        _batch_source_remove(*sources)


# =============================================================================
# GRID CARDS
# =============================================================================
class GridCardBase(Gtk.Button):
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
        self.title_label: Gtk.Label | None = None

        self.base_style = str(properties.get("style", "default")).lower()
        self._current_card_style = ""
        self._apply_base_style(self.base_style)

    def _apply_base_style(self, style: str) -> None:
        """Dynamically add/remove CSS classes based on the current style state."""
        if style == self._current_card_style:
            return
            
        if self._current_card_style == "destructive":
            self.remove_css_class("destructive-card")
        elif self._current_card_style == "suggested":
            self.remove_css_class("suggested-card")
            
        if style == "destructive":
            self.add_css_class("destructive-card")
        elif style == "suggested":
            self.add_css_class("suggested-card")
            
        self._current_card_style = style

    def do_unroot(self) -> None:
        self._perform_cleanup()
        Gtk.Button.do_unroot(self)

    def _perform_cleanup(self) -> None:
        sources = self._state.mark_destroyed_and_get_sources()
        _batch_source_remove(*sources)

    def _build_content(self, icon: str, title: str) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)

        img = Gtk.Image.new_from_icon_name(icon)
        img.set_pixel_size(ICON_PIXEL_SIZE)
        img.add_css_class("hero-icon")
        self.icon_widget = img

        self.title_label = Gtk.Label(label=title, css_classes=["hero-title"])
        self.title_label.set_wrap(True)
        self.title_label.set_justify(Gtk.Justification.CENTER)
        self.title_label.set_max_width_chars(LABEL_MAX_WIDTH_CHARS)

        box.append(img)
        box.append(self.title_label)
        return box


class GridCard(DynamicIconMixin, GridCardBase):
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

        # Build overlay hierarchy before attaching to prevent visual flashes
        self.badge_label: Gtk.Label | None = None
        badge_path = properties.get("badge_file")
        
        if badge_path:
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
        else:
            self.set_child(box)

        self.connect("clicked", self._on_clicked)

        if _is_dynamic_icon(icon_conf) and isinstance(icon_conf, dict):
            self._start_icon_update_loop(icon_conf)

        # Dynamic Text and Style Polling
        self.text_file: str | None = properties.get("button_text_file")
        self.text_map: dict[str, str] = properties.get("button_text_map") or {}
        self.style_map: dict[str, str] = properties.get("style_map") or {}
        self.base_title = str(properties.get("title", "Unnamed"))
        
        if self.text_file:
            self._start_dynamic_style_poll()

    def _start_dynamic_style_poll(self) -> None:
        # Fetch immediately to bypass the initial get_mapped() delay
        _submit_task_safe(self._fetch_dynamic_state_async, self._state)
        
        with self._state.lock:
            if not self._state.is_destroyed:
                self._state.value.source_id = GLib.timeout_add_seconds(
                    MONITOR_INTERVAL_SECONDS, self._dynamic_state_tick
                )

    def _dynamic_state_tick(self) -> bool:
        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE
                
        if not self.get_mapped():
            return GLib.SOURCE_CONTINUE
            
        _submit_task_safe(self._fetch_dynamic_state_async, self._state)
        return GLib.SOURCE_CONTINUE

    def _fetch_dynamic_state_async(self) -> None:
        """Runs in the background thread pool."""
        val: str | None = None
        try:
            if self.text_file:
                path = _expand_path(self.text_file)
                if path.exists():
                    val = path.read_text(encoding="utf-8").strip()
        except Exception as e: 
            log.debug(f"Failed to read dynamic state file {self.text_file}: {e}")
            
        GLib.idle_add(self._apply_dynamic_state_ui, val)

    def _apply_dynamic_state_ui(self, val: str | None) -> bool:
        """Runs on the GTK main thread."""
        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE
                
        if val is not None:
            new_label = self.text_map.get(val, self.text_map.get("default", self.base_title))
            if self.title_label and self.title_label.get_label() != new_label: 
                self.title_label.set_label(new_label)
                
            new_style = self.style_map.get(val, self.style_map.get("default", self.base_style))
            self._apply_base_style(new_style)
            
        return GLib.SOURCE_REMOVE

    def _start_badge_monitor(self, path_str: str) -> None:
        self._check_badge_tick(path_str)
        with self._state.lock:
            if self._state.is_destroyed: return
            self._state.misc.source_id = GLib.timeout_add_seconds(
                DEFAULT_INTERVAL_SECONDS, self._check_badge_tick, path_str
            )

    def _check_badge_tick(self, path_str: str) -> bool:
        if not self.get_mapped(): return GLib.SOURCE_CONTINUE
        with self._state.lock:
            if self._state.is_destroyed: return GLib.SOURCE_REMOVE
        _submit_task_safe(lambda: self._fetch_badge_async(path_str), self._state)
        return GLib.SOURCE_CONTINUE

    def _fetch_badge_async(self, path_str: str) -> None:
        count_text: str | None = None
        try:
            path = _expand_path(path_str)
            if path.exists():
                content = path.read_text(encoding="utf-8").strip()
                if content.isdigit() and int(content) > 0:
                    count_text = content
        except Exception: pass
        GLib.idle_add(self._update_badge_ui, count_text)

    def _update_badge_ui(self, text: str | None) -> bool:
        with self._state.lock:
            if self._state.is_destroyed: return GLib.SOURCE_REMOVE
        if self.badge_label:
            if text:
                self.badge_label.set_label(text)
                self.badge_label.set_visible(True)
            else:
                self.badge_label.set_visible(False)
        return GLib.SOURCE_REMOVE

    def _on_clicked(self, _button: Gtk.Button) -> None:
        if not isinstance(self.on_action, dict): return
        match self.on_action.get("type"):
            case "exec":
                if cmd := self.on_action.get("command"):
                    success = utility.execute_command(str(cmd).strip(), "Command", bool(self.on_action.get("terminal", False)))
                    utility.toast(self.toast_overlay, "▶ Launched" if success else "✖ Failed")
            case "redirect":
                if pid := self.on_action.get("page"):
                    _perform_redirect(str(pid), self.context.get("config") or {}, self.context.get("sidebar"))
class GridToggleCard(DynamicIconMixin, StateMonitorMixin, GridCardBase):
    __gtype_name__ = "DuskyGridToggleCard"

    def __init__(
        self,
        properties: RowProperties,
        on_toggle: ActionConfig | None = None,
        context: RowContext | None = None,
    ) -> None:
        super().__init__(properties, on_toggle, context)

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
            val = utility.load_setting(str(key).strip(), default=False)
            if isinstance(val, bool):
                self._set_visual(val)

        self.connect("clicked", self._on_clicked)
        self._start_state_monitor()

        if _is_dynamic_icon(icon_conf) and isinstance(icon_conf, dict):
            self._start_icon_update_loop(icon_conf)

    def _apply_state_update(self, new_state: bool) -> bool:
        with self._state.lock:
            if self._state.is_destroyed: return GLib.SOURCE_REMOVE
        if new_state != self.is_active:
            self._set_visual(new_state)
        return GLib.SOURCE_REMOVE

    def _set_visual(self, state: bool) -> None:
        self.is_active = state
        self.status_lbl.set_label(STATE_ON if state else STATE_OFF)
        if state: self.add_css_class("toggle-active")
        else: self.remove_css_class("toggle-active")

    def _on_clicked(self, _button: Gtk.Button) -> None:
        new_state = not self.is_active
        self._set_visual(new_state)
        if isinstance(self.on_action, dict):
            action_key = "enabled" if new_state else "disabled"
            if act := self.on_action.get(action_key):
                if isinstance(act, dict) and (cmd := act.get("command")):
                    utility.execute_command(str(cmd).strip(), "Toggle", bool(act.get("terminal", False)))
        
        # Offload file I/O to thread pool
        if key := self.properties.get("key"):
            key_str = str(key).strip()
            _submit_task_safe(lambda: utility.save_setting(key_str, new_state), self._state)
            
        return False
