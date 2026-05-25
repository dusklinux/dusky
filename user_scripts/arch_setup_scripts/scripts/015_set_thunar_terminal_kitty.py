#!/usr/bin/env python3
"""
Advanced Thunar UCA (Custom Actions) Terminal Configurator.
Optimized for Python 3.14 (requires 3.11+).

Environment: Arch Linux / Hyprland / UWSM
Author: Systems Architect / Elite DevOps Edition
"""

import argparse
import os
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Final, NoReturn

# Enforce modern Python requirements
if sys.version_info < (3, 11):
    sys.exit("\033[0;31m[ERROR]\033[0m This script requires Python 3.11 or newer.")

# ─────────────────────────────────────────────────────────────
# TTY-Aware UI & OS Integration (Stream-isolated)
# ─────────────────────────────────────────────────────────────
USE_COLOR_OUT: Final[bool] = sys.stdout.isatty()
USE_COLOR_ERR: Final[bool] = sys.stderr.isatty()

def c_wrap(text: str, color_code: str, bold: bool = False, use_color: bool = USE_COLOR_OUT) -> str:
    """Wraps text in ANSI escape codes safely, respecting the target stream's TTY status."""
    if not use_color:
        return text
    prefix = "\033[1;" if bold else "\033[0;"
    return f"{prefix}{color_code}m{text}\033[0m"

def log_info(msg: str) -> None:
    print(f"{c_wrap('==>', '34', bold=True, use_color=USE_COLOR_OUT)} {msg}")

def log_step(msg: str) -> None:
    print(f"  {c_wrap('->', '34', bold=True, use_color=USE_COLOR_OUT)} {msg}")

def log_success(msg: str) -> None:
    print(f"{c_wrap('==>', '32', bold=True, use_color=USE_COLOR_OUT)} {c_wrap(msg, '32', use_color=USE_COLOR_OUT)}")

def log_warn(msg: str) -> None:
    prefix = c_wrap('==>', '33', bold=True, use_color=USE_COLOR_ERR)
    label = c_wrap('WARNING:', '33', bold=True, use_color=USE_COLOR_ERR)
    print(f"{prefix} {label} {msg}", file=sys.stderr)

def log_error(msg: str) -> None:
    prefix = c_wrap('==>', '31', bold=True, use_color=USE_COLOR_ERR)
    label = c_wrap('ERROR:', '31', bold=True, use_color=USE_COLOR_ERR)
    print(f"{prefix} {label} {msg}", file=sys.stderr)

def fatal_error(msg: str, exit_code: int = 1) -> NoReturn:
    log_error(msg)
    sys.exit(exit_code)

# ─────────────────────────────────────────────────────────────
# Core Logic
# ─────────────────────────────────────────────────────────────
class ThunarUCAConfigurator:
    """Encapsulates the logic for safely managing Thunar's Custom Actions XML."""
    
    ACTION_NAME: Final[str] = "Open Terminal Here"
    
    # Used for the interactive prompt mapping
    DEFAULT_TERMINALS: Final[list[str]] = [
        "foot", "kitty", "alacritty", "wezterm", "ghostty", "konsole"
    ]

    # Command-line injection templates based on the specific terminal specifications
    # Add new terminals here if they require specialized arguments in the future.
    TERMINAL_ARGS: Final[dict[str, str]] = {
        "kitty": "kitty --working-directory %f",
        "alacritty": "alacritty --working-directory %f",
        "wezterm": "wezterm start --cwd %f",
        "ghostty": "ghostty --working-directory=%f",
        "foot": "foot --working-directory=%f",
        "konsole": "konsole --workdir %f",
        "gnome-terminal": "gnome-terminal --working-directory=%f",
        "xfce4-terminal": "xfce4-terminal --working-directory=%f"
    }

    def __init__(self) -> None:
        self.xdg_config_home = self._get_xdg_config_home()
        self.thunar_dir = self.xdg_config_home / "Thunar"
        self.uca_file = self.thunar_dir / "uca.xml"
        
        if os.geteuid() == 0:
            fatal_error("This script manages user dotfiles. Do not run as root/sudo.")

    @staticmethod
    def _get_xdg_config_home() -> Path:
        """Resolve XDG_CONFIG_HOME strictly conforming to XDG specs."""
        xdg_config = os.getenv("XDG_CONFIG_HOME")
        if xdg_config and xdg_config.strip() and Path(xdg_config).is_absolute():
            return Path(xdg_config)
        return Path.home() / ".config"

    @staticmethod
    def _generate_uid() -> str:
        """Generates a pseudo-unique ID matching Thunar's expectations."""
        return f"{str(time.time_ns())[:16]}-1"

    @classmethod
    def _get_terminal_command(cls, term_name: str) -> str:
        """Retrieves the exact templated command for the terminal."""
        cmd_template = cls.TERMINAL_ARGS.get(term_name.lower())
        if cmd_template:
            return cmd_template
        # Fallback for completely unknown terminals
        return f"{term_name} %f"

    def prompt(self) -> str:
        """Interactive terminal selector leveraging structural pattern matching."""
        log_info("No terminal flag provided. Interactive mode activated.\n")
        
        custom_idx = len(self.DEFAULT_TERMINALS) + 1
        
        for i, term in enumerate(self.DEFAULT_TERMINALS, start=1):
            installed = shutil.which(term) is not None
            marker_color = "32" if installed else "31"
            marker_text = "[Installed]" if installed else "[Not Found in $PATH]"
            installed_marker = c_wrap(marker_text, marker_color, use_color=USE_COLOR_OUT)
            
            print(f"  {c_wrap(str(i) + ')', '34', bold=True, use_color=USE_COLOR_OUT)} {term:<18} {installed_marker}")
            
        print(f"  {c_wrap(str(custom_idx) + ')', '34', bold=True, use_color=USE_COLOR_OUT)} Custom (Input raw terminal name)")

        while True:
            try:
                print()
                choice = input(f"{c_wrap('::', '34', bold=True, use_color=USE_COLOR_OUT)} Select target terminal for Thunar (1-{custom_idx}): ").strip()
                
                if not choice:
                    continue

                match choice:
                    case c if c.isdigit() and 1 <= int(c) <= len(self.DEFAULT_TERMINALS):
                        return self.DEFAULT_TERMINALS[int(c) - 1]
                    
                    case c if c.isdigit() and int(c) == custom_idx:
                        custom_term = input(f"{c_wrap('::', '34', bold=True, use_color=USE_COLOR_OUT)} Enter terminal executable (e.g., 'st'): ").strip()
                        if custom_term:
                            return custom_term
                        log_warn("Input cannot be empty.")
                    
                    case c if any(c.lower() == t.lower() for t in self.DEFAULT_TERMINALS):
                        return next(t for t in self.DEFAULT_TERMINALS if t.lower() == c.lower())
                    
                    case _:
                        log_warn("Invalid input. Please select a valid number or terminal name.")

            except (KeyboardInterrupt, EOFError):
                print()
                sys.exit(130)

    def configure_terminal(self, term_name: str) -> None:
        """Safely parses, modifies, and commits changes to uca.xml."""
        cmd = self._get_terminal_command(term_name)
        log_info(f"Targeting action command: {c_wrap(cmd, '32', bold=True)}")
        
        if not shutil.which(term_name):
            log_warn(f"'{term_name}' was not found in $PATH. The action may fail if not installed.")

        self.thunar_dir.mkdir(parents=True, exist_ok=True)

        # 1. Handle missing configuration entirely
        if not self.uca_file.is_file():
            log_info(f"Configuration file not found. Creating new {self.uca_file.name}...")
            root = ET.Element("actions")
            self._append_action(root, term_name, cmd)
            self._write_xml(root)
            return

        # 2. Parse existing configuration
        log_info(f"Found existing {self.uca_file.name}. Validating tree structure...")
        try:
            tree = ET.parse(self.uca_file)
            root = tree.getroot()
        except ET.ParseError as e:
            log_warn(f"Malformed XML detected ({e}). Attempting to backup and recreate...")
            backup_path = self.uca_file.with_suffix(".xml.bak")
            self.uca_file.rename(backup_path)
            log_step(f"Corrupted file backed up to {backup_path.name}")
            root = ET.Element("actions")
            self._append_action(root, term_name, cmd)
            self._write_xml(root)
            return

        # 3. Search and modify, or inject
        action_found = False
        for action in root.findall("action"):
            name_elem = action.find("name")
            if name_elem is not None and name_elem.text == self.ACTION_NAME:
                cmd_elem = action.find("command")
                desc_elem = action.find("description")
                
                # Deterministic update
                if cmd_elem is not None:
                    cmd_elem.text = cmd
                else:
                    cmd_elem = ET.SubElement(action, "command")
                    cmd_elem.text = cmd
                    
                if desc_elem is not None:
                    desc_elem.text = f"Open {term_name} in current folder"
                    
                action_found = True
                log_step(f"Updated existing '{self.ACTION_NAME}' action configuration.")
                break
                
        if not action_found:
            log_step(f"Action '{self.ACTION_NAME}' missing. Injecting new block...")
            self._append_action(root, term_name, cmd)

        self._write_xml(root)

    def _append_action(self, root: ET.Element, term_name: str, cmd: str) -> None:
        """Constructs a standard Thunar compliant XML sub-element block."""
        action = ET.SubElement(root, "action")
        
        ET.SubElement(action, "icon").text = "utilities-terminal"
        ET.SubElement(action, "name").text = self.ACTION_NAME
        ET.SubElement(action, "submenu").text = ""
        ET.SubElement(action, "unique-id").text = self._generate_uid()
        ET.SubElement(action, "command").text = cmd
        ET.SubElement(action, "description").text = f"Open {term_name} in current folder"
        ET.SubElement(action, "range").text = ""
        ET.SubElement(action, "patterns").text = "*"
        ET.SubElement(action, "startup-notify")
        ET.SubElement(action, "directories")

    def _write_xml(self, root: ET.Element) -> None:
        """Executes a fully atomic write avoiding partial configuration state."""
        ET.indent(root, space="    ", level=0)
        tree = ET.ElementTree(root)
        
        temp_file = self.uca_file.with_suffix(".xml.tmp")
        
        try:
            tree.write(temp_file, encoding="UTF-8", xml_declaration=True)
            
            # Post-write validation
            if temp_file.stat().st_size == 0:
                fatal_error("Generated XML block is empty. Aborting to prevent data loss.")
                
            temp_file.replace(self.uca_file)
            log_success(f"Thunar custom actions configured successfully!")
            
        except OSError as e:
            if temp_file.exists():
                temp_file.unlink()
            fatal_error(f"Failed atomic write operation: {e}")

# ─────────────────────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Configure the default 'Open Terminal Here' action in Thunar File Manager.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-t", "--terminal", 
        type=str,
        default=None,
        help="Name of the terminal (e.g., foot, kitty, alacritty)"
    )
    
    args = parser.parse_args()
    configurator = ThunarUCAConfigurator()

    selected_terminal = args.terminal if args.terminal else configurator.prompt()
    
    if not selected_terminal:
        fatal_error("Execution aborted. No terminal selected.")

    configurator.configure_terminal(selected_terminal)

if __name__ == "__main__":
    main()
