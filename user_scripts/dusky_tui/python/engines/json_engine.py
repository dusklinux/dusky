#!/usr/bin/env python3
import re
lazy import json
lazy import threading
lazy from pathlib import Path
lazy from typing import Any

from python.frontend.core_types import BaseEngine
from python.shared.config_io import atomic_write, read_text

_RE_JSONC_COMMENTS = re.compile(r'("(?:\\.|[^"\\])*")|//[^\r\n]*|/\*.*?\*/', re.DOTALL)
_RE_JSONC_TRAILING_COMMAS = re.compile(r'("(?:\\.|[^"\\])*")|,\s*(?=[}\]])')

class JsonEngine(BaseEngine):
    """
    Advanced JSON & JSONC (JSON with Comments) Configuration Engine.
    
    Features:
    - Robust JSONC comment stripping (single-line //, block /* */, trailing commas).
    - Recursive nested dictionary flattening for O(1) TUI scope & key lookups.
    - Deep nested dictionary insertion & mutation for scoped keys (e.g. scope='display', key='border').
    - Atomic crash-proof file writes via temporary file replacement + fsync.
    - Thread-safe loading and writes.
    """

    def __init__(self, config_path: str = ""):
        self.config_path = Path(config_path).expanduser().resolve()
        self.cache: dict[str, Any] = {}
        self.file_mtime_ns: int = 0
        self._lock = threading.Lock()
        self._snapshot = None
        self._loaded = False

    @property
    def target_path(self) -> str:
        return str(self.config_path)

    @staticmethod
    def _strip_json_comments(text: str) -> str:
        """Strips single-line // comments, block /* */ comments, and trailing commas from JSON/JSONC."""
        # Match strings before comments or commas so their contents stay intact.
        # Whitespace prevents a comment from joining otherwise invalid tokens.
        clean = _RE_JSONC_COMMENTS.sub(lambda match: match.group(1) or " ", text)
        return _RE_JSONC_TRAILING_COMMAS.sub(lambda match: match.group(1) or "", clean)

    @staticmethod
    def _state(data):
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
                content, self._snapshot = read_text(self.config_path)
                self._loaded = True
                self.file_mtime_ns = self._snapshot[3] if self._snapshot else 0

                if not content.strip():
                    return self.cache

                try:
                    data = json.loads(content)
                except json.JSONDecodeError:
                    clean_content = self._strip_json_comments(content)
                    data = json.loads(clean_content)

                if not isinstance(data, dict):
                    return self.cache

                self.cache = self._state(data)

            except Exception as e:
                print(f"[JsonEngine] Failed to read JSON/JSONC config ({self.config_path.name}): {e}")

            return self.cache

    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = "string") -> tuple[bool, str, str]:
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        if not changes:
            return True, "No pending changes.", ""

        with self._lock:
            data = {}
            try:
                content, snapshot = read_text(self.config_path)
                if self._loaded and snapshot != self._snapshot:
                    return False, 'Configuration modified externally. Reload required.', ''
                if content.strip():
                    try:
                        data = json.loads(content)
                    except json.JSONDecodeError:
                        data = json.loads(self._strip_json_comments(content))
            except FileNotFoundError:
                pass
            except (OSError, ValueError) as exc:
                return False, f"Refusing to write: Cannot read target JSON/JSONC ({exc}).", ""

            if not isinstance(data, dict):
                return False, "Refusing to write: Target JSON must contain an object.", ""

            for key, scope, val, itype in changes:
                if val is None or val in ("nil", "__DELETE__"):
                    parsed_val = None
                elif itype == "bool":
                    if isinstance(val, str):
                        parsed_val = val.lower() in ("true", "1", "yes", "on", "t", "y")
                    else:
                        parsed_val = bool(val)
                elif itype in ("int", "float"):
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
                elif isinstance(val, str) and ((val.startswith("[") and val.endswith("]")) or (val.startswith("{") and val.endswith("}"))):
                    try:
                        parsed_val = json.loads(val)
                    except Exception:
                        parsed_val = val
                else:
                    parsed_val = val

                # Determine nested dict path
                path_parts = []
                if scope and scope != "DEFAULT":
                    path_parts.extend((scope.split("/") if "/" in scope else scope.split(".")))

                path_parts.extend(key.split("/") if "/" in key else key.split("."))

                if any(not part for part in path_parts):
                    return False, f"Invalid JSON path: {scope}/{key}", ""

                # Traverse/instantiate nested dictionary hierarchy
                curr = data
                for part in path_parts[:-1]:
                    if parsed_val is None and part not in curr:
                        curr = None
                        break
                    curr = curr.setdefault(part, {})
                    if not isinstance(curr, dict):
                        return False, f"Cannot write {scope}.{key}: {part!r} is not a JSON object.", ""

                if curr is None:
                    continue
                target_prop = path_parts[-1]
                if parsed_val is None:
                    curr.pop(target_prop, None)
                else:
                    curr[target_prop] = parsed_val

            try:
                formatted = json.dumps(data, indent=4, ensure_ascii=False, allow_nan=False) + '\n'
                self._snapshot = atomic_write(self.config_path, formatted, snapshot) if formatted != content else snapshot
                self._loaded = True
                self.file_mtime_ns = self._snapshot[3] if self._snapshot else 0
                self.cache = self._state(data)
            except (OSError, UnicodeError, ValueError, TypeError) as exc:
                return False, f'Failed to write JSON: {exc}', ''
            return True, f'Successfully saved {len(changes)} changes.', ''
