#!/usr/bin/env python3
"""Move Rust wallpaper-selector state to Dusky Papers, then retire its old launcher."""

import argparse
from contextlib import ExitStack
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def merge_json(source: Path, destination: Path) -> None:
    old = json.loads(source.read_text())
    new = json.loads(destination.read_text())
    if not isinstance(old, dict) or not isinstance(new, dict):
        raise ValueError(f"Expected JSON objects in {source} and {destination}")
    merged = old | new  # Keep current preferences and color results on collisions.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=destination.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(merged, stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    source.unlink()


def merge_directory(source: Path, destination: Path) -> None:
    if not source.exists() and not source.is_symlink():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() and not destination.is_symlink():
        source.rename(destination)
        return
    if source.is_symlink() or destination.is_symlink():
        raise ValueError(f"Cannot merge existing symlinked directories: {source}, {destination}")
    for old in source.iterdir():
        new = destination / old.name
        if not new.exists() and not new.is_symlink():
            old.rename(new)
        elif old.is_dir() and not old.is_symlink():
            merge_directory(old, new)
        elif old.name in {"colors.json", "preferences.json"}:
            merge_json(old, new)
        else:
            # Both caches contain this file; preserve the current destination.
            old.unlink()
    source.rmdir()


def new_launcher_works(home: Path) -> bool:
    try:
        result = subprocess.run(
            [str(home / ".local/bin/dusky_papers"), "--version"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0 and result.stdout.startswith("dusky_papers ")
    except (OSError, subprocess.TimeoutExpired):
        return False


def remove_old_binary(home: Path) -> None:
    install = home / ".local/share/dusky/wallpaper_selector"
    launcher = home / ".local/bin/wallpaper_selector"
    if launcher.is_symlink():
        target = (launcher.parent / launcher.readlink()).absolute()
        if target in {install / "wallpaper_selector", Path("/usr/bin/dusky-wallpaper-selector")}:
            launcher.unlink()
        else:
            raise ValueError(f"Preserving unexpected launcher target: {launcher} -> {target}")
    elif launcher.exists():
        raise ValueError(f"Preserving unexpected non-symlink launcher: {launcher}")
    for name in ("wallpaper_selector", "binary_manifest.json"):
        (install / name).unlink(missing_ok=True)
    if install.is_dir() and not any(install.iterdir()):
        install.rmdir()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cleanup", action="store_true", help="Also remove the old native binary after verifying the new launcher")
    args = parser.parse_args()
    home = Path.home()
    if args.cleanup and not new_launcher_works(home):
        print("[WARN] Dusky Papers is not ready; keeping the old binary and retrying on a later update.")
        return 1
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    with ExitStack() as stack:
        # Hold the same locks as both Rust apps. Do not unlink lock files.
        for name in ("dusky_wallpaper_selector.lock", "dusky_papers.lock"):
            lock = stack.enter_context((runtime / name).open("a"))
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("[WARN] A wallpaper app is open; retrying migration on a later update.")
                return 1
        for old, new in (
            (".cache/dusky_images/wallpaper_selector_rust", ".cache/dusky_images/dusky_papers"),
            (".config/dusky/settings/wallpaper_selector_rust", ".config/dusky/settings/dusky_papers"),
        ):
            merge_directory(home / old, home / new)
        if args.cleanup:
            remove_old_binary(home)
    print("[OK] Wallpaper cache and preferences migrated" + ("; obsolete native binary removed" if args.cleanup else ""))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError) as error:
        print(f"[WARN] Migration incomplete; retrying on a later update: {error}", file=sys.stderr)
        sys.exit(1)
