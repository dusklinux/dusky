#!/usr/bin/env python3
"""
Dusky Dynamic Theme Builder - Ultimate Live Preview Master Edition
Optimized for: Arch Linux, Python 3.14.5, Dusky Sites Native Host Integration
"""

import os
import sys
import re
import time
import shutil
import subprocess
from urllib.parse import urlparse
from pathlib import Path
from typing import Any

# Enable GNU Readline for native up-arrow history support in input()
try:
    import readline
except ImportError:
    pass

# =============================================================================
# ▼ DEPENDENCY BOOTSTRAP (Arch Linux Native) ▼
# =============================================================================
def is_in_venv() -> bool:
    """Safely detect if running inside a virtual environment (PEP 668)."""
    return sys.prefix != sys.base_prefix

try:
    import rich
    import tinycss2
except ImportError:
    print("\n[!] Essential libraries ('rich' or 'tinycss2') are missing.")
    
    if os.environ.get("_DUSKY_BOOTSTRAP_ATTEMPTED"):
        print("[!] Bootstrap loop detected. Dependency resolution failed permanently.")
        print("[!] Please install manually: sudo pacman -S python-rich python-tinycss2")
        sys.exit(1)
        
    try:
        if is_in_venv():
            print("[*] Virtual environment detected. Installing dependencies via pip...")
            subprocess.run([sys.executable, '-m', 'pip', 'install', 'rich', 'tinycss2'], check=True)
        else:
            print("[*] System environment detected. Installing dependencies via pacman...")
            subprocess.run(['sudo', 'pacman', '-S', 'python-rich', 'python-tinycss2', '--needed', '--noconfirm'], check=True)
            
        print("[+] Installation successful! Initializing UI...\n")
        sys.stdout.flush()
        
        script_path = Path(sys.argv[0]).resolve()
        if not script_path.exists():
            which_path = shutil.which(sys.argv[0])
            if which_path:
                script_path = Path(which_path).resolve()
                
        if not script_path.exists():
            print("\n[!] Could not automatically resolve execution path. Please restart manually.")
            sys.exit(1)
                
        os.environ["_DUSKY_BOOTSTRAP_ATTEMPTED"] = "1"
        os.execve(sys.executable, [sys.executable, str(script_path)] + sys.argv[1:], os.environ)
    except subprocess.CalledProcessError:
        print("\n[!] Failed to install dependencies automatically.")
        sys.exit(1)
    except Exception as e:
        print(f"\n[!] Bootstrap exception: {e}")
        sys.exit(1)

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt

console = Console()

def get_xdg_config_home() -> Path:
    """Resolve the XDG base directory for configurations."""
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))

CONFIG_DIR = get_xdg_config_home() / "dusky_sites"

# =============================================================================
# ▼ LIVE MATUGEN PARSER (DYNAMIC MTIME POLLING) ▼
# =============================================================================

class ColorTracker:
    """
    Monitors the Matugen CSS file via mtime (Modification Time).
    If the wallpaper changes, it instantly absorbs the new variables for the TUI.
    """
    def __init__(self):
        self.path = get_xdg_config_home() / "matugen" / "generated" / "dusky_sites.css"
        self.last_mtime = 0.0
        self.colors: dict[str, str] = {}

    def get_colors(self) -> dict[str, str]:
        if not self.path.exists():
            return self.colors
        try:
            current_mtime = self.path.stat().st_mtime
            if current_mtime != self.last_mtime:
                content = self.path.read_text(encoding="utf-8")
                matches = re.findall(r'(--[a-zA-Z0-9_-]+):\s*([^;{}]+?)\s*;', content)
                self.colors = {k: v for k, v in matches}
                self.last_mtime = current_mtime
        except (OSError, UnicodeDecodeError):
            pass # Graceful fallback for lock/permission/encoding issues, prevents masking logic bugs
        return self.colors

live_color_tracker = ColorTracker()

# =============================================================================
# ▼ CORE CONFIGURATION & DRILL-DOWN MENUS ▼
# =============================================================================

def build_menus() -> tuple[dict[str, dict[str, str]], dict[str, Any], dict[str, dict[str, str]]]:
    """
    Dynamically rebuilds the menu structure, injecting the latest Matugen 
    variables into the 'Raw' sub-menu whenever called.
    """
    menu_structure = {
        "0": {"name": "🛑 Hide / Disable Element", "prop": "display", "var": "none"},
        "1": {"name": "Main Background", "prop": "background-color", "var": "var(--surface)"},
        "2": {"name": "Sidebar / Navigation", "prop": "background-color", "var": "var(--surface_container_low)"},
        "3": {"name": "Panel/Card Background", "prop": "background-color", "var": "var(--surface_container)"},
        "4": {"name": "Input Field / Search", "prop": "background-color", "var": "var(--surface_container_highest)"},
        "5": {"name": "Primary Text", "prop": "color", "var": "var(--on_surface)"},
        "6": {"name": "Muted Text", "prop": "color", "var": "var(--on_surface_variant)"},
        "7": {"name": "Borders & Dividers", "prop": "border-color", "var": "var(--outline)"},
        "8": {"name": "Accent Element (Primary)", "prop": "background-color", "var": "var(--primary)"},
        "9": {"name": "👻 Make Transparent", "prop": "background-color", "var": "transparent"},
    }

    sub_menus = {
        "s": {
            "name": "Surfaces & Containers",
            "items": {
                "s1": {"name": "App Background (Deepest)", "prop": "background-color", "var": "var(--background)"},
                "s2": {"name": "Surface Container Lowest", "prop": "background-color", "var": "var(--surface_container_lowest)"},
                "s3": {"name": "Surface Container High", "prop": "background-color", "var": "var(--surface_container_high)"},
                "s4": {"name": "Surface Dim", "prop": "background-color", "var": "var(--surface_dim)"},
                "s5": {"name": "Surface Bright", "prop": "background-color", "var": "var(--surface_bright)"},
                "s6": {"name": "Surface Variant", "prop": "background-color", "var": "var(--surface_variant)"},
                "s7": {"name": "Inverse Surface", "prop": "background-color", "var": "var(--inverse_surface)"},
            }
        },
        "a": {
            "name": "Accents (Secondary, Tertiary, Fixed)",
            "items": {
                "a1": {"name": "Primary Container", "prop": "background-color", "var": "var(--primary_container)"},
                "a2": {"name": "Secondary Base", "prop": "background-color", "var": "var(--secondary)"},
                "a3": {"name": "Secondary Container", "prop": "background-color", "var": "var(--secondary_container)"},
                "a4": {"name": "Tertiary Base", "prop": "background-color", "var": "var(--tertiary)"},
                "a5": {"name": "Tertiary Container", "prop": "background-color", "var": "var(--tertiary_container)"},
                "a6": {"name": "Inverse Primary", "prop": "background-color", "var": "var(--inverse_primary)"},
                "f1": {"name": "Primary Fixed", "prop": "background-color", "var": "var(--primary_fixed)"},
                "f2": {"name": "Primary Fixed Dim", "prop": "background-color", "var": "var(--primary_fixed_dim)"},
                "f3": {"name": "Secondary Fixed", "prop": "background-color", "var": "var(--secondary_fixed)"},
                "f4": {"name": "Tertiary Fixed", "prop": "background-color", "var": "var(--tertiary_fixed)"},
                "f5": {"name": "Text on Accent", "prop": "color", "var": "var(--on_primary)"},
            }
        },
        "t": {
            "name": "Advanced Text & Content",
            "items": {
                "t1": {"name": "On Background", "prop": "color", "var": "var(--on_background)"},
                "t2": {"name": "On Primary Container", "prop": "color", "var": "var(--on_primary_container)"},
                "t3": {"name": "On Secondary", "prop": "color", "var": "var(--on_secondary)"},
                "t4": {"name": "On Secondary Container", "prop": "color", "var": "var(--on_secondary_container)"},
                "t5": {"name": "On Tertiary", "prop": "color", "var": "var(--on_tertiary)"},
                "t6": {"name": "On Tertiary Container", "prop": "color", "var": "var(--on_tertiary_container)"},
                "t7": {"name": "Inverse On Surface", "prop": "color", "var": "var(--inverse_on_surface)"},
                "t8": {"name": "On Primary Fixed", "prop": "color", "var": "var(--on_primary_fixed)"},
                "t9": {"name": "Outline Variant", "prop": "border-color", "var": "var(--outline_variant)"},
            }
        },
        "e": {
            "name": "Error & Feedback States",
            "items": {
                "e1": {"name": "Error Base", "prop": "background-color", "var": "var(--error)"},
                "e2": {"name": "Error Container", "prop": "background-color", "var": "var(--error_container)"},
                "e3": {"name": "On Error Text", "prop": "color", "var": "var(--on_error)"},
                "e4": {"name": "On Error Container", "prop": "color", "var": "var(--on_error_container)"},
            }
        },
        "u": {
            "name": "Utilities, SVGs & Overlays",
            "items": {
                "u1": {"name": "Transparent Border", "prop": "border-color", "var": "transparent"},
                "u2": {"name": "Transparent Text", "prop": "color", "var": "transparent"},
                "u3": {"name": "Darken Scrim/Overlay", "prop": "background-color", "var": "rgba(var(--scrim_rgb), 0.5)"},
                "u4": {"name": "Invert Image/Element", "prop": "filter", "var": "invert(1) hue-rotate(180deg)"},
                "u5": {"name": "Remove Annoying Shadows", "prop": "box-shadow", "var": "none"},
                "u6": {"name": "SVG Icon Fill (Primary)", "prop": "fill", "var": "var(--primary)"},
                "u7": {"name": "SVG Icon Stroke (Primary)", "prop": "stroke", "var": "var(--primary)"},
            }
        }
    }

    current_colors = live_color_tracker.get_colors()
    if current_colors:
        sub_menus["r"] = {
            "name": "Raw Matugen Variables (Dynamically Loaded)",
            "items": {}
        }
        clean_vars = {k: v for k, v in current_colors.items() if not k.endswith("_rgb")}
        for idx, (var_name, hex_val) in enumerate(clean_vars.items(), 1):
            prop_guess = "color" if ("on_" in var_name or "text" in var_name or "outline" in var_name) else "background-color"
            sub_menus["r"]["items"][f"r{idx}"] = {
                "name": f"Raw: {var_name}",
                "prop": prop_guess,
                "var": f"var({var_name})"
            }

    all_roles = {**menu_structure}
    for sub in sub_menus.values():
        all_roles.update(sub["items"])

    return menu_structure, sub_menus, all_roles

def safe_write_atomic(filepath: Path, content: str) -> None:
    """
    Writes to a file atomically via POSIX bindings.
    Guarantees hardware sync (fsync) and strict permission preservation.
    Safely resolves symlinks (like stow dotfiles) and commits the parent 
    directory entry to the filesystem journal.
    """
    target_path = filepath.resolve()
    temp_path = target_path.with_name(f"{target_path.name}.{os.getpid()}.tmp")
    
    try:
        # Preserve original permissions if available, else default to secure 0o644
        mode = target_path.stat().st_mode if target_path.exists() else 0o644
        
        # O_TRUNC clears the file if it somehow existed; O_CREAT establishes the inode
        fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        
        # Wrap raw FD in Python's high-level file object for guaranteed full writes
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
            f.flush()     # Push application-level buffers into kernel space
            os.fsync(fd)  # Flush kernel block buffers securely to disk hardware
            
        # Atomic rename swap over the resolved original file
        os.replace(temp_path, target_path)
        
        # POSIX directory sync: Enforce filesystem journal commit of the rename operation
        dir_fd = os.open(target_path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
            
    finally:
        # Ensure cleanup if operations fail mid-flight
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass

# =============================================================================
# ▼ AST CSS ENGINE (tinycss2) ▼
# =============================================================================

class DuskyASTManager:
    """
    Elite AST manipulation class. 
    Parses stylesheets, extracts variables, safely merges AST tokens, and handles @rules.
    """
    def __init__(self, domain: str, filepath: Path):
        self.domain = domain
        self.filepath = filepath
        self.raw_css = filepath.read_text(encoding='utf-8') if filepath.exists() else ""
        self.stylesheet = tinycss2.parse_stylesheet(self.raw_css, skip_comments=False)

    def _get_target_moz_documents(self) -> list[tinycss2.ast.AtRule]:
        docs = []
        escaped_domain = re.escape(self.domain)
        pattern = rf'(?:[\'"]{escaped_domain}[\'"]|\b{escaped_domain}\b)'
        for node in self.stylesheet:
            if getattr(node, 'at_keyword', None) == '-moz-document':
                prelude = tinycss2.serialize(node.prelude)
                if re.search(pattern, prelude):
                    docs.append(node)
        return docs

    def _prune_empty_moz_nodes(self) -> None:
        kept_nodes = []
        for node in self.stylesheet:
            if getattr(node, 'at_keyword', None) == '-moz-document':
                if not node.content:
                    continue
                inner_rules = tinycss2.parse_rule_list(node.content)
                has_active_rules = any(
                    getattr(r, 'type', '') not in ('whitespace', 'error', 'comment') 
                    for r in inner_rules
                )
                if not has_active_rules:
                    continue 
            kept_nodes.append(node)
        self.stylesheet = kept_nodes

    def inject_rules(self, new_rules: list[dict[str, Any]]):
        docs = self._get_target_moz_documents()
        if not docs:
            moz_code = f'@-moz-document domain("{self.domain}") {{\n}}\n'
            moz_node = next(n for n in tinycss2.parse_stylesheet(moz_code) if getattr(n, 'type', '') == 'at-rule')
            self.stylesheet.append(moz_node)
            docs = [moz_node]

        target_moz_node = docs[-1]
        inner_nodes = target_moz_node.content if target_moz_node.content else []
        inner_rules = tinycss2.parse_rule_list(inner_nodes)

        existing_rules_map = {}
        for r in inner_rules:
            if getattr(r, 'type', '') == 'qualified-rule':
                sel = tinycss2.serialize(r.prelude).strip()
                raw_content = tinycss2.serialize(r.content)
                decls = [d for d in tinycss2.parse_declaration_list(raw_content) if getattr(d, 'type', '') == 'declaration']
                meta_decl = next((d for d in decls if d.lower_name == '--dusky-meta'), None)
                meta_val = tinycss2.serialize(meta_decl.value).strip().strip('\'"') if meta_decl else None
                existing_rules_map[(sel, meta_val)] = r

        for r_data in new_rules:
            if r_data.get('type') == 'at-rule':
                inner_rules.append(r_data['ast_node'])
                continue

            sel = r_data['selector']
            props = r_data['props']
            meta = r_data.get('meta')
            map_key = (sel, meta)

            if map_key in existing_rules_map:
                old_rule = existing_rules_map[map_key]
                raw_old_content = tinycss2.serialize(old_rule.content)
                parsed_content = tinycss2.parse_declaration_list(raw_old_content)
                keys_to_update = {k if k.startswith('--') else k.lower() for k, _ in props}

                new_content = []
                skip_trailing = False
                
                # Forward-looking token loop to prune ONLY associated formatting
                for node in parsed_content:
                    if getattr(node, 'type', '') == 'declaration':
                        target_name = node.name if node.name.startswith('--') else node.lower_name
                        if target_name in keys_to_update:
                            skip_trailing = True
                            continue

                    if skip_trailing and getattr(node, 'type', '') in ('whitespace', 'comment'):
                        if getattr(node, 'type', '') == 'whitespace' and '\n' in node.value:
                            skip_trailing = False  # Reset flag once newline clears the declaration block
                        continue

                    skip_trailing = False
                    new_content.append(node)

                while new_content and getattr(new_content[-1], 'type', '') == 'whitespace':
                    new_content.pop()
                
                for k, v in props:
                    # Enforce !important for robust override capability across sites
                    suffix = " !important" if "!important" not in str(v).lower() else ""
                    new_content.extend(tinycss2.parse_declaration_list(f"\n        {k}: {v}{suffix};"))

                new_content.extend(tinycss2.parse_component_value_list("\n    "))
                old_rule.content = new_content
            else:
                css_lines = [f"{sel} {{"]
                if meta:
                    css_lines.append(f"        --dusky-meta: \"{meta}\";")
                for k, v in props:
                    suffix = " !important" if "!important" not in str(v).lower() else ""
                    css_lines.append(f"        {k}: {v}{suffix};")
                css_lines.append("    }")
                
                parsed_nodes = tinycss2.parse_stylesheet("\n".join(css_lines))
                new_rule_ast = next((n for n in parsed_nodes if getattr(n, 'type', '') == 'qualified-rule'), None)
                
                if new_rule_ast:
                    inner_rules.append(new_rule_ast)
                    existing_rules_map[map_key] = new_rule_ast

        self._repack_moz_node(target_moz_node, inner_rules)

    def get_semantic_audit_list(self) -> list[dict[str, str]]:
        audit_list = []
        for moz_node in self._get_target_moz_documents():
            if not moz_node.content:
                continue
            inner_rules = tinycss2.parse_rule_list(moz_node.content)
            for r in inner_rules:
                if getattr(r, 'type', '') == 'qualified-rule':
                    sel = tinycss2.serialize(r.prelude).strip()
                    raw_content = tinycss2.serialize(r.content)
                    decls = [d for d in tinycss2.parse_declaration_list(raw_content) if getattr(d, 'type', '') == 'declaration']
                    meta_decl = next((d for d in decls if d.lower_name == '--dusky-meta'), None)
                    meta_val = tinycss2.serialize(meta_decl.value).strip().strip('\'"') if meta_decl else "[Unnamed Rule]"
                    audit_list.append({'selector': sel, 'meta': meta_val})
        return audit_list

    def update_rule_selector(self, target_selector: str, target_meta: str, new_selector: str):
        for moz_node in self._get_target_moz_documents():
            if not moz_node.content:
                continue
            inner_rules = tinycss2.parse_rule_list(moz_node.content)
            modified = False
            for r in inner_rules:
                if getattr(r, 'type', '') == 'qualified-rule':
                    sel = tinycss2.serialize(r.prelude).strip()
                    raw_content = tinycss2.serialize(r.content)
                    decls = [d for d in tinycss2.parse_declaration_list(raw_content) if getattr(d, 'type', '') == 'declaration']
                    meta_decl = next((d for d in decls if d.lower_name == '--dusky-meta'), None)
                    meta_val = tinycss2.serialize(meta_decl.value).strip().strip('\'"') if meta_decl else "[Unnamed Rule]"
                    
                    if sel == target_selector and meta_val == target_meta:
                        r.prelude = tinycss2.parse_component_value_list(new_selector + " ")
                        modified = True
                        
            if modified:
                self._repack_moz_node(moz_node, inner_rules)

    def delete_rule(self, target_selector: str, target_meta: str):
        for moz_node in self._get_target_moz_documents():
            if not moz_node.content:
                continue
            inner_rules = tinycss2.parse_rule_list(moz_node.content)
            new_rules = []
            modified = False
            for r in inner_rules:
                if getattr(r, 'type', '') == 'qualified-rule':
                    sel = tinycss2.serialize(r.prelude).strip()
                    raw_content = tinycss2.serialize(r.content)
                    decls = [d for d in tinycss2.parse_declaration_list(raw_content) if getattr(d, 'type', '') == 'declaration']
                    meta_decl = next((d for d in decls if d.lower_name == '--dusky-meta'), None)
                    meta_val = tinycss2.serialize(meta_decl.value).strip().strip('\'"') if meta_decl else "[Unnamed Rule]"
                    
                    if sel == target_selector and meta_val == target_meta:
                        modified = True
                        continue 
                new_rules.append(r)
                
            if modified:
                self._repack_moz_node(moz_node, new_rules)
                
        self._prune_empty_moz_nodes()

    def _repack_moz_node(self, moz_node: tinycss2.ast.AtRule, inner_rules: list):
        valid_rules = [r for r in inner_rules if getattr(r, 'type', '') not in ('error', 'whitespace')]
        if not valid_rules:
            moz_node.content = []
            return
        repacked_css = "\n\n    ".join(r.serialize().strip() for r in valid_rules)
        moz_node.content = tinycss2.parse_component_value_list(f"\n    {repacked_css}\n")

    def generate_css(self) -> str:
        raw_output = "".join(node.serialize() for node in self.stylesheet)
        return re.sub(r'\n{3,}', '\n\n', raw_output).strip() + "\n"

# =============================================================================
# ▼ INTELLIGENT UX & PARSER UTILITIES ▼
# =============================================================================

def extract_domain(raw_input: str) -> str:
    raw_input = raw_input.strip()
    if not raw_input.startswith(('http://', 'https://')):
        raw_input = 'https://' + raw_input
    parsed = urlparse(raw_input)
    domain = parsed.netloc.split(':')[0]
    return re.sub(r'[^\w.-]', '', domain).removeprefix('www.')[:200]

def extract_css_variables(text: str) -> list[str]:
    matches = re.findall(r'(--[a-zA-Z0-9_-]+)', text)
    return list(dict.fromkeys([m for m in matches if m != '--dusky-meta']))

def get_smart_input(prompt_msg: str) -> str:
    """Safely captures massive multi-line CSS pastes avoiding premature truncation."""
    console.print(f"[bold cyan]{prompt_msg}[/]")
    console.print("[dim](Paste content. Press Ctrl+D (EOF) to finish, or type 'END' on a new line)[/]")
    lines = []
    while True:
        try:
            line = input()
            if line.strip().upper() == "END":
                break
            lines.append(line)
        except EOFError:
            break
    return "\n".join(lines).strip()

def render_menu_item(data_dict: dict[str, str]) -> str:
    """Formats the item name, extracting and injecting live color swatches via rich markup."""
    name = data_dict['name']
    var_string = data_dict['var']
    swatch = "[dim]  [/dim]"
    var_name = ""
    
    match = re.search(r'var\((--[\w-]+)\)', var_string)
    if match:
        var_name = match.group(1)
        hex_val = live_color_tracker.get_colors().get(var_name)
        if hex_val:
            if hex_val.startswith('#') or (hex_val.startswith('rgb') and not hex_val.startswith('rgba')):
                safe_hex = hex_val.replace(" ", "")
                swatch = f"[{safe_hex}]██[/]"
            elif hex_val.startswith('rgba'):
                # Extract pure RGB from RGBA to safely display in Rich and prevent StyleSyntaxError crashes
                rgba_match = re.search(r'rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)', hex_val)
                if rgba_match:
                    r, g, b = rgba_match.groups()
                    swatch = f"[rgb({r},{g},{b})]██[/]"
                else:
                    swatch = "[dim]██[/dim]"
            else:
                swatch = "[dim]██[/dim]"
            
    if data_dict.get('prop') == 'display' and 'none' in data_dict.get('var', ''):
        swatch = "[red]✖✖[/red]"
    elif 'transparent' in data_dict.get('var', '') or 'none' in data_dict.get('var', ''):
        swatch = "[dim]▒▒[/dim]"

    if var_name and var_name not in name:
        return f"{swatch} {name} [dim]({var_name})[/dim]"
    return f"{swatch} {name}"

def print_main_menu(menu_structure: dict, sub_menus: dict) -> None:
    table = Table(show_header=True, header_style="bold magenta", border_style="dim", expand=True)
    table.add_column("Key", style="cyan", justify="center", width=5)
    table.add_column("Core Setup / Semantic Element", style="white")
    table.add_column("Key", style="cyan", justify="center", width=5)
    table.add_column("Core Setup / Semantic Element", style="white")
    
    keys = list(menu_structure.keys())
    mid = (len(keys) + 1) // 2
    for i in range(mid):
        k1 = keys[i]
        v1 = render_menu_item(menu_structure[k1])
        if i + mid < len(keys):
            k2 = keys[i + mid]
            v2 = render_menu_item(menu_structure[k2])
            table.add_row(f"[{k1}]", v1, f"[{k2}]", v2)
        else:
            table.add_row(f"[{k1}]", v1, "", "")
            
    console.print(table)
    
    adv_table = Table(show_header=False, border_style="dim", expand=True)
    adv_table.add_column(style="cyan", justify="center", width=5)
    adv_table.add_column(style="dim white")
    for key, data in sub_menus.items():
        adv_table.add_row(f"[{key.upper()}]", f"Browse {data['name']}...")
    console.print(adv_table)

def print_sub_menu(sub_key: str, sub_menus: dict) -> None:
    data = sub_menus[sub_key]
    table = Table(title=f"=== {data['name']} ===", show_header=True, header_style="bold yellow", border_style="dim")
    table.add_column("Key", style="cyan", justify="center", width=5)
    table.add_column("Role / Semantic Element", style="white")
    
    for k, v in data['items'].items():
        table.add_row(f"[{k}]", render_menu_item(v))
    console.print(table)

def prompt_for_role(context_msg: str) -> dict[str, str] | None:
    while True:
        menu_structure, sub_menus, all_roles = build_menus()

        console.print("\n[dim]" + "━"*50 + "[/]")
        console.print(context_msg)
        print_main_menu(menu_structure, sub_menus)
        choice = Prompt.ask("\n[bold cyan]Select Role[/] [dim](Enter to skip)[/]").strip().lower()
        
        if not choice:
            return None
            
        if choice in sub_menus:
            while True:
                console.print("\n[dim]" + "━"*50 + "[/]")
                console.print(f"[bold yellow]Sub-Menu:[/] {sub_menus[choice]['name']}")
                print_sub_menu(choice, sub_menus)
                sub_choice = Prompt.ask(f"\n[bold cyan]Select {sub_menus[choice]['name']} role[/] [dim](or 'b' to go back)[/]").strip().lower()
                
                if sub_choice == 'b':
                    break
                    
                if sub_choice in all_roles:
                    return all_roles[sub_choice]
                else:
                    console.print("[bold red]✖ Invalid choice.[/]\n")
            continue
            
        if choice in all_roles:
            return all_roles[choice]
        else:
            console.print("[bold red]✖ Invalid choice. Try again.[/]\n")

# =============================================================================
# ▼ TUI WORKFLOWS ▼
# =============================================================================

def flow_audit_mode():
    console.clear()
    console.print(Panel.fit("=== Dusky Auditor: Fix or Prune Selectors ===", style="bold yellow"))
    
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    css_files = sorted(CONFIG_DIR.glob("*.css"), key=lambda f: f.name)
    
    if not css_files:
        console.print("[bold red]No themes found in your Dusky Sites config directory.[/]")
        Prompt.ask("\nPress Enter to return")
        return

    console.print("\n[bold cyan]Select a theme to audit:[/]")
    for idx, f in enumerate(css_files, 1):
        console.print(f"  [{idx}] {f.name}")
        
    file_choice = Prompt.ask("\nChoice", default="1")
    try:
        f_idx = int(file_choice) - 1
        if f_idx < 0: raise ValueError
        selected_file = css_files[f_idx]
    except (IndexError, ValueError):
        console.print("[bold red]Invalid choice.[/]")
        Prompt.ask("\nPress Enter to return")
        return

    domain = selected_file.stem
    manager = DuskyASTManager(domain, selected_file)
    
    while True:
        audit_list = manager.get_semantic_audit_list()
        if not audit_list:
            console.print(f"\n[bold yellow]No tracked active rules found in {selected_file.name}.[/]")
            Prompt.ask("\nPress Enter to return")
            return

        console.clear()
        console.print(f"[bold magenta]Auditing:[/] {selected_file.name}\n")
        console.print("[dim]Live modifications will instantly trigger Dusky Sites reload.[/dim]")
        
        table = Table(title="Tracked Semantic Elements", show_header=True, header_style="bold cyan")
        table.add_column("ID", justify="center", style="yellow", width=4)
        table.add_column("Semantic Name (Meta)", style="green", width=30)
        table.add_column("Current Selector", style="dim white", overflow="fold")
        
        for idx, item in enumerate(audit_list, 1):
            table.add_row(str(idx), item['meta'], item['selector'])
            
        console.print(table)
        
        choice = Prompt.ask("\n[bold cyan]Enter ID to modify[/] [dim](or 'q' to quit)[/]")
        if choice.lower() == 'q': break
            
        try:
            c_idx = int(choice) - 1
            if c_idx < 0: raise ValueError
            target = audit_list[c_idx]
            
            console.print(f"\n[bold green]Targeting:[/] {target['meta']}")
            console.print(f"Selector: [dim]{target['selector']}[/]")
            
            # API safety protocol embedded directly into the prompt message structure
            action_prompt = "\n[bold cyan]Action[/]\n  [1] Edit Selector\n  [2] Delete Rule Completely\n  [3] Cancel\nChoice"
            action = Prompt.ask(action_prompt, choices=["1", "2", "3"], default="1", show_choices=False)

            match action:
                case "1":
                    new_sel = Prompt.ask("\n[bold cyan]Paste the new updated selector[/]").strip()
                    if new_sel and new_sel != target['selector']:
                        manager.update_rule_selector(target['selector'], target['meta'], new_sel)
                        safe_write_atomic(selected_file, manager.generate_css())
                        console.print("[bold green]✔ Selector updated & Pushed to Browser! (Dusky Sites)[/]")
                case "2":
                    confirm = Prompt.ask("[bold red]Are you sure you want to delete this rule?[/] (y/N)", default="n")
                    if confirm.lower() == 'y':
                        manager.delete_rule(target['selector'], target['meta'])
                        safe_write_atomic(selected_file, manager.generate_css())
                        console.print("[bold green]✔ Rule purged & Pushed to Browser! (Dusky Sites)[/]")
                        
            if action in ["1", "2"]:
                time.sleep(1.0)
                
        except (IndexError, ValueError):
            console.print("[bold red]Invalid ID.[/]")
            time.sleep(1.2) # Hard pause to prevent TUI screen tearing and logic wipe

def flow_create_edit():
    console.clear()
    console.print(Panel.fit("=== Dusky Dynamic Editor (Live Preview Mode) ===", style="bold magenta"))
    
    raw_domain = Prompt.ask("\n[bold cyan]Target Domain URL[/] [dim](e.g., https://github.com/)[/]").strip()
    if not raw_domain: return
    domain = extract_domain(raw_domain)
    if not domain: return
        
    console.print(f"[*] Locking on: [bold green]{domain}[/]")
    console.print("[dim]Every mapped rule will instantly save and trigger Dusky Sites in your browser.[/dim]\n")
    
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    file_path = CONFIG_DIR / f"{domain}.css"
    manager = DuskyASTManager(domain, file_path)
    
    if file_path.exists():
        console.print(f"[bold yellow]⚡ Existing AST loaded for {domain}. Edits are merged safely.[/]\n")

    while True:
        console.print("[dim]" + "━"*50 + "[/]")
        user_input = get_smart_input("Paste a Selector, CSS Variable, or whole CSS Block")
        
        if not user_input:
            break
            
        pending_rules = []
        
        if "{" in user_input and "}" in user_input:
            parsed_rules = tinycss2.parse_stylesheet(user_input, skip_comments=True)
            for pr in parsed_rules:
                if getattr(pr, 'type', '') == 'qualified-rule':
                    sel = tinycss2.serialize(pr.prelude).strip()
                    decls = [d for d in tinycss2.parse_declaration_list(pr.content) if getattr(d, 'type', '') == 'declaration']
                    props = []
                    for d in decls:
                        prop_name = d.name if d.name.startswith('--') else d.lower_name
                        if prop_name == '--dusky-meta':
                            continue
                            
                        val = tinycss2.serialize(d.value).strip()
                        if getattr(d, 'important', False) and "!important" not in val.lower():
                            val += " !important"
                        if val:
                            props.append((prop_name, val))
                            
                    if props:
                        meta_name = Prompt.ask(f"[bold yellow]Name this block (Selector: {sel})[/] [dim](Enter to skip)[/]").strip()
                        if meta_name: meta_name = meta_name.replace('"', "'")
                        pending_rules.append({"selector": sel, "props": props, "meta": meta_name if meta_name else None})
                        
                elif getattr(pr, 'type', '') == 'at-rule':
                    name = getattr(pr, 'at_keyword', 'unknown')
                    pending_rules.append({"type": "at-rule", "ast_node": pr, "meta": None})
                    console.print(f"[dim]Injected @{name} block directly (untracked)[/dim]")

        elif extract_css_variables(user_input):
            extracted_vars = extract_css_variables(user_input)
            console.print(f"\n[bold green]✔ Extracted {len(extracted_vars)} CSS Variables![/]")
            
            # Unrestricted assignment mapping: always prompt and allow universal targeting.
            root_selector = ":root, .dark"
            custom_root = Prompt.ask(f"\n[bold cyan]Apply to selector[/] [dim](Default: {root_selector})[/]").strip()
            if custom_root: 
                root_selector = custom_root

            for var in extracted_vars:
                role_data = prompt_for_role(f"[bold yellow]Map {var} to Role[/]")
                if role_data:
                    pending_rules.append({
                        "selector": root_selector,
                        "props": [(var, role_data['var'])],
                        "meta": f"Variable {var}"
                    })

        else:
            role_data = prompt_for_role("[bold cyan]Select the role for your pasted selector[/]")
            if role_data:
                meta_name = Prompt.ask("[bold yellow]Optional: Name this element (for easy future fixes)[/] [dim](e.g. Like Button)[/]").strip()
                if meta_name: meta_name = meta_name.replace('"', "'")
                pending_rules.append({
                    "selector": user_input,
                    "props": [(role_data['prop'], role_data['var'])],
                    "meta": meta_name if meta_name else None
                })
        
        if pending_rules:
            try:
                manager.inject_rules(pending_rules)
                safe_write_atomic(file_path, manager.generate_css())
                console.print(f"\n[bold green]🚀 Mapped {len(pending_rules)} rule(s). Pushed to Dusky Sites Instantly![/]")
            except Exception as e:
                console.print(f"\n[bold red]✖ Critical Error during injection (Malformed Paste?): {e}[/]")

    console.print("\n[bold green]✔ Live session complete. File is up-to-date.[/]")
    
    console.print("\n[dim]If Dusky Sites is running, your browser is already synced.[/dim]")
    deploy_choice = Prompt.ask("Execute legacy hard-deploy shell scripts? (y/N)", default="n").lower()
    
    if deploy_choice == "y":
        scripts_dir = Path.home() / "user_scripts" / "firefox" / "theme_matugen"
        tui_script = scripts_dir / "tui_dusky_sites.py"
        if tui_script.exists():
            console.print("[dim]Executing Hard AST Deployment...[/]")
            try:
                subprocess.run([sys.executable, str(tui_script), "--export-state"], check=True)
                console.print("[bold green]✔ Deployment injected into Firefox profile![/]")
            except subprocess.CalledProcessError as e:
                console.print(f"[bold red]✖ Deployment failed: {e}[/]")
        
        restart_sh = scripts_dir / "restart_browser.sh"
        if not restart_sh.exists(): restart_sh = scripts_dir / "restart.sh"
        if restart_sh.exists():
            console.print("[dim]Cycling Wayland Firefox instance...[/]")
            try:
                subprocess.run(["bash", str(restart_sh)], check=True)
                console.print("[bold green]✔ Firefox rebooted.[/]")
            except Exception as e:
                console.print(f"[bold red]✖ Reboot error: {e}[/]")

    Prompt.ask("\nPress Enter to return to main menu")

# =============================================================================
# ▼ ENTRY POINT ▼
# =============================================================================

def main():
    while True:
        console.clear()
        console.print(Panel.fit(
            "[bold cyan]Dusky Wayland CSS Generator[/] (Live Master Edition)\n"
            "[dim]Powered by tinycss2 | Auto-Syncing with Dusky Sites[/]",
            border_style="magenta"
        ))
        
        if live_color_tracker.get_colors():
            console.print(f" [bold green]✔ Matugen Source Linked[/] [dim](Colors Hot-Reloading Active)[/]\n")
        else:
            console.print(f" [bold yellow]⚠ Live Colors Offline[/] [dim]({live_color_tracker.path.name} not found)[/]\n")
        
        console.print("  [1] Live Editor (Create/Modify Theme)")
        console.print("  [2] Audit / Fix / Prune Existing Theme")
        console.print("  [3] Exit\n")
        
        choice = Prompt.ask("System Command", choices=["1", "2", "3"])
        
        match choice:
            case "1":
                flow_create_edit()
            case "2":
                flow_audit_mode()
            case "3" | _:
                break
            
    console.print("\n[dim]AST Engine disengaged. Goodbye![/]\n")

if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\n\n[!] Operation aborted. Goodbye!")
        sys.exit(0)
