#!/usr/bin/env python3
import os
import json
from pathlib import Path
from typing import Any

from python.frontend.core_types import BaseEngine

class JsonEngine(BaseEngine):
    def __init__(self, config_path: str = ""):
        self.config_path = Path(config_path).expanduser().resolve()
        self.cache: dict[str, Any] = {}
        self.file_mtime_ns: int = 0

    @property
    def target_path(self) -> str:
        return str(self.config_path)

    def load_state(self) -> dict[str, Any]:
        self.cache = {}
        if not self.config_path.exists():
            return self.cache

        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self.file_mtime_ns = os.fstat(f.fileno()).st_mtime_ns
                data = json.load(f)
                for k, v in data.items():
                    # Store as bare key for DEFAULT scope — matches _lookup_state expectation
                    self.cache[k] = v
        except Exception as e:
            print(f"Failed to read json config: {e}")

        return self.cache

    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = "string") -> tuple[bool, str, str]:
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        if not changes:
            return True, "No pending changes.", ""

        data = {}
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    current_mtime_ns = os.fstat(f.fileno()).st_mtime_ns
                    if current_mtime_ns > self.file_mtime_ns:
                        self.file_mtime_ns = current_mtime_ns
                    data = json.load(f)
            except Exception:
                data = {}

        for key, scope, val, itype in changes:
            # type conversion
            if val is None: continue
            if itype == "bool":
                if isinstance(val, str):
                    val = val.lower() in ("true", "1", "yes", "on")
                else:
                    val = bool(val)
            elif itype == "int":
                try: val = int(val)
                except: continue
            elif itype == "float":
                try: val = float(val)
                except: continue

            data[key] = val

        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4)
            # Update mtime so the next write won't see our own write as an external modification
            self.file_mtime_ns = os.stat(self.config_path).st_mtime_ns
        except Exception as e:
            return False, f"Failed to write json: {e}", ""

        return True, f"Successfully saved {len(changes)} changes.", ""
