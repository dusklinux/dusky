#!/usr/bin/env python3
"""
===============================================================================
DUSKY SCREENTIME: DESKTOP ENTRY RESOLVER
===============================================================================
Resolve Hyprland application classes from XDG desktop entries. User entries
take precedence, including Hidden overrides. Application names stay stable;
window titles are shown separately by the dashboard. Resolved classes are cached.
"""

import os
import shlex
import sys
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from screentime_common import xdg_path

KNOWN_TERMINALS: set[str] = {
    "kitty",
    "alacritty",
    "wezterm",
    "foot",
    "ghostty",
    "konsole",
    "gnome-terminal",
    "urxvt",
    "st",
    "rio",
    "termite",
    "xterm",
}


def _unescape(value: str) -> str:
    escapes = {"s": " ", "n": "\n", "t": "\t", "r": "\r", "\\": "\\"}
    result = []
    index = 0
    while index < len(value):
        if value[index] == "\\" and index + 1 < len(value) and value[index + 1] in escapes:
            index += 1
            result.append(escapes[value[index]])
        else:
            result.append(value[index])
        index += 1
    return "".join(result)


@dataclass(slots=True, frozen=True)
class AppInfo:
    name: str
    category: str
    icon: str
    window_class: str


class DesktopResolver:
    """
    High-performance caching resolver for XDG application desktop entries.
    """

    def __init__(self) -> None:
        # Lookup tables mapped by lowercase key to AppInfo
        self._by_wmclass: dict[str, AppInfo] = {}
        self._by_stem: dict[str, AppInfo] = {}
        self._by_alias: dict[str, AppInfo] = {}
        self._by_name: dict[str, AppInfo] = {}
        self._by_exec: dict[str, AppInfo] = {}

        # Cache for previously resolved window_classes during runtime
        self._resolved_cache: OrderedDict[str, AppInfo] = OrderedDict()
        self.reload()

    def reload(self) -> None:
        """
        Scan all XDG application directories and build lookup indexes.
        """
        self._by_wmclass.clear()
        self._by_stem.clear()
        self._by_alias.clear()
        self._by_name.clear()
        self._by_exec.clear()
        self._resolved_cache.clear()

        search_dirs = [xdg_path("XDG_DATA_HOME", ".local/share") / "applications"]
        for directory in (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":"):
            if directory.startswith("/"):
                search_dirs.append(Path(directory) / "applications")
        search_dirs.extend([
            xdg_path("XDG_DATA_HOME", ".local/share") / "flatpak/exports/share/applications",
            Path("/var/lib/flatpak/exports/share/applications"),
        ])
        seen_ids: set[str] = set()
        for directory in dict.fromkeys(search_dirs):
            for filepath in sorted(directory.rglob("*.desktop")):
                desktop_id = str(filepath.relative_to(directory)).replace("/", "-")
                if desktop_id not in seen_ids:
                    seen_ids.add(desktop_id)
                    self._parse_file(filepath, desktop_id.removesuffix(".desktop"))

    def _parse_file(self, filepath: Path, desktop_stem: str | None = None) -> None:
        name = ""
        generic_name = ""
        icon = ""
        wm_class = ""
        exec_cmd = ""
        categories = ""
        hidden = False
        entry_type = "Application"
        in_desktop_entry = False

        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("["):
                        in_desktop_entry = (line == "[Desktop Entry]")
                        continue

                    if not in_desktop_entry:
                        continue

                    # Exact key prefix matching (prevent locale keys like Name[de]= from overriding Name=)
                    if line.startswith("Name=") and not name:
                        name = line[5:].strip()
                    elif line.startswith("GenericName=") and not generic_name:
                        generic_name = line[12:].strip()
                    elif line.startswith("Icon=") and not icon:
                        icon = line[5:].strip()
                    elif line.startswith("StartupWMClass=") and not wm_class:
                        wm_class = line[15:].strip()
                    elif line.startswith("Exec=") and not exec_cmd:
                        exec_cmd = line[5:].strip()
                    elif line.startswith("Categories=") and not categories:
                        categories = line[11:].strip()
                    elif line.startswith("Hidden="):
                        hidden = line[7:].strip() == "true"
                    elif line.startswith("Type="):
                        entry_type = line[5:].strip()
        except OSError:
            return

        # NoDisplay hides launcher items, but running applications still need names.
        if not name or hidden or entry_type != "Application":
            return

        name, generic_name, icon, wm_class = map(_unescape, (name, generic_name, icon, wm_class))

        # Determine best category description
        category_desc = generic_name
        if not category_desc and categories:
            cats = [c.strip() for c in categories.split(";") if c.strip()]
            for c in cats:
                match c:
                    case "Application" | "X-GNOME-Utilities" | "GTK" | "Qt" | "KDE" | "GNOME":
                        continue
                    case "AudioVideo":
                        category_desc = "Audio & Video"
                    case "Network":
                        category_desc = "Internet"
                    case "Development":
                        category_desc = "Development"
                    case "Utility":
                        category_desc = "Utilities"
                    case "System":
                        category_desc = "System"
                    case "Game":
                        category_desc = "Gaming"
                    case "Graphics":
                        category_desc = "Graphics"
                    case "Office":
                        category_desc = "Office"
                    case "TerminalEmulator":
                        category_desc = "Terminal & Shell"
                    case _:
                        category_desc = c
                break
            if not category_desc and cats:
                category_desc = cats[0]

        if not category_desc:
            category_desc = "Application"

        stem = desktop_stem or filepath.stem
        info = AppInfo(
            name=name,
            category=category_desc,
            icon=icon or "application-x-executable",
            window_class=wm_class or stem,
        )

        # Index by StartupWMClass
        if wm_class:
            self._by_wmclass.setdefault(wm_class.lower(), info)

        # Index by stem (e.g. firefox from firefox.desktop)
        self._by_stem.setdefault(stem.lower(), info)

        # If stem has dots (e.g. org.kde.kdenlive), also index the last segment
        if "." in stem:
            last_seg = stem.split(".")[-1].lower()
            self._by_alias.setdefault(last_seg, info)

        # Index by exact Name
        self._by_name.setdefault(name.lower(), info)

        # Index by Exec command (handle quotes and path basenames cleanly)
        if exec_cmd:
            try:
                words = shlex.split(_unescape(exec_cmd))
            except ValueError:
                words = []
            if words and Path(words[0]).name == "env":
                words = words[1:]
                while words and ("=" in words[0] or words[0] == "--"):
                    words.pop(0)
            if words and not words[0].startswith("-"):
                self._by_exec.setdefault(Path(words[0]).name.lower(), info)

    def _cache(self, key: str, info: AppInfo) -> AppInfo:
        self._resolved_cache[key] = info
        self._resolved_cache.move_to_end(key)
        if len(self._resolved_cache) > 512:
            self._resolved_cache.popitem(last=False)
        return info

    def resolve(self, window_class: str, window_title: str = "") -> AppInfo:
        """
        Given a raw Hyprland window class and optional title, resolve to a
        clean AppInfo object with human-readable Name, Category, and Icon.
        """
        if not window_class:
            return AppInfo(
                name="Desktop / Idle",
                category="System",
                icon="user-desktop",
                window_class="desktop",
            )

        cache_key = window_class.lower()
        if cache_key in self._resolved_cache:
            self._resolved_cache.move_to_end(cache_key)
            return self._resolved_cache[cache_key]

        wc_lower = window_class.lower()

        # Terminal heuristic prioritization
        if wc_lower in KNOWN_TERMINALS:
            entry = self._by_wmclass.get(wc_lower) or self._by_stem.get(wc_lower)
            term_name = window_class.replace("-", " ").replace("_", " ").title()
            res = AppInfo(
                name=entry.name if entry else f"{term_name} Terminal",
                category="Terminal & Shell",
                icon=entry.icon if entry else "utilities-terminal",
                window_class=window_class,
            )
            return self._cache(cache_key, res)

        # Exact identifiers always beat guessed aliases and class prefixes.
        for index in (self._by_wmclass, self._by_stem, self._by_name, self._by_exec):
            if wc_lower in index:
                return self._cache(cache_key, index[wc_lower])

        if wc_lower in self._by_alias:
            return self._cache(cache_key, self._by_alias[wc_lower])

        # 3. Check if window_class has dots or hyphens (e.g. codium-url-handler -> codium / vscodium)
        if "." in wc_lower:
            last_seg = wc_lower.split(".")[-1]
            if last_seg in self._by_stem:
                res = self._by_stem[last_seg]
                return self._cache(cache_key, res)
            if last_seg in self._by_wmclass:
                res = self._by_wmclass[last_seg]
                return self._cache(cache_key, res)

        if "-" in wc_lower:
            first_seg = wc_lower.split("-")[0]
            if first_seg in self._by_stem:
                res = self._by_stem[first_seg]
                return self._cache(cache_key, res)
            if first_seg in self._by_wmclass:
                res = self._by_wmclass[first_seg]
                return self._cache(cache_key, res)

        # 6. Fallback heuristics for un-indexed window classes (including reverse-DNS)
        clean_name = window_class
        if "." in window_class:
            parts = [p for p in window_class.split(".") if p]
            if len(parts) > 1:
                clean_name = parts[-1]
                if clean_name.lower() in ("desktop", "client", "app", "ui") and len(parts) > 2:
                    clean_name = f"{parts[-2]} {parts[-1]}"

        clean_name = (
            clean_name.replace("-", " ")
            .replace("_", " ")
            .strip()
            .title()
        )

        res = AppInfo(
            name=clean_name or window_class,
            category="Application",
            icon="application-x-executable",
            window_class=window_class,
        )
        return self._cache(cache_key, res)


if __name__ == "__main__":
    resolver = DesktopResolver()
    test_classes = (
        sys.argv[1:]
        if len(sys.argv) > 1
        else [
            ("firefox", ""),
            ("code", ""),
            ("steam", ""),
            ("kitty", "nvim desktop_resolver.py"),
            ("org.kde.kdenlive", ""),
            ("codium-url-handler", ""),
        ]
    )
    print("\033[1;34m::\033[0m \033[1mDusky Screentime Desktop Resolver Test\033[0m\n")
    for item in test_classes:
        cls, title = item if isinstance(item, tuple) else (item, "")
        info = resolver.resolve(cls, title)
        print(
            f"Class: \033[96m{cls:<22}\033[0m Title: \033[90m{title:<25}\033[0m => Name: \033[92m{info.name:<25}\033[0m | Category: \033[93m{info.category:<18}\033[0m | Icon: {info.icon}"
        )
