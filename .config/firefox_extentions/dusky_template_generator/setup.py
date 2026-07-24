#!/usr/bin/env python3
"""
🦊 Dusky Template Generator — Setup & Installer Script
======================================================
Provisions native messaging manifests to browser profiles and
prepares the WebExtension for instant Firefox loading.
"""

from __future__ import annotations

import sys
import os
import json
import shutil
import stat
from pathlib import Path

C_CYAN = "\033[0;36m"
C_GREEN = "\033[0;32m"
C_YELLOW = "\033[1;33m"
C_RED = "\033[0;31m"
C_RESET = "\033[0m"

MANIFEST_NAME = "dusky_template_generator.json"

def main() -> None:
    print(f"\n{C_CYAN}================================================================={C_RESET}")
    print(f"{C_CYAN}  🦊 Dusky Template Generator Setup Script{C_RESET}")
    print(f"{C_CYAN}================================================================={C_RESET}\n")

    base_dir = Path(__file__).parent.resolve()
    host_script = base_dir / "host" / "dusky_template_host.py"
    manifest_src = base_dir / "host" / MANIFEST_NAME

    if not host_script.is_file():
        print(f"{C_RED}❌ Error:{C_RESET} Host script not found at {host_script}")
        sys.exit(1)

    # 1. Make host script executable
    try:
        current_mode = host_script.stat().st_mode
        host_script.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print(f"{C_GREEN}✓{C_RESET} Made host script executable: {host_script}")
    except Exception as e:
        print(f"{C_YELLOW}⚠ Could not set executable mode on {host_script}: {e}{C_RESET}")

    # 2. Update host path inside dusky_template_generator.json
    manifest_payload = {
        "name": "dusky_template_generator",
        "description": "Dusky Sites Template Generator Native Host",
        "path": str(host_script),
        "type": "stdio",
        "allowed_extensions": ["dusky_template_generator@dusk.com"]
    }
    manifest_src.write_text(json.dumps(manifest_payload, indent=2) + "\n", encoding="utf-8")
    print(f"{C_GREEN}✓{C_RESET} Manifest updated with exact path: {manifest_src}")

    # 3. Install manifest into browser native-messaging-hosts directories
    home = Path.home()
    browser_dirs = [
        home / ".mozilla" / "native-messaging-hosts",
        home / ".config" / "mozilla" / "native-messaging-hosts",
        home / ".librewolf" / "native-messaging-hosts",
        home / ".zen" / "native-messaging-hosts",
        home / ".waterfox" / "native-messaging-hosts",
        home / ".floorp" / "native-messaging-hosts",
    ]

    installed_count = 0
    for target_dir in browser_dirs:
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            target_manifest = target_dir / MANIFEST_NAME
            target_manifest.write_text(json.dumps(manifest_payload, indent=2) + "\n", encoding="utf-8")
            print(f"{C_GREEN}✓{C_RESET} Installed host manifest in {target_dir.parent.name} → {target_manifest}")
            installed_count += 1
        except Exception as e:
            print(f"{C_YELLOW}⚠ Skipped {target_dir}: {e}{C_RESET}")

    print(f"\n{C_GREEN}✅ Setup Complete!{C_RESET}")
    print(f"To load the extension into Firefox:")
    print(f"  1. Open Firefox and go to {C_CYAN}about:debugging#/runtime/this-firefox{C_RESET}")
    print(f"  2. Click {C_CYAN}'Load Temporary Add-on...'{C_RESET}")
    print(f"  3. Select: {C_CYAN}{base_dir / 'manifest.json'}{C_RESET}\n")

if __name__ == "__main__":
    main()
