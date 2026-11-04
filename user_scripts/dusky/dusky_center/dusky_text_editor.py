#!/usr/bin/env python3
"""
dusky_text_editor.py: Dynamic text editor launcher for the Dusky Control Center.
Parses default_apps.lua and wraps terminal editors in the default terminal emulator.
Executes via os.execvp to replace the process cleanly with dusky-run.
"""

import os
import re
import shlex
import sys
from pathlib import Path

CONF_VARS = Path.home() / ".config/hypr/edit_here/source/default_apps.lua"
DEFAULT_EDITOR = "mousepad"
DEFAULT_TERMINAL = "kitty"
TERMINAL_EDITORS = {"nvim", "nano", "hx", "helix", "micro", "vi", "vim"}

def parse_config() -> tuple[str, str]:
    """Parses textEditor and terminal values from default_apps.lua."""
    editor = DEFAULT_EDITOR
    terminal = DEFAULT_TERMINAL
    
    if not CONF_VARS.is_file():
        return editor, terminal
        
    try:
        content = CONF_VARS.read_text(encoding="utf-8")
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("--"):
                continue
            
            editor_match = re.search(r'(?:local\s+)?textEditor\s*=\s*[\'"]([^\'"]+)[\'"]', stripped)
            if editor_match:
                val = editor_match.group(1).strip()
                if val:
                    editor = val

            terminal_match = re.search(r'(?:local\s+)?terminal\s*=\s*[\'"]([^\'"]+)[\'"]', stripped)
            if terminal_match:
                val = terminal_match.group(1).strip()
                if val:
                    terminal = val
    except Exception as e:
        sys.stderr.write(f"dusky-text-editor: error reading config: {e}\n")
        
    return editor, terminal

def main():
    editor, terminal = parse_config()
    args = sys.argv[1:]
    try:
        editor_args = shlex.split(editor)
        terminal_args = shlex.split(terminal)
    except ValueError as error:
        sys.exit(f"dusky-text-editor: invalid editor/terminal command: {error}")
    if not editor_args or not terminal_args:
        sys.exit("dusky-text-editor: editor and terminal commands must not be empty")
    editor_name = Path(editor_args[0]).name.lower()
    
    # Check if the chosen editor requires a terminal window
    is_term_editor = editor_name in TERMINAL_EDITORS
    
    # Base dusky-run command
    cmd_args = ["dusky-run"]
    
    if is_term_editor:
        # Launch terminal editor inside terminal wrapper
        term_lower = Path(terminal_args[0]).name.lower()
        cmd_args.extend(terminal_args)
        if term_lower == "kitty":
            cmd_args.extend(["--class", editor_name])
        elif term_lower in {"foot", "footclient"}:
            cmd_args.extend(["--app-id", editor_name])
        elif term_lower == "alacritty":
            cmd_args.extend(["--class", editor_name, "-e"])
        elif term_lower == "wezterm":
            cmd_args.extend(["start", "--class", editor_name, "--"])
        else:
            # Fallback for generic terminal wrappers
            cmd_args.append("-e")
        cmd_args.extend(editor_args)
    else:
        # Launch GUI editor directly
        cmd_args.extend(editor_args)
        
    # Append the target file paths
    cmd_args.extend(args)
    
    try:
        # Replaces the current process with dusky-run cleanly (no Python overhead remains)
        os.execvp(cmd_args[0], cmd_args)
    except Exception as e:
        sys.stderr.write(f"dusky-text-editor: failed to execute {cmd_args[0]}: {e}\n")
        sys.exit(1)

if __name__ == "__main__":
    main()
