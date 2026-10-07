import os
import re
lazy import stat
lazy import subprocess
lazy import tempfile
lazy from pathlib import Path
lazy from typing import Any

from python.frontend.core_types import BaseEngine

# Sentinel object to safely detect omitted defaults in overridden dict methods
_sentinel = sentinel("_sentinel")

class BridgedStateDict(dict):
    """
    A bridged dictionary that prevents the TUI from marking optional parameters 
    as '[Missing]' and striking them out.
    
    Because kernel parameters are flags that are inherently 'optional' (their absence 
    just implies the kernel's compile-time behavior), they should always be treated as 
    available and fully editable by the UI.
    """
    def __contains__(self, key: Any) -> bool:
        return True

    def __getitem__(self, key: Any) -> Any:
        return super().get(key, "unset")

    def get(self, key: Any, default: Any = _sentinel) -> Any:
        # Respect the caller's explicit default if provided, otherwise fallback to "unset"
        if default is _sentinel:
            return super().get(key, "unset")
        return super().get(key, default)


class CmdlineEngine(BaseEngine):
    """
    Bridged Intelligent engine for /etc/kernel/cmdline and similar kernel parameter files.
    
    Features:
    - Bridged State: Prevents optional parameters from rendering as missing/broken.
    - Type-Aware AST: Strictly separates boolean flags (rw) from KV pairs (root=xyz).
    - Token-Preservation: Uses regex to preserve the exact spacing and order of all arguments.
    - Kernel Precedence: Pre-scans AST to map edits to the LAST occurrence of duplicate keys.
    - Atomic Commits: Synchronous flushes with exact security context replication.
    - TOCTOU Guarded: MTime precision fetched directly from temporary file descriptors.
    """
    
    def __init__(self, config_path: str = "/etc/kernel/cmdline"):
        self.config_path = Path(config_path).expanduser().resolve()
        self.cache: BridgedStateDict = BridgedStateDict()
        self.file_mtime_ns: int = 0

    @property
    def target_path(self) -> str:
        return str(self.config_path)

    def load_state(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return BridgedStateDict()

        self.cache = BridgedStateDict()
        
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                # Lock timestamp precision immediately after securing the file descriptor
                self.file_mtime_ns = os.fstat(f.fileno()).st_mtime_ns
                content = f.read().strip()
                
            # Advanced tokenization respecting single/double quotes
            tokens = re.split(r'((?:[^\s"\']|"[^"]*"|\'[^\']*\')+)', content)
            args = [t for t in tokens if t.strip()]
            counts: dict[str, int] = {}
            
            for arg in args:
                if "=" in arg:
                    k, v = arg.split("=", 1)
                else:
                    k, v = arg, "true"
                    
                counts[k] = counts.get(k, 0) + 1
                count = counts[k]
                
                # Explicit index mapping for duplicated parameters
                self.cache[f"DEFAULT/{k}:{count}"] = v
                
                # Unconditional overwrite ensures the base UI key always targets the LAST 
                # occurrence, accurately mimicking the Linux Kernel's parsing precedence.
                self.cache[f"DEFAULT/{k}"] = v

        except OSError as e:
            print(f"Failed to read cmdline config {self.config_path}: {e}")
            
        return self.cache

    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = "string") -> tuple[bool, str, str]:
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        if not changes:
            return True, "No pending changes.", ""

        content = ""
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    # Prevent TOCTOU modifications by verifying against the active file descriptor
                    current_mtime_ns = os.fstat(f.fileno()).st_mtime_ns
                    if current_mtime_ns > self.file_mtime_ns:
                        return False, f"File {self.config_path.name} modified externally. Reload required.", ""
                    content = f.read().strip().replace('\n', ' ')
            except OSError as e:
                return False, f"Failed to open config for verification: {e}", ""

        changes_dict = {(scope, key): (val, itype) for key, scope, val, itype in changes}
        applied_commits = set()
        
        try:
            tokens = re.split(r'((?:[^\s"\']|"[^"]*"|\'[^\']*\')+)', content)
            
            # Pre-scan occurrence counts. Essential for correctly updating the active parameter
            max_counts = {}
            for t in tokens:
                if not t.strip():
                    continue
                k = t.split("=", 1)[0]
                max_counts[k] = max_counts.get(k, 0) + 1
                
            out_tokens: list[str] = []
            counts: dict[str, int] = {}
            
            for t in tokens:
                if not t.strip():
                    out_tokens.append(t)
                    continue
                    
                if "=" in t:
                    k, v = t.split("=", 1)
                else:
                    k, v = t, ""
                    
                counts[k] = counts.get(k, 0) + 1
                count = counts[k]
                
                lookup_exact = ("DEFAULT", f"{k}:{count}")
                lookup_base = ("DEFAULT", k)
                
                target_val = None
                target_itype = None
                matched_lookup = None
                
                # Evaluate explicit occurrence overrides first
                if lookup_exact in changes_dict:
                    target_val, target_itype = changes_dict[lookup_exact]
                    matched_lookup = lookup_exact
                # Map standard schema keys exclusively to the final, active kernel occurrence
                elif count == max_counts[k] and lookup_base in changes_dict:
                    target_val, target_itype = changes_dict[lookup_base]
                    matched_lookup = lookup_base
                    
                if target_val is not None:
                    applied_commits.add(matched_lookup)
                    val_str = str(target_val)
                    val_lower = val_str.lower()
                    
                    if val_str in ("__DELETE__", "unset", "") or (target_itype == "bool" and val_lower == "false"):
                        # Safely collapse unbounded whitespace during parameter removal
                        if out_tokens and out_tokens[-1].isspace():
                            out_tokens.pop()
                    elif target_itype == "bool" and val_lower == "true":
                        out_tokens.append(k)
                    else:
                        out_tokens.append(f"{k}={val_str}")
                else:
                    out_tokens.append(t)
                        
            # Append completely missing parameters to the tail of the command line
            missing_changes = set(changes_dict.keys()) - applied_commits
            
            for scope, key_raw in missing_changes:
                val, target_itype = changes_dict[(scope, key_raw)]
                val_str = str(val)
                val_lower = val_str.lower()
                
                if val_str in ("__DELETE__", "unset", "") or (target_itype == "bool" and val_lower == "false"):
                    continue
                    
                # Strip potential explicit duplicate index mappings (e.g., 'root:2' -> 'root')
                clean_key = key_raw.split(":")[0] if ":" in key_raw else key_raw
                
                needs_space = False
                for tk in reversed(out_tokens):
                    if tk:
                        needs_space = bool(tk.strip())
                        break
                if needs_space:
                    out_tokens.append(" ")
                    
                if target_itype == "bool" and val_lower == "true":
                    out_tokens.append(clean_key)
                else:
                    out_tokens.append(f"{clean_key}={val_str}")
                    
                applied_commits.add((scope, key_raw))
                
            final_content = "".join(out_tokens).strip() + "\n"
            
            # --- Safe Atomic File Commit with Security Context Preservation ---
            success = False
            status_msg = "Failed"
            temp_file_path = None
            
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8', dir=self.config_path.parent) as tf:
                temp_file_path = Path(tf.name)
                tf.write(final_content)
                tf.flush()
                os.fsync(tf.fileno())  # Strictly guarantee data has landed on disk before proceeding
                
            if self.config_path.exists():
                try:
                    stat_info = self.config_path.stat()
                    # Do NOT use shutil.copystat as it destructively copies the old mtime, 
                    # masking the write event from internal/external watchers.
                    os.chmod(temp_file_path, stat.S_IMODE(stat_info.st_mode))
                    os.chown(temp_file_path, stat_info.st_uid, stat_info.st_gid)
                except OSError: 
                    pass
            
            # Extract precise MTime directly from the temporary descriptor PRIOR to replacement.
            # This completely nullifies potential microsecond TOCTOU race conditions.
            temp_mtime_ns = temp_file_path.stat().st_mtime_ns
            os.replace(temp_file_path, self.config_path)
            
            self.file_mtime_ns = temp_mtime_ns
            success = True
            
        except PermissionError:
            if 'temp_file_path' in locals() and temp_file_path and temp_file_path.exists():
                try: temp_file_path.unlink()
                except OSError: pass
            try:
                with tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8') as tf:
                    tf.write(final_content)
                    tmp_path = tf.name
                res = subprocess.run(
                    ["sudo", "-n", "tee", str(self.config_path)],
                    input=final_content.encode(), capture_output=True, timeout=5
                )
                try: os.unlink(tmp_path)
                except OSError: pass
                if res.returncode == 0:
                    self.file_mtime_ns = self.config_path.stat().st_mtime_ns
                    return True, f"Successfully batched {len(applied_commits)} commits (sudo).", ""
                return False, "AUTH_REQUIRED", ""
            except Exception:
                return False, "AUTH_REQUIRED", ""
        except OSError as e:
            status_msg = f"Atomic commit failed: {e}"
        finally:
            if 'temp_file_path' in locals() and temp_file_path and temp_file_path.exists() and not success:
                try: temp_file_path.unlink()
                except OSError: pass

        if success:
            return True, f"Successfully batched {len(applied_commits)} commits.", ""
            
        return False, status_msg, ""
