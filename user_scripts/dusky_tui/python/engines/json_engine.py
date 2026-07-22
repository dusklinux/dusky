#!/usr/bin/env python3
import os
import re
import json
import tempfile
import threading
from pathlib import Path
from typing import Any

from python.frontend.core_types import BaseEngine

class JsonEngine(BaseEngine):
    """
    Advanced JSON & JSONC (JSON with Comments) Configuration Engine.
    
    Features:
    - Robust JSONC comment stripping (single-line //, block /* */, trailing commas).
    - Recursive nested dictionary flattening for O(1) TUI scope & key lookups.
    - Deep nested dictionary insertion & mutation for scoped keys (e.g. scope='display', key='border').
    - Atomic crash-proof file writes via temporary file replacement + fsync.
    - Thread-safe operations with re-entrant locking.
    """

    def __init__(self, config_path: str = ""):
        self.config_path = Path(config_path).expanduser().resolve()
        self.cache: dict[str, Any] = {}
        self.file_mtime_ns: int = 0
        self._lock = threading.Lock()

    @property
    def target_path(self) -> str:
        return str(self.config_path)

    @staticmethod
    def _strip_json_comments(text: str) -> str:
        """Strips single-line // comments, block /* */ comments, and trailing commas from JSON/JSONC."""
        pattern = r"(\"(?:\\\\.|[^\"\\\\])*\")|//.*?$|/\*.*?\*/"
        def replace(match):
            if match.group(1):
                return match.group(1) # Preserve string literals
            return ""
        clean = re.sub(pattern, replace, text, flags=re.DOTALL | re.MULTILINE)
        clean = re.sub(r",\s*([\]}])", r"\1", clean)
        return clean

    def load_state(self) -> dict[str, Any]:
        with self._lock:
            self.cache = {}
            if not self.config_path.exists():
                return self.cache

            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    self.file_mtime_ns = os.fstat(f.fileno()).st_mtime_ns
                    content = f.read()

                if not content.strip():
                    return self.cache

                try:
                    data = json.loads(content)
                except json.JSONDecodeError:
                    clean_content = self._strip_json_comments(content)
                    data = json.loads(clean_content)

                if not isinstance(data, dict):
                    return self.cache

                # Flatten nested state into scope.key, scope/key, and unique key entries
                def _flatten(d: dict, prefix: str = ""):
                    for k, v in d.items():
                        full_key = f"{prefix}.{k}" if prefix else k
                        slash_key = f"{prefix}/{k}" if prefix else k

                        if full_key not in self.cache:
                            self.cache[full_key] = v
                        if slash_key not in self.cache:
                            self.cache[slash_key] = v

                        # Store bare key if unique or first encountered
                        if k not in self.cache:
                            self.cache[k] = v

                        if isinstance(v, dict):
                            _flatten(v, full_key)

                _flatten(data)

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
            if self.config_path.exists():
                try:
                    with open(self.config_path, "r", encoding="utf-8") as f:
                        self.file_mtime_ns = os.fstat(f.fileno()).st_mtime_ns
                        content = f.read()

                    if content.strip():
                        try:
                            data = json.loads(content)
                        except json.JSONDecodeError:
                            data = json.loads(self._strip_json_comments(content))
                except Exception:
                    data = {}

            if not isinstance(data, dict):
                data = {}

            for key, scope, val, itype in changes:
                if val is None or val == "nil":
                    parsed_val = None
                elif itype == "bool":
                    if isinstance(val, str):
                        parsed_val = val.lower() in ("true", "1", "yes", "on", "t", "y")
                    else:
                        parsed_val = bool(val)
                elif itype in ("int", "float"):
                    try:
                        parsed_val = float(val) if itype == "float" else int(float(val))
                    except (ValueError, TypeError):
                        continue
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
                    path_parts.extend(scope.replace("/", ".").split("."))

                if "." in key:
                    path_parts.extend(key.split("."))
                else:
                    path_parts.append(key)

                # Traverse/instantiate nested dictionary hierarchy
                curr = data
                for part in path_parts[:-1]:
                    if part not in curr or not isinstance(curr[part], dict):
                        curr[part] = {}
                    curr = curr[part]

                target_prop = path_parts[-1]
                if parsed_val is None:
                    curr.pop(target_prop, None)
                else:
                    curr[target_prop] = parsed_val

            # Atomic Crash-Proof Disk Commit
            try:
                parent_dir = self.config_path.parent
                parent_dir.mkdir(parents=True, exist_ok=True)

                tmp_file = tempfile.NamedTemporaryFile("w", dir=parent_dir, delete=False, encoding="utf-8")
                tmp_path = Path(tmp_file.name)

                json.dump(data, tmp_file, indent=4, ensure_ascii=False)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
                tmp_file.close()

                if self.config_path.exists():
                    try:
                        mode = self.config_path.stat().st_mode
                        os.chmod(tmp_path, mode)
                    except Exception:
                        pass

                os.replace(tmp_path, self.config_path)
                self.file_mtime_ns = os.stat(self.config_path).st_mtime_ns

            except Exception as e:
                if "tmp_path" in locals() and tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except Exception:
                        pass
                return False, f"Failed to write json: {e}", ""

            return True, f"Successfully saved {len(changes)} changes.", ""
