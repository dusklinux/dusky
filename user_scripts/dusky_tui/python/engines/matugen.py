#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: MATUGEN TOML CONFIGURATION ENGINE
===============================================================================
Target: Arch Linux / Hyprland / Matugen dynamic TOML template manager.
Python 3.14.6 implementation using PEP 695 type aliases, structural pattern matching,
concurrency mtime checks, and atomic file replacements.
"""

import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any, Self, override

from python.frontend.core_types import BaseEngine

# PEP 695 Strict Type Alias
type ScopeKeyMap = dict[str, bool]
type ChangeTuple = tuple[str, str, str, str]


class MatugenEngine(BaseEngine):
    """
    Production-grade AST-like parser and mutator for Matugen template blocks inside `config.toml`.
    
    Provides strict atomicity, concurrency protection (mtime locks), precise multiline string
    tracking (preserving post_hook scripts with triple single/double quotes), blank-line demarcation,
    and comment-toggling.
    """

    # Matches active or commented template headers, e.g.:
    # [templates.gtk3]  or  # [templates.gtk4]  or  #   [templates.master_dump]
    _RE_TEMPLATE_HEADER = re.compile(
        r"^[ \t]*(#?)[ \t]*\[templates\.['\"]?([a-zA-Z0-9_.-]+)['\"]?\][ \t]*(?:#.*)?$"
    )

    # General TOML section header check
    _RE_ANY_HEADER = re.compile(r"^[ \t]*#?[ \t]*\[.*\][ \t]*(?:#.*)?$")

    def __init__(self, config_path: str | Path = "~/.config/matugen/config.toml") -> None:
        self.config_path = Path(config_path).expanduser().resolve()
        self.cache: dict[str, bool] = {}
        self.file_mtime: float = 0.0

    @property
    @override
    def target_path(self) -> str:
        return str(self.config_path)

    @override
    def load_state(self) -> dict[str, bool]:
        """
        Parses all template blocks from config.toml into a state map.
        Key: template_name (e.g. 'gtk3', 'waybar')
        Value: True if active (uncommented header), False if disabled (commented header).
        """
        if not self.config_path.exists():
            self.cache = {}
            return self.cache

        try:
            self.file_mtime = self.config_path.stat().st_mtime
        except OSError:
            self.file_mtime = 0.0

        self.cache = {}
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                for line in f:
                    match = self._RE_TEMPLATE_HEADER.match(line)
                    if match:
                        cmt_char, template_key = match.groups()
                        is_active = (cmt_char == "")
                        self.cache[template_key] = is_active
                        self.cache[f"DEFAULT/{template_key}"] = is_active
        except (OSError, IOError) as e:
            print(f"[-] MatugenEngine: Failed to read {self.config_path}: {e}")

        return self.cache

    @override
    def write_value(
        self,
        target_key: str,
        target_scope: str,
        new_value: str,
        item_type: str = "bool"
    ) -> tuple[bool, str, str]:
        """Routes single write calls to batch mutator."""
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    @override
    def write_batch(self, changes: list[ChangeTuple]) -> tuple[bool, str, str]:
        """
        Atomically toggles template blocks between commented (#) and uncommented states.
        Enforces mtime concurrency locks, multiline quote boundary safety, and blank-line demarcation.
        """
        if not changes:
            return True, "No pending changes.", ""

        if not self.config_path.exists():
            return False, f"Target configuration file {self.config_path} does not exist.", ""

        # Concurrency safety lock
        try:
            current_mtime = self.config_path.stat().st_mtime
            if self.file_mtime > 0 and current_mtime > self.file_mtime:
                return False, f"File {self.config_path.name} was modified externally. Reload required.", ""
        except OSError:
            pass

        # Read full lines
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except (OSError, IOError) as e:
            return False, f"Failed to read {self.config_path}: {e}", ""

        # Build normalized changes map: target_key -> bool
        changes_dict: dict[str, bool] = {}
        for key, scope, val, _ in changes:
            clean_key = key.split("/")[-1] if "/" in key else key
            if isinstance(val, bool):
                bool_val = val
            elif isinstance(val, str):
                bool_val = val.strip().lower() in {"true", "1", "yes", "on", "t", "y"}
            else:
                bool_val = bool(val)
            changes_dict[clean_key] = bool_val

        modified = False
        triple_sq = "'''"
        triple_dq = '"""'

        for key, target_active in changes_dict.items():
            start_idx: int | None = None
            is_currently_active: bool = False

            # 1. Locate start of template block
            for idx, line in enumerate(lines):
                m = self._RE_TEMPLATE_HEADER.match(line)
                if m and m.group(2) == key:
                    start_idx = idx
                    is_currently_active = (m.group(1) == "")
                    break

            if start_idx is None:
                print(f"[!] MatugenEngine: Key '{key}' not found in {self.config_path.name}")
                continue

            # If current state already matches target state, skip mutation to protect internal block comments
            if is_currently_active == target_active:
                self.cache[key] = target_active
                self.cache[f"DEFAULT/{key}"] = target_active
                continue

            # 2. Determine end of template block using multiline tracking and demarcation rules
            end_idx = len(lines) - 1
            in_multiline = False
            multiline_token = ""

            for i in range(start_idx + 1, len(lines)):
                curr = lines[i]
                stripped = re.sub(r"^[ \t]*#[ \t]?", "", curr)

                if not in_multiline:
                    c_sq = stripped.count(triple_sq)
                    if c_sq % 2 == 1:
                        in_multiline = True
                        multiline_token = triple_sq

                    c_dq = stripped.count(triple_dq)
                    if c_dq % 2 == 1:
                        in_multiline = True
                        multiline_token = triple_dq

                    if not in_multiline:
                        # Direct hit: Next section header encountered
                        if self._RE_ANY_HEADER.match(curr):
                            end_idx = i - 1
                            break

                        # Blank line demarcation check
                        if curr.strip() == "":
                            # Look ahead to next non-blank line
                            next_nb_idx = None
                            for k in range(i + 1, len(lines)):
                                if lines[k].strip() != "":
                                    next_nb_idx = k
                                    break

                            if next_nb_idx is not None:
                                next_line = lines[next_nb_idx]
                                # Next non-blank line is a section header
                                if self._RE_ANY_HEADER.match(next_line):
                                    end_idx = i - 1
                                    break

                                # Next non-blank line is a comment preceding a section header
                                if re.match(r"^[ \t]*#", next_line):
                                    hdr_found = False
                                    for k2 in range(next_nb_idx, len(lines)):
                                        l2 = lines[k2]
                                        if l2.strip() == "":
                                            continue
                                        if self._RE_ANY_HEADER.match(l2):
                                            hdr_found = True
                                            break
                                        if not re.match(r"^[ \t]*#", l2):
                                            break
                                    if hdr_found:
                                        end_idx = i - 1
                                        break
                else:
                    c_tok = stripped.count(multiline_token)
                    if c_tok % 2 == 1:
                        in_multiline = False
                        multiline_token = ""

            # 3. Apply state mutation to block lines [start_idx .. end_idx]
            for i in range(start_idx, end_idx + 1):
                line = lines[i]

                if target_active:
                    # Uncomment line by stripping exactly one outer comment prefix (#  or #)
                    if line.startswith("# "):
                        lines[i] = line[2:]
                        modified = True
                    elif line.startswith("#"):
                        lines[i] = line[1:]
                        modified = True
                else:
                    # Comment line by prepending outer comment prefix (# )
                    if line.strip() != "":
                        lines[i] = f"# {line}"
                        modified = True

            # Update engine cache state
            self.cache[key] = target_active
            self.cache[f"DEFAULT/{key}"] = target_active

        if not modified:
            return True, "No modifications required.", ""

        # 4. Atomic file write
        target_dir = self.config_path.parent
        target_dir.mkdir(parents=True, exist_ok=True)

        try:
            fd, tmp_path_str = tempfile.mkstemp(
                dir=str(target_dir),
                prefix=f".{self.config_path.name}.tmp-",
                suffix=".tmp"
            )
            tmp_path = Path(tmp_path_str)

            with os.fdopen(fd, "w", encoding="utf-8") as out_f:
                out_f.writelines(lines)
                out_f.flush()
                os.fsync(out_f.fileno())

            # Preserve permissions if target exists
            if self.config_path.exists():
                try:
                    mode = stat.S_IMODE(self.config_path.stat().st_mode)
                    tmp_path.chmod(mode)
                except OSError:
                    pass

            os.replace(tmp_path, self.config_path)

            # Update stored mtime
            self.file_mtime = self.config_path.stat().st_mtime
            return True, f"Successfully updated {len(changes_dict)} template key(s).", ""

        except Exception as e:
            if 'tmp_path' in locals() and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            return False, f"Atomic write failed: {e}", ""
