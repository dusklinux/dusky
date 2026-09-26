#!/usr/bin/env python3
"""
Dusky Control Center: a GTK4/Libadwaita launcher for Dusky settings.
"""

from __future__ import annotations

import logging
import re
import shlex
import signal
import sys
import threading
import traceback
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import (
    Any,
    Final,
    Literal,
    NotRequired,
    TypedDict,
)

# =============================================================================
# VERSION CHECK
# =============================================================================
# Safe check to prevent systemd service restart loops when running headless
import os
if "--validate" in sys.argv:
    from lib.config_schema import validate_file

    try:
        position = sys.argv.index("--validate")
        config_file = Path(sys.argv[position + 1]) if position + 1 < len(sys.argv) else Path(__file__).with_name("dusky_config.toml")
        validate_file(config_file)
    except (OSError, UnicodeError, ValueError, TypeError) as error:
        sys.stderr.write(f"dusky-control-center: invalid config: {error}\n")
        sys.exit(2)
    print(f"Valid configuration: {config_file}")
    sys.exit(0)

if not os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("DISPLAY"):
    sys.stderr.write("dusky-control-center: error: WAYLAND_DISPLAY and DISPLAY are not set. Cannot run GUI application.\n")
    sys.exit(5)

if sys.version_info < (3, 14, 5):
    sys.exit("[FATAL] Python 3.14.5+ is required.")

# =============================================================================
# LOGGING CONFIGURATION
# =============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger(__name__)


# =============================================================================
# CACHE CONFIGURATION
# =============================================================================
def _setup_cache() -> None:
    """Configure pycache directory following XDG spec."""
    import os

    try:
        xdg_cache_env = os.environ.get("XDG_CACHE_HOME", "").strip()
        xdg_cache = Path(xdg_cache_env) if xdg_cache_env else Path.home() / ".cache"
        cache_dir = xdg_cache / "duskycc"
        cache_dir.mkdir(parents=True, exist_ok=True)
        sys.pycache_prefix = str(cache_dir)
    except OSError as e:
        log.warning("Could not set custom pycache location: %s", e)


_setup_cache()

# =============================================================================
# IMPORTS & PRE-FLIGHT
# =============================================================================
try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    gi.require_version("GLibUnix", "2.0")
except (ImportError, ValueError) as error:
    sys.exit(f"dusky-control-center: GTK4/libadwaita Python bindings unavailable: {error}")

import lib.utility as utility
from lib.config_schema import validate_config

utility.preflight_check()
from gi.repository import Adw, Gdk, Gio, GLib, GLibUnix, Gtk, Pango

import lib.rows as rows


# =============================================================================
# CONSTANTS
# =============================================================================
APP_ID: Final[str] = "com.github.dusky.controlcenter"
APP_TITLE: Final[str] = "Dusky Control Center"
CONFIG_FILENAME: Final[str] = "dusky_config.toml"
CSS_FILENAME: Final[str] = "dusky_style.css"
SCRIPT_DIR: Final[Path] = Path(__file__).resolve().parent

# UI Layout Constants
WINDOW_DEFAULT_WIDTH: Final[int] = 639
WINDOW_DEFAULT_HEIGHT: Final[int] = 800
SIDEBAR_MIN_WIDTH: Final[int] = 154
SIDEBAR_MAX_WIDTH: Final[int] = 154
SIDEBAR_WIDTH_FRACTION: Final[float] = 0.25

# Page Identifiers
PAGE_PREFIX: Final[str] = "page-"
SEARCH_PAGE_ID: Final[str] = "search-results"
ERROR_PAGE_ID: Final[str] = "error-state"
EMPTY_PAGE_ID: Final[str] = "empty-state"

# Behavior
SEARCH_DEBOUNCE_MS: Final[int] = 200
SEARCH_MAX_RESULTS: Final[int] = 50
DEFAULT_TOAST_TIMEOUT: Final[int] = 2

# Icons
ICON_SYSTEM: Final[str] = "emblem-system-symbolic"
ICON_SEARCH: Final[str] = "system-search-symbolic"
ICON_ERROR: Final[str] = "dialog-error-symbolic"
ICON_EMPTY: Final[str] = "document-open-symbolic"
ICON_WARNING: Final[str] = "dialog-warning-symbolic"
ICON_DEFAULT: Final[str] = "application-x-executable-symbolic"
ICON_SIDEBAR_TOGGLE: Final[str] = "sidebar-show-symbolic"


# =============================================================================
# TYPE DEFINITIONS (Strict)
# =============================================================================
class ItemType(StrEnum):
    """Valid item types in config."""
    BUTTON = "button"
    TOGGLE = "toggle"
    LABEL = "label"
    SLIDER = "slider"
    SPIN = "spin"
    SELECTION = "selection"
    ENTRY = "entry"
    SECRET = "secret"
    MULTI_TEXT = "multi_text"
    KEYBIND = "keybind"
    COLOR = "color"
    PATH = "path"
    NAVIGATION = "navigation"
    WARNING_BANNER = "warning_banner"
    TOGGLE_CARD = "toggle_card"
    GRID_CARD = "grid_card"
    EXPANDER = "expander"
    DIRECTORY_GENERATOR = "directory_generator"
    FILE_GENERATOR = "file_generator"
    ASYNC_SELECTOR = "async_selector"
    FLAG_GROUP = "flag_group"
    SERVICE = "service"
    SERVICE_CARD = "service_card"


class SectionType(StrEnum):
    """Valid section types."""
    SECTION = "section"
    GRID_SECTION = "grid_section"


class ItemProperties(TypedDict, total=False):
    """Properties for UI items."""
    title: str
    description: str
    icon: str
    message: str
    key: str
    key_inverse: bool
    save_as_int: bool
    style: str
    button_text: str
    min: float
    max: float
    step: float
    default: float
    mode: str
    debounce: bool
    options: list[Any]
    exclusive: bool
    options_map: dict[str, str]
    placeholder: str
    path: str
    sort: str
    auto_refresh: bool
    display_max_length: int
    list_command: str
    display_template: str
    hyprland_event: str


class ConfigItem(TypedDict, total=False):
    """A single item in the configuration."""
    type: str
    properties: ItemProperties
    on_press: dict[str, Any] | None
    on_toggle: dict[str, Any] | None
    on_change: dict[str, Any] | None
    on_action: dict[str, Any] | None
    layout: list[Any]
    items: list[Any]
    item_template: dict[str, Any]
    value: dict[str, Any] | None


class ConfigSection(TypedDict, total=False):
    """A section containing items."""
    type: str
    properties: ItemProperties
    items: list[ConfigItem]


class ConfigPage(TypedDict):
    """A navigation page (required keys)."""
    id: NotRequired[str]
    title: str
    icon: NotRequired[str]
    layout: NotRequired[list[ConfigSection]]


class AppConfig(TypedDict):
    """Root configuration object."""
    pages: list[ConfigPage]


class RowContext(TypedDict):
    """Shared context passed to row builders."""
    stack: Adw.ViewStack | None
    config: AppConfig
    sidebar: Gtk.ListBox | None
    toast_overlay: Adw.ToastOverlay | None
    nav_view: Adw.NavigationView | None
    builder_func: Callable[..., Adw.NavigationPage] | None
    row_builder: Callable[..., Adw.PreferencesRow] | None
    path: list[str]


class ConfigLoadResult(TypedDict):
    """Result from config loading operation."""
    success: bool
    config: AppConfig
    css: str
    error: str | None


@dataclass(slots=True, frozen=True)
class SearchHit:
    title: str
    description: str
    icon_name: str
    page_idx: int
    nav_path: tuple[str, ...]
    unique_id: str
    score: int = 0


def _fuzzy_subsequence(needle: str, haystack: str) -> bool:
    """Return True if all chars of *needle* appear in *haystack* in order."""
    it = iter(haystack)
    return all(char in it for char in needle)


@dataclass(slots=True)
class ApplicationState:
    """
    Mutable application state container.
    All mutations occur on the main GTK thread via GLib.idle_add,
    eliminating the need for explicit locking in the main controller.
    """
    config: AppConfig = field(default_factory=lambda: {"pages": []})
    css_content: str = ""
    last_visible_page: str | None = None
    debounce_source_id: int = 0
    config_error: str | None = None


class DuskyControlCenter(Adw.Application):
    """
    Main Application Controller.

    Manages the application lifecycle, UI construction, hot-reload functionality,
    and search capabilities for the Dusky Control Center.
    """

    def __init__(self) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )

        self._state = ApplicationState()
        self._init_widget_refs()
        self._css_provider: Gtk.CssProvider | None = None
        self._display: Gdk.Display | None = None
        self._window: Adw.Window | None = None
        self._reload_running = False
        self._reload_queued = False
        self._directory_generator_cache: dict[int, tuple[ConfigItem, ...]] = {}
        self._file_generator_cache: dict[int, tuple[ConfigItem, ...]] = {}

    def _init_widget_refs(self) -> None:
        """Initialize or reset all widget references to None."""
        self._sidebar_list: Gtk.ListBox | None = None
        self._stack: Adw.ViewStack | None = None
        self._toast_overlay: Adw.ToastOverlay | None = None
        self._search_bar: Gtk.SearchBar | None = None
        self._search_entry: Gtk.SearchEntry | None = None
        self._search_btn: Gtk.ToggleButton | None = None
        self._search_page: Adw.NavigationPage | None = None
        self._search_results_group: Adw.PreferencesGroup | None = None
        self._split_view: Adw.OverlaySplitView | None = None

    # ─────────────────────────────────────────────────────────────────────────
    # LIFECYCLE HOOKS
    # ─────────────────────────────────────────────────────────────────────────
    def do_startup(self) -> None:
        """
        GTK Startup hook.
        PRE-LOAD LOGIC: Initialize resources and build UI hidden to ensure instant startup.
        """
        Adw.Application.do_startup(self)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.DEFAULT)

        self.hold()
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda *_: self.quit())

        result = self._load_config_and_css_sync()
        self._state.config = result["config"]
        self._state.css_content = result["css"]
        self._state.config_error = result["error"]

        try:
            self._apply_css()
        except ValueError as error:
            log.error("%s", error)
        self._build_ui()

        if self._window:
            self._window.realize()
            self._window.set_visible(False)

    def do_activate(self) -> None:
        """
        Application entry point.
        DAEMON LOGIC: Window is pre-built in do_startup. Toggle visibility.
        """
        if self._window:
            if self._window.get_visible():
                self._window.set_visible(False)
            else:
                self._window.present()

    def do_shutdown(self) -> None:
        """Cleanup resources on application exit."""
        self._cancel_debounce()
        if self._window:
            self._window.destroy()
        utility.flush_settings()
        self._remove_css_provider()
        self._directory_generator_cache.clear()
        self._file_generator_cache.clear()
        Adw.Application.do_shutdown(self)

    # ─────────────────────────────────────────────────────────────────────────
    # RESOURCE MANAGEMENT
    # ─────────────────────────────────────────────────────────────────────────
    def _cancel_debounce(self) -> None:
        """Cancel any pending search debounce timer."""
        if self._state.debounce_source_id > 0:
            GLib.source_remove(self._state.debounce_source_id)
            self._state.debounce_source_id = 0

    def _remove_css_provider(self) -> None:
        """Remove CSS provider from display to prevent memory leaks."""
        if self._css_provider is not None and self._display is not None:
            try:
                Gtk.StyleContext.remove_provider_for_display(
                    self._display,
                    self._css_provider,
                )
            except Exception as e:
                log.debug("CSS provider removal warning: %s", e)
        self._css_provider = None

    # ─────────────────────────────────────────────────────────────────────────
    # CONFIG I/O
    # ─────────────────────────────────────────────────────────────────────────
    def _load_config_and_css_sync(self) -> ConfigLoadResult:
        """
        Synchronous load for initial startup.

        Returns:
            ConfigLoadResult with config, css, success status, and any error message.
        """
        config, config_error = self._do_load_config()
        try:
            css = self._do_load_css()
        except (OSError, UnicodeError) as error:
            log.warning("Cannot load CSS: %s", error)
            css = ""
            config_error = config_error or f"CSS read error: {error}"

        return {
            "success": config_error is None,
            "config": config,
            "css": css,
            "error": config_error,
        }

    def _do_load_config(self) -> tuple[AppConfig, str | None]:
        """
        Safely load and validate the configuration file.

        Returns:
            Tuple of (config dict, error message or None)
        """
        config_path = SCRIPT_DIR / CONFIG_FILENAME

        try:
            loaded = utility.load_config(config_path)
            match loaded:
                case {"pages": list()}:
                    validate_config(loaded)
                    return loaded, None # type: ignore
                case {"pages": _}:
                    return {"pages": []}, "'pages' must be a list"
                case dict():
                    return {"pages": []}, "Config missing required 'pages' key"
                case _:
                    return {"pages": []}, f"Config is not a dictionary (got {type(loaded).__name__})"

        except FileNotFoundError:
            return {"pages": []}, f"Config file not found: {config_path}"
        except Exception as e:
            error_detail = "".join(traceback.format_exception_only(type(e), e)).strip()
            return {"pages": []}, f"Config parse error: {error_detail}"

    def _do_load_css(self) -> str:
        """
        Load CSS. A missing optional stylesheet is empty; other read errors fail reload.

        Returns:
            CSS content string, or empty string on failure.
        """
        css_path = SCRIPT_DIR / CSS_FILENAME
        try:
            return css_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            log.info("No custom CSS file found at: %s", css_path)
            return ""

    def _apply_css(self) -> None:
        """Apply loaded CSS without discarding the last working provider on parse failure."""
        if not self._state.css_content:
            self._remove_css_provider()
            self._display = Gdk.Display.get_default()
            return

        display = Gdk.Display.get_default()
        if display is None:
            log.warning("No default display available for CSS")
            return

        provider = Gtk.CssProvider()
        errors: list[str] = []
        def on_parse_error(_provider, _section, error):
            if error.domain == "gtk-css-parser-error-quark":
                errors.append(error.message)
        provider.connect("parsing-error", on_parse_error)
        try:
            provider.load_from_string(self._state.css_content)
        except GLib.Error as e:
            log.error("CSS parsing failed: %s", e.message)
            return

        if errors:
            raise ValueError("CSS parsing failed: " + "; ".join(errors))

        old_provider = self._css_provider
        old_display = self._display

        Gtk.StyleContext.add_provider_for_display(
            display,
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_USER + 10,
        )

        self._css_provider = provider
        self._display = display

        if old_provider is not None and old_display is not None:
            try:
                Gtk.StyleContext.remove_provider_for_display(old_display, old_provider)
            except Exception as e:
                log.debug("CSS provider removal warning: %s", e)

    def _get_context(
        self,
        nav_view: Adw.NavigationView | None = None,
        builder_func: Callable[..., Adw.NavigationPage] | None = None,
        path: list[str] | None = None,
    ) -> RowContext:
        """
        Construct the shared context dictionary for child widget builders.
        """
        return {
            "stack": self._stack,
            "config": self._state.config,
            "sidebar": self._sidebar_list,
            "toast_overlay": self._toast_overlay,
            "nav_view": nav_view,
            "builder_func": builder_func,
            "row_builder": self._build_item_row,
            "path": path or [],
        }

    # ─────────────────────────────────────────────────────────────────────────
    # UI CONSTRUCTION
    # ─────────────────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        """Construct and present the main application window."""
        self._window = Adw.Window(application=self, title=APP_TITLE)
        self._window.set_default_size(WINDOW_DEFAULT_WIDTH, WINDOW_DEFAULT_HEIGHT)
        self._window.set_size_request(520, 460)
        self._window.connect("close-request", self._on_close_request)

        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self._window.add_controller(key_ctrl)

        self._toast_overlay = Adw.ToastOverlay()
        utility.register_toast_overlay(self._toast_overlay)

        self._split_view = Adw.OverlaySplitView()
        self._split_view.set_min_sidebar_width(SIDEBAR_MIN_WIDTH)
        self._split_view.set_max_sidebar_width(SIDEBAR_MAX_WIDTH)
        self._split_view.set_sidebar_width_fraction(SIDEBAR_WIDTH_FRACTION)

        self._split_view.set_sidebar(self._create_sidebar())
        self._split_view.set_content(self._create_content_panel())

        self._toast_overlay.set_child(self._split_view)
        self._window.set_content(self._toast_overlay)

        self._create_search_page()

        if self._state.config_error:
            self._show_error_state(self._state.config_error)
        elif not self._state.config.get("pages"):
            self._show_empty_state()
        else:
            self._populate_pages()

    def _on_close_request(self, window: Adw.Window) -> bool:
        """
        Intercept window close. Return True to prevent destruction.
        Hide window and suspend all background activity to achieve zero-CPU idle.
        """
        window.set_visible(False)
        return True

    def _on_key_pressed(
        self,
        controller: Gtk.EventControllerKey,
        keyval: int,
        keycode: int,
        state: Gdk.ModifierType,
    ) -> bool:
        """Handle global keyboard shortcuts."""
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)

        match (ctrl, keyval):
            case (True, Gdk.KEY_r):
                self._reload_app_async()
                return True
            case (True, Gdk.KEY_f):
                self._activate_search()
                return True
            case (True, Gdk.KEY_q):
                self.quit()
                return True
            case (False, Gdk.KEY_Escape):
                if self._search_bar and self._search_bar.get_search_mode():
                    self._deactivate_search()
                    return True
        return False

    def _create_sidebar_toggle_button(self) -> Gtk.Button:
        """Create a button to toggle the sidebar visibility/overlay."""
        btn = Gtk.Button(icon_name=ICON_SIDEBAR_TOGGLE)
        btn.set_tooltip_text("Toggle Sidebar")
        btn.connect("clicked", self._on_toggle_sidebar)
        return btn

    def _on_toggle_sidebar(self, _btn: Gtk.Button) -> None:
        """Toggle the sidebar visibility using OverlaySplitView logic."""
        if self._split_view:
            self._split_view.set_show_sidebar(not self._split_view.get_show_sidebar())

    # ─────────────────────────────────────────────────────────────────────────
    # ASYNC HOT RELOAD
    # ─────────────────────────────────────────────────────────────────────────
    def _reload_app_async(self) -> None:
        """
        Initiate hot reload with background I/O.

        Reload requests are coalesced so repeated Ctrl+R presses don't race
        or apply stale results out of order.
        """
        if self._reload_running:
            self._reload_queued = True
            return

        self._reload_running = True
        log.info("Hot Reload Initiated...")

        current_page = self._get_current_page_index()
        old_pages = self._state.config.get("pages", [])
        current_page_id = old_pages[current_page].get("id") if current_page is not None and current_page < len(old_pages) else None
        old_config = self._state.config
        old_css = self._state.css_content
        old_error = self._state.config_error

        def background_load() -> ConfigLoadResult:
            config, error = self._do_load_config()
            css = self._do_load_css()
            return {
                "success": error is None,
                "config": config,
                "css": css,
                "error": error,
            }

        def on_complete(
            result: ConfigLoadResult | None,
            error: BaseException | None,
        ) -> None:
            try:
                if error is not None:
                    log.error("Reload thread error: %s", error, exc_info=True)
                    self._toast(f"Reload Failed: {error}", 4)
                    return

                if result is None:
                    self._toast("Reload Failed: No result", 3)
                    return

                if not result["success"]:
                    self._toast(f"Reload Failed: {result['error']}", 4)
                    return

                self._state.config = result["config"]
                self._state.css_content = result["css"]
                self._state.config_error = result["error"]

                self._apply_css()
                new_pages = self._state.config.get("pages", [])
                restore_index = next((i for i, page in enumerate(new_pages) if page.get("id") == current_page_id), current_page)
                self._clear_and_rebuild_ui(restore_index)

                if result["error"]:
                    self._toast(f"Config Error: {result['error'][:50]}...", 4)
                else:
                    self._toast("Configuration Reloaded 🚀")

            except Exception as rebuild_error:
                log.error("UI Rebuild failed: %s", rebuild_error, exc_info=True)

                self._state.config = old_config
                self._state.css_content = old_css
                self._state.config_error = old_error

                try:
                    self._apply_css()
                    self._clear_and_rebuild_ui(current_page)
                except Exception:
                    log.critical("Rollback UI rebuild failed", exc_info=True)

                self._toast("Reload Failed: UI rebuild error", 3)

            finally:
                self._reload_running = False
                if self._reload_queued:
                    self._reload_queued = False
                    GLib.idle_add(self._reload_app_async)

        self._run_in_background(background_load, on_complete)

    def _get_current_page_index(self) -> int | None:
        """Get the index of the currently selected sidebar row."""
        if self._sidebar_list is None:
            return None
        row = self._sidebar_list.get_selected_row()
        return row.get_index() if row else None

    def _run_in_background(
        self,
        task: Callable[[], Any],
        callback: Callable[[Any, BaseException | None], None],
    ) -> None:
        """
        Execute a task in a background thread and callback on main thread.
        """
        def wrapper() -> None:
            result: Any = None
            error: BaseException | None = None
            try:
                result = task()
            except BaseException as e:
                error = e
                log.error("Background task failed: %s", e, exc_info=True)

            GLib.idle_add(callback, result, error)

        thread = threading.Thread(target=wrapper, daemon=True, name="reload-worker")
        thread.start()

    def _clear_and_rebuild_ui(self, restore_page_index: int | None) -> None:
        """
        Clear existing UI elements and rebuild from current config.
        """
        self._deactivate_search()
        self._directory_generator_cache.clear()
        self._file_generator_cache.clear()
        self._state.last_visible_page = None

        self._search_page = None
        self._search_results_group = None

        self._clear_sidebar()
        self._clear_stack()

        self._create_search_page()

        if self._state.config_error:
            self._show_error_state(self._state.config_error)
        elif not self._state.config.get("pages"):
            self._show_empty_state()
        else:
            self._populate_pages(restore_page_index)

    def _clear_sidebar(self) -> None:
        """Remove all rows from the sidebar."""
        if self._sidebar_list is None:
            return
        while (row := self._sidebar_list.get_row_at_index(0)) is not None:
            self._sidebar_list.remove(row)

    def _clear_stack(self) -> None:
        """Remove all children from the content stack."""
        if self._stack is None:
            return
        while (child := self._stack.get_first_child()) is not None:
            self._stack.remove(child)

    # ─────────────────────────────────────────────────────────────────────────
    # SEARCH FUNCTIONALITY
    # ─────────────────────────────────────────────────────────────────────────
    def _generate_widget_id(self, item: object) -> str:
        """Generate a runtime-unique widget ID for a config item."""
        return f"cfg_{id(item):x}"

    def _highlight_widget_by_id(self, parent: Gtk.Widget, unique_id: str) -> Literal[False]:
        """Find the widget by its ID, auto-scroll to it, and trigger a visual pulse."""
        widget = self._find_widget_by_name(parent, unique_id)
        if widget:
            ancestor = widget.get_parent()
            while ancestor is not None:
                if isinstance(ancestor, Adw.ExpanderRow):
                    ancestor.set_expanded(True)
                ancestor = ancestor.get_parent()
            widget.grab_focus()
            widget.add_css_class("highlight-pulse")
            GLib.timeout_add(
                1500,
                lambda w=widget: (w.remove_css_class("highlight-pulse"), GLib.SOURCE_REMOVE)[1],
            )
        return GLib.SOURCE_REMOVE

    def _find_widget_by_name(self, parent: Gtk.Widget, name: str) -> Gtk.Widget | None:
        """Recursively scan the GTK widget tree for a specific name."""
        if parent.get_name() == name:
            return parent
        child = parent.get_first_child()
        while child is not None:
            found = self._find_widget_by_name(child, name)
            if found:
                return found
            child = child.get_next_sibling()
        return None

    def _create_search_page(self) -> None:
        """Create the search results page in the stack."""
        if self._stack is None:
            return

        self._search_page = Adw.NavigationPage(title="Search", tag="search")

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.pack_start(self._create_sidebar_toggle_button())
        toolbar.add_top_bar(header)

        pref_page = Adw.PreferencesPage()
        self._search_results_group = Adw.PreferencesGroup(title="Search Results")
        pref_page.add(self._search_results_group)

        toolbar.set_content(pref_page)
        self._search_page.set_child(toolbar)

        self._stack.add_named(self._search_page, SEARCH_PAGE_ID)

    def _activate_search(self) -> None:
        """Activate the search bar and focus the entry."""
        if self._stack:
            current = self._stack.get_visible_child_name()
            if current and current != SEARCH_PAGE_ID:
                self._state.last_visible_page = current

        if self._search_bar:
            self._search_bar.set_search_mode(True)
        if self._search_btn:
            self._search_btn.set_active(True)
        if self._search_entry:
            self._search_entry.grab_focus()

    def _deactivate_search(self) -> None:
        """Deactivate search and restore the previous page."""
        self._cancel_debounce()

        if self._search_bar:
            self._search_bar.set_search_mode(False)
        if self._search_btn:
            self._search_btn.set_active(False)
        if self._search_entry:
            self._search_entry.set_text("")
            self._cancel_debounce()

        if self._state.last_visible_page and self._stack:
            self._stack.set_visible_child_name(self._state.last_visible_page)

        self._state.last_visible_page = None

    def _on_search_btn_toggled(self, btn: Gtk.ToggleButton) -> None:
        """Handle search button toggle."""
        if btn.get_active():
            self._activate_search()
        else:
            self._deactivate_search()

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        """Handle search text changes with debouncing."""
        self._cancel_debounce()
        query = entry.get_text()
        src_id = GLib.timeout_add(
            SEARCH_DEBOUNCE_MS,
            self._execute_search,
            query,
        )
        if src_id > 0:
            self._state.debounce_source_id = src_id

    def _execute_search(self, query: str) -> Literal[False]:
        """
        Execute the search and populate results.
        Returns GLib.SOURCE_REMOVE to prevent repeated execution.
        """
        self._state.debounce_source_id = 0

        if self._stack is None or self._search_results_group is None:
            return GLib.SOURCE_REMOVE

        display_query = query.strip()
        normalized_query = display_query.casefold()

        if not normalized_query:
            self._reset_search_results("Search Results")
            if self._state.last_visible_page:
                self._stack.set_visible_child_name(self._state.last_visible_page)
            return GLib.SOURCE_REMOVE

        if self._state.last_visible_page is None:
            current = self._stack.get_visible_child_name()
            if current and current != SEARCH_PAGE_ID:
                self._state.last_visible_page = current

        self._stack.set_visible_child_name(SEARCH_PAGE_ID)
        self._reset_search_results(f"Results for '{display_query}'")
        self._populate_search_results(normalized_query)

        return GLib.SOURCE_REMOVE

    def _reset_search_results(self, title: str) -> None:
        """Reset the search results group with a new title."""
        if self._search_page is None:
            return

        toolbar = self._search_page.get_child()
        if not isinstance(toolbar, Adw.ToolbarView):
            return

        page = toolbar.get_content()
        if not isinstance(page, Adw.PreferencesPage):
            return

        if self._search_results_group is not None:
            page.remove(self._search_results_group)

        self._search_results_group = Adw.PreferencesGroup(title=GLib.markup_escape_text(title))
        page.add(self._search_results_group)

    def _populate_search_results(self, query: str) -> None:
        """Populate search results, limited to prevent UI freeze."""
        if self._search_results_group is None:
            return

        # Collect then sort by relevance so the most useful matches surface first.
        hits = sorted(
            (hit for hit in self._iter_matching_items(query) if hit.score > 0),
            key=lambda hit: hit.score,
            reverse=True,
        )

        kept = hits[:SEARCH_MAX_RESULTS]
        for hit in kept:
            self._search_results_group.add(self._build_search_result_row(hit))

        if len(hits) > SEARCH_MAX_RESULTS:
            overflow_row = Adw.ActionRow(
                title=f"Showing first {SEARCH_MAX_RESULTS} results...",
                subtitle="Refine your search for more specific results",
            )
            overflow_row.set_activatable(False)
            overflow_row.add_css_class("dim-label")
            self._search_results_group.add(overflow_row)

        if not kept:
            no_results = Adw.ActionRow(
                title="No results found",
                subtitle="Try different search terms",
            )
            no_results.set_activatable(False)
            self._search_results_group.add(no_results)

    def _build_search_result_row(self, hit: SearchHit) -> Adw.ActionRow:
        """Build a clickable row that navigates to the matched item's location."""
        row = Adw.ActionRow(
            title=GLib.markup_escape_text(hit.title),
            subtitle=GLib.markup_escape_text(hit.description)
        )
        row.add_css_class("action-row")
        row.set_activatable(True)

        icon_widget = Gtk.Image.new_from_icon_name(hit.icon_name)
        icon_widget.add_css_class("action-row-prefix-icon")
        row.add_prefix(icon_widget)

        go_icon = Gtk.Image.new_from_icon_name("go-next-symbolic")
        go_icon.set_valign(Gtk.Align.CENTER)
        row.add_suffix(go_icon)

        row.connect("activated", lambda _row, h=hit: self._navigate_from_search(h))
        return row

    def _navigate_from_search(self, hit: SearchHit) -> None:
        """Navigate from a search result to its actual location."""
        self._deactivate_search()

        if self._sidebar_list is None:
            return

        row = self._sidebar_list.get_row_at_index(hit.page_idx)
        if row is None:
            return

        self._sidebar_list.select_row(row)

        page_name = f"{PAGE_PREFIX}{hit.page_idx}"
        root_tag = f"root_{hit.page_idx}"
        self._switch_to_page_and_reset(page_name, root_tag)

        target_page = None

        if self._stack:
            child = self._stack.get_child_by_name(page_name)
            if isinstance(child, Adw.NavigationView):
                target_page = child.get_visible_page()

                if len(hit.nav_path) > 1:
                    current_layout = self._state.config.get("pages", [])[hit.page_idx].get("layout", [])
                    current_path = [hit.nav_path[0]]

                    for depth in range(1, len(hit.nav_path)):
                        step_title = hit.nav_path[depth]
                        current_path.append(step_title)

                        def find_layout(layout_data: list[Any]) -> list[Any] | None:
                            for section in layout_data:
                                if not isinstance(section, dict):
                                    continue
                                items = section.get("items", [section]) if "items" in section else [section]
                                for item in items:
                                    if not isinstance(item, dict):
                                        continue
                                    if item.get("type") == ItemType.NAVIGATION:
                                        props = item.get("properties", {})
                                        if str(props.get("title", "")).strip() == step_title:
                                            return item.get("layout", [])
                                    elif item.get("type") == ItemType.EXPANDER:
                                        exp_res = find_layout([{"items": item.get("items", [])}])
                                        if exp_res is not None:
                                            return exp_res
                            return None

                        next_layout = find_layout(current_layout)
                        if next_layout is None:
                            break

                        current_layout = next_layout
                        tag = self._make_nav_tag(current_path)
                        page = child.find_page(tag)

                        if page is None:
                            ctx = self._get_context(
                                nav_view=child,
                                builder_func=self._build_nav_page,
                                path=list(current_path),
                            )
                            page = self._build_nav_page(step_title, current_layout, ctx)

                        child.push(page)
                        target_page = page

        if target_page:
            GLib.timeout_add(150, self._highlight_widget_by_id, target_page, hit.unique_id)

    def _extract_icon_name(self, props: dict[str, Any]) -> str:
        icon_config = props.get("icon", ICON_DEFAULT)

        if isinstance(icon_config, str) and icon_config:
            return icon_config

        if isinstance(icon_config, dict):
            icon_name = icon_config.get("name")
            if isinstance(icon_name, str) and icon_name:
                return icon_name

        return ICON_DEFAULT

    def _iter_matching_items(self, query: str) -> Iterator[SearchHit]:
        """
        Iterate through all config items matching the query.
        Yields SearchHit objects with page and navigation path metadata.
        """
        if not query:
            return

        for page_idx, page in enumerate(self._state.config.get("pages", [])):
            if not isinstance(page, dict):
                continue

            page_title = str(page.get("title", "Unknown")).strip() or "Unknown"
            layout = page.get("layout", [])

            if isinstance(layout, list):
                yield from self._iter_layout_hits(
                    layout,
                    query,
                    page_title,
                    page_idx,
                    (page_title,),
                )

    def _iter_layout_hits(
        self,
        layout: list[ConfigSection],
        query: str,
        breadcrumb: str,
        page_idx: int,
        nav_path: tuple[str, ...],
    ) -> Iterator[SearchHit]:
        for section in layout:
            if not isinstance(section, dict):
                continue

            items = section.get("items")
            if isinstance(items, list):
                for item in items:
                    yield from self._iter_item_hits(item, query, breadcrumb, page_idx, nav_path)
            else:
                yield from self._iter_item_hits(section, query, breadcrumb, page_idx, nav_path)

    def _iter_item_hits(
        self,
        item: Any,
        query: str,
        breadcrumb: str,
        page_idx: int,
        nav_path: tuple[str, ...],
    ) -> Iterator[SearchHit]:
        if not isinstance(item, dict):
            return

        item_type = item.get("type", "")
        props = item.get("properties", {})
        if not isinstance(props, dict):
            props = {}

        if item_type == ItemType.DIRECTORY_GENERATOR:
            for gen_item in self._process_directory_generator(item):
                yield from self._iter_item_hits(gen_item, query, breadcrumb, page_idx, nav_path)
            return

        if item_type == ItemType.FILE_GENERATOR:
            for gen_item in self._process_file_generator(item):
                yield from self._iter_item_hits(gen_item, query, breadcrumb, page_idx, nav_path)
            return

        title = str(props.get("title", "")).strip()
        desc = str(props.get("description", "")).strip()

        unique_id = self._generate_widget_id(item)

        score = self._score_search_match(query, title, desc)
        if score > 0:
            yield SearchHit(
                title=title or "Unnamed",
                description=f"{breadcrumb} • {desc}" if desc else breadcrumb,
                icon_name=self._extract_icon_name(props),
                page_idx=page_idx,
                nav_path=nav_path,
                unique_id=unique_id,
                score=score,
            )

        if item_type == ItemType.NAVIGATION:
            sub_title = title or "Submenu"
            sub_layout = item.get("layout")
            if isinstance(sub_layout, list):
                next_path = (*nav_path, sub_title)
                yield from self._iter_layout_hits(
                    sub_layout,
                    query,
                    f"{breadcrumb} › {sub_title}",
                    page_idx,
                    next_path,
                )

        if item_type == ItemType.EXPANDER:
            sub_title = title or "Expander"
            sub_items = item.get("items")
            if isinstance(sub_items, list):
                next_breadcrumb = f"{breadcrumb} › {sub_title}"
                for child in sub_items:
                    yield from self._iter_item_hits(
                        child,
                        query,
                        next_breadcrumb,
                        page_idx,
                        nav_path,
                    )

    @staticmethod
    def _score_search_match(query: str, title: str, desc: str) -> int:
        """
        Rank a config item against a search query.

        Returns a non-negative relevance score (0 == no match). Substring and
        word-prefix matches outrank fuzzy subsequences, and title matches
        always outrank description matches. Typo-tolerant fuzzy matching lets
        users find settings without needing the exact spelling.
        """
        q = query.casefold()
        if not q:
            return 0
        t = title.casefold()
        d = desc.casefold()

        # Fuzzy subsequence is typo-tolerant but noisy for very short queries;
        # only apply it once the user has typed enough to disambiguate.
        allow_fuzzy = len(q) >= 3

        # Exact / prefix / substring match on the title is the strongest signal.
        if t == q:
            return 1000
        if t.startswith(q):
            return 900
        if q in t:
            return 800

        # Alphanumeric normalized matching (e.g., "wifi" <-> "Wi-Fi", "lockscreen" <-> "Lock Screen")
        q_clean = "".join(c for c in q if c.isalnum())
        t_clean = "".join(c for c in t if c.isalnum())
        d_clean = "".join(c for c in d if c.isalnum())

        if q_clean and t_clean == q_clean:
            return 950
        if q_clean and t_clean.startswith(q_clean):
            return 850
        if q_clean and q_clean in t_clean:
            return 750

        if any(word.startswith(q) for word in t.split()):
            return 500
        if allow_fuzzy and _fuzzy_subsequence(q, t):
            return 300

        # Description matches are weaker but still useful.
        if q in d:
            return 200
        if q_clean and q_clean in d_clean:
            return 175
        if any(word.startswith(q) for word in d.split()):
            return 150
        if allow_fuzzy and _fuzzy_subsequence(q, d):
            return 100

        return 0

    # ─────────────────────────────────────────────────────────────────────────
    # SIDEBAR
    # ─────────────────────────────────────────────────────────────────────────
    def _create_sidebar(self) -> Adw.ToolbarView:
        """Create the sidebar with header, search bar, and navigation list."""
        view = Adw.ToolbarView()
        view.add_css_class("sidebar-container")

        header = Adw.HeaderBar()
        header.add_css_class("sidebar-header")
        header.set_show_end_title_buttons(False)

        title = Gtk.Label(label="Dusky", css_classes=["title"])
        header.set_title_widget(title)

        self._search_btn = Gtk.ToggleButton(icon_name=ICON_SEARCH)
        self._search_btn.set_tooltip_text("Search (Ctrl+F)")
        self._search_btn.connect("toggled", self._on_search_btn_toggled)
        header.pack_end(self._search_btn)
        view.add_top_bar(header)

        self._search_bar = Gtk.SearchBar()
        self._search_entry = Gtk.SearchEntry(placeholder_text="Find setting...")
        self._search_entry.connect("search-changed", self._on_search_changed)
        self._search_bar.set_child(self._search_entry)
        self._search_bar.connect_entry(self._search_entry)
        view.add_top_bar(self._search_bar)

        self._sidebar_list = Gtk.ListBox(css_classes=["sidebar-listbox"])
        self._sidebar_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._sidebar_list.connect("row-selected", self._on_row_selected)
        self._sidebar_list.connect("row-activated", self._on_row_activated)

        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(self._sidebar_list)
        view.set_content(scroll)

        return view

    def _create_content_panel(self) -> Adw.ViewStack:
        """Create the main content panel stack."""
        self._stack = Adw.ViewStack(vexpand=True, hexpand=True)
        return self._stack

    def _on_row_selected(
        self,
        listbox: Gtk.ListBox,
        row: Gtk.ListBoxRow | None,
    ) -> None:
        """Handle sidebar row selection."""
        if row is None or self._stack is None:
            return

        idx = row.get_index()
        pages = self._state.config.get("pages", [])
        if 0 <= idx < len(pages):
            page_name = f"{PAGE_PREFIX}{idx}"
            root_tag = f"root_{idx}"
            self._switch_to_page_and_reset(page_name, root_tag)

    def _on_row_activated(self, listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        """Handle sidebar row activation (clicking already selected row)."""
        self._on_row_selected(listbox, row)

    def _switch_to_page_and_reset(self, page_name: str, root_tag: str) -> None:
        """Switch to the page and pop navigation to root."""
        if self._stack:
            if child := self._stack.get_child_by_name(page_name):
                if isinstance(child, Adw.NavigationView):
                    if child.get_visible_page() is None:
                        idx = int(page_name.removeprefix(PAGE_PREFIX))
                        config = self._state.config["pages"][idx]
                        title = str(config.get("title", "Untitled"))
                        ctx = self._get_context(child, self._build_nav_page, [title])
                        child.add(self._build_nav_page(title, config.get("layout", []), ctx, root_tag=root_tag))
                    child.pop_to_tag(root_tag)

            self._stack.set_visible_child_name(page_name)

    # ─────────────────────────────────────────────────────────────────────────
    # PAGE BUILDING
    # ─────────────────────────────────────────────────────────────────────────
    def _populate_pages(self, select_index: int | None = None) -> None:
        """
        Create sidebar rows and content pages from config.
        """
        pages = self._state.config.get("pages", [])
        if not pages:
            self._show_empty_state()
            return

        first_row: Gtk.ListBoxRow | None = None
        target_row: Gtk.ListBoxRow | None = None

        for idx, page in enumerate(pages):
            title = str(page.get("title", "Untitled"))
            icon = str(page.get("icon", ICON_DEFAULT))
            row = self._create_sidebar_row(title, icon)

            if self._sidebar_list:
                self._sidebar_list.append(row)
                if first_row is None:
                    first_row = row
                if idx == select_index:
                    target_row = row

            nav = Adw.NavigationView()

            if self._stack:
                self._stack.add_named(nav, f"{PAGE_PREFIX}{idx}")

        if self._sidebar_list:
            row_to_select = target_row or first_row
            if row_to_select:
                self._sidebar_list.select_row(row_to_select)

    def _create_sidebar_row(self, title: str, icon_name: str) -> Gtk.ListBoxRow:
        """Create a styled sidebar navigation row."""
        row = Gtk.ListBoxRow(css_classes=["sidebar-row"])
        box = Gtk.Box()
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.add_css_class("sidebar-row-icon")
        
        label = Gtk.Label(
            label=title,
            xalign=0,
            hexpand=True,
            css_classes=["sidebar-row-label"],
        )
        label.set_ellipsize(Pango.EllipsizeMode.END)
        
        box.append(icon)
        box.append(label)
        row.set_child(box)

        return row

    def _make_nav_tag(self, path: list[str] | tuple[str, ...]) -> str:
        parts: list[str] = []
        for raw_part in path:
            part = "".join(ch if ch.isalnum() else "_" for ch in raw_part.strip())
            parts.append(part.strip("_") or "page")
        return f"page_{len(path)}_{'__'.join(parts)}"

    def _build_nav_page(
        self,
        title: str,
        layout: list[ConfigSection],
        ctx: RowContext,
        root_tag: str | None = None,
    ) -> Adw.NavigationPage:
        """
        Build a navigation page with toolbar and preferences content.
        """
        path = list(ctx.get("path") or [])
        if not path or path[-1] != title:
            path.append(title)

        tag = root_tag if root_tag else self._make_nav_tag(path)

        page = Adw.NavigationPage(title=title, tag=tag)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)

        if len(path) == 1:
            header.pack_start(self._create_sidebar_toggle_button())

        window_title = Adw.WindowTitle(title=title)
        if len(path) > 1:
            window_title.set_subtitle(" › ".join(path[:-1]))

        header.set_title_widget(window_title)
        toolbar.add_top_bar(header)

        pref_page = Adw.PreferencesPage()
        self._populate_pref_content(pref_page, layout, ctx)

        toolbar.set_content(pref_page)
        page.set_child(toolbar)
        return page

    def _populate_pref_content(
        self,
        page: Adw.PreferencesPage,
        layout: list[ConfigSection],
        ctx: RowContext,
    ) -> None:
        """Populate a preferences page with sections and items."""
        for section in layout:
            section_type = section.get("type", SectionType.SECTION)

            if section_type == SectionType.GRID_SECTION:
                page.add(self._build_grid_section(section, ctx))
                continue

            if isinstance(section.get("items"), list):
                page.add(self._build_standard_section(section, ctx))
                continue

            group = Adw.PreferencesGroup()
            group.add(self._build_item_row(section, ctx))  # type: ignore
            page.add(group)

    def _build_grid_section(
        self,
        section: ConfigSection,
        ctx: RowContext,
    ) -> Adw.PreferencesGroup:
        """Build a grid section with flow box layout."""
        group = Adw.PreferencesGroup()
        props = section.get("properties", {})
        if not isinstance(props, dict):
            props = {}

        if title := props.get("title"):
            group.set_title(GLib.markup_escape_text(str(title)))

        if props.get("center_title"):
            self._center_preferences_group(group)
            GLib.idle_add(lambda g=group: (self._center_preferences_group(g), GLib.SOURCE_REMOVE)[1])

        flow = Gtk.FlowBox()
        flow.set_valign(Gtk.Align.START)
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_homogeneous(True)
        flow.set_column_spacing(10)
        flow.set_row_spacing(10)
        flow.set_min_children_per_line(3)
        flow.set_max_children_per_line(3)

        def append_grid_item(grid_item: ConfigItem) -> None:
            item_type = grid_item.get("type", "")
            item_props = grid_item.get("properties", {})
            if not isinstance(item_props, dict):
                item_props = {}

            try:
                match item_type:
                    case ItemType.TOGGLE_CARD:
                        child = rows.GridToggleCard(item_props, grid_item.get("on_toggle"), ctx)
                    case ItemType.SERVICE_CARD:
                        child = rows.ServiceToggleCard(item_props, grid_item.get("on_toggle"), ctx)
                    case ItemType.GRID_CARD:
                        child = rows.GridCard(item_props, grid_item.get("on_press"), ctx)
                    case ItemType.SERVICE:
                        # Allow service rows in grid as toggle cards fallback
                        child = rows.ServiceToggleCard(item_props, grid_item.get("on_toggle"), ctx)
                    case _:
                        log.warning(
                            "Unsupported grid item type '%s', defaulting to GridCard",
                            item_type,
                        )
                        child = rows.GridCard(item_props, grid_item.get("on_press"), ctx)
            except Exception as e:
                log.error("Failed to build grid item for type '%s': %s", item_type, e)
                flow.append(self._build_error_row(str(e), str(item_props.get("title", "Unknown"))))
                return

            child.set_name(self._generate_widget_id(grid_item))
            flow.append(child)

        for item in section.get("items", []):
            if not isinstance(item, dict):
                continue
            if item.get("type") == ItemType.DIRECTORY_GENERATOR:
                for gen_item in self._process_directory_generator(item):
                    append_grid_item(gen_item)
            elif item.get("type") == ItemType.FILE_GENERATOR:
                for gen_item in self._process_file_generator(item):
                    append_grid_item(gen_item)
            else:
                append_grid_item(item)

        group.add(flow)
        return group

    def _center_preferences_group(self, group: Adw.PreferencesGroup) -> None:
        """Center the heading/description labels of a PreferencesGroup."""
        try:
            # group -> vertical box
            outer = group.get_first_child()
            if not isinstance(outer, Gtk.Box):
                return
            header = outer.get_first_child()
            if not isinstance(header, Gtk.Box):
                return
            # header is horizontal box with class "header"
            # its first child is vertical labels box
            labels_box = header.get_first_child()
            if not isinstance(labels_box, Gtk.Box):
                return
            # Traverse labels inside labels_box
            label = labels_box.get_first_child()
            while label is not None:
                if isinstance(label, Gtk.Label):
                    label.set_halign(Gtk.Align.CENTER)
                    label.set_xalign(0.5)
                    label.set_hexpand(True)
                    label.set_justify(Gtk.Justification.CENTER)
                label = label.get_next_sibling()
            # Also center the header box itself if needed
            header.set_halign(Gtk.Align.CENTER)
            header.set_hexpand(True)
            labels_box.set_halign(Gtk.Align.CENTER)
            labels_box.set_hexpand(True)
            outer.set_halign(Gtk.Align.FILL)
        except Exception as e:
            log.debug("Failed to center group title: %s", e)

    def _build_standard_section(
        self,
        section: ConfigSection,
        ctx: RowContext,
    ) -> Adw.PreferencesGroup:
        """Build a standard preferences group with row items."""
        group = Adw.PreferencesGroup()
        props = section.get("properties", {})

        if title := props.get("title"):
            group.set_title(GLib.markup_escape_text(str(title)))
        if desc := props.get("description"):
            group.set_description(GLib.markup_escape_text(str(desc)))

        if props.get("center_title"):
            # Defer slightly to ensure labels are realized, but also try immediately
            self._center_preferences_group(group)
            GLib.idle_add(lambda g=group: (self._center_preferences_group(g), GLib.SOURCE_REMOVE)[1])

        for item in section.get("items", []):
            if not isinstance(item, dict):
                continue
            if item.get("type") == ItemType.DIRECTORY_GENERATOR:
                for gen_item in self._process_directory_generator(item):
                    group.add(self._build_item_row(gen_item, ctx))
            elif item.get("type") == ItemType.FILE_GENERATOR:
                for gen_item in self._process_file_generator(item):
                    group.add(self._build_item_row(gen_item, ctx))
            else:
                group.add(self._build_item_row(item, ctx))

        return group

    def _process_directory_generator(self, config: ConfigItem) -> Iterator[ConfigItem]:
        """Generate items based on directory contents, with per-config caching."""
        cache_key = id(config)
        cached = self._directory_generator_cache.get(cache_key)
        if cached is not None:
            yield from cached
            return

        props = config.get("properties", {})
        if not isinstance(props, dict):
            self._directory_generator_cache[cache_key] = ()
            return

        path_str = props.get("path")
        template = config.get("item_template")

        if not isinstance(path_str, str) or not path_str or not isinstance(template, dict):
            self._directory_generator_cache[cache_key] = ()
            return

        base_path = Path(path_str).expanduser()
        if not base_path.is_dir():
            self._directory_generator_cache[cache_key] = ()
            return

        try:
            dirs = sorted(
                (p for p in base_path.iterdir() if p.is_dir()),
                key=lambda p: p.name.casefold(),
            )
        except OSError:
            self._directory_generator_cache[cache_key] = ()
            return

        generated: list[ConfigItem] = []

        for directory in dirs:
            name_pretty = directory.name.replace("_", " ").title()
            variables = {
                "name": directory.name,
                "path": str(directory),
                "name_pretty": name_pretty,
            }
            generated_item = self._inject_variables(template, variables)
            if isinstance(generated_item, dict):
                generated.append(generated_item)

        frozen = tuple(generated)
        self._directory_generator_cache[cache_key] = frozen
        yield from frozen

    def _process_file_generator(self, config: ConfigItem) -> Iterator[ConfigItem]:
        """Generate items from files in a directory, with per-loaded-config caching.

        Properties:
          path      – base directory to scan
          glob      – glob pattern (default "*.conf")
          recursive – if true, also scans immediate subdirectories (default false)

        Template variables:
          {name}        – filename stem (e.g. "wg0", "us-nyc-wg-506")
          {filename}    – filename with extension (e.g. "wg0.conf")
          {path}        – absolute path (e.g. "/etc/wireguard/wg0.conf")
          {name_pretty} – human-readable stem (e.g. "Wg0", "Us Nyc Wg 506")
          {relpath}     – path relative to base (e.g. "wg0.conf", "mullvad/us-nyc-wg-506.conf")
          {subdir}      – parent dir name for subdir files, empty for top-level

        The cache is scoped to the loaded config generation and is cleared on hot
        reload. This keeps generated widget IDs stable between UI construction and
        search/highlight traversal without hiding newly added files after Ctrl+R.
        Symlinks are included — wg-quick resolves them as root; we only need the name.
        """
        cache_key = id(config)
        cached = self._file_generator_cache.get(cache_key)
        if cached is not None:
            yield from cached
            return

        generated: list[ConfigItem] = []

        props = config.get("properties", {})
        if not isinstance(props, dict):
            self._file_generator_cache[cache_key] = ()
            return

        template = config.get("item_template")
        if not isinstance(template, dict):
            self._file_generator_cache[cache_key] = ()
            return

        path_str = props.get("path", "")
        glob_raw = props.get("glob", "*.conf")
        glob_pattern = glob_raw if isinstance(glob_raw, str) and glob_raw else "*.conf"
        recursive = bool(props.get("recursive", False))

        if not isinstance(path_str, str) or not path_str.strip():
            self._file_generator_cache[cache_key] = ()
            return

        base_path = Path(path_str).expanduser()

        def _iter_files() -> Iterator[Path]:
            try:
                for p in base_path.glob(glob_pattern):
                    if p.is_file() or p.is_symlink():
                        yield p
            except (OSError, PermissionError, ValueError):
                return

            if not recursive:
                return

            try:
                subdirs = sorted(
                    (p for p in base_path.iterdir() if p.is_dir() and not p.is_symlink()),
                    key=lambda p: p.name.casefold(),
                )
            except (OSError, PermissionError):
                return

            for subdir in subdirs:
                try:
                    for p in subdir.glob(glob_pattern):
                        if p.is_file() or p.is_symlink():
                            yield p
                except (OSError, PermissionError, ValueError):
                    continue

        files = sorted(
            _iter_files(),
            key=lambda p: (p.parent != base_path, p.parent.name.casefold(), p.name.casefold()),
        )

        for filepath in files:
            stem = filepath.stem
            is_subdir = filepath.parent != base_path
            subdir_name = filepath.parent.name if is_subdir else ""
            relpath = f"{subdir_name}/{filepath.name}" if is_subdir else filepath.name
            variables = {
                "name": stem,
                "filename": filepath.name,
                "path": str(filepath),
                "name_pretty": stem.replace("_", " ").replace("-", " ").title(),
                "relpath": relpath,
                "subdir": subdir_name,
            }
            gen_item = self._inject_variables(template, variables)
            if isinstance(gen_item, dict):
                generated.append(gen_item)

        frozen = tuple(generated)
        self._file_generator_cache[cache_key] = frozen
        yield from frozen

    def _inject_variables(self, item: Any, vars: dict[str, str]) -> Any:
        """Recursively replace variables in strings."""
        if isinstance(item, str):
            return re.sub(r"\{(name|filename|path|name_pretty|relpath|subdir)\}", lambda match: vars[match.group(1)], item)
        if isinstance(item, list):
            return [self._inject_variables(x, vars) for x in item]
        if isinstance(item, dict):
            return {
                k: self._inject_variables(v, {name: shlex.quote(value) for name, value in vars.items()} if k == "state_command" else vars)
                for k, v in item.items()
            }
        return item

    def _build_item_row(
        self,
        item: ConfigItem | ConfigSection,
        ctx: RowContext,
    ) -> Adw.PreferencesRow:
        """
        Build the appropriate row widget for a config item.
        """
        item_type = item.get("type", "")
        props = item.get("properties", {})
        row = None

        try:
            match item_type:
                case ItemType.BUTTON | ItemType.GRID_CARD:
                    row = rows.ButtonRow(props, item.get("on_press"), ctx)
                case ItemType.TOGGLE | ItemType.TOGGLE_CARD:
                    row = rows.ToggleRow(props, item.get("on_toggle"), ctx)
                case ItemType.LABEL:
                    row = rows.LabelRow(props, item.get("value"), ctx)
                case ItemType.SLIDER:
                    row = rows.SliderRow(props, item.get("on_change"), ctx)
                case ItemType.SPIN:
                    row = rows.SpinRow(props, item.get("on_change"), ctx)
                case ItemType.SELECTION:
                    row = rows.SelectionRow(props, item.get("on_change"), ctx)
                case ItemType.ENTRY:
                    row = rows.EntryRow(props, item.get("on_action"), ctx)
                case ItemType.SECRET:
                    row = rows.SecretRow(props, item.get("on_action"), ctx)
                case ItemType.MULTI_TEXT:
                    row = rows.MultiTextRow(props, item.get("on_action"), ctx)
                case ItemType.KEYBIND:
                    row = rows.KeybindRow(props, item.get("on_action"), ctx)
                case ItemType.COLOR:
                    row = rows.ColorRow(props, item.get("on_action"), ctx)
                case ItemType.PATH:
                    row = rows.PathRow(props, item.get("on_action"), ctx)
                case ItemType.NAVIGATION:
                    row = rows.NavigationRow(props, item.get("layout"), ctx)
                case ItemType.EXPANDER:
                    row = rows.ExpanderRow(props, item.get("items"), ctx)
                case ItemType.WARNING_BANNER:
                    row = self._build_warning_banner(props)
                case ItemType.ASYNC_SELECTOR:
                    row = rows.AsyncSelectorRow(props, item.get("on_action"), ctx)
                case ItemType.FLAG_GROUP:
                    row = rows.FlagGroupRow(props, item.get("on_action"), ctx)
                case ItemType.SERVICE:
                    row = rows.ServiceToggleRow(props, item.get("on_toggle"), ctx)
                case ItemType.SERVICE_CARD:
                    row = rows.ServiceToggleCard(props, item.get("on_toggle"), ctx)
                case _:
                    log.warning("Unknown item type '%s', defaulting to button", item_type)
                    row = rows.ButtonRow(props, item.get("on_press"), ctx)

            if row is not None:
                row.set_name(self._generate_widget_id(item))

            return row

        except Exception as e:
            log.error("Failed to build row for type '%s': %s", item_type, e)
            return self._build_error_row(str(e), str(props.get("title", "Unknown")))

    def _build_warning_banner(self, props: ItemProperties) -> Adw.PreferencesRow:
        """Build a warning banner row. Icon is optional; compact/subtle mode is supported."""
        row = Adw.PreferencesRow(css_classes=["action-row"])

        # Determine icon: optional, custom allowed. Default to ICON_WARNING for backward compat
        # if key not present. Use "" / "none" / "hidden" to hide icon.
        if "icon" not in props:
            icon_name = ICON_WARNING
            show_icon = True
        else:
            raw_icon = props.get("icon")
            if raw_icon is None:
                icon_name = ICON_WARNING
                show_icon = True
            else:
                icon_str = str(raw_icon).strip()
                if icon_str == "" or icon_str.lower() in ("none", "hidden", "no", "false"):
                    show_icon = False
                    icon_name = ""
                else:
                    icon_name = icon_str
                    show_icon = True

        # Compact / subtle mode: triggered by compact=true, subtle=true, or style="compact"/"subtle"/"info"
        is_compact = False
        if props.get("compact") or props.get("subtle"):
            # any truthy value triggers compact
            with __import__("contextlib").suppress(Exception):
                is_compact = bool(props.get("compact") or props.get("subtle"))
        style_val = str(props.get("style", "")).strip().lower()
        if style_val in ("compact", "subtle", "info", "small", "minimal"):
            is_compact = True

        if is_compact:
            # Subtle horizontal layout: small icon + left-aligned text, minimal padding
            # Add compact class to outer row to shrink its chrome
            row.add_css_class("compact")
            box = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=8,
                css_classes=["warning-banner-box", "compact"],
            )
            box.set_margin_top(0)
            box.set_margin_bottom(0)

            text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            text_box.set_hexpand(True)
            text_box.set_valign(Gtk.Align.CENTER)

            title_str = str(props.get("title", "")).strip()
            # Only show title if non-empty and not just whitespace
            if title_str:
                title = Gtk.Label(
                    label=title_str,
                    css_classes=["heading"],
                    xalign=0.0,
                )
                title.set_halign(Gtk.Align.START)
                title.set_wrap(True)
                text_box.append(title)

            message_str = str(props.get("message", "")).strip()
            if message_str:
                # Use body/heading for high contrast, not dim caption
                if title_str:
                    css = ["caption"]
                else:
                    css = ["body"]
                msg = Gtk.Label(
                    label=message_str,
                    css_classes=css,
                    xalign=0.0,
                )
                msg.set_halign(Gtk.Align.START)
                msg.set_wrap(True)
                msg.set_justify(Gtk.Justification.LEFT)
                text_box.append(msg)

            if show_icon:
                icon = Gtk.Image.new_from_icon_name(icon_name)
                icon.set_valign(Gtk.Align.CENTER)
                icon.add_css_class("warning-banner-icon")
                # compact icon will be sized via CSS .compact override
                box.append(icon)
            box.append(text_box)
            row.set_child(box)
            return row

        # Default: large centered vertical layout (backward compat)
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
            css_classes=["warning-banner-box"],
        )

        icon = None
        if show_icon:
            icon = Gtk.Image.new_from_icon_name(icon_name)
            icon.set_halign(Gtk.Align.CENTER)
            icon.set_margin_bottom(8)
            icon.add_css_class("warning-banner-icon")

        title = Gtk.Label(
            label=str(props.get("title", "Warning")),
            css_classes=["title-1"],
        )
        title.set_halign(Gtk.Align.CENTER)

        message = Gtk.Label(
            label=str(props.get("message", "")),
            css_classes=["body"],
        )
        message.set_halign(Gtk.Align.CENTER)
        message.set_wrap(True)

        if icon is not None:
            box.append(icon)
        box.append(title)
        box.append(message)
        row.set_child(box)

        return row

    def _build_error_row(self, error: str, title: str) -> Adw.ActionRow:
        """Build an error placeholder row for failed item builds."""
        row = Adw.ActionRow(
            title=GLib.markup_escape_text(f"⚠ {title}"),
            subtitle=GLib.markup_escape_text(f"Build error: {error[:80]}"),
        )
        row.add_css_class("error")
        row.set_activatable(False)
        return row

    # ─────────────────────────────────────────────────────────────────────────
    # STATE PAGES
    # ─────────────────────────────────────────────────────────────────────────
    def _show_error_state(self, error_message: str) -> None:
        """Display an error status page when config loading fails."""
        if self._stack is None:
            return

        status = Adw.StatusPage(
            icon_name=ICON_ERROR,
            title="Configuration Error",
            description=GLib.markup_escape_text(error_message),
        )
        status.add_css_class("error-state")

        hint = Gtk.Label(
            label="Press Ctrl+R to reload after fixing the configuration.",
            css_classes=["dim-label"],
        )
        hint.set_margin_top(12)
        status.set_child(hint)

        self._stack.add_named(status, ERROR_PAGE_ID)
        self._stack.set_visible_child_name(ERROR_PAGE_ID)

    def _show_empty_state(self) -> None:
        """Display an empty status page when no pages are configured."""
        if self._stack is None:
            return

        status = Adw.StatusPage(
            icon_name=ICON_EMPTY,
            title="No Configuration Found",
            description="The configuration file exists but contains no pages.",
        )
        status.add_css_class("empty-state")

        hint = Gtk.Label(
            label=f"Add pages to {CONFIG_FILENAME} and press Ctrl+R to reload.",
            css_classes=["dim-label"],
        )
        hint.set_margin_top(12)
        status.set_child(hint)

        self._stack.add_named(status, EMPTY_PAGE_ID)
        self._stack.set_visible_child_name(EMPTY_PAGE_ID)

    # ─────────────────────────────────────────────────────────────────────────
    # UTILITIES
    # ─────────────────────────────────────────────────────────────────────────
    def _toast(self, message: str, timeout: int = DEFAULT_TOAST_TIMEOUT) -> None:
        """Display a toast notification."""
        if self._toast_overlay:
            toast = Adw.Toast(title=message, timeout=timeout)
            self._toast_overlay.add_toast(toast)


# =============================================================================
# ENTRY POINT
# =============================================================================
def main() -> int:
    """Application entry point."""
    app = DuskyControlCenter()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
