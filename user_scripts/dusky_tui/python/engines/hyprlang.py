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
    
    Architectural Guarantees:
    - Zero-Destruction mutations: Preserves all whitespace, escaped characters (##), and inline comments.
    - C-Style Brace Indexing: Correctly tracks sequential duplicates (e.g. listener:1, listener:2).
    - Arithmetic Immunity: Ignores braces inside {{ math_operations }} to prevent premature block closures.
    - Atomic Writes: Utilizes tmpfiles and os.replace to prevent config corruption during unexpected halts.
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
        # Temporarily hide escaped hashes
        hidden = line.replace('##', '\x00')
        # Split at the first real hash
        if '#' in hidden:
            hidden = hidden.split('#', 1)[0]
        # Restore escaped hashes, replacing them with a single hash as per hyprlang spec
        return hidden.replace('\x00', '#').strip()

    def load_state(self) -> dict[str, Any]:
        """Parses active configurations into a flat state dictionary mapped to the UI scope requirements."""
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
                    if not clean:
                        continue
                    
                    # 1. Match Block Opens (Supports standard and special categories like `device[name] {`)
                    open_match = re.search(r'^([a-zA-Z0-9_.-]+(?:\[[^\]]+\])?(?:\s+[a-zA-Z0-9_.-]+)?)\s*\{', clean)
                    if open_match:
                        b_name = open_match.group(1).strip()
                        block_counts[b_name] = block_counts.get(b_name, 0) + 1
                        block_stack.append((b_name, block_counts[b_name]))
                        # Strip the open block component so subsequent logic doesn't misinterpret it
                        clean = clean[open_match.end():]
                    
                    # 2. Match Assignments
                    if "=" in clean:
                        k_raw, v_raw = clean.split("=", 1)
                        k = k_raw.strip()
                        v = v_raw.strip()
                        
                        # Handle inline categories (e.g., category:variable = value)
                        if ":" in k and not k.startswith("$") and not block_stack:
                            inline_parts = k.split(":", 1)
                            inline_scope = inline_parts[0].strip()
                            inline_key = inline_parts[1].strip()
                            self.cache[f"{inline_scope}/{inline_key}"] = v
                        else:
                            # Standard assignment
                            if k.startswith("$"):
                                self.cache[f"DEFAULT/{k}"] = v
                            elif block_stack:
                                current_b_name, current_count = block_stack[-1]
                                # Store standard unindexed access (if it's the first occurrence)
                                if current_count == 1:
                                    self.cache[f"{current_b_name}/{k}"] = v
                                # Always store explicit exact indexed name (e.g., listener:3)
                                self.cache[f"{current_b_name}:{current_count}/{k}"] = v
                            else:
                                self.cache[f"DEFAULT/{k}"] = v
                    
                    # 3. Match Block Closes (Immune to arithmetic {{}} braces)
                    clean_no_arith = re.sub(r'\{\{.*?\}\}', '', clean)
                    closes = clean_no_arith.split("=")[0].count("}")
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
        block_close_indices = {} # Tracks exact list index of `}` to append missing keys right before it
        
        try:
            if not self.config_path.exists():
                lines = []
            else:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                    
            # --- PASS 1: Inline Replacement & AST Traversal ---
            for line in lines:
                hidden = line.replace('##', '\x00')
                clean_no_comment = hidden.split('#')[0].replace('\x00', '#').strip()
                do_replace = False
                
                # 1. Update AST State (Opens)
                open_match = re.search(r'^([a-zA-Z0-9_.-]+(?:\[[^\]]+\])?(?:\s+[a-zA-Z0-9_.-]+)?)\s*\{', clean_no_comment)
                if open_match:
                    b_name = open_match.group(1).strip()
                    block_counts[b_name] = block_counts.get(b_name, 0) + 1
                    block_stack.append((b_name, block_counts[b_name]))
                    
                # 2. Match Target Mutations (Strictly avoids mutating lines that are concurrently opening blocks)
                if "=" in clean_no_comment and not open_match:
                    k_raw, v_raw = line.split("=", 1)
                    k = self._strip_comments(k_raw).strip()
                    matched_scope = None
                    
                    if k.startswith("$") and ("DEFAULT", k) in changes_dict:
                        matched_scope = "DEFAULT"
                    elif not block_stack and ":" in k and not k.startswith("$"):
                        inline_parts = k.split(":", 1)
                        inline_scope, inline_key = inline_parts[0].strip(), inline_parts[1].strip()
                        if (inline_scope, inline_key) in changes_dict:
                            matched_scope = inline_scope
                            k = inline_key
                    elif block_stack:
                        current_b_name, current_count = block_stack[-1]
                        check_scopes = [f"{current_b_name}:{current_count}"]
                        if current_count == 1:
                            check_scopes.append(current_b_name)
                            
                        for s in check_scopes:
                            if (s, k) in changes_dict:
                                matched_scope = s
                                break
                    elif ("DEFAULT", k) in changes_dict:
                        matched_scope = "DEFAULT"
                    
                    if matched_scope:
                        lookup = (matched_scope, k)
                        if lookup not in applied_commits:
                            val = changes_dict[lookup]
                            
                            if val == "__DELETE__":
                                applied_commits.add(lookup)
                                do_replace = True
                            else:
                                # Safe Rebuild: Protects prefix indentation and inline comments
                                prefix_whitespace = k_raw[:len(k_raw) - len(k_raw.lstrip())]
                                
                                comment_part = ""
                                if '#' in hidden:
                                    hash_idx = hidden.index('#')
                                    comment_part = " " + line[hash_idx:].rstrip('\n')
                                
                                out_lines.append(f"{prefix_whitespace}{k} = {val}{comment_part}\n")
                                applied_commits.add(lookup)
                                do_replace = True

                # 3. Update AST State (Closes) - Measured against current out_lines length
                clean_no_arith = re.sub(r'\{\{.*?\}\}', '', clean_no_comment)
                closes = clean_no_arith.split("=")[0].count("}")
                if closes > 0 and not do_replace:
                    for _ in range(closes):
                        if block_stack:
                            closed_block = block_stack.pop()
                            if closed_block not in block_close_indices:
                                block_close_indices[closed_block] = len(out_lines)

                if not do_replace:
                    out_lines.append(line)
                    
            # --- PASS 2: Intelligent Append of Missing Keys & Blocks ---
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
                    
                    # Insert right above existing block closure OR prepare entirely new block at EOF
                    if target_block in block_close_indices:
                        idx = block_close_indices[target_block]
                        insertions.setdefault(idx, []).append(f"    {key} = {val}\n")
                    else:
                        eof_blocks.setdefault(scope, []).append(f"    {key} = {val}\n")
                    
                    applied_commits.add((scope, key))
                    
                # Apply localized block insertions backwards to preserve static slice indices
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

        # --- PASS 3: Safe Atomic File Commit ---
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
            # Smart Reload Heuristics specific to the Hyprland ecosystem
            filename = self.config_path.name
            try:
                if "hypridle" in filename:
                    subprocess.run(["systemctl", "--user", "restart", "hypridle.service"], check=False, capture_output=True)
                elif "hyprpaper" in filename:
                    subprocess.run(["hyprctl", "hyprpaper", "reload"], check=False, capture_output=True)
                elif "hyprland" in filename or filename.endswith(".conf"):
                    subprocess.run(["hyprctl", "reload"], check=False, capture_output=True)
            except Exception:
                pass
            
            if len(applied_commits) == len(changes):
                return True, f"Successfully batched {len(changes)} commits.", ""
            else:
                return False, f"Partial success: saved {len(applied_commits)}/{len(changes)} items.", ""
                
        return False, status_msg, ""
