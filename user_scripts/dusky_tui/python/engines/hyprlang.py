#!/usr/bin/env python3
import os
import re
import stat
import tempfile
import subprocess
from pathlib import Path
from typing import Any

from python.frontend.core_types import BaseEngine

class HyprlangEngine(BaseEngine):
    """
    Production-grade AST-like engine for the modern Hyprlang configuration ecosystem.
    (Powers Hyprland, Hypridle, Hyprpaper, Hyprlock, etc.)
    
    Features:
    - Parses C-like brace structures and special categories (e.g., `device[name] {`).
    - Intelligently indexes duplicate blocks (e.g., `listener:1`, `listener:2`).
    - Respects Hyprlang arithmetic `{{}}` and comment escapes `##`.
    - Features PASS 2 Appends: Dynamically generates missing keys and blocks at EOF.
    """
    
    def __init__(self, config_path: str):
        self.config_path = Path(config_path).expanduser().resolve()
        self.cache: dict[str, Any] = {}
        self.file_mtime: float = 0.0

    @property
    def target_path(self) -> str:
        return str(self.config_path)

    def _strip_comments(self, line: str) -> str:
        """
        Safely strips Hyprlang comments (#) while respecting escaped hashes (##).
        """
        # Finds the first '#' that is not preceded or followed by another '#'
        clean = re.sub(r'(?<!#)#(?!#).*$', '', line)
        # Unescape the literal hashes
        return clean.replace('##', '#')

    def load_state(self) -> dict[str, Any]:
        """Parses active configurations into a flat state dictionary."""
        if not self.config_path.exists():
            return {}

        self.file_mtime = self.config_path.stat().st_mtime
        self.cache = {}
        
        block_stack = []
        block_counts = {}
        
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                for line in f:
                    clean = self._strip_comments(line)
                    
                    # 1. Count block opens (supports standard and special categories)
                    for match in re.finditer(r"([a-zA-Z0-9_.-]+(?:\[[^\]]+\])?)\s*\{", clean):
                        b_name = match.group(1).strip()
                        block_counts[b_name] = block_counts.get(b_name, 0) + 1
                        block_stack.append((b_name, block_counts[b_name]))
                    
                    # 2. Parse Assignments
                    if "=" in clean:
                        k, v = clean.split("=", 1)
                        k = k.strip()
                        v = v.strip()
                        
                        # Handle inline categories (e.g., category:variable = value)
                        if ":" in k and not k.startswith("$") and not block_stack:
                            inline_scope, inline_key = k.split(":", 1)
                            self.cache[f"{inline_scope.strip()}/{inline_key.strip()}"] = v
                        else:
                            # Standard assignment
                            if k.startswith("$"):
                                self.cache[f"DEFAULT/{k}"] = v
                            elif block_stack:
                                current_b_name, current_count = block_stack[-1]
                                # Store generic name (if it's the first) AND exact indexed name 
                                # to ensure the UI can seamlessly target either `general` or `listener:3`
                                if current_count == 1:
                                    self.cache[f"{current_b_name}/{k}"] = v
                                self.cache[f"{current_b_name}:{current_count}/{k}"] = v
                            else:
                                self.cache[f"DEFAULT/{k}"] = v
                        
                    # 3. Count block closes
                    closes = clean.count("}")
                    for _ in range(closes):
                        if block_stack:
                            block_stack.pop()
                            
        except (OSError, IOError) as e:
            print(f"Failed to read Hyprlang config {self.config_path}: {e}")
            
        return self.cache

    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = "string") -> tuple[bool, str, str]:
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        if not changes:
            return True, "No pending changes.", ""
            
        if self.config_path.exists():
            current_mtime = self.config_path.stat().st_mtime
            if current_mtime > self.file_mtime:
                return False, f"File {self.config_path.name} modified externally. Reload required.", ""

        changes_dict = {(scope, key): val for key, scope, val, _ in changes}
        applied_commits = set()
        out_lines = []
        
        block_stack = []
        block_counts = {}
        block_close_indices = {} # Tracks the line index of `}` for appending missing keys dynamically
        
        try:
            if not self.config_path.exists():
                # Allow building a config from scratch
                lines = []
            else:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                    
            # --- PASS 1: Inline Replacement ---
            for line in lines:
                clean = self._strip_comments(line)
                do_replace = False
                
                # 1. Update AST State (Opens)
                for match in re.finditer(r"([a-zA-Z0-9_.-]+(?:\[[^\]]+\])?)\s*\{", clean):
                    b_name = match.group(1).strip()
                    block_counts[b_name] = block_counts.get(b_name, 0) + 1
                    block_stack.append((b_name, block_counts[b_name]))
                    
                # 2. Match Target Mutations
                if "=" in clean:
                    k = clean.split("=")[0].strip()
                    matched_scope = None
                    
                    if k.startswith("$") and ("DEFAULT", k) in changes_dict:
                        matched_scope = "DEFAULT"
                    
                    if not matched_scope and block_stack:
                        current_b_name, current_count = block_stack[-1]
                        check_scopes = [f"{current_b_name}:{current_count}"]
                        if current_count == 1:
                            check_scopes.append(current_b_name)
                            
                        for s in check_scopes:
                            if (s, k) in changes_dict:
                                matched_scope = s
                                break
                    
                    # Handle inline category mutations
                    if not matched_scope and ":" in k and not k.startswith("$"):
                        inline_scope, inline_key = k.split(":", 1)
                        if (inline_scope.strip(), inline_key.strip()) in changes_dict:
                            matched_scope = inline_scope.strip()
                            k = inline_key.strip()
                            
                    if matched_scope:
                        lookup = (matched_scope, k)
                        if lookup not in applied_commits:
                            # Reconstruct line protecting original formatting, comments, and inline braces
                            eq_idx = line.find("=")
                            prefix = line[:eq_idx + 1]
                            
                            comment_part = ""
                            match_c = re.search(r'(?<!#)#(?!#)', line)
                            if match_c:
                                comment_part = " " + line[match_c.start():].rstrip('\n')
                                
                            tail = line[eq_idx + 1:match_c.start() if match_c else None]
                            braces_part = ""
                            if "}" in tail:
                                braces_part = " " + tail[tail.find("}"):].rstrip('\n')
                                
                            out_lines.append(f"{prefix} {changes_dict[lookup]}{braces_part}{comment_part}\n")
                            applied_commits.add(lookup)
                            do_replace = True

                # 3. Update AST State (Closes)
                closes = clean.count("}")
                for _ in range(closes):
                    if block_stack:
                        closed_block = block_stack.pop()
                        if closed_block not in block_close_indices:
                            block_close_indices[closed_block] = len(out_lines)

                if not do_replace:
                    out_lines.append(line)
                    
            # --- PASS 2: Intelligent Append ---
            missing_changes = set(changes_dict.keys()) - applied_commits
            if missing_changes:
                insertions = {}
                eof_blocks = {}
                
                for scope, key in missing_changes:
                    val = changes_dict[(scope, key)]
                    if val in ("__DELETE__", "nil", ""): continue
                    
                    if scope == "DEFAULT":
                        eof_blocks.setdefault(scope, []).append(f"{key} = {val}\n")
                        applied_commits.add((scope, key))
                        continue
                        
                    # Parse Target Scope
                    if ":" in scope:
                        parts = scope.rsplit(":", 1)
                        if len(parts) == 2 and parts[1].isdigit():
                            b_name, b_count = parts[0], int(parts[1])
                        else:
                            b_name, b_count = scope, 1
                    else:
                        b_name, b_count = scope, 1
                        
                    target_block = (b_name, b_count)
                    
                    # Insert into existing block OR prepare new EOF block
                    if target_block in block_close_indices:
                        idx = block_close_indices[target_block]
                        insertions.setdefault(idx, []).append(f"    {key} = {val}\n")
                    else:
                        eof_blocks.setdefault(scope, []).append(f"    {key} = {val}\n")
                    applied_commits.add((scope, key))
                    
                # Apply localized insertions backward to preserve tracking indices
                for idx in sorted(insertions.keys(), reverse=True):
                    out_lines = out_lines[:idx] + insertions[idx] + out_lines[idx:]
                    
                # Apply EOF Generation
                if eof_blocks:
                    if out_lines and not out_lines[-1].endswith("\n"):
                        out_lines[-1] += "\n"
                    for scope, lines in eof_blocks.items():
                        if scope == "DEFAULT":
                            out_lines.extend(lines)
                        else:
                            true_b_name = scope.rsplit(":", 1)[0] if (":" in scope and scope.rsplit(":",1)[1].isdigit()) else scope
                            out_lines.append(f"\n{true_b_name} {{\n")
                            out_lines.extend(lines)
                            out_lines.append("}\n")
                            
        except OSError as e:
            return False, f"Failed to open config for reading: {e}", ""

        # --- Safe Atomic File Commit ---
        success = False
        status_msg = "Failed"
        temp_file_path = None
        
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8', dir=self.config_path.parent) as tf:
                temp_file_path = Path(tf.name)
                tf.writelines(out_lines)
                
            if self.config_path.exists():
                try: temp_file_path.chmod(stat.S_IMODE(self.config_path.stat().st_mode))
                except OSError: pass
                    
            os.replace(temp_file_path, self.config_path)
            self.file_mtime = self.config_path.stat().st_mtime
            success = True
            
        except OSError as e:
            status_msg = f"Atomic commit failed: {e}"
        finally:
            if temp_file_path and temp_file_path.exists() and not success:
                try: temp_file_path.unlink()
                except OSError: pass

        if success:
            # Smart Reload Heuristics based on filename
            filename = self.config_path.name
            if "hypridle" in filename:
                try:
                    subprocess.run(["systemctl", "--user", "reset-failed", "hypridle.service"], check=False, capture_output=True)
                    subprocess.run(["systemctl", "--user", "restart", "hypridle.service"], check=False, capture_output=True)
                except Exception:
                    pass
            elif "hyprpaper" in filename:
                try: subprocess.run(["hyprctl", "hyprpaper", "reload"], check=False, capture_output=True)
                except Exception: pass
            
            if len(applied_commits) == len(changes):
                return True, f"Successfully batched {len(changes)} commits.", ""
            else:
                return False, f"Partial success: saved {len(applied_commits)}/{len(changes)} items.", ""
                
        return False, status_msg, ""
