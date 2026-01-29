#!/usr/bin/env python3
"""
Dusky Control Center
A GTK4/Libadwaita configuration launcher for the Dusky Dotfiles.
Fully UWSM-compliant for Arch Linux/Hyprland environments.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
import subprocess

# Local imports
import lib.utility as utility

utility.preflight_check()

# Safe to import after check
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

import lib.rows as rows

# =============================================================================
# CONSTANTS
# =============================================================================
APP_ID = "com.github.dusky.controlcenter"
APP_TITLE = "Dusky Control Center"
CONFIG_FILENAME = "dusky_config.yaml"
SCRIPT_DIR = Path(__file__).resolve().parent
CSS_FILENAME = "dusky_style.css"

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

        # Slider state
        self.slider_changing = False
        self.search_bar: Gtk.SearchBar | None = None
        self.search_entry: Gtk.SearchEntry | None = None
        self.search_page: Adw.PreferencesPage | None = None
        self.search_results_group: Adw.PreferencesGroup | None = None
        self.last_visible_page: str | None = None
        self.last_snapped_value: float | None = None

    def do_activate(self) -> None:
        """Application activation entry point."""
        # Let Adwaita handle theming (respects system preference)
        Adw.StyleManager.get_default()

        self.config = utility.load_config(SCRIPT_DIR / CONFIG_FILENAME)
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
                return str(pages[index].get("title", "Settings"))
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
            page_name = str(page.get("title", "Unknown"))

            for section in page.get("layout", []):
                if section.get("type") == "section":
                    for item in section.get("items", []):
                        title = str(item.get("properties", {}).get("title", "")).lower()
                        desc = str(item.get("properties", {}).get("description", "")).lower()

                        if query in title or query in desc:
                            # Create context-aware copy for display
                            context_item = item.copy()
                            original_desc = item.get("properties", {}).get("description", "")
                            context_item["properties"]["description"] = (
                                f"{page_name} • {original_desc}" if original_desc else page_name
                            )

                            row = self._build_item_row(context_item)
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
            page_name = str(pages[index].get("title", ""))
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
            name = str(page_data.get("title", "Untitled"))
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

        for section_data in page_data.get("layout", []):
            if section_data.get("type") == "section":
                group = Adw.PreferencesGroup()

                title = str(section_data.get("properties", {}).get("title", ""))
                if title:
                    group.set_title(GLib.markup_escape_text(title))

                desc = str(section_data.get("properties", {}).get("description", ""))
                if desc:
                    group.set_description(GLib.markup_escape_text(desc))

                for item in section_data.get("items", []):
                    group.add(self._build_item_row(item))

                page.add(group)
            else:
                # Fallback for non-section items
                # Create a group with no title
                group = Adw.PreferencesGroup()
                group.add(self._build_item_row(section_data))
                page.add(group)

        return page

    def _build_item_row(self, item: dict[str, Any]) -> Adw.ActionRow | Adw.PreferencesRow:
        """Build a row based on item type."""
        item_type = item.get("type")
        properties = item.get("properties", {})
        context = {
            "stack": self.stack,
            "content_title_label": self.content_title_label,
            "config": self.config,
            "sidebar": self.sidebar_list,
            "toast_overlay": self.toast_overlay,
        }

        if item_type == "button":
            #return self._build_button_row(properties, item.get("on_press", {}))
            return rows.ButtonRow(properties, item.get("on_press", {}), context)
        elif item_type == "toggle":
            #return self._build_toggle_row(properties, item.get("on_toggle", {}))
            return rows.ToggleRow(properties, item.get("on_toggle", {}), context)
        elif item_type == "label":
            #return self._build_label_row(properties, item.get("value", {}))
            return rows.LabelRow(properties, item.get("value", {}), context)
        elif item_type == "slider":
            #return self._build_slider_row(properties, item.get("on_change", {}))
            return rows.SliderRow(properties, item.get("on_change", {}), context)
        elif item_type == "warning_banner":
            return self._build_warning_banner_row(properties)
        else:
            # Fallback to button
            return rows.ButtonRow(properties, item.get("on_press", {}), context)
            # return self._build_button_row(properties, item.get("on_press", {}))

    def _build_prefix_icon(self, icon: dict[str, Any]) -> Gtk.Image:
        """Create a prefix icon with background styling."""
        if isinstance(icon, dict):
            icon_type = icon.get("type", "system")
            if icon_type == "file":
                file_path_template = str(icon.get("path", "")).strip()
                if file_path_template:
                    file_path = file_path_template.replace("~", str(Path.home()))
                    icon = Gtk.Image.new_from_file(file_path)
                    icon.add_css_class("action-row-prefix-icon")
                    return icon
            # Fallback to default icon name if type is unknown
            icon = str(icon.get("name", "utilities-terminal-symbolic"))

        prefix_icon = Gtk.Image.new_from_icon_name(icon)
        prefix_icon.add_css_class("action-row-prefix-icon")
        return prefix_icon

    def _build_warning_banner_row(self, properties: dict[str, Any]) -> Adw.PreferencesRow:
        """Build a warning banner row."""
        # Create the wrapper (Adw.PreferencesRow)
        row = Adw.PreferencesRow()
        row.add_css_class("action-row")

        # Create the banner box
        banner_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        banner_box.add_css_class("warning-banner-box")

        warning_icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
        warning_icon.set_halign(Gtk.Align.CENTER)
        warning_icon.set_margin_bottom(8)
        warning_icon.add_css_class("warning-banner-icon")

        title = str(properties.get("title", "Warning"))
        message = str(properties.get("message", ""))

        title_label = Gtk.Label(label=GLib.markup_escape_text(title))
        title_label.add_css_class("title-1")
        title_label.set_halign(Gtk.Align.CENTER)

        message_label = Gtk.Label(label=GLib.markup_escape_text(message))
        message_label.add_css_class("body")
        message_label.set_halign(Gtk.Align.CENTER)
        message_label.set_wrap(True)

        banner_box.append(warning_icon)
        banner_box.append(title_label)
        banner_box.append(message_label)

        row.set_child(banner_box)

        return row

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
