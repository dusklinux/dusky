#!/usr/bin/env python3
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any

from python.frontend.core_types import BaseEngine

class IniConfigEngine(BaseEngine):
    """
    Production-grade AST-like engine for INI-style and Arch Linux configuration files 
    (e.g., pacman.conf, makepkg.conf).
    
    Provides strict atomicity, concurrency protection (mtime locks), and precise 
    preservation of structural comments and documentation keys.
    """
    
    # Matches a section header like [options]
    _RE_SECTION = re.compile(r"^\s*\[(.*?)\]\s*$")
    
    # Matches a key, intelligently separating it from comment prefixes and values
    # Group 1: Prefix (whitespace + optional # or ;), Group 2: Key, Group 3: Value/Tail
    _RE_KEY = re.compile(r"^([ \t]*[#;][ \t]*|[ \t]*)([a-zA-Z0-9_.-]+)(?:([ \t]*=.*)|[ \t]*)$")
    
    def __init__(self, config_path: str = "/etc/pacman.conf"):
        self.config_path = Path(config_path).expanduser().resolve()
        self.cache: dict[str, Any] = {}
        self.file_mtime: float = 0.0

    @property
    def target_path(self) -> str:
        return str(self.config_path)

    def load_state(self) -> dict[str, Any]:
        """Parses active, uncommented configurations into a flat state dictionary."""
        if not self.config_path.exists():
            return {}

        self.file_mtime = self.config_path.stat().st_mtime
        self.cache = {}
        current_scope = "DEFAULT"
        
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    
                    # 1. Scope/Section change
                    sec_match = self._RE_SECTION.match(line)
                    if sec_match:
                        current_scope = sec_match.group(1).strip()
                        continue
                        
                    # 2. Skip comments during load (we only want the live state)
                    if line.startswith('#') or line.startswith(';'):
                        continue
                        
                    # 3. Parse Keys and Valueless Flags
                    if '=' in line:
                        key, val = line.split('=', 1)
                        key = key.strip()
                        val = val.strip()
                        
                        # Strip standard UI string quotes if present
                        if val.startswith('"') and val.endswith('"') and len(val) >= 2:
                            val = val[1:-1]
                            
                        self.cache[f"{current_scope}/{key}"] = val
                    else:
                        # Valueless flags (like 'Color', 'ILoveCandy')
                        key = line.strip()
                        self.cache[f"{current_scope}/{key}"] = True
                        
        except (OSError, IOError) as e:
            print(f"Failed to read INI file {self.config_path}: {e}")
            
        return self.cache

    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = "string") -> tuple[bool, str, str]:
        """Proxy method. Routes single mutations through the high-speed batch architecture."""
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        """
        O(1) pass batched mutator with atomicity and exact singularity enforcement.
        """
        if not changes:
            return True, "No pending changes.", ""
            
        # Concurrency safety lock
        if self.config_path.exists():
            current_mtime = self.config_path.stat().st_mtime
            if current_mtime > self.file_mtime:
                return False, f"File {self.config_path.name} was modified externally. Reload required.", ""

        changes_dict = {(scope, key): val for key, scope, val, _ in changes}
        applied_commits = set()
        out_lines = []
        
        try:
            if self.config_path.exists():
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
            else:
                lines = []
        except OSError as e:
            return False, f"Failed to open config for reading: {e}", ""

        current_scope = "DEFAULT"
        
        # --- PASS 1: Inline Replacement & Singularity Enforcement ---
        for line in lines:
            sec_match = self._RE_SECTION.match(line)
            if sec_match:
                current_scope = sec_match.group(1).strip()
                out_lines.append(line)
                continue
                
            match = self._RE_KEY.match(line.rstrip('\n'))
            
            if match:
                prefix = match.group(1)
                key = match.group(2)
                
                lookup_key = (current_scope, key)
                if lookup_key in changes_dict:
                    new_val = changes_dict[lookup_key]
                    
                    # Strip UI Theme Variable wrappers if passed
                    if isinstance(new_val, str) and new_val.startswith("__VAR__"):
                        new_val = new_val[7:]

                    if lookup_key not in applied_commits:
                        # FIRST HIT: Mutate this line to become the single active state
                        applied_commits.add(lookup_key)
                        
                        if new_val in ("false", "nil", "__DELETE__"):
                            if not ('#' in prefix or ';' in prefix):
                                out_lines.append(f"#{line.lstrip()}") # Disable safely
                            else:
                                out_lines.append(line)                # Already disabled
                                
                        elif new_val == "true":
                            out_lines.append(f"{key}\n")              # Valueless flag enabled
                            
                        else:
                            out_lines.append(f"{key} = {new_val}\n")  # Key=Value enabled
                    else:
                        # SUBSEQUENT HITS: Mute duplicates to prevent overriding
                        if not ('#' in prefix or ';' in prefix):
                            out_lines.append(f"#{line.lstrip()}")
                        else:
                            out_lines.append(line)
                            
                    continue # Bypass appending the original unmodified line
                    
            out_lines.append(line)
            
        # --- PASS 2: Append Missing Keys ---
        missing_changes = [k for k in changes_dict if k not in applied_commits]
        if missing_changes:
            from collections import defaultdict
            missing_by_scope = defaultdict(list)
            for scope, key in missing_changes:
                missing_by_scope[scope].append(key)
                
            # Locate bottom of each scope
            scope_end_indices = {}
            active_scope = "DEFAULT"
            for i, line in enumerate(out_lines):
                if self._RE_SECTION.match(line):
                    scope_end_indices[active_scope] = i
                    active_scope = self._RE_SECTION.match(line).group(1).strip()
            scope_end_indices[active_scope] = len(out_lines)
            
            # Insert bottom-up to prevent array shifting
            for scope in sorted(missing_by_scope.keys(), key=lambda s: scope_end_indices.get(s, 0), reverse=True):
                insert_idx = scope_end_indices.get(scope, len(out_lines))
                
                # Create scope header if it doesn't exist
                if scope not in scope_end_indices and scope != "DEFAULT":
                    # Ensure preceding newline for clean formatting
                    if insert_idx > 0 and not out_lines[insert_idx - 1].endswith('\n\n'):
                        out_lines.append("\n")
                    out_lines.append(f"[{scope}]\n")
                    insert_idx = len(out_lines)
                    
                lines_to_insert = []
                for key in missing_by_scope[scope]:
                    val = changes_dict[(scope, key)]
                    if isinstance(val, str) and val.startswith("__VAR__"):
                        val = val[7:]
                        
                    if val in ("false", "nil", "__DELETE__"):
                        continue 
                    elif val == "true":
                        lines_to_insert.append(f"{key}\n")
                    else:
                        lines_to_insert.append(f"{key} = {val}\n")
                        
                if lines_to_insert:
                    out_lines = out_lines[:insert_idx] + lines_to_insert + out_lines[insert_idx:]
                    for key in missing_by_scope[scope]:
                        applied_commits.add((scope, key))

        # --- PASS 3: Safe Atomic File Commit ---
        success = False
        status_msg = "Failed"
        temp_file_path = None
        
        try:
            # 1. Write to isolated temporary file
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8', dir=self.config_path.parent) as tf:
                temp_file_path = Path(tf.name)
                tf.writelines(out_lines)
                
            # 2. Inherit permissions from original file (if it exists)
            if self.config_path.exists():
                try:
                    temp_file_path.chmod(stat.S_IMODE(self.config_path.stat().st_mode))
                except OSError:
                    pass
                    
            # 3. Atomic replacement
            os.replace(temp_file_path, self.config_path)
            self.file_mtime = self.config_path.stat().st_mtime
            success = True
            
        except OSError as e:
            status_msg = f"Atomic commit failed: {e}"
        finally:
            # Absolute cleanup guarantee
            if temp_file_path and temp_file_path.exists() and not success:
                try:
                    temp_file_path.unlink()
                except OSError:
                    pass

        if success:
            if len(applied_commits) == len(changes):
                return True, f"Successfully batched {len(changes)} INI commits.", ""
            else:
                return False, f"Partial success: saved {len(applied_commits)}/{len(changes)} INI items.", ""
                
        return False, status_msg, ""
