#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ==============================================================================
# ARCH LINUX :: DUSKY THEME :: GTK3 WALLPAPER SELECTOR
# ==============================================================================
# Description: Native, lightning-fast GTK3 replacement for the Rofi wallpaper
#              selector. Features lazy-loading, instant grid mapping, smart
#              mtime caching, live search, and full keyboard navigation.
# ==============================================================================

import os
import sys
import re
import uuid
import time
import shutil
import hashlib
import threading
import subprocess
import argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('Pango', '1.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Gio, Pango

# --- CONSTANTS & PATHS ---
HOME = Path.home()
WALLPAPER_DIR = HOME / "Pictures/wallpapers"
SETTINGS_DIR = HOME / ".config/dusky/settings"
THEME_DIR = SETTINGS_DIR / "dusky_theme"
FAVORITES_FILE = THEME_DIR / "wal_fav_list"
STATE_FILE = THEME_DIR / "state.conf"
FAV_STATE_FILE = THEME_DIR / "current_fav"
TRACK_LIGHT = THEME_DIR / "light_wal"
TRACK_DARK = THEME_DIR / "dark_wal"
THEME_CTL = HOME / "user_scripts/theme_matugen/theme_ctl.sh"

APP_SETTINGS_FILE = THEME_DIR / "gtk_wall_settings"
CACHE_DIR = HOME / ".cache/dusky_images/wallpaper_selector/"
THUMB_DIR = CACHE_DIR / "thumbs"

THUMB_SIZE = 240
RENDER_SIZE = 145
IMAGE_EXTENSIONS = frozenset({'.jpg', '.jpeg', '.png', '.webp', '.gif'})

_NATURAL_SORT_RE = re.compile(r'(\d+)')

def natural_keys(text: str) -> list:
    """Algorithms for natural/version sorting (matches bash 'sort -V')."""
    return [int(c) if c.isdigit() else c.lower() for c in _NATURAL_SORT_RE.split(text)]


def atomic_write(path: Path, content: str):
    """Ensures state and setting files are never corrupted and preserves symlinks."""
    real_path = path.resolve()
    real_path.parent.mkdir(parents=True, exist_ok=True)
    # Safely append tmp suffix without stripping original extension
    tmp_path = real_path.with_name(f"{real_path.name}.tmp.{uuid.uuid4().hex}")
    
    try:
        with open(tmp_path, 'w', encoding='utf-8') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
            
        os.replace(tmp_path, real_path)
        
        try:
            dir_fd = os.open(str(real_path.parent), os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
            
    except OSError as e:
        print(f"Atomic write failed for {real_path}: {e}")
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


# ==============================================================================
# HEADLESS CACHE MANAGER
# ==============================================================================
class CacheManager:
    @staticmethod
    def get_all_wallpapers() -> list[str]:
        """
        Scans the wallpaper directory via a safe recursive traversal.
        Implements st_dev/st_ino tracking and iterator exhaustion to completely
        neutralize symlink loops and prevent File Descriptor exhaustion.
        """
        wallpapers = []
        if not WALLPAPER_DIR.exists():
            return wallpapers

        visited_nodes = set()

        def traverse_dir(virtual_dir: Path):
            try:
                stat = virtual_dir.stat()
                node_id = (stat.st_dev, stat.st_ino)
                if node_id in visited_nodes:
                    return
                visited_nodes.add(node_id)
            except OSError:
                return

            dirs_to_visit = []
            try:
                with os.scandir(virtual_dir) as it:
                    for entry in it:
                        try:
                            is_dir = entry.is_dir(follow_symlinks=True)
                            is_file = entry.is_file(follow_symlinks=True)
                        except OSError:
                            continue

                        virtual_path = virtual_dir / entry.name
                        if is_dir:
                            dirs_to_visit.append(virtual_path)
                        elif is_file:
                            if virtual_path.suffix.lower() in IMAGE_EXTENSIONS:
                                try:
                                    rel = virtual_path.relative_to(WALLPAPER_DIR)
                                    wallpapers.append(str(rel))
                                except ValueError:
                                    wallpapers.append(virtual_path.name)
            except OSError:
                return

            for d in dirs_to_visit:
                traverse_dir(d)

        traverse_dir(WALLPAPER_DIR)
        wallpapers.sort(key=natural_keys)
        return wallpapers

    @staticmethod
    def get_digest(rel_path: str) -> str:
        """Calculates SHA256 digest with aggressive invalidation tagging."""
        return hashlib.sha256((rel_path + "_r24").encode('utf-8')).hexdigest()

    @staticmethod
    def get_thumb_path(rel_path: str) -> Path:
        return THUMB_DIR / f"{CacheManager.get_digest(rel_path)}.png"

    @staticmethod
    def generate_thumb(rel_path: str, force: bool = False) -> bool:
        """
        Thumbnail generation with optional force regeneration.
        When force=False, acts idempotently (skips if cache is fresh).
        When force=True, always regenerates regardless of mtime.
        """
        full_path = WALLPAPER_DIR / rel_path
        if not full_path.exists():
            full_path = Path(rel_path)
            if not full_path.exists():
                return False

        thumb_path = CacheManager.get_thumb_path(rel_path)
        bad_marker_path = thumb_path.with_suffix('.bad')
        tmp_thumb_path = None

        try:
            # Short-circuit empty/0-byte files immediately
            if full_path.stat().st_size == 0:
                bad_marker_path.touch(exist_ok=True)
                return False
        except OSError:
            return False

        try:
            if not force:
                # Fast path out: skip if marked uncacheable and marker is up to date
                if bad_marker_path.exists() and bad_marker_path.stat().st_mtime >= full_path.stat().st_mtime:
                    return False
                # Fast path out: skip if valid cache exists and is up to date
                if thumb_path.exists() and thumb_path.stat().st_mtime >= full_path.stat().st_mtime:
                    return False

            tmp_thumb_path = thumb_path.with_suffix(f'.{uuid.uuid4().hex}.tmp.png')

            # Safely escape characters that trigger Magick's internal parsers
            escaped_path = str(full_path).replace('[', '\\[').replace(']', '\\]').replace('*', '\\*').replace('?', '\\?')
            input_arg = f"{escaped_path}[0]"

            subprocess.run([
                "nice", "-n", "19", "magick", 
                "-limit", "thread", "1",
                "-limit", "memory", "256MiB",  # Prevent RAM exhaustion
                "-limit", "map", "512MiB",     # Prevent map exhaustion
                "-limit", "width", "16384",    # Prevent decompression dimension bombs
                "-limit", "height", "16384",
                "-limit", "time", "14",        # Allow Magick to safely abort right before subprocess SIGKILL
                input_arg, "-auto-orient", "-strip",  
                "-thumbnail", f"{THUMB_SIZE}x{THUMB_SIZE}^",
                "-gravity", "center", "-extent", f"{THUMB_SIZE}x{THUMB_SIZE}",
                "(", "-size", f"{THUMB_SIZE}x{THUMB_SIZE}", "xc:none", "-fill", "white",
                "-draw", f"roundrectangle 0,0,{THUMB_SIZE - 1},{THUMB_SIZE - 1},24,24", ")",
                "-alpha", "set", "-compose", "DstIn", "-composite",
                str(tmp_thumb_path)
            ], check=True, capture_output=True, text=True, timeout=15)

            os.replace(tmp_thumb_path, thumb_path)
            
            # Clean up the .bad marker if processing finally succeeded
            if bad_marker_path.exists():
                try:
                    bad_marker_path.unlink()
                except OSError:
                    pass

            return True

        except subprocess.TimeoutExpired as e:
            print(f"Magick timed out processing {rel_path} after {e.timeout}s. Marking as bad.")
            try:
                bad_marker_path.touch(exist_ok=True)
            except OSError:
                pass
        except subprocess.CalledProcessError as e:
            print(f"Magick failed to process {rel_path}:\n{e.stderr.strip()}\nMarking as bad.")
            try:
                bad_marker_path.touch(exist_ok=True)
            except OSError:
                pass
        except Exception as e:
            print(f"Error processing {rel_path}: {e}")
            try:
                bad_marker_path.touch(exist_ok=True)
            except OSError:
                pass
        finally:
            if tmp_thumb_path:
                try:
                    tmp_thumb_path.unlink(missing_ok=True)
                except OSError:
                    pass

        return False

    @staticmethod
    def sweep_orphaned_cache(valid_wallpapers: list[str]):
        """Garbage collection for deleted wallpapers & outdated thumbnails."""
        print("Sweeping orphaned cache files...")
        valid_digests = {CacheManager.get_digest(w) for w in valid_wallpapers}
        orphans_removed = 0

        if THUMB_DIR.exists():
            try:
                with os.scandir(THUMB_DIR) as it:
                    for entry in it:
                        # Sweep both standard .png cache and .bad markers
                        if entry.is_file() and (entry.name.endswith('.png') or entry.name.endswith('.bad')):
                            # Prevent concurrency race: Only sweep stale tmp files older than 1 hour
                            if '.tmp.' in entry.name:
                                try:
                                    if time.time() - entry.stat().st_mtime > 3600:
                                        os.remove(entry.path)
                                except OSError:
                                    pass
                                continue

                            stem = entry.name.split('.')[0]
                            if stem not in valid_digests:
                                try:
                                    os.remove(entry.path)
                                    orphans_removed += 1
                                except OSError:
                                    pass
            except OSError:
                pass

        print(f"Orphans removed: {orphans_removed}")

    @staticmethod
    def nuke_cache():
        """Completely deletes the entire thumbnail cache directory."""
        if THUMB_DIR.exists():
            print(f"Nuking cache directory: {THUMB_DIR}")
            shutil.rmtree(THUMB_DIR, ignore_errors=True)
        THUMB_DIR.mkdir(parents=True, exist_ok=True)
        print("Cache directory purged and recreated.")

    @staticmethod
    def build_cache(force: bool = False, progress_callback=None) -> list[str]:
        """
        Unified cache builder.
        Returns the finalized list of wallpapers to eliminate redundant I/O calls downstream.
        """
        if force:
            CacheManager.nuke_cache()
        else:
            THUMB_DIR.mkdir(parents=True, exist_ok=True)

        print(f"Scanning directory: {WALLPAPER_DIR}")
        wallpapers = CacheManager.get_all_wallpapers()
        total = len(wallpapers)
        print(f"Found {total} valid images.")

        CacheManager.sweep_orphaned_cache(wallpapers)

        print("Verifying cache and generating thumbnails...")
        workers = min(os.process_cpu_count() or 4, 8)
        generated_count = 0
        last_update = 0.0

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(CacheManager.generate_thumb, w, force): w
                for w in wallpapers
            }

            for i, future in enumerate(as_completed(futures), 1):
                try:
                    if future.result():
                        generated_count += 1
                    
                    now = time.monotonic()
                    if now - last_update > 0.05 or i == total:
                        if progress_callback:
                            progress_callback(i, total, generated_count)
                        else:
                            sys.stdout.write(
                                f"\rProgress: [{i}/{total}] | Generated: {generated_count} "
                            )
                            sys.stdout.flush()
                        last_update = now
                except Exception as e:
                    print(f"\nWorker exception on {futures[future]}: {e}")

        if not progress_callback:
            print()
        print(f"Done! Generated {generated_count} new/updated wallpapers. Cache is warm.")
        return wallpapers


# ==============================================================================
# GTK APPLICATION LOGIC
# ==============================================================================
class WallpaperApp:
    def __init__(self):
        self.Gtk = Gtk
        self.Gdk = Gdk
        self.GdkPixbuf = GdkPixbuf
        self.GLib = GLib
        self.Pango = Pango

        self.app = self.Gtk.Application(
            application_id='com.dusky.wallpaperselector',
            flags=Gio.ApplicationFlags.FLAGS_NONE
        )
        self.app.connect("activate", self.do_activate)
        self.app.connect("shutdown", self.on_app_shutdown)

        self.window = None
        self.scrolled = None
        self.flowbox = None
        self.search_entry = None
        self.stack = None

        self.btn_all = None
        self.btn_fav = None
        self.btn_refresh = None
        self.btn_settings = None
        self.btn_help = None

        self._loading_progress_bar = None
        self._loading_status_label = None

        self.wallpapers = []
        self.favorites = set()
        self.app_settings = {}
        self.search_query = ""

        self.ui_children = {}
        self.loaded_pixbufs = {}
        self.current_generation = 0
        self.current_selected_child = None
        self._is_refreshing = False

        workers = min(os.process_cpu_count() or 4, 8)
        self.executor = ThreadPoolExecutor(max_workers=workers)

        self._load_app_settings()
        self._load_favorites()

    def _load_app_settings(self):
        self.app_settings = {
            "AUTO_CLOSE": False,
            "FAST_APPLY_AUTO_CLOSE": False,
            "SHOW_FILENAMES": True,
            "START_IN_FAVORITES": False,
            "AUTO_SWEEP_CACHE": False
        }

        if APP_SETTINGS_FILE.exists():
            try:
                content = APP_SETTINGS_FILE.read_text(encoding='utf-8')
                for line in content.splitlines():
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        k, v = line.split('=', 1)
                        k = k.strip()
                        v_raw = v.strip()
                        v_lower = v_raw.lower()
                        if v_lower in ('true', '1', 'yes'):
                            self.app_settings[k] = True
                        elif v_lower in ('false', '0', 'no'):
                            self.app_settings[k] = False
                        else:
                            self.app_settings[k] = v_raw
            except Exception as e:
                print(f"Error loading app settings: {e}")

        self.show_only_favorites = self.app_settings.get("START_IN_FAVORITES", False)

    def _save_app_settings(self):
        lines = ["# Dusky GTK Wallpaper Selector Configuration"]
        for k, v in sorted(self.app_settings.items()):
            if isinstance(v, bool):
                val = 'true' if v else 'false'
            else:
                val = str(v)
            lines.append(f"{k}={val}")

        atomic_write(APP_SETTINGS_FILE, "\n".join(lines) + "\n")

    def _load_favorites(self):
        self.favorites.clear()
        if FAVORITES_FILE.exists():
            try:
                content = FAVORITES_FILE.read_text(encoding='utf-8')
                self.favorites.update(filter(None, content.splitlines()))
            except Exception as e:
                print(f"Error loading favorites: {e}")

    def _save_favorites(self):
        atomic_write(FAVORITES_FILE, "\n".join(sorted(self.favorites)) + "\n")

    def set_view_mode(self, show_favorites: bool):
        if not self.btn_all or not self.btn_fav:
            return

        self.show_only_favorites = show_favorites

        if self.show_only_favorites:
            self.btn_all.get_style_context().remove_class("active-all")
            self.btn_fav.get_style_context().add_class("active-fav")
        else:
            self.btn_all.get_style_context().add_class("active-all")
            self.btn_fav.get_style_context().remove_class("active-fav")

        if self.flowbox:
            self.flowbox.invalidate_filter()
            self.GLib.idle_add(self._update_visibility_and_selection)

    def do_activate(self, application):
        if not self.window:
            self.window = self.Gtk.ApplicationWindow(application=application)
            self.window.set_title("Wallpaper Selector")
            self.window.set_default_size(800, 600)
            self.window.set_position(self.Gtk.WindowPosition.CENTER)

            self.window.connect("destroy", self.on_window_destroy)
            self.window.connect("key-press-event", self.on_key_press)

            self.setup_css()

            vbox = self.Gtk.Box(orientation=self.Gtk.Orientation.VERTICAL, spacing=0)
            self.window.add(vbox)

            header = self.Gtk.Box(orientation=self.Gtk.Orientation.HORIZONTAL, spacing=0)
            header.set_name("header_bar")

            left_box = self.Gtk.Box(orientation=self.Gtk.Orientation.HORIZONTAL, spacing=15)

            self.search_entry = self.Gtk.SearchEntry()
            self.search_entry.set_placeholder_text("Search... (Press /)")
            self.search_entry.set_tooltip_text("Filter wallpapers by filename (Press / to focus)")
            self.search_entry.set_width_chars(28)
            self.search_entry.get_style_context().add_class("search-bar")
            self.search_entry.connect("search-changed", self.on_search_changed)
            left_box.pack_start(self.search_entry, False, False, 0)

            center_box = self.Gtk.Box(orientation=self.Gtk.Orientation.HORIZONTAL, spacing=0)

            tab_container = self.Gtk.Box(orientation=self.Gtk.Orientation.HORIZONTAL, spacing=4)
            tab_container.get_style_context().add_class("tab-container")

            self.btn_all = self.Gtk.Button(label="All")
            self.btn_all.get_style_context().add_class("tab-btn")
            self.btn_all.set_tooltip_text("Show all wallpapers")
            self.btn_all.connect("clicked", lambda w: self.set_view_mode(False))

            self.btn_fav = self.Gtk.Button(label="♥")
            self.btn_fav.get_style_context().add_class("tab-btn")
            self.btn_fav.get_style_context().add_class("fav-btn")
            self.btn_fav.set_tooltip_text("Show only favorite wallpapers [Alt+P]")
            self.btn_fav.connect("clicked", lambda w: self.set_view_mode(True))

            if self.show_only_favorites:
                self.btn_fav.get_style_context().add_class("active-fav")
            else:
                self.btn_all.get_style_context().add_class("active-all")

            tab_container.pack_start(self.btn_all, False, False, 0)
            tab_container.pack_start(self.btn_fav, False, False, 0)
            center_box.pack_start(tab_container, False, False, 0)

            right_box = self.Gtk.Box(orientation=self.Gtk.Orientation.HORIZONTAL, spacing=8)

            self.btn_refresh = self.Gtk.Button()
            self.btn_refresh.set_tooltip_text("Rebuild Cache [Alt+R]")
            self.btn_refresh.set_image(
                self.Gtk.Image.new_from_icon_name("view-refresh-symbolic", self.Gtk.IconSize.BUTTON)
            )
            self.btn_refresh.connect("clicked", lambda w: self.trigger_action('refresh'))
            self.btn_refresh.get_style_context().add_class("action-btn")
            self.btn_refresh.get_style_context().add_class("icon-btn")

            self.btn_settings = self.Gtk.Button()
            self.btn_settings.set_tooltip_text("Preferences [Alt+O]")
            self.btn_settings.set_image(
                self.Gtk.Image.new_from_icon_name("preferences-system-symbolic", self.Gtk.IconSize.BUTTON)
            )
            self.btn_settings.connect("clicked", self.show_settings_popover)
            self.btn_settings.get_style_context().add_class("action-btn")
            self.btn_settings.get_style_context().add_class("icon-btn")

            self.btn_help = self.Gtk.Button()
            self.btn_help.set_tooltip_text("Keyboard Shortcuts [F1]")
            self.btn_help.set_image(
                self.Gtk.Image.new_from_icon_name("help-about-symbolic", self.Gtk.IconSize.BUTTON)
            )
            self.btn_help.connect("clicked", self.show_shortcuts_popover)
            self.btn_help.get_style_context().add_class("action-btn")
            self.btn_help.get_style_context().add_class("icon-btn")

            right_box.pack_start(self.btn_refresh, False, False, 0)
            right_box.pack_start(self.btn_settings, False, False, 0)
            right_box.pack_start(self.btn_help, False, False, 0)

            header.pack_start(left_box, False, False, 0)
            header.set_center_widget(center_box)
            header.pack_end(right_box, False, False, 0)

            vbox.pack_start(header, False, False, 0)

            self.stack = self.Gtk.Stack()
            self.stack.set_transition_type(self.Gtk.StackTransitionType.CROSSFADE)
            self.stack.set_transition_duration(150)

            self.scrolled = self.Gtk.ScrolledWindow()
            self.scrolled.set_policy(self.Gtk.PolicyType.NEVER, self.Gtk.PolicyType.AUTOMATIC)
            self.scrolled.set_hexpand(True)
            self.scrolled.set_vexpand(True)

            self.flowbox = self.Gtk.FlowBox()
            self.flowbox.set_valign(self.Gtk.Align.START)
            self.flowbox.set_selection_mode(self.Gtk.SelectionMode.SINGLE)
            self.flowbox.set_min_children_per_line(3)
            self.flowbox.set_max_children_per_line(30)

            self.flowbox.set_sort_func(self.sort_flowbox)
            self.flowbox.set_filter_func(self.filter_flowbox)
            self.flowbox.connect("child-activated", self.on_child_activated)
            self.flowbox.connect("selected-children-changed", self.on_selection_changed)
            self.flowbox.connect("button-press-event", self.on_flowbox_button_press)

            self.scrolled.add(self.flowbox)

            self.stack.add_named(self.scrolled, "grid")
            self.stack.add_named(self._create_empty_state_placeholder(), "empty")
            self.stack.add_named(self._create_loading_state_placeholder(), "loading")

            vbox.pack_start(self.stack, True, True, 0)
            self.window.show_all()

            self.refresh_ui()

        if self.window:
            self.window.present()
            self.flowbox.grab_focus()

    def show_settings_popover(self, widget):
        popover = self.Gtk.Popover.new(widget)
        popover.set_position(self.Gtk.PositionType.BOTTOM)

        box = self.Gtk.Box(orientation=self.Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_start(18)
        box.set_margin_end(18)
        box.set_margin_top(16)
        box.set_margin_bottom(16)

        title = self.Gtk.Label(label="Preferences")
        title.get_style_context().add_class("popover-title")
        title.set_halign(self.Gtk.Align.START)
        box.pack_start(title, False, False, 0)

        grid = self.Gtk.Grid()
        grid.set_column_spacing(24)
        grid.set_row_spacing(14)

        def add_setting(row, label_text, key):
            lbl = self.Gtk.Label(label=label_text)
            lbl.set_halign(self.Gtk.Align.START)

            switch = self.Gtk.Switch()
            switch.set_valign(self.Gtk.Align.CENTER)
            switch.set_halign(self.Gtk.Align.END)
            switch.set_active(self.app_settings.get(key, False))

            def on_toggled(sw, gparam, k=key):
                self.app_settings[k] = sw.get_active()
                self._save_app_settings()
                if k == "SHOW_FILENAMES":
                    self.apply_filename_visibility()

            switch.connect("notify::active", on_toggled)

            grid.attach(lbl, 0, row, 1, 1)
            grid.attach(switch, 1, row, 1, 1)

        add_setting(0, "Auto-close after Full Apply", "AUTO_CLOSE")
        add_setting(1, "Auto-close after Fast Apply", "FAST_APPLY_AUTO_CLOSE")
        add_setting(2, "Show Wallpaper Filenames", "SHOW_FILENAMES")
        add_setting(3, "Default to Favorites View", "START_IN_FAVORITES")
        add_setting(4, "Auto-Sweep Cache on Startup", "AUTO_SWEEP_CACHE")

        box.pack_start(grid, False, False, 0)
        box.show_all()
        popover.add(box)
        popover.popup()

    def show_shortcuts_popover(self, widget):
        popover = self.Gtk.Popover.new(widget)
        popover.set_position(self.Gtk.PositionType.BOTTOM)

        box = self.Gtk.Box(orientation=self.Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_start(18)
        box.set_margin_end(18)
        box.set_margin_top(16)
        box.set_margin_bottom(16)

        title = self.Gtk.Label(label="Keyboard Shortcuts")
        title.get_style_context().add_class("popover-title")
        title.set_halign(self.Gtk.Align.START)
        box.pack_start(title, False, False, 0)

        grid = self.Gtk.Grid()
        grid.set_column_spacing(24)
        grid.set_row_spacing(10)

        shortcuts = [
            ("Apply & Regen Theme", "Enter / L-Click"),
            ("Fast Apply", "Alt+S / R-Click"),
            ("Toggle Favorite (Pin)", "Alt+A / M-Click"),
            ("Toggle Favorites View", "Alt+P"),
            ("Rebuild Cache", "Alt+R"),
            ("Preferences", "Alt+O"),
            ("Keyboard Shortcuts", "F1"),
            ("Focus Search", "Ctrl+F / /"),
            ("Quit Selector", "Esc / Q / Ctrl+C")
        ]

        for i, (desc, keys) in enumerate(shortcuts):
            lbl_desc = self.Gtk.Label(label=desc)
            lbl_desc.set_halign(self.Gtk.Align.START)

            lbl_keys = self.Gtk.Label()
            lbl_keys.set_markup(
                f"<span font_family='monospace' foreground='#a6adc8'><b>{keys}</b></span>"
            )
            lbl_keys.set_halign(self.Gtk.Align.END)

            grid.attach(lbl_desc, 0, i, 1, 1)
            grid.attach(lbl_keys, 1, i, 1, 1)

        box.pack_start(grid, False, False, 0)
        box.show_all()
        popover.add(box)
        popover.popup()

    def on_window_destroy(self, widget):
        self.window = None

    def on_app_shutdown(self, application):
        self.executor.shutdown(wait=False, cancel_futures=True)

    def _create_empty_state_placeholder(self):
        box = self.Gtk.Box(orientation=self.Gtk.Orientation.VERTICAL, spacing=12)
        box.set_halign(self.Gtk.Align.CENTER)
        box.set_valign(self.Gtk.Align.CENTER)

        icon = self.Gtk.Image.new_from_icon_name("edit-find-symbolic", self.Gtk.IconSize.DIALOG)
        icon.set_pixel_size(72)
        icon.get_style_context().add_class("placeholder-icon")

        title = self.Gtk.Label(label="No Wallpapers Found")
        title.get_style_context().add_class("placeholder-title")

        subtitle = self.Gtk.Label(
            label="Try adjusting your search criteria or toggling your favorites view."
        )
        subtitle.get_style_context().add_class("placeholder-subtitle")

        for w in (icon, title, subtitle):
            box.pack_start(w, False, False, 0)
        box.show_all()
        return box

    def _create_loading_state_placeholder(self):
        box = self.Gtk.Box(orientation=self.Gtk.Orientation.VERTICAL, spacing=16)
        box.set_halign(self.Gtk.Align.CENTER)
        box.set_valign(self.Gtk.Align.CENTER)

        spinner = self.Gtk.Spinner()
        spinner.start()
        spinner.set_size_request(64, 64)

        title = self.Gtk.Label(label="Rebuilding Image Cache...")
        title.get_style_context().add_class("placeholder-title")

        subtitle = self.Gtk.Label(
            label="Optimizing thumbnails, analyzing geometry, and sweeping orphans."
        )
        subtitle.get_style_context().add_class("placeholder-subtitle")

        progress_bar = self.Gtk.ProgressBar()
        progress_bar.set_size_request(400, -1)
        progress_bar.set_show_text(True)
        progress_bar.set_text("Preparing...")
        progress_bar.get_style_context().add_class("rebuild-progress")
        self._loading_progress_bar = progress_bar

        status_label = self.Gtk.Label(label="")
        status_label.get_style_context().add_class("placeholder-subtitle")
        self._loading_status_label = status_label

        for w in (spinner, title, subtitle, progress_bar, status_label):
            box.pack_start(w, False, False, 0)
        box.show_all()
        return box

    def setup_css(self):
        css_provider = self.Gtk.CssProvider()
        custom_css = """
        window { background-color: @theme_bg_color; }
        #header_bar {
            background-color: shade(@theme_bg_color, 0.97);
            padding: 10px 14px;
            border-bottom: 1px solid alpha(@theme_fg_color, 0.1);
        }
        .search-bar {
            border-radius: 8px;
            padding: 6px 10px;
            font-size: 0.95em;
            box-shadow: inset 0 1px 3px rgba(0,0,0,0.1);
        }
        .action-btn {
            padding: 5px 12px; border-radius: 8px; font-weight: bold; font-size: 0.9em;
            background-color: alpha(@theme_fg_color, 0.04);
            border: 1px solid alpha(@theme_fg_color, 0.08);
            transition: all 0.2s ease;
        }
        .action-btn:hover {
            background-color: alpha(@theme_selected_bg_color, 0.15);
            border-color: @theme_selected_bg_color;
        }
        .icon-btn {
            padding: 6px 8px;
        }

        .tab-container {
            background-color: alpha(@theme_fg_color, 0.03);
            border: 1px solid alpha(@theme_fg_color, 0.06);
            border-radius: 10px;
            padding: 4px;
            box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.05);
        }
        .tab-btn {
            background-image: none;
            background-color: transparent;
            border: 1px solid transparent;
            border-radius: 6px;
            padding: 6px 24px;
            font-weight: 800;
            font-size: 0.95em;
            color: alpha(@theme_fg_color, 0.5);
            transition: all 0.25s cubic-bezier(0.25, 0.8, 0.25, 1);
        }
        .tab-btn.fav-btn {
            font-size: 1.05em;
        }
        .tab-btn:hover {
            background-color: alpha(@theme_fg_color, 0.05);
            color: alpha(@theme_fg_color, 0.8);
        }
        .tab-btn.active-all {
            background-color: alpha(@theme_fg_color, 0.12);
            color: @theme_fg_color;
            border: 1px solid alpha(@theme_fg_color, 0.1);
            box-shadow: 0px 4px 10px rgba(0,0,0,0.15);
        }
        .tab-btn.active-fav {
            background-color: alpha(#f38ba8, 0.15);
            color: #f38ba8;
            border: 1px solid alpha(#f38ba8, 0.3);
            box-shadow: 0px 4px 12px alpha(#f38ba8, 0.25);
            text-shadow: 0px 1px 3px alpha(#f38ba8, 0.4);
        }

        .popover-title {
            font-weight: 800;
            font-size: 1.1em;
            margin-bottom: 8px;
            color: @theme_selected_bg_color;
            border-bottom: 1px solid alpha(@theme_fg_color, 0.1);
            padding-bottom: 6px;
        }
        stack, scrolledwindow, viewport {
            background-color: @theme_base_color;
        }

        /* THEME THE OVERSCROLL "RUBBER BAND" GLOW */
        scrolledwindow overshoot.top {
            background-image: radial-gradient(farthest-side at top, alpha(@theme_selected_bg_color, 0.2), transparent);
        }
        scrolledwindow overshoot.bottom {
            background-image: radial-gradient(farthest-side at bottom, alpha(@theme_selected_bg_color, 0.2), transparent);
        }

        /* REMOVE THE STATIC UNDERSHOOT "DASHED LINE" IF PRESENT */
        scrolledwindow undershoot.top,
        scrolledwindow undershoot.bottom {
            background-image: none;
            background-color: transparent;
        }

        flowbox {
            background-color: transparent;
            padding: 12px;
        }
        flowboxchild {
            border-radius: 20px; padding: 6px; margin: 4px;
            background-color: transparent; transition: all 0.2s ease;
            border: 2px solid transparent;
        }
        flowboxchild:selected {
            background-color: alpha(@theme_selected_bg_color, 0.15);
            border: 2px solid @theme_selected_bg_color;
            box-shadow: 0px 4px 12px alpha(@theme_selected_bg_color, 0.3);
        }
        flowboxchild:hover {
            background-color: alpha(@theme_selected_bg_color, 0.1);
        }
        .placeholder-box {
            background-color: alpha(@theme_fg_color, 0.05);
            border-radius: 14px;
        }
        .wallpaper-name-overlay {
            background-color: alpha(@theme_bg_color, 0.85); color: @theme_fg_color;
            border-radius: 6px; padding: 4px 8px; font-size: 0.75em; font-weight: bold;
            box-shadow: 0px 2px 4px rgba(0, 0, 0, 0.3);
        }
        .heart-icon {
            color: #f38ba8;
            font-size: 1.5em;
            text-shadow: 0px 2px 5px rgba(0,0,0,0.6);
        }
        .placeholder-icon { color: alpha(@theme_fg_color, 0.4); margin-bottom: 10px; }
        .placeholder-title {
            font-size: 1.5em; font-weight: 800;
            color: alpha(@theme_fg_color, 0.8); margin-bottom: 4px;
        }
        .placeholder-subtitle {
            font-size: 1.0em; color: alpha(@theme_fg_color, 0.5);
        }
        .rebuild-progress {
            border-radius: 6px;
            min-height: 12px;
        }
        .rebuild-progress trough {
            border-radius: 6px;
            min-height: 12px;
            background-color: alpha(@theme_fg_color, 0.08);
        }
        .rebuild-progress progress {
            border-radius: 6px;
            min-height: 12px;
            background-color: @theme_selected_bg_color;
        }
        """

        try:
            css_provider.load_from_data(custom_css.encode('utf-8'))
            self.Gtk.StyleContext.add_provider_for_screen(
                self.Gdk.Screen.get_default(), css_provider,
                self.Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        except Exception as e:
            print(f"CSS Error: {e}")

    def sort_flowbox(self, child1, child2):
        key1 = natural_keys(getattr(child1, 'rel_path', ''))
        key2 = natural_keys(getattr(child2, 'rel_path', ''))
        if key1 < key2:
            return -1
        if key1 > key2:
            return 1
        return 0

    def filter_flowbox(self, child) -> bool:
        rel_path = getattr(child, 'rel_path', '')
        if self.show_only_favorites and rel_path not in self.favorites:
            return False
        if self.search_query and self.search_query not in rel_path.lower():
            return False
        return True

    def _update_visibility_and_selection(self):
        if getattr(self, '_is_refreshing', False):
            return False

        selected = self.flowbox.get_selected_children()
        current_selected = selected[0] if selected else None

        if current_selected and self.filter_flowbox(current_selected):
            self.stack.set_visible_child_name("grid")
            return False

        has_visible = False
        first_visible = None

        for child in self.flowbox.get_children():
            if self.filter_flowbox(child):
                has_visible = True
                first_visible = child
                break

        if has_visible:
            self.stack.set_visible_child_name("grid")
            if first_visible:
                self.flowbox.select_child(first_visible)
        else:
            self.stack.set_visible_child_name("empty")

        return False

    def on_search_changed(self, widget):
        self.search_query = self.search_entry.get_text().lower()
        self.flowbox.invalidate_filter()
        self.GLib.idle_add(self._update_visibility_and_selection)

    def on_selection_changed(self, flowbox):
        selected = flowbox.get_selected_children()

        prev = self.current_selected_child
        if prev and hasattr(prev, 'name_label'):
            prev.name_label.hide()

        if selected:
            self.current_selected_child = selected[0]
            if hasattr(self.current_selected_child, 'name_label'):
                if self.app_settings.get("SHOW_FILENAMES", True):
                    self.current_selected_child.name_label.show()
        else:
            self.current_selected_child = None

    def apply_filename_visibility(self):
        show_labels = self.app_settings.get("SHOW_FILENAMES", True)
        selected = self.flowbox.get_selected_children()
        active_child = selected[0] if selected else None

        if active_child and hasattr(active_child, 'name_label'):
            if show_labels:
                active_child.name_label.show()
            else:
                active_child.name_label.hide()

    def get_current_wallpaper_id(self) -> str:
        state = self.parse_state_conf()
        theme_mode = state.get('THEME_MODE', 'dark')
        track_file = TRACK_LIGHT if theme_mode == "light" else TRACK_DARK

        if track_file.exists():
            try:
                return track_file.read_text(encoding='utf-8').strip()
            except Exception as e:
                print(f"Error reading track file: {e}")
        return ""

    def refresh_ui(self, pre_scanned_wallpapers=None):
        """
        Reconstructs the UI state.
        Uses pre_scanned_wallpapers to skip blocking the UI thread if triggered by a background rebuild.
        """
        self.current_generation += 1

        for child in self.flowbox.get_children():
            self.flowbox.remove(child)
            child.destroy()
            
        self.ui_children.clear()
        self.loaded_pixbufs.clear()

        THUMB_DIR.mkdir(parents=True, exist_ok=True)
        
        if pre_scanned_wallpapers is not None:
            self.wallpapers = pre_scanned_wallpapers
        else:
            self.wallpapers = CacheManager.get_all_wallpapers()

        # Orphan sweep safely relies on file age/timestamp thresholds to avoid race conditions
        if self.app_settings.get("AUTO_SWEEP_CACHE", False):
            self.executor.submit(CacheManager.sweep_orphaned_cache, self.wallpapers)

        current_id = self.get_current_wallpaper_id()
        target_child = None

        for rel_path in self.wallpapers:
            child = self.Gtk.FlowBoxChild()
            child.rel_path = rel_path

            box = self.Gtk.Box()
            box.set_size_request(RENDER_SIZE, RENDER_SIZE)
            box.get_style_context().add_class("placeholder-box")

            spinner = self.Gtk.Spinner()
            spinner.start()
            spinner.set_halign(self.Gtk.Align.CENTER)
            spinner.set_valign(self.Gtk.Align.CENTER)
            box.pack_start(spinner, True, True, 0)

            child.add(box)
            self.flowbox.add(child)
            self.ui_children[rel_path] = child

            if current_id and (rel_path == current_id or os.path.basename(rel_path) == current_id):
                target_child = child

        if self.window:
            self.window.show_all()

        if target_child:
            self.flowbox.select_child(target_child)

        self.flowbox.invalidate_filter()
        self._update_visibility_and_selection()

        scroll_ctx = {'retries': 0}

        def _grab_focus():
            selected = self.flowbox.get_selected_children()
            if selected:
                child = selected[0]
                alloc = child.get_allocation()

                if alloc.height <= 1 and scroll_ctx['retries'] < 20:
                    scroll_ctx['retries'] += 1
                    return True

                child.grab_focus()

                if self.scrolled:
                    adj = self.scrolled.get_vadjustment()
                    row_offset = RENDER_SIZE + 24
                    target_y = alloc.y - row_offset

                    lower = adj.get_lower()
                    upper = adj.get_upper() - adj.get_page_size()

                    if upper > lower:
                        adj.set_value(max(lower, min(target_y, upper)))
            else:
                self.flowbox.grab_focus()
            return False

        self.GLib.timeout_add(16, _grab_focus)

        gen = self.current_generation
        for rel_path in self.wallpapers:
            self.executor.submit(self._load_and_render_image, rel_path, gen)

    def _load_and_render_image(self, rel_path: str, generation: int):
        if generation != self.current_generation:
            return

        CacheManager.generate_thumb(rel_path, force=False)

        if generation != self.current_generation:
            return

        thumb_path = CacheManager.get_thumb_path(rel_path)

        try:
            if not thumb_path.exists():
                # Throw silently so the UI spinner can be cleaned up without terminal spam
                raise FileNotFoundError("Thumbnail not generated (possibly marked as bad)")

            pixbuf = self.GdkPixbuf.Pixbuf.new_from_file_at_scale(
                str(thumb_path), RENDER_SIZE, RENDER_SIZE, True
            )
            self.GLib.idle_add(self._update_ui_child, rel_path, pixbuf, generation)
        except Exception as e:
            # Only print the error if it's NOT just an intentionally skipped bad file
            if not isinstance(e, FileNotFoundError):
                print(f"Failed loading {rel_path} into Pixbuf: {e}")
                # If the thumb actually exists but crashed GdkPixbuf, it's corrupt. Destroy and mark bad.
                if thumb_path.exists():
                    try:
                        thumb_path.unlink(missing_ok=True)
                        CacheManager.get_thumb_path(rel_path).with_suffix('.bad').touch(exist_ok=True)
                    except OSError:
                        pass
                        
            # Handles exceptions by passing None to the UI so the spinner is cleanly destroyed
            self.GLib.idle_add(self._update_ui_child, rel_path, None, generation)

    def _update_ui_child(self, rel_path: str, pixbuf, generation: int = -1):
        if generation != -1 and generation != self.current_generation:
            return False

        self.loaded_pixbufs[rel_path] = pixbuf

        child = self.ui_children.get(rel_path)
        if not child:
            return False

        # Destroy the spinner
        for c in child.get_children():
            child.remove(c)
            c.destroy()
            
        if not pixbuf:
            # Reached if thumbnail failed or doesn't exist. Leaves the empty placeholder box.
            return False

        image = self.Gtk.Image.new_from_pixbuf(pixbuf)
        overlay = self.Gtk.Overlay()
        overlay.add(image)

        if rel_path in self.favorites:
            heart = self.Gtk.Label(label="♥")
            heart.get_style_context().add_class("heart-icon")
            heart.set_halign(self.Gtk.Align.END)
            heart.set_valign(self.Gtk.Align.START)
            heart.set_margin_top(8)
            heart.set_margin_end(8)
            overlay.add_overlay(heart)

        name_label = self.Gtk.Label(label=os.path.basename(rel_path))
        name_label.get_style_context().add_class("wallpaper-name-overlay")
        name_label.set_halign(self.Gtk.Align.END)
        name_label.set_valign(self.Gtk.Align.END)
        name_label.set_margin_bottom(8)
        name_label.set_margin_end(8)
        name_label.set_no_show_all(True)

        child.name_label = name_label
        overlay.add_overlay(name_label)
        overlay.show_all()
        child.add(overlay)

        if self.current_selected_child == child and self.app_settings.get("SHOW_FILENAMES", True):
            name_label.show()

        return False

    def get_selected_path(self):
        selected = self.flowbox.get_selected_children()
        return getattr(selected[0], 'rel_path', None) if selected else None

    def trigger_action(self, action_type: str):
        path = self.get_selected_path()
        match action_type:
            case 'fast':
                if path:
                    self.apply_wallpaper(path, regen=False)
            case 'fav':
                if path:
                    self.toggle_favorite(path)
            case 'toggle':
                self.set_view_mode(not self.show_only_favorites)
            case 'refresh':
                if self._is_refreshing:
                    return

                self._is_refreshing = True
                print("Force rebuilding entire cache...")
                self.stack.set_visible_child_name("loading")

                if self._loading_progress_bar:
                    self._loading_progress_bar.set_fraction(0.0)
                    self._loading_progress_bar.set_text("Preparing...")
                if self._loading_status_label:
                    self._loading_status_label.set_text("")

                def _progress_callback(current, total, generated):
                    fraction = current / total if total > 0 else 0.0
                    text = f"{current} / {total}  ({generated} regenerated)"
                    self.GLib.idle_add(self._update_rebuild_progress, fraction, text)

                def _bg_rebuild():
                    wallpapers = None
                    try:
                        wallpapers = CacheManager.build_cache(force=True, progress_callback=_progress_callback)
                    finally:
                        def _on_done():
                            self._is_refreshing = False
                            self.refresh_ui(wallpapers)
                        self.GLib.idle_add(_on_done)

                threading.Thread(target=_bg_rebuild, daemon=True).start()

    def _update_rebuild_progress(self, fraction: float, text: str):
        if self._loading_progress_bar:
            self._loading_progress_bar.set_fraction(fraction)
            self._loading_progress_bar.set_text(text)
        return False

    def on_child_activated(self, flowbox, child):
        self.apply_wallpaper(getattr(child, 'rel_path', None), regen=True)

    def on_flowbox_button_press(self, widget, event):
        if event.type == self.Gdk.EventType.BUTTON_PRESS:
            if event.button in (2, 3):
                child = self.flowbox.get_child_at_pos(int(event.x), int(event.y))
                if child:
                    rel_path = getattr(child, 'rel_path', None)
                    if rel_path:
                        self.flowbox.select_child(child)

                        if event.button == 3:
                            self.apply_wallpaper(rel_path, regen=False)
                        elif event.button == 2:
                            self.toggle_favorite(rel_path)

                        return True
        return False

    def on_key_press(self, widget, event):
        keyval = event.keyval
        state = event.state

        is_alt = (state & self.Gdk.ModifierType.MOD1_MASK) != 0
        is_ctrl = (state & self.Gdk.ModifierType.CONTROL_MASK) != 0

        if keyval == self.Gdk.KEY_Escape:
            if self.window:
                self.window.close()
            return True

        if keyval in (self.Gdk.KEY_q, self.Gdk.KEY_Q) and not is_alt and not is_ctrl:
            if not self.search_entry.is_focus():
                if self.window:
                    self.window.close()
                return True

        if keyval in (self.Gdk.KEY_c, self.Gdk.KEY_C) and is_ctrl:
            if self.window:
                self.window.close()
            return True

        if keyval == self.Gdk.KEY_F1:
            if self.btn_help:
                self.show_shortcuts_popover(self.btn_help)
            return True

        if keyval in (self.Gdk.KEY_o, self.Gdk.KEY_O) and is_alt:
            if self.btn_settings:
                self.show_settings_popover(self.btn_settings)
            return True

        if self.search_entry.is_focus():
            return False

        if keyval == self.Gdk.KEY_slash and not is_alt and not is_ctrl:
            self.search_entry.grab_focus()
            return True

        if keyval in (self.Gdk.KEY_f, self.Gdk.KEY_F) and is_ctrl:
            self.search_entry.grab_focus()
            return True

        rel_path = self.get_selected_path()

        match keyval:
            case self.Gdk.KEY_Return | self.Gdk.KEY_KP_Enter:
                if rel_path:
                    self.apply_wallpaper(rel_path, regen=True)
                return True

            case self.Gdk.KEY_s | self.Gdk.KEY_S if is_alt:
                if rel_path:
                    self.apply_wallpaper(rel_path, regen=False)
                return True

            case self.Gdk.KEY_a | self.Gdk.KEY_A if is_alt:
                if rel_path:
                    self.toggle_favorite(rel_path)
                return True

            case self.Gdk.KEY_p | self.Gdk.KEY_P if is_alt:
                self.trigger_action('toggle')
                return True

            case self.Gdk.KEY_r | self.Gdk.KEY_R if is_alt:
                self.trigger_action('refresh')
                return True

        return False

    def toggle_favorite(self, rel_path: str):
        if rel_path in self.favorites:
            self.favorites.remove(rel_path)
        else:
            self.favorites.add(rel_path)

        self._save_favorites()

        if rel_path in self.loaded_pixbufs:
            self._update_ui_child(rel_path, self.loaded_pixbufs[rel_path], self.current_generation)

        if self.show_only_favorites:
            self.flowbox.invalidate_filter()
            self.GLib.idle_add(self._update_visibility_and_selection)

    def parse_state_conf(self) -> dict[str, str]:
        state = {}
        if STATE_FILE.exists():
            try:
                content = STATE_FILE.read_text(encoding='utf-8')
                for line in content.splitlines():
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        k, v = line.split('=', 1)
                        state[k.strip()] = v.strip().strip("'").strip('"')
            except Exception as e:
                print(f"Error reading state file: {e}")
        return state

    def update_trackers(self, rel_path: str, theme_mode: str):
        basename = os.path.basename(rel_path)
        track_file = TRACK_LIGHT if theme_mode == "light" else TRACK_DARK
        atomic_write(track_file, f"{basename}\n")
        atomic_write(FAV_STATE_FILE, f"{basename}\n")

    def apply_wallpaper(self, rel_path: str, regen: bool):
        if not rel_path:
            return
        full_path = WALLPAPER_DIR / rel_path

        if not full_path.exists():
            full_path = Path(rel_path)
            if not full_path.exists():
                print(f"Error: Path {full_path} does not exist.")
                return

        print(f"Applying: {full_path} (Regen: {regen})")

        should_close = False
        if regen and self.app_settings.get("AUTO_CLOSE", False):
            should_close = True
        elif not regen and self.app_settings.get("FAST_APPLY_AUTO_CLOSE", False):
            should_close = True

        if should_close and self.window:
            self.window.hide()

        state = self.parse_state_conf()
        theme_mode = state.get('THEME_MODE', 'dark')
        self.update_trackers(rel_path, theme_mode)

        awww_cmd = ["uwsm-app", "--", "awww", "img"]

        def add_opt(key, flag):
            val = state.get(key, 'disable')
            if val and val != 'disable':
                awww_cmd.extend([flag, val])

        add_opt('AWWW_TRANS_TYPE', '--transition-type')
        add_opt('AWWW_TRANS_DURATION', '--transition-duration')
        add_opt('AWWW_TRANS_FPS', '--transition-fps')
        add_opt('AWWW_TRANS_BEZIER', '--transition-bezier')
        add_opt('AWWW_TRANS_ANGLE', '--transition-angle')
        add_opt('AWWW_TRANS_POS', '--transition-pos')
        awww_cmd.append(str(full_path))

        self.app.hold()

        def _exec_backend():
            success = False
            err_msg = ""
            try:
                subprocess.run(awww_cmd, check=True, capture_output=True, text=True)
                if regen:
                    subprocess.run(
                        [str(THEME_CTL), "refresh"], check=True, capture_output=True, text=True
                    )
                success = True
            except subprocess.CalledProcessError as e:
                err_msg = e.stderr.strip() if e.stderr else str(e)
                print(f"Backend execution failed: {err_msg}")
            except Exception as e:
                err_msg = str(e)
                print(f"Unexpected execution error: {err_msg}")
            finally:
                self.GLib.idle_add(self._on_backend_complete, success, err_msg, should_close)

        threading.Thread(target=_exec_backend, daemon=True).start()

    def _on_backend_complete(self, success: bool, err_msg: str, should_close: bool):
        try:
            if not success:
                if self.window:
                    self.window.present()
                    dialog = self.Gtk.MessageDialog(
                        transient_for=self.window,
                        flags=self.Gtk.DialogFlags.MODAL,
                        message_type=self.Gtk.MessageType.ERROR,
                        buttons=self.Gtk.ButtonsType.OK,
                        text="Theme Application Failed"
                    )
                    dialog.format_secondary_text(
                        f"The backend process encountered an error:\n\n{err_msg}"
                    )

                    def on_dialog_response(dlg, response_id):
                        dlg.destroy()

                    dialog.connect("response", on_dialog_response)
                    dialog.show_all()
            elif should_close and self.window:
                self.window.close()
        finally:
            self.app.release()
        return False

    def run(self):
        return self.app.run([sys.argv[0]])


# ==============================================================================
# ENTRY POINT & CLI PARSING
# ==============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dusky Theme GTK3 Wallpaper Selector")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        '--build-cache', action='store_true',
        help="Idempotent: generate missing thumbnails and sweep orphans, then exit."
    )
    group.add_argument(
        '--rebuild-cache', action='store_true',
        help="Force: nuke entire cache directory and regenerate all thumbnails, then exit."
    )
    group.add_argument('--precache', action='store_true', help=argparse.SUPPRESS)

    args, unknown = parser.parse_known_args()

    if args.rebuild_cache:
        CacheManager.build_cache(force=True)
        sys.exit(0)
    elif args.build_cache or args.precache:
        CacheManager.build_cache(force=False)
        sys.exit(0)
    else:
        selector = WallpaperApp()
        exit_status = selector.run()
        sys.exit(exit_status)
