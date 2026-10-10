#!/usr/bin/env python3
"""GTK application preferences for Hyprland, using installed schema metadata."""
import os
import sys
from pathlib import Path
from typing import Any
lazy import configparser
lazy import shutil
lazy import shlex
lazy import subprocess

_DUSKY_TUI_ROOT = Path(__file__).resolve().parents[1] / "dusky_tui"
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))

from python.frontend.core_types import ConfigItem
from python.engines.gsettings import GSettingsEngine, INTEGER_LIMITS, SCALAR_KINDS

ENGINE_TYPE = "gsettings"
TARGET_FILE = str(Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "dconf/user")
APP_TITLE = "Dusky GSettings Manager"
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"
ENABLE_USER_PRESETS = True
USER_PRESETS_TAB = "Presets"
HIDE_MISSING_ITEMS = True

# Metadata is required for truthful defaults and typed controls. The engine also
# supports CLI-only consumers; this schema deliberately requires PyGObject.
import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio

SOURCE = Gio.SettingsSchemaSource.get_default()
INSTALLED_SCHEMAS = set(SOURCE.list_schemas(True)[0]) if SOURCE else set()
TABS: list[str] = []
SCHEMA: dict[int, list[ConfigItem]] = {}
TAB_NOTICES: dict[int, dict[str, str]] = {}


class GSettingsItem(ConfigItem):
    __slots__ = ()

    def deserialize(self, raw: Any) -> Any:
        # Gio returns literal strings, unlike file engines parsing quoted text.
        if self.type_ in {"string", "picker", "cycle"} and isinstance(raw, str):
            return raw
        return super().deserialize(raw)

    def validate_preset_value(self, value: Any) -> Any:
        if value is None:
            raise ValueError("GSettings values cannot be null")
        _, variant = GSettingsEngine._variant_for_key(self.scope, self.key, str(value))
        return self.deserialize(GSettingsEngine._variant_text(variant))


def _data_roots() -> list[Path]:
    return [Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share"),
            *(Path(p) for p in (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":") if p)]


def _discover_themes(kind: str) -> list[str]:
    subdirectory = "themes" if kind == "gtk" else "icons"
    roots = [root / subdirectory for root in _data_roots()]
    roots.append(Path.home() / (".themes" if kind == "gtk" else ".icons"))
    found = set()
    for root in roots:
        try:
            for entry in root.iterdir():
                if entry.name.startswith(".") or not entry.is_dir():
                    continue
                if kind == "gtk":
                    valid = (entry / "gtk-3.0/gtk.css").is_file() or (entry / "gtk-4.0/gtk.css").is_file()
                elif kind == "cursor":
                    valid = (entry / "cursors").is_dir()
                else:
                    parser = configparser.ConfigParser(interpolation=None)
                    parser.optionxform = str
                    try:
                        parser.read(entry / "index.theme", encoding="utf-8")
                        valid = bool(parser.get("Icon Theme", "Directories", fallback="") or
                                     parser.get("Icon Theme", "ScaledDirectories", fallback=""))
                        valid = valid and not parser.getboolean("Icon Theme", "Hidden", fallback=False)
                    except (configparser.Error, UnicodeError, ValueError):
                        valid = False
                if valid:
                    found.add(entry.name)
        except OSError:
            continue
    return sorted(found, key=lambda name: (not name.lower().startswith("dusky"), name.casefold()))


GTK_THEMES = _discover_themes("gtk")
ICON_THEMES = _discover_themes("icon")
CURSOR_THEMES = _discover_themes("cursor")
TERMINALS = [
    name for name in (
        "xdg-terminal-exec", "kitty", "alacritty", "wezterm", "foot", "ghostty",
        "gnome-terminal", "konsole",
    ) if shutil.which(name)
]


def _add(tab: str, scope: str, keys: str | None, group: str, notice: str = "") -> None:
    if scope not in INSTALLED_SCHEMAS:
        return
    schema = SOURCE.lookup(scope, True)
    settings = Gio.Settings.new_full(schema, None, None)
    items = []
    for name in keys.split() if keys is not None else sorted(schema.list_keys()):
        if not schema.has_key(name):
            continue
        metadata = schema.get_key(name)
        signature = metadata.get_value_type().dup_string()
        kind = "string" if signature == "as" else SCALAR_KINDS.get(signature)
        if kind is None:
            continue  # Runtime geometry and internal history are not controls.
        default_variant = settings.get_default_value(name)
        default = default_variant.print_(True) if signature == "as" else default_variant.unpack()
        range_kind, bounds = metadata.get_range().unpack()
        options = list(bounds) if range_kind == "enum" else []
        if options:
            kind = "cycle"
        discovered = {"gtk-theme": GTK_THEMES, "icon-theme": ICON_THEMES,
                      "cursor-theme": CURSOR_THEMES}.get(name)
        if scope.endswith("default-applications.terminal") and name == "exec":
            discovered = TERMINALS
        if discovered is not None:
            kind = "picker"
            options = list(dict.fromkeys([*discovered, default, settings.get_string(name)]))
        minimum = maximum = None
        if range_kind == "range":
            minimum, maximum = bounds
        elif kind == "int":
            minimum, maximum = INTEGER_LIMITS[signature]
        if scope == "org.gnome.desktop.interface" and name == "cursor-size":
            minimum = 1
        description = metadata.get_description() or metadata.get_summary() or name
        if signature == "as":
            description += "\n\nEnter a GVariant string array, for example `[\"one\", \"two\"]` or `[]`."
        if scope == "org.gnome.desktop.privacy":
            description += (
                "\n\nThis is a preference for services that honor it; "
                "it does not enforce hardware restrictions in Hyprland."
            )
        items.append(GSettingsItem(
            label=metadata.get_summary() or name.replace("-", " ").title(),
            key=name, scope=scope, type_=kind, default=default,
            options=options, min_val=minimum, max_val=maximum,
            step=0.1 if kind == "float" else 1 if kind == "int" else None,
            group=group, read_only=not settings.is_writable(name),
            extended_help=f"**{name}**\n\n{description}\n\nSchema: `{scope}`",
        ))
    if not items:
        return
    if tab not in TABS:
        TABS.append(tab)
        index = len(TABS) - 1
        SCHEMA[index] = []
        TAB_NOTICES[index] = {
            "level": "info",
            "message": notice or "Preferences apply to applications and services that honor these schemas.",
        }
    SCHEMA[TABS.index(tab)].extend(items)


INTERFACE = "org.gnome.desktop.interface"
_add(
    "Appearance",
    INTERFACE,
    "color-scheme accent-color gtk-theme icon-theme cursor-theme cursor-size",
    "Themes & Cursor",
    'GTK and libadwaita appearance preferences. Cursor changes also synchronize Hyprland; '
    'application support varies.',
)
_add("Fonts", INTERFACE, "font-name document-font-name monospace-font-name text-scaling-factor", "Fonts & Scaling")
_add("Fonts", INTERFACE, "font-rendering font-antialiasing font-hinting font-rgba-order", "Rendering")
_add(
    "GTK",
    INTERFACE,
    "enable-animations overlay-scrolling gtk-enable-primary-paste toolkit-accessibility",
    "Application Behavior",
    'GTK application behavior; these settings do not configure Hyprland input, animations, '
    'or Waybar.',
)
_add("GTK", INTERFACE, "cursor-blink cursor-blink-time cursor-blink-timeout", "Text Caret")
_add("GTK", INTERFACE, "gtk-im-module gtk-key-theme menubar-accel", "Input & Menus")
_add(
    "Titlebars",
    "org.gnome.desktop.wm.preferences",
    'button-layout action-double-click-titlebar action-middle-click-titlebar '
    'action-right-click-titlebar titlebar-font titlebar-uses-system-font',
    "Client-side Decorations",
    'GTK client-side titlebars where supported. Hyprland controls compositor borders and '
    'window management.',
)
_add(
    "Apps",
    "org.gnome.desktop.default-applications.terminal",
    "exec exec-arg",
    "Terminal Launcher",
    'Legacy terminal launch preference for applications that consult GSettings; MIME '
    'associations are configured separately. Keep executable and arguments consistent.',
)
_add("Apps", "org.cinnamon.desktop.default-applications.terminal", "exec exec-arg", "Cinnamon Terminal Launcher")
for tab, scope in (("Nemo", "org.nemo.preferences"), ("Nautilus", "org.gnome.nautilus.preferences")):
    _add(
        tab,
        scope,
        'show-hidden-files sort-directories-first default-folder-viewer default-sort-order '
        'default-sort-in-reverse-order show-image-thumbnails thumbnail-limit click-policy '
        'executable-text-activation confirm-trash show-delete-link date-format search-view '
        'always-use-location-entry recursive-search show-create-link show-open-in-terminal '
        'show-full-path-titles close-device-view-on-device-eject',
        "File Management",
    )
    for suffix in ("icon-view", "list-view", "compact-view"):
        _add(
            tab,
            scope.removesuffix("preferences") + suffix,
            "default-zoom-level default-visible-columns default-column-order",
            suffix.replace("-", " ").title(),
        )
for version, scope in (("GTK3", "org.gtk.Settings.FileChooser"), ("GTK4", "org.gtk.gtk4.Settings.FileChooser")):
    _add(
        "Dialogs",
        scope,
        'show-hidden sort-directories-first location-mode startup-mode view-type '
        'sort-column sort-order show-size-column show-type-column type-format date-format '
        'clock-format expand-folders',
        version,
        'Open/Save dialog preferences. Portal-provided dialogs may use a different '
        'implementation.',
    )
_add(
    "Media",
    "org.gnome.desktop.media-handling",
    'automount automount-open autorun-never autorun-x-content-ignore '
    'autorun-x-content-open-folder autorun-x-content-start-app',
    "Removable Media",
    "Media handling requires a file manager or service that implements these preferences.",
)
_add("Media", "org.gnome.desktop.thumbnail-cache", None, "Thumbnail Cache")
_add("Sound", "org.gnome.desktop.sound", None, "Application Sounds",
     "Sound preferences for participating applications and GNOME services; these do not configure PipeWire volume.")
_add(
    "Privacy",
    "org.gnome.desktop.privacy",
    'remember-recent-files recent-files-max-age remember-app-usage remove-old-temp-files '
    'remove-old-trash-files old-files-age',
    "History & Cleanup",
    'Preferences for participating services. Hyprland does not enforce GNOME camera, '
    'microphone, telemetry, or cleanup policies.',
)
_add(
    "Privacy",
    "org.gnome.desktop.privacy",
    'disable-camera disable-microphone disable-sound-output send-software-usage-stats '
    'report-technical-problems',
    "Service Preferences",
)
_add("Proxy", "org.gnome.system.proxy", "mode autoconfig-url use-same-proxy ignore-hosts", "Proxy Mode",
     "Proxy preferences for applications using GSettings; these do not change system routing or all applications.")
for protocol in ("http", "https", "ftp", "socks"):
    _add(
        "Proxy",
        f"org.gnome.system.proxy.{protocol}",
        "host port enabled use-authentication authentication-user authentication-password",
        protocol.upper(),
    )

TABS.append("Presets")
SCHEMA[len(TABS) - 1] = [
    ConfigItem(
        label="Apply Schema Defaults",
        key="preset_factory_reset",
        scope="DEFAULT",
        type_="preset",
        default=None,
        group="Defaults",
        confirm_message="Apply installed schema defaults to all managed settings?",
        preset_payload={"__ALL_DEFAULTS__": True},
        extended_help='Applies the installed defaults to managed keys. This writes explicit values; it '
        'does not erase dconf overrides or change unmanaged keys.',
    ),
]
if INTERFACE in INSTALLED_SCHEMAS:
    SCHEMA[len(TABS) - 1].append(
        ConfigItem(
            label="Sync Hyprland Cursor", key="action_sync_cursor",
            scope="DEFAULT", type_="action",
            default=shlex.join([
                sys.executable, str(Path(__file__).resolve()), "--sync-cursor",
            ]),
            group="Actions", popup_message="Cursor synchronization completed.",
            extended_help=(
                "Reads the active cursor theme and size, updates the running "
                "Hyprland session, and writes ~/.icons/default/index.theme."
            ),
        )
    )
TAB_NOTICES[len(TABS) - 1] = {
    "level": "info",
    "message": "Apply installed defaults, manage saved presets, or synchronize the current cursor.",
}

if __name__ == "__main__":
    if sys.argv[1:] == ["--sync-cursor"]:
        engine = GSettingsEngine()
        success = engine.sync_cursor_wayland()
        if not success:
            print(engine.last_sync_error, file=sys.stderr)
        sys.exit(0 if success else 1)
    router = _DUSKY_TUI_ROOT / "python/main/main.py"
    sys.exit(subprocess.run([sys.executable, str(router), str(Path(__file__).resolve()), *sys.argv[1:]]).returncode)
