#!/usr/bin/env python3
"""
Initializes or validates the core 'hyprland.lua' configuration file.
Ensures the template in defaults/main/hyprland.lua is deployed to ~/.config/hypr/hyprland.lua.
"""

import argparse
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# --- ANSI Color Codes ---
class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[0;33m'
    BLUE = '\033[0;34m'
    RESET = '\033[0m'

class ColoredFormatter(logging.Formatter):
    """Custom logging formatter for ANSI colored outputs."""
    FORMATS = {
        logging.DEBUG: f"{Colors.BLUE}[DEBUG]{Colors.RESET} %(message)s",
        logging.INFO: f"{Colors.BLUE}[INFO]{Colors.RESET} %(message)s",
        logging.WARNING: f"{Colors.YELLOW}[WARN]{Colors.RESET} %(message)s",
        logging.ERROR: f"{Colors.RED}[ERR]{Colors.RESET}  %(message)s",
        logging.CRITICAL: f"{Colors.RED}[CRIT]{Colors.RESET} %(message)s",
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno, self.FORMATS[logging.INFO])
        if getattr(record, 'success', False):
            log_fmt = f"{Colors.GREEN}[OK]{Colors.RESET}   %(message)s"
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)

logger = logging.getLogger(__name__)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(ColoredFormatter())
logger.addHandler(handler)
logger.setLevel(logging.INFO)

def log_success(msg: str):
    logger.info(msg, extra={'success': True})

def main() -> None:
    if os.geteuid() == 0:
        logger.error("This script must NOT be run as root.")
        logger.error(f"It modifies user configuration files in {Path.home()}.")
        sys.exit(1)

    HOME = Path.home()
    HYPR_DIR = HOME / ".config" / "hypr"
    MAIN_CONF = HYPR_DIR / "hyprland.lua"
    DEFAULTS_DIR = HOME / "user_scripts" / "hypr" / "defaults" / "main"
    DEFAULT_CONF = DEFAULTS_DIR / "hyprland.lua"

    APPS_DEFAULTS_REQUIRE = 'require("edit_here.source.default_apps")'
    OVERLAY_REQUIRE = 'require("edit_here.hyprland")'

    if not DEFAULTS_DIR.exists():
        logger.error(f"Defaults directory not found at {DEFAULTS_DIR}.")
        sys.exit(1)

    if not DEFAULT_CONF.exists():
        logger.error(f"Default hyprland.lua template not found at {DEFAULT_CONF}.")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Initialize or validate the core 'hyprland.lua' configuration for Hyprland.")
    parser.add_argument("--force", action="store_true", help="Deletes existing hyprland.lua and regenerates it from defaults.")
    args = parser.parse_args()

    # Force Mode - Clean Delete (No Backups)
    if args.force:
        logger.warning("Force mode: Deleting existing hyprland.lua without backup...")
        
        # Delete main config file only
        if MAIN_CONF.exists():
            logger.warning(f"  - Deleting '{MAIN_CONF}'...")
            MAIN_CONF.unlink()
            
        log_success("Deletion complete. Proceeding with clean regeneration.")

    logger.info("Initializing/Verifying Hyprland core configuration...")
    
    if not HYPR_DIR.exists():
        logger.info(f"Creating directory: {HYPR_DIR}")
        HYPR_DIR.mkdir(parents=True, exist_ok=True)

    # Deploy hyprland.lua
    if MAIN_CONF.exists() and not args.force:
        logger.info("  - Exists: hyprland.lua")
    else:
        if MAIN_CONF.exists() and args.force:
            logger.warning("  - Overwriting: hyprland.lua -> Deploying from defaults...")
        else:
            logger.warning("  - Missing: hyprland.lua -> Deploying from defaults...")
        shutil.copy2(DEFAULT_CONF, MAIN_CONF)
        log_success("    Deployed: hyprland.lua")

    # Verify/Modify Main Config imports
    if MAIN_CONF.exists():
        logger.info(f"Verifying loader imports at '{MAIN_CONF}'...")
        main_conf_content = MAIN_CONF.read_text()
        main_conf_lines = main_conf_content.splitlines()
        changed = False

        if APPS_DEFAULTS_REQUIRE in main_conf_content:
            log_success("Main config already contains default_apps require().")
        else:
            main_conf_lines.insert(0, APPS_DEFAULTS_REQUIRE)
            changed = True
            log_success(f"Prepended '{APPS_DEFAULTS_REQUIRE}' to '{MAIN_CONF}'.")

        # Re-check overlay loader require
        current_content = "\n".join(main_conf_lines)
        if OVERLAY_REQUIRE in current_content:
            log_success("Main config already contains the overlay loader require().")
        else:
            main_conf_lines.append("")
            main_conf_lines.append("-- Source User Custom Config Overlay")
            main_conf_lines.append(OVERLAY_REQUIRE)
            changed = True
            log_success(f"Appended '{OVERLAY_REQUIRE}' to '{MAIN_CONF}'.")

        if changed:
            MAIN_CONF.write_text("\n".join(main_conf_lines) + "\n")

    # Hot-Reload
    if shutil.which("hyprctl"):
        try:
            subprocess.run(["hyprctl", "reload", "config-only"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            log_success("Forced hot-reload of Hyprland configuration (config-only).")
        except subprocess.CalledProcessError:
            pass

    print()
    log_success("Core Configuration Setup/Verification complete!")

if __name__ == "__main__":
    main()
