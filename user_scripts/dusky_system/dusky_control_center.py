#!/usr/bin/env python3
"""
Dusky Control Center
A GTK4/Libadwaita configuration launcher for the Dusky Dotfiles.
Fully UWSM-compliant for Arch Linux/Hyprland environments.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

# =============================================================================
# PRE-FLIGHT DEPENDENCY CHECK
# =============================================================================
def preflight_check() -> None:
    """Verify all dependencies are available before proceeding."""
    missing: list[str] = []

    try:
        import yaml  # noqa: F401
    except ImportError:
        missing.append("python-yaml")

    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Gtk, Adw  # noqa: F401
    except (ImportError, ValueError):
        if "python-gobject" not in missing:
            missing.append("python-gobject")
        missing.extend(["gtk4", "libadwaita"])

    if missing:
        unique_missing = list(dict.fromkeys(missing))
        print("\n╭───────────────────────────────────────────────────────────╮")
        print("│  ⚠  Missing Dependencies                                  │")
        print("╰───────────────────────────────────────────────────────────╯")
        print(f"\n  The following packages are required:\n")
        for pkg in unique_missing:
            print(f"    • {pkg}")
        print(f"\n  Install with:\n")
        print(f"    sudo pacman -S --needed {' '.join(unique_missing)}\n")
        sys.exit(1)


preflight_check()

# Safe to import after check
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango
import yaml

# =============================================================================
# CONSTANTS
# =============================================================================
APP_ID = "com.github.dusky.controlcenter"
APP_TITLE = "Dusky Control Center"
CONFIG_FILENAME = "dusky_config.yaml"
SCRIPT_DIR = Path(__file__).resolve().parent

# =============================================================================
# STYLESHEET (Uses System Theme Variables)
# Imported from dusky_style.css
# =============================================================================
CSS = open(SCRIPT_DIR / CSS_FILENAME, "r", encoding="utf-8").read()


# =============================================================================
# MAIN APPLICATION CLASS
# =============================================================================
class DuskyControlCenter(Adw.Application):
    """Main GTK4/Libadwaita Application."""

    def __init__(self) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self.config: dict[str, Any] = {}
        self.sidebar_list: Gtk.ListBox | None = None
        self.stack: Adw.ViewStack | None = None
        self.content_title_label: Gtk.Label | None = None
        self.toast_overlay: Adw.ToastOverlay | None = None

        # Search components
        self.search_bar: Gtk.SearchBar | None = None
        self.search_entry: Gtk.SearchEntry | None = None
        self.search_page: Adw.PreferencesPage | None = None
        self.search_results_group: Adw.PreferencesGroup | None = None
        self.last_visible_page: str | None = None

    def do_activate(self) -> None:
        """Application activation entry point."""
        # Let Adwaita handle theming (respects system preference)
        Adw.StyleManager.get_default()

        self.config = load_config()
        self._apply_css()
        self._build_ui()

    def _apply_css(self) -> None:
        """Load and apply custom stylesheet."""
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _build_ui(self) -> None:
        """Construct the main window and widgets."""
        window = Adw.Window(application=self, title=APP_TITLE)
        window.set_default_size(1180, 780)
        window.set_size_request(800, 600)
        self.toast_overlay = Adw.ToastOverlay()

        # Split view: Sidebar | Content
        split = Adw.OverlaySplitView()
        split.set_min_sidebar_width(200)
        split.set_max_sidebar_width(240)
        split.set_sidebar_width_fraction(0.24)

        split.set_sidebar(self._create_sidebar())
        split.set_content(self._create_content_panel())

        self.toast_overlay.set_child(split)
        window.set_content(self.toast_overlay)

        # Add Search Page container to stack
        self._create_search_page()

        self._populate_pages()
        window.present()

    # ─────────────────────────────────────────────────────────────────────────
    # SEARCH FUNCTIONALITY
    # ─────────────────────────────────────────────────────────────────────────
    def _create_search_page(self) -> None:
        """Initialize the hidden search results page."""
        self.search_page = Adw.PreferencesPage()
        self.search_results_group = Adw.PreferencesGroup(title="Search Results")
        self.search_page.add(self.search_results_group)

        if self.stack:
            self.stack.add_named(self.search_page, "search-results")

    def _on_search_btn_toggled(self, button: Gtk.ToggleButton) -> None:
        """Toggle the visibility of the search bar."""
        if not self.search_bar:
            return

        is_active = button.get_active()
        self.search_bar.set_search_mode(is_active)

        if is_active:
            if self.search_entry:
                self.search_entry.grab_focus()
        else:
            # Closing search: restore previous state
            self._exit_search_mode()

    def _exit_search_mode(self) -> None:
        """Clean up and return from search results view."""
        # Clear search entry for next time
        if self.search_entry:
            self.search_entry.set_text("")

        # Return to previous page
        if self.last_visible_page and self.stack:
            self.stack.set_visible_child_name(self.last_visible_page)

            # Restore the title from the page name
            if self.content_title_label:
                page_title = self._get_page_title_by_id(self.last_visible_page)
                self.content_title_label.set_label(page_title)

    def _get_page_title_by_id(self, page_id: str) -> str:
        """Retrieve page name from config based on stack page ID."""
        if not page_id.startswith("page-"):
            return "Settings"

        try:
            index = int(page_id.split("-", 1)[1])
            pages = self.config.get("pages", [])
            if 0 <= index < len(pages):
                return str(pages[index].get("name", "Settings"))
        except (ValueError, IndexError):
            pass

        return "Settings"

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        """Handle text input in search bar."""
        if not self.stack or not self.search_page or not self.search_results_group:
            return

        query = entry.get_text().strip().lower()

        # Empty query: clear results but stay in search mode
        if not query:
            self._clear_search_results("Search Results")
            return

        # Save current page before switching to search (only once per search session)
        current_page = self.stack.get_visible_child_name()
        if current_page and current_page != "search-results":
            self.last_visible_page = current_page

        # Switch to search view
        self.stack.set_visible_child_name("search-results")
        if self.content_title_label:
            self.content_title_label.set_label("Search")

        # Refresh results
        self._clear_search_results(f"Results for '{query}'")
        self._perform_search(query)

    def _clear_search_results(self, new_title: str) -> None:
        """Remove and recreate the search results group."""
        if self.search_page and self.search_results_group:
            self.search_page.remove(self.search_results_group)
            self.search_results_group = Adw.PreferencesGroup(title=new_title)
            self.search_page.add(self.search_results_group)

    def _perform_search(self, query: str) -> None:
        """Scan config and populate search results."""
        if not self.search_results_group:
            return

        pages = self.config.get("pages", [])
        found_count = 0

        for page in pages:
            page_name = str(page.get("name", "Unknown"))

            for group in page.get("groups", []):
                for item in group.get("items", []):
                    title = str(item.get("title", "")).lower()
                    desc = str(item.get("description", "")).lower()

                    if query in title or query in desc:
                        # Create context-aware copy for display
                        context_item = item.copy()
                        original_desc = item.get("description", "")
                        context_item["description"] = (
                            f"{page_name} • {original_desc}" if original_desc else page_name
                        )

                        row = self._build_action_row(context_item)
                        self.search_results_group.add(row)
                        found_count += 1

        if found_count == 0:
            status = Adw.ActionRow(title="No results found")
            status.set_activatable(False)
            self.search_results_group.add(status)

    # ─────────────────────────────────────────────────────────────────────────
    # SIDEBAR
    # ─────────────────────────────────────────────────────────────────────────
    def _create_sidebar(self) -> Adw.ToolbarView:
        """Build the navigation sidebar."""
        view = Adw.ToolbarView()
        view.add_css_class("sidebar-container")

        # Header bar
        header = Adw.HeaderBar()
        header.add_css_class("sidebar-header")
        header.set_show_end_title_buttons(False)

        title_box = Gtk.Box(spacing=8)
        icon = Gtk.Image.new_from_icon_name("emblem-system-symbolic")
        icon.add_css_class("sidebar-header-icon")
        label = Gtk.Label(label="Dusky")
        label.add_css_class("title")
        title_box.append(icon)
        title_box.append(label)
        header.set_title_widget(title_box)

        # NEW: Search Button
        search_btn = Gtk.ToggleButton(icon_name="system-search-symbolic")
        search_btn.set_tooltip_text("Search Settings")
        search_btn.connect("toggled", self._on_search_btn_toggled)
        header.pack_end(search_btn)
        
        view.add_top_bar(header)

        # NEW: Search Bar (Hidden by default)
        self.search_bar = Gtk.SearchBar()
        self.search_entry = Gtk.SearchEntry(placeholder_text="Find setting...")
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_bar.set_child(self.search_entry)
        self.search_bar.connect_entry(self.search_entry)
        view.add_top_bar(self.search_bar)

        # Scrollable list
        self.sidebar_list = Gtk.ListBox()
        self.sidebar_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.sidebar_list.add_css_class("sidebar-listbox")
        self.sidebar_list.add_css_class("navigation-sidebar")
        self.sidebar_list.connect("row-selected", self._on_row_selected)

        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(self.sidebar_list)

        view.set_content(scroll)
        return view

    def _make_sidebar_row(self, name: str, icon_name: str) -> Gtk.ListBoxRow:
        """Create a styled sidebar navigation row."""
        row = Gtk.ListBoxRow()
        row.add_css_class("sidebar-row")

        box = Gtk.Box(spacing=12)

        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.add_css_class("sidebar-row-icon")

        label = Gtk.Label(label=name, xalign=0, hexpand=True)
        label.add_css_class("sidebar-row-label")
        label.set_ellipsize(Pango.EllipsizeMode.END)

        chevron = Gtk.Image.new_from_icon_name("go-next-symbolic")
        chevron.add_css_class("sidebar-row-chevron")

        box.append(icon)
        box.append(label)
        box.append(chevron)
        row.set_child(box)

        return row

    def _on_row_selected(self, listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        """Handle sidebar selection changes."""
        if row is None:
            return

        index = row.get_index()
        pages = self.config.get("pages", [])

        if 0 <= index < len(pages):
            self.stack.set_visible_child_name(f"page-{index}")
            page_name = str(pages[index].get("name", ""))
            if self.content_title_label:
                self.content_title_label.set_label(page_name)

    # ─────────────────────────────────────────────────────────────────────────
    # CONTENT PANEL
    # ─────────────────────────────────────────────────────────────────────────
    def _create_content_panel(self) -> Adw.ToolbarView:
        """Build the main content area."""
        view = Adw.ToolbarView()

        header = Adw.HeaderBar()
        header.add_css_class("content-header")

        self.content_title_label = Gtk.Label(label="Welcome")
        self.content_title_label.add_css_class("content-title")
        header.set_title_widget(self.content_title_label)

        view.add_top_bar(header)

        self.stack = Adw.ViewStack(vexpand=True, hexpand=True)
        view.set_content(self.stack)

        return view

    # ─────────────────────────────────────────────────────────────────────────
    # POPULATE FROM CONFIG
    # ─────────────────────────────────────────────────────────────────────────
    def _populate_pages(self) -> None:
        """Load pages from configuration into UI."""
        pages = self.config.get("pages", [])

        if not pages:
            self._show_empty_state()
            return

        first_row: Gtk.ListBoxRow | None = None

        for idx, page_data in enumerate(pages):
            name = str(page_data.get("name", "Untitled"))
            icon = str(page_data.get("icon", "application-x-executable-symbolic"))

            # Sidebar entry
            row = self._make_sidebar_row(name, icon)
            self.sidebar_list.append(row)

            # Content page
            pref_page = self._build_pref_page(page_data)
            self.stack.add_named(pref_page, f"page-{idx}")

            if idx == 0:
                first_row = row

        if first_row:
            self.sidebar_list.select_row(first_row)

    def _build_pref_page(self, page_data: dict[str, Any]) -> Adw.PreferencesPage:
        """Build a PreferencesPage from config data."""
        page = Adw.PreferencesPage()

        for group_data in page_data.get("groups", []):
            group = Adw.PreferencesGroup()

            title = str(group_data.get("title", ""))
            if title:
                group.set_title(GLib.markup_escape_text(title))

            desc = str(group_data.get("description", ""))
            if desc:
                group.set_description(GLib.markup_escape_text(desc))

            for item in group_data.get("items", []):
                group.add(self._build_action_row(item))

            page.add(group)

        return page

    def _build_action_row(self, item: dict[str, Any]) -> Adw.ActionRow:
        """Build an ActionRow with run button."""
        row = Adw.ActionRow()
        row.add_css_class("action-row")

        title = str(item.get("title", "Unnamed"))
        subtitle = str(item.get("description", ""))
        icon_name = str(item.get("icon", "utilities-terminal-symbolic"))

        row.set_title(GLib.markup_escape_text(title))
        if subtitle:
            row.set_subtitle(GLib.markup_escape_text(subtitle))

        # Prefix icon with background
        prefix_icon = Gtk.Image.new_from_icon_name(icon_name)
        prefix_icon.add_css_class("action-row-prefix-icon")
        row.add_prefix(prefix_icon)

        # Get custom label or default to "Run"
        btn_label = str(item.get("button_text", "Run"))
        
        # Run button
        run_btn = Gtk.Button(label=btn_label)
        run_btn.add_css_class("run-btn")
        run_btn.add_css_class("suggested-action")
        run_btn.set_valign(Gtk.Align.CENTER)
        run_btn.connect("clicked", self._on_run_clicked, item)

        row.add_suffix(run_btn)
        row.set_activatable_widget(run_btn)

        return row

    def _on_run_clicked(self, button: Gtk.Button, item: dict[str, Any]) -> None:
        """Handle run button click - UWSM-compliant execution."""
        command = str(item.get("command", "")).strip()
        title = str(item.get("title", "Command"))
        use_terminal = bool(item.get("terminal", False))

        if not command:
            self._toast("⚠ No command specified", timeout=3)
            return

        success = execute_command(command, title, use_terminal)

        if success:
            self._toast(f"▶ Launched: {title}")
        else:
            self._toast(f"✖ Failed to launch: {title}", timeout=4)

    # ─────────────────────────────────────────────────────────────────────────
    # EMPTY STATE
    # ─────────────────────────────────────────────────────────────────────────
    def _show_empty_state(self) -> None:
        """Display a helpful empty state when no config is found."""
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=8,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
        )
        box.add_css_class("empty-state-box")

        icon = Gtk.Image.new_from_icon_name("document-open-symbolic")
        icon.add_css_class("empty-state-icon")

        title = Gtk.Label(label="No Configuration Found")
        title.add_css_class("empty-state-title")

        subtitle = Gtk.Label(label="Create a config file to define your control center layout.")
        subtitle.add_css_class("empty-state-subtitle")
        subtitle.set_wrap(True)
        subtitle.set_max_width_chars(50)
        subtitle.set_justify(Gtk.Justification.CENTER)

        hint = Gtk.Label(label=str(SCRIPT_DIR / CONFIG_FILENAME))
        hint.add_css_class("empty-state-hint")
        hint.set_selectable(True)

        box.append(icon)
        box.append(title)
        box.append(subtitle)
        box.append(hint)

        self.stack.add_named(box, "empty-state")
        if self.content_title_label:
            self.content_title_label.set_label("Welcome")

    # ─────────────────────────────────────────────────────────────────────────
    # TOAST NOTIFICATIONS
    # ─────────────────────────────────────────────────────────────────────────
    def _toast(self, message: str, timeout: int = 2) -> None:
        """Show a toast notification."""
        if self.toast_overlay:
            toast = Adw.Toast(title=message, timeout=timeout)
            self.toast_overlay.add_toast(toast)


# =============================================================================
# ENTRY POINT
# =============================================================================
if __name__ == "__main__":
    app = DuskyControlCenter()
    sys.exit(app.run(sys.argv))
