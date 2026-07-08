#!/usr/bin/env python3
"""
Firefox Symlink Partition Utility
----------------------------------
Manages ownership, permissions, and directory layout for the Firefox data partition
mounted directly at ~/.config/mozilla.

Written in Python 3.14.6 with Rich UI & Auto-Elevation.
"""

import argparse
import grp
import os
import pwd
import shutil
import sys
from pathlib import Path

# Try importing rich, if missing, print instructions
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Confirm
    from rich.table import Table
except ImportError:
    print("[INFO] Missing 'rich' library. Auto-installing via pacman...")
    try:
        import subprocess
        subprocess.run(["sudo", "pacman", "-S", "--needed", "--noconfirm", "python-rich"], check=True)
        # Restart the script
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e:
        print(f"[ERROR] Failed to auto-install dependencies: {e}")
        sys.exit(1)

# Initialize Rich Console
console = Console()


def log_info(msg: str) -> None:
    console.print(f"[bold blue]::[/] {msg}")


def log_success(msg: str) -> None:
    console.print(f"[bold green]::[/] {msg}")


def log_warn(msg: str) -> None:
    console.print(f"[bold yellow]:: WARNING:[/] {msg}")


def log_error(msg: str) -> None:
    console.print(f"[bold red]ERROR:[/] {msg}", style="red")


def setup_permissions[T: (str, Path)](path: T, uid: int, gid: int, dry_run: bool) -> None:
    """Sets ownership and permissions (755 directories, 644/755 files) recursively."""
    p = Path(path)
    if dry_run:
        log_info(f"[dim][[Dry Run] Would chown {p} to {uid}:{gid} and chmod 755[/]")
        if p.is_dir():
            for item in p.rglob("*"):
                log_info(f"[dim][[Dry Run] Would chown {item} to {uid}:{gid}[/]")
        return

    try:
        os.chown(p, uid, gid)
        if p.is_dir():
            p.chmod(0o755)
        else:
            p.chmod(0o644)
    except Exception as e:
        log_warn(f"Failed to set permissions/ownership on root path {p}: {e}")

    if p.is_dir():
        for item in list(p.rglob("*")):
            try:
                if not item.is_symlink():
                    os.chown(item, uid, gid)
                    if item.is_dir():
                        item.chmod(0o755)
                    elif item.is_file():
                        mode = item.stat().st_mode
                        item.chmod(0o755 if (mode & 0o111) else 0o644)
                else:
                    os.lchown(item, uid, gid)
            except FileNotFoundError:
                continue
            except Exception as e:
                log_warn(f"Failed to set permissions/ownership on {item}: {e}")


def merge_directories(src: Path, dst: Path, uid: int, gid: int, dry_run: bool) -> None:
    """Recursively moves and merges directory contents from src to dst, setting ownership."""
    if not src.exists():
        return

    if dry_run:
        log_info(f"[dim][[Dry Run] Would recursively merge contents of {src} into {dst}[/]")
        return

    for item in list(src.rglob("*")):
        rel_path = item.relative_to(src)
        target = dst / rel_path

        try:
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                os.chown(target, uid, gid)
                target.chmod(0o755)
            elif item.is_file() or item.is_symlink():
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                shutil.copy2(item, target, follow_symlinks=False)
                if not item.is_symlink():
                    os.chown(target, uid, gid)
                    mode = item.stat().st_mode
                    target.chmod(0o755 if (mode & 0o111) else 0o644)
                else:
                    os.lchown(target, uid, gid)
        except FileNotFoundError:
            continue
        except Exception as e:
            log_warn(f"Failed to copy/permissions for {item} -> {target}: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Firefox data partition migration and permission manager."
    )
    parser.add_argument(
        "-y", "--yes", action="store_true", help="Bypass all interactive prompts."
    )
    parser.add_argument(
        "-n", "--dry-run", action="store_true", help="Print actions without modifying system."
    )

    args = parser.parse_args()

    # 1. Auto-Elevation check
    if os.geteuid() != 0:
        log_info("Script requires elevated privileges. Auto-elevating via sudo...")
        try:
            # Re-execute using sudo
            os.execvp("sudo", ["sudo", sys.executable] + sys.argv)
        except Exception as e:
            log_error(f"Failed to elevate privileges: {e}")
            sys.exit(1)

    # 2. Pre-flight Checks (Root and User detection)
    sudo_user = os.environ.get("SUDO_USER")
    if not sudo_user:
        log_error("Could not detect the actual user (SUDO_USER environment variable missing).")
        sys.exit(1)

    try:
        pw_info = pwd.getpwnam(sudo_user)
        real_uid = pw_info.pw_uid
        real_gid = pw_info.pw_gid
        real_home = Path(pw_info.pw_dir)
        real_group = grp.getgrgid(real_gid).gr_name
    except KeyError:
        log_error(f"Could not resolve user details for: {sudo_user}")
        sys.exit(1)

    # Render a premium title panel
    title_text = (
        f"[bold white]Firefox Partition Migration Utility[/bold white]\n"
        f"[dim]Relocates browser files to a dedicated encrypted partition[/dim]\n\n"
        f"[bold cyan]User Details:[/bold cyan]\n"
        f"  • User:       [green]{sudo_user}[/green] (UID: {real_uid})\n"
        f"  • Group:      [green]{real_group}[/green] (GID: {real_gid})\n"
        f"  • Home Path:  [green]{real_home}[/green]"
    )
    console.print(Panel(title_text, title="[bold red]System Topology[/bold red]", border_style="blue"))

    # Render descriptive overview of drive management
    overview_text = (
        "[bold cyan]=== Purpose & Drive Management ===[/bold cyan]\n"
        "This utility configures your system to store your Firefox data on an encrypted\n"
        "dedicated volume directly mounted at [bold yellow]~/.config/mozilla[/bold yellow].\n\n"
        "1. [bold green]Unlocking/Mounting:[/] The volume is managed by your Universal Drive Manager.\n"
        "   You can unlock and mount the partition on-demand using the alias:\n"
        "   [bold yellow]unlock browser[/bold yellow]\n"
        "2. [bold green]Integration:[/] The script creates a symbolic link from [bold yellow]~/.mozilla[/bold yellow]\n"
        "   pointing to [bold yellow]~/.config/mozilla[/bold yellow], so both legacy and modern paths point\n"
        "   to the encrypted container. Permissions are recursively set to [bold yellow]755[/bold yellow].\n"
    )
    console.print(overview_text)

    target_dir = real_home / ".config" / "mozilla"

    # 3. Verify Target directory and mountpoint
    if not target_dir.exists():
        log_error(f"Target directory {target_dir} does not exist.")
        sys.exit(1)

    if not target_dir.is_mount():
        log_error(f"{target_dir} is NOT a mounted partition.")
        log_warn("Please unlock and mount your partition first using the drive manager ([bold]unlock browser[/bold]).")
        sys.exit(1)

    # 4. Confirmation Prompts
    if not args.yes:
        confirm = Confirm.ask(
            f"[bold yellow]Configure symlink and permissions for [cyan]{target_dir}[/cyan]?[/bold yellow]",
            default=False
        )
        if not confirm:
            log_info("Execution cancelled by user.")
            sys.exit(0)

    # 5. Handle Symlink from ~/.mozilla to ~/.config/mozilla
    symlink_path = real_home / ".mozilla"
    
    if symlink_path.exists():
        if symlink_path.is_symlink():
            target_link = os.readlink(symlink_path)
            if target_link != str(target_dir):
                log_info(f"Removing outdated symlink {symlink_path} -> {target_link}")
                if not args.dry_run:
                    symlink_path.unlink()
        else:
            # It's a real directory, merge its contents into the mount point
            log_info(f"Merging existing local directory {symlink_path} into mount point {target_dir}...")
            merge_directories(symlink_path, target_dir, real_uid, real_gid, args.dry_run)
            log_info(f"Removing local directory {symlink_path}...")
            if not args.dry_run:
                shutil.rmtree(symlink_path)

    # Create the symbolic link if it doesn't exist
    if not symlink_path.exists():
        log_info(f"Creating symbolic link: {symlink_path} -> {target_dir}")
        if not args.dry_run:
            symlink_path.symlink_to(target_dir)
            os.lchown(symlink_path, real_uid, real_gid)

    # 6. Perform Ownership & Permission Changes (sweep at the end to cover all merged files)
    log_info(f"Setting ownership and permissions recursively on {target_dir}...")
    setup_permissions(target_dir, real_uid, real_gid, args.dry_run)

    log_success("Firefox partition configuration complete.")


if __name__ == "__main__":
    main()
