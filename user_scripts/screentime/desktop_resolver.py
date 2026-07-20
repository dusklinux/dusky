#!/usr/bin/env python3
"""
===============================================================================
DUSKY SCREENTIME: DESKTOP ENTRY RESOLVER
===============================================================================
Scans and parses system and user `.desktop` entries line-by-line without any
subprocesses or regex bottlenecks, matching the exact behavior of Rofi
(`rofi/dusky_launcher.sh`) to provide clean application names, icons, and
categories from raw Hyprland window classes.
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple


@dataclass
class AppInfo:
    name: str
    category: str
    icon: str
    window_class: str


class DesktopResolver:
    """
    High-performance caching resolver for XDG application desktop entries.
    """

    def __init__(self):
        # Lookup tables mapped by lowercase key to AppInfo
        self._by_wmclass: Dict[str, AppInfo] = {}
        self._by_stem: Dict[str, AppInfo] = {}
        self._by_name: Dict[str, AppInfo] = {}
        self._by_exec: Dict[str, AppInfo] = {}

        # Cache for previously resolved window_classes during runtime
        self._resolved_cache: Dict[str, AppInfo] = {}
        self.reload()

    def reload(self) -> None:
        """
        Scan all XDG application directories and build lookup indexes.
        """
        self._by_wmclass.clear()
        self._by_stem.clear()
        self._by_name.clear()
        self._by_exec.clear()
        self._resolved_cache.clear()

        search_dirs = [
            Path(os.path.expanduser("~/.local/share/applications")),
            Path("/usr/share/applications"),
            Path("/usr/local/share/applications"),
            Path("/var/lib/flatpak/exports/share/applications"),
            Path(
                os.path.expanduser("~/.local/share/flatpak/exports/share/applications")
            ),
        ]

        for d in search_dirs:
            if not d.exists() or not d.is_dir():
                continue
            for filepath in d.glob("*.desktop"):
                self._parse_file(filepath)

    def _parse_file(self, filepath: Path) -> None:
        name = ""
        generic_name = ""
        icon = ""
        wm_class = ""
        exec_cmd = ""
        categories = ""
        in_desktop_entry = False

        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("["):
                        if line == "[Desktop Entry]":
                            in_desktop_entry = True
                        else:
                            in_desktop_entry = False
                        continue

                    if not in_desktop_entry:
                        continue

                    # Native prefix matching without regex
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
        except Exception:
            return

        if not name:
            return

        # Clean up XML/Pango entities if present
        name = name.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        generic_name = (
            generic_name.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        )

        # Determine best category description
        category_desc = generic_name
        if not category_desc and categories:
            # Pick first meaningful category from standard XDG categories
            cats = [c.strip() for c in categories.split(";") if c.strip()]
            for c in cats:
                if c not in (
                    "Application",
                    "X-GNOME-Utilities",
                    "GTK",
                    "Qt",
                    "KDE",
                    "GNOME",
                ):
                    # Format e.g. AudioVideo -> Audio & Video
                    if c == "AudioVideo":
                        category_desc = "Audio & Video"
                    elif c == "Network":
                        category_desc = "Internet"
                    elif c == "Development":
                        category_desc = "Development"
                    elif c == "Utility":
                        category_desc = "Utilities"
                    elif c == "System":
                        category_desc = "System"
                    elif c == "Game":
                        category_desc = "Gaming"
                    elif c == "Graphics":
                        category_desc = "Graphics"
                    elif c == "Office":
                        category_desc = "Office"
                    else:
                        category_desc = c
                    break
            if not category_desc and cats:
                category_desc = cats[0]
        if not category_desc:
            category_desc = "Application"

        stem = filepath.stem
        info = AppInfo(
            name=name,
            category=category_desc,
            icon=icon or "application-x-executable",
            window_class=wm_class or stem,
        )

        # Index by StartupWMClass
        if wm_class:
            self._by_wmclass[wm_class.lower()] = info

        # Index by stem (e.g. firefox from firefox.desktop)
        self._by_stem[stem.lower()] = info

        # If stem has dots (e.g. org.kde.kdenlive), also index the last segment
        if "." in stem:
            last_seg = stem.split(".")[-1].lower()
            if last_seg not in self._by_stem:
                self._by_stem[last_seg] = info

        # Index by exact Name
        self._by_name[name.lower()] = info

        # Index by Exec command
        if exec_cmd:
            # Strip %u, %F, etc. and take executable basename
            clean_exec = exec_cmd.split()[0].split("/")[-1].lower()
            if clean_exec and clean_exec not in self._by_exec:
                self._by_exec[clean_exec] = info

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

        cache_key = f"{window_class.lower()}::{window_title.lower()}"
        if cache_key in self._resolved_cache:
            return self._resolved_cache[cache_key]

        wc_lower = window_class.lower()

        # 1. Check StartupWMClass exact match
        if wc_lower in self._by_wmclass:
            res = self._by_wmclass[wc_lower]
            self._resolved_cache[cache_key] = res
            return res

        # 2. Check filename stem exact match
        if wc_lower in self._by_stem:
            res = self._by_stem[wc_lower]
            self._resolved_cache[cache_key] = res
            return res

        # 3. Check if window_class has dots or hyphens (e.g. codium-url-handler -> codium / vscodium)
        if "." in wc_lower:
            last_seg = wc_lower.split(".")[-1]
            if last_seg in self._by_stem:
                res = self._by_stem[last_seg]
                self._resolved_cache[cache_key] = res
                return res
            if last_seg in self._by_wmclass:
                res = self._by_wmclass[last_seg]
                self._resolved_cache[cache_key] = res
                return res

        if "-" in wc_lower:
            first_seg = wc_lower.split("-")[0]
            if first_seg in self._by_stem:
                res = self._by_stem[first_seg]
                self._resolved_cache[cache_key] = res
                return res
            if first_seg in self._by_wmclass:
                res = self._by_wmclass[first_seg]
                self._resolved_cache[cache_key] = res
                return res

        # 4. Check Name exact match
        if wc_lower in self._by_name:
            res = self._by_name[wc_lower]
            self._resolved_cache[cache_key] = res
            return res

        # 5. Check Exec command exact match
        if wc_lower in self._by_exec:
            res = self._by_exec[wc_lower]
            self._resolved_cache[cache_key] = res
            return res

        # 6. Fallback heuristics for common un-indexed window classes
        if wc_lower in (
            "kitty",
            "alacritty",
            "wezterm",
            "foot",
            "ghostty",
            "konsole",
            "gnome-terminal",
            "urxvt",
        ):
            # Check if terminal title shows active application
            title_clean = (
                window_title.split(" - ")[-1] if " - " in window_title else window_title
            )
            res = AppInfo(
                name=f"{window_class.capitalize()} ({title_clean})"
                if title_clean and title_clean != window_class
                else f"{window_class.capitalize()} Terminal",
                category="Terminal & Shell",
                icon="utilities-terminal",
                window_class=window_class,
            )
            self._resolved_cache[cache_key] = res
            return res

        # Clean up window_class formatting into human title
        clean_name = (
            window_class.replace("-", " ")
            .replace("_", " ")
            .replace(".", " ")
            .strip()
            .title()
        )
        res = AppInfo(
            name=clean_name or window_class,
            category="Application",
            icon="application-x-executable",
            window_class=window_class,
        )
        self._resolved_cache[cache_key] = res
        return res


if __name__ == "__main__":
    resolver = DesktopResolver()
    test_classes = (
        sys.argv[1:]
        if len(sys.argv) > 1
        else [
            "firefox",
            "code",
            "steam",
            "kitty",
            "org.kde.kdenlive",
            "codium-url-handler",
        ]
    )
    print("\033[1;34m::\033[0m \033[1mDusky Screentime Desktop Resolver Test\033[0m\n")
    for cls in test_classes:
        info = resolver.resolve(cls)
        print(
            f"Class: \033[96m{cls:<22}\033[0m => Name: \033[92m{info.name:<25}\033[0m | Category: \033[93m{info.category:<18}\033[0m | Icon: {info.icon}"
        )
