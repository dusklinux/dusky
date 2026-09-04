#!/usr/bin/env python3
"""
Dusky Downloader — Modern GTK3 Frontend
Platform: Arch Linux (Hyprland / Wayland / X11) | Python 3.14+ | GTK 3
Engine: Powered by dusky_yt_dlp core architecture.
Uses native GTK system theme colors and classes (adw-gtk3-dark / Material / System).
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango

# Set application identity early so Wayland app_id and window managers identify the window
GLib.set_prgname("dusky-downloader")
GLib.set_application_name("Dusky Downloader")

# Ensure parent directory is importable
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import dusky_yt_dlp as engine
from dusky_yt_dlp import TargetFormat

# Adaptive CSS styling — dynamically blends with the system theme (adw-gtk3 / user GTK theme)
ADAPTIVE_THEME_CSS = b"""
.card {
    background-color: alpha(@theme_fg_color, 0.04);
    border: 1px solid alpha(@borders, 0.6);
    border-radius: 8px;
    padding: 12px 16px;
    margin: 4px 10px;
}

.card-title {
    font-size: 13px;
    font-weight: 600;
    color: @theme_fg_color;
}

.active-title {
    font-size: 13px;
    font-weight: 600;
    color: @theme_selected_bg_color;
}

.progress-pct {
    font-size: 12px;
    font-weight: 600;
    color: @theme_fg_color;
}

.progress-sub {
    font-size: 11px;
    color: alpha(@theme_fg_color, 0.65);
}

entry {
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 12px;
    border: 1px solid alpha(@borders, 0.7);
}

entry:focus {
    border-color: @theme_selected_bg_color;
}

button.btn-skip {
    background-color: alpha(@warning_color, 0.15);
    color: @warning_color;
    border: 1px solid alpha(@warning_color, 0.35);
}

button.btn-skip:hover {
    background-color: alpha(@warning_color, 0.25);
}

button.btn-skip:disabled {
    background-color: transparent;
    color: alpha(@theme_fg_color, 0.3);
    border-color: alpha(@borders, 0.3);
}

progressbar progress {
    background-color: @theme_selected_bg_color;
    border-radius: 4px;
    min-height: 7px;
}

progressbar trough {
    background-color: alpha(@theme_fg_color, 0.07);
    border: 1px solid alpha(@borders, 0.4);
    border-radius: 4px;
    min-height: 7px;
}

treeview {
    border-radius: 6px;
    font-size: 12px;
}

.stat-pill {
    background-color: alpha(@theme_fg_color, 0.05);
    border: 1px solid alpha(@borders, 0.5);
    border-radius: 6px;
    padding: 4px 10px;
}

.stat-pill label {
    font-size: 11px;
    color: alpha(@theme_fg_color, 0.85);
}

.count-badge {
    background-color: alpha(@theme_selected_bg_color, 0.15);
    color: @theme_selected_bg_color;
    border-radius: 10px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 600;
}

.status-bar {
    font-size: 11px;
    color: alpha(@theme_fg_color, 0.7);
    padding: 4px 12px 6px 12px;
}

.footer-btn {
    padding: 3px 8px;
    font-size: 11px;
}
"""


def format_bytes(size_bytes: float | int | None) -> str:
    if size_bytes is None or size_bytes <= 0:
        return "--"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"


def format_speed(bps: float | None) -> str:
    if bps is None or bps <= 0:
        return "-- KB/s"
    return f"{format_bytes(bps)}/s"


def format_eta(secs: int | None) -> str:
    if secs is None or secs < 0:
        return "--:--"
    hours, secs = divmod(secs, 3600)
    mins, secs = divmod(secs, 60)
    return f"{hours:02d}:{mins:02d}:{secs:02d}" if hours else f"{mins:02d}:{secs:02d}"


def rgba_to_hex(rgba: Gdk.RGBA | None, fallback: str) -> str:
    if rgba is None:
        return fallback
    r = max(0, min(255, int(rgba.red * 255)))
    g = max(0, min(255, int(rgba.green * 255)))
    b = max(0, min(255, int(rgba.blue * 255)))
    return f"#{r:02x}{g:02x}{b:02x}"


def get_display_format(mode: TargetFormat, quality_cap: int | None) -> str:
    short_names = {
        TargetFormat.VIDEO_BEST: "Best Video",
        TargetFormat.VIDEO: "MP4 (H.264)",
        TargetFormat.VIDEO_AV1: "AV1",
        TargetFormat.VIDEO_VP9: "VP9",
        TargetFormat.VIDEO_MKV: "MKV",
        TargetFormat.AUDIO_BEST: "Audio (Best)",
        TargetFormat.AUDIO_OPUS: "Opus",
        TargetFormat.AUDIO_MP3: "MP3 320k",
        TargetFormat.AUDIO_FLAC: "FLAC",
        TargetFormat.AUDIO_M4A: "M4A",
        TargetFormat.AUDIO_WAV: "WAV",
    }
    base = short_names.get(mode, mode.value)
    if mode.is_video and quality_cap:
        return f"{base} ({quality_cap}p)"
    return base


def make_icon_btn(
    icon_name: str,
    label_text: str | None = None,
    tooltip: str | None = None,
    css_class: str | None = None,
) -> Gtk.Button:
    btn = Gtk.Button()
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    img = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.BUTTON)
    box.pack_start(img, False, False, 0)
    if label_text:
        lbl = Gtk.Label(label=label_text)
        box.pack_start(lbl, False, False, 0)
    btn.add(box)
    if tooltip:
        btn.set_tooltip_text(tooltip)
    if css_class:
        btn.get_style_context().add_class(css_class)
    return btn


def make_stat_pill(icon_name: str, initial_text: str) -> tuple[Gtk.Box, Gtk.Label]:
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    box.get_style_context().add_class("stat-pill")
    img = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU)
    lbl = Gtk.Label(label=initial_text)
    box.pack_start(img, False, False, 0)
    box.pack_start(lbl, False, False, 0)
    return box, lbl


class DownloadItem:
    def __init__(self, title: str, url: str, mode: TargetFormat, quality_cap: int | None):
        self.title = title
        self.url = url
        self.mode = mode
        self.quality_cap = quality_cap
        self.status = "Queued"
        self.saved_file = "--"
        self.size_mb = 0.0
        self.error: str | None = None
        self.skip_requested = False


# TreeView Column indices for Queue Table
COL_CHECK = 0
COL_INDEX = 1
COL_TITLE = 2
COL_FORMAT = 3
COL_STATUS = 4
COL_SIZE = 5
COL_URL = 6
COL_ITEM = 7

QUALITY_OPTIONS = [
    ("best", "Best Available"),
    ("2160", "2160p (4K UHD)"),
    ("1440", "1440p (QHD)"),
    ("1080", "1080p (Full HD)"),
    ("720", "720p (HD)"),
    ("480", "480p (SD)"),
    ("360", "360p"),
]


class PlaylistSelectDialog(Gtk.Dialog):
    """Interactive modal dialog allowing users to inspect, filter, check, and uncheck playlist items."""

    def __init__(
        self,
        parent: Gtk.Window,
        title: str,
        items: list[tuple[str, str]],
        initial_mode: TargetFormat = TargetFormat.AUDIO_BEST,
        initial_quality: int | None = None,
    ):
        super().__init__(
            title="Select Playlist Items",
            transient_for=parent,
            modal=True,
            destroy_with_parent=True,
        )
        self.set_default_size(760, 540)
        self.raw_items = items
        self.chosen_mode = initial_mode
        self.chosen_quality = initial_quality

        # Dialog buttons
        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.confirm_btn = self.add_button("Add Selected", Gtk.ResponseType.OK)
        self.confirm_btn.get_style_context().add_class("suggested-action")

        content = self.get_content_area()
        content.set_spacing(10)
        content.set_border_width(14)

        # Header info
        hdr_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        icon = Gtk.Image.new_from_icon_name("playlist-symbolic", Gtk.IconSize.DIALOG)
        if not icon.get_storage_type():
            icon = Gtk.Image.new_from_icon_name("view-list-symbolic", Gtk.IconSize.DIALOG)
        hdr_box.pack_start(icon, False, False, 0)

        title_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        p_title = Gtk.Label(xalign=0)
        p_title.set_markup(f"<span size='large' weight='bold'>{GLib.markup_escape_text(title)}</span>")
        p_title.set_ellipsize(Pango.EllipsizeMode.END)
        title_vbox.pack_start(p_title, False, False, 0)

        self.subtitle_lbl = Gtk.Label(xalign=0)
        title_vbox.pack_start(self.subtitle_lbl, False, False, 0)
        hdr_box.pack_start(title_vbox, True, True, 0)
        content.pack_start(hdr_box, False, False, 0)

        # Action Toolbar (Select All, Deselect All, Invert, First Only, Search Entry)
        tools_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        self.sel_all_btn = make_icon_btn("edit-select-all-symbolic", label_text="Check All", tooltip="Check all visible items")
        self.sel_all_btn.connect("clicked", self._on_check_all)
        tools_box.pack_start(self.sel_all_btn, False, False, 0)

        self.unsel_all_btn = make_icon_btn("edit-clear-symbolic", label_text="Uncheck All", tooltip="Uncheck all visible items")
        self.unsel_all_btn.connect("clicked", self._on_uncheck_all)
        tools_box.pack_start(self.unsel_all_btn, False, False, 0)

        invert_btn = make_icon_btn("object-flip-horizontal-symbolic", label_text="Invert", tooltip="Invert checked state")
        invert_btn.connect("clicked", self._on_invert)
        tools_box.pack_start(invert_btn, False, False, 0)

        first_only_btn = make_icon_btn("go-top-symbolic", label_text="First Only", tooltip="Select only the first track (ideal if link was a single video inside a YouTube Mix)")
        first_only_btn.connect("clicked", self._on_first_only)
        tools_box.pack_start(first_only_btn, False, False, 0)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Filter tracks by title...")
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("search-changed", self._on_search_changed)
        tools_box.pack_start(self.search_entry, True, True, 0)

        content.pack_start(tools_box, False, False, 0)

        # TreeView with Filter Model
        # Columns in base store:
        # 0: bool (checked), 1: int (index), 2: str (title), 3: str (url), 4: bool (visible)
        self.store = Gtk.ListStore(bool, int, str, str, bool)
        for i, (t, u) in enumerate(items, 1):
            self.store.append([True, i, t, u, True])

        self.filter_model = self.store.filter_new()
        self.filter_model.set_visible_column(4)

        self.treeview = Gtk.TreeView(model=self.filter_model)
        self.treeview.get_selection().set_mode(Gtk.SelectionMode.SINGLE)
        self.treeview.connect("row-activated", self._on_row_activated)
        self.treeview.connect("key-press-event", self._on_treeview_key_press)

        # Checkbox column
        check_renderer = Gtk.CellRendererToggle()
        check_renderer.set_property("activatable", True)
        check_renderer.connect("toggled", self._on_item_toggled)
        col_check = Gtk.TreeViewColumn("", check_renderer, active=0)
        col_check.set_min_width(36)
        col_check.set_resizable(False)
        self.treeview.append_column(col_check)

        # # Column
        num_renderer = Gtk.CellRendererText()
        num_renderer.set_alignment(0.5, 0.5)
        num_renderer.set_padding(6, 4)
        col_num = Gtk.TreeViewColumn("#", num_renderer, text=1)
        col_num.set_min_width(45)
        self.treeview.append_column(col_num)

        # Title Column
        title_renderer = Gtk.CellRendererText()
        title_renderer.set_padding(6, 4)
        title_renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
        col_title = Gtk.TreeViewColumn("Title", title_renderer, text=2)
        col_title.set_resizable(True)
        col_title.set_expand(True)
        col_title.set_min_width(320)
        self.treeview.append_column(col_title)

        # URL Column
        url_renderer = Gtk.CellRendererText()
        url_renderer.set_padding(6, 4)
        url_renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
        col_url = Gtk.TreeViewColumn("URL / Target", url_renderer, text=3)
        col_url.set_resizable(True)
        col_url.set_min_width(180)
        self.treeview.append_column(col_url)

        sw = Gtk.ScrolledWindow()
        sw.set_vexpand(True)
        sw.set_min_content_height(280)
        sw.add(self.treeview)
        content.pack_start(sw, True, True, 0)

        # Format & Quality selector row for the playlist
        opt_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        fmt_lbl = Gtk.Label(label="Target Format:", xalign=0)
        opt_box.pack_start(fmt_lbl, False, False, 0)

        self.fmt_combo = Gtk.ComboBoxText()
        for fmt in TargetFormat:
            self.fmt_combo.append(fmt.value, fmt.label)
        self.fmt_combo.set_active_id(self.chosen_mode.value)
        self.fmt_combo.connect("changed", self._on_fmt_changed)
        opt_box.pack_start(self.fmt_combo, False, False, 0)

        q_lbl = Gtk.Label(label="Quality Cap:", xalign=0)
        opt_box.pack_start(q_lbl, False, False, 0)

        self.q_combo = Gtk.ComboBoxText()
        for q_id, q_label in QUALITY_OPTIONS:
            self.q_combo.append(q_id, q_label)
        q_init = "best" if initial_quality is None else str(initial_quality)
        self.q_combo.set_active_id(q_init)
        self.q_combo.set_sensitive(self.chosen_mode.is_video)
        opt_box.pack_start(self.q_combo, False, False, 0)

        content.pack_start(opt_box, False, False, 0)

        self._update_counter()
        self.show_all()

    def _on_fmt_changed(self, combo):
        active_id = combo.get_active_id() or "audio-best"
        self.chosen_mode = TargetFormat(active_id)
        self.q_combo.set_sensitive(self.chosen_mode.is_video)

    def _on_item_toggled(self, cell, path):
        filter_iter = self.filter_model.get_iter(path)
        child_iter = self.filter_model.convert_iter_to_child_iter(filter_iter)
        val = self.store.get_value(child_iter, 0)
        self.store.set_value(child_iter, 0, not val)
        self._update_counter()

    def _on_row_activated(self, treeview, path, column):
        self._on_item_toggled(None, path)

    def _on_treeview_key_press(self, widget, event):
        if event.keyval == Gdk.KEY_space:
            selection = self.treeview.get_selection()
            model, tree_iter = selection.get_selected()
            if tree_iter:
                path = model.get_path(tree_iter)
                self._on_item_toggled(None, path)
                return True
        return False

    def _on_check_all(self, widget):
        txt = self.search_entry.get_text().strip().lower()
        for row in self.store:
            if not txt or row[4]:
                row[0] = True
        self._update_counter()

    def _on_uncheck_all(self, widget):
        txt = self.search_entry.get_text().strip().lower()
        for row in self.store:
            if not txt or row[4]:
                row[0] = False
        self._update_counter()

    def _on_invert(self, widget):
        txt = self.search_entry.get_text().strip().lower()
        for row in self.store:
            if not txt or row[4]:
                row[0] = not row[0]
        self._update_counter()

    def _on_first_only(self, widget):
        for i, row in enumerate(self.store):
            row[0] = (i == 0)
        self._update_counter()

    def _on_search_changed(self, entry):
        txt = entry.get_text().strip().lower()
        for row in self.store:
            if not txt:
                row[4] = True
            else:
                row[4] = txt in row[2].lower() or txt in row[3].lower()
        self.filter_model.refilter()
        has_filter = bool(txt)
        self.sel_all_btn.set_label("Check Filtered" if has_filter else "Check All")
        self.unsel_all_btn.set_label("Uncheck Filtered" if has_filter else "Uncheck All")

    def _update_counter(self):
        total = len(self.store)
        checked = sum(1 for row in self.store if row[0])
        self.subtitle_lbl.set_markup(f"<b>{checked}</b> of {total} items selected")
        self.confirm_btn.set_label(f"Add Selected ({checked} items)")
        self.confirm_btn.set_sensitive(checked > 0)

    def get_selected(self) -> tuple[list[tuple[str, str]], TargetFormat, int | None]:
        chosen = [(row[2], row[3]) for row in self.store if row[0]]
        active_fmt = TargetFormat(self.fmt_combo.get_active_id() or "audio-best")
        q_id = self.q_combo.get_active_id() or "best"
        q_val = None if q_id == "best" else int(q_id)
        return chosen, active_fmt, q_val


class DuskyDownloaderApp(Gtk.Window):
    def __init__(self):
        super().__init__(title="Dusky Downloader")
        self.set_role("dusky-downloader")
        self.set_default_size(920, 700)
        self.set_position(Gtk.WindowPosition.CENTER)

        # Apply Adaptive Theme CSS that leverages GTK system colors
        self._apply_theme_css()

        # Engine State
        self.queue: list[DownloadItem] = []
        self.queue_lock = threading.Lock()
        self.active_item: DownloadItem | None = None
        self.active_proc: subprocess.Popen | None = None
        self.active_pgid: int | None = None
        self.abort_all_flag = False
        self.worker_thread: threading.Thread | None = None

        # Concurrency & Multi-Download State (defaults to 3 concurrent downloads)
        self.max_concurrent = 3
        self.active_downloads: dict[DownloadItem, dict] = {}
        self.active_downloads_lock = threading.Lock()
        self.focused_item: DownloadItem | None = None

        # Probing & Live Extraction State
        self.is_probing = False
        self.probing_cancelled = False
        self.probe_pulse_timer: int | None = None
        self.probe_total: int | None = None

        # Storage Pool
        self.storage_dir = engine.resolve_storage_pool()

        # UI Initialization
        self._build_ui()
        self._setup_drag_and_drop()
        self._setup_keybindings()

        self.connect("destroy", self.on_window_close)
        self._update_status_bar()

    def _apply_theme_css(self):
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(ADAPTIVE_THEME_CSS)
        screen = Gdk.Screen.get_default()
        if screen:
            Gtk.StyleContext.add_provider_for_screen(
                screen, css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    def _is_ram_pool(self) -> bool:
        path_str = str(self.storage_dir)
        return any(path_str.startswith(p) for p in ("/mnt/zram", "/dev/shm", "/tmp/dusky"))

    def _build_ui(self):
        # Header Bar
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        title_box.set_valign(Gtk.Align.CENTER)
        t_lbl = Gtk.Label(label="Dusky Downloader")
        t_lbl.get_style_context().add_class("title")
        st_lbl = Gtk.Label(label="Universal Media Downloader")
        st_lbl.get_style_context().add_class("subtitle")
        title_box.pack_start(t_lbl, False, False, 0)
        title_box.pack_start(st_lbl, False, False, 0)
        header.set_custom_title(title_box)

        self.set_titlebar(header)

        # Main Layout Container
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add(main_box)

        # ==========================================
        # 1. TOP CARD: INPUT & CONFIGURATION
        # ==========================================
        input_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        input_card.get_style_context().add_class("card")

        card_hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        card_icon = Gtk.Image.new_from_icon_name("list-add-symbolic", Gtk.IconSize.BUTTON)
        card_lbl = Gtk.Label(label="Add Download Target", xalign=0)
        card_lbl.get_style_context().add_class("card-title")
        card_hdr.pack_start(card_icon, False, False, 0)
        card_hdr.pack_start(card_lbl, False, False, 0)
        input_card.pack_start(card_hdr, False, False, 0)

        # Target entry row
        entry_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.url_entry = Gtk.Entry()
        self.url_entry.set_placeholder_text("Enter video URL, playlist link, or batch file path...")
        self.url_entry.set_hexpand(True)
        self.url_entry.connect("activate", self.on_add_clicked)

        paste_btn = make_icon_btn("edit-paste-symbolic", label_text="Paste", tooltip="Paste clipboard into target field")
        paste_btn.connect("clicked", self.on_paste_clicked)

        browse_btn = make_icon_btn("document-open-symbolic", label_text="Browse...", tooltip="Choose batch text file from disk")
        browse_btn.connect("clicked", self.on_browse_file_clicked)

        entry_row.pack_start(self.url_entry, True, True, 0)
        entry_row.pack_start(paste_btn, False, False, 0)
        entry_row.pack_start(browse_btn, False, False, 0)
        input_card.pack_start(entry_row, False, False, 0)

        # Controls Row (Format, Quality, Add)
        ctrl_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        # Format picker — comprehensive video and audio codecs
        ctrl_row.pack_start(Gtk.Label(label="Format:"), False, False, 0)
        self.format_combo = Gtk.ComboBoxText()
        # Video options
        self.format_combo.append("video-best", "Video: Best Quality (Native AV1/VP9/Highest)")
        self.format_combo.append("video", "Video: MP4 (H.264/AAC Universal)")
        self.format_combo.append("video-av1", "Video: AV1 (Modern Next-Gen Codec)")
        self.format_combo.append("video-vp9", "Video: VP9 / WebM (Google Open Media)")
        self.format_combo.append("video-mkv", "Video: MKV (Lossless Multi-Track)")
        # Audio options
        self.format_combo.append("audio-best", "Audio: Best (Native Stream, No Transcode)")
        self.format_combo.append("audio-opus", "Audio: Opus (High Quality OGG)")
        self.format_combo.append("audio-mp3", "Audio: MP3 (320 kbps Universal)")
        self.format_combo.append("audio-flac", "Audio: FLAC (Lossless)")
        self.format_combo.append("audio-m4a", "Audio: M4A / AAC (Apple & Mobile)")
        self.format_combo.append("audio-wav", "Audio: WAV (Lossless PCM)")

        self.format_combo.set_active(5)  # default to audio-best
        self.format_combo.connect("changed", self.on_format_changed)
        ctrl_row.pack_start(self.format_combo, False, False, 0)

        # Quality picker
        ctrl_row.pack_start(Gtk.Label(label="Quality:"), False, False, 0)
        self.quality_combo = Gtk.ComboBoxText()
        self.quality_combo.append("best", "Best Available")
        self.quality_combo.append("2160", "2160p (4K UHD)")
        self.quality_combo.append("1440", "1440p (QHD)")
        self.quality_combo.append("1080", "1080p (Full HD)")
        self.quality_combo.append("720", "720p (HD)")
        self.quality_combo.append("480", "480p (SD)")
        self.quality_combo.append("360", "360p")
        self.quality_combo.set_active(0)
        self.quality_combo.set_sensitive(False)  # default audio-best is not video
        ctrl_row.pack_start(self.quality_combo, False, False, 0)

        # Add button — uses system suggested-action class (matches user accent color)
        self.add_btn = make_icon_btn(
            "list-add-symbolic",
            label_text="Add to Queue",
            tooltip="Add link or batch to the active download queue",
            css_class="suggested-action",
        )
        self.add_btn.connect("clicked", self.on_add_clicked)
        ctrl_row.pack_end(self.add_btn, False, False, 0)

        input_card.pack_start(ctrl_row, False, False, 0)

        # Probing Banner Revealer (displays live feedback while querying large playlists/mixes)
        self.probe_revealer = Gtk.Revealer()
        self.probe_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.probe_revealer.set_transition_duration(250)

        probe_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        probe_box.get_style_context().add_class("stat-pill")
        probe_box.set_margin_top(8)

        self.probe_spinner = Gtk.Spinner()
        probe_box.pack_start(self.probe_spinner, False, False, 0)

        self.probe_msg_lbl = Gtk.Label(label="Analyzing media target...", xalign=0)
        self.probe_msg_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        probe_box.pack_start(self.probe_msg_lbl, True, True, 0)

        self.probe_cancel_btn = make_icon_btn(
            "process-stop-symbolic",
            label_text="Cancel",
            tooltip="Cancel probing this target",
            css_class="btn-skip",
        )
        self.probe_cancel_btn.connect("clicked", self._on_cancel_probe_clicked)
        probe_box.pack_end(self.probe_cancel_btn, False, False, 0)

        self.probe_revealer.add(probe_box)
        input_card.pack_start(self.probe_revealer, False, False, 0)

        main_box.pack_start(input_card, False, False, 0)

        # ==========================================
        # 2. MIDDLE CARD: ACTIVE DOWNLOAD DASHBOARD
        # ==========================================
        active_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        active_card.get_style_context().add_class("card")

        top_active_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        active_icon = Gtk.Image.new_from_icon_name("browser-download-symbolic", Gtk.IconSize.BUTTON)
        active_lbl = Gtk.Label(label="Active Download:", xalign=0)
        active_lbl.get_style_context().add_class("card-title")
        top_active_box.pack_start(active_icon, False, False, 0)
        top_active_box.pack_start(active_lbl, False, False, 0)

        self.active_title_lbl = Gtk.Label(label="Queue is idle", xalign=0)
        self.active_title_lbl.get_style_context().add_class("active-title")
        self.active_title_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self.active_title_lbl.set_hexpand(True)
        top_active_box.pack_start(self.active_title_lbl, True, True, 4)

        # Skip Current Button
        self.skip_current_btn = make_icon_btn(
            "media-skip-forward-symbolic",
            label_text="Skip",
            tooltip="Immediately skip the currently downloading item and move to the next",
            css_class="btn-skip",
        )
        self.skip_current_btn.connect("clicked", self.on_skip_current_clicked)
        self.skip_current_btn.set_sensitive(False)

        # Cancel / Abort All Button — uses system destructive-action class
        self.cancel_all_btn = make_icon_btn(
            "process-stop-symbolic",
            label_text="Abort All",
            tooltip="Stop all ongoing downloads and clear the queue",
            css_class="destructive-action",
        )
        self.cancel_all_btn.connect("clicked", self.on_abort_all_clicked)
        self.cancel_all_btn.set_sensitive(False)

        top_active_box.pack_end(self.cancel_all_btn, False, False, 0)
        top_active_box.pack_end(self.skip_current_btn, False, False, 0)
        active_card.pack_start(top_active_box, False, False, 0)

        # Progress info line above progress bar
        prog_info = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.prog_pct_lbl = Gtk.Label(label="Idle", xalign=0)
        self.prog_pct_lbl.get_style_context().add_class("progress-pct")
        prog_info.pack_start(self.prog_pct_lbl, False, False, 0)

        self.prog_sub_lbl = Gtk.Label(label="Ready", xalign=1)
        self.prog_sub_lbl.get_style_context().add_class("progress-sub")
        prog_info.pack_end(self.prog_sub_lbl, False, False, 0)
        active_card.pack_start(prog_info, False, False, 0)

        # Modern slim progress bar (styled with system @theme_selected_bg_color)
        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_fraction(0.0)
        self.progress_bar.set_show_text(False)
        active_card.pack_start(self.progress_bar, False, False, 0)

        # Telemetry Badges (4 clean pills with system icons)
        telemetry_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        pill_sp, self.badge_speed = make_stat_pill("network-transmit-receive-symbolic", "Speed: -- KB/s")
        pill_sz, self.badge_downloaded = make_stat_pill("drive-harddisk-symbolic", "Size: -- / --")
        pill_et, self.badge_eta = make_stat_pill("preferences-system-time-symbolic", "ETA: --:--")
        pill_st, self.badge_stage = make_stat_pill("emblem-synchronizing-symbolic", "Stage: Idle")

        telemetry_box.pack_start(pill_sp, False, False, 0)
        telemetry_box.pack_start(pill_sz, False, False, 0)
        telemetry_box.pack_start(pill_et, False, False, 0)
        telemetry_box.pack_start(pill_st, False, False, 0)

        active_card.pack_start(telemetry_box, False, False, 0)
        main_box.pack_start(active_card, False, False, 0)

        # ==========================================
        # 3. BOTTOM CARD: QUEUE & HISTORY TABLE
        # ==========================================
        queue_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        queue_card.get_style_context().add_class("card")
        queue_card.set_vexpand(True)

        queue_hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        queue_icon = Gtk.Image.new_from_icon_name("view-list-symbolic", Gtk.IconSize.BUTTON)
        queue_title = Gtk.Label(label="Download Queue", xalign=0)
        queue_title.get_style_context().add_class("card-title")
        self.queue_badge = Gtk.Label(label="0 items")
        self.queue_badge.get_style_context().add_class("count-badge")

        queue_hdr.pack_start(queue_icon, False, False, 0)
        queue_hdr.pack_start(queue_title, False, False, 0)
        queue_hdr.pack_start(self.queue_badge, False, False, 0)

        # Action buttons for the queue
        check_all_btn = make_icon_btn(
            "edit-select-all-symbolic",
            label_text="Check All",
            tooltip="Check / re-enable all items in the queue",
        )
        check_all_btn.connect("clicked", self.on_check_all_clicked)

        uncheck_all_btn = make_icon_btn(
            "edit-clear-symbolic",
            label_text="Uncheck All",
            tooltip="Uncheck / skip all pending items in the queue",
        )
        uncheck_all_btn.connect("clicked", self.on_uncheck_all_clicked)

        skip_selected_btn = make_icon_btn(
            "media-skip-forward-symbolic",
            label_text="Skip Selected",
            tooltip="Mark selected queued download as skipped",
        )
        skip_selected_btn.connect("clicked", self.on_skip_selected_clicked)

        remove_btn = make_icon_btn(
            "list-remove-symbolic",
            label_text="Remove",
            tooltip="Remove selected item from queue (or press Delete)",
        )
        remove_btn.connect("clicked", self.on_remove_selected_clicked)

        clear_done_btn = make_icon_btn(
            "edit-clear-symbolic",
            label_text="Clear Finished",
            tooltip="Remove all completed or skipped items from list",
        )
        clear_done_btn.connect("clicked", self.on_clear_finished_clicked)

        queue_hdr.pack_end(clear_done_btn, False, False, 0)
        queue_hdr.pack_end(remove_btn, False, False, 0)
        queue_hdr.pack_end(skip_selected_btn, False, False, 0)
        queue_hdr.pack_end(uncheck_all_btn, False, False, 0)
        queue_hdr.pack_end(check_all_btn, False, False, 0)

        queue_card.pack_start(queue_hdr, False, False, 0)

        # TreeView List
        # Columns: Check (bool), # (int), Title (str), Format (str), Status (str), Size (str), URL (str), DownloadItem (object)
        self.liststore = Gtk.ListStore(bool, int, str, str, str, str, str, object)
        self.treeview = Gtk.TreeView(model=self.liststore)
        self.treeview.get_selection().set_mode(Gtk.SelectionMode.SINGLE)
        self.treeview.set_has_tooltip(True)
        self.treeview.connect("query-tooltip", self.on_treeview_query_tooltip)
        self.treeview.connect("row-activated", self.on_row_activated)
        self.treeview.connect("button-press-event", self.on_treeview_button_press)
        self.treeview.connect("cursor-changed", self.on_treeview_cursor_changed)

        # Checkbox column for enable / disable / skip
        check_renderer = Gtk.CellRendererToggle()
        check_renderer.set_property("activatable", True)
        check_renderer.connect("toggled", self.on_queue_item_toggled)
        check_col = Gtk.TreeViewColumn("", check_renderer, active=COL_CHECK)
        check_col.set_min_width(36)
        check_col.set_resizable(False)
        self.treeview.append_column(check_col)

        cols_def = [
            ("#", COL_INDEX, 45, True, False),
            ("Title", COL_TITLE, 280, False, True),
            ("Format", COL_FORMAT, 110, False, True),
            ("Status", COL_STATUS, 110, False, True),
            ("Size", COL_SIZE, 85, False, True),
            ("URL / Target", COL_URL, 220, False, True),
        ]

        for name, idx, width, is_center, is_resizable in cols_def:
            renderer = Gtk.CellRendererText()
            renderer.set_padding(8, 5)
            renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
            if is_center:
                renderer.set_alignment(0.5, 0.5)

            if idx == COL_STATUS:
                col = Gtk.TreeViewColumn(name, renderer, text=idx)
                col.set_cell_data_func(renderer, self._render_status_cell)
            else:
                col = Gtk.TreeViewColumn(name, renderer, text=idx)

            col.set_resizable(is_resizable)
            col.set_min_width(width)
            self.treeview.append_column(col)

        self.scrolled_window = Gtk.ScrolledWindow()
        self.scrolled_window.set_vexpand(True)
        self.scrolled_window.set_min_content_height(180)
        self.scrolled_window.add(self.treeview)
        queue_card.pack_start(self.scrolled_window, True, True, 0)

        main_box.pack_start(queue_card, True, True, 0)

        # ==========================================
        # 4. FOOTER: INTERACTIVE STATUS BAR
        # ==========================================
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        footer.get_style_context().add_class("status-bar")

        # Interactive Storage Button on Left
        self.storage_btn = make_icon_btn(
            "folder-open-symbolic",
            label_text="Storage",
            tooltip="Click to open storage folder in file manager",
            css_class="footer-btn",
        )
        self.storage_btn.connect("clicked", self.on_open_storage_clicked)
        footer.pack_start(self.storage_btn, False, False, 0)

        # Storage Path Label
        self.storage_path_lbl = Gtk.Label(label="", xalign=0)
        self.storage_path_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        footer.pack_start(self.storage_path_lbl, False, False, 0)

        # Queue Summary Label in Center
        self.status_lbl = Gtk.Label(label="", xalign=0)
        footer.pack_start(self.status_lbl, True, True, 0)

        # Interactive RAM / ZRAM Engine Button on Right
        self.engine_btn = make_icon_btn(
            "system-run-symbolic",
            label_text="RAM / ZRAM Engine Active",
            tooltip="Click to inspect Storage Pool & Acceleration Engine details",
            css_class="footer-btn",
        )
        self.engine_btn.connect("clicked", self.on_engine_details_clicked)
        footer.pack_end(self.engine_btn, False, False, 0)

        main_box.pack_start(footer, False, False, 2)

    def _setup_drag_and_drop(self):
        self.drag_dest_set(Gtk.DestDefaults.ALL, [], Gdk.DragAction.COPY)
        self.drag_dest_add_uri_targets()
        self.drag_dest_add_text_targets()
        self.connect("drag-data-received", self.on_drag_data_received)

    def _setup_keybindings(self):
        self.connect("key-press-event", self.on_window_key_press)

    def _render_status_cell(self, col, cell, model, it, data):
        status = model.get_value(it, COL_STATUS)
        sc = self.get_style_context()
        _, sel_col = sc.lookup_color("theme_selected_bg_color")
        _, err_col = sc.lookup_color("error_color")
        _, warn_col = sc.lookup_color("warning_color")
        _, succ_col = sc.lookup_color("success_color")
        _, fg_col = sc.lookup_color("theme_fg_color")

        colors = {
            "Queued": rgba_to_hex(fg_col, "#94a3b8"),
            "Downloading": rgba_to_hex(sel_col, "#60a5fa"),
            "Success": rgba_to_hex(succ_col, "#34d399"),
            "Skipped": rgba_to_hex(warn_col, "#fbbf24"),
            "Failed": rgba_to_hex(err_col, "#f87171"),
        }
        color = colors.get(status, "#cbd5e1")
        cell.set_property("markup", f"<span color='{color}' font_weight='600'>{status}</span>")

    def _renumber_rows(self):
        for i, row in enumerate(self.liststore, start=1):
            row[COL_INDEX] = i

    def on_queue_item_toggled(self, widget, path):
        it = self.liststore.get_iter(path)
        current = self.liststore.get_value(it, COL_CHECK)
        new_val = not current
        self.liststore.set_value(it, COL_CHECK, new_val)
        item: DownloadItem = self.liststore.get_value(it, COL_ITEM)
        if not item:
            return
        if not new_val:
            if item.status in ("Queued", "Ready"):
                item.status = "Skipped"
                item.skip_requested = True
                self.liststore.set_value(it, COL_STATUS, "Skipped")
        else:
            if item.status == "Skipped":
                item.status = "Queued"
                item.skip_requested = False
                self.liststore.set_value(it, COL_STATUS, "Queued")
                self._ensure_worker_running()
        self._update_status_bar()

    def on_check_all_clicked(self, widget):
        with self.queue_lock:
            for row in self.liststore:
                row[COL_CHECK] = True
                item: DownloadItem = row[COL_ITEM]
                if item and item.status == "Skipped":
                    item.status = "Queued"
                    item.skip_requested = False
                    row[COL_STATUS] = "Queued"
        self._ensure_worker_running()
        self._update_status_bar()

    def on_uncheck_all_clicked(self, widget):
        with self.queue_lock:
            for row in self.liststore:
                row[COL_CHECK] = False
                item: DownloadItem = row[COL_ITEM]
                if item:
                    item.skip_requested = True
                    if item.status in ("Queued", "Ready"):
                        item.status = "Skipped"
                        row[COL_STATUS] = "Skipped"
                    elif item.status == "Downloading":
                        self.on_skip_current_clicked(None)
        self._update_status_bar()

    def on_window_key_press(self, widget, event):
        # If Delete or Backspace pressed on treeview
        if event.keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete, Gdk.KEY_BackSpace):
            focused = self.get_focus()
            if focused == self.treeview or (isinstance(focused, Gtk.Widget) and self.treeview.is_ancestor(focused)):
                self.on_remove_selected_clicked(None)
                return True
        # Space key on treeview -> toggle check / skip state
        if event.keyval == Gdk.KEY_space:
            focused = self.get_focus()
            if focused == self.treeview or (isinstance(focused, Gtk.Widget) and self.treeview.is_ancestor(focused)):
                selection = self.treeview.get_selection()
                model, tree_iter = selection.get_selected()
                if tree_iter:
                    path = model.get_path(tree_iter)
                    self.on_queue_item_toggled(None, path)
                    return True
        # Ctrl+O -> open file chooser
        if (event.state & Gdk.ModifierType.CONTROL_MASK) and event.keyval in (Gdk.KEY_o, Gdk.KEY_O):
            self.on_browse_file_clicked(None)
            return True
        return False

    def on_drag_data_received(self, widget, drag_context, x, y, data, info, time):
        text = data.get_text()
        uris = data.get_uris()
        drag_context.finish(True, False, time)

        target = None
        if uris and len(uris) > 0:
            target = uris[0]
        elif text:
            target = text.strip()

        if target:
            if target.startswith("file://"):
                parsed = urlparse(target)
                target = unquote(parsed.path)
            self.url_entry.set_text(target)
            self.on_add_clicked(None)

    def on_treeview_query_tooltip(self, treeview, x, y, keyboard_mode, tooltip):
        path_info = treeview.get_path_at_pos(x, y)
        if not path_info:
            return False
        path, col, cellx, celly = path_info
        model = treeview.get_model()
        tree_iter = model.get_iter(path)
        item: DownloadItem = model.get_value(tree_iter, COL_ITEM)
        if not item:
            return False

        lines = [
            f"<b>Title:</b> {GLib.markup_escape_text(item.title)}",
            f"<b>URL:</b> {GLib.markup_escape_text(item.url)}",
            f"<b>Format:</b> {item.mode.label}" + (f" ({item.quality_cap}p)" if item.quality_cap else ""),
            f"<b>Status:</b> {item.status}",
        ]
        if item.status == "Success" and item.saved_file != "--":
            lines.append(f"<b>Saved File:</b> {GLib.markup_escape_text(item.saved_file)}")
        if item.error:
            lines.append(f"<b>Details:</b> {GLib.markup_escape_text(item.error)}")

        tooltip.set_markup("\n".join(lines))
        return True

    def on_row_activated(self, treeview, path, column):
        model = treeview.get_model()
        tree_iter = model.get_iter(path)
        item: DownloadItem = model.get_value(tree_iter, COL_ITEM)
        if not item:
            return

        if item.status == "Success":
            dest_path = self.storage_dir / item.saved_file if item.saved_file != "--" else None
            if dest_path and dest_path.exists():
                subprocess.Popen(["xdg-open", str(dest_path)])
            else:
                subprocess.Popen(["xdg-open", str(self.storage_dir)])
        elif item.status in ("Queued", "Skipped"):
            self.on_queue_item_toggled(None, path)
        elif item.status == "Failed":
            dialog = Gtk.MessageDialog(
                transient_for=self,
                modal=True,
                destroy_with_parent=True,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text=f"Download Failed: {item.title}",
            )
            dialog.format_secondary_text(f"URL: {item.url}\n\nError details:\n{item.error or 'Unknown error'}")
            dialog.run()
            dialog.destroy()

    def on_treeview_button_press(self, treeview, event):
        if event.type == Gdk.EventType.BUTTON_PRESS and event.button == 3:  # Right-click
            path_info = treeview.get_path_at_pos(int(event.x), int(event.y))
            if path_info:
                path, col, cellx, celly = path_info
                treeview.get_selection().select_path(path)
                self._show_context_menu(event, path)
                return True
        return False

    def _show_context_menu(self, event, path):
        model = self.treeview.get_model()
        tree_iter = model.get_iter(path)
        item: DownloadItem = model.get_value(tree_iter, COL_ITEM)
        if not item:
            return

        menu = Gtk.Menu()

        # Check / Uncheck Toggle
        if item.status in ("Queued", "Skipped"):
            is_checked = model.get_value(tree_iter, COL_CHECK)
            toggle_label = "Uncheck (Skip Item)" if is_checked else "Check (Enable Item)"
            toggle_item = Gtk.MenuItem(label=toggle_label)
            toggle_item.connect("activate", lambda _: self.on_queue_item_toggled(None, path))
            menu.append(toggle_item)
            menu.append(Gtk.SeparatorMenuItem())

        if item.status == "Success":
            dest_path = self.storage_dir / item.saved_file if item.saved_file != "--" else None

            open_item = Gtk.MenuItem(label="Open File")
            def _open_file(_):
                if dest_path and dest_path.exists():
                    subprocess.Popen(["xdg-open", str(dest_path)])
            open_item.connect("activate", _open_file)
            menu.append(open_item)

            folder_item = Gtk.MenuItem(label="Open Containing Folder")
            def _open_folder(_):
                subprocess.Popen(["xdg-open", str(self.storage_dir)])
            folder_item.connect("activate", _open_folder)
            menu.append(folder_item)

            menu.append(Gtk.SeparatorMenuItem())

        # Copy URL
        copy_item = Gtk.MenuItem(label="Copy URL")
        def _copy_url(_):
            clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            clip.set_text(item.url, -1)
        copy_item.connect("activate", _copy_url)
        menu.append(copy_item)

        if item.status == "Queued":
            skip_item = Gtk.MenuItem(label="Skip in Queue")
            skip_item.connect("activate", lambda _: self.on_skip_selected_clicked(None))
            menu.append(skip_item)
        elif item.status == "Downloading":
            skip_item = Gtk.MenuItem(label="Skip Current Download")
            skip_item.connect("activate", lambda _: self.on_skip_current_clicked(None))
            menu.append(skip_item)

        if item.status in ("Queued", "Success", "Skipped", "Failed"):
            remove_item = Gtk.MenuItem(label="Remove from Queue")
            remove_item.connect("activate", lambda _: self.on_remove_selected_clicked(None))
            menu.append(remove_item)

        menu.append(Gtk.SeparatorMenuItem())

        # Batch Check All / Uncheck All
        chk_all = Gtk.MenuItem(label="Check All Items")
        chk_all.connect("activate", self.on_check_all_clicked)
        menu.append(chk_all)

        unchk_all = Gtk.MenuItem(label="Uncheck All Items")
        unchk_all.connect("activate", self.on_uncheck_all_clicked)
        menu.append(unchk_all)

        menu.show_all()
        menu.popup_at_pointer(event)

    def on_format_changed(self, combo):
        active_id = combo.get_active_id() or "audio-best"
        try:
            mode = TargetFormat(active_id)
            self.quality_combo.set_sensitive(mode.is_video)
        except ValueError:
            self.quality_combo.set_sensitive(False)

    def on_paste_clicked(self, widget):
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        text = clipboard.wait_for_text()
        if text:
            self.url_entry.set_text(text.strip())

    def on_browse_file_clicked(self, widget):
        dialog = Gtk.FileChooserDialog(
            title="Choose Batch File or Links",
            parent=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_buttons(
            "Cancel", Gtk.ResponseType.CANCEL,
            "Open", Gtk.ResponseType.OK
        )
        open_btn = dialog.get_widget_for_response(Gtk.ResponseType.OK)
        if open_btn:
            open_btn.get_style_context().add_class("suggested-action")
        filter_text = Gtk.FileFilter()
        filter_text.set_name("Text / Batch files (*.txt)")
        filter_text.add_pattern("*.txt")
        filter_text.add_mime_type("text/plain")
        dialog.add_filter(filter_text)

        filter_all = Gtk.FileFilter()
        filter_all.set_name("All files (*.*)")
        filter_all.add_pattern("*")
        dialog.add_filter(filter_all)

        if dialog.run() == Gtk.ResponseType.OK:
            filename = dialog.get_filename()
            if filename:
                self.url_entry.set_text(filename)
        dialog.destroy()

    def on_open_storage_clicked(self, widget):
        try:
            subprocess.Popen(["xdg-open", str(self.storage_dir)])
        except Exception as e:
            dialog = Gtk.MessageDialog(
                transient_for=self,
                modal=True,
                destroy_with_parent=True,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text=f"Failed to open storage folder: {e}",
            )
            dialog.run()
            dialog.destroy()

    def on_engine_details_clicked(self, widget):
        """Interactive dialog detailing the storage engine, free space, and allowing folder selection."""
        dialog = Gtk.Dialog(
            title="Storage Pool & Acceleration Engine",
            parent=self,
            modal=True,
            destroy_with_parent=True,
        )
        dialog.add_button("Change Folder...", Gtk.ResponseType.APPLY)
        dialog.add_button("Open in File Manager", Gtk.ResponseType.YES)
        close_btn = dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        if close_btn:
            close_btn.get_style_context().add_class("suggested-action")

        box = dialog.get_content_area()
        box.set_spacing(10)
        box.set_border_width(14)

        is_ram = self._is_ram_pool()
        engine_type = "High-Speed RAM / ZRAM VFS Mount" if is_ram else "Standard Local Disk Storage"
        perf_note = "Zero NVMe wear, instantaneous write bandwidth." if is_ram else "Persistent local disk storage."

        try:
            total_b, used_b, free_b = shutil.disk_usage(self.storage_dir)
            space_str = f"{format_bytes(free_b)} free of {format_bytes(total_b)}"
        except Exception:
            space_str = "Available"

        with self.queue_lock:
            q_tot = len(self.queue)
            q_done = sum(1 for x in self.queue if x.status == "Success")

        info_markup = (
            f"<b>Active Directory:</b>\n<tt>{GLib.markup_escape_text(str(self.storage_dir))}</tt>\n\n"
            f"<b>Engine Architecture:</b>\n{engine_type}\n<i>{perf_note}</i>\n\n"
            f"<b>Storage Capacity:</b>\n{space_str}\n\n"
            f"<b>Session Status:</b>\n{q_tot} items in queue ({q_done} completed)"
        )
        lbl = Gtk.Label(xalign=0)
        lbl.set_markup(info_markup)
        box.pack_start(lbl, True, True, 0)
        dialog.show_all()

        response = dialog.run()
        if response == Gtk.ResponseType.YES:
            dialog.destroy()
            self.on_open_storage_clicked(None)
            return

        if response == Gtk.ResponseType.APPLY:
            dialog.destroy()
            self._choose_new_storage_folder()
            return

        dialog.destroy()

    def _choose_new_storage_folder(self):
        fcd = Gtk.FileChooserDialog(
            title="Select Custom Download Folder",
            parent=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        fcd.add_buttons(
            "Cancel", Gtk.ResponseType.CANCEL,
            "Select Folder", Gtk.ResponseType.OK,
        )
        sel_btn = fcd.get_widget_for_response(Gtk.ResponseType.OK)
        if sel_btn:
            sel_btn.get_style_context().add_class("suggested-action")
        fcd.set_current_folder(str(self.storage_dir))
        if fcd.run() == Gtk.ResponseType.OK:
            chosen = fcd.get_filename()
            if chosen:
                p = Path(chosen).resolve()
                p.mkdir(parents=True, exist_ok=True)
                self.storage_dir = p
                self._update_status_bar()
        fcd.destroy()

    def _on_cancel_probe_clicked(self, widget):
        self.probing_cancelled = True
        self.probe_msg_lbl.set_text("Cancelling extraction probe...")
        self.probe_cancel_btn.set_sensitive(False)

    def on_add_clicked(self, widget):
        raw_target = self.url_entry.get_text().strip()
        if not raw_target:
            return

        fmt_id = self.format_combo.get_active_id() or "audio-best"
        mode = TargetFormat(fmt_id)

        q_id = self.quality_combo.get_active_id() or "best"
        quality_cap = None if q_id == "best" else int(q_id)

        # Probing state initialization
        self.is_probing = True
        self.probing_cancelled = False
        self.probe_total = None
        self.url_entry.set_sensitive(False)
        self.add_btn.set_sensitive(False)
        self.cancel_all_btn.set_sensitive(True)

        self.probe_revealer.set_reveal_child(True)
        self.probe_spinner.start()
        self.probe_cancel_btn.set_sensitive(True)
        self.probe_msg_lbl.set_text("Connecting to media target and inspecting playlist...")

        # If download worker is idle, show visual cues in Active Download dashboard
        if self.active_item is None:
            self.active_title_lbl.set_text("Analyzing target collection / playlist...")
            self.prog_pct_lbl.set_text("Probing...")
            self.prog_sub_lbl.set_text("Connecting...")
            self.badge_stage.set_text("Stage: Probing")
            self.progress_bar.set_fraction(0.0)

            def _pulse():
                if self.is_probing and self.probe_total is None:
                    self.progress_bar.pulse()
                    return True
                return False

            self.probe_pulse_timer = GLib.timeout_add(80, _pulse)

        def _apply_probe_progress(msg: str, current: int | None, total: int | None):
            if not self.is_probing:
                return
            self.probe_msg_lbl.set_text(msg)
            self.status_lbl.set_text(msg)
            if self.active_item is None:
                self.active_title_lbl.set_text(msg)
                if total and current:
                    self.probe_total = total
                    fraction = min(1.0, max(0.0, current / total))
                    self.progress_bar.set_fraction(fraction)
                    self.prog_pct_lbl.set_text(f"{int(fraction * 100)}% ({current}/{total})")
                    self.prog_sub_lbl.set_text(f"{current} of {total} items discovered")
                    self.badge_downloaded.set_text(f"Items: {current} / {total}")
                    self.badge_stage.set_text(f"Stage: Item {current}/{total}")
                elif current:
                    self.progress_bar.pulse()
                    self.prog_pct_lbl.set_text(f"{current} items")
                    self.prog_sub_lbl.set_text(f"{current} items discovered so far")
                    self.badge_downloaded.set_text(f"Items: {current}")

        def _on_probe_progress(msg: str, current: int | None, total: int | None):
            GLib.idle_add(_apply_probe_progress, msg, current, total)

        def _restore_ui():
            self.is_probing = False
            if self.probe_pulse_timer:
                GLib.source_remove(self.probe_pulse_timer)
                self.probe_pulse_timer = None
            self.probe_spinner.stop()
            self.probe_revealer.set_reveal_child(False)
            self.url_entry.set_sensitive(True)
            self.url_entry.set_text("")
            self.url_entry.set_placeholder_text("Enter video URL, playlist link, or batch file path...")
            self.add_btn.set_sensitive(True)
            if self.active_item is None:
                self.active_title_lbl.set_text("Queue is idle")
                self.prog_pct_lbl.set_text("Idle")
                self.prog_sub_lbl.set_text("Ready")
                self.progress_bar.set_fraction(0.0)
                self.badge_speed.set_text("Speed: -- KB/s")
                self.badge_downloaded.set_text("Size: -- / --")
                self.badge_eta.set_text("ETA: --:--")
                self.badge_stage.set_text("Stage: Idle")
                self.cancel_all_btn.set_sensitive(False)
                self._update_status_bar()

        # Resolve paths or URL lists in background to avoid any GUI freeze
        def _resolve_and_queue():
            try:
                target_path = Path(raw_target.strip("'\"")).expanduser()
                if target_path.is_file():
                    try:
                        urls = engine.parse_batch_file(target_path)
                    except Exception as e:
                        GLib.idle_add(self._show_error, f"Cannot parse batch file: {e}")
                        GLib.idle_add(_restore_ui)
                        return

                    if not urls:
                        GLib.idle_add(self._show_error, "Batch file is empty.")
                        GLib.idle_add(_restore_ui)
                        return

                    items = [(engine.label_from_url(u) or u, u) for u in urls]
                    GLib.idle_add(_restore_ui)
                    GLib.idle_add(self._prompt_playlist_selection, f"Batch File: {target_path.name}", items, mode, quality_cap)
                    return

                url_candidates = engine.split_url_list(raw_target)
                if not url_candidates:
                    GLib.idle_add(self._show_error, "No valid URLs or batch entries found.")
                    GLib.idle_add(_restore_ui)
                    return

                if len(url_candidates) == 1:
                    u = url_candidates[0]
                    try:
                        found, is_coll, label, _ = engine.probe_media_target(
                            u,
                            progress_cb=_on_probe_progress,
                            cancel_check=lambda: self.probing_cancelled,
                        )
                        if self.probing_cancelled:
                            GLib.idle_add(_restore_ui)
                            return
                        if is_coll and len(found) > 1:
                            GLib.idle_add(_restore_ui)
                            GLib.idle_add(self._prompt_playlist_selection, label, found, mode, quality_cap)
                            return
                        elif found:
                            title = found[0][0]
                            real_url = found[0][1]
                            item = DownloadItem(title=title, url=real_url, mode=mode, quality_cap=quality_cap)
                            GLib.idle_add(_restore_ui)
                            GLib.idle_add(self._append_to_queue, [item])
                            return
                    except KeyboardInterrupt:
                        GLib.idle_add(_restore_ui)
                        return
                    except Exception:
                        if self.probing_cancelled:
                            GLib.idle_add(_restore_ui)
                            return
                        pass

                    label = engine.label_from_url(u) or u
                    item = DownloadItem(title=label, url=u, mode=mode, quality_cap=quality_cap)
                    GLib.idle_add(_restore_ui)
                    GLib.idle_add(self._append_to_queue, [item])
                else:
                    items = [(engine.label_from_url(u) or u, u) for u in url_candidates]
                    GLib.idle_add(_restore_ui)
                    GLib.idle_add(self._prompt_playlist_selection, f"Batch List ({len(items)} items)", items, mode, quality_cap)

            except BaseException as e:
                if not isinstance(e, (KeyboardInterrupt, SystemExit)) and not self.probing_cancelled:
                    GLib.idle_add(self._show_error, f"Error processing target: {e}")
                GLib.idle_add(_restore_ui)

        threading.Thread(target=_resolve_and_queue, daemon=True).start()

    def _prompt_playlist_selection(self, title: str, items: list[tuple[str, str]], mode: TargetFormat, quality_cap: int | None):
        dialog = PlaylistSelectDialog(self, title, items, mode, quality_cap)
        resp = dialog.run()
        if resp == Gtk.ResponseType.OK:
            selected_items, chosen_mode, chosen_q = dialog.get_selected()
            new_queue_items = [
                DownloadItem(title=t, url=u, mode=chosen_mode, quality_cap=chosen_q)
                for t, u in selected_items
            ]
            self._append_to_queue(new_queue_items)
        dialog.destroy()

    def _show_error(self, message: str):
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            destroy_with_parent=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK,
            text=message,
        )
        dialog.run()
        dialog.destroy()

    def _append_to_queue(self, items: list[DownloadItem]):
        with self.queue_lock:
            start_idx = len(self.queue) + 1
            for i, item in enumerate(items):
                idx = start_idx + i
                self.queue.append(item)
                fmt_display = get_display_format(item.mode, item.quality_cap)
                is_checked = (item.status != "Skipped")
                self.liststore.append([is_checked, idx, item.title, fmt_display, item.status, "--", item.url, item])

        self._update_status_bar()
        self._ensure_worker_running()

    def _update_status_bar(self):
        with self.queue_lock:
            total = len(self.queue)
            done = sum(1 for x in self.queue if x.status == "Success")
            skip = sum(1 for x in self.queue if x.status == "Skipped")
            fail = sum(1 for x in self.queue if x.status == "Failed")
            with self.active_downloads_lock:
                active = len(self.active_downloads)

        try:
            total_b, used_b, free_b = shutil.disk_usage(self.storage_dir)
            free_str = f"{format_bytes(free_b)} free"
        except Exception:
            free_str = "Ready"

        # Update Storage Path Label in footer
        if hasattr(self, "storage_path_lbl"):
            self.storage_path_lbl.set_text(f"{self.storage_dir} ({free_str})")
        if hasattr(self, "storage_btn"):
            self.storage_btn.set_tooltip_text(f"Storage: {self.storage_dir} ({free_str})\nClick to open in file manager.")

        # Update Engine Button
        if hasattr(self, "engine_btn"):
            box = self.engine_btn.get_child()
            if isinstance(box, Gtk.Box):
                children = box.get_children()
                if len(children) > 1 and isinstance(children[1], Gtk.Label):
                    children[1].set_text("RAM / ZRAM Engine Active" if self._is_ram_pool() else "Disk Storage Active")

        summary = []
        if total > 0:
            summary.append(f"{total} total")
        if active > 0:
            summary.append(f"{active} active ({self.max_concurrent} slots)")
        if done > 0:
            summary.append(f"{done} completed")
        if skip > 0:
            summary.append(f"{skip} skipped")
        if fail > 0:
            summary.append(f"{fail} failed")

        if summary:
            self.status_lbl.set_text(" | " + " • ".join(summary))
        else:
            self.status_lbl.set_text(f" | Queue idle ({self.max_concurrent} concurrent slots)")

        if hasattr(self, "queue_badge"):
            self.queue_badge.set_text(f"{total} items")

    def _ensure_worker_running(self):
        self.abort_all_flag = False
        self._trigger_workers()

    def _trigger_workers(self):
        if self.abort_all_flag:
            return

        to_start: list[DownloadItem] = []
        with self.queue_lock:
            with self.active_downloads_lock:
                running_cnt = len(self.active_downloads)
                slots_avail = max(0, self.max_concurrent - running_cnt)
                if slots_avail > 0:
                    for it in self.queue:
                        if it.status == "Queued" and not it.skip_requested:
                            it.status = "Downloading"
                            self.active_downloads[it] = {
                                "proc": None,
                                "pgid": None,
                                "progress": engine.MediaProgress(),
                                "started_ns": time.monotonic_ns(),
                            }
                            to_start.append(it)
                            if len(to_start) >= slots_avail:
                                break

        for item in to_start:
            GLib.idle_add(self._on_item_started, item)
            threading.Thread(target=self._download_worker_thread, args=(item,), daemon=True).start()

        with self.active_downloads_lock:
            active_cnt = len(self.active_downloads)
        with self.queue_lock:
            queued_cnt = sum(1 for x in self.queue if x.status == "Queued")

        if active_cnt == 0 and queued_cnt == 0:
            GLib.idle_add(self._on_queue_finished)

    def _download_worker_thread(self, item: DownloadItem):
        try:
            self._download_item(item)
        finally:
            with self.active_downloads_lock:
                self.active_downloads.pop(item, None)
            GLib.idle_add(self._on_item_finished, item)
            self._trigger_workers()

    def _find_row_for_item(self, item: DownloadItem):
        for row in self.liststore:
            if row[COL_ITEM] is item:
                return row
        return None

    def on_treeview_cursor_changed(self, treeview):
        selection = treeview.get_selection()
        model, tree_iter = selection.get_selected()
        if tree_iter:
            item = model.get_value(tree_iter, COL_ITEM)
            with self.active_downloads_lock:
                if item and item in self.active_downloads:
                    self.focused_item = item
                    d = self.active_downloads[item]
                    self._update_progress_ui(item, d["progress"])

    def _get_displayed_item(self) -> DownloadItem | None:
        with self.active_downloads_lock:
            if self.focused_item and self.focused_item in self.active_downloads:
                return self.focused_item
            if self.active_downloads:
                return next(iter(self.active_downloads.keys()))
        return None

    def _download_item(self, item: DownloadItem):
        if self.abort_all_flag or item.skip_requested:
            item.status = "Skipped" if item.skip_requested else "Failed"
            item.error = "skipped by user" if item.skip_requested else "aborted by user"
            return

        # 1. Background probe to resolve title and metadata only if generic URL title
        if not item.title or item.title.startswith("http://") or item.title.startswith("https://"):
            try:
                found, is_coll, label, _ = engine.probe_media_target(item.url)
                if is_coll and label:
                    item.title = label
                elif found and found[0][0]:
                    item.title = found[0][0]
            except Exception:
                pass
            GLib.idle_add(self._update_item_title, item)

        if self.abort_all_flag or item.skip_requested:
            item.status = "Skipped" if item.skip_requested else "Failed"
            item.error = "skipped by user" if item.skip_requested else "aborted by user"
            return

        runner = engine.YtdlpRunner(item.mode, self.storage_dir, item.url, item.quality_cap)
        parser = engine.YtdlpProgressParser()
        progress_state = engine.MediaProgress()
        started_ns = time.monotonic_ns()

        try:
            before_names: set[str] = {p.name for p in self.storage_dir.iterdir() if p.is_file()}
        except OSError:
            before_names = set()

        proc: subprocess.Popen | None = None
        pgid: int | None = None
        try:
            proc, pgid = runner.spawn()
            with self.active_downloads_lock:
                self.active_downloads[item] = {
                    "proc": proc,
                    "pgid": pgid,
                    "progress": progress_state,
                    "started_ns": started_ns,
                }
        except Exception as err:
            item.status = "Failed"
            item.error = str(err)
            return

        # Drain streams
        def drain(stream, is_stdout: bool):
            buf = bytearray()
            read1 = getattr(stream, "read", None)
            try:
                while True:
                    chunk = read1(1) if callable(read1) else None
                    if not chunk:
                        break
                    byte = chunk[0] if isinstance(chunk, (bytes, bytearray)) else ord(chunk)
                    if byte in (10, 13):
                        if buf:
                            line = bytes(buf).decode("utf-8", errors="replace")
                            del buf[:]
                            parser.parse_line(line, progress_state)
                            GLib.idle_add(self._update_progress_ui, item, progress_state)
                        continue
                    buf.append(byte)
            except Exception:
                pass
            finally:
                if buf:
                    line = bytes(buf).decode("utf-8", errors="replace")
                    parser.parse_line(line, progress_state)
                    GLib.idle_add(self._update_progress_ui, item, progress_state)

        t_out = threading.Thread(target=drain, args=(proc.stdout, True), daemon=True)
        t_err = threading.Thread(target=drain, args=(proc.stderr, False), daemon=True)
        t_out.start()
        t_err.start()

        # Monitor loop
        while proc.poll() is None:
            if self.abort_all_flag or item.skip_requested:
                if pgid and pgid > 1:
                    engine._kill_pgids([pgid], signal.SIGTERM)
                try:
                    proc.wait(timeout=3)
                except Exception:
                    if pgid and pgid > 1:
                        engine._kill_pgids([pgid], signal.SIGKILL)
                break
            time.sleep(0.1)

        t_out.join(timeout=1.0)
        t_err.join(timeout=1.0)

        with engine._ACTIVE_PG_LOCK:
            if pgid:
                engine.ACTIVE_PROCESS_GROUPS.discard(pgid)

        if item.skip_requested:
            item.status = "Skipped"
            item.error = "skipped by user"
            return

        if self.abort_all_flag:
            item.status = "Failed"
            item.error = "aborted by user"
            return

        if proc.returncode != 0:
            item.status = "Failed"
            item.error = f"yt-dlp exited with error code {proc.returncode}"
            return

        if progress_state.already_archived:
            item.status = "Skipped"
            item.saved_file = "--"
            item.error = "already in archive"
            return

        # Locate produced file
        dest_file = progress_state.destination_file or "--"
        actual_path = None
        if dest_file != "--":
            actual_path = self.storage_dir / dest_file
            if not actual_path.exists():
                actual_path = engine._resolve_output_file(self.storage_dir, started_ns, dest_file, before_names)
        else:
            actual_path = engine._resolve_output_file(self.storage_dir, started_ns, None, before_names)

        if actual_path and actual_path.exists():
            item.status = "Success"
            item.saved_file = actual_path.name
            try:
                item.size_mb = actual_path.stat().st_size / (1024 * 1024)
            except OSError:
                item.size_mb = 0.0
        else:
            item.status = "Failed"
            item.error = "no output file produced"

    # ==========================================
    # GLIB IDLE CALLBACKS (MAIN THREAD UI UPDATES)
    # ==========================================
    def _on_item_started(self, item: DownloadItem):
        self.skip_current_btn.set_sensitive(True)
        self.cancel_all_btn.set_sensitive(True)

        row = self._find_row_for_item(item)
        if row:
            row[COL_STATUS] = "Downloading"

        with self.active_downloads_lock:
            active_cnt = len(self.active_downloads)

        displayed = self._get_displayed_item()
        if displayed is item or active_cnt <= 1:
            self.active_title_lbl.set_text(f"[{active_cnt} Active] {item.title}" if active_cnt > 1 else item.title)
            self.prog_pct_lbl.set_text("Starting...")
            self.prog_sub_lbl.set_text("Initializing streams...")
            self.progress_bar.set_fraction(0.0)

        self._update_status_bar()

    def _update_item_title(self, item: DownloadItem):
        displayed = self._get_displayed_item()
        if displayed is item:
            with self.active_downloads_lock:
                active_cnt = len(self.active_downloads)
            prefix = f"[{active_cnt} Active] " if active_cnt > 1 else ""
            self.active_title_lbl.set_text(f"{prefix}{item.title}")
        row = self._find_row_for_item(item)
        if row:
            row[COL_TITLE] = item.title

    def _update_progress_ui(self, item: DownloadItem, progress_state: engine.MediaProgress):
        # Update row in treeview with live percentage
        row = self._find_row_for_item(item)
        if row and row[COL_STATUS] != "Skipped":
            row[COL_STATUS] = f"Downloading ({progress_state.percentage:.0f}%)"

        displayed = self._get_displayed_item()
        with self.active_downloads_lock:
            active_cnt = len(self.active_downloads)
            total_speed = sum((d["progress"].speed_bps or 0.0) for d in self.active_downloads.values())

        if displayed is item:
            fraction = max(0.0, min(1.0, progress_state.percentage / 100.0))
            self.progress_bar.set_fraction(fraction)

            # Left side above progress bar
            self.prog_pct_lbl.set_text(f"Downloading • {progress_state.percentage:.1f}%")

            # Right side above progress bar: Transferred size only (NO SPEED, NO ETA! Clean & non-redundant!)
            dl_str = format_bytes(progress_state.downloaded_bytes)
            tot_str = format_bytes(progress_state.total_bytes)
            if active_cnt > 1:
                self.prog_sub_lbl.set_text(f"Transferred: {dl_str} / {tot_str} • {active_cnt} active")
            else:
                self.prog_sub_lbl.set_text(f"Transferred: {dl_str} / {tot_str}")

            prefix = f"[{active_cnt} Active] " if active_cnt > 1 else ""
            self.active_title_lbl.set_text(f"{prefix}{item.title}")

        # Telemetry pills below progress bar
        speed_tag = f" ({active_cnt} parallel)" if active_cnt > 1 else ""
        self.badge_speed.set_text(f"Speed: {format_speed(total_speed)}{speed_tag}")

        if displayed:
            with self.active_downloads_lock:
                disp_info = self.active_downloads.get(displayed)
                disp_prog = disp_info["progress"] if disp_info else progress_state
            dl_str = format_bytes(disp_prog.downloaded_bytes)
            tot_str = format_bytes(disp_prog.total_bytes)
            self.badge_downloaded.set_text(f"Size: {dl_str} / {tot_str}")
            self.badge_eta.set_text(f"ETA: {format_eta(disp_prog.eta_secs)}")
            stage_name = disp_prog.stage.value.capitalize()
            self.badge_stage.set_text(f"Stage: {stage_name}")

    def _on_item_finished(self, item: DownloadItem):
        row = self._find_row_for_item(item)
        if row:
            row[COL_STATUS] = item.status
            size_str = f"{item.size_mb:.2f} MB" if item.status == "Success" else "--"
            row[COL_SIZE] = size_str
            if item.status == "Skipped":
                row[COL_CHECK] = False
        if self.focused_item is item:
            self.focused_item = None
        self._update_status_bar()

    def _on_queue_finished(self):
        with self.active_downloads_lock:
            if len(self.active_downloads) > 0:
                return
        self.active_title_lbl.set_text("Queue is idle")
        self.prog_pct_lbl.set_text("Idle")
        self.prog_sub_lbl.set_text("Ready")
        self.progress_bar.set_fraction(0.0)

        self.skip_current_btn.set_sensitive(False)
        self.cancel_all_btn.set_sensitive(False)

        self.badge_speed.set_text("Speed: -- KB/s")
        self.badge_downloaded.set_text("Size: -- / --")
        self.badge_eta.set_text("ETA: --:--")
        self.badge_stage.set_text("Stage: Idle")
        self._update_status_bar()

    # ==========================================
    # USER ACTIONS: SKIP & CANCEL
    # ==========================================
    def on_skip_current_clicked(self, widget):
        with self.active_downloads_lock:
            target_item = self.focused_item
            if target_item is None or target_item not in self.active_downloads:
                target_item = next(iter(self.active_downloads.keys()), None)
            if target_item and target_item in self.active_downloads:
                target_item.skip_requested = True
                pgid = self.active_downloads[target_item].get("pgid")
                if pgid and pgid > 1:
                    engine._kill_pgids([pgid], signal.SIGTERM)

    def on_abort_all_clicked(self, widget):
        if self.is_probing:
            self.probing_cancelled = True
        self.abort_all_flag = True
        with self.active_downloads_lock:
            for it, d in list(self.active_downloads.items()):
                it.skip_requested = True
                pgid = d.get("pgid")
                if pgid and pgid > 1:
                    engine._kill_pgids([pgid], signal.SIGTERM)
        with self.queue_lock:
            for it in self.queue:
                if it.status == "Queued":
                    it.status = "Skipped"
                    it.error = "aborted by user"
                    row = self._find_row_for_item(it)
                    if row:
                        row[COL_STATUS] = "Skipped"
                        row[COL_CHECK] = False
        self._update_status_bar()

    def on_skip_selected_clicked(self, widget):
        selection = self.treeview.get_selection()
        model, tree_iter = selection.get_selected()
        if not tree_iter:
            return
        item: DownloadItem = model.get_value(tree_iter, COL_ITEM)
        if not item:
            return

        with self.active_downloads_lock:
            is_active = item in self.active_downloads
            active_info = self.active_downloads.get(item)

        if is_active and active_info:
            item.skip_requested = True
            pgid = active_info.get("pgid")
            if pgid and pgid > 1:
                engine._kill_pgids([pgid], signal.SIGTERM)
        else:
            with self.queue_lock:
                if item.status == "Queued":
                    item.status = "Skipped"
                    item.error = "skipped by user in queue"
                    model.set_value(tree_iter, COL_STATUS, "Skipped")
                    model.set_value(tree_iter, COL_CHECK, False)
        self._update_status_bar()

    def on_remove_selected_clicked(self, widget):
        selection = self.treeview.get_selection()
        model, tree_iter = selection.get_selected()
        if not tree_iter:
            return
        item: DownloadItem = model.get_value(tree_iter, COL_ITEM)
        if not item:
            return

        with self.active_downloads_lock:
            active_info = self.active_downloads.get(item)
            if active_info:
                item.skip_requested = True
                pgid = active_info.get("pgid")
                if pgid and pgid > 1:
                    engine._kill_pgids([pgid], signal.SIGTERM)

        with self.queue_lock:
            if item in self.queue:
                self.queue.remove(item)
            model.remove(tree_iter)
            self._renumber_rows()

        self._update_status_bar()

    def on_clear_finished_clicked(self, widget):
        with self.queue_lock:
            self.queue = [it for it in self.queue if it.status not in ("Success", "Skipped", "Failed")]

        # Rebuild liststore cleanly
        self.liststore.clear()
        with self.queue_lock:
            for idx, item in enumerate(self.queue, start=1):
                fmt_display = get_display_format(item.mode, item.quality_cap)
                size_str = f"{item.size_mb:.2f} MB" if item.status == "Success" else "--"
                is_checked = (item.status != "Skipped")
                self.liststore.append([is_checked, idx, item.title, fmt_display, item.status, size_str, item.url, item])

        self._update_status_bar()

    def on_window_close(self, widget):
        self.is_probing = False
        self.probing_cancelled = True
        if self.probe_pulse_timer:
            GLib.source_remove(self.probe_pulse_timer)
            self.probe_pulse_timer = None
        self.abort_all_flag = True
        if self.active_pgid and self.active_pgid > 1:
            engine._kill_pgids([self.active_pgid], signal.SIGTERM)
        if Gtk.main_level() > 0:
            Gtk.main_quit()


def main():
    app = DuskyDownloaderApp()
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        raw_target = " ".join(sys.argv[1:]).strip()
        if raw_target:
            app.url_entry.set_text(raw_target)
            GLib.idle_add(app.on_add_clicked, None)
    app.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
