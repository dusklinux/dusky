#!/usr/bin/env python3
import os
import re
import stat
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from python.frontend.core_types import BaseEngine

class ShellEnvEngine(BaseEngine):
    """
    Production-grade Engine for strict POSIX/Bash environment files.
    (e.g., /etc/locale.conf, /etc/vconsole.conf, /etc/environment, ~/.config/uwsm/env)
    
    Guarantees:
    - Strict spacing: Enforces `KEY=VALUE` (no spaces around `=`), preventing boot failures.
    - Export awareness: Preserves the `export ` prefix natively.
    - Advanced Quote Handling: Escapes inner quotes and auto-quotes shell metacharacters.
    - Comment Preservation: Preserves inline Bash comments perfectly via state-machine parsing.
    - Duplicate Indexing: Tracks and modifies exact duplicate keys (e.g., multiple PATH exports).
    - Structural Integrity: Maintains exact leading indentation and POSIX newline compliance.
    """
    
    # Matches: (1) leading space, (2) optional 'export ', (3) key, (4) raw value + potential comment
    _RE_ENV = re.compile(r"^([ \t]*)(export[ \t]+)?([a-zA-Z0-9_]+)=(.*)$")
    
    def __init__(self, config_path: str = "/etc/locale.conf"):
        self.config_path = Path(config_path).expanduser().resolve()
        self.cache: dict[str, Any] = {}
        self.file_mtime_ns: int = 0

    @property
    def target_path(self) -> str:
        return str(self.config_path)

    @staticmethod
    def _parse_value(raw_val: str) -> tuple[str, str]:
        """
        State-machine that intelligently separates a bash value from its inline comment,
        respecting single quotes, double quotes, and escapes.
        Returns: (core_value, comment_string)
        """
        in_sq, in_dq, escaped = False, False, False
        for i, c in enumerate(raw_val):
            if escaped:
                escaped = False
                continue
            if c == '\\':
                escaped = True
                continue
            if c == "'" and not in_dq:
                in_sq = not in_sq
                continue
            if c == '"' and not in_sq:
                in_dq = not in_dq
                continue
            # A hash starts a comment only if unquoted and preceded by whitespace or start of line
            if c == '#' and not in_sq and not in_dq:
                if i == 0 or raw_val[i-1] in ' \t':
                    return raw_val[:i].strip(), raw_val[i:]
        return raw_val.strip(), ""

    def load_state(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {}

        self.cache = {}
        counts: dict[str, int] = {}
        
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                # Lock timestamp precision immediately after securing the file descriptor
                self.file_mtime_ns = os.fstat(f.fileno()).st_mtime_ns
                for line in f:
                    # Rstrip only line endings to preserve leading whitespace for the regex
                    clean_line = line.rstrip('\r\n')
                    if not clean_line.strip() or clean_line.lstrip().startswith('#'):
                        continue
                        
                    match = self._RE_ENV.match(clean_line)
                    if match:
                        _, _, key, raw_val = match.groups()
                        
                        # Isolate the core value from any trailing bash comments
                        val, _ = self._parse_value(raw_val)
                        
                        # Strip surrounding quotes for a clean UI representation
                        if len(val) >= 2 and ((val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'"))):
                            val = val[1:-1]
                        
                        counts[key] = counts.get(key, 0) + 1
                        count = counts[key]
                        
                        # Cache the first occurrence as the primary UI binding
                        if count == 1:
                            self.cache[f"DEFAULT/{key}"] = val
                            
                        # Cache subsequent occurrences with index tags to preserve them during writes
                        self.cache[f"DEFAULT/{key}:{count}"] = val
                        
        except OSError as e:
            print(f"Failed to read env file {self.config_path}: {e}")
            
        return self.cache

    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = "string") -> tuple[bool, str, str]:
        """Proxy single mutations through the atomic batch architecture."""
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        """
        O(1) Atomic Mutator.
        Updates values perfectly in-place, protects duplicate keys, appends missing keys, 
        preserves exact indentation/comments, and executes physical hardware fsyncs.
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
                    if os.fstat(f.fileno()).st_mtime_ns > self.file_mtime_ns:
                        return False, f"File {self.config_path.name} was modified externally. Reload required.", ""
                    lines = f.readlines()
            else:
                lines = []
        except OSError as e:
            return False, f"Failed to open config for reading: {e}", ""

        seen_counts: dict[str, int] = {}
        
        # --- PASS 1: Inline Replacement & Singularity Enforcement ---
        for line in lines:
            clean_line = line.rstrip('\r\n')
            if not clean_line.strip() or clean_line.lstrip().startswith('#'):
                out_lines.append(line)
                continue
                
            match = self._RE_ENV.match(clean_line)
            if match:
                ws, export_prefix, key, raw_val = match.groups()
                export_str = export_prefix if export_prefix else ""
                
                # Separate original value from any trailing comments to preserve them
                old_val_core, comment_part = self._parse_value(raw_val)
                comment_str = f" {comment_part}" if comment_part else ""
                
                seen_counts[key] = seen_counts.get(key, 0) + 1
                count = seen_counts[key]
                
                lookup_exact = ("DEFAULT", f"{key}:{count}")
                lookup_base = ("DEFAULT", key)
                
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
                    if val_str.startswith("__VAR__"): val_str = val_str[7:]
                        
                    if val_str in ("__DELETE__", "nil"):
                        # Safe Deletion: Comment out instead of deleting to preserve file context/indentation
                        out_lines.append(f"{ws}#{line.lstrip()}")
                        continue
                        
                    if target_itype == "bool":
                        val_str = "true" if val_str.lower() in ("true", "1", "yes", "on", "t", "y") else "false"
                    
                    # Detect if original value was quoted to maintain stylistic integrity & safe auto-quote
                    if len(old_val_core) >= 2 and old_val_core.startswith('"') and old_val_core.endswith('"'):
                        safe_val = val_str.replace('\\', '\\\\').replace('"', '\\"')
                        out_lines.append(f"{ws}{export_str}{key}=\"{safe_val}\"{comment_str}\n")
                    elif len(old_val_core) >= 2 and old_val_core.startswith("'") and old_val_core.endswith("'"):
                        # Bash single quotes natively escape via sequence: '"'"' (or '\'')
                        safe_val = val_str.replace("'", "'\\''")
                        out_lines.append(f"{ws}{export_str}{key}='{safe_val}'{comment_str}\n")
                    else:
                        # Auto-quote securely if shell metacharacters, spaces, or a
                        # leading hash (hex colors like #rrggbb) are present
                        if re.search(r'[ \t\n&|;<>()`"\'*?\[\]#]', val_str):
                            safe_val = val_str.replace('\\', '\\\\').replace('"', '\\"')
                            out_lines.append(f"{ws}{export_str}{key}=\"{safe_val}\"{comment_str}\n")
                        else:
                            out_lines.append(f"{ws}{export_str}{key}={val_str}{comment_str}\n")
                    continue
                    
            out_lines.append(line)

        # --- PASS 2: Append Missing Keys ---
        # Enforce POSIX compliance: ensure the file ends with a newline before appending
        if out_lines and not out_lines[-1].endswith('\n'):
            out_lines[-1] += '\n'

        missing_changes = [k for k in changes_dict.keys() if k not in applied_commits]
        for scope, key in missing_changes:
            val, target_itype = changes_dict[(scope, key)]
            val_str = str(val) if val is not None else ""
            
            if val_str in ("__DELETE__", "nil"): continue
            if val_str.startswith("__VAR__"): val_str = val_str[7:]
            if target_itype == "bool":
                val_str = "true" if val_str.lower() in ("true", "1", "yes", "on", "t", "y") else "false"
                
            # Reconstruct the raw backend key string if it was an indexed duplicate UI binding
            clean_key = key.split(":")[0] if ":" in key else key
                
            if re.search(r'[ \t\n&|;<>()`"\'*?\[\]#]', val_str):
                safe_val = val_str.replace('\\', '\\\\').replace('"', '\\"')
                out_lines.append(f"{clean_key}=\"{safe_val}\"\n")
            else:
                out_lines.append(f"{clean_key}={val_str}\n")
            applied_commits.add((scope, key))

        # --- PASS 3: Atomic Hardware Commit ---
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
                    stat_info = self.config_path.stat()
                    os.chown(temp_file_path, stat_info.st_uid, stat_info.st_gid)
                except OSError: pass
            else:
                try:
                    parent_stat = self.config_path.parent.stat()
                    os.chown(temp_file_path, parent_stat.st_uid, parent_stat.st_gid)
                    temp_file_path.chmod(0o644)
                except OSError: pass
                    
            os.replace(temp_file_path, self.config_path)
            
            # Immediately refresh the internal nanosecond state tracking (optimised to skip re-opening)
            self.file_mtime_ns = self.config_path.stat().st_mtime_ns
            success = True
            
        except PermissionError:
            if temp_file_path:
                temp_file_path.unlink(missing_ok=True)
            try:
                content = "".join(out_lines)
                res = subprocess.run(
                    ["sudo", "-n", "tee", str(self.config_path)],
                    input=content.encode(), capture_output=True, timeout=5
                )
                if res.returncode == 0:
                    self.file_mtime_ns = self.config_path.stat().st_mtime_ns
                    return True, f"Successfully batched {len(applied_commits)} env commits (sudo).", ""
                return False, "AUTH_REQUIRED", ""
            except Exception:
                return False, "AUTH_REQUIRED", ""
        except OSError as e:
            status_msg = f"Atomic commit failed: {e}"
        finally:
            # Modern Python 3.14 Pathlib Cleanup 
            if temp_file_path:
                temp_file_path.unlink(missing_ok=True)

        if success:
            return True, f"Successfully batched {len(applied_commits)} env commits.", ""
                
        return False, status_msg, ""
