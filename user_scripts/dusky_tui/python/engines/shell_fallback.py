#!/usr/bin/env python3
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from python.frontend.core_types import BaseEngine

class ShellFallbackEngine(BaseEngine):
    """
    Engine for bash/shell variable fallback definitions.
    Specifically parses lines like:
    readonly KEY="${KEY:-DEFAULT_VAL}"
    
    Guarantees:
    - Inline replacement of fallback value.
    - Sudo/Pkexec safe permissions inheritance.
    """
    
    # Matches: readonly KEY="${KEY:-VALUE}"
    _RE_FALLBACK = re.compile(r"^([ \t]*)readonly[ \t]+([a-zA-Z0-9_]+)=\"\$\{\2:-([^}]*)\}\"(.*)$")
    
    def __init__(self, config_path: str):
        self.config_path = Path(config_path).expanduser().resolve()
        self.cache: dict[str, Any] = {}
        self.file_mtime_ns: int = 0

    @property
    def target_path(self) -> str:
        return str(self.config_path)

    def load_state(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {}

        self.cache = {}
        
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self.file_mtime_ns = os.fstat(f.fileno()).st_mtime_ns
                for line in f:
                    clean_line = line.rstrip('\r\n')
                    match = self._RE_FALLBACK.match(clean_line)
                    if match:
                        _, key, val, _ = match.groups()
                        self.cache[f"DEFAULT/{key}"] = val
                        
        except OSError as e:
            print(f"Failed to read file {self.config_path}: {e}")
            
        return self.cache

    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = "string") -> tuple[bool, str, str]:
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        if not changes:
            return True, "No pending changes.", ""

        changes_dict = {key: val for key, scope, val, itype in changes}
        out_lines = []
        applied_commits = set()
        
        try:
            if self.config_path.exists():
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    if os.fstat(f.fileno()).st_mtime_ns > self.file_mtime_ns:
                        return False, f"File {self.config_path.name} was modified externally. Reload required.", ""
                    lines = f.readlines()
            else:
                lines = []
        except OSError as e:
            return False, f"Failed to open config for reading: {e}", ""

        for line in lines:
            clean_line = line.rstrip('\r\n')
            match = self._RE_FALLBACK.match(clean_line)
            if match:
                ws, key, old_val, comment = match.groups()
                if key in changes_dict:
                    new_val = changes_dict[key]
                    # Ensure bool values write as true/false
                    if old_val in ("true", "false"):
                        new_val = "true" if str(new_val).lower() in ("true", "1", "yes", "on", "t", "y") else "false"
                    out_lines.append(f"{ws}readonly {key}=\"${{{key}:-{new_val}}}\"{comment}\n")
                    applied_commits.add(key)
                else:
                    out_lines.append(line)
            else:
                out_lines.append(line)

        # Atomic commit
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8', dir=self.config_path.parent) as tf:
                temp_file_path = Path(tf.name)
                tf.writelines(out_lines)
                tf.flush()
                os.fsync(tf.fileno())
                
            # Keep original permissions/ownership
            if self.config_path.exists():
                orig_stat = self.config_path.stat()
                temp_file_path.chmod(orig_stat.st_mode)
                os.chown(temp_file_path, orig_stat.st_uid, orig_stat.st_gid)
                
            shutil.move(str(temp_file_path), str(self.config_path))
            
            # Reload metadata timestamp
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self.file_mtime_ns = os.fstat(f.fileno()).st_mtime_ns
                
            return True, "Successfully updated configurations.", ""
        except Exception as e:
            if temp_file_path and temp_file_path.exists():
                temp_file_path.unlink()
            return False, f"Failed during atomic commit: {e}", ""
