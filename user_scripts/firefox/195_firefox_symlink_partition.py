#!/usr/bin/env python3
"""
Firefox Symlink Partition Utility
----------------------------------
Manages ownership, permissions, and directory layout for the Firefox data partition
mounted directly at ~/.config/mozilla.

Written in Python 3.14.6.
"""

import argparse
import grp
import os
import pwd
import shutil
import sys
from pathlib import Path

# Setup beautiful console formatting constants
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
RED = "\033[0;31m"
BLUE = "\033[0;34m"
NC = "\033[0m"


def log_info(msg: str) -> None:
    print(f"{BLUE}:: {msg}{NC}")


def log_success(msg: str) -> None:
    print(f"{GREEN}:: {msg}{NC}")


def log_warn(msg: str) -> None:
    print(f"{YELLOW}:: WARNING: {msg}{NC}")


def log_error(msg: str) -> None:
    print(f"{RED}ERROR: {msg}{NC}", file=sys.stderr)


def prompt_confirm(prompt_msg: str, default: bool = False) -> bool:
    """Prompts the user directly via /dev/tty to bypass pipeline stdin redirection."""
    if not sys.stdin.isatty():
        return default
    try:
        with open("/dev/tty", "r") as tty_in, open("/dev/tty", "w") as tty_out:
            default_str = " (y/N): " if not default else " (Y/n): "
            tty_out.write(f"{YELLOW}:: {prompt_msg}{default_str}{NC}")
            tty_out.flush()
            line = tty_in.readline().strip().lower()
            if not line:
                return default
            return line.startswith("y")
    except Exception as e:
        # Fallback to standard input/output
        try:
            res = input(f"{YELLOW}:: {prompt_msg} (y/N): {NC}")
            return res.strip().lower().startswith("y")
        except Exception:
            return default


def setup_permissions[T: (str, Path)](path: T, uid: int, gid: int, dry_run: bool) -> None:
    """Sets ownership and permissions (755 directories, 644/755 files) recursively."""
    p = Path(path)
    if dry_run:
        log_info(f"[Dry Run] Would chown {p} to {uid}:{gid} and chmod 755")
        if p.is_dir():
            for item in p.rglob("*"):
                log_info(f"[Dry Run] Would chown {item} to {uid}:{gid} and set permissions")
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
        # Using a list copy of rglob to prevent issues if files are modified during iteration
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
                # File might have been a temporary file deleted during traversal
                continue
            except Exception as e:
                log_warn(f"Failed to set permissions/ownership on {item}: {e}")


def merge_directories(src: Path, dst: Path, uid: int, gid: int, dry_run: bool) -> None:
    """Recursively moves and merges directory contents from src to dst, setting ownership."""
    if not src.exists():
        return

    if dry_run:
        log_info(f"[Dry Run] Would recursively merge contents of {src} into {dst}")
        return

    for item in list(src.rglob("*")):
        # Get relative path from src to construct dst target path
        rel_path = item.relative_to(src)
        target = dst / rel_path

        try:
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                os.chown(target, uid, gid)
                target.chmod(0o755)
            elif item.is_file() or item.is_symlink():
                # Ensure parent directory exists before copying
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

    # 1. Pre-flight Checks
    if os.geteuid() != 0:
        log_error("Please run this script with sudo.")
        sys.exit(1)

    sudo_user = os.environ.get("SUDO_USER")
    if not sudo_user:
        log_error("Could not detect the actual user. Do not run as root directly.")
        sys.exit(1)

    try:
        pw_info = pwd.getpwnam(sudo_user)
        real_uid = pw_info.pw_uid
        real_gid = pw_info.pw_gid
        real_home = Path(pw_info.pw_dir)
        real_group = grp.getgrgid(real_gid).gr_name
    except KeyError:
        log_error(f"Could not resolve password database info for user: {sudo_user}")
        sys.exit(1)

    log_info(f"Target User: {sudo_user}")
    log_info(f"Target Home: {real_home}")

    target_dir = real_home / ".config" / "mozilla"

    # 2. Verify Mount Point
    if not target_dir.exists():
        log_error(f"Directory {target_dir} does not exist.")
        sys.exit(1)

    if not target_dir.is_mount():
        log_error(f"{target_dir} is NOT a mounted partition.")
        log_warn("Please unlock and mount your partition first using the drive manager.")
        sys.exit(1)

    # 3. Confirmation Prompts
    if not args.yes:
        if not prompt_confirm(f"Do you want to configure permissions on {target_dir}?"):
            log_info("Execution cancelled by user.")
            sys.exit(0)

    # 4. Handle Symlink from ~/.mozilla to ~/.config/mozilla
    symlink_path = real_home / ".mozilla"
    
    if symlink_path.exists():
        if symlink_path.is_symlink():
            target_link = os.readlink(symlink_path)
            # Clean up if it points to a different location (like /mnt/browser/.mozilla)
            if target_link != str(target_dir):
                log_info(f"Removing outdated symlink {symlink_path} -> {target_link}")
                if not args.dry_run:
                    symlink_path.unlink()
        else:
            # It's a real directory, merge its contents into the mount point
            log_info(f"Merging existing {symlink_path} directory into mount point {target_dir}...")
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

    # 5. Perform Ownership & Permission Changes (do it at the end to cover all merged files)
    log_info(f"Setting ownership permissions (755) recursively on {target_dir}...")
    setup_permissions(target_dir, real_uid, real_gid, args.dry_run)

    log_success("Firefox partition configuration complete.")


if __name__ == "__main__":
    main()
