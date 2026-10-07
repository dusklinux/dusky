#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: HYPRLOCK THEME ENGINE
===============================================================================
Engine Type: "hyprlock"
Target: ~/.config/hypr/hyprlock.conf

Switches the active Hyprlock configuration by atomically updating the
`source = ~/.config/hypr/hyprlock_themes/<theme>/hyprlock.conf` directive in the
user's `hyprlock.conf`.

Theme Structure:
  ~/.config/hypr/hyprlock_themes/
    ├── 001_dusky/
    │   ├── hyprlock.conf
    │   └── theme.json  {"name": "...", "description": "...", "author": "..."}
    ├── 002_dusky_oled/
    │   ├── hyprlock.conf
    │   └── theme.json
    └── ...

State is saved to ~/.config/dusky/settings/hyprlock/.dusky_hyprlock_state.json
following the canonical Dusky TUI state management pattern.
===============================================================================
"""

import os
lazy import json
import re
lazy import stat
lazy import tempfile
lazy import subprocess
lazy import shutil
lazy from pathlib import Path
lazy from typing import Any

from python.frontend.core_types import BaseEngine


class HyprlockEngine(BaseEngine):
    """
    Production-grade engine for managing Hyprlock themes in the Dusky ecosystem.

    Guarantees:
    - Zero username hardcoding (purely uses dynamic Home / XDG paths).
    - Atomic file mutations via tempfile & os.replace to prevent corruption.
    - Preserves external comments and config structures in hyprlock.conf.
    - Full bidirectional synchronization with state files.
    - Native support for headless automation, CLI scripting, and TUI interactivity.
    """

    def __init__(
        self,
        config_path: str = "~/.config/hypr/hyprlock.conf",
        themes_dir: str = "~/.config/hypr/hyprlock_themes"
    ):
        self.config_path = Path(config_path).expanduser().resolve()
        self.themes_dir = Path(themes_dir).expanduser().resolve()

        self.state_dir = Path("~/.config/dusky/settings/hyprlock").expanduser().resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.state_dir / ".dusky_hyprlock_state.json"

        self.cache: dict[str, Any] = {}
        self.theme_dirs: list[Path] = []
        self.theme_folders: list[str] = []
        self.theme_names: list[str] = []
        self.theme_metadata: list[dict[str, Any]] = []
        self.file_mtime: float = 0.0

    @property
    def target_path(self) -> str:
        return str(self.config_path)

    def _refresh_themes(self) -> None:
        """Discovers all valid theme subdirectories containing hyprlock.conf."""
        self.theme_dirs = []
        self.theme_folders = []
        self.theme_names = []
        self.theme_metadata = []

        if not self.themes_dir.is_dir():
            return

        # Find all subdirectories that contain hyprlock.conf (sorted naturally)
        candidates = sorted(
            [d for d in self.themes_dir.iterdir() if d.is_dir() and (d / "hyprlock.conf").is_file()],
            key=lambda p: p.name.lower()
        )

        for d in candidates:
            folder = d.name
            conf_path = d / "hyprlock.conf"
            json_path = d / "theme.json"

            display_name = folder
            description = ""
            author = ""

            if json_path.is_file():
                try:
                    data = json.loads(json_path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        display_name = str(data.get("name") or folder).strip()
                        description = str(data.get("description") or "").strip()
                        author = str(data.get("author") or "").strip()
                except (OSError, json.JSONDecodeError):
                    pass

            clean_name = display_name
            if clean_name.endswith("(Default)"):
                clean_name = clean_name[:-9].strip()
            if clean_name.islower():
                clean_name = clean_name.title()

            meta = {
                "folder": folder,
                "name": clean_name,
                "raw_name": display_name,
                "description": description,
                "author": author,
                "dir": d,
                "conf": conf_path,
                "conf_path": conf_path
            }

            self.theme_dirs.append(d)
            self.theme_folders.append(folder)
            self.theme_names.append(clean_name)
            self.theme_metadata.append(meta)

    def _extract_source_from_config(self) -> str | None:
        """Extracts the active source path from hyprlock.conf if present."""
        if not self.config_path.is_file():
            return None

        try:
            content = self.config_path.read_text(encoding="utf-8")
            for line in content.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                # Match: source = <path>
                if "=" in stripped:
                    key, val = stripped.split("=", 1)
                    if key.strip() == "source":
                        clean_val = val.split("#", 1)[0].strip()
                        return clean_val
        except OSError:
            pass
        return None

    def load_state(self) -> dict[str, Any]:
        """Parses active configuration into a flat state dictionary mapped to the UI."""
        self._refresh_themes()

        if self.config_path.exists():
            self.file_mtime = self.config_path.stat().st_mtime

        active_idx = -1
        active_folder = ""
        active_name = ""

        # 1. Attempt detection by parsing the source directive in hyprlock.conf
        raw_source = self._extract_source_from_config()
        if raw_source:
            resolved_source = Path(raw_source).expanduser().resolve()
            for i, theme_dir in enumerate(self.theme_dirs):
                theme_conf = (theme_dir / "hyprlock.conf").resolve()
                if resolved_source == theme_conf:
                    active_idx = i
                    active_folder = self.theme_folders[i]
                    active_name = self.theme_names[i]
                    break

        # 2. Fallback to state file if config parsing yielded no exact theme match
        if active_idx == -1 and self.state_file.is_file():
            try:
                state_data = json.loads(self.state_file.read_text(encoding="utf-8"))
                saved_folder = state_data.get("active_theme_folder")
                saved_name = state_data.get("active_theme_name")
                saved_idx = state_data.get("active_theme_index", -1)

                if saved_folder and saved_folder in self.theme_folders:
                    active_idx = self.theme_folders.index(saved_folder)
                elif saved_name and saved_name in self.theme_names:
                    active_idx = self.theme_names.index(saved_name)
                elif 0 <= saved_idx < len(self.theme_folders):
                    active_idx = saved_idx

                if active_idx != -1:
                    active_folder = self.theme_folders[active_idx]
                    active_name = self.theme_names[active_idx]
            except (OSError, json.JSONDecodeError):
                pass

        # 3. Ultimate fallback: default to first theme if available
        if active_idx == -1 and self.theme_folders:
            active_idx = 0
            active_folder = self.theme_folders[0]
            active_name = self.theme_names[0]

        active_number = active_idx + 1 if active_idx >= 0 else 1
        source_repr = f"~/.config/hypr/hyprlock_themes/{active_folder}/hyprlock.conf" if active_folder else ""

        self.cache = {
            "active_theme_index": active_idx,
            "active_theme_folder": active_folder,
            "active_theme_name": active_name,
            "active_theme_number": active_number,
            "hyprlock": active_number,
            "source": source_repr,

            "DEFAULT/active_theme_index": active_idx,
            "DEFAULT/active_theme_folder": active_folder,
            "DEFAULT/active_theme_name": active_name,
            "DEFAULT/active_theme_number": active_number,
            "DEFAULT/hyprlock": active_number,
            "DEFAULT/source": source_repr,

            # Momentary button triggers locked to False on load
            "toggle_forward": False,
            "DEFAULT/toggle_forward": False,
            "toggle_backward": False,
            "DEFAULT/toggle_backward": False,
            "action_heal_state": False,
            "DEFAULT/action_heal_state": False,
            "action_test_lock": False,
            "DEFAULT/action_test_lock": False,
        }

        # Dynamic radio button keys for theme items
        for i, folder in enumerate(self.theme_folders):
            theme_key = f"__hyprlock_theme_{folder}"
            is_selected = (i == active_idx)
            self.cache[theme_key] = is_selected
            self.cache[f"DEFAULT/{theme_key}"] = is_selected

        return self.cache

    def _atomic_write_config(self, new_source_path: str) -> bool:
        """
        Safely writes the new source directive into hyprlock.conf.
        Preserves all comments and other configuration directives if present.
        """
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        out_lines: list[str] = []
        replaced = False

        if self.config_path.is_file():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    for line in f:
                        stripped = line.strip()
                        if not stripped.startswith("#") and "=" in stripped:
                            k = stripped.split("=", 1)[0].strip()
                            if k == "source":
                                # Extract any trailing comment
                                comment = ""
                                if "#" in line:
                                    hash_idx = line.index("#")
                                    comment = " " + line[hash_idx:].rstrip("\n")
                                out_lines.append(f"source = {new_source_path}{comment}\n")
                                replaced = True
                                continue
                        out_lines.append(line)
            except OSError:
                return False

        if not replaced:
            out_lines.insert(0, f"source = {new_source_path}\n")

        # Atomic commit via temp file in same directory
        tmp_file: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                dir=self.config_path.parent,
                delete=False,
                encoding="utf-8"
            ) as tf:
                tmp_file = Path(tf.name)
                tf.writelines(out_lines)
                tf.flush()
                os.fsync(tf.fileno())

            if self.config_path.exists():
                try:
                    st = self.config_path.stat()
                    tmp_file.chmod(stat.S_IMODE(st.st_mode))
                except OSError:
                    pass

            os.replace(tmp_file, self.config_path)
            self.file_mtime = self.config_path.stat().st_mtime
            return True
        except OSError:
            if tmp_file and tmp_file.exists():
                try:
                    tmp_file.unlink(missing_ok=True)
                except OSError:
                    pass
            return False

    def _save_state_file(self, folder: str, name: str, index: int) -> None:
        """Atomically records the active theme in the persistent JSON state file."""
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            data = {
                "active_theme_folder": folder,
                "active_theme_name": name,
                "active_theme_index": index
            }
            tmp_state = self.state_file.with_suffix(".tmp")
            tmp_state.write_text(json.dumps(data, indent=4), encoding="utf-8")
            os.replace(tmp_state, self.state_file)
        except OSError:
            pass

    def _apply_theme_by_index(self, index: int) -> tuple[bool, str]:
        """Applies a theme by its zero-based index."""
        if not self.theme_folders:
            return False, "No Hyprlock themes found in ~/.config/hypr/hyprlock_themes/"

        if not (0 <= index < len(self.theme_folders)):
            return False, f"Theme index {index} is out of range (0..{len(self.theme_folders)-1})."

        folder = self.theme_folders[index]
        display_name = self.theme_names[index]
        source_directive = f"~/.config/hypr/hyprlock_themes/{folder}/hyprlock.conf"

        success = self._atomic_write_config(source_directive)
        if not success:
            return False, f"Failed to write configuration to {self.config_path.name}"

        self._save_state_file(folder, display_name, index)

        active_number = index + 1
        self.cache.update({
            "active_theme_index": index,
            "active_theme_folder": folder,
            "active_theme_name": display_name,
            "active_theme_number": active_number,
            "hyprlock": active_number,
            "source": source_directive,

            "DEFAULT/active_theme_index": index,
            "DEFAULT/active_theme_folder": folder,
            "DEFAULT/active_theme_name": display_name,
            "DEFAULT/active_theme_number": active_number,
            "DEFAULT/hyprlock": active_number,
            "DEFAULT/source": source_directive,
        })

        for i, fld in enumerate(self.theme_folders):
            theme_key = f"__hyprlock_theme_{fld}"
            is_sel = (i == index)
            self.cache[theme_key] = is_sel
            self.cache[f"DEFAULT/{theme_key}"] = is_sel

        return True, f"Applied Hyprlock theme: {display_name}"

    def write_value(
        self,
        target_key: str,
        target_scope: str,
        new_value: str,
        item_type: str = "string"
    ) -> tuple[bool, str, str]:
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    def write_batch(
        self,
        changes: list[tuple[str, str, str, str]]
    ) -> tuple[bool, str, str]:
        self.load_state()

        if not self.theme_folders:
            return False, "No valid themes found in ~/.config/hypr/hyprlock_themes/", ""

        current_idx = self.cache.get("active_theme_index", 0)
        if current_idx < 0:
            current_idx = 0

        target_idx = current_idx
        requires_write = False
        status_msg = ""

        for key, scope, val, itype in changes:
            str_val = str(val).strip().lower()

            match key:
                case "active_theme_number" | "hyprlock":
                    try:
                        num = int(val)
                        if 1 <= num <= len(self.theme_folders):
                            target_idx = num - 1
                            requires_write = True
                        else:
                            return False, f"Theme number {val} is out of bounds (1..{len(self.theme_folders)}).", ""
                    except ValueError:
                        return False, f"Invalid theme number: {val}", ""

                case "active_theme_index":
                    try:
                        idx = int(val)
                        if 0 <= idx < len(self.theme_folders):
                            target_idx = idx
                            requires_write = True
                        else:
                            return False, f"Theme index {val} is out of bounds.", ""
                    except ValueError:
                        return False, f"Invalid theme index: {val}", ""

                case "active_theme_folder" | "active_theme_name" | "theme":
                    target_str = str(val).strip()
                    matched = False

                    # Exact folder match
                    if target_str in self.theme_folders:
                        target_idx = self.theme_folders.index(target_str)
                        requires_write = True
                        matched = True
                    # Case-insensitive folder match
                    elif not matched:
                        for idx, fld in enumerate(self.theme_folders):
                            if fld.lower() == target_str.lower():
                                target_idx = idx
                                requires_write = True
                                matched = True
                                break
                    # Exact or case-insensitive display name match
                    if not matched:
                        for idx, nm in enumerate(self.theme_names):
                            if nm.lower() == target_str.lower():
                                target_idx = idx
                                requires_write = True
                                matched = True
                                break
                    # Raw name match fallback (e.g. including (Default))
                    if not matched:
                        for idx, m in enumerate(self.theme_metadata):
                            if m.get("raw_name", "").lower() == target_str.lower():
                                target_idx = idx
                                requires_write = True
                                matched = True
                                break
                    # Number match fallback
                    if not matched:
                        try:
                            num = int(target_str)
                            if 1 <= num <= len(self.theme_folders):
                                target_idx = num - 1
                                requires_write = True
                                matched = True
                        except ValueError:
                            pass

                    if not matched:
                        return False, f"Theme '{target_str}' not found in hyprlock_themes.", ""

                case "toggle_forward" if str_val in ("true", "1", "yes"):
                    target_idx = (current_idx + 1) % len(self.theme_folders)
                    requires_write = True

                case "toggle_backward" if str_val in ("true", "1", "yes"):
                    target_idx = (current_idx - 1 + len(self.theme_folders)) % len(self.theme_folders)
                    requires_write = True

                case "action_heal_state" if str_val in ("true", "1", "yes"):
                    if self.state_file.is_file():
                        try:
                            state_data = json.loads(self.state_file.read_text(encoding="utf-8"))
                            saved_fld = state_data.get("active_theme_folder")
                            saved_idx = state_data.get("active_theme_index", -1)
                            if saved_fld and saved_fld in self.theme_folders:
                                target_idx = self.theme_folders.index(saved_fld)
                            elif 0 <= saved_idx < len(self.theme_folders):
                                target_idx = saved_idx
                        except (OSError, json.JSONDecodeError):
                            pass
                    requires_write = True
                    status_msg = "State restored from file and config healed."

                case "action_test_lock" if str_val in ("true", "1", "yes"):
                    # Launch hyprlock test in background
                    lock_cmd = shutil.which("hyprlock") or "hyprlock"
                    try:
                        subprocess.Popen(
                            [lock_cmd],
                            start_new_session=True,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL
                        )
                        status_msg = "Launched hyprlock test session."
                    except OSError as e:
                        return False, f"Failed to launch hyprlock: {e}", ""

                case _ if key.startswith("__hyprlock_theme_"):
                    fld_name = key[len("__hyprlock_theme_"):]
                    if fld_name in self.theme_folders:
                        target_idx = self.theme_folders.index(fld_name)
                        requires_write = True
                    else:
                        return False, f"Theme folder '{fld_name}' not found.", ""

        if requires_write:
            ok, msg = self._apply_theme_by_index(target_idx)
            if not ok:
                return False, msg, ""
            if not status_msg:
                status_msg = msg
            return True, status_msg, ""

        return True, status_msg or "No pending changes.", ""

    # =========================================================================
    # PUBLIC SCRIPTING HELPERS
    # =========================================================================
    def get_themes(self) -> list[dict[str, Any]]:
        """Returns the full metadata list of all discovered themes."""
        self._refresh_themes()
        return self.theme_metadata

    def get_active_theme(self) -> dict[str, Any] | None:
        """Returns metadata for the currently active theme."""
        self.load_state()
        idx = self.cache.get("active_theme_index", -1)
        if 0 <= idx < len(self.theme_metadata):
            return self.theme_metadata[idx]
        return None

    def apply_theme(self, identifier: str | int) -> tuple[bool, str]:
        """Applies a theme by name, folder, or 1-based number."""
        return self.write_batch([("theme", "DEFAULT", str(identifier), "string")])[:2]

    def next_theme(self) -> tuple[bool, str]:
        """Cycles to the next theme chronologically."""
        return self.write_batch([("toggle_forward", "DEFAULT", "true", "bool")])[:2]

    def prev_theme(self) -> tuple[bool, str]:
        """Cycles to the previous theme chronologically."""
        return self.write_batch([("toggle_backward", "DEFAULT", "true", "bool")])[:2]
