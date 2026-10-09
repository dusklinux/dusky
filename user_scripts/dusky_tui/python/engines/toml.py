#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: TOML CONFIGURATION ENGINE
===============================================================================
Engine Type: "toml"
Target: Any standard TOML file (e.g. ~/.config/dusky/settings/dusky_keys/config.toml)
===============================================================================
"""

import re
lazy import json
lazy import threading
lazy import tomllib
lazy import datetime
lazy from pathlib import Path
lazy from typing import Any

from python.frontend.core_types import BaseEngine
from python.config_io import atomic_write, boolean, read_text

_RE_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")

class TomlEngine(BaseEngine):
    """Nested TOML state and atomic writes; regeneration discards comments."""

    def __init__(self, config_path: str = ""):
        self.config_path = Path(config_path).expanduser().resolve()
        self.cache: dict[str, Any] = {}
        self._snapshot = None
        self._loaded = False
        self._lock = threading.RLock()

    @property
    def target_path(self) -> str:
        return str(self.config_path)

    @staticmethod
    def _state(data: dict[str, Any]) -> dict[str, Any]:
        cache = {}
        def flatten(mapping, dotted='', slash=''):
            for key, value in mapping.items():
                full = f'{dotted}.{key}' if dotted else key
                qualified = f'{slash}/{key}' if slash else key
                for alias in (full, qualified, key):
                    cache.setdefault(alias, value)
                if isinstance(value, dict):
                    flatten(value, full, qualified)
        flatten(data)
        return cache

    def load_state(self) -> dict[str, Any]:
        with self._lock:
            self.cache = {}
            self._loaded = False
            try:
                text, self._snapshot = read_text(self.config_path)
                self.cache = self._state(tomllib.loads(text))
                self._loaded = True
            except (OSError, UnicodeError, ValueError) as exc:
                print(f'TomlEngine: Failed to read {self.config_path}: {exc}')
            return self.cache

    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = "string") -> tuple[bool, str, str]:
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        if not changes:
            return True, "No pending changes.", ""

        with self._lock:
            try:
                text, snapshot = read_text(self.config_path)
                if self._loaded and snapshot != self._snapshot:
                    return False, f'File {self.config_path.name} was modified externally. Reload required.', ''
                data = tomllib.loads(text)
            except (OSError, UnicodeError, ValueError) as exc:
                return False, f'Refusing to write: Cannot read target TOML ({exc}).', ''

            for key, scope, val, itype in changes:
                # Coerce UI values before changing the in-memory document.
                match val:
                    case None | "nil" | "__DELETE__":
                        parsed_val = None
                    case _ if itype == "bool":
                        parsed_val = boolean(val)
                    case _ if itype in {"int", "float"}:
                        try:
                            if itype == "float":
                                parsed_val = float(val)
                            else:
                                try:
                                    parsed_val = int(val)
                                except (ValueError, TypeError):
                                    parsed_val = int(float(val))
                        except (ValueError, TypeError, OverflowError) as exc:
                            return False, f"Invalid {itype} value for {scope}.{key}: {exc}", ""
                    case _:
                        parsed_val = str(val)

                # Determine table path dynamically
                path_parts = []
                if scope and scope != "DEFAULT":
                    path_parts.extend((scope.split("/") if "/" in scope else scope.split(".")))

                path_parts.extend((key.split('/') if '/' in key else key.split('.')))
                if any(not part for part in path_parts):
                    return False, f'Invalid TOML path: {scope}/{key}', ''

                # Traverse/instantiate nested TOML dictionary tables dynamically
                curr = data
                for part in path_parts[:-1]:
                    if parsed_val is None and part not in curr:
                        curr = None
                        break
                    curr = curr.setdefault(part, {})
                    if not isinstance(curr, dict):
                        return False, f"Cannot write {scope}.{key}: {part!r} is not a TOML table.", ""

                if curr is None:
                    continue
                target_prop = path_parts[-1]
                if parsed_val is None:
                    curr.pop(target_prop, None)
                else:
                    curr[target_prop] = parsed_val

            try:
                formatted_toml = self._dump_toml(data)
                tomllib.loads(formatted_toml)
                self._snapshot = atomic_write(self.config_path, formatted_toml, snapshot) if formatted_toml != text else snapshot
                self._loaded = True
                self.cache = self._state(data)
            except (OSError, UnicodeError, ValueError) as exc:
                return False, f'TOML commit failed: {exc}', ''
            return True, f'Successfully saved {len(changes)} TOML changes.', ''

    @staticmethod
    def _quote_key(key: str) -> str:
        """
        Dynamically wraps keys in quotes if they contain spaces or special characters,
        as required by TOML for bare keys.
        """
        if not _RE_BARE_KEY.fullmatch(key):
            return json.dumps(key, ensure_ascii=False).replace("\x7f", "\\u007f")
        return key

    @staticmethod
    def _dump_toml(data: dict[str, Any], parent_keys: list[str] | None = None) -> str:
        """
        Recursively serializes dictionary to fully spec-compliant TOML format, 
        correctly separating scalar values from deeply nested tables to prevent layout corruption.
        """
        parent_keys = parent_keys or []
        lines = []
        scalars = {}
        tables = {}

        # Segregate scalars from nested structures to ensure valid TOML layout
        for k, v in data.items():
            if isinstance(v, dict):
                tables[k] = v
            else:
                scalars[k] = v

        # Print table header if we are inside a nested scope
        if parent_keys:
            header = ".".join(TomlEngine._quote_key(k) for k in parent_keys)
            # Only emit header when the table actually holds scalars; pure
            # intermediate tables (only subtables, no direct keys) are
            # implied by their children (e.g. [runtime.wine] implies
            # [runtime]) and an empty [runtime] header would be redundant.
            if scalars or not tables:
                lines.append(f"[{header}]")

        # Print inline key-value pairs
        for k, v in scalars.items():
            lines.append(f"{TomlEngine._quote_key(k)} = {TomlEngine._format_val(v)}")

        if scalars:
            lines.append("")

        # Recurse strictly into nested tables
        for k, v in tables.items():
            nested_block = TomlEngine._dump_toml(v, parent_keys + [k])
            if nested_block.strip():
                lines.append(nested_block)

        # A final strip cleans trailing padding without stripping necessary TOML spacing
        return "\n".join(lines).strip() + "\n"

    @staticmethod
    def _format_val(v: Any) -> str:
        """
        Translates raw Python data types into strictly valid TOML syntax representations.
        """
        match v:
            case bool():
                return "true" if v else "false"
            case int() | float():
                return str(v)
            case datetime.datetime() | datetime.date() | datetime.time():
                # Prevent silent data mutation: Output raw TOML iso-formats, not JSON strings
                return v.isoformat()
            case str():
                # JSON escapes also form valid TOML basic strings, except literal DEL.
                return json.dumps(v, ensure_ascii=False).replace("\x7f", "\\u007f")
            case list() | tuple():
                items = [TomlEngine._format_val(x) for x in v]
                return f"[{', '.join(items)}]"
            case dict():
                # Support inline table formatting `{a = 1, b = 2}` inside arrays
                items = [f"{TomlEngine._quote_key(k)} = {TomlEngine._format_val(val)}" for k, val in v.items()]
                return f"{{{', '.join(items)}}}"
            case _:
                return json.dumps(str(v), ensure_ascii=False).replace("\x7f", "\\u007f")
