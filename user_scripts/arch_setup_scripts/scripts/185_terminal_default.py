#!/usr/bin/env python3
"""
Advanced XDG Terminal Configurator for Wayland/Hyprland.
Optimized for Python 3.14 (requires 3.11+).

Author: Systems Architect / Elite DevOps Edition
"""

import argparse
import os
import shlex
import shutil
import sys
import tomllib
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
    # Warnings are piped to stderr to prevent stdout corruption
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
class TerminalConfigurator:
    """Encapsulates the logic for configuring the XDG terminal environment."""
    
    DEFAULT_TERMINALS: Final[list[str]] = [
        "foot", "kitty", "alacritty", "wezterm", "ghostty", "konsole"
    ]

    def __init__(self) -> None:
        self.xdg_config_home = self._get_xdg_config_home()
        self.target_file = self.xdg_config_home / "xdg-terminals.list"
        self.terminals = self._load_terminals()
        self.xdg_data_dirs = self._get_xdg_data_dirs()

    @staticmethod
    def _get_xdg_config_home() -> Path:
        """Resolve XDG_CONFIG_HOME strictly conforming to XDG specs."""
        xdg_config = os.getenv("XDG_CONFIG_HOME")
        if xdg_config and xdg_config.strip() and Path(xdg_config).is_absolute():
            return Path(xdg_config)
        return Path.home() / ".config"

    @staticmethod
    def _get_xdg_data_dirs() -> list[Path]:
        """Resolve XDG_DATA_HOME and XDG_DATA_DIRS robustly per XDG specs."""
        dirs: list[Path] = []
        
        data_home = os.getenv("XDG_DATA_HOME")
        if data_home and data_home.strip() and Path(data_home).is_absolute():
            dirs.append(Path(data_home) / "applications")
        else:
            dirs.append(Path.home() / ".local" / "share" / "applications")
            
        data_dirs = os.getenv("XDG_DATA_DIRS")
        if not data_dirs or not data_dirs.strip():
            data_dirs = "/usr/local/share:/usr/share"
            
        for d in data_dirs.split(":"):
            if d and d.strip() and Path(d).is_absolute():
                dirs.append(Path(d) / "applications")
                
        return [d for d in dirs if d.is_dir()]

    def _load_terminals(self) -> list[str]:
        """Loads terminals safely, ensuring type compliance from TOML configs."""
        terms = self.DEFAULT_TERMINALS.copy()
        config_path = self.xdg_config_home / "xdg-terminal-setter" / "config.toml"
        
        if config_path.is_file():
            try:
                with config_path.open("rb") as f:
                    data = tomllib.load(f)
                    if custom_terms := data.get("terminals"):
                        if isinstance(custom_terms, list):
                            # Enforce string types to prevent silent runtime crashes
                            valid_terms = [t for t in custom_terms if isinstance(t, str)]
                            terms.extend([t for t in valid_terms if t not in terms])
            except tomllib.TOMLDecodeError as e:
                log_warn(f"Failed to parse {config_path}: {e}")
            except Exception as e:
                log_warn(f"Unexpected error reading {config_path}: {e}")
                
        return terms

    def _find_desktop_file(self, desktop_name: str) -> Path | None:
        """Locates the .desktop file in standard XDG data locations."""
        for d in self.xdg_data_dirs:
            target = d / desktop_name
            if target.is_file():
                return target
        return None

    @staticmethod
    def _get_exec_from_desktop(desktop_path: Path) -> str | None:
        """Safely parses the Exec= binary from a .desktop file, handling shell quotes."""
        try:
            for line in desktop_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("Exec="):
                    cmd = line.split("=", 1)[1].strip()
                    try:
                        tokens = shlex.split(cmd)
                        return tokens[0] if tokens else None
                    except ValueError:
                        # Fallback if shlex fails on malformed quotation marks
                        return cmd.split()[0] if cmd else None
        except (OSError, UnicodeDecodeError):
            pass
        return None

    def prompt(self) -> str:
        """Interactive prompt utilizing strict structural pattern matching."""
        log_info("No terminal flag provided. Interactive mode activated.\n")
        
        custom_idx = len(self.terminals) + 1
        
        for i, term in enumerate(self.terminals, start=1):
            desktop_filename = term if term.endswith(".desktop") else f"{term}.desktop"
            desktop_path = self._find_desktop_file(desktop_filename)
            
            # Deep binary resolution checking (solves reverse-DNS false negatives)
            can_launch = False
            if desktop_path:
                exec_bin = self._get_exec_from_desktop(desktop_path)
                if exec_bin and shutil.which(exec_bin):
                    can_launch = True
                elif shutil.which(term):
                    can_launch = True
            else:
                if shutil.which(term):
                    can_launch = True

            marker_color = "32" if can_launch else "31"
            marker_text = "[Installed]" if can_launch else "[Not Found / Not in $PATH]"
            installed_marker = c_wrap(marker_text, marker_color, use_color=USE_COLOR_OUT)
            
            print(f"  {c_wrap(str(i) + ')', '34', bold=True, use_color=USE_COLOR_OUT)} {term:<18} {installed_marker}")
            
        print(f"  {c_wrap(str(custom_idx) + ')', '34', bold=True, use_color=USE_COLOR_OUT)} Custom (Input raw .desktop name)")

        while True:
            try:
                print()
                choice = input(f"{c_wrap('::', '34', bold=True, use_color=USE_COLOR_OUT)} Select your default terminal (1-{custom_idx}): ").strip()
                
                if not choice:
                    continue

                match choice:
                    case c if c.isdigit() and 1 <= int(c) <= len(self.terminals):
                        return self.terminals[int(c) - 1]
                    
                    case c if c.isdigit() and int(c) == custom_idx:
                        custom_term = input(f"{c_wrap('::', '34', bold=True, use_color=USE_COLOR_OUT)} Enter exact terminal command or desktop name: ").strip()
                        if custom_term:
                            return custom_term
                        log_warn("Input cannot be empty.")
                    
                    # Exact structural match preserving original casing
                    case c if any(c.lower() == t.lower() for t in self.terminals):
                        return next(t for t in self.terminals if t.lower() == c.lower())
                    
                    case _:
                        log_warn("Invalid input. Please select a valid number or terminal name.")

            except (KeyboardInterrupt, EOFError):
                print()
                sys.exit(130)

    def set_terminal(self, selected_terminal: str) -> None:
        """Validates and atomic-writes the target desktop file to the XDG config."""
        
        desktop_filename = selected_terminal if selected_terminal.endswith(".desktop") else f"{selected_terminal}.desktop"
        base_cmd = selected_terminal.removesuffix(".desktop")
        desktop_path = self._find_desktop_file(desktop_filename)

        log_info("Validating environment...")
        
        can_launch = False
        if desktop_path:
            exec_bin = self._get_exec_from_desktop(desktop_path)
            if exec_bin and shutil.which(exec_bin):
                can_launch = True
            elif shutil.which(base_cmd):
                can_launch = True
        else:
            if shutil.which(base_cmd):
                can_launch = True

        if not desktop_path:
            log_warn(f"Desktop entry '{desktop_filename}' not found in XDG data directories.")
            log_step("Note: Some applications use reverse-DNS naming (e.g., org.wezfurlong.wezterm.desktop).")

        if not can_launch:
            log_warn("The target executable was not found in $PATH. The terminal may fail to launch.")

        log_info(f"Target file: {self.target_file}")
        log_info(f"Setting XDG terminal to: {c_wrap(desktop_filename, '32', bold=True, use_color=USE_COLOR_OUT)}")

        try:
            self.target_file.parent.mkdir(parents=True, exist_ok=True)
            self.target_file.write_text(f"{desktop_filename}\n", encoding="utf-8")
            log_success("Terminal successfully configured!")
            
        except PermissionError:
            fatal_error("Insufficient permissions. Cannot write to XDG_CONFIG_HOME.")
        except OSError as e:
            fatal_error(f"OS level error during I/O operation: {e}")

# ─────────────────────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Configure the default XDG terminal for modern Wayland environments.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog="Configuration: Add custom terminals by creating ~/.config/xdg-terminal-setter/config.toml"
    )
    parser.add_argument(
        "-t", "--terminal", 
        type=str,
        default=None,
        help="Name of the terminal (e.g., foot, kitty, org.wezfurlong.wezterm.desktop)"
    )
    
    args = parser.parse_args()
    configurator = TerminalConfigurator()

    selected_terminal = args.terminal if args.terminal else configurator.prompt()
    
    if not selected_terminal:
        fatal_error("Execution aborted. No terminal selected.")

    configurator.set_terminal(selected_terminal)

if __name__ == "__main__":
    main()
