#!/usr/bin/env python3
import os
import re
import stat
import tempfile
import subprocess
from pathlib import Path
from typing import Any

from python.frontend.core_types import BaseEngine

class IniConfigEngine(BaseEngine):
    """
    Production-grade AST-like engine for INI-style and Arch Linux configuration files 
    (e.g., pacman.conf, makepkg.conf, mako/config).
    
    Provides strict atomicity, concurrency protection (mtime locks), precise 
    preservation of structural comments, and dynamic assignment operator detection.
    """
    
    # Matches a section header like [options] or [mode=do-not-disturb]
    _RE_SECTION = re.compile(r"^\s*\[(.*?)\]\s*$")
    
    # Matches a key, intelligently separating it from comment prefixes, assignment operators, and values
    # Group 1: Leading whitespace
    # Group 2: Comment char (# or ; or empty)
    # Group 3: Whitespace after comment char
    # Group 4: Key
    # Group 5: Assignment operator (e.g., ' = ', '=', or None)
    # Group 6: Value (or None)
    _RE_KEY = re.compile(r"^([ \t]*)([#;]?)([ \t]*)([a-zA-Z0-9_.-]+)(?:([ \t]*=[ \t]*)(.*)|[ \t]*)$")
    
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
                    sec_match = self._RE_SECTION.match(line)
                    if sec_match:
                        current_scope = sec_match.group(1).strip()
                        continue
                        
                    match = self._RE_KEY.match(line.rstrip('\n'))
                    if match:
                        ws1, cmt, ws2, key, assign_op, val = match.groups()
                        
                        # Only load active (uncommented) keys
                        if not cmt:
                            if assign_op is not None:
                                v = val.strip()
                                # Strip standard UI string quotes if present
                                if v.startswith('"') and v.endswith('"') and len(v) >= 2:
                                    v = v[1:-1]
                                self.cache[f"{current_scope}/{key}"] = v
                            else:
                                # Valueless flags (like 'Color', 'ILoveCandy')
                                self.cache[f"{current_scope}/{key}"] = True
                                
        except (OSError, IOError) as e:
            print(f"Failed to read config file {self.config_path}: {e}")
            
        return self.cache

    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = "string") -> tuple[bool, str, str]:
        """Proxy method. Routes single mutations through the high-speed batch architecture."""
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        """
        O(1) pass batched mutator with atomicity and exact singularity enforcement.
        Now featuring dynamic syntax heuristics for cross-daemon compatibility.
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
        
        # Heuristics: Analyze assignment styles to match the file's native format
        assign_op_counts = {}
        valueless_count = 0
        
        # --- PASS 1: Inline Replacement & Singularity Enforcement ---
        for line in lines:
            sec_match = self._RE_SECTION.match(line)
            if sec_match:
                current_scope = sec_match.group(1).strip()
                out_lines.append(line)
                continue
                
            match = self._RE_KEY.match(line.rstrip('\n'))
            if match:
                ws1, cmt, ws2, key, assign_op, old_val = match.groups()
                
                # Gather telemetry for appended keys
                if assign_op is not None:
                    assign_op_counts[assign_op] = assign_op_counts.get(assign_op, 0) + 1
                else:
                    if not cmt: # Only count active valueless flags
                        valueless_count += 1
                
                lookup_key = (current_scope, key)
                if lookup_key in changes_dict:
                    new_val = changes_dict[lookup_key]
                    
                    # Strip UI Theme Variable wrappers if passed
                    if isinstance(new_val, str) and new_val.startswith("__VAR__"):
                        new_val = new_val[7:]

                    if lookup_key not in applied_commits:
                        # FIRST HIT: Mutate this line to become the single active state
                        applied_commits.add(lookup_key)
                        
                        is_delete_signal = str(new_val) == "__DELETE__" or str(new_val) == "nil"
                        is_false_signal = str(new_val).lower() == "false"
                        
                        if is_delete_signal:
                            if cmt:
                                out_lines.append(line)                # Already disabled
                            else:
                                out_lines.append(f"{ws1}#{ws2}{key}{(assign_op or '')}{(old_val or '')}\n") # Disable safely
                        elif is_false_signal:
                            if assign_op is not None:
                                out_lines.append(f"{ws1}{key}{assign_op}{new_val}\n")
                            else:
                                # For valueless flags, false means disable (comment out)
                                if cmt:
                                    out_lines.append(line)
                                else:
                                    out_lines.append(f"{ws1}#{ws2}{key}\n")
                        else:
                            # Enable / Modify
                            if assign_op is not None:
                                out_lines.append(f"{ws1}{key}{assign_op}{new_val}\n")
                            else:
                                if str(new_val).lower() == "true":
                                    out_lines.append(f"{ws1}{key}\n")
                                else:
                                    dominant_op = max(assign_op_counts, key=assign_op_counts.get) if assign_op_counts else "="
                                    out_lines.append(f"{ws1}{key}{dominant_op}{new_val}\n")
                    else:
                        # SUBSEQUENT HITS: Mute duplicates to prevent overriding
                        if cmt:
                            out_lines.append(line)
                        else:
                            out_lines.append(f"{ws1}#{ws2}{key}{(assign_op or '')}{(old_val or '')}\n")
                            
                    continue # Bypass appending the original unmodified line
                    
            out_lines.append(line)
            
        # Determine dominant assignment operator for new keys
        dominant_assign_op = "="
        if assign_op_counts:
            dominant_assign_op = max(assign_op_counts, key=assign_op_counts.get)

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
                    if insert_idx > 0 and not out_lines[insert_idx - 1].endswith('\n\n') and out_lines[insert_idx - 1] != '\n':
                        out_lines.insert(insert_idx, "\n")
                        insert_idx += 1
                    out_lines.insert(insert_idx, f"[{scope}]\n")
                    insert_idx += 1
                    
                lines_to_insert = []
                for key in missing_by_scope[scope]:
                    val = changes_dict[(scope, key)]
                    if isinstance(val, str) and val.startswith("__VAR__"):
                        val = val[7:]
                        
                    is_delete_signal = str(val) == "__DELETE__" or str(val) == "nil"
                    is_false_signal = str(val).lower() == "false"
                    
                    if is_delete_signal:
                        continue 
                    elif is_false_signal:
                        if valueless_count > 0:
                            pass # We don't append a valueless flag if it's explicitly set to false
                        else:
                            lines_to_insert.append(f"{key}{dominant_assign_op}{val}\n")
                    elif str(val).lower() == "true":
                        # Smart Valueless vs Assignment resolution
                        if valueless_count > 0:
                            lines_to_insert.append(f"{key}\n")
                        else:
                            lines_to_insert.append(f"{key}{dominant_assign_op}true\n")
                    else:
                        lines_to_insert.append(f"{key}{dominant_assign_op}{val}\n")
                        
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
            # Smart Reload Heuristics for Arch Linux Daemons
            filename = self.config_path.name.lower()
            try:
                if filename == "config" and "mako" in str(self.config_path.parent).lower():
                    subprocess.run(["makoctl", "reload"], check=False, capture_output=True)
            except Exception:
                pass

            if len(applied_commits) == len(changes):
                return True, f"Successfully batched {len(changes)} INI commits.", ""
            else:
                return False, f"Partial success: saved {len(applied_commits)}/{len(changes)} INI items.", ""
                
        return False, status_msg, ""
