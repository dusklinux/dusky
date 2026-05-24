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
import fcntl
import hashlib
import threading
import subprocess
import argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- CONSTANTS & PATHS ---
HOME = Path.home()
WALLPAPER_DIR = HOME / "Pictures/wallpapers"
SETTINGS_DIR = HOME / ".config/dusky/settings"
THEME_DIR = SETTINGS_DIR / "dusky_theme"
FAVORITES_FILE = THEME_DIR / "wal_fav_rofi"
STATE_FILE = THEME_DIR / "state.conf"
FAV_STATE_FILE = THEME_DIR / "current_fav"
TRACK_LIGHT = THEME_DIR / "light_wal"
TRACK_DARK = THEME_DIR / "dark_wal"
THEME_CTL = HOME / "user_scripts/theme_matugen/theme_ctl.sh"

CACHE_DIR = HOME / ".cache/rofi-wallpaper-thumbs/v4-300"
THUMB_DIR = CACHE_DIR / "thumbs"

# Safe fallback for XDG_RUNTIME_DIR if run from raw TTY
_xdg_runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
LOCK_FILE = Path(_xdg_runtime) / "gtk-wallpaper-selector.lock"
GTK_CSS_PATH = HOME / ".config/gtk-3.0/gtk.css"

THUMB_SIZE = 240
RENDER_SIZE = 145
IMAGE_EXTENSIONS = frozenset({'.jpg', '.jpeg', '.png', '.webp', '.gif'})

_NATURAL_SORT_RE = re.compile(r'(\d+)')

def natural_keys(text: str) -> list:
    """Algorithms for natural/version sorting (matches bash 'sort -V')."""
    return [int(c) if c.isdigit() else c.lower() for c in _NATURAL_SORT_RE.split(text)]


# ==============================================================================
# HEADLESS CACHE MANAGER
# ==============================================================================
class CacheManager:
    @staticmethod
    def get_all_wallpapers() -> list[str]:
        """Scans the directory following symlinks, returning sorted relative paths."""
        wallpapers = []
        if WALLPAPER_DIR.exists():
            for root, _, files in os.walk(WALLPAPER_DIR, followlinks=True):
                root_path = Path(root)
                for f in files:
                    if Path(f).suffix.lower() in IMAGE_EXTENSIONS:
                        path = root_path / f
                        wallpapers.append(str(path.relative_to(WALLPAPER_DIR)))
        wallpapers.sort(key=natural_keys)
        return wallpapers

    @staticmethod
    def get_thumb_path(rel_path: str) -> Path:
        digest = hashlib.sha256(rel_path.encode('utf-8')).hexdigest()
        return THUMB_DIR / f"{digest}.png"

    @staticmethod
    def generate_thumb_if_needed(rel_path: str) -> bool:
        """
        Idempotent thumbnail generation using Atomic POSIX writes.
        Returns True if a new thumbnail was generated, False if already cached.
        """
        full_path = WALLPAPER_DIR / rel_path
        thumb_path = CacheManager.get_thumb_path(rel_path)
        
        try:
            # Check idempotency condition (O(1) filesystem stat)
            if thumb_path.exists() and thumb_path.stat().st_mtime >= full_path.stat().st_mtime:
                return False
                
            tmp_thumb_path = thumb_path.with_suffix('.tmp.png')
            
            # ImageMagick processing -> write to temp file
            subprocess.run([
                "nice", "-n", "19", "magick", "-limit", "thread", "1",
                str(full_path), "-auto-orient", "-strip", 
                "-thumbnail", f"{THUMB_SIZE}x{THUMB_SIZE}^", 
                "-gravity", "center", "-extent", f"{THUMB_SIZE}x{THUMB_SIZE}", 
                str(tmp_thumb_path)
            ], check=True, stderr=subprocess.DEVNULL)
            
            # Atomic POSIX rename prevents corrupted cache if process is SIGKILL'd
            os.replace(tmp_thumb_path, thumb_path)
            return True
                
        except subprocess.CalledProcessError:
            print(f"Magick failed to process: {rel_path}")
            if 'tmp_thumb_path' in locals() and tmp_thumb_path.exists():
                tmp_thumb_path.unlink()
        except Exception as e:
            print(f"Error processing {rel_path}: {e}")
            
        return False

    @staticmethod
    def sweep_orphaned_cache(valid_wallpapers: list[str]):
        """Garbage collection for deleted wallpapers."""
        print("Sweeping orphaned cache files...")
        valid_digests = {hashlib.sha256(w.encode('utf-8')).hexdigest() for w in valid_wallpapers}
        orphans_removed = 0
        
        if THUMB_DIR.exists():
            with os.scandir(THUMB_DIR) as it:
                for entry in it:
                    if entry.is_file() and entry.name.endswith('.png'):
                        if entry.name.endswith('.tmp.png'):
                            os.remove(entry.path)
                            continue
                            
                        stem = entry.name[:-4]  # Remove .png
                        if stem not in valid_digests:
                            os.remove(entry.path)
                            orphans_removed += 1
                            
        print(f"Orphans removed: {orphans_removed}")

    @staticmethod
    def precache_all():
        """Headless multithreaded CLI cache generation mode."""
        THUMB_DIR.mkdir(parents=True, exist_ok=True)
        print(f"Scanning directory: {WALLPAPER_DIR}")
        
        wallpapers = CacheManager.get_all_wallpapers()
        print(f"Found {len(wallpapers)} valid images.")
        
        CacheManager.sweep_orphaned_cache(wallpapers)

        print("Verifying cache and generating missing thumbnails...")
        workers = min(os.cpu_count() or 4, 8)
        generated_count = 0
        
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(CacheManager.generate_thumb_if_needed, w): w for w in wallpapers}
            
            for i, future in enumerate(as_completed(futures), 1):
                try:
                    if future.result():
                        generated_count += 1
                    sys.stdout.write(f"\rProgress: [{i}/{len(wallpapers)}] | Generated: {generated_count} ")
                    sys.stdout.flush()
                except Exception as e:
                    print(f"\nWorker exception on {futures[future]}: {e}")
                    
        print(f"\nDone! Pre-cached {generated_count} new/updated wallpapers. Cache is warm.")


# ==============================================================================
# GTK APPLICATION LOGIC
# ==============================================================================
class WallpaperApp:
    def __init__(self):
        # Deferred imports guarantee no graphical socket initializations in TTY/Cron
        import gi
        gi.require_version('Gtk', '3.0')
        gi.require_version('Gdk', '3.0')
        gi.require_version('GdkPixbuf', '2.0')
        gi.require_version('Pango', '1.0') # Loaded for UI text ellipsize safeties
        from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Gio, Pango
        
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
        
        self.window = None
        self.scrolled = None
        self.flowbox = None
        self.search_entry = None
        self.stack = None
        
        self.wallpapers = []
        self.favorites = set()
        self.show_only_favorites = False
        self.search_query = ""
        
        self.ui_children = {}
        self.loaded_pixbufs = {}
        self.current_generation = 0
        self.current_selected_child = None
        
        workers = min(os.cpu_count() or 4, 8)
        self.executor = ThreadPoolExecutor(max_workers=workers)

        self.lock_fd = None
        self._acquire_lock()
        self._load_favorites()

    def _acquire_lock(self):
        try:
            LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
            self.lock_fd = open(LOCK_FILE, 'w')
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another instance is already running. Exiting.")
            sys.exit(0)

    def _load_favorites(self):
        self.favorites.clear()
        if FAVORITES_FILE.exists():
            try:
                content = FAVORITES_FILE.read_text(encoding='utf-8')
                self.favorites.update(filter(None, content.splitlines()))
            except Exception as e:
                print(f"Error loading favorites: {e}")

    def _save_favorites(self):
        FAVORITES_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(FAVORITES_FILE, 'w') as f:
                f.write("\n".join(sorted(self.favorites)) + "\n")
        except Exception as e:
            print(f"Error saving favorites: {e}")

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

            # --- HEADER ---
            header = self.Gtk.Box(orientation=self.Gtk.Orientation.HORIZONTAL, spacing=15)
            header.set_name("header_bar")
            
            self.search_entry = self.Gtk.SearchEntry()
            self.search_entry.set_placeholder_text("Search... (Press /)")
            self.search_entry.set_tooltip_text("Filter wallpapers by filename (Press / to focus)")
            self.search_entry.set_width_chars(26)  # Widened for comfort
            self.search_entry.get_style_context().add_class("search-bar")
            self.search_entry.connect("search-changed", self.on_search_changed)
            header.pack_start(self.search_entry, False, False, 0)
            
            # --- SHORTCUTS IN HEADER ---
            shortcuts_data = [
                ("RMB", "Fast Apply"),
                ("MMB", "Favorite"),
                ("Alt+T", "Toggle Favs"),
                ("Alt+Y", "Rebuild Cache")
            ]
            
            markup_parts = [
                f"<span background='#313244' foreground='#cdd6f4' font_family='monospace' size='7500'><b> {k} </b></span> "
                f"<span size='7800' foreground='#a6adc8'>{d}</span>"
                for k, d in shortcuts_data
            ]
            
            shortcuts_label = self.Gtk.Label()
            shortcuts_label.set_use_markup(True)
            shortcuts_label.set_markup("  •  ".join(markup_parts))
            shortcuts_label.set_halign(self.Gtk.Align.CENTER)
            # Ellipsize prevents the label from pushing other UI elements offscreen if the window shrinks
            shortcuts_label.set_ellipsize(self.Pango.EllipsizeMode.END) 
            
            # The shortcuts label elegantly absorbs the middle spacer area
            header.pack_start(shortcuts_label, True, True, 0)

            # --- ACTION BUTTONS ---
            action_box = self.Gtk.Box(orientation=self.Gtk.Orientation.HORIZONTAL, spacing=8)
            
            btn_toggle = self.Gtk.Button(label="♥")
            btn_toggle.set_tooltip_text("Toggle view to show only favorite wallpapers [Alt+T]")
            btn_toggle.connect("clicked", lambda w: self.trigger_action('toggle'))
            btn_toggle.get_style_context().add_class("action-btn")
            btn_toggle.get_style_context().add_class("toggle-btn")

            action_box.pack_start(btn_toggle, False, False, 0)

            header.pack_start(action_box, False, False, 0)
            vbox.pack_start(header, False, False, 0)

            # --- STACK FOR GRID/EMPTY ---
            self.stack = self.Gtk.Stack()
            self.stack.set_transition_type(self.Gtk.StackTransitionType.CROSSFADE)
            self.stack.set_transition_duration(150)

            self.scrolled = self.Gtk.ScrolledWindow()
            self.scrolled.set_policy(self.Gtk.PolicyType.NEVER, self.Gtk.PolicyType.AUTOMATIC)
            self.scrolled.set_hexpand(True)
            self.scrolled.set_vexpand(True)

            self.flowbox = self.Gtk.FlowBox()
            self.flowbox.set_valign(self.Gtk.Align.START) # Keeps images anchored to the top perfectly
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
            
            vbox.pack_start(self.stack, True, True, 0)
            self.window.show_all()
            
            # Initiate async load pipeline
            self.refresh_ui()

        self.window.present()
        self.flowbox.grab_focus()

    def on_window_destroy(self, widget):
        print("Shutting down... killing background workers.")
        self.executor.shutdown(wait=False, cancel_futures=True)
        if self.lock_fd:
            try:
                fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
                self.lock_fd.close()
                LOCK_FILE.unlink(missing_ok=True)
            except Exception:
                pass

    def _create_empty_state_placeholder(self):
        box = self.Gtk.Box(orientation=self.Gtk.Orientation.VERTICAL, spacing=12)
        box.set_halign(self.Gtk.Align.CENTER)
        box.set_valign(self.Gtk.Align.CENTER)
        
        icon = self.Gtk.Image.new_from_icon_name("edit-find-symbolic", self.Gtk.IconSize.DIALOG)
        icon.set_pixel_size(72)
        icon.get_style_context().add_class("placeholder-icon")
        
        title = self.Gtk.Label(label="No Wallpapers Found")
        title.get_style_context().add_class("placeholder-title")
        
        subtitle = self.Gtk.Label(label="Try adjusting your search criteria or toggling your favorites view.")
        subtitle.get_style_context().add_class("placeholder-subtitle")
        
        for w in (icon, title, subtitle):
            box.pack_start(w, False, False, 0)
        box.show_all()
        return box

    def setup_css(self):
        css_provider = self.Gtk.CssProvider()
        custom_css = """
        window { background-color: @window_bg_color; }
        #header_bar {
            background-color: shade(@window_bg_color, 0.97);
            padding: 10px 14px;
            border-bottom: 1px solid alpha(@window_fg_color, 0.1);
        }
        .search-bar { 
            border-radius: 8px; 
            padding: 6px 10px; 
            font-size: 0.95em; 
            box-shadow: inset 0 1px 3px rgba(0,0,0,0.1);
        }
        .action-btn {
            padding: 5px 12px; border-radius: 8px; font-weight: bold; font-size: 0.9em;
            background-color: alpha(@window_fg_color, 0.04);
            border: 1px solid alpha(@window_fg_color, 0.08);
            transition: all 0.2s ease;
        }
        .action-btn:hover { 
            background-color: alpha(@accent_color, 0.15); 
            border-color: @accent_color; 
        }
        .toggle-btn {
            font-size: 1.15em;
            padding: 4px 10px;
            color: #f38ba8;
        }
        
        /* FIX FOR BLACK BOTTOM AREA: 
           When Flowbox shrinks (due to valign=START), it exposes the container underneath.
           We force all underlying scroll/stack wrappers to inherit the view background color. */
        stack, scrolledwindow, viewport {
            background-color: @view_bg_color;
        }
        
        flowbox { 
            background-color: transparent; 
            padding: 12px; 
        }
        flowboxchild {
            border-radius: 12px; padding: 6px; margin: 4px;
            background-color: transparent; transition: all 0.2s ease;
        }
        flowboxchild:selected { 
            background-color: @accent_bg_color; 
            outline: 2px solid @accent_color;
            outline-offset: -2px;
            box-shadow: 0px 4px 12px alpha(@accent_color, 0.3);
        }
        flowboxchild:hover { 
            background-color: alpha(@accent_color, 0.1); 
        }
        .placeholder-box { 
            background-color: alpha(@window_fg_color, 0.05); 
            border-radius: 10px; 
        }
        .wallpaper-name-overlay {
            background-color: alpha(@window_bg_color, 0.85); color: @window_fg_color;
            border-radius: 6px; padding: 4px 8px; font-size: 0.75em; font-weight: bold;
            box-shadow: 0px 2px 4px rgba(0, 0, 0, 0.3);
        }
        .heart-icon {
            color: #f38ba8;
            font-size: 1.5em;
            text-shadow: 0px 2px 5px rgba(0,0,0,0.6);
        }
        .placeholder-icon { color: alpha(@window_fg_color, 0.4); margin-bottom: 10px; }
        .placeholder-title { font-size: 1.5em; font-weight: 800; color: alpha(@window_fg_color, 0.8); margin-bottom: 4px; }
        .placeholder-subtitle { font-size: 1.0em; color: alpha(@window_fg_color, 0.5); }
        """

        final_css = ""
        if GTK_CSS_PATH.exists():
            try: final_css += GTK_CSS_PATH.read_text(encoding='utf-8') + "\n"
            except Exception as e: print(f"Warning: Could not read {GTK_CSS_PATH}: {e}")

        final_css += custom_css

        try:
            css_provider.load_from_data(final_css.encode('utf-8'))
            self.Gtk.StyleContext.add_provider_for_screen(
                self.Gdk.Screen.get_default(), css_provider, self.Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        except Exception as e: print(f"CSS Error: {e}")

    def sort_flowbox(self, child1, child2):
        key1 = natural_keys(getattr(child1, 'rel_path', ''))
        key2 = natural_keys(getattr(child2, 'rel_path', ''))
        return -1 if key1 < key2 else (1 if key1 > key2 else 0)

    def filter_flowbox(self, child) -> bool:
        rel_path = getattr(child, 'rel_path', '')
        if self.show_only_favorites and rel_path not in self.favorites: return False
        if self.search_query and self.search_query not in rel_path.lower(): return False
        return True

    def _update_visibility_and_selection(self):
        """O(1) Smart Grid Selection Updater. Prevents SearchEntry from destroying Grid Focus."""
        selected = self.flowbox.get_selected_children()
        current_selected = selected[0] if selected else None
        
        # O(1) Fast path: If current selection is still valid and visible, keep it!
        if current_selected and self.filter_flowbox(current_selected):
            self.stack.set_visible_child_name("grid")
            return False
            
        # O(N) Fallback: Find the very first visible child (Happens during typing)
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
        
        if getattr(self, 'current_selected_child', None):
            if hasattr(self.current_selected_child, 'name_label'):
                self.current_selected_child.name_label.hide()
                
        if selected:
            self.current_selected_child = selected[0]
            if hasattr(self.current_selected_child, 'name_label'):
                self.current_selected_child.name_label.show()
        else:
            self.current_selected_child = None

    def get_current_wallpaper_id(self) -> str:
        """Parses active theme tracking to determine what wallpaper is currently live."""
        state = self.parse_state_conf()
        theme_mode = state.get('THEME_MODE', 'dark')
        track_file = TRACK_LIGHT if theme_mode == "light" else TRACK_DARK
        
        if track_file.exists():
            try:
                return track_file.read_text(encoding='utf-8').strip()
            except Exception as e:
                print(f"Error reading track file: {e}")
        return ""

    def refresh_ui(self):
        self.current_generation += 1
        
        for child in self.flowbox.get_children(): self.flowbox.remove(child)
        self.ui_children.clear()
        self.loaded_pixbufs.clear()  # Prevent strict memory leaks on reloads

        THUMB_DIR.mkdir(parents=True, exist_ok=True)
        self.wallpapers = CacheManager.get_all_wallpapers()

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

            # Match via ID (Theme_ctl handles full paths, GUI stores basenames - we check both to be safe)
            if current_id and (rel_path == current_id or os.path.basename(rel_path) == current_id):
                target_child = child

        self.window.show_all()
        
        # Inject the active wallpaper selection BEFORE filter validation
        if target_child:
            self.flowbox.select_child(target_child)
            
        self.flowbox.invalidate_filter()
        self._update_visibility_and_selection()

        # Idle execution guarantees GTK Layout is fully resolved before forcibly snatching focus
        # We override standard layout scrolling here to align the active wallpaper on the 1st/2nd line.
        scroll_ctx = {'retries': 0}
        
        def _grab_focus():
            selected = self.flowbox.get_selected_children()
            if selected:
                child = selected[0]
                alloc = child.get_allocation()
                
                # Wait for GTK to assign actual 2D layout coordinates (bypasses default 1x1 size)
                if alloc.height <= 1 and scroll_ctx['retries'] < 20:
                    scroll_ctx['retries'] += 1
                    return True
                    
                # GTK native focus (naturally places item glued to the absolute bottom of viewport)
                child.grab_focus()
                
                # Override and scroll beautifully to the 1st or 2nd line.
                if self.scrolled:
                    adj = self.scrolled.get_vadjustment()
                    # Updated for slightly larger UI padding
                    row_offset = RENDER_SIZE + 24 
                    target_y = alloc.y - row_offset
                    
                    lower = adj.get_lower()
                    upper = adj.get_upper() - adj.get_page_size()
                    
                    if upper > lower:
                        # Clamp the adjustment safely within bounds
                        adj.set_value(max(lower, min(target_y, upper)))
            else:
                self.flowbox.grab_focus()
            return False
            
        self.GLib.idle_add(_grab_focus)

        for rel_path in self.wallpapers:
            self.executor.submit(self._load_and_render_image, rel_path, self.current_generation)

    def _load_and_render_image(self, rel_path: str, generation: int):
        if generation != self.current_generation: return

        CacheManager.generate_thumb_if_needed(rel_path)
        thumb_path = CacheManager.get_thumb_path(rel_path)

        try:
            pixbuf = self.GdkPixbuf.Pixbuf.new_from_file_at_scale(str(thumb_path), RENDER_SIZE, RENDER_SIZE, True)
            self.loaded_pixbufs[rel_path] = pixbuf
            self.GLib.idle_add(self._update_ui_child, rel_path, pixbuf, generation)
        except Exception as e:
            print(f"Failed loading {rel_path} into Pixbuf: {e}")

    def _update_ui_child(self, rel_path: str, pixbuf, generation: int = -1):
        if generation != -1 and generation != self.current_generation: return False

        child = self.ui_children.get(rel_path)
        if not child: return False

        for c in child.get_children(): child.remove(c)
        if not pixbuf: return False

        image = self.Gtk.Image.new_from_pixbuf(pixbuf)
        overlay = self.Gtk.Overlay()
        overlay.add(image)

        if rel_path in self.favorites:
            heart = self.Gtk.Label(label="♥")
            heart.get_style_context().add_class("heart-icon")
            heart.set_halign(self.Gtk.Align.END)
            heart.set_valign(self.Gtk.Align.START)
            heart.set_margin_top(6)
            heart.set_margin_end(6)
            overlay.add_overlay(heart)

        name_label = self.Gtk.Label(label=os.path.basename(rel_path))
        name_label.get_style_context().add_class("wallpaper-name-overlay")
        name_label.set_halign(self.Gtk.Align.END)
        name_label.set_valign(self.Gtk.Align.END)
        name_label.set_margin_bottom(6)
        name_label.set_margin_end(6)
        name_label.set_no_show_all(True) 
        
        child.name_label = name_label
        overlay.add_overlay(name_label)
        overlay.show_all()
        child.add(overlay)
        
        if getattr(self, 'current_selected_child', None) == child:
            name_label.show()

        return False

    def get_selected_path(self):
        selected = self.flowbox.get_selected_children()
        return getattr(selected[0], 'rel_path', None) if selected else None

    def trigger_action(self, action_type: str):
        path = self.get_selected_path()
        match action_type:
            case 'fast':
                if path: self.apply_wallpaper(path, regen=False)
            case 'fav':
                if path: self.toggle_favorite(path)
            case 'toggle':
                self.show_only_favorites = not self.show_only_favorites
                self.flowbox.invalidate_filter()
                self.GLib.idle_add(self._update_visibility_and_selection)

    def on_child_activated(self, flowbox, child):
        self.apply_wallpaper(getattr(child, 'rel_path', None), regen=True)

    def on_flowbox_button_press(self, widget, event):
        """Intercepts raw pointer events for Right-Click and Middle-Click interactions."""
        if event.type == self.Gdk.EventType.BUTTON_PRESS:
            # Button 2 = Middle Click, Button 3 = Right Click
            if event.button in (2, 3):
                # Retrieve the specific child occupying the physical mouse layout coordinates
                child = self.flowbox.get_child_at_pos(int(event.x), int(event.y))
                if child:
                    rel_path = getattr(child, 'rel_path', None)
                    if rel_path:
                        # Select the child visibly to guarantee immediate UX feedback
                        self.flowbox.select_child(child)
                        
                        if event.button == 3: # Right Click -> Fast Apply
                            self.apply_wallpaper(rel_path, regen=False)
                        elif event.button == 2: # Middle Click -> Toggle Favorite
                            self.toggle_favorite(rel_path)
                            
                        return True # Consume event to prevent GTK defaults
        return False

    def on_key_press(self, widget, event):
        keyval = event.keyval
        state = event.state

        is_alt = (state & self.Gdk.ModifierType.MOD1_MASK) != 0
        is_ctrl = (state & self.Gdk.ModifierType.CONTROL_MASK) != 0
        
        # --- AGGRESSIVE APP KILL SWITCH ---
        # No matter the focus state, Escape instantly destroys the window
        if keyval == self.Gdk.KEY_Escape:
            self.window.close()
            return True

        if keyval == self.Gdk.KEY_q and not is_alt and not is_ctrl and not self.search_entry.is_focus():
            self.window.close()
            return True
        if keyval in (self.Gdk.KEY_c, self.Gdk.KEY_C) and is_ctrl:
            self.window.close()
            return True

        # Stop keystrokes from leaking into shortcuts if the search bar is active
        if self.search_entry.is_focus():
            return False

        if keyval == self.Gdk.KEY_slash and not is_alt and not is_ctrl:
            self.search_entry.grab_focus()
            return True
            
        if keyval in (self.Gdk.KEY_f, self.Gdk.KEY_F) and is_ctrl:
            self.search_entry.grab_focus()
            return True

        if keyval in (self.Gdk.KEY_t, self.Gdk.KEY_T) and is_ctrl:
            self.show_only_favorites = not self.show_only_favorites
            self.flowbox.invalidate_filter()
            self.GLib.idle_add(self._update_visibility_and_selection)
            return True

        rel_path = self.get_selected_path()

        match keyval:
            case self.Gdk.KEY_Return | self.Gdk.KEY_KP_Enter:
                if rel_path: self.apply_wallpaper(rel_path, regen=True)
                return True
            case self.Gdk.KEY_h if is_alt:
                if rel_path: self.apply_wallpaper(rel_path, regen=False)
                return True
            case self.Gdk.KEY_u if is_alt:
                if rel_path: self.toggle_favorite(rel_path)
                return True
            case self.Gdk.KEY_t if is_alt:
                self.show_only_favorites = not self.show_only_favorites
                self.flowbox.invalidate_filter()
                self.GLib.idle_add(self._update_visibility_and_selection)
                return True
            case self.Gdk.KEY_y if is_alt:
                print("Rebuilding cache dynamically...")
                CacheManager.precache_all()
                self.refresh_ui()
                return True

        return False

    def toggle_favorite(self, rel_path: str):
        if rel_path in self.favorites: self.favorites.remove(rel_path)
        else: self.favorites.add(rel_path)
        
        self._save_favorites()
        
        # In-memory O(1) redrawing logic instantly renders/hides the heart icon
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
        THEME_DIR.mkdir(parents=True, exist_ok=True)
        with open(track_file, 'w') as f: f.write(f"{basename}\n")
        with open(FAV_STATE_FILE, 'w') as f: f.write(f"{basename}\n")

    def apply_wallpaper(self, rel_path: str, regen: bool):
        if not rel_path: return
        full_path = WALLPAPER_DIR / rel_path
        
        if not full_path.exists():
            print(f"Error: Path {full_path} does not exist.")
            return

        print(f"Applying: {full_path} (Regen: {regen})")
        state = self.parse_state_conf()
        theme_mode = state.get('THEME_MODE', 'dark')
        self.update_trackers(rel_path, theme_mode)

        awww_cmd = ["uwsm-app", "--", "awww", "img"]
        
        def add_opt(key, flag):
            val = state.get(key, 'disable')
            if val and val != 'disable': awww_cmd.extend([flag, val])

        add_opt('AWWW_TRANS_TYPE', '--transition-type')
        add_opt('AWWW_TRANS_DURATION', '--transition-duration')
        add_opt('AWWW_TRANS_FPS', '--transition-fps')
        add_opt('AWWW_TRANS_BEZIER', '--transition-bezier')
        add_opt('AWWW_TRANS_ANGLE', '--transition-angle')
        add_opt('AWWW_TRANS_POS', '--transition-pos')
        awww_cmd.append(str(full_path))

        def _exec_backend():
            try:
                subprocess.run(awww_cmd, check=True)
                if regen: subprocess.run([str(THEME_CTL), "refresh"], check=True)
            except subprocess.CalledProcessError as e:
                print(f"Backend execution failed: {e}")

        threading.Thread(target=_exec_backend, daemon=True).start()

    def run(self):
        # We strip sys.argv because custom argparse flags will crash GTKApplication
        return self.app.run([sys.argv[0]])


# ==============================================================================
# ENTRY POINT & CLI PARSING
# ==============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dusky Theme GTK3 Wallpaper Selector")
    parser.add_argument('--precache', action='store_true', help="Run silently in the background to generate caches and sweep orphans, then exit.")
    
    args, unknown = parser.parse_known_args()
    
    if args.precache:
        CacheManager.precache_all()
        sys.exit(0)
    else:
        selector = WallpaperApp()
        exit_status = selector.run()
        sys.exit(exit_status)
