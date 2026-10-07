#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: TOML CONFIGURATION ENGINE
===============================================================================
Engine Type: "toml"
Target: Any standard TOML file (e.g. ~/.config/dusky/settings/dusky_keys/config.toml)
===============================================================================
"""

import os
import re
lazy import json
lazy import tempfile
lazy import threading
lazy import tomllib
lazy import datetime
lazy from pathlib import Path
lazy from typing import Any

from python.frontend.core_types import BaseEngine

_RE_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")

class TomlEngine(BaseEngine):
    """
    Production-grade, Crash-Proof TOML Configuration Engine for Dusky TUI.

    Features & Guarantees:
    - Scoped table traversal & caching (e.g., scope='display', key='buffer_size').
    - True recursive TOML table generation (fixes nested table layout corruption).
    - Anti-Clobber Protection: Refuses to overwrite files with existing syntax errors.
    - Datetime Preservation: Natively retains TOML date/time objects without mutation.
    - Dynamic Regex key-quoting to prevent invalid syntax on space-containing keys.
    - JSON-backed C-level string serialization for perfect control character escaping.
    - Atomic file commit via temporary file + fsync (TOCTOU hardened).
    - Python 3.10+ Structural Pattern Matching for O(1) type coercion.
    - Sudo/Pkexec safe: Enforces UID/GID inheritance on virgin file creation.
    """

    def __init__(self, config_path: str = ""):
        self.config_path = Path(config_path).expanduser().resolve()
        self.cache: dict[str, Any] = {}
        self.file_mtime_ns: int = 0
        self._lock = threading.Lock()

    @property
    def target_path(self) -> str:
        return str(self.config_path)

    def load_state(self) -> dict[str, Any]:
        with self._lock:
            self.cache.clear()
            if not self.config_path.exists():
                return self.cache

            try:
                # Lock timestamp precision immediately after securing the file descriptor
                with open(self.config_path, "rb") as f:
                    self.file_mtime_ns = os.fstat(f.fileno()).st_mtime_ns
                    data = tomllib.load(f)

                if not isinstance(data, dict):
                    return self.cache

                # Flatten nested TOML data into scope.key, scope/key, and bare key lookups
                def _flatten(d: dict[str, Any], prefix: str = "", slash_prefix: str = ""):
                    for k, v in d.items():
                        full_key = f"{prefix}.{k}" if prefix else k
                        slash_key = f"{slash_prefix}/{k}" if slash_prefix else k

                        # Cache all permutation formats for robust UI binding
                        if full_key not in self.cache:
                            self.cache[full_key] = v
                        if slash_key not in self.cache:
                            self.cache[slash_key] = v
                        if k not in self.cache:
                            self.cache[k] = v

                        if isinstance(v, dict):
                            _flatten(v, full_key, slash_key)

                _flatten(data)

            except (OSError, UnicodeError) as e:
                print(f"[TomlEngine] Disk I/O error reading ({self.config_path.name}): {e}")
            except tomllib.TOMLDecodeError as e:
                print(f"[TomlEngine] TOML syntax error in ({self.config_path.name}): {e}")

            return self.cache

    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = "string") -> tuple[bool, str, str]:
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        if not changes:
            return True, "No pending changes.", ""

        with self._lock:
            data: dict[str, Any] = {}
            try:
                with open(self.config_path, "rb") as f:
                    data = tomllib.load(f)
            except FileNotFoundError:
                pass
            except tomllib.TOMLDecodeError as e:
                return False, f"Refusing to write: Target file has a syntax error ({e}).", ""
            except (OSError, UnicodeError) as e:
                return False, f"Refusing to write: Cannot read target TOML ({e}).", ""

            if not isinstance(data, dict):
                data = {}

            for key, scope, val, itype in changes:
                # Absolute bleeding-edge: Structural Pattern Matching for fast type coercion
                match val:
                    case None | "nil" | "__DELETE__":
                        parsed_val = None
                    case _ if itype == "bool":
                        parsed_val = val.lower() in {"true", "1", "yes", "on", "t", "y"} if isinstance(val, str) else bool(val)
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
                    path_parts.extend(scope.replace("/", ".").split("."))

                path_parts.extend(key.split("."))

                # Traverse/instantiate nested TOML dictionary tables dynamically
                curr = data
                for part in path_parts[:-1]:
                    curr = curr.setdefault(part, {})
                    if not isinstance(curr, dict):
                        return False, f"Cannot write {scope}.{key}: {part!r} is not a TOML table.", ""

                target_prop = path_parts[-1]
                if parsed_val is None:
                    curr.pop(target_prop, None)
                else:
                    curr[target_prop] = parsed_val

            # Format and dump to strictly compliant TOML string
            formatted_toml = self._dump_toml(data)

            # Atomic Crash-Proof Disk Commit
            try:
                parent_dir = self.config_path.parent
                parent_dir.mkdir(parents=True, exist_ok=True)

                with tempfile.NamedTemporaryFile("w", dir=parent_dir, delete=False, encoding="utf-8") as tmp_file:
                    tmp_path = Path(tmp_file.name)
                    tmp_file.write(formatted_toml)
                    
                    # GOLDEN STANDARD: Force OS hardware buffer sync before allowing pointer swap
                    tmp_file.flush()
                    os.fsync(tmp_file.fileno())

                # Smart Permissions/Ownership Sync (Sudo/Pkexec safe)
                if self.config_path.exists():
                    try:
                        file_stat = self.config_path.stat()
                        os.chown(tmp_path, file_stat.st_uid, file_stat.st_gid)
                        tmp_path.chmod(file_stat.st_mode)
                    except OSError:
                        pass
                else:
                    # Absolute fallback: If root is creating the config file for the first time natively,
                    # forcefully inherit the UID/GID of the user's config directory to prevent permanent lockout.
                    try:
                        parent_stat = self.config_path.parent.stat()
                        os.chown(tmp_path, parent_stat.st_uid, parent_stat.st_gid)
                        tmp_path.chmod(0o644)
                    except OSError:
                        pass

                os.replace(tmp_path, self.config_path)
                
                # Refresh nanosecond precision internal state immediately
                self.file_mtime_ns = self.config_path.stat().st_mtime_ns

            except (OSError, UnicodeError) as e:
                if 'tmp_path' in locals() and tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)
                return False, f"Atomic commit failed: {e}", ""

            return True, f"Successfully saved {len(changes)} TOML changes.", ""

    @staticmethod
    def _quote_key(key: str) -> str:
        """
        Dynamically wraps keys in quotes if they contain spaces or special characters,
        as mandated by the TOML v1.0.0 specification for bare keys.
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
                # Exploit JSON's C-level serializer for perfect unicode and control-character escaping
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
