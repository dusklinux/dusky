#!/usr/bin/env python3
"""
==============================================================================
 GPARTED WAYLAND AUTO-CONFIGURATOR
 Description: Fully idempotent, zero-hardcoding configurator that installs
              GParted, creates a native Wayland launcher wrapper, and deploys
              a desktop entry directly to XDG applications for menu visibility.

              The desktop entry is NOT placed in ~/.config/desktop_entries/all/
              because that directory is reserved for static, git-tracked entries
              managed by the Dusky Desktop Entry Synchronizer. This script's
              output is dynamically generated and deployed directly to its
              final XDG destination.

 Target:      Arch Linux (Kernel 7.0+ / Python 3.14+)
 Usage:       python3 gparted_wayland_setup.py [--uninstall]
==============================================================================
"""

import os
import sys
import shutil
import getpass
import subprocess
from pathlib import Path

# ── ANSI Colors ──────────────────────────────────────────────────────────────
C_RESET  = "\033[0m"
C_BOLD   = "\033[1m"
C_GREEN  = "\033[32m"
C_BLUE   = "\033[34m"
C_YELLOW = "\033[33m"
C_RED    = "\033[31m"
C_CYAN   = "\033[36m"

# ── Required Packages ────────────────────────────────────────────────────────
REQUIRED_PACKAGES = ["gparted", "xorg-xhost"]

# ── File Names (no hardcoded paths, composed dynamically from user home) ─────
WRAPPER_NAME  = "gparted-wayland"
DESKTOP_NAME  = "gparted-wayland.desktop"


# ── Helper Functions ─────────────────────────────────────────────────────────

def resolve_user() -> tuple[str, Path]:
    """Resolves the real unprivileged user and home directory, even under sudo."""
    user = os.environ.get("SUDO_USER") or os.environ.get("USER") or getpass.getuser()
    home = Path(f"/home/{user}")
    if not home.is_dir():
        home = Path.home()
    return user, home


def is_package_installed(name: str) -> bool:
    """Checks if a pacman package is installed."""
    return subprocess.run(
        ["pacman", "-Qq", name], capture_output=True
    ).returncode == 0


def run_privileged(cmd: list[str]) -> None:
    """Runs a command with privilege escalation if not already root."""
    if os.getuid() == 0:
        subprocess.run(cmd, check=True)
    elif shutil.which("sudo"):
        subprocess.run(["sudo"] + cmd, check=True)
    elif shutil.which("pkexec"):
        subprocess.run(["pkexec"] + cmd, check=True)
    else:
        print(f"  {C_RED}✖ No privilege escalation tool found (sudo/pkexec).{C_RESET}", file=sys.stderr)
        sys.exit(1)


def install_packages(packages: list[str]) -> None:
    """Idempotently installs packages via pacman --needed."""
    missing = [p for p in packages if not is_package_installed(p)]

    if not missing:
        print(f"  {C_GREEN}✔{C_RESET} All required packages already installed: {', '.join(packages)}")
        return

    print(f"  {C_YELLOW}•{C_RESET} Installing missing packages: {', '.join(missing)}")
    try:
        run_privileged(["pacman", "-S", "--needed", "--noconfirm"] + missing)
        print(f"  {C_GREEN}✔{C_RESET} Package installation complete.")
    except subprocess.CalledProcessError as e:
        print(f"  {C_RED}✖ pacman failed: {e}{C_RESET}", file=sys.stderr)
        sys.exit(1)


def write_file_safe(path: Path, content: str, mode: int = 0o644) -> None:
    """Writes a file atomically via temp-then-rename, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.chmod(mode)
        tmp.replace(path)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        print(f"  {C_RED}✖ Failed to write {path}: {e}{C_RESET}", file=sys.stderr)
        sys.exit(1)


def invalidate_rofi_cache(home: Path) -> None:
    """Clears rofi's drun cache so new entries appear immediately."""
    cache = home / ".cache" / "rofi3.druncache"
    if cache.is_file():
        cache.unlink(missing_ok=True)
        print(f"  {C_GREEN}✔{C_RESET} Cleared Rofi drun cache.")


# ── Core Logic ───────────────────────────────────────────────────────────────

def do_install(user: str, home: Path) -> None:
    """Full install: packages, wrapper, desktop entry, cache invalidation."""

    wrapper_path  = home / ".local" / "bin" / WRAPPER_NAME
    desktop_path  = home / ".local" / "share" / "applications" / DESKTOP_NAME

    # ── Phase 1: Packages ────────────────────────────────────────────────
    print(f"\n{C_BOLD}{C_CYAN}[Phase 1] Package Verification{C_RESET}")
    install_packages(REQUIRED_PACKAGES)

    # Verify gparted binary actually exists after install
    gparted_bin = shutil.which("gparted") or Path("/usr/bin/gparted")
    if not Path(gparted_bin).is_file():
        print(f"  {C_RED}✖ gparted binary not found after install.{C_RESET}", file=sys.stderr)
        sys.exit(1)
    print(f"  {C_GREEN}✔{C_RESET} gparted binary confirmed at: {gparted_bin}")

    # ── Phase 2: Wrapper Script ──────────────────────────────────────────
    print(f"\n{C_BOLD}{C_CYAN}[Phase 2] Wrapper Script{C_RESET}")

    wrapper_content = (
        '#!/bin/sh\n'
        '# Native Wayland wrapper for GParted (auto-generated)\n'
        '# Passes the active Wayland socket and runtime dir to the root process\n'
        'exec pkexec env \\\n'
        '    WAYLAND_DISPLAY="$WAYLAND_DISPLAY" \\\n'
        '    XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \\\n'
        '    /usr/bin/gparted "$@"\n'
    )

    write_file_safe(wrapper_path, wrapper_content, mode=0o755)
    print(f"  {C_GREEN}✔{C_RESET} Wrapper written: {wrapper_path}")

    # ── Phase 3: Desktop Entry ───────────────────────────────────────────
    print(f"\n{C_BOLD}{C_CYAN}[Phase 3] Desktop Entry{C_RESET}")

    desktop_content = (
        '[Desktop Entry]\n'
        'Version=1.0\n'
        'Type=Application\n'
        'Name=GParted (Wayland)\n'
        'GenericName=Partition Editor\n'
        'Comment=Create, reorganize, and delete partitions natively on Wayland\n'
        f'Exec=dusky-run -- {wrapper_path} %f\n'
        'Icon=gparted\n'
        'Terminal=false\n'
        'Categories=GNOME;GTK;System;Filesystem;\n'
        'StartupNotify=true\n'
    )

    write_file_safe(desktop_path, desktop_content, mode=0o644)
    print(f"  {C_GREEN}✔{C_RESET} Desktop entry written: {desktop_path}")

    # Validate
    if shutil.which("desktop-file-validate"):
        result = subprocess.run(
            ["desktop-file-validate", str(desktop_path)],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and not result.stdout.strip():
            print(f"  {C_GREEN}✔{C_RESET} Desktop entry passes XDG validation.")
        else:
            output = (result.stdout + result.stderr).strip()
            print(f"  {C_YELLOW}⚠ Validation output: {output}{C_RESET}")

    # Update desktop database
    if shutil.which("update-desktop-database"):
        subprocess.run(
            ["update-desktop-database", str(desktop_path.parent)],
            capture_output=True,
        )
        print(f"  {C_GREEN}✔{C_RESET} Desktop database updated.")

    # ── Phase 4: Cache Cleanup ───────────────────────────────────────────
    print(f"\n{C_BOLD}{C_CYAN}[Phase 4] Cache Cleanup{C_RESET}")
    invalidate_rofi_cache(home)

    print(f"\n  {C_GREEN}{C_BOLD}✔ GParted Wayland setup complete.{C_RESET}")
    print(f"  Launch from Rofi as: {C_BOLD}GParted (Wayland){C_RESET}\n")


def do_uninstall(user: str, home: Path) -> None:
    """Removes wrapper script and desktop entry (does not uninstall packages)."""
    wrapper_path  = home / ".local" / "bin" / WRAPPER_NAME
    desktop_path  = home / ".local" / "share" / "applications" / DESKTOP_NAME

    removed = False
    for path in (wrapper_path, desktop_path):
        if path.is_file():
            path.unlink()
            print(f"  {C_GREEN}✔{C_RESET} Removed: {path}")
            removed = True

    if removed:
        if shutil.which("update-desktop-database"):
            subprocess.run(
                ["update-desktop-database", str(desktop_path.parent)],
                capture_output=True,
            )
        invalidate_rofi_cache(home)
        print(f"\n  {C_GREEN}{C_BOLD}✔ Uninstall complete.{C_RESET}\n")
    else:
        print(f"  {C_YELLOW}• Nothing to remove (already clean).{C_RESET}\n")


# ── Entry Point ──────────────────────────────────────────────────────────────

def main() -> None:
    print(f"{C_BOLD}{C_BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C_RESET}")
    print(f"{C_BOLD}{C_BLUE}       GPARTED WAYLAND AUTO-CONFIGURATOR{C_RESET}")
    print(f"{C_BOLD}{C_BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C_RESET}")

    user, home = resolve_user()
    print(f"  {C_CYAN}User:{C_RESET} {C_BOLD}{user}{C_RESET}  |  {C_CYAN}Home:{C_RESET} {home}")

    if "--uninstall" in sys.argv:
        do_uninstall(user, home)
    else:
        do_install(user, home)

    print(f"{C_BOLD}{C_BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C_RESET}")


if __name__ == "__main__":
    main()
