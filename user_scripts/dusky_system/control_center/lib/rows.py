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
import json
import logging
import math
import os
import shlex
import signal
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, suppress
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


class _SettingsExecutorManager:
    """
    Dedicated single-worker executor for persisted setting writes.
    This preserves submission order and prevents stale values from
    winning when the same key is updated rapidly.
    """

    __slots__ = ("_executor", "_lock", "_is_shutdown")
    _instance: _SettingsExecutorManager | None = None

    def __new__(cls) -> _SettingsExecutorManager:
        if cls._instance is None:
            instance = super().__new__(cls)
            instance._executor = None
            instance._lock = threading.Lock()
            instance._is_shutdown = False
            atexit.register(instance.shutdown)
            cls._instance = instance
        return cls._instance

    def get(self) -> ThreadPoolExecutor:
        if self._executor is None or self._is_shutdown:
            with self._lock:
                if self._executor is None or self._is_shutdown:
                    self._is_shutdown = False
                    self._executor = ThreadPoolExecutor(
                        max_workers=1,
                        thread_name_prefix="dusky-settings-",
                    )
        return self._executor

    def shutdown(self) -> None:
        with self._lock:
            if self._executor is not None and not self._is_shutdown:
                log.debug("Shutting down settings write executor.")
                self._is_shutdown = True
                self._executor.shutdown(wait=False, cancel_futures=True)
                self._executor = None


def _get_settings_executor() -> ThreadPoolExecutor:
    return _SettingsExecutorManager().get()


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


class ActionRedirect(TypedDict, total=False):
    type: str  # Literal["redirect"]
    page: str
    subpage: str


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
    Encapsulates the lifecycle of a single repeating task.
    """

    source_id: int = 0
    cancellable: Any = None
    is_running: bool = False
    generation: int = 0


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
        """Mark destroyed, detach cleanup handles under lock, then cancel outside the lock."""
        sources: list[int] = []
        cancellables: list[Any] = []

        with self.lock:
            if self.is_destroyed:
                return ()

            self.is_destroyed = True

            for slot in self._slots:
                if slot.cancellable is not None:
                    cancellables.append(slot.cancellable)

                slot.cancellable = None
                slot.is_running = False

                if slot.source_id > 0:
                    sources.append(slot.source_id)
                    slot.source_id = 0

            if self.debounce_source_id > 0:
                sources.append(self.debounce_source_id)
                self.debounce_source_id = 0

        for cancellable in cancellables:
            with suppress(Exception):
                cancellable.cancel()

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
    action: dict[str, Any],
    context: RowContext,
) -> None:
    """
    Process an advanced page redirect. 
    Handles basic sidebar jumps as well as nested subpage layout traversal, generating
    and pushing subsequent navigation pages if requested.
    """
    page_id = action.get("page")
    subpage_title = action.get("subpage")

    if not page_id:
        return

    config = context.get("config", {})
    sidebar = context.get("sidebar")
    stack = context.get("stack")
    builder_func = context.get("builder_func")

    if sidebar is None or stack is None:
        return

    pages = config.get("pages")
    if not isinstance(pages, list):
        return

    target_page_idx = -1
    target_page_config = None

    for idx, page in enumerate(pages):
        if isinstance(page, dict) and page.get("id") == page_id:
            target_page_idx = idx
            target_page_config = page
            break

    if target_page_idx == -1 or not target_page_config:
        return

    # Select the main page in the sidebar (triggers pop_to_tag -> Root sync)
    if row := sidebar.get_row_at_index(target_page_idx):
        sidebar.select_row(row)

    # Force switch stack visible child natively
    page_name = f"page-{target_page_idx}"
    stack.set_visible_child_name(page_name)

    if not subpage_title:
        return

    # Extract target page view container
    child = stack.get_child_by_name(page_name)
    if not isinstance(child, Adw.NavigationView):
        return

    def find_subpage_layout(layout: list[Any], target: str) -> tuple[list[str], list[Any]] | None:
        for section in layout:
            if not isinstance(section, dict):
                continue
            items = section.get("items", [section]) if "items" in section else [section]
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "navigation":
                    props = item.get("properties", {})
                    title = str(props.get("title", "")).strip()
                    if title == target:
                        return ([title], item.get("layout", []))
                    sub_layout = item.get("layout")
                    if isinstance(sub_layout, list):
                        res = find_subpage_layout(sub_layout, target)
                        if res:
                            return ([title] + res[0], res[1])
                elif item.get("type") == "expander":
                    res = find_subpage_layout([{"items": item.get("items", [])}], target)
                    if res:
                        return res
        return None

    layout = target_page_config.get("layout", [])
    main_title = str(target_page_config.get("title", "Unknown")).strip()
    found = find_subpage_layout(layout, str(subpage_title))

    if not found or not builder_func:
        return

    path_suffix, _ = found
    current_path = [main_title]
    current_layout = layout

    def make_nav_tag(p: list[str]) -> str:
        parts = []
        for raw_part in p:
            part = "".join(ch if ch.isalnum() else "_" for ch in raw_part.strip())
            parts.append(part.strip("_") or "page")
        return f"page_{len(p)}_{'__'.join(parts)}"

    def get_step_layout(lay: list[Any], title: str) -> list[Any] | None:
        for sec in lay:
            if not isinstance(sec, dict): continue
            items = sec.get("items", [sec]) if "items" in sec else [sec]
            for it in items:
                if not isinstance(it, dict): continue
                if it.get("type") == "navigation":
                    props = it.get("properties", {})
                    if str(props.get("title", "")).strip() == title:
                        return it.get("layout", [])
                elif it.get("type") == "expander":
                    res = get_step_layout([{"items": it.get("items", [])}], title)
                    if res is not None:
                        return res
        return None

    # Iteratively build and push each section of the found layout path
    for step_title in path_suffix:
        current_path.append(step_title)
        step_layout = get_step_layout(current_layout, step_title)
        if step_layout is None:
            break

        current_layout = step_layout
        tag = make_nav_tag(current_path)
        page = child.find_page(tag)

        if not page:
            new_ctx = dict(context)
            new_ctx["path"] = list(current_path)
            new_ctx["nav_view"] = child
            page = builder_func(step_title, current_layout, new_ctx)

        child.push(page)


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
    with state.lock:
        if state.is_destroyed:
            return False

    try:
        _get_executor().submit(func)
        return True
    except RuntimeError:
        return False
    except Exception as e:
        log.error("Failed to submit task: %s", e)
        return False


def _submit_setting_save_safe(key: str, value: object) -> bool:
    try:
        _get_settings_executor().submit(utility.save_setting, key, value)
        return True
    except RuntimeError:
        return False
    except Exception as e:
        log.error("Failed to submit setting save for %r: %s", key, e)
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


class _AsyncCommandHandle:
    __slots__ = ("_proc", "_cancellable", "_lock", "_timeout_source_id")

    def __init__(self, proc: Gio.Subprocess, cancellable: Gio.Cancellable) -> None:
        self._proc = proc
        self._cancellable = cancellable
        self._lock = threading.Lock()
        self._timeout_source_id = 0

    def set_timeout_source(self, source_id: int) -> None:
        with self._lock:
            self._timeout_source_id = source_id

    def forget_timeout_source(self) -> None:
        with self._lock:
            self._timeout_source_id = 0

    def clear_timeout_source(self) -> None:
        with self._lock:
            source_id = self._timeout_source_id
            self._timeout_source_id = 0
        _safe_source_remove(source_id)

    def cancel(self) -> None:
        self.clear_timeout_source()
        with suppress(Exception):
            self._cancellable.cancel()
        with suppress(Exception):
            self._proc.force_exit()


def _run_shell_async(
    command: str,
    timeout_seconds: int,
    on_complete: Callable[[str | None], None],
) -> _AsyncCommandHandle | None:
    """
    Asynchronously run *command*, invoking *on_complete* on the main thread.
    Simple commands are exec'd directly; shell wrapping is only used when
    shell features are required.
    """
    cancellable = Gio.Cancellable()

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

    handle = _AsyncCommandHandle(proc, cancellable)

    def on_timeout() -> bool:
        handle.forget_timeout_source()
        handle.cancel()
        return GLib.SOURCE_REMOVE

    def on_communicate_finish(
        proc: Gio.Subprocess, result: Gio.AsyncResult,
    ) -> None:
        handle.clear_timeout_source()

        try:
            success, stdout_data, _ = proc.communicate_utf8_finish(result)
            if success and proc.get_successful() and stdout_data is not None:
                on_complete(stdout_data.strip())
            else:
                on_complete(None)
        except GLib.Error:
            on_complete(None)

    if timeout_seconds > 0:
        handle.set_timeout_source(GLib.timeout_add_seconds(timeout_seconds, on_timeout))

    proc.communicate_utf8_async(None, cancellable, on_communicate_finish)
    return handle


def _spawn_command_async(command: str) -> bool:
    """
    Spawn *command* without capturing output and asynchronously reap it.
    """
    argv = _parse_simple_argv(command)
    if argv is None:
        argv = ["/bin/sh", "-c", command]

    try:
        launcher = Gio.SubprocessLauncher.new(
            Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE
        )
        proc = launcher.spawnv(argv)
    except GLib.Error as e:
        log.debug("Failed to spawn command '%.30s...': %s", command, e.message)
        return False

    def on_wait_finish(proc: Gio.Subprocess, result: Gio.AsyncResult) -> None:
        with suppress(GLib.Error):
            proc.wait_check_finish(result)

    proc.wait_check_async(None, on_wait_finish)
    return True


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
        interval = max(1, interval)

        if immediate:
            self._poll_command(slot, command, on_output, timeout)

        old_source_id = 0
        with self._state.lock:
            if self._state.is_destroyed:
                return
            old_source_id = slot.source_id
            slot.source_id = 0

        _safe_source_remove(old_source_id)

        with self._state.lock:
            if self._state.is_destroyed:
                return
            slot.source_id = GLib.timeout_add_seconds(
                interval,
                self._poll_tick,
                slot,
                command,
                on_output,
                timeout,
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
        old_handle: Any = None

        with self._state.lock:
            if self._state.is_destroyed:
                slot.is_running = False
                return

            slot.generation += 1
            generation = slot.generation
            slot.is_running = True

            old_handle = slot.cancellable
            slot.cancellable = None

        if old_handle is not None:
            with suppress(Exception):
                old_handle.cancel()

        handle: _AsyncCommandHandle | None = None

        def on_result(output: str | None) -> None:
            with self._state.lock:
                if self._state.is_destroyed or slot.generation != generation:
                    return
                if slot.cancellable is not handle:
                    return

                slot.cancellable = None
                slot.is_running = False

            if output is not None:
                on_output(output)

        try:
            handle = _run_shell_async(command, timeout, on_result)
        except Exception as e:
            log.error("Failed to execute async shell command: %s", e)
            with self._state.lock:
                if slot.generation == generation:
                    slot.is_running = False
            return

        cancel_new_handle = False

        with self._state.lock:
            if self._state.is_destroyed or slot.generation != generation:
                cancel_new_handle = handle is not None
                if slot.generation == generation:
                    slot.is_running = False
            elif handle is None:
                slot.is_running = False
            else:
                slot.cancellable = handle

        if cancel_new_handle and handle is not None:
            with suppress(Exception):
                handle.cancel()


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
            interval = _safe_int(self.properties.get("interval"), MONITOR_INTERVAL_SECONDS)
            self._start_poll_loop(
                self._state.monitor,
                state_cmd.strip(),
                interval,
                on_output=self._handle_state_output,
                timeout=SUBPROCESS_TIMEOUT_SHORT,
                immediate=True,
            )
            return

        key = str(self.properties.get("key", "")).strip()
        settings_dir = utility.SETTINGS_DIR.resolve()
        file_path = (settings_dir / key).resolve()

        if not file_path.is_relative_to(settings_dir):
            log.error("Refusing to monitor setting key outside SETTINGS_DIR: %r", key)
            return

        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            if not file_path.exists():
                file_path.touch()

            gfile = Gio.File.new_for_path(str(file_path.parent))
            monitor = gfile.monitor_directory(Gio.FileMonitorFlags.NONE, None)
            monitor.connect("changed", self._on_file_changed, key, file_path.name)

            with self._state.lock:
                if self._state.is_destroyed:
                    monitor.cancel()
                    return
                self._state.monitor.cancellable = monitor
        except Exception as e:
            log.error("File monitor setup failed for %s: %s", key, e)

    def _handle_state_output(self, output: str) -> None:
        new_state = output.strip().lower() in TRUE_VALUES
        self._apply_state_update(new_state)

    def _on_file_changed(
        self,
        monitor: Gio.FileMonitor,
        file: Gio.File,
        other_file: Gio.File | None,
        event_type: Gio.FileMonitorEvent,
        key: str,
        target_name: str,
    ) -> None:
        with self._state.lock:
            if self._state.is_destroyed:
                return

        changed_name = file.get_basename() if file is not None else None
        other_name = other_file.get_basename() if other_file is not None else None
        if target_name not in (changed_name, other_name):
            return

        handled_events = {
            Gio.FileMonitorEvent.CHANGED,
            Gio.FileMonitorEvent.CHANGES_DONE_HINT,
            Gio.FileMonitorEvent.CREATED,
        }

        for optional_event in ("MOVED_IN", "RENAMED"):
            if event := getattr(Gio.FileMonitorEvent, optional_event, None):
                handled_events.add(event)

        if event_type not in handled_events:
            return

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
                    img.set_valign(Gtk.Align.CENTER)
                    return img

        icon_name = _resolve_static_icon_name(icon)
        img = Gtk.Image.new_from_icon_name(icon_name)
        img.add_css_class("action-row-prefix-icon")
        img.set_valign(Gtk.Align.CENTER)
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
                    if s == "suggested":
                        b.add_css_class("suggested-action")
                    elif s == "destructive":
                        b.add_css_class("destructive-action")
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
            case "destructive":
                self.btn.add_css_class("destructive-action")
            case "suggested":
                self.btn.add_css_class("suggested-action")
            case _:
                self.btn.add_css_class("default-action")

    def _start_dynamic_poll(self) -> None:
        # Use 'misc' slot for button text polling. File I/O is kept off the GTK thread.
        self._queue_dynamic_state_read()

        with self._state.lock:
            if not self._state.is_destroyed:
                self._state.misc.source_id = GLib.timeout_add_seconds(
                    2,
                    self._update_dynamic_state,
                )

    def _update_dynamic_state(self) -> bool:
        if not self.get_mapped():
            return GLib.SOURCE_CONTINUE

        self._queue_dynamic_state_read()
        return GLib.SOURCE_CONTINUE

    def _queue_dynamic_state_read(self) -> None:
        with self._state.lock:
            if self._state.is_destroyed or self._state.misc.is_running:
                return

            self._state.misc.is_running = True
            self._state.misc.generation += 1
            generation = self._state.misc.generation

        if not _submit_task_safe(lambda: self._read_dynamic_state_async(generation), self._state):
            with self._state.lock:
                if self._state.misc.generation == generation:
                    self._state.misc.is_running = False

    def _read_dynamic_state_async(self, generation: int) -> None:
        value: str | None = None

        try:
            path = Path(str(self.text_file)).expanduser()
            if path.exists():
                value = path.read_text(encoding="utf-8").strip()
        except Exception as e:
            log.debug("Failed to read button dynamic state file %r: %s", self.text_file, e)

        GLib.idle_add(self._apply_dynamic_state, generation, value)

    def _apply_dynamic_state(self, generation: int, value: str | None) -> bool:
        with self._state.lock:
            if self._state.misc.generation == generation:
                self._state.misc.is_running = False

            if self._state.is_destroyed or self._state.misc.generation != generation:
                return GLib.SOURCE_REMOVE

        if value is None:
            return GLib.SOURCE_REMOVE

        text_map = self.text_map if isinstance(self.text_map, dict) else {}
        style_map = self.style_map if isinstance(self.style_map, dict) else {}

        new_label = text_map.get(value, text_map.get("default", self.btn.get_label()))
        if self.btn.get_label() != new_label:
            self.btn.set_label(new_label)

        new_style = style_map.get(value, style_map.get("default", self.base_style))
        self._apply_base_style(str(new_style).lower())

        return GLib.SOURCE_REMOVE

    def _on_button_clicked(self, _button: Gtk.Button) -> None:
        self._trigger_action(self.on_action)

    def _on_multi_clicked(self, _b: Gtk.Button, cfg: dict[str, Any]) -> None:
        if act := cfg.get("on_press"):
            self._trigger_action(act)

    def _trigger_action(self, act: Any) -> None:
        if not isinstance(act, dict):
            return
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
            _perform_redirect(act, self.context)


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
        if self._programmatic_update_event.is_set():
            return False

        if isinstance(self.on_action, dict):
            action_key = "enabled" if state else "disabled"
            if action := self.on_action.get(action_key):
                if isinstance(action, dict) and (cmd := action.get("command")):
                    utility.execute_command(
                        str(cmd).strip(),
                        "Toggle",
                        bool(action.get("terminal", False)),
                    )

        if key := self.properties.get("key"):
            key_str = str(key).strip()
            _submit_setting_save_safe(key_str, state)

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
                            interval, self._on_timeout,
                        )

    def _handle_async_output(self, output: str) -> None:
        """Callback for Gio async loop execution."""
        self._update_label(output.strip() if output else LABEL_NA)

    def _on_timeout(self) -> bool:
        if isinstance(self, Gtk.Widget) and not self.get_mapped():
            return GLib.SOURCE_CONTINUE
        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE
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
                if self._state.is_destroyed:
                    return
            result = self._get_value_text(self.value_config)
        finally:
            with self._state.lock:
                self._state.value.is_running = False

        GLib.idle_add(self._update_label, result)

    def _update_label(self, text: str) -> bool:
        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE
        if self.value_label.get_label() != text:
            self.value_label.set_label(text)
            self.value_label.remove_css_class("dim-label")
        return GLib.SOURCE_REMOVE

    def _get_value_text(self, val: ValueConfig) -> str:
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
                return LABEL_NA if result is None else str(result)

        return LABEL_NA

    def _exec_cmd(self, cmd: str) -> str:
        cmd = cmd.strip()
        if not cmd:
            return LABEL_NA
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
        if not path.strip():
            return LABEL_NA
        try:
            return _expand_path(path.strip()).read_text(encoding="utf-8").strip()
        except OSError:
            return LABEL_NA


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
            value=default_val,
            lower=self.min_val,
            upper=self.max_val,
            step_increment=self.step_val,
            page_increment=self.step_val * 10,
            page_size=0,
        )

        self.slider = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj)
        self.slider.set_valign(Gtk.Align.CENTER)
        self.slider.set_hexpand(False)
        self.slider.set_draw_value(False)
        self.slider.set_size_request(250, -1)
        self.slider.connect("value-changed", self._on_value_changed)
        self.add_suffix(self.slider)

        self._start_value_monitor()

    @staticmethod
    def _format_action_value(value: float) -> str:
        return str(int(value)) if value.is_integer() else f"{value:.15g}"

    def _snap_value(self, value: float) -> float:
        clamped = max(self.min_val, min(value, self.max_val))
        steps = round((clamped - self.min_val) / self.step_val)
        snapped = self.min_val + (steps * self.step_val)
        return max(self.min_val, min(snapped, self.max_val))

    def _apply_value_update(self, new_value: float) -> bool:
        """Push a polled value into the slider, suppressing feedback."""
        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE

        safe_val = self._snap_value(new_value)
        current = self.slider.get_value()
        if math.isclose(current, safe_val, abs_tol=MIN_STEP_VALUE):
            self._last_snapped = safe_val
            return GLib.SOURCE_REMOVE

        self._slider_changing = True
        try:
            self.slider.set_value(safe_val)
            self._last_snapped = safe_val
        finally:
            self._slider_changing = False

        return GLib.SOURCE_REMOVE

    def _on_value_changed(self, scale: Gtk.Scale) -> None:
        if self._slider_changing:
            return

        val = scale.get_value()
        snapped = self._snap_value(val)

        if self._last_snapped is not None and math.isclose(
            snapped,
            self._last_snapped,
            abs_tol=MIN_STEP_VALUE,
        ):
            if not math.isclose(val, snapped, abs_tol=MIN_STEP_VALUE):
                self._slider_changing = True
                try:
                    self.slider.set_value(snapped)
                finally:
                    self._slider_changing = False
            return

        self._last_snapped = snapped

        if not math.isclose(snapped, val, abs_tol=MIN_STEP_VALUE):
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
                SLIDER_DEBOUNCE_MS, self._execute_debounced_action,
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
                value_text = self._format_action_value(value)
                final_cmd = str(cmd).replace("{value}", value_text)
                is_term = bool(self.on_action.get("terminal", False))
                if is_term:
                    utility.execute_command(final_cmd, "Slider", True)
                else:
                    _spawn_command_async(final_cmd)
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
        self._selection_fetch_running = False
        self._selection_fetch_pending = False
        self._selection_fetch_generation = 0
        self._options_fetch_running = False
        self._options_fetch_pending = False
        self._options_fetch_generation = 0

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
            self._queue_options_fetch()

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
        try:
            yield
        finally:
            self._programmatic_update = False

    def _queue_options_fetch(self) -> None:
        with self._state.lock:
            if self._state.is_destroyed:
                return
            if self._options_fetch_running:
                self._options_fetch_pending = True
                return
            self._options_fetch_running = True
            self._options_fetch_pending = False
            self._options_fetch_generation += 1
            generation = self._options_fetch_generation

        if not _submit_task_safe(lambda: self._fetch_options_async(generation), self._state):
            with self._state.lock:
                self._options_fetch_running = False

    def _complete_options_fetch(self) -> None:
        next_generation: int | None = None

        with self._state.lock:
            if self._state.is_destroyed:
                self._options_fetch_running = False
                self._options_fetch_pending = False
                return

            if self._options_fetch_pending:
                self._options_fetch_pending = False
                self._options_fetch_generation += 1
                next_generation = self._options_fetch_generation
            else:
                self._options_fetch_running = False

        if next_generation is not None:
            if not _submit_task_safe(
                lambda: self._fetch_options_async(next_generation),
                self._state,
            ):
                with self._state.lock:
                    self._options_fetch_running = False

    def _queue_selection_fetch(self) -> None:
        with self._state.lock:
            if self._state.is_destroyed:
                return
            if self._selection_fetch_running:
                self._selection_fetch_pending = True
                return
            self._selection_fetch_running = True
            self._selection_fetch_pending = False
            self._selection_fetch_generation += 1
            generation = self._selection_fetch_generation

        if not _submit_task_safe(lambda: self._fetch_selection_async(generation), self._state):
            with self._state.lock:
                self._selection_fetch_running = False

    def _complete_selection_fetch(self) -> None:
        next_generation: int | None = None

        with self._state.lock:
            if self._state.is_destroyed:
                self._selection_fetch_running = False
                self._selection_fetch_pending = False
                return

            if self._selection_fetch_pending:
                self._selection_fetch_pending = False
                self._selection_fetch_generation += 1
                next_generation = self._selection_fetch_generation
            else:
                self._selection_fetch_running = False

        if next_generation is not None:
            if not _submit_task_safe(
                lambda: self._fetch_selection_async(next_generation),
                self._state,
            ):
                with self._state.lock:
                    self._selection_fetch_running = False

    def _fetch_options_async(self, generation: int) -> None:
        cmd = self.properties.get("options_command", "")
        try:
            if not cmd:
                return

            res = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=SUBPROCESS_TIMEOUT_LONG,
            )
            if res.returncode == 0:
                lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
                GLib.idle_add(self._update_options_ui, lines, generation)
        except Exception as e:
            log.error("Options fetch failed: %s", e)
        finally:
            self._complete_options_fetch()

    def _update_options_ui(self, new_options: list[str], generation: int) -> bool:
        with self._state.lock:
            if self._state.is_destroyed or generation != self._options_fetch_generation:
                return GLib.SOURCE_REMOVE

        if new_options != self.options_list:
            self.options_list = new_options
            with self._suppress_change_signal():
                self.set_model(Gtk.StringList.new(self.options_list))
            self._queue_selection_fetch()

        return GLib.SOURCE_REMOVE

    def _start_selection_monitor(self) -> None:
        # SelectionRow uses 'value' slot for selection monitoring
        interval = max(
            1,
            _safe_int(self.properties.get("interval"), DEFAULT_INTERVAL_SECONDS),
        )

        with self._state.lock:
            if self._state.is_destroyed:
                return
            self._state.value.source_id = GLib.timeout_add_seconds(
                interval,
                self._check_selection_tick,
            )

    def _on_map(self, _widget: Gtk.Widget) -> None:
        self._queue_selection_fetch()
        if self.properties.get("options_command"):
            self._queue_options_fetch()

    def _check_selection_tick(self) -> bool:
        if not self.get_mapped():
            return GLib.SOURCE_CONTINUE

        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE

        self._queue_selection_fetch()
        return GLib.SOURCE_CONTINUE

    def _fetch_selection_async(self, generation: int) -> None:
        try:
            if key := self.properties.get("key"):
                try:
                    val = utility.load_setting(str(key).strip(), default="")
                    val_lower = str(val).lower()
                    mapped_val = self.options_map.get(val_lower, str(val))
                    if mapped_val:
                        GLib.idle_add(self._update_selection_ui, mapped_val, generation)
                except Exception:
                    pass
                return

            cmd = self.properties.get("value_command", "")
            if not cmd:
                return

            try:
                res = subprocess.run(
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=SUBPROCESS_TIMEOUT_SHORT,
                )
                if res.returncode == 0:
                    value = res.stdout.strip()
                    value_lower = value.lower()
                    mapped_val = self.options_map.get(value_lower, value)
                    if mapped_val:
                        GLib.idle_add(self._update_selection_ui, mapped_val, generation)
            except Exception:
                pass
        finally:
            self._complete_selection_fetch()

    def _update_selection_ui(self, value: str, generation: int) -> bool:
        with self._state.lock:
            if self._state.is_destroyed or generation != self._selection_fetch_generation:
                return GLib.SOURCE_REMOVE

        if value not in self.options_list:
            if self.properties.get("options_command"):
                self._queue_options_fetch()
            return GLib.SOURCE_REMOVE

        idx = self.options_list.index(value)
        if self.get_selected() != idx:
            with self._suppress_change_signal():
                self.set_selected(idx)

        return GLib.SOURCE_REMOVE

    def _on_selected(self, _row: Adw.ComboRow, _param: GObject.ParamSpec) -> None:
        if self._programmatic_update:
            return
        model = self.get_model()
        if not model:
            return
        idx = self.get_selected()
        if idx >= model.get_n_items():
            return
        item = model.get_string(idx)

        if key := self.properties.get("key"):
            key_str = str(key).strip()
            write_val = self.reverse_map.get(item, item)
            _submit_setting_save_safe(key_str, write_val)

        if isinstance(self.on_action, dict):
            action = self.on_action.get(item)
            if not isinstance(action, dict) and "command" in self.on_action:
                action = self.on_action

            if isinstance(action, dict) and (cmd := action.get("command")):
                safe_value = shlex.quote(item)
                final_cmd = str(cmd).replace("{value}", safe_value)
                utility.execute_command(
                    final_cmd,
                    "Selection",
                    bool(action.get("terminal", False)),
                )

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
        if not text:
            return
        if isinstance(self.on_action, dict) and (cmd := self.on_action.get("command")):
            safe_value = shlex.quote(text)
            final_cmd = str(cmd).replace("{value}", safe_value)
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
            if not isinstance(item, dict):
                continue
            row = self._build_single_row(item)
            if row is not None:
                self.add_row(row)

    def _build_single_row(self, item: dict[str, object]) -> Adw.PreferencesRow | None:
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



class AsyncSelectorRow(DynamicIconMixin, Adw.PreferencesRow):
    """
    A generalized widget matching a Vertical UI paradigm:
    Top: Title & Description
    Bottom: Controls (Refresh + Dropdown + Optional Action Button)
    """
    __gtype_name__ = "DuskyAsyncSelectorRow"

    def __init__(
        self,
        properties: RowProperties,
        on_action: ActionConfig | None = None,
        context: RowContext | None = None,
    ) -> None:
        super().__init__()
        self.set_activatable(False)

        self._state = WidgetState()
        self.properties = properties
        self.on_action: ActionConfig = on_action or {}
        self.context: RowContext = context or {}
        self.config: dict[str, object] = self.context.get("config") or {}
        self.sidebar: Gtk.ListBox | None = self.context.get("sidebar")
        self.toast_overlay: Adw.ToastOverlay | None = self.context.get("toast_overlay")

        self.list_command = str(properties.get("list_command", ""))
        self.display_template = str(properties.get("display_template", "{id}"))
        self.sort_order = str(properties.get("sort", "none")).lower()
        self.auto_refresh = bool(properties.get("auto_refresh", False))
        self.auto_execute = bool(properties.get("auto_execute", False))
        self._programmatic_update = False

        raw_timeout = properties.get("command_timeout", 60.0)
        try:
            command_timeout = float(raw_timeout)
        except (TypeError, ValueError):
            command_timeout = 60.0
        self.command_timeout = command_timeout if command_timeout > 0 else 60.0

        button_text = str(properties.get("button_text", "Execute"))
        button_style = str(properties.get("style", "default")).lower()

        self.json_data: list[dict] = []
        self._fetch_in_progress = False
        self._fetch_process: subprocess.Popen | None = None

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        main_box.set_margin_top(12)
        main_box.set_margin_bottom(12)
        main_box.set_margin_start(12)
        main_box.set_margin_end(12)

        top_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        icon_config = properties.get("icon", DEFAULT_ICON)
        self.icon_widget = self._create_icon_widget(icon_config)
        self.icon_widget.set_valign(Gtk.Align.CENTER)
        top_box.append(self.icon_widget)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text_box.set_valign(Gtk.Align.CENTER)
        text_box.set_hexpand(True)

        title_str = str(properties.get("title", "Unnamed"))
        title_label = Gtk.Label(xalign=0)
        title_label.set_markup(f"<b>{GLib.markup_escape_text(title_str)}</b>")
        title_label.set_wrap(True)
        text_box.append(title_label)

        if sub_str := properties.get("description", ""):
            sub_label = Gtk.Label(label=sub_str, xalign=0)
            sub_label.add_css_class("dim-label")
            sub_label.set_wrap(True)
            text_box.append(sub_label)

        top_box.append(text_box)

        bottom_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        self.refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        self.refresh_btn.set_valign(Gtk.Align.CENTER)
        self.refresh_btn.set_tooltip_text("Load Data")
        self.refresh_btn.connect("clicked", self._on_refresh_clicked)
        self.refresh_btn.add_css_class("flat")

        if self.auto_refresh:
            self.refresh_btn.set_visible(False)

        self.model = Gtk.StringList.new([])
        self.dropdown = Gtk.DropDown(model=self.model)
        self.dropdown.set_valign(Gtk.Align.CENTER)
        self.dropdown.set_hexpand(True)
        self.dropdown.connect("notify::selected", self._on_dropdown_selected)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_dropdown_setup)
        factory.connect("bind", self._on_dropdown_bind)
        self.dropdown.set_factory(factory)

        bottom_box.append(self.refresh_btn)
        bottom_box.append(self.dropdown)

        self.has_action = isinstance(self.on_action, dict) and (
            (self.on_action.get("type") == "exec" and bool(self.on_action.get("command")))
            or (self.on_action.get("type") == "redirect" and bool(self.on_action.get("page")))
        )

        self.action_btn = None
        if self.has_action and not self.auto_execute:
            self.action_btn = Gtk.Button(label=button_text)
            self.action_btn.set_valign(Gtk.Align.CENTER)
            self.action_btn.connect("clicked", self._on_action_clicked)
            self.action_btn.set_sensitive(False)

            if button_style == "destructive":
                self.action_btn.add_css_class("destructive-action")
            elif button_style == "suggested":
                self.action_btn.add_css_class("suggested-action")
            else:
                self.action_btn.add_css_class("default-action")

            bottom_box.append(self.action_btn)

        main_box.append(top_box)
        main_box.append(bottom_box)
        self.set_child(main_box)

        self.connect("map", self._on_map)

        if _is_dynamic_icon(icon_config) and isinstance(icon_config, dict):
            self._start_icon_update_loop(icon_config)

    @contextmanager
    def _suppress_change_signal(self):
        """Context manager to prevent dropdown signals from firing during UI updates."""
        self._programmatic_update = True
        try:
            yield
        finally:
            self._programmatic_update = False

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

    def _on_dropdown_setup(self, factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
        label = Gtk.Label(xalign=0)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        list_item.set_child(label)

    def _on_dropdown_bind(self, factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
        label = list_item.get_child()
        string_obj = list_item.get_item()
        if label and string_obj:
            label.set_text(string_obj.get_string())

    def _on_map(self, _widget: Gtk.Widget) -> None:
        if self.auto_refresh and not self.json_data and not self._fetch_in_progress:
            self._on_refresh_clicked(self.refresh_btn)

    def _on_refresh_clicked(self, _btn: Gtk.Button) -> None:
        if not self.list_command or self._fetch_in_progress:
            return

        self._fetch_in_progress = True
        self.refresh_btn.set_sensitive(False)

        if self.action_btn:
            self.action_btn.set_sensitive(False)

        if _submit_task_safe(self._fetch_data_async, self._state):
            return

        self._fetch_in_progress = False
        self.refresh_btn.set_sensitive(True)

        if self.action_btn:
            self.action_btn.set_sensitive(bool(self.json_data) and self.has_action)

        utility.toast(self.toast_overlay, "✖ Failed to queue data fetch", 4)

    def _signal_process_group(self, proc: subprocess.Popen, sig: signal.Signals) -> None:
        if proc.poll() is not None:
            return

        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        except Exception:
            with suppress(Exception):
                proc.send_signal(sig)

    def _kill_and_reap_process(self, proc: subprocess.Popen) -> None:
        if proc.poll() is None:
            self._signal_process_group(proc, signal.SIGTERM)

        try:
            proc.communicate(timeout=1)
            return
        except subprocess.TimeoutExpired:
            self._signal_process_group(proc, signal.SIGKILL)
        except Exception:
            return

        with suppress(Exception):
            proc.communicate(timeout=1)

    def _cancel_active_fetch(self) -> None:
        with self._state.lock:
            proc = self._fetch_process

        if proc is None or proc.poll() is not None:
            return

        self._signal_process_group(proc, signal.SIGKILL)

    def _fetch_data_async(self) -> None:
        with self._state.lock:
            if self._state.is_destroyed:
                return

        proc: subprocess.Popen | None = None

        try:
            argv = (
                shlex.split(self.list_command)
                if not _SHELL_METACHAR.intersection(self.list_command)
                else ["/bin/sh", "-c", self.list_command]
            )

            proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )

            with self._state.lock:
                self._fetch_process = proc
                destroyed = self._state.is_destroyed

            if destroyed:
                self._kill_and_reap_process(proc)
                return

            try:
                stdout, stderr = proc.communicate(timeout=self.command_timeout)
            except subprocess.TimeoutExpired:
                self._kill_and_reap_process(proc)
                raise TimeoutError(f"Command timed out after {self.command_timeout:g} seconds")

            if proc.returncode != 0:
                raise subprocess.CalledProcessError(
                    proc.returncode,
                    argv,
                    output=stdout,
                    stderr=stderr,
                )

            output = stdout.strip()
            parsed_data = json.loads(output) if output else []

            if not isinstance(parsed_data, list):
                raise ValueError("Expected a JSON array.")
            if parsed_data and not all(isinstance(item, dict) for item in parsed_data):
                raise ValueError("Expected every JSON array item to be a dictionary object.")

            GLib.idle_add(self._update_ui, parsed_data)
        except Exception as e:
            log.error("AsyncSelector fetch failed: %s", e)
            GLib.idle_add(self._on_fetch_failed)
        finally:
            if proc is not None:
                with self._state.lock:
                    if self._fetch_process is proc:
                        self._fetch_process = None

    def _update_ui(self, parsed_data: list[dict]) -> bool:
        self._fetch_in_progress = False
        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE

        if self.sort_order == "reverse":
            self.json_data = list(reversed(parsed_data))
        else:
            self.json_data = list(parsed_data)

        strings: list[str] = []

        class SafeDict(dict):
            def __missing__(self, key):
                return f"{{{key}}}"

        for item in self.json_data:
            try:
                label = self.display_template.format_map(SafeDict(item))
                strings.append(label)
            except Exception:
                strings.append("Format Error")

        if not strings:
            strings.append("(No Data)")
            self.json_data = []

        with self._suppress_change_signal():
            self.model.splice(0, self.model.get_n_items(), strings)

        self.refresh_btn.set_sensitive(True)
        if self.action_btn:
            self.action_btn.set_sensitive(bool(self.json_data) and self.has_action)

        return GLib.SOURCE_REMOVE

    def _on_fetch_failed(self) -> bool:
        self._fetch_in_progress = False
        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE

        self.json_data.clear()

        with self._suppress_change_signal():
            self.model.splice(0, self.model.get_n_items(), [])

        if self.auto_refresh:
            self.refresh_btn.set_visible(True)

        self.refresh_btn.set_sensitive(True)
        if self.action_btn:
            self.action_btn.set_sensitive(False)

        if self.toast_overlay:
            utility.toast(self.toast_overlay, "✖ Failed to fetch data", 4)

        return GLib.SOURCE_REMOVE

    def _on_dropdown_selected(self, _dropdown: Gtk.DropDown, _param: GObject.ParamSpec) -> None:
        """Triggered whenever the dropdown selection changes."""
        if self._programmatic_update or not self.has_action or not self.auto_execute:
            return

        selected_idx = self.dropdown.get_selected()
        if not self.json_data or selected_idx == Gtk.INVALID_LIST_POSITION or selected_idx >= len(self.json_data):
            return

        self._on_action_clicked()

    def _on_action_clicked(self, _btn: Gtk.Button | None = None) -> None:
        selected_idx = self.dropdown.get_selected()
        if selected_idx == Gtk.INVALID_LIST_POSITION or selected_idx >= len(self.json_data):
            return

        selected_dict = self.json_data[selected_idx]

        if not isinstance(self.on_action, dict):
            return

        if self.on_action.get("type") == "exec" and (cmd_template := self.on_action.get("command")):
            class SafeDict(dict):
                def __missing__(self, key):
                    return f"{{{key}}}"

            title = str(self.properties.get("title", "Action"))

            try:
                final_cmd = cmd_template.format_map(SafeDict(selected_dict))
            except Exception as e:
                log.error("AsyncSelector action formatting failed: %s", e)
                utility.toast(self.toast_overlay, f"✖ Failed: {title}", 4)
                return

            is_term = bool(self.on_action.get("terminal", False))

            success = utility.execute_command(final_cmd, title, is_term)
            msg = f"{'▶ Executed' if success else '✖ Failed'}: {title}"
            utility.toast(self.toast_overlay, msg, 2 if success else 4)

        elif self.on_action.get("type") == "redirect":
            _perform_redirect(self.on_action, self.context)

    def do_unroot(self) -> None:
        sources = self._state.mark_destroyed_and_get_sources()
        self._cancel_active_fetch()
        _batch_source_remove(*sources)
        Adw.PreferencesRow.do_unroot(self)

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
        # Fetch immediately to bypass the initial get_mapped() delay.
        self._queue_dynamic_state_fetch()

        with self._state.lock:
            if not self._state.is_destroyed:
                self._state.value.source_id = GLib.timeout_add_seconds(
                    MONITOR_INTERVAL_SECONDS,
                    self._dynamic_state_tick,
                )

    def _dynamic_state_tick(self) -> bool:
        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE

        if not self.get_mapped():
            return GLib.SOURCE_CONTINUE

        self._queue_dynamic_state_fetch()
        return GLib.SOURCE_CONTINUE

    def _queue_dynamic_state_fetch(self) -> None:
        with self._state.lock:
            if self._state.is_destroyed or self._state.value.is_running:
                return

            self._state.value.is_running = True
            self._state.value.generation += 1
            generation = self._state.value.generation

        if not _submit_task_safe(lambda: self._fetch_dynamic_state_async(generation), self._state):
            with self._state.lock:
                if self._state.value.generation == generation:
                    self._state.value.is_running = False

    def _fetch_dynamic_state_async(self, generation: int) -> None:
        """Runs in the background thread pool."""
        val: str | None = None

        try:
            if self.text_file:
                path = _expand_path(self.text_file)
                if path.exists():
                    val = path.read_text(encoding="utf-8").strip()
        except Exception as e:
            log.debug("Failed to read dynamic state file %r: %s", self.text_file, e)

        GLib.idle_add(self._apply_dynamic_state_ui, generation, val)

    def _apply_dynamic_state_ui(self, generation: int, val: str | None) -> bool:
        """Runs on the GTK main thread."""
        with self._state.lock:
            if self._state.value.generation == generation:
                self._state.value.is_running = False

            if self._state.is_destroyed or self._state.value.generation != generation:
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
            if self._state.is_destroyed:
                return
            self._state.misc.source_id = GLib.timeout_add_seconds(
                DEFAULT_INTERVAL_SECONDS,
                self._check_badge_tick,
                path_str,
            )

    def _check_badge_tick(self, path_str: str) -> bool:
        if not self.get_mapped():
            return GLib.SOURCE_CONTINUE

        with self._state.lock:
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE

        self._queue_badge_fetch(path_str)
        return GLib.SOURCE_CONTINUE

    def _queue_badge_fetch(self, path_str: str) -> None:
        with self._state.lock:
            if self._state.is_destroyed or self._state.misc.is_running:
                return

            self._state.misc.is_running = True
            self._state.misc.generation += 1
            generation = self._state.misc.generation

        if not _submit_task_safe(lambda: self._fetch_badge_async(path_str, generation), self._state):
            with self._state.lock:
                if self._state.misc.generation == generation:
                    self._state.misc.is_running = False

    def _fetch_badge_async(self, path_str: str, generation: int) -> None:
        count_text: str | None = None

        try:
            path = _expand_path(path_str)
            if path.exists():
                content = path.read_text(encoding="utf-8").strip()
                if content.isdigit() and int(content) > 0:
                    count_text = content
        except Exception:
            pass

        GLib.idle_add(self._update_badge_ui, generation, count_text)

    def _update_badge_ui(self, generation: int, text: str | None) -> bool:
        with self._state.lock:
            if self._state.misc.generation == generation:
                self._state.misc.is_running = False

            if self._state.is_destroyed or self._state.misc.generation != generation:
                return GLib.SOURCE_REMOVE

        if self.badge_label:
            if text:
                self.badge_label.set_label(text)
                self.badge_label.set_visible(True)
            else:
                self.badge_label.set_visible(False)

        return GLib.SOURCE_REMOVE

    def _on_clicked(self, _button: Gtk.Button) -> None:
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
                    utility.toast(self.toast_overlay, "▶ Launched" if success else "✖ Failed")
            case "redirect":
                _perform_redirect(self.on_action, self.context)


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
            if self._state.is_destroyed:
                return GLib.SOURCE_REMOVE
        if new_state != self.is_active:
            self._set_visual(new_state)
        return GLib.SOURCE_REMOVE

    def _set_visual(self, state: bool) -> None:
        self.is_active = state
        self.status_lbl.set_label(STATE_ON if state else STATE_OFF)
        if state:
            self.add_css_class("toggle-active")
        else:
            self.remove_css_class("toggle-active")

    def _on_clicked(self, _button: Gtk.Button) -> None:
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
            key_str = str(key).strip()
            _submit_setting_save_safe(key_str, new_state)

        return False
