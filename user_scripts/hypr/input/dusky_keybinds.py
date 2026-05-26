#!/usr/bin/env python3
# ==============================================================================
# Description: Advanced TUI for Hyprland 0.55+ Lua Keybinds
#              - Lexically perfect parser (supports multiline functions/closures)
#              - Auto-installs dependencies via Arch Linux pacman
#              - Dynamic System $EDITOR detection with safe fallbacks
#              - Synchronous UI rendering natively handled by Rich
# ==============================================================================

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

# ──────────────────────────────────────────────────────────────────────────────
# Pre-flight Dependency Setup (Arch Native)
# ──────────────────────────────────────────────────────────────────────────────
def ensure_dependencies() -> None:
    try:
        import rich
    except ImportError:
        print("\033[0;33m[!] Missing required dependency: 'rich' for advanced syntax formatting.\033[0m")
        print("\033[0;36m[*] Auto-installing via pacman (you may be prompted for your sudo password)...\033[0m")
        try:
            subprocess.run(['sudo', 'pacman', '-S', 'python-rich', '--needed', '--noconfirm'], check=True)
            print("\033[0;32m[+] Successfully installed python-rich. Booting...\033[0m")
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except subprocess.CalledProcessError:
            print("\033[0;31m[-] Failed to install python-rich automatically.\033[0m")
            print("Please install it manually: sudo pacman -S python-rich")
            sys.exit(1)
        except KeyboardInterrupt:
            print("\n\033[0;31m[-] Installation aborted by user.\033[0m")
            sys.exit(1)

ensure_dependencies()

from rich.console import Console, Group
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

console = Console()

# ──────────────────────────────────────────────────────────────────────────────
# ANSI Colours for FZF (FZF strictly requires raw ANSI)
# ──────────────────────────────────────────────────────────────────────────────
BLUE         = '\033[0;34m'
GREEN        = '\033[0;32m'
YELLOW       = '\033[0;33m'
RED          = '\033[0;31m'
CYAN         = '\033[0;36m'
PURPLE       = '\033[0;35m'
GREY         = '\033[0;90m'
BOLD         = '\033[1m'
RESET        = '\033[0m'
DIM          = '\033[2m'

# ──────────────────────────────────────────────────────────────────────────────
# Paths (XDG Compliant)
# ──────────────────────────────────────────────────────────────────────────────
HOME = Path.home()
XDG_CONFIG_HOME = Path(os.environ.get('XDG_CONFIG_HOME', HOME / '.config'))

SOURCE_LUA = XDG_CONFIG_HOME / 'hypr/source/keybinds.lua'
CUSTOM_LUA = XDG_CONFIG_HOME / 'hypr/edit_here/source/keybinds.lua'

VIEW_ONLY = False
EMPTY_TEMPLATE = 'hl.bind("SUPER + ", hl.dsp.exec_cmd(""), { description = "" })'

# ==============================================================================
# Data Structures
# ==============================================================================

@dataclass
class Bind:
    key_str:     str   
    norm_mods:   str   
    norm_key:    str   
    dispatcher:  str   
    options:     str   
    description: str   
    submap:      str   
    raw_call:    str   
    origin:      str   
    char_start:  int   
    char_end:    int   
    is_unbind:   bool = False

# ==============================================================================
# System Utilities
# ==============================================================================

def die(msg: str) -> None:
    console.print(f"[bold red][FATAL][/bold red] {msg}")
    sys.exit(1)

def atomic_write(content: str, path: Path) -> None:
    real = path.resolve()
    real.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=real.parent, prefix='.keybinds_write_')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            fh.write(content)
        try:
            if real.exists():
                os.chmod(tmp, real.stat().st_mode)
        except FileNotFoundError:
            pass
        os.replace(tmp, real)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise

def reload_hyprland() -> None:
    if not os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'):
        console.print("[dim]Not running under Hyprland; skipping reload.[/dim]")
        return
    if not shutil.which('hyprctl'): return
    result = subprocess.run(['hyprctl', 'reload'], capture_output=True, text=True)
    if result.returncode == 0:
        console.print(Panel("Hyprland configuration successfully reloaded.", style="bold green", expand=False))
    else:
        out = (result.stdout or result.stderr or '').strip()
        console.print(Panel(f"Hyprland reload issue:\n{out}\n\nKeybind was saved. Reload manually or restart Hyprland.", title="[bold yellow]WARNING[/bold yellow]", border_style="yellow", expand=False))

def edit_in_editor(initial_text: str, editor_choice: str) -> Optional[str]:
    """Handles spawning the chosen editor (Native/Nano/Inline). Returns None on abort/error."""
    if editor_choice == 'inline':
        console.print("\n[bold yellow]Inline Editing Mode[/bold yellow]")
        console.print("Enter your new Lua code. Press [bold cyan]Ctrl+D[/bold cyan] on a new empty line when finished.")
        console.print("[dim]Original code for reference:[/dim]")
        console.print(Syntax(initial_text, "lua", theme="monokai", background_color="default"))
        
        lines = []
        try:
            for line in sys.stdin:
                lines.append(line)
        except EOFError:
            pass
        except KeyboardInterrupt:
            return None
        return "".join(lines).strip()

    cmd = shlex.split(editor_choice)
    with tempfile.NamedTemporaryFile(mode='w+', suffix='.lua', delete=False, encoding='utf-8') as tf:
        tf.write(initial_text)
        tf.flush()
        filepath = tf.name
        
    try:
        cmd.append(filepath)
        subprocess.run(cmd, check=True)
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except subprocess.CalledProcessError:
        return None
    except KeyboardInterrupt:
        return None
    finally:
        if os.path.exists(filepath):
            os.remove(filepath)

def get_editor_choice() -> str:
    """Dynamically probes for $EDITOR before offering fallbacks."""
    sys_editor = os.environ.get('EDITOR')
    
    console.print("\n[bold yellow]Select Editor:[/bold yellow]")
    if sys_editor:
        console.print(f"  [bold white]\\[1][/bold white] [cyan]System Default[/cyan] ({sys_editor})")
        primary_cmd = sys_editor
    else:
        console.print("  [bold white]\\[1][/bold white] [cyan]Neovim[/cyan] (nvim)")
        primary_cmd = 'nvim'
        
    console.print("  [bold white]\\[2][/bold white] [cyan]Nano[/cyan] (nano)")
    console.print("  [bold white]\\[3][/bold white] [cyan]Terminal Inline[/cyan] (Standard Input)")
    
    while True:
        choice = console.input("\n[bold cyan]Select [1/2/3] > [/bold cyan]").strip().lower()
        if choice == '1': return primary_cmd
        if choice in ('2', 'nano'): return 'nano'
        if choice in ('3', 'inline', 't'): return 'inline'
        # Secondary fallback if the user types 'nvim' natively
        if choice in ('nvim', 'vim', 'n'): return 'nvim'

# ==============================================================================
# Robust Lua Lexical Parsing (Untouched Core Logic)
# ==============================================================================

def get_long_bracket_end(code: str, start_idx: int) -> int:
    q = start_idx + 1
    while q < len(code) and code[q] == '=': q += 1
    if q < len(code) and code[q] == '[':
        eq_count = q - start_idx - 1
        end_seq = ']' + ('=' * eq_count) + ']'
        end_idx = code.find(end_seq, q + 1)
        if end_idx != -1: return end_idx + len(end_seq)
    return -1

def build_active_code_mask(code: str) -> list[bool]:
    mask = [True] * len(code)
    i, n = 0, len(code)
    while i < n:
        ch = code[i]
        if ch in ('"', "'"):
            mask[i] = False
            quote = ch
            i += 1
            while i < n:
                mask[i] = False
                if code[i] == '\\':
                    i += 1
                    if i < n: mask[i] = False
                elif code[i] == quote:
                    i += 1
                    break
                i += 1
        elif ch == '-' and i + 1 < n and code[i+1] == '-':
            mask[i], mask[i+1] = False, False
            i += 2
            if i < n and code[i] == '[':
                lb_end = get_long_bracket_end(code, i)
                if lb_end != -1:
                    while i < lb_end:
                        mask[i] = False
                        i += 1
                    continue
            while i < n and code[i] != '\n':
                mask[i] = False
                i += 1
        elif ch == '[':
            lb_end = get_long_bracket_end(code, i)
            if lb_end != -1:
                while i < lb_end:
                    mask[i] = False
                    i += 1
                continue
            i += 1
        else:
            i += 1
    return mask

def find_balanced_end(text: str, open_pos: int, mask: list[bool]) -> int:
    depth = 0
    for i in range(open_pos, len(text)):
        if not mask[i]: continue
        if text[i] == '(': depth += 1
        elif text[i] == ')':
            depth -= 1
            if depth == 0: return i + 1
    return len(text)

def split_top_args(text: str, start_idx: int, end_idx: int, mask: list[bool]) -> list[str]:
    args, buf, depth = [], [], 0
    for i in range(start_idx, end_idx):
        ch = text[i]
        if not mask[i]:
            buf.append(ch)
            continue
        if ch in ('(', '[', '{'):
            depth += 1
            buf.append(ch)
        elif ch in (')', ']', '}'):
            depth -= 1
            buf.append(ch)
        elif ch == ',' and depth == 0:
            args.append(''.join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf: args.append(''.join(buf).strip())
    return args

def strip_quotes(arg: str) -> str:
    arg = arg.strip()
    if arg.startswith('"') and arg.endswith('"'): return arg[1:-1]
    if arg.startswith("'") and arg.endswith("'"): return arg[1:-1]
    m = re.match(r'^\[(=*)\[(.*)\]\1\]$', arg, re.DOTALL)
    if m: return m.group(2)
    return arg

def extract_local_vars(text: str, mask: list[bool]) -> dict[str, str]:
    result = {}
    for m in re.finditer(r'local\s+(\w+)\s*=\s*"([^"]*)"', text):
        if mask[m.start()]: result[m.group(1)] = m.group(2)
    for m in re.finditer(r"local\s+(\w+)\s*=\s*'([^']*)'", text):
        if mask[m.start()]: result[m.group(1)] = m.group(2)
    return result

def resolve_key_arg(arg: str, local_vars: dict[str, str]) -> str:
    if '..' in arg:
        parts = [p.strip() for p in arg.split('..')]
        pieces = []
        for part in parts:
            if part.startswith('"') or part.startswith("'") or part.startswith('['):
                pieces.append(strip_quotes(part))
            elif part in local_vars:
                pieces.append(local_vars[part])
            else:
                pieces.append('SUPER')
        return ''.join(pieces)
    if not (arg.startswith('"') or arg.startswith("'") or arg.startswith('[')):
        return local_vars.get(arg, arg)
    return strip_quotes(arg)

def extract_description(options: str) -> str:
    for m in (re.search(r'description\s*=\s*"([^"]*)"', options),
              re.search(r"description\s*=\s*'([^']*)'", options)):
        if m: return m.group(1)
    return ''

_MOD_ALIASES = {
    'ctrl_l': 'ctrl',   'ctrl_r': 'ctrl',   'control': 'ctrl',
    'super_l': 'super', 'super_r': 'super', 'mod4': 'super',
    'alt_l': 'alt',     'alt_r': 'alt',     'mod1': 'alt',
    'shift_l': 'shift', 'shift_r': 'shift',
}

def normalize_key(key_str: str) -> tuple[str, str]:
    if '+' not in key_str: return '', key_str.strip().lower()
    parts = key_str.split('+')
    clean_parts = [p.strip().lower() for p in parts]
    if len(clean_parts) >= 2 and clean_parts[-1] == '':
        clean_parts = clean_parts[:-2] + ['+']
    clean_parts = [_MOD_ALIASES.get(p, p) for p in clean_parts if p]
    if not clean_parts: return '', ''
    if len(clean_parts) == 1: return '', clean_parts[0]
    mods = sorted(set(clean_parts[:-1]))
    key = clean_parts[-1]
    return '+'.join(mods), key

def find_submap_regions(text: str, mask: list[bool]) -> list[tuple[int, int, str]]:
    regions = []
    for m in re.finditer(r'hl\.define_submap\s*\(', text):
        start = m.start()
        if not mask[start]: continue
        paren_pos = text.find('(', start)
        end = find_balanced_end(text, paren_pos, mask)
        args = split_top_args(text, paren_pos + 1, end - 1, mask)
        if args: regions.append((start, end, strip_quotes(args[0])))
    return regions

def parse_lua_file_content(text: str, origin: str) -> list[Bind]:
    mask = build_active_code_mask(text)
    submap_regions = find_submap_regions(text, mask)
    local_vars = extract_local_vars(text, mask)
    binds = []

    for m in re.finditer(r'hl\.(bind|unbind)\s*\(', text):
        start = m.start()
        if not mask[start]: continue

        is_unbind = (m.group(1) == 'unbind')
        paren_pos = text.find('(', start)
        end = find_balanced_end(text, paren_pos, mask)
        
        args = split_top_args(text, paren_pos + 1, end - 1, mask)
        if not args: continue

        key_arg = resolve_key_arg(args[0], local_vars)
        if is_unbind:
            dispatcher, options, description = "UNBIND", "", "Source Bind Disabled"
        else:
            if len(args) < 2: continue
            dispatcher = args[1].strip()
            options = args[2].strip() if len(args) > 2 else ''
            description = extract_description(options)
        
        norm_mods, norm_key = normalize_key(key_arg)
        submap = ''
        for s, e, name in submap_regions:
            if s < start < e:
                submap = name
                break

        binds.append(Bind(key_str=key_arg, norm_mods=norm_mods, norm_key=norm_key,
                          dispatcher=dispatcher, options=options, description=description,
                          submap=submap, raw_call=text[start:end], origin=origin,
                          char_start=start, char_end=end, is_unbind=is_unbind))
    return binds

def _preceding_comment_start(text: str, block_start: int) -> int:
    line_start = text.rfind('\n', 0, block_start) + 1
    if line_start > 0:
        prev_nl = text.rfind('\n', 0, line_start - 1)
        prev_start = prev_nl + 1
        prev_line = text[prev_start: line_start - 1].strip()
        if re.match(r'^--\s*\[\d{4}-\d{2}-\d{2}', prev_line): return prev_start
    return line_start

def filter_out_bind_from_text(text: str, norm_mods: str, norm_key: str, submap: str) -> str:
    binds = parse_lua_file_content(text, "CUST")
    to_remove = []
    
    for b in binds:
        if b.norm_mods == norm_mods and b.norm_key == norm_key and b.submap == submap:
            blk_start = _preceding_comment_start(text, b.char_start)
            blk_end = b.char_end
            if blk_end < len(text) and text[blk_end] == '\n': blk_end += 1
            to_remove.append((blk_start, blk_end))

    if not to_remove: return text
    
    to_remove.sort(reverse=True)
    for start, end in to_remove:
        text = text[:start] + text[end:]
    return text

# ==============================================================================
# UI Displays (Rich Library)
# ==============================================================================

def print_binding_info_box(bind: Bind, raw_text: str) -> None:
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Property", style="bold cyan")
    table.add_column("Value", style="bold white")
    
    table.add_row("Key Comb:", f"[green]{bind.key_str}[/green]")
    if bind.submap: table.add_row("Submap:", f"[purple]{bind.submap}[/purple]")
    table.add_row("Action:", f"[cyan]{bind.dispatcher}[/cyan]")
    
    desc = bind.description if bind.description else "[dim]No Description[/dim]"
    table.add_row("Info:", desc)

    syntax = Syntax(raw_text, "monokai", background_color="default", word_wrap=True)
    
    group = Group(
        table,
        "\n[bold blue]─── Raw Lua Code ───────────────────────────────────────────────[/bold blue]",
        syntax
    )
    
    panel = Panel(group, title="[bold blue] CURRENT BINDING INFO [/bold blue]", border_style="blue", expand=False)
    console.print(panel)

def format_display(b: Bind) -> str:
    submap_pfx = f'{PURPLE}[{b.submap}]{RESET} ' if b.submap else ''
    ui_key = b.key_str[:32].ljust(32).replace('\n', ' ').replace('\t', ' ')

    if b.is_unbind:
        return f'{RED}[UNB]{RESET}  {submap_pfx}{RED}{ui_key}{RESET} {GREY}│{RESET} {DIM}Source Bind Disabled{RESET}'

    tag = f'{GREEN}[CUST]{RESET}' if b.origin == 'CUST' else f'{BLUE}[SRC] {RESET}'
    ui_desc = (b.description if b.description else "No Description").replace('\n', ' ').replace('\t', ' ')
    
    return f'{tag}  {submap_pfx}{BOLD}{ui_key}{RESET} {GREY}│{RESET} {ui_desc}'

def generate_bind_rows(source_binds: list[Bind], custom_binds: list[Bind]) -> tuple[list[str], list[Bind]]:
    custom_active = {(b.norm_mods, b.norm_key, b.submap) for b in custom_binds if not b.is_unbind}
    custom_ovr = {(b.norm_mods, b.norm_key, b.submap) for b in custom_binds}
    
    displayed = []
    for b in sorted(custom_binds, key=lambda x: f'{x.submap}|{x.norm_mods}|{x.norm_key}'):
        if b.is_unbind and (b.norm_mods, b.norm_key, b.submap) in custom_active: continue
        displayed.append(b)
        
    for b in sorted(source_binds, key=lambda x: f'{x.submap}|{x.norm_mods}|{x.norm_key}'):
        if (b.norm_mods, b.norm_key, b.submap) not in custom_ovr: displayed.append(b)

    rows = [f'{format_display(b)}\t{idx}' for idx, b in enumerate(displayed)]
    return rows, displayed

# ==============================================================================
# Core Flow Operations
# ==============================================================================

def edit_loop(bind: Optional[Bind], source_binds: list[Bind], custom_binds: list[Bind]) -> bool:
    origin = bind.origin if bind else 'NEW'
    actual_text = bind.raw_call.strip() if bind else EMPTY_TEMPLATE
    bind_submap = bind.submap if bind else ''
    orig_mods = bind.norm_mods if bind else ''
    orig_key = bind.norm_key if bind else ''

    editor_choice = get_editor_choice()

    while True:
        console.clear()
        
        user_line = edit_in_editor(actual_text, editor_choice)
        console.clear()
        
        if user_line is None:
            console.print(Panel("[bold yellow]Action Aborted:[/bold yellow] Editor was closed with an error or interrupted.", border_style="yellow", expand=False))
            console.input('\n[bold cyan]Press Enter to return...[/bold cyan]')
            return False

        if not user_line or user_line == EMPTY_TEMPLATE:
            console.print(Panel("[bold yellow]Action Aborted:[/bold yellow] Empty input or Template unchanged.", border_style="yellow", expand=False))
            console.input('\n[bold cyan]Press Enter to return...[/bold cyan]')
            return False
            
        if user_line == actual_text:
            console.print(Panel("[bold yellow]Action Aborted:[/bold yellow] No changes were made.", border_style="yellow", expand=False))
            console.input('\n[bold cyan]Press Enter to return...[/bold cyan]')
            return False

        temp_binds = parse_lua_file_content(user_line, "TMP")
        if not temp_binds:
            console.print(Panel("[bold red]Syntax Error:[/bold red] Input must contain a valid hl.bind(...) call.", border_style="red", expand=False))
            
            console.print("\n[bold yellow]Options:[/bold yellow]")
            console.print("  [bold white]\\[e][/bold white] [cyan]Return to Editor[/cyan]")
            console.print("  [bold white]\\[d][/bold white] [red]Discard and Exit[/red]")
            while True:
                ch = console.input('\n[bold cyan]Select > [/bold cyan]').strip().lower()
                if ch in ('e', 'd'): break
            if ch == 'd': return False
            if ch == 'e':
                actual_text = user_line
                continue

        new_b = temp_binds[0]
        new_mods, new_key = new_b.norm_mods, new_b.norm_key

        conflict_cust = next((b for b in custom_binds if b.norm_mods == new_mods and b.norm_key == new_key and b.submap == bind_submap and (orig_mods != new_mods or orig_key != new_key)), None)
        conflict_src = next((b for b in source_binds if b.norm_mods == new_mods and b.norm_key == new_key and b.submap == bind_submap), None)

        if conflict_cust:
            panel = Panel(Syntax(conflict_cust.raw_call, "monokai", word_wrap=True), title="[bold red] CONFLICT DETECTED (Custom Bind) [/bold red]", border_style="red", expand=False)
            console.print(panel)
            
            console.print("\n[bold yellow]Conflict Options:[/bold yellow]")
            console.print("  [bold white]\\[y][/bold white] [red]Overwrite existing custom bind[/red]")
            console.print("  [bold white]\\[e][/bold white] [cyan]Return to Editor[/cyan]")
            console.print("  [bold white]\\[d][/bold white] [dim]Discard changes[/dim]")
            while True:
                ch = console.input('\n[bold cyan]Select > [/bold cyan]').strip().lower()
                if ch in ('y', 'e', 'd'): break
            if ch == 'd': return False
            if ch == 'e':
                actual_text = user_line
                continue
                
        elif conflict_src:
            panel = Panel(Syntax(conflict_src.raw_call, "monokai", word_wrap=True), title="[bold yellow] CONFLICT DETECTED (Source Bind) [/bold yellow]", border_style="yellow", expand=False)
            console.print(panel)
            console.print("  [dim]Note: Your custom bind will safely take precedence.[/dim]")

        # ─── CONFIRMATION BOX (Rich Panel) ───
        group_items = []
        if bind_submap:
            group_items.append(f"[bold purple]Target Submap:[/bold purple] {bind_submap}")
            
        if actual_text and actual_text != EMPTY_TEMPLATE and origin != 'NEW':
            group_items.append("\n[bold red]─── OLD ────────────────────────────────────────────────────────[/bold red]")
            group_items.append(Syntax(actual_text, "monokai", background_color="default", word_wrap=True))
            
        group_items.append("\n[bold green]─── NEW ────────────────────────────────────────────────────────[/bold green]")
        group_items.append(Syntax(user_line, "monokai", background_color="default", word_wrap=True))
        
        panel = Panel(Group(*group_items), title="[bold cyan] CONFIRM CHANGES (SAVE) [/bold cyan]", border_style="cyan", expand=False)
        console.print("\n")
        console.print(panel)

        console.print("\n[bold yellow]What would you like to do?[/bold yellow]")
        console.print("  [bold white]\\[y][/bold white] [green]Confirm and Save changes[/green]")
        console.print("  [bold white]\\[e][/bold white] [cyan]Return to Editor[/cyan]")
        console.print("  [bold white]\\[d][/bold white] [red]Discard and Exit[/red]")
        
        while True:
            ch = console.input('\n[bold cyan]Select > [/bold cyan]').strip().lower()
            if ch in ('y', 'e', 'd'): break
            
        if ch == 'd': return False
        if ch == 'e':
            actual_text = user_line
            continue
            
        # Write Phase
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
        try: text = CUSTOM_LUA.read_text(encoding='utf-8')
        except FileNotFoundError: text = ''

        if orig_mods or orig_key:
            text = filter_out_bind_from_text(text, orig_mods, orig_key, bind_submap)

        key_changed = (orig_mods != new_mods) or (orig_key != new_key)
        if key_changed and bind:
            src_conflict_old = next((b for b in source_binds if b.norm_mods == orig_mods and b.norm_key == orig_key and b.submap == bind_submap), None)
            if src_conflict_old:
                unbind_stmt = f'hl.unbind("{src_conflict_old.key_str}")'
                cmt = f'-- [{timestamp}] UNBIND (Moved away from SRC key)'
                if bind_submap:
                    unbind_block = f'\n{cmt}\nhl.define_submap("{bind_submap}", function()\n    {unbind_stmt}\nend)\n'
                else:
                    unbind_block = f'\n{cmt}\n{unbind_stmt}\n'
                text = text.rstrip('\n') + '\n' + unbind_block

        if conflict_cust:
            text = filter_out_bind_from_text(text, new_mods, new_key, bind_submap)

        comment = f'-- [{timestamp}] {origin}'
        unbind_prefix = ""
        if conflict_src:
            if bind_submap: unbind_prefix = f'    hl.unbind("{conflict_src.key_str}")\n'
            else: unbind_prefix = f'hl.unbind("{conflict_src.key_str}")\n'

        if bind_submap:
            new_block = f'\n{comment}\nhl.define_submap("{bind_submap}", function()\n{unbind_prefix}{user_line}\nend)\n'
        else:
            new_block = f'\n{comment}\n{unbind_prefix}{user_line}\n'

        text = text.rstrip('\n') + '\n' + new_block
        atomic_write(text, CUSTOM_LUA)

        console.print("\n")
        console.print(Panel(f"Saved successfully to {CUSTOM_LUA}", title="[bold green]SUCCESS[/bold green]", border_style="green", expand=False))
        reload_hyprland()
        return True

def delete_flow(bind: Bind) -> bool:
    console.clear()
    
    group_items = []
    if bind.submap: 
        group_items.append(f"[bold purple]Submap:[/bold purple] {bind.submap}")
        
    if bind.origin == 'SRC':
        group_items.append("\n[bold red]Action:[/bold red] DISABLE SOURCE BIND")
        group_items.append("[dim]This dynamically appends hl.unbind() to your custom config.[/dim]")
    else:
        act_text = "RESTORE SOURCE BIND" if bind.is_unbind else "DELETE FROM CUSTOM FILE"
        group_items.append(f"\n[bold red]Action:[/bold red] {act_text}")

    group_items.append("\n[bold red]─── Target ─────────────────────────────────────────────────────[/bold red]")
    group_items.append(Syntax(bind.raw_call.strip(), "monokai", background_color="default", word_wrap=True))
    
    panel = Panel(Group(*group_items), title="[bold cyan] CONFIRM DELETION [/bold cyan]", border_style="cyan", expand=False)
    console.print(panel)
    
    console.print("\n[bold yellow]Options:[/bold yellow]")
    console.print("  [bold white]\\[y][/bold white] [red]Confirm Delete[/red]")
    console.print("  [bold white]\\[n][/bold white] [cyan]Go Back[/cyan]")
    
    if not console.input('\n[bold cyan]Select > [/bold cyan]').strip().lower().startswith('y'): return False

    try: text = CUSTOM_LUA.read_text(encoding='utf-8')
    except FileNotFoundError: text = ''

    if bind.origin == 'SRC':
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
        text = filter_out_bind_from_text(text, bind.norm_mods, bind.norm_key, bind.submap)
        comment = f'-- [{timestamp}] UNBIND SRC'
        if bind.submap:
            new_block = f'\n{comment}\nhl.define_submap("{bind.submap}", function()\n    hl.unbind("{bind.key_str}")\nend)\n'
        else:
            new_block = f'\n{comment}\nhl.unbind("{bind.key_str}")\n'

        text = text.rstrip('\n') + '\n' + new_block
        atomic_write(text, CUSTOM_LUA)
        console.print("\n")
        console.print(Panel("Source bind explicitly disabled.", title="[bold green]SUCCESS[/bold green]", border_style="green", expand=False))
    else:
        new_text = filter_out_bind_from_text(text, bind.norm_mods, bind.norm_key, bind.submap)
        atomic_write(new_text, CUSTOM_LUA)
        console.print("\n")
        if bind.is_unbind:
            console.print(Panel("Source bind safely restored.", title="[bold green]SUCCESS[/bold green]", border_style="green", expand=False))
        else:
            console.print(Panel("Keybind safely removed from custom file.", title="[bold green]SUCCESS[/bold green]", border_style="green", expand=False))

    reload_hyprland()
    return True

# ==============================================================================
# Main Execution
# ==============================================================================

def main() -> None:
    global VIEW_ONLY
    if '--view' in sys.argv: VIEW_ONLY = True

    if not shutil.which('fzf'): die("'fzf' is required but not installed.")
    CUSTOM_LUA.parent.mkdir(parents=True, exist_ok=True)
    if not CUSTOM_LUA.exists():
        CUSTOM_LUA.write_text('-- Custom Hyprland Keybinds Override File\n\n', encoding='utf-8')

    try:
        while True:
            console.clear()
            
            try: src_text = SOURCE_LUA.read_text(encoding='utf-8')
            except FileNotFoundError: src_text = ""
                
            try: cust_text = CUSTOM_LUA.read_text(encoding='utf-8')
            except FileNotFoundError: cust_text = ""

            source_binds = parse_lua_file_content(src_text, 'SRC')
            custom_binds = parse_lua_file_content(cust_text, 'CUST')

            rows, displayed = generate_bind_rows(source_binds, custom_binds)
            
            # FZF Branded Header
            brand = " Dusky Binds "
            bl = (80 - len(brand)) // 2
            br = 80 - len(brand) - bl
            fzf_brand = f"\033[0;35m{'─' * bl}\033[0m\033[1m\033[0;33m{brand}\033[0m\033[0;35m{'─' * br}\033[0m"
            fzf_header = f'{fzf_brand}\n  SELECT KEYBIND  │  SRC = Default  │  CUST = Your Override  │  [UNB] = Disabled Source Bind\n  Type to search · Enter = select · Esc = quit'

            if VIEW_ONLY:
                res = subprocess.run(['fzf', '--ansi', '--delimiter=\t', '--with-nth=1', f'--header=[VIEW MODE]\n{fzf_header}', '--info=inline', '--layout=reverse', '--border', '--prompt=Search > '], input='\n'.join(rows), capture_output=True, text=True)
                if res.returncode != 0: sys.exit(0)
                
                try: idx = int(res.stdout.strip().rsplit('\t', 1)[-1]) if res.stdout.strip() else -1
                except (ValueError, IndexError): idx = -1
                    
                if 0 <= idx < len(displayed):
                    console.clear()
                    print_binding_info_box(displayed[idx], displayed[idx].raw_call.strip())
                console.input('\n[bold cyan]Press Enter to continue...[/bold cyan]')
                continue

            create_row = f'{BOLD}[+] Create New Keybind{RESET}\t-1'
            fzf_input = create_row + '\n' + '\n'.join(rows)

            res = subprocess.run(['fzf', '--ansi', '--delimiter=\t', '--with-nth=1', f'--header={fzf_header}', '--info=inline', '--layout=reverse', '--border', '--prompt=Search > '], input=fzf_input, capture_output=True, text=True)
            if res.returncode != 0: sys.exit(0)

            selected = res.stdout.strip()
            if not selected: continue

            try: idx = int(selected.rsplit('\t', 1)[-1])
            except (ValueError, IndexError): continue

            if idx == -1:
                edit_loop(None, source_binds, custom_binds)
                continue

            if 0 <= idx < len(displayed):
                selected_bind = displayed[idx]
                
                console.clear()
                print_binding_info_box(selected_bind, selected_bind.raw_call.strip())
                
                console.print("\n[bold yellow]Available Actions:[/bold yellow]")
                console.print("  [bold white]\\[e][/bold white] [cyan]Edit this bind[/cyan]")
                console.print("  [bold white]\\[d][/bold white] [red]Delete / Unbind[/red]")
                console.print("  [bold white]\\[b][/bold white] [dim]Go Back[/dim]")
                console.print("  [bold white]\\[q][/bold white] [dim]Quit Application[/dim]")
                
                ch = console.input('\n[bold cyan]Select > [/bold cyan]').strip().lower()

                if ch.startswith('q'): sys.exit(0)
                elif ch.startswith('d'):
                    delete_flow(selected_bind)
                    console.input('\n[bold cyan]Press Enter to continue...[/bold cyan]')
                elif ch.startswith('e'):
                    if getattr(selected_bind, 'is_unbind', False):
                        console.print(Panel("Cannot directly edit an Unbind directive. Delete it to restore the source bind, or select 'Create New Keybind'.", title="[bold yellow]NOTE[/bold yellow]", border_style="yellow", expand=False))
                        console.input('\n[bold cyan]Press Enter to continue...[/bold cyan]')
                        continue
                    
                    changed = edit_loop(selected_bind, source_binds, custom_binds)
                    if changed:
                        console.print("\n[bold yellow]Options:[/bold yellow]")
                        console.print("  [bold white]\\[Enter][/bold white] [cyan]Edit another bind[/cyan]")
                        console.print("  [bold white]\\[q][/bold white] [dim]Quit[/dim]")
                        if console.input('\n[bold cyan]Select > [/bold cyan]').strip().lower().startswith('q'): sys.exit(0)

    except KeyboardInterrupt:
        console.clear()
        sys.exit(0)

if __name__ == '__main__':
    main()
