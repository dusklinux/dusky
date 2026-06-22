#!/usr/bin/env python3
"""
Initializes or overwrites the 'state.conf' user configuration for Dusky Theme.
Designed for Arch Linux environments using Python 3.

Usage: python 006_dusky_state_setup.py
"""

import sys
from pathlib import Path

# --- Dependency Check ---
try:
    from rich.console import Console
    from rich.theme import Theme
except ImportError:
    print("[ERR]  The 'rich' library is required but not found.")
    print("       Please install it via: pacman -S python-rich (or pip install rich)")
    sys.exit(1)

# --- Strict Mode & Configuration ---
# Set up a custom theme to exactly mirror the Bash script's ANSI color codes
custom_theme = Theme({
    "info": "bold blue",
    "success": "bold green",
    "warn": "bold yellow",
    "error": "bold red"
})
console = Console(theme=custom_theme)

# --- Helper Functions (Mimicking Bash Setup) ---
def log_info(msg: str) -> None:
    console.print(f"[info][INFO][/info] {msg}")

def log_success(msg: str) -> None:
    console.print(f"[success][OK][/success]   {msg}")

def log_warn(msg: str) -> None:
    console.print(f"[warn][WARN][/warn] {msg}")

def log_error(msg: str) -> None:
    console.print(f"[error][ERR][/error]  {msg}")

def main() -> None:
    # ------------------------------------------------------------------------------
    # 1. Paths & Content Definition
    # ------------------------------------------------------------------------------
    # Expand the tilde (~) to the user's actual home directory
    target_path = Path("~/.config/dusky/settings/dusky_theme/state.conf").expanduser()
    target_dir = target_path.parent

    # The exact configuration text requested (formatted explicitly to prevent indentation errors)
    state_content = (
        "# Dusky Theme State File\n"
        "THEME_MODE=\"dark\"\n"
        "MATUGEN_TYPE=\"scheme-vibrant\"\n"
        "MATUGEN_CONTRAST=\"0\"\n"
        "SOURCE_COLOR_INDEX=\"1\"\n"
        "BASE16_BACKEND=\"disable\"\n"
        "AWWW_TRANS_TYPE=\"random\"\n"
        "AWWW_TRANS_DURATION=\"2\"\n"
        "AWWW_TRANS_FPS=\"60\"\n"
        "AWWW_TRANS_BEZIER=\".54,0,.34,.99\"\n"
        "AWWW_TRANS_ANGLE=\"45\"\n"
        "AWWW_TRANS_POS=\"center\"\n"
    )

    # ------------------------------------------------------------------------------
    # 2. Main Logic: Create Directory & Overwrite File
    # ------------------------------------------------------------------------------
    log_info("Initializing Dusky Theme state configuration...")

    # Ensure base directory structure exists FIRST
    if not target_dir.exists():
        log_info(f"Creating config directory: {target_dir}")
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            log_success(f"Directory created: {target_dir}")
        except PermissionError:
            log_error("Permission denied. This script modifies user configuration and should NOT be run as root.")
            sys.exit(1)
        except Exception as e:
            log_error(f"Failed to create directory: {e}")
            sys.exit(1)
    else:
        log_info(f"Directory exists: {target_dir} (verifying contents...)")

    # Check existence and inform the user of the overwrite action (No backup per instructions)
    if target_path.exists():
        log_warn(f"Target file already exists at '{target_path.name}'. Overwriting without backup...")
    else:
        log_info(f"Writing new file: {target_path.name}...")

    # Write the content
    try:
        # Using mode='w' automatically handles the overwrite
        target_path.write_text(state_content, encoding="utf-8")
        log_success(f"Created: {target_path.name}")
    except Exception as e:
        log_error(f"Failed to write file: {e}")
        sys.exit(1)

    # ------------------------------------------------------------------------------
    # 3. Completion
    # ------------------------------------------------------------------------------
    print()
    log_success("Setup complete!")
    log_info(f"Your configuration is located in: {target_dir}")

if __name__ == "__main__":
    main()
