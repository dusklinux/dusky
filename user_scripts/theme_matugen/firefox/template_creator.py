#!/usr/bin/env python3
"""
Dusky Dynamic Theme Builder
Optimized for: Arch Linux, Python 3.14.3
"""

import os
import sys
import re
import shutil
import subprocess
from urllib.parse import urlparse
from pathlib import Path

# =============================================================================
# ▼ DEPENDENCY BOOTSTRAP ▼
# =============================================================================
def is_in_venv() -> bool:
    """Safely detect if running inside a virtual environment (PEP 668)."""
    return sys.prefix != sys.base_prefix

try:
    import rich
except ImportError:
    print("\n[!] The 'rich' UI library is missing.")
    try:
        if is_in_venv():
            print("[*] Virtual environment detected. Installing 'rich' via pip...")
            subprocess.run([sys.executable, '-m', 'pip', 'install', 'rich'], check=True)
        else:
            print("[*] System environment detected. Installing 'python-rich' via pacman...")
            subprocess.run(['sudo', 'pacman', '-S', 'python-rich', '--needed', '--noconfirm'], check=True)
            
        print("[+] Installation successful! Initializing UI...\n")
        
        # Safely resolve absolute script path to guarantee process context replacement
        script_path = Path(sys.argv[0]).resolve()
        if not script_path.exists():
            which_path = shutil.which(sys.argv[0])
            if which_path:
                script_path = Path(which_path).resolve()
                
        os.execv(sys.executable, [sys.executable, str(script_path)] + sys.argv[1:])
    except subprocess.CalledProcessError:
        print("\n[!] Failed to install dependencies automatically.")
        print("[!] Your Arch package database might be out of sync, or you lack permissions.")
        print("[!] Please update and install manually:\n    sudo pacman -Syu python-rich")
        sys.exit(1)
    except FileNotFoundError:
        print("\n[!] Error: 'pacman' or 'pip' not found. Ensure your Arch environment is healthy.")
        sys.exit(1)
    except Exception as e:
        print(f"\n[!] Bootstrap exception: {e}")
        sys.exit(1)

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt, Confirm

# =============================================================================
# ▼ CORE CONFIGURATION ▼
# =============================================================================

console = Console()
CONFIG_DIR = Path.home() / ".config" / "dusky_sites"

ROLES = {
    "1": {"name": "Main Background", "prop": "background-color", "var": "var(--surface)"},
    "2": {"name": "Sidebar / Navigation Background", "prop": "background-color", "var": "var(--surface_container_low)"},
    "3": {"name": "Panel/Card Background", "prop": "background-color", "var": "var(--surface_container)"},
    "4": {"name": "Input Field / Search Bar", "prop": "background-color", "var": "var(--surface_container_highest)"},
    "5": {"name": "Primary Text (Headings/Body)", "prop": "color", "var": "var(--on_surface)"},
    "6": {"name": "Muted Text (Subtitles/Dates)", "prop": "color", "var": "var(--on_surface_variant)"},
    "7": {"name": "Borders & Dividers", "prop": "border-color", "var": "var(--outline)"},
    "8": {"name": "Accent Element (Buttons/Links)", "prop": "background-color", "var": "var(--primary)"},
    "9": {"name": "Text on Accent Button", "prop": "color", "var": "var(--on_primary)"},
    "10": {"name": "Error/Warning Alert", "prop": "background-color", "var": "var(--error)"},
    "11": {"name": "Hide / Remove Element", "prop": "display", "var": "none"}
}

DEBUG_COLORS = [
    "red", "teal", "dodgerblue", "blueviolet", "lime", 
    "magenta", "yellow", "cyan", "darkorange", "hotpink"
]

# =============================================================================
# ▼ CSS PARSING AND MANIPULATION LOGIC ▼
# =============================================================================

def split_selectors(selector_string: str) -> list[str]:
    """Safely splits a grouped CSS selector, tracking strings, parentheses, and global escapes."""
    result = []
    current = []
    paren_depth = 0
    in_string = False
    string_char = ''
    
    i = 0
    while i < len(selector_string):
        char = selector_string[i]
        
        # Absolute Escape Handling (Protects against \. or \, breaking the parser)
        if char == '\\':
            current.append(char)
            i += 1
            if i < len(selector_string):
                current.append(selector_string[i])
            i += 1
            continue
            
        if in_string:
            if char == string_char:
                in_string = False
            current.append(char)
        else:
            if char in ("'", '"'):
                in_string = True
                string_char = char
                current.append(char)
            elif char == '(':
                paren_depth += 1
                current.append(char)
            elif char == ')':
                paren_depth -= 1
                current.append(char)
            elif char == ',' and paren_depth == 0:
                result.append("".join(current).strip())
                current.clear()
                i += 1
                continue
            else:
                current.append(char)
        i += 1
        
    if current:
        result.append("".join(current).strip())
    return [r for r in result if r]

def clean_css_rules(css_text: str, target_selector: str) -> str:
    """
    Robust lexical parser. Parses CSS blocks dynamically.
    Recurses intelligently into at-rules (@media, @supports) to prevent AST destruction.
    """
    target_sub_selectors = split_selectors(target_selector)
    i = 0
    depth = 0
    in_string = False
    string_char = ''
    in_comment = False
    
    result_css: list[str] = []
    current_chunk_start = 0
    first_brace_idx = -1
    
    while i < len(css_text):
        char = css_text[i]
        
        # 1. Global Escape Handler
        if char == '\\':
            i += 2
            continue
            
        # 2. Comment Handlers
        if in_comment:
            if char == '*' and i + 1 < len(css_text) and css_text[i+1] == '/':
                in_comment = False
                i += 1
            i += 1
            continue
            
        if char == '/' and i + 1 < len(css_text) and css_text[i+1] == '*':
            in_comment = True
            i += 2
            continue
            
        # 3. String Handlers
        if in_string:
            if char == string_char:
                in_string = False
            i += 1
            continue
            
        if char in ("'", '"'):
            in_string = True
            string_char = char
            i += 1
            continue
            
        # 4. Block Hierarchy Handlers
        if char == '{':
            if depth == 0:
                first_brace_idx = i
            depth += 1
            
        elif char == '}':
            depth -= 1
            if depth == 0:
                block_text = css_text[current_chunk_start:i+1]
                
                # Defends against "brace inside comment" poisoning
                rel_brace_idx = first_brace_idx - current_chunk_start
                
                raw_selector = block_text[:rel_brace_idx]
                body = block_text[rel_brace_idx+1:-1]
                
                pure_selector = re.sub(r'/\*.*?\*/', '', raw_selector, flags=re.DOTALL).strip()
                
                # Recursive protection for nested rules (e.g. @media)
                if pure_selector.startswith('@'):
                    cleaned_body = clean_css_rules(body, target_selector)
                    result_css.append(f"{raw_selector}{{{cleaned_body}}}")
                else:
                    selectors = split_selectors(pure_selector)
                    original_len = len(selectors)
                    selectors = [s for s in selectors if s not in target_sub_selectors]
                    
                    if not selectors:
                        pass # Complete match, prune the block
                    elif len(selectors) < original_len:
                        leading_space = raw_selector[:len(raw_selector) - len(raw_selector.lstrip())]
                        new_selector_text = leading_space + ",\n".join(selectors) + " "
                        result_css.append(f"{new_selector_text}{{{body}}}")
                    else:
                        result_css.append(block_text)
                        
                current_chunk_start = i + 1
                first_brace_idx = -1
        i += 1
        
    result_css.append(css_text[current_chunk_start:])
    return "".join(result_css)

def isolate_domain_block(css_content: str, domain: str) -> tuple[str, str, str]:
    """Safely extracts a target @-moz-document block by actively matching braces."""
    # Immune to comments injected between the declaration and the opening brace
    match = re.search(rf'@-moz-document\s+domain\([\'"]?{re.escape(domain)}[\'"]?\)(?:\s|/\*.*?\*/)*{{', css_content, flags=re.DOTALL)
    if not match:
        return ("", "", "")
    
    brace_start = match.end() - 1 
    
    i = brace_start
    depth = 0
    in_string = False
    string_char = ''
    in_comment = False
    
    while i < len(css_content):
        char = css_content[i]
        
        if char == '\\':
            i += 2
            continue
            
        if in_comment:
            if char == '*' and i + 1 < len(css_content) and css_content[i+1] == '/':
                in_comment = False
                i += 1
            i += 1
            continue
            
        if char == '/' and i + 1 < len(css_content) and css_content[i+1] == '*':
            in_comment = True
            i += 2
            continue
            
        if in_string:
            if char == string_char:
                in_string = False
            i += 1
            continue
            
        if char in ("'", '"'):
            in_string = True
            string_char = char
            i += 1
            continue
            
        if char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                prefix = css_content[:brace_start + 1]
                inner = css_content[brace_start + 1 : i]
                suffix = css_content[i:]
                return prefix, inner, suffix
        i += 1
        
    return ("", "", "")

def generate_production_css(domain: str, rules: list[dict[str, str]], existing_content: str = "") -> str:
    new_rules_parts = []
    for rule in rules:
        role_data = ROLES[rule['role']]
        new_rules_parts.append(f"    /* {role_data['name']} */\n")
        new_rules_parts.append(f"    {rule['selector']} {{\n")
        new_rules_parts.append(f"        {role_data['prop']}: {role_data['var']} !important;\n")
        new_rules_parts.append("    }\n\n")
        
    new_rules_str = "".join(new_rules_parts)
    
    if not existing_content.strip():
        return f'@-moz-document domain("{domain}") {{\n\n{new_rules_str}}}\n'
        
    prefix, inner, suffix = isolate_domain_block(existing_content, domain)
    
    if not inner and not prefix and not suffix:
        # File has contents, but no matching domain block yet
        return existing_content.rstrip() + f'\n\n@-moz-document domain("{domain}") {{\n\n{new_rules_str}}}\n'
        
    # Safely strip targets from inside the isolated domain block
    for rule in rules:
        inner = clean_css_rules(inner, rule['selector'])
        
    # Clean whitespace boundaries
    inner = inner.strip('\n')
    inner = re.sub(r'\n{3,}', '\n\n', inner)
    if inner:
        inner = f"\n    {inner}\n\n"
    else:
        inner = "\n\n"
        
    return f"{prefix}{inner}{new_rules_str}{suffix}"

def generate_preview_css(domain: str, rules: list[dict[str, str]]) -> str:
    css_parts = [f'@-moz-document domain("{domain}") {{\n\n']
    
    for idx, rule in enumerate(rules):
        role_data = ROLES[rule['role']]
        css_value = DEBUG_COLORS[idx % len(DEBUG_COLORS)]
        
        css_parts.append(f"    /* [NEW] {role_data['name']} */\n")
        css_parts.append(f"    {rule['selector']} {{\n")
        css_parts.append(f"        {role_data['prop']}: {css_value} !important;\n")
        css_parts.append("    }\n\n")
        
    css_parts.append("}\n")
    return "".join(css_parts)

# =============================================================================
# ▼ UTILITY FUNCTIONS ▼
# =============================================================================

def extract_domain(raw_input: str) -> str:
    raw_input = raw_input.strip()
    if not raw_input.startswith(('http://', 'https://')):
        raw_input = 'https://' + raw_input
        
    parsed = urlparse(raw_input)
    domain = parsed.netloc.split(':')[0]
    domain = re.sub(r'[^\w.-]', '', domain)
    
    if domain.startswith('www.'):
        domain = domain[4:]
        
    return domain

def copy_to_clipboard(text: str) -> None:
    try:
        subprocess.run(['wl-copy'], input=text, text=True, check=True, capture_output=True)
        console.print("[bold green]📋 Hot Preview automatically copied to clipboard! (via wl-copy)[/]")
        return
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    try:
        subprocess.run(['xclip', '-selection', 'clipboard'], input=text, text=True, check=True, capture_output=True)
        console.print("[bold green]📋 Hot Preview automatically copied to clipboard! (via xclip)[/]")
        return
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    console.print("[bold yellow]⚠️ Could not automatically copy. Please install `wl-clipboard` or `xclip`.[/]")

def show_instructions() -> None:
    intro_text = (
        "[bold cyan]How to copy the correct CSS Selector:[/]\n"
        "1. Right-click the element on the website and click [bold]Inspect (Q)[/].\n"
        "2. Hover over the HTML lines in the DevTools until the target element highlights.\n"
        "3. Right-click that highlighted HTML line.\n"
        "4. Click [bold]Copy[/] -> [bold]CSS Selector[/].\n"
        "5. Paste it below!"
    )
    console.print(Panel(intro_text, title="[bold magenta]Quick Start Guide[/]", border_style="cyan"))

def print_menu() -> None:
    table = Table(show_header=True, header_style="bold magenta", border_style="dim")
    table.add_column("Key", style="cyan", justify="center")
    table.add_column("Role / Element", style="white")
    
    for key, data in ROLES.items():
        table.add_row(f"[{key}]", data['name'])
        
    console.print(table)

# =============================================================================
# ▼ MAIN EXECUTION ▼
# =============================================================================

def main() -> None:
    console.clear()
    console.print(Panel.fit("=== Dusky Dynamic Theme Builder ===", style="bold magenta"))
    show_instructions()
    
    raw_domain = Prompt.ask("\n[bold cyan]Enter the website domain or paste the URL[/] [dim](e.g., https://x.com/)[/]").strip()
    domain = extract_domain(raw_domain)
    
    if not domain:
        console.print("[bold red]Valid domain is required. Exiting.[/]")
        return
        
    console.print(f"[*] Targeting domain: [bold green]{domain}[/]")
    
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    file_path = CONFIG_DIR / f"{domain}.css"
    existing_content = ""
    
    if file_path.exists():
        existing_content = file_path.read_text(encoding="utf-8")
        console.print(f"[bold yellow]⚡ Existing template found! New rules will be cleanly merged into it.[/]\n")
    else:
        console.print("\n")

    collected_rules: list[dict[str, str]] = []
    
    while True:
        console.print("[dim]" + "━"*40 + "[/]")
        selector = Prompt.ask("[bold cyan]Paste the CSS selector[/] [dim](or press Enter to finish)[/]").strip()
        
        if not selector:
            break
            
        if existing_content and selector in existing_content:
            console.print(f"[bold yellow]⚠ Note: '{selector}' was detected in your template. It will be seamlessly updated.[/]")
            
        print_menu()
        role_choice = Prompt.ask("[bold cyan]Select the role[/]", choices=list(ROLES.keys()))
        
        if role_choice in ROLES:
            collected_rules.append({
                "selector": selector,
                "role": role_choice
            })
            console.print(f"[bold green]✔ Added rule for {ROLES[role_choice]['name']}[/]")
        else:
            console.print("[bold red]✖ Invalid choice. Rule skipped.[/]")

    if not collected_rules:
        console.print("\n[bold yellow]No rules collected. Exiting.[/]")
        return

    # Generate Templates safely
    production_css = generate_production_css(domain, collected_rules, existing_content)
    preview_css = generate_preview_css(domain, collected_rules)

    # Render Preview
    console.print("\n")
    preview_panel = Panel(
        f"[bold white]{preview_css}[/]", 
        title="[bold yellow]🎨 HOT PREVIEW (New Rules Only)[/]", 
        border_style="yellow",
        subtitle="[dim]Paste these into Stylus to test your newly added elements[/]"
    )
    console.print(preview_panel)
    
    copy_to_clipboard(preview_css)

    console.print("\n[dim]Your Managing/Production Template uses dynamic var(--...) variables.[/]")
    save = Confirm.ask(f"Do you want to apply and save this to [bold]{file_path}[/]?")

    if not save:
        console.print("\n[bold cyan]Here is your Full Merged Production Code to copy manually:[/]\n")
        console.print(production_css)
    else:
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(production_css)
            console.print(f"\n[bold green]✔ Success! Template beautifully updated at:[/] {file_path}")
            console.print("[bold cyan]You can now open your Dusky TUI Manager to enable and deploy it.[/]")
        except OSError as e:
            console.print(f"\n[bold red]✖ Error saving file: {e}[/]")
            console.print("[bold cyan]Here is your Merged Production Code instead:[/]\n")
            console.print(production_css)

if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\n\nExiting Theme Builder. Goodbye!")
        sys.exit(0)
