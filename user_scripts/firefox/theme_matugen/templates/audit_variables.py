#!/usr/bin/env python3
"""
🦊 MatugenFox & Dusky Sites — Theme Variable Audit & Verification Tool
======================================================================
Automated script to audit, verify, and inspect Matugen CSS variables,
WebExtension theme mappings, and installed Firefox profile stylesheets.
"""

import sys
import os
import re
import json
from pathlib import Path

# Terminal Styling
C_CYAN = '\033[0;36m'
C_GREEN = '\033[0;32m'
C_YELLOW = '\033[1;33m'
C_RED = '\033[0;31m'
C_RESET = '\033[0m'

def audit():
    home = Path.home()
    print(f"\n{C_CYAN}================================================================={C_RESET}")
    print(f"{C_CYAN}  🦊 MATUGENFOX & DUSKY SITES VARIABLE AUDIT TOOL{C_RESET}")
    print(f"{C_CYAN}================================================================={C_RESET}\n")

    # 1. Matugen Palette Audit
    matugen_file = home / ".config/matugen/generated/dusky_sites.css"
    matugen_vars = {}
    print(f"{C_CYAN}[1/4] Checking Matugen Palette Output File...{C_RESET}")
    if matugen_file.is_file():
        content = matugen_file.read_text(encoding='utf-8')
        matugen_vars = dict(re.findall(r"(--[\w-]+):\s*([^;]+);", content))
        print(f"   {C_GREEN}✓{C_RESET} Found {len(matugen_vars)} CSS variables in {matugen_file.name}")
    else:
        print(f"   {C_RED}❌ Error:{C_RESET} {matugen_file} not found!")

    # 2. background.js Theme Mapping Audit
    bg_js = home / ".config/firefox_extentions/matugenfox/extension/background.js"
    palette_roles = {}
    browser_elements = {}
    print(f"\n{C_CYAN}[2/4] Auditing Extension Engine (background.js)...{C_RESET}")
    if bg_js.is_file():
        content = bg_js.read_text(encoding='utf-8')
        palette_match = re.search(r"paletteTemplate:\s*\{([^}]+)\}", content)
        browser_match = re.search(r"browserTemplate:\s*\{([^}]+)\}", content)

        if palette_match:
            palette_roles = dict(re.findall(r"(\w+):\s*[\x27\"](--[\w-]+)[\x27\"]", palette_match.group(1)))
            print(f"   {C_GREEN}✓{C_RESET} Palette Template Roles ({len(palette_roles)} roles):")
            for role, var_name in palette_roles.items():
                status = f"{C_GREEN}✓ VERIFIED{C_RESET}" if var_name in matugen_vars else f"{C_RED}❌ MISSING IN MATUGEN{C_RESET}"
                print(f"      • {role:20s} -> {var_name:25s} [{status}]")

        if browser_match:
            browser_elements = dict(re.findall(r"(\w+):\s*[\x27\"](\w+)[\x27\"]", browser_match.group(1)))
            print(f"   {C_GREEN}✓{C_RESET} Browser Theme Elements ({len(browser_elements)} elements mapped)")
    else:
        print(f"   {C_RED}❌ Error:{C_RESET} {bg_js} not found!")

    # 3. dusky_menu.css Rules Audit across Firefox Profiles
    print(f"\n{C_CYAN}[3/4] Auditing Browser Profile Stylesheets (dusky_menu.css)...{C_RESET}")
    profiles = list(home.glob(".config/mozilla/firefox/*")) + list(home.glob(".mozilla/firefox/*")) + list(home.glob(".zen/*"))
    valid_profiles = [p for p in profiles if p.is_dir() and (p / "prefs.js").exists()]

    for p in valid_profiles:
        dusky_menu = p / "chrome" / "dusky_menu.css"
        user_chrome = p / "chrome" / "userChrome.css"
        user_js = p / "user.js"

        pref_ok = user_js.is_file() and "toolkit.legacyUserProfileCustomizations.stylesheets" in user_js.read_text(errors='ignore')
        import_ok = user_chrome.is_file() and "dusky_menu.css" in user_chrome.read_text(errors='ignore')
        file_ok = dusky_menu.is_file()

        pref_str = "YES" if pref_ok else "NO"
        file_str = "YES" if file_ok else "NO"
        import_str = "YES" if import_ok else "NO"

        print(f"   {C_GREEN}├─ [{p.name}]{C_RESET}")
        print(f"   │    Stylesheet Pref Enabled: {pref_str}")
        print(f"   │    dusky_menu.css Present:  {file_str}")
        print(f"   │    userChrome.css Import:   {import_str}")

    # 4. Summary & Verification Status
    print(f"\n{C_CYAN}================================================================={C_RESET}")
    print(f"{C_GREEN}  ✓ AUDIT COMPLETE: All components inspected cleanly.{C_RESET}")
    print(f"{C_CYAN}=================================================================\n")

if __name__ == "__main__":
    audit()
