#!/usr/bin/env python3
"""
🦊 MatugenFox Setup Script (Modern Arch Linux / Python 3.12+)
============================================================
Automated setup script for MatugenFox Native Messaging Host.
Provisions XDG configuration directories, detects Firefox-family browsers,
and registers native messaging host manifests.
"""

import sys
import os
import json
import stat
from pathlib import Path

# --- Terminal Styling ---
C_CYAN = '\033[0;36m'
C_GREEN = '\033[0;32m'
C_BLUE = '\033[0;34m'
C_YELLOW = '\033[1;33m'
C_RED = '\033[0;31m'
C_RESET = '\033[0m'

def print_step(msg: str): print(f"{C_BLUE}==>{C_RESET} {msg}")
def print_success(msg: str): print(f"{C_GREEN}✓{C_RESET} {msg}")
def print_warn(msg: str): print(f"{C_YELLOW}⚠{C_RESET} {msg}")
def print_error(msg: str): print(f"{C_RED}❌ Error:{C_RESET} {msg}"); sys.exit(1)

def main():
    print(f"\n{C_CYAN}🦊 MatugenFox Setup Script (Arch Linux / Python 3.12+){C_RESET}\n")

    # 1. Paths Setup
    script_dir = Path(__file__).parent.resolve()
    host_path = script_dir / "matugenfox_host.py"
    manifest_name = "matugenfox.json"

    print_step("Performing pre-flight checks...")
    if not host_path.is_file():
        print_error(f"Host script not found at {host_path}")
    print_success("Pre-flight checks passed.")

    # 2. Host Execution Permissions
    print_step("Securing host script permissions...")
    try:
        st = host_path.stat()
        host_path.chmod(st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print_success("Host script is now executable.")
    except Exception as e:
        print_error(f"Failed to set executable permissions: {e}")

    # 3. Intelligent Directory Provisioning (XDG Standard)
    print_step("Provisioning configuration directories...")
    home = Path.home()
    
    # Primary config directory: ~/.config/dusky/settings/matugenfox
    config_dir = home / ".config" / "dusky" / "settings" / "matugenfox"
    config_dir.mkdir(parents=True, exist_ok=True)
    
    config_file = config_dir / "config.json"
    if not config_file.is_file():
        config_data = {
            "colorsPath": "~/.config/matugen/generated/firefox_websites.css",
            "websitesDir": "~/.config/dusky_sites"
        }
        config_file.write_text(json.dumps(config_data, indent=2), encoding='utf-8')
        print_success(f"Created primary config file at {config_file}")
    else:
        print_success(f"Config file exists at {config_file}")

    # Dusky sites directory
    dusky_sites_dir = home / ".config" / "dusky_sites"
    dusky_sites_dir.mkdir(parents=True, exist_ok=True)
    print_success(f"Ensured templates directory exists at {dusky_sites_dir}")

    # Matugen generated directory
    matugen_gen_dir = home / ".config" / "matugen" / "generated"
    matugen_gen_dir.mkdir(parents=True, exist_ok=True)
    print_success(f"Ensured Matugen output directory exists at {matugen_gen_dir}")

    # 4. Multi-Browser Detection (Arch Linux focus)
    print_step("Detecting supported Firefox-based browsers...")
    targets = []

    candidates = [
        ("Firefox", home / ".mozilla"),
        ("LibreWolf", home / ".librewolf"),
        ("Zen", home / ".zen"),
        ("Waterfox", home / ".waterfox"),
        ("Floorp", home / ".floorp"),
        ("FireDragon", home / ".firedragon"),
        ("Firefox (Flatpak)", home / ".var" / "app" / "org.mozilla.firefox" / ".mozilla"),
        ("LibreWolf (Flatpak)", home / ".var" / "app" / "io.gitlab.librewolf-community" / ".librewolf"),
    ]
    
    for name, path in candidates:
        nmh_dir = path / "native-messaging-hosts"
        if path.is_dir() or nmh_dir.is_dir():
            targets.append((name, nmh_dir))

    # Fallback to default Firefox native-messaging-hosts if no specific directory matched yet
    if not targets:
        default_nmh = home / ".mozilla" / "native-messaging-hosts"
        targets.append(("Firefox (Default)", default_nmh))

    # 5. Install Manifests
    print_step("Installing native messaging manifests...")
    manifest_payload = {
        "name": "matugenfox",
        "description": "MatugenFox Native Messaging Host",
        "path": str(host_path),
        "type": "stdio",
        "allowed_extensions": [
            "matugenfox@ubaid.com"
        ]
    }

    installed_count = 0
    for name, target_dir in targets:
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            manifest_file = target_dir / manifest_name
            manifest_file.write_text(json.dumps(manifest_payload, indent=2), encoding='utf-8')
            print_success(f"Manifest installed for {name} → {target_dir}")
            installed_count += 1
        except Exception as e:
            print_warn(f"Failed to install manifest in {target_dir}: {e}")

    # 6. Completion Report
    print(f"\n{C_GREEN}✅ Setup Complete! MatugenFox was installed into {installed_count} browser target(s).{C_RESET}")
    print("------------------------------------------------------------------")
    print(f"{C_CYAN}1. Package Extension:{C_RESET} cd {script_dir}/extension && zip -r ../matugenfox.zip ./*")
    print(f"{C_CYAN}2. Sign Add-on:{C_RESET}       Upload matugenfox.zip to Mozilla Developer Hub (Unlisted)")
    print(f"{C_CYAN}3. Install in Firefox:{C_RESET} Open about:addons -> Install Add-on From File (.xpi)")
    print("------------------------------------------------------------------\n")

if __name__ == "__main__":
    main()
