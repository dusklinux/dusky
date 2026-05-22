#!/usr/bin/env python3
"""
Dusky Dynamic Theme Builder - Golden AST Architecture
Optimized for: Arch Linux, Python 3.14+ (Hyprland/Wayland Ecosystem)
"""

import os
import sys
import re
import shutil
import subprocess
from urllib.parse import urlparse
from pathlib import Path

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
    
    # Prevent infinite loop if installation succeeds but resolution fails
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
        os.execv(sys.executable, [sys.executable, str(script_path)] + sys.argv[1:])
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
from rich.syntax import Syntax

# =============================================================================
# ▼ CORE CONFIGURATION & UTILITIES ▼
# =============================================================================

console = Console()
CONFIG_DIR = Path.home() / ".config" / "dusky_sites"

ROLES: dict[str, dict[str, str]] = {
    "1": {"name": "Main Background", "prop": "background-color", "var": "var(--surface)"},
    "2": {"name": "Sidebar / Navigation", "prop": "background-color", "var": "var(--surface_container_low)"},
    "3": {"name": "Panel/Card Background", "prop": "background-color", "var": "var(--surface_container)"},
    "4": {"name": "Input Field / Search", "prop": "background-color", "var": "var(--surface_container_highest)"},
    "5": {"name": "Primary Text", "prop": "color", "var": "var(--on_surface)"},
    "6": {"name": "Muted Text", "prop": "color", "var": "var(--on_surface_variant)"},
    "7": {"name": "Borders & Dividers", "prop": "border-color", "var": "var(--outline)"},
    "8": {"name": "Accent Element (Buttons)", "prop": "background-color", "var": "var(--primary)"},
    "9": {"name": "Text on Accent Button", "prop": "color", "var": "var(--on_primary)"},
    "10": {"name": "Error/Warning Alert", "prop": "background-color", "var": "var(--error)"},
    "11": {"name": "Hide / Remove Element", "prop": "display", "var": "none"},
    "12": {"name": "Make BG Transparent", "prop": "background-color", "var": "transparent"},
    "13": {"name": "Make Border Transparent", "prop": "border-color", "var": "transparent"},
    "14": {"name": "Make Text Transparent", "prop": "color", "var": "transparent"}
}

def safe_write_atomic(filepath: Path, content: str) -> None:
    """Writes to a file atomically to prevent corruption on crash or interrupt."""
    temp_path = filepath.with_suffix('.css.tmp')
    temp_path.write_text(content, encoding='utf-8')
    temp_path.replace(filepath)

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
        """Locates ALL @-moz-document blocks for the target domain."""
        docs = []
        # [FIX] Supports standard double/single quotes OR unquoted domains securely.
        escaped_domain = re.escape(self.domain)
        pattern = rf'(?:[\'"]{escaped_domain}[\'"]|\b{escaped_domain}\b)'
        
        for node in self.stylesheet:
            if getattr(node, 'at_keyword', None) == '-moz-document':
                prelude = tinycss2.serialize(node.prelude)
                if re.search(pattern, prelude):
                    docs.append(node)
        return docs

    def inject_rules(self, new_rules: list[dict]):
        """Injects or intelligently non-destructively merges rules into the AST."""
        docs = self._get_target_moz_documents()

        if not docs:
            moz_code = f'@-moz-document domain("{self.domain}") {{\n}}\n'
            moz_node = tinycss2.parse_stylesheet(moz_code)[0]
            self.stylesheet.append(moz_node)
            docs = [moz_node]

        target_moz_node = docs[-1] # Inject new items into the last matching block
        inner_nodes = target_moz_node.content if target_moz_node.content else []
        inner_rules = tinycss2.parse_rule_list(inner_nodes)

        existing_rules_map = {}
        for r in inner_rules:
            if getattr(r, 'type', '') == 'qualified-rule':
                sel = tinycss2.serialize(r.prelude).strip()
                existing_rules_map[sel] = r

        for r_data in new_rules:
            if r_data.get('type') == 'at-rule':
                # [FIX] Smart-merge for nested @rules (like @media) to prevent exponential bloat.
                new_node = r_data['ast_node']
                prelude_str = tinycss2.serialize(new_node.prelude).strip()
                keyword = getattr(new_node, 'at_keyword', '')
                
                replaced = False
                for i, existing_rule in enumerate(inner_rules):
                    if getattr(existing_rule, 'type', '') == 'at-rule' and getattr(existing_rule, 'at_keyword', '') == keyword:
                        if tinycss2.serialize(existing_rule.prelude).strip() == prelude_str:
                            inner_rules[i] = new_node
                            replaced = True
                            break
                            
                if not replaced:
                    inner_rules.append(new_node)
                continue

            sel = r_data['selector']
            props = r_data['props'] # List of tuples: [(key, val)]
            meta = r_data.get('meta')

            if sel in existing_rules_map:
                # [NON-DESTRUCTIVE AST MERGE MODE]
                old_rule = existing_rules_map[sel]
                parsed_content = tinycss2.parse_declaration_list(old_rule.content)
                
                keys_to_update = {k.lower() for k, _ in props}
                if meta:
                    keys_to_update.add('--dusky-meta')

                new_content = []
                for node in parsed_content:
                    # Strip out ONLY the declarations we are directly overriding
                    if getattr(node, 'type', '') == 'declaration' and node.lower_name in keys_to_update:
                        continue
                    new_content.append(node) # Preserves comments, whitespace, and fallbacks!

                if meta:
                    new_content.extend(tinycss2.parse_declaration_list(f"\n        --dusky-meta: \"{meta}\";"))
                
                for k, v in props:
                    # Append new declarations at the bottom of the block
                    suffix = " !important" if "!important" not in str(v).lower() else ""
                    new_content.extend(tinycss2.parse_declaration_list(f"\n        {k}: {v}{suffix};"))

                new_content.extend(tinycss2.parse_component_value_list("\n    "))
                old_rule.content = new_content
            else:
                # [CREATE NEW AST RULE]
                css_lines = [f"\n    {sel} {{"]
                if meta:
                    css_lines.append(f"        --dusky-meta: \"{meta}\";")
                for k, v in props:
                    suffix = " !important" if "!important" not in str(v).lower() else ""
                    css_lines.append(f"        {k}: {v}{suffix};")
                css_lines.append("    }\n")
                
                new_rule_ast = tinycss2.parse_stylesheet("\n".join(css_lines))[0]
                inner_rules.append(new_rule_ast)

        self._repack_moz_node(target_moz_node, inner_rules)

    def get_semantic_audit_list(self) -> list[dict]:
        """Scans the AST across all domain-matching @-moz-document blocks for metadata."""
        audit_list = []
        for moz_node in self._get_target_moz_documents():
            if not moz_node.content:
                continue
            inner_rules = tinycss2.parse_rule_list(moz_node.content)
            for r in inner_rules:
                if getattr(r, 'type', '') == 'qualified-rule':
                    sel = tinycss2.serialize(r.prelude).strip()
                    decls = [d for d in tinycss2.parse_declaration_list(r.content) if getattr(d, 'type', '') == 'declaration']
                    meta_decl = next((d for d in decls if d.lower_name == '--dusky-meta'), None)
                    if meta_decl:
                        meta_val = tinycss2.serialize(meta_decl.value).strip().strip('\'"')
                        audit_list.append({'selector': sel, 'meta': meta_val})
        return audit_list

    def update_rule_selector(self, target_selector: str, target_meta: str, new_selector: str):
        """Mutates a rule's selector safely across all domain blocks."""
        for moz_node in self._get_target_moz_documents():
            if not moz_node.content:
                continue
            inner_rules = tinycss2.parse_rule_list(moz_node.content)
            modified = False
            for r in inner_rules:
                if getattr(r, 'type', '') == 'qualified-rule':
                    sel = tinycss2.serialize(r.prelude).strip()
                    decls = [d for d in tinycss2.parse_declaration_list(r.content) if getattr(d, 'type', '') == 'declaration']
                    meta_decl = next((d for d in decls if d.lower_name == '--dusky-meta'), None)
                    meta_val = tinycss2.serialize(meta_decl.value).strip().strip('\'"') if meta_decl else None
                    
                    if sel == target_selector and meta_val == target_meta:
                        r.prelude = tinycss2.parse_component_value_list(new_selector + " ")
                        modified = True
                        
            if modified:
                self._repack_moz_node(moz_node, inner_rules)

    def delete_rule(self, target_selector: str, target_meta: str):
        """Purges a specific rule from the AST using semantic identity matching."""
        for moz_node in self._get_target_moz_documents():
            if not moz_node.content:
                continue
            inner_rules = tinycss2.parse_rule_list(moz_node.content)
            new_rules = []
            modified = False
            for r in inner_rules:
                if getattr(r, 'type', '') == 'qualified-rule':
                    sel = tinycss2.serialize(r.prelude).strip()
                    decls = [d for d in tinycss2.parse_declaration_list(r.content) if getattr(d, 'type', '') == 'declaration']
                    meta_decl = next((d for d in decls if d.lower_name == '--dusky-meta'), None)
                    meta_val = tinycss2.serialize(meta_decl.value).strip().strip('\'"') if meta_decl else None
                    
                    if sel == target_selector and meta_val == target_meta:
                        modified = True
                        continue # Skip and prune
                new_rules.append(r)
                
            if modified:
                self._repack_moz_node(moz_node, new_rules)

    def _repack_moz_node(self, moz_node: tinycss2.ast.AtRule, inner_rules: list):
        """Safely repacks inner rules, preventing multiline serialization corruption."""
        # [FIX] Added explicit double newlines to prevent stylesheet compaction bloat
        repacked_css = "\n\n".join(r.serialize().strip() for r in inner_rules)
        moz_node.content = tinycss2.parse_component_value_list(f"\n    {repacked_css}\n")

    def generate_css(self) -> str:
        """Serializes the entire modified AST back to a pristine string."""
        raw_output = "".join(node.serialize() for node in self.stylesheet)
        return re.sub(r'\n{3,}', '\n\n', raw_output).strip() + "\n"

# =============================================================================
# ▼ INTELLIGENT UX & PARSER UTILITIES ▼
# =============================================================================

def extract_domain(raw_input: str) -> str:
    """Extracts base domain securely and truncates to prevent OS-level path crashes."""
    raw_input = raw_input.strip()
    if not raw_input.startswith(('http://', 'https://')):
        raw_input = 'https://' + raw_input
    parsed = urlparse(raw_input)
    domain = parsed.netloc.split(':')[0]
    return re.sub(r'[^\w.-]', '', domain).removeprefix('www.')[:200]

def extract_css_variables(text: str) -> list[str]:
    """Bulletproof regex extraction to capture variables safely."""
    matches = re.findall(r'(--[a-zA-Z0-9_-]+)', text)
    return list(dict.fromkeys(matches)) # Deduplicate maintaining order

def get_smart_input(prompt_msg: str) -> str:
    """Safely captures massive multi-line CSS pastes avoiding premature truncation."""
    console.print(f"[bold cyan]{prompt_msg}[/]")
    console.print("[dim](Paste content. Type 'END' on a new line to finish, or press Ctrl+D or press Return Twice)[/]")
    lines = []
    empty_count = 0
    while True:
        try:
            line = input()
            if line.strip().upper() == "END":
                break
                
            if line == "":
                empty_count += 1
                if empty_count >= 2:
                    # Remove the first empty line added by the initial Return
                    if lines and lines[-1] == "":
                        lines.pop()
                    break
            else:
                empty_count = 0
                
            lines.append(line)
        except EOFError:
            break
    return "\n".join(lines).strip()

def print_menu() -> None:
    table = Table(show_header=True, header_style="bold magenta", border_style="dim", expand=True)
    table.add_column("Key", style="cyan", justify="center", width=5)
    table.add_column("Role / Semantic Element", style="white")
    table.add_column("Key", style="cyan", justify="center", width=5)
    table.add_column("Role / Semantic Element", style="white")
    
    keys = list(ROLES.keys())
    mid = (len(keys) + 1) // 2
    for i in range(mid):
        k1 = keys[i]
        v1 = ROLES[k1]['name']
        if i + mid < len(keys):
            k2 = keys[i + mid]
            v2 = ROLES[k2]['name']
            table.add_row(f"[{k1}]", v1, f"[{k2}]", v2)
        else:
            table.add_row(f"[{k1}]", v1, "", "")
            
    console.print(table)

# =============================================================================
# ▼ TUI WORKFLOWS ▼
# =============================================================================

def flow_audit_mode():
    console.clear()
    console.print(Panel.fit("=== Dusky Auditor: Fix or Prune Selectors ===", style="bold yellow"))
    
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # Sorted for deterministic TUI presentation
    css_files = sorted(CONFIG_DIR.glob("*.css"), key=lambda f: f.name)
    
    if not css_files:
        console.print("[bold red]No themes found in ~/.config/dusky_sites/[/]")
        return Prompt.ask("\nPress Enter to return")

    console.print("\n[bold cyan]Select a theme to audit:[/]")
    for idx, f in enumerate(css_files, 1):
        console.print(f"  [{idx}] {f.name}")
        
    file_choice = Prompt.ask("\nChoice", default="1")
    try:
        # [FIX] Prevent silent negative indexing wrapping to the end of the list
        f_idx = int(file_choice) - 1
        if f_idx < 0:
            raise ValueError
        selected_file = css_files[f_idx]
    except (IndexError, ValueError):
        return console.print("[red]Invalid choice.[/]")

    domain = selected_file.stem
    manager = DuskyASTManager(domain, selected_file)
    
    while True:
        audit_list = manager.get_semantic_audit_list()
        if not audit_list:
            console.print(f"\n[bold yellow]No semantic metadata (--dusky-meta) found in {selected_file.name}.[/]")
            return Prompt.ask("Press Enter to return")

        console.clear()
        console.print(f"[bold magenta]Auditing:[/] {selected_file.name}\n")
        
        table = Table(title="Tracked Semantic Elements", show_header=True, header_style="bold cyan")
        table.add_column("ID", justify="center", style="yellow")
        table.add_column("Semantic Name (Meta)", style="green")
        table.add_column("Current Selector", style="dim white")
        
        for idx, item in enumerate(audit_list, 1):
            table.add_row(str(idx), item['meta'], item['selector'])
            
        console.print(table)
        
        choice = Prompt.ask("\n[bold cyan]Enter ID to modify[/] [dim](or 'q' to quit)[/]")
        if choice.lower() == 'q':
            break
            
        try:
            # [FIX] Prevent silent negative indexing mapping
            c_idx = int(choice) - 1
            if c_idx < 0:
                raise ValueError
            target = audit_list[c_idx]
            
            console.print(f"\n[bold green]Targeting:[/] {target['meta']}")
            console.print(f"Selector: [dim]{target['selector']}[/]")
            
            action = Prompt.ask("\n[bold cyan]Action[/]", choices=["1", "2", "3"], 
                                default="1", show_choices=False,
                                prompt_suffix="\n  [1] Edit Selector\n  [2] Delete Rule Completely\n  [3] Cancel\nChoice: ")

            if action == "1":
                new_sel = Prompt.ask("\n[bold cyan]Paste the new updated selector[/]").strip()
                if new_sel and new_sel != target['selector']:
                    manager.update_rule_selector(target['selector'], target['meta'], new_sel)
                    safe_write_atomic(selected_file, manager.generate_css())
                    console.print("[bold green]✔ Selector updated & AST safely saved![/]")
            
            elif action == "2":
                confirm = Prompt.ask("[bold red]Are you sure you want to delete this rule?[/] (y/N)", default="n")
                if confirm.lower() == 'y':
                    manager.delete_rule(target['selector'], target['meta'])
                    safe_write_atomic(selected_file, manager.generate_css())
                    console.print("[bold green]✔ Rule purged & AST safely saved![/]")
                    
            if action in ["1", "2"]:
                Prompt.ask("Press Enter to continue")
                
        except (IndexError, ValueError):
            console.print("[red]Invalid ID.[/]")

def flow_create_edit():
    console.clear()
    console.print(Panel.fit("=== Dusky Dynamic Editor ===", style="bold magenta"))
    
    raw_domain = Prompt.ask("\n[bold cyan]Target Domain URL[/] [dim](e.g., https://github.com/)[/]").strip()
    domain = extract_domain(raw_domain)
    if not domain: return
        
    console.print(f"[*] Locking on: [bold green]{domain}[/]\n")
    
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    file_path = CONFIG_DIR / f"{domain}.css"
    manager = DuskyASTManager(domain, file_path)
    
    if file_path.exists():
        console.print(f"[bold yellow]⚡ Existing AST loaded for {domain}. Rules will be cleanly merged.[/]\n")

    pending_rules = []
    
    while True:
        console.print("[dim]" + "━"*50 + "[/]")
        user_input = get_smart_input("Paste a Selector, CSS Variable, or whole CSS Block")
        
        if not user_input:
            break
            
        # Path C: Prioritize parsing whole CSS Blocks natively (including @rules)
        if "{" in user_input and "}" in user_input:
            parsed_rules = tinycss2.parse_rule_list(user_input)
            found_rules = False
            for pr in parsed_rules:
                if getattr(pr, 'type', '') == 'qualified-rule':
                    sel = tinycss2.serialize(pr.prelude).strip()
                    decls = [d for d in tinycss2.parse_declaration_list(pr.content) if getattr(d, 'type', '') == 'declaration']
                    props = []
                    for d in decls:
                        val = tinycss2.serialize(d.value).strip()
                        # Preserve existing !important tags natively written in the paste
                        if getattr(d, 'important', False) and "!important" not in val.lower():
                            val += " !important"
                        if val:
                            props.append((d.lower_name, val))
                            
                    if props:
                        meta_name = Prompt.ask(f"[bold yellow]Name this block (Selector: {sel})[/] [dim](Enter to skip)[/]").strip()
                        # [FIX] Sanitize metadata quotes to prevent CSS syntax injection breaking the file
                        if meta_name: meta_name = meta_name.replace('"', "'")
                        
                        pending_rules.append({
                            "selector": sel,
                            "props": props,
                            "meta": meta_name if meta_name else None
                        })
                        found_rules = True
                        console.print(f"[bold green]✔ Added parsed block rule into memory: {sel}[/]")
                        
                elif getattr(pr, 'type', '') == 'at-rule':
                    name = getattr(pr, 'at_keyword', 'unknown')
                    meta_name = Prompt.ask(f"[bold yellow]Name this @{name} block[/] [dim](Enter to skip)[/]").strip()
                    if meta_name: meta_name = meta_name.replace('"', "'")
                    
                    pending_rules.append({
                        "type": "at-rule",
                        "ast_node": pr,
                        "meta": meta_name if meta_name else None
                    })
                    found_rules = True
                    console.print(f"[bold green]✔ Added parsed @{name} block into memory[/]")
                    
            if found_rules:
                continue

        # Path A: User pasted explicit variables
        extracted_vars = extract_css_variables(user_input)
        if extracted_vars:
            console.print(f"\n[bold green]✔ Extracted {len(extracted_vars)} CSS Variables![/]")
            
            root_selector = ":root, .dark"
            if len(extracted_vars) > 1:
                custom_root = Prompt.ask(f"\n[bold cyan]Apply to selector[/] [dim](Default: {root_selector})[/]").strip()
                if custom_root: root_selector = custom_root

            for var in extracted_vars:
                console.print(f"\n[bold yellow]Targeting Variable:[/] {var}")
                print_menu()
                role_choice = Prompt.ask(f"[bold cyan]Map {var} to Role[/] [dim](Enter to skip)[/]").strip()
                
                if role_choice in ROLES:
                    pending_rules.append({
                        "selector": root_selector,
                        "props": [(var, ROLES[role_choice]['var'])],
                        "meta": f"Variable {var}"
                    })
                    console.print(f"[bold green]✔ {var} mapped to {ROLES[role_choice]['name']}[/]")
            continue
            
        # Path B: User pasted a standard selector
        print_menu()
        role_choice = Prompt.ask("\n[bold cyan]Select the role[/]").strip()
        
        if role_choice in ROLES:
            role_data = ROLES[role_choice]
            meta_name = Prompt.ask("[bold yellow]Optional: Name this element (for easy future fixes)[/] [dim](e.g. Like Button)[/]").strip()
            if meta_name: meta_name = meta_name.replace('"', "'")
            
            pending_rules.append({
                "selector": user_input,
                "props": [(role_data['prop'], role_data['var'])],
                "meta": meta_name if meta_name else None
            })
            console.print(f"[bold green]✔ Added {role_data['name']} rule into memory.[/]")
        else:
            console.print("[bold red]✖ Invalid choice. Rule skipped.[/]")

    if not pending_rules:
        return console.print("\n[bold yellow]No rules collected. Exiting to main menu.[/]")

    # Inject and Serialize
    manager.inject_rules(pending_rules)
    final_css = manager.generate_css()

    console.print("\n")
    syntax = Syntax(final_css, "css", theme="monokai", line_numbers=True)
    console.print(Panel(syntax, title="[bold green]📄 GENERATED AST PREVIEW[/]", border_style="green"))

    # Rich Deployment Pipeline
    console.print("\n[bold magenta]Deployment Pipeline[/]")
    console.print("  [1] Save to ~/.config/dusky_sites/ only")
    console.print("  [2] Save & Deploy (Run dusky_firefox_tui.sh)")
    console.print("  [3] Save, Deploy, & Restart Firefox")
    console.print("  [4] Cancel & Discard")
    
    deploy_choice = Prompt.ask("\nChoice", choices=["1", "2", "3", "4"], default="2")
    
    if deploy_choice == "4":
        return console.print("[bold yellow]Discarded. Returning to menu.[/]")
        
    try:
        safe_write_atomic(file_path, final_css)
        console.print(f"\n[bold green]✔ AST safely written to:[/] {file_path}")
    except Exception as e:
        return console.print(f"[bold red]✖ Error writing file: {e}[/]")

    if deploy_choice in ["2", "3"]:
        scripts_dir = Path.home() / "user_scripts" / "theme_matugen" / "firefox"
        tui_script = scripts_dir / "dusky_firefox_tui.sh"
        
        if tui_script.exists():
            console.print("[dim]Executing AST Deployment via Dusky Manager...[/]")
            try:
                subprocess.run(["bash", str(tui_script), "--auto"], check=True)
                console.print("[bold green]✔ Deployment injected into Firefox profile![/]")
            except subprocess.CalledProcessError as e:
                console.print(f"[bold red]✖ Deployment failed: {e}[/]")
        else:
            console.print("[bold yellow]⚠ TUI deploy script not found. Deploy manually.[/]")

    if deploy_choice == "3":
        restart_sh = scripts_dir / "restart_browser.sh"
        if not restart_sh.exists(): restart_sh = scripts_dir / "restart.sh"
        
        if restart_sh.exists():
            console.print("[dim]Cycling Wayland Firefox instance...[/]")
            try:
                subprocess.run(["bash", str(restart_sh)], check=True)
                console.print("[bold green]✔ Firefox rebooted. New theme active.[/]")
            except Exception as e:
                console.print(f"[bold red]✖ Reboot error: {e}[/]")
        else:
            console.print("[bold yellow]⚠ Restart script not found. Please restart Firefox manually.[/]")

    Prompt.ask("\nPress Enter to return to main menu")

# =============================================================================
# ▼ ENTRY POINT ▼
# =============================================================================

def main():
    while True:
        console.clear()
        console.print(Panel.fit(
            "[bold cyan]Dusky Wayland CSS Generator[/] (AST Edition)\n"
            "[dim]Powered by tinycss2 | Built for Dusky[/]",
            border_style="magenta"
        ))
        
        console.print("\n  [1] Create or Edit Theme")
        console.print("  [2] Audit / Fix / Prune Existing Theme")
        console.print("  [3] Exit\n")
        
        choice = Prompt.ask("System Command", choices=["1", "2", "3"])
        
        if choice == "1":
            flow_create_edit()
        elif choice == "2":
            flow_audit_mode()
        else:
            break
            
    console.print("\n[dim]AST Engine disengaged. Goodbye![/]\n")

if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\n\n[!] Operation aborted. Goodbye!")
        sys.exit(0)
