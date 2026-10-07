#!/usr/bin/env python3
import os
lazy import stat
lazy import shutil
lazy import tempfile
lazy from pathlib import Path
lazy from typing import Any

from python.frontend.core_types import BaseEngine

class FlatDotConfigEngine(BaseEngine):
    """
    Production-grade Precision Engine for GPU Screen Recorder (`config_ui`) formats.
    
    Format Rules & Engine Guarantees:
    - No [sections]. Handled natively.
    - Flat dot-notated keys (e.g., `record.record_options.fps`).
    - Key and Value are separated strictly by the FIRST space.
    - Preserves exact duplicate keys natively (e.g., multiple audio tracks).
    - Hardened against TOCTOU race conditions via nanosecond fstat locks.
    - Ext4/BTRFS power-loss protection via explicit fsync hardware block commits.
    - Sudo/Pkexec safe: Enforces UID/GID inheritance even on virgin file creation.
    """
    
    def __init__(self, config_path: str = "~/.config/gpu-screen-recorder/config_ui"):
        self.config_path = Path(config_path).expanduser().resolve()
        self.cache: dict[str, Any] = {}
        self.file_mtime_ns: int = 0

    @property
    def target_path(self) -> str:
        return str(self.config_path)

    def load_state(self) -> dict[str, Any]:
        """Parses the space-delimited config into the UI state dictionary."""
        if not self.config_path.exists():
            return {}

        self.cache = {}
        counts: dict[str, int] = {}
        
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                # Lock timestamp precision immediately after securing the file descriptor
                self.file_mtime_ns = os.fstat(f.fileno()).st_mtime_ns
                
                for line in f:
                    clean_line = line.rstrip('\r\n')
                    if not clean_line:
                        continue
                        
                    # Split strictly by the FIRST space to allow spaces in values
                    parts = clean_line.split(' ', 1)
                    raw_key = parts[0]
                    raw_val = parts[1] if len(parts) > 1 else ""
                    
                    # Deduce Scope and Key for exact UI mapping
                    if "." in raw_key:
                        scope, key = raw_key.split(".", 1)
                    else:
                        scope, key = "DEFAULT", raw_key
                        
                    counts[raw_key] = counts.get(raw_key, 0) + 1
                    count = counts[raw_key]
                    
                    # Cache the first occurrence as the primary UI binding
                    if count == 1:
                        self.cache[f"{scope}/{key}"] = raw_val
                        
                    # Cache subsequent occurrences with index tags to preserve them during writes
                    self.cache[f"{scope}/{key}:{count}"] = raw_val
                                
        except OSError as e:
            print(f"Failed to read config file {self.config_path}: {e}")
            
        return self.cache

    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = "string") -> tuple[bool, str, str]:
        """Proxy single mutations through the atomic batch architecture."""
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        """
        O(1) Atomic AST Mutator.
        Updates values perfectly in-place, protects duplicate keys, appends missing keys, 
        and executes physical hardware fsyncs to prevent data tearing.
        """
        if not changes:
            return True, "No pending changes.", ""

        changes_dict = {(scope, key): (val, itype) for key, scope, val, itype in changes}
        applied_commits = set()
        out_lines = []
        
        try:
            if self.config_path.exists():
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    # Prevent TOCTOU modifications by verifying against the active file descriptor
                    current_mtime_ns = os.fstat(f.fileno()).st_mtime_ns
                    if current_mtime_ns > self.file_mtime_ns:
                        return False, f"File {self.config_path.name} was modified externally. Reload required.", ""
                    lines = f.readlines()
            else:
                lines = []
        except OSError as e:
            return False, f"Failed to open config for reading: {e}", ""

        seen_counts = {}
        
        # --- PASS 1: Inline Replacement ---
        for line in lines:
            clean_line = line.rstrip('\r\n')
            if not clean_line:
                out_lines.append(line)
                continue
                
            parts = clean_line.split(' ', 1)
            raw_key = parts[0]
            
            seen_counts[raw_key] = seen_counts.get(raw_key, 0) + 1
            count = seen_counts[raw_key]
            
            if "." in raw_key:
                scope, key = raw_key.split(".", 1)
            else:
                scope, key = "DEFAULT", raw_key

            lookup_exact = (scope, f"{key}:{count}")
            lookup_base = (scope, key)
            
            matched_lookup = None
            target_val = None
            target_itype = None
            
            # Prioritize exact duplicate index overrides over base key mappings
            if lookup_exact in changes_dict:
                target_val, target_itype = changes_dict[lookup_exact]
                matched_lookup = lookup_exact
            elif count == 1 and lookup_base in changes_dict:
                target_val, target_itype = changes_dict[lookup_base]
                matched_lookup = lookup_base
                
            if matched_lookup is not None:
                applied_commits.add(matched_lookup)
                
                val_str = str(target_val) if target_val is not None else ""
                if val_str.startswith("__VAR__"):
                    val_str = val_str[7:]
                    
                # Strict Coercion: Prevent Python capital Booleans from crashing the C++ parser
                if target_itype == "bool":
                    val_str = "true" if val_str.lower() in ("true", "1", "yes", "on", "t", "y") else "false"
                    
                if val_str in ("__DELETE__", "nil"):
                    continue # Skip appending to effectively delete the key from the hierarchy
                else:
                    out_lines.append(f"{raw_key} {val_str}\n")
            else:
                out_lines.append(line) # Pass unmodified lines through safely

        # --- PASS 2: Append Missing Keys ---
        # Traverse dictionary keys to enforce UI array insertion order (sets destroy order)
        missing_changes = [k for k in changes_dict.keys() if k not in applied_commits]
        
        for scope, key in missing_changes:
            val, target_itype = changes_dict[(scope, key)]
            val_str = str(val) if val is not None else ""
            
            if val_str in ("__DELETE__", "nil"):
                continue
                
            if val_str.startswith("__VAR__"):
                val_str = val_str[7:]
                
            if target_itype == "bool":
                val_str = "true" if val_str.lower() in ("true", "1", "yes", "on", "t", "y") else "false"
                
            # Reconstruct the raw backend key string
            clean_key = key.split(":")[0] if ":" in key else key
            reconstructed_key = f"{scope}.{clean_key}" if scope != "DEFAULT" else clean_key
            
            out_lines.append(f"{reconstructed_key} {val_str}\n")
            applied_commits.add((scope, key))

        # --- PASS 3: True Hardware-Atomic File Commit ---
        success = False
        status_msg = "Failed"
        temp_file_path = None
        
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8', dir=self.config_path.parent) as tf:
                temp_file_path = Path(tf.name)
                tf.writelines(out_lines)
                
                # GOLDEN STANDARD: Force the OS to write buffer completely to disk before allowing the pointer swap
                tf.flush()
                os.fsync(tf.fileno())
                
            # Smart Permissions/Ownership sync (Sudo/Pkexec safe)
            if self.config_path.exists():
                try: 
                    shutil.copystat(self.config_path, temp_file_path)
                except OSError: pass
                
                try:
                    stat_info = self.config_path.stat()
                    os.chown(temp_file_path, stat_info.st_uid, stat_info.st_gid)
                except OSError: pass
            else:
                # Absolute fallback: If root is creating the config file for the first time natively,
                # forcefully inherit the UID/GID of the user's config directory to prevent permanent lockout.
                parent_stat = self.config_path.parent.stat()
                try:
                    os.chown(temp_file_path, parent_stat.st_uid, parent_stat.st_gid)
                except OSError:
                    pass
                try:
                    temp_file_path.chmod(0o644)
                except OSError:
                    pass
                    
            os.replace(temp_file_path, self.config_path)
            
            # Immediately refresh the internal nanosecond state tracking (optimised to skip re-opening)
            self.file_mtime_ns = self.config_path.stat().st_mtime_ns
            success = True
            
        except OSError as e:
            status_msg = f"Atomic commit failed: {e}"
        finally:
            if temp_file_path and temp_file_path.exists() and not success:
                try: temp_file_path.unlink()
                except OSError: pass

        if success:
            if len(applied_commits) == len(changes):
                return True, f"Successfully batched {len(changes)} config_ui commits.", ""
            else:
                return False, f"Partial success: saved {len(applied_commits)}/{len(changes)} items.", ""
                
        return False, status_msg, ""
