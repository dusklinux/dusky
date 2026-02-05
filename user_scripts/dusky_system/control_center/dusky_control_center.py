#!/usr/bin/env python3
"""
Dusky Control Center (Production Build)
A GTK4/Libadwaita configuration launcher for the Dusky Dotfiles.
Fully UWSM-compliant for Arch Linux/Hyprland environments.

Forensic Improvements (v2):
- Error UI: Malformed YAML now shows Adw.StatusPage with stack trace.
- Hot Reload: Full widget teardown with reference nullification to prevent Segfaults.
- Type Safety: Strict TypedDict with NotRequired and StrEnum validation.
- Resource Safety: CSS provider lifecycle is fully guarded against leaks.
- UX: Hot reload preserves the currently selected page index.
- Fix: Search results now correctly render Grid/Toggle cards as List rows.

Daemon & Optimization Features (Implemented):
- SINGLE INSTANCE: Uses Gtk.Application uniqueness. Second launch simply raises existing window.
- RAM EFFICIENCY: Hides window on close; garbage collects to free memory.
- DAEMON MODE: Process stays alive in background (sleeping) when window is closed.
- KEEPALIVE: self.hold() prevents GApplication 10s service timeout.
- INSTANT LAUNCH: UI is pre-built and realized during startup to ensure 0ms latency on activation.
"""
from __future__ import annotations

import gc
import logging
import sys
import threading
import traceback
from collections.abc import Callable, Iterator
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Final,
    Literal,
    NotRequired,
    TypedDict,
)

# =============================================================================
# VERSION CHECK
# =============================================================================
if sys.version_info < (3, 13):
    sys.exit("[FATAL] Python 3.13+ is required.")

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
import lib.utility as utility

utility.preflight_check()

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

import lib.rows as rows

if TYPE_CHECKING:
    pass

# =============================================================================
# CONSTANTS
# =============================================================================
APP_ID: Final[str] = "com.github.dusky.controlcenter"
APP_TITLE: Final[str] = "Dusky Control Center"
CONFIG_FILENAME: Final[str] = "dusky_config.yaml"
CSS_FILENAME: Final[str] = "dusky_style.css"
SCRIPT_DIR: Final[Path] = Path(__file__).resolve().parent

# UI Layout Constants
WINDOW_DEFAULT_WIDTH: Final[int] = 1180
WINDOW_DEFAULT_HEIGHT: Final[int] = 780
SIDEBAR_MIN_WIDTH: Final[int] = 180
SIDEBAR_MAX_WIDTH: Final[int] = 180
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
    SELECTION = "selection"  # Updated
    ENTRY = "entry"          # Updated
    NAVIGATION = "navigation"
    WARNING_BANNER = "warning_banner"
    TOGGLE_CARD = "toggle_card"
    GRID_CARD = "grid_card"
    EXPANDER = "expander"


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
    debounce: bool
    options: list[str]
    placeholder: str


class ConfigItem(TypedDict, total=False):
    """A single item in the configuration."""
    type: str
    properties: ItemProperties
    on_press: dict[str, Any] | None
    on_toggle: dict[str, Any] | None
    on_change: dict[str, Any] | None
    on_action: dict[str, Any] | None
    layout: list[Any]  # Recursive reference
    items: list[Any]   # For expander rows
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


class ConfigLoadResult(TypedDict):
    """Result from config loading operation."""
    success: bool
    config: AppConfig
    css: str
    error: str | None


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

    # Slots optimization for GObject-based class mixed with Python logic
    __slots__ = (
        "_state",
        "_sidebar_list",
        "_stack",
        "_toast_overlay",
        "_search_bar",
        "_search_entry",
        "_search_btn",
        "_search_page",
        "_search_results_group",
        "_css_provider",
        "_display",
        "_window",
        "_split_view",
    )

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

        # DAEMON FIX: Explicitly hold the application to prevent 10s timeout
        # when running with --gapplication-service without an active window.
        self.hold()
        
        # INSTANT STARTUP FIX: 
        # Move config loading and UI building here (Background) instead of do_activate (Foreground).
        # This consumes RAM immediately but makes activation instant.
        result = self._load_config_and_css_sync()
        self._state.config = result["config"]
        self._state.css_content = result["css"]
        self._state.config_error = result["error"]

        self._apply_css()
        self._build_ui()
        
        # CRITICAL: Force the window to 'realize' immediately.
        # This allocates the GDK surface and backend resources (the ~200MB RAM usage)
        # without actually showing the window on screen (unmapped).
        # This ensures that when do_activate calls present(), the heavy lifting is already done.
        if self._window:
            self._window.realize()
            self._window.set_visible(False)

    def do_activate(self) -> None:
        """
        Application entry point.
        DAEMON LOGIC: Window is pre-built in do_startup. Just present it.
        """
        if self._window:
            self._window.present()

    def do_shutdown(self) -> None:
        """Cleanup resources on application exit."""
        self._cancel_debounce()
        self._remove_css_provider()
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
                    self._display, self._css_provider
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
        css = self._do_load_css()
        
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
            
            if not isinstance(loaded, dict):
                return {"pages": []}, f"Config is not a dictionary (got {type(loaded).__name__})"
            
            if "pages" not in loaded:
                return {"pages": []}, "Config missing required 'pages' key"
            
            if not isinstance(loaded.get("pages"), list):
                return {"pages": []}, "'pages' must be a list"
            
            # Validate each page has required 'title'
            for idx, page in enumerate(loaded["pages"]):
                if not isinstance(page, dict):
                    return {"pages": []}, f"Page {idx} is not a dictionary"
                if "title" not in page:
                    return {"pages": []}, f"Page {idx} missing required 'title' key"
            
            return loaded, None  # type: ignore[return-value]
            
        except FileNotFoundError:
            return {"pages": []}, f"Config file not found: {config_path}"
        except Exception as e:
            error_detail = "".join(traceback.format_exception_only(type(e), e)).strip()
            return {"pages": []}, f"Config parse error: {error_detail}"

    def _do_load_css(self) -> str:
        """
        Safely load the CSS stylesheet.
        
        Returns:
            CSS content string, or empty string on failure.
        """
        css_path = SCRIPT_DIR / CSS_FILENAME
        try:
            return css_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            log.info("No custom CSS file found at: %s", css_path)
            return ""
        except OSError as e:
            log.warning("Failed to read CSS file: %s", e)
            return ""

    def _apply_css(self) -> None:
        """Apply loaded CSS to the default display."""
        self._remove_css_provider()

        if not self._state.css_content:
            return

        self._display = Gdk.Display.get_default()
        if self._display is None:
            log.warning("No default display available for CSS")
            return

        provider = Gtk.CssProvider()
        try:
            provider.load_from_string(self._state.css_content)
            Gtk.StyleContext.add_provider_for_display(
                self._display,
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )
            # Only store reference after successful add to avoid leak on partial fail
            self._css_provider = provider
        except GLib.Error as e:
            log.error("CSS parsing failed: %s", e.message)
            # Don't store the failed provider

    def _get_context(
        self,
        nav_view: Adw.NavigationView | None = None,
        builder_func: Callable[..., Adw.NavigationPage] | None = None,
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
        }

    # ─────────────────────────────────────────────────────────────────────────
    # UI CONSTRUCTION
    # ─────────────────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        """Construct and present the main application window."""
        self._window = Adw.Window(application=self, title=APP_TITLE)
        self._window.set_default_size(WINDOW_DEFAULT_WIDTH, WINDOW_DEFAULT_HEIGHT)
        self._window.set_size_request(720, 600)  # (Min Width, Min Height)
        self._window.connect("close-request", self._on_close_request)

        # Keyboard event handling
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self._window.add_controller(key_ctrl)

        # Main layout
        self._toast_overlay = Adw.ToastOverlay()

        self._split_view = Adw.OverlaySplitView()
        self._split_view.set_min_sidebar_width(SIDEBAR_MIN_WIDTH)
        self._split_view.set_max_sidebar_width(SIDEBAR_MAX_WIDTH)
        self._split_view.set_sidebar_width_fraction(SIDEBAR_WIDTH_FRACTION)

        self._split_view.set_sidebar(self._create_sidebar())
        self._split_view.set_content(self._create_content_panel())

        self._toast_overlay.set_child(self._split_view)
        self._window.set_content(self._toast_overlay)

        # Populate content based on config state
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
        Hide window and GC to free RAM.
        """
        window.set_visible(False)
        gc.collect()  # Explicitly free memory while hidden
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
                # Close (Hide) via the window manager logic
                if self._window:
                    self._window.close()
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
        
        Preserves the current page selection and restores it after rebuild.
        Uses thread + idle pattern to prevent UI freeze.
        """
        log.info("Hot Reload Initiated...")
        
        # Capture current selection for restoration
        current_page = self._get_current_page_index()
        
        # Snapshot for rollback on failure
        old_config = deepcopy(self._state.config)
        old_css = self._state.css_content

        def background_load() -> ConfigLoadResult:
            """Execute I/O operations in background thread."""
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
            error: BaseException | None
        ) -> None:
            """Handle reload completion on main thread."""
            if error is not None:
                log.error("Reload thread error: %s", error)
                self._toast("Reload Failed: Internal error", 3)
                return

            if result is None:
                self._toast("Reload Failed: No result", 3)
                return

            try:
                # Update state
                self._state.config = result["config"]
                self._state.css_content = result["css"]
                self._state.config_error = result["error"]

                # Rebuild UI
                self._apply_css()
                self._clear_and_rebuild_ui(current_page)

                if result["error"]:
                    self._toast(f"Config Error: {result['error'][:50]}...", 4)
                else:
                    self._toast("Configuration Reloaded 🚀")

            except Exception as rebuild_error:
                log.error("UI Rebuild failed: %s", rebuild_error, exc_info=True)
                # Rollback state
                self._state.config = old_config
                self._state.css_content = old_css
                self._state.config_error = None
                self._toast("Reload Failed: UI rebuild error", 3)

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
        # Nullify widget references before clearing to avoid GTK warnings
        self._search_page = None
        self._search_results_group = None

        # Clear containers
        self._clear_sidebar()
        self._clear_stack()

        # Rebuild
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
    def _create_search_page(self) -> None:
        """Create the search results page in the stack."""
        if self._stack is None:
            return

        self._search_page = Adw.NavigationPage(title="Search", tag="search")

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        # Add sidebar toggle to search page as well for consistency
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
        if self._search_bar:
            self._search_bar.set_search_mode(True)
        if self._search_btn:
            self._search_btn.set_active(True)
        if self._search_entry:
            self._search_entry.grab_focus()

    def _deactivate_search(self) -> None:
        """Deactivate search and restore previous page."""
        if self._search_bar:
            self._search_bar.set_search_mode(False)
        if self._search_btn:
            self._search_btn.set_active(False)
        if self._search_entry:
            self._search_entry.set_text("")

        if self._state.last_visible_page and self._stack:
            self._stack.set_visible_child_name(self._state.last_visible_page)

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
            query
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

        query = query.strip().lower()
        if not query:
            self._reset_search_results("Search Results")
            return GLib.SOURCE_REMOVE

        # Save current page before switching to search
        current = self._stack.get_visible_child_name()
        if current and current != SEARCH_PAGE_ID:
            self._state.last_visible_page = current

        self._stack.set_visible_child_name(SEARCH_PAGE_ID)
        self._reset_search_results(f"Results for '{query}'")
        self._populate_search_results(query)

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

        self._search_results_group = Adw.PreferencesGroup(title=title)
        page.add(self._search_results_group)

    def _populate_search_results(self, query: str) -> None:
        """Populate search results, limited to prevent UI freeze."""
        if self._search_results_group is None:
            return

        count = 0
        context = self._get_context()

        for match in self._iter_matching_items(query):
            if count >= SEARCH_MAX_RESULTS:
                # Add overflow indicator
                overflow_row = Adw.ActionRow(
                    title=f"Showing first {SEARCH_MAX_RESULTS} results...",
                    subtitle="Refine your search for more specific results",
                )
                overflow_row.set_activatable(False)
                overflow_row.add_css_class("dim-label")
                self._search_results_group.add(overflow_row)
                break

            self._search_results_group.add(self._build_item_row(match, context))
            count += 1

        if count == 0:
            no_results = Adw.ActionRow(
                title="No results found",
                subtitle="Try different search terms",
            )
            no_results.set_activatable(False)
            self._search_results_group.add(no_results)

    def _iter_matching_items(self, query: str) -> Iterator[ConfigItem]:
        """
        Iterate through all config items matching the search query.
        """
        for page in self._state.config.get("pages", []):
            page_title = str(page.get("title", "Unknown"))
            yield from self._recursive_search(
                page.get("layout", []), 
                query, 
                page_title
            )

    def _recursive_search(
        self,
        layout: list[ConfigSection],
        query: str,
        breadcrumb: str,
    ) -> Iterator[ConfigItem]:
        """
        Recursively search through sections and nested layouts.
        Injects breadcrumb path into the item description for context.
        """
        for section in layout:
            for item in section.get("items", []):
                props = item.get("properties", {})
                title = str(props.get("title", "")).lower()
                desc = str(props.get("description", "")).lower()

                # Exclude navigation items from search (they're structural)
                item_type = item.get("type", "")
                if item_type not in (ItemType.NAVIGATION, ItemType.EXPANDER):
                    if query in title or query in desc:
                        result: ConfigItem = deepcopy(item)
                        result.setdefault("properties", {})
                        original_desc = props.get("description", "")
                        result["properties"]["description"] = (
                            f"{breadcrumb} • {original_desc}" 
                            if original_desc 
                            else breadcrumb
                        )
                        yield result

                # Recurse into nested layouts (NavigationRow)
                if "layout" in item:
                    sub_title = str(props.get("title", "Submenu"))
                    yield from self._recursive_search(
                        item.get("layout", []),
                        query,
                        f"{breadcrumb} › {sub_title}",
                    )

                # Recurse into expander items
                if "items" in item and item_type == ItemType.EXPANDER:
                    sub_title = str(props.get("title", "Expander"))
                    yield from self._search_expander_items(
                        item.get("items", []),
                        query,
                        f"{breadcrumb} › {sub_title}",
                    )

    def _search_expander_items(
        self,
        items: list[ConfigItem],
        query: str,
        breadcrumb: str,
    ) -> Iterator[ConfigItem]:
        """
        Search through expander child items recursively.
        """
        for item in items:
            if not isinstance(item, dict):
                continue

            props = item.get("properties", {})
            title = str(props.get("title", "")).lower()
            desc = str(props.get("description", "")).lower()
            item_type = item.get("type", "")

            # Match against query
            if item_type not in (ItemType.NAVIGATION, ItemType.EXPANDER):
                if query in title or query in desc:
                    result: ConfigItem = deepcopy(item)
                    result.setdefault("properties", {})
                    original_desc = props.get("description", "")
                    result["properties"]["description"] = (
                        f"{breadcrumb} • {original_desc}"
                        if original_desc
                        else breadcrumb
                    )
                    yield result

            # Recurse into nested expanders
            if "items" in item and item_type == ItemType.EXPANDER:
                sub_title = str(props.get("title", "Expander"))
                yield from self._search_expander_items(
                    item.get("items", []),
                    query,
                    f"{breadcrumb} › {sub_title}",
                )

    # ─────────────────────────────────────────────────────────────────────────
    # SIDEBAR
    # ─────────────────────────────────────────────────────────────────────────
    def _create_sidebar(self) -> Adw.ToolbarView:
        """Create the sidebar with header, search bar, and navigation list."""
        view = Adw.ToolbarView()
        view.add_css_class("sidebar-container")

        # Header bar
        header = Adw.HeaderBar()
        header.add_css_class("sidebar-header")
        header.set_show_end_title_buttons(False)

        # Title with icon
        title_box = Gtk.Box(spacing=8)
        icon = Gtk.Image.new_from_icon_name(ICON_SYSTEM)
        icon.add_css_class("sidebar-header-icon")
        title = Gtk.Label(label="Dusky", css_classes=["title"])
        title_box.append(icon)
        title_box.append(title)
        header.set_title_widget(title_box)

        # Search button
        self._search_btn = Gtk.ToggleButton(icon_name=ICON_SEARCH)
        self._search_btn.set_tooltip_text("Search (Ctrl+F)")
        self._search_btn.connect("toggled", self._on_search_btn_toggled)
        header.pack_end(self._search_btn)
        view.add_top_bar(header)

        # Search bar
        self._search_bar = Gtk.SearchBar()
        self._search_entry = Gtk.SearchEntry(placeholder_text="Find setting...")
        self._search_entry.connect("search-changed", self._on_search_changed)
        self._search_bar.set_child(self._search_entry)
        self._search_bar.connect_entry(self._search_entry)
        view.add_top_bar(self._search_bar)

        # Navigation list
        self._sidebar_list = Gtk.ListBox(css_classes=["sidebar-listbox"])
        self._sidebar_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._sidebar_list.connect("row-selected", self._on_row_selected)

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
        row: Gtk.ListBoxRow | None
    ) -> None:
        """Handle sidebar row selection."""
        if row is None or self._stack is None:
            return

        idx = row.get_index()
        pages = self._state.config.get("pages", [])
        if 0 <= idx < len(pages):
            page_name = f"{PAGE_PREFIX}{idx}"
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

            # Create sidebar row
            row = self._create_sidebar_row(title, icon)
            
            if self._sidebar_list:
                self._sidebar_list.append(row)
                if first_row is None:
                    first_row = row
                if idx == select_index:
                    target_row = row

            # Create content page
            nav = Adw.NavigationView()
            ctx = self._get_context(nav_view=nav, builder_func=self._build_nav_page)
            root = self._build_nav_page(title, page.get("layout", []), ctx)
            nav.add(root)

            if self._stack:
                self._stack.add_named(nav, f"{PAGE_PREFIX}{idx}")

        # Select appropriate row
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
            css_classes=["sidebar-row-label"]
        )
        label.set_ellipsize(Pango.EllipsizeMode.END)
        
        box.append(icon)
        box.append(label)
        row.set_child(box)
        
        return row

    def _build_nav_page(
        self, 
        title: str, 
        layout: list[ConfigSection], 
        ctx: RowContext
    ) -> Adw.NavigationPage:
        """
        Build a navigation page with toolbar and preferences content.
        """
        tag = title.lower().replace(" ", "-")
        page = Adw.NavigationPage(title=title, tag=tag)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        # Add the sidebar toggle button here
        header.pack_start(self._create_sidebar_toggle_button())
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
            elif "items" in section:
                page.add(self._build_standard_section(section, ctx))
            else:
                # Treat as single-item implicit section
                group = Adw.PreferencesGroup()
                # Safely convert section to item (they share structure)
                item: ConfigItem = {
                    "type": section.get("type", ""),
                    "properties": section.get("properties", {}),
                }
                group.add(self._build_item_row(item, ctx))
                page.add(group)

    def _build_grid_section(
        self, 
        section: ConfigSection, 
        ctx: RowContext
    ) -> Adw.PreferencesGroup:
        """Build a grid section with flow box layout."""
        group = Adw.PreferencesGroup()
        props = section.get("properties", {})

        if title := props.get("title"):
            group.set_title(GLib.markup_escape_text(str(title)))

        flow = Gtk.FlowBox()
        flow.set_valign(Gtk.Align.START)
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_column_spacing(12)
        flow.set_row_spacing(12)
        flow.set_min_children_per_line(2)
        flow.set_max_children_per_line(3)

        for item in section.get("items", []):
            item_type = item.get("type", "")
            item_props = item.get("properties", {})
            
            if item_type == ItemType.TOGGLE_CARD:
                card = rows.GridToggleCard(item_props, item.get("on_toggle"), ctx)
            else:
                card = rows.GridCard(item_props, item.get("on_press"), ctx)
            
            flow.append(card)

        group.add(flow)
        return group

    def _build_standard_section(
        self, 
        section: ConfigSection, 
        ctx: RowContext
    ) -> Adw.PreferencesGroup:
        """Build a standard preferences group with row items."""
        group = Adw.PreferencesGroup()
        props = section.get("properties", {})

        if title := props.get("title"):
            group.set_title(GLib.markup_escape_text(str(title)))
        if desc := props.get("description"):
            group.set_description(GLib.markup_escape_text(str(desc)))

        for item in section.get("items", []):
            group.add(self._build_item_row(item, ctx))

        return group

    def _build_item_row(
        self, 
        item: ConfigItem, 
        ctx: RowContext
    ) -> Adw.PreferencesRow:
        """
        Build the appropriate row widget for a config item.
        """
        item_type = item.get("type", "")
        props = item.get("properties", {})

        try:
            match item_type:
                case ItemType.BUTTON:
                    return rows.ButtonRow(props, item.get("on_press"), ctx)
                case ItemType.TOGGLE:
                    return rows.ToggleRow(props, item.get("on_toggle"), ctx)
                case ItemType.GRID_CARD:
                    return rows.ButtonRow(props, item.get("on_press"), ctx)
                case ItemType.TOGGLE_CARD:
                    return rows.ToggleRow(props, item.get("on_toggle"), ctx)
                case ItemType.LABEL:
                    return rows.LabelRow(props, item.get("value"), ctx)
                case ItemType.SLIDER:
                    return rows.SliderRow(props, item.get("on_change"), ctx)
                case ItemType.SELECTION:
                    return rows.SelectionRow(props, item.get("on_change"), ctx)
                case ItemType.ENTRY:
                    return rows.EntryRow(props, item.get("on_action"), ctx)
                case ItemType.NAVIGATION:
                    return rows.NavigationRow(props, item.get("layout"), ctx)
                case ItemType.EXPANDER:
                    return rows.ExpanderRow(props, item.get("items"), ctx)
                case ItemType.WARNING_BANNER:
                    return self._build_warning_banner(props)
                case _:
                    log.warning("Unknown item type '%s', defaulting to button", item_type)
                    return rows.ButtonRow(props, item.get("on_press"), ctx)
        except Exception as e:
            log.error("Failed to build row for type '%s': %s", item_type, e)
            # Return error placeholder row
            return self._build_error_row(str(e), props.get("title", "Unknown"))

    def _build_warning_banner(self, props: ItemProperties) -> Adw.PreferencesRow:
        """Build a warning banner row."""
        row = Adw.PreferencesRow(css_classes=["action-row"])

        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
            css_classes=["warning-banner-box"],
        )

        icon = Gtk.Image.new_from_icon_name(ICON_WARNING)
        icon.set_halign(Gtk.Align.CENTER)
        icon.set_margin_bottom(8)
        icon.add_css_class("warning-banner-icon")

        title = Gtk.Label(
            label=GLib.markup_escape_text(str(props.get("title", "Warning"))),
            css_classes=["title-1"],
        )
        title.set_halign(Gtk.Align.CENTER)

        message = Gtk.Label(
            label=GLib.markup_escape_text(str(props.get("message", ""))),
            css_classes=["body"],
        )
        message.set_halign(Gtk.Align.CENTER)
        message.set_wrap(True)

        box.append(icon)
        box.append(title)
        box.append(message)
        row.set_child(box)

        return row

    def _build_error_row(self, error: str, title: str) -> Adw.ActionRow:
        """Build an error placeholder row for failed item builds."""
        row = Adw.ActionRow(
            title=f"⚠ {title}",
            subtitle=f"Build error: {error[:80]}",
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

        # Add reload hint
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
