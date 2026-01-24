from typing import Any
import yaml, subprocess, shlex, os, sys

# =============================================================================
# CONFIGURATION LOADER
# =============================================================================

def load_config(config_path) -> dict[str, Any]:
    """Load and validate the YAML configuration file."""

    if not config_path.is_file():
        print(f"[INFO] Config not found: {config_path}")
        return {}

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            if not isinstance(data, dict):
                print(f"[WARN] Config is not a valid dictionary.")
                return {}
            return data
    except yaml.YAMLError as e:
        print(f"[ERROR] YAML parse error: {e}")
        return {}
    except OSError as e:
        print(f"[ERROR] Could not read config: {e}")
        return {}

# =============================================================================
# UWSM-COMPLIANT COMMAND RUNNER
# =============================================================================
def execute_command(cmd_string: str, title: str, run_in_terminal: bool) -> bool:
    """
    Execute a command using UWSM for proper Wayland session integration.

    For GUI apps:      uwsm-app -- <command>
    For terminal apps: uwsm-app -- kitty --title <title> --hold sh -c <command>

    Returns True on successful Popen, False on error.
    """
    # Fix: Expand both variables ($HOME) and user paths (~)
    expanded_cmd = os.path.expanduser(os.path.expandvars(cmd_string)).strip()

    if not expanded_cmd:
        return False

    try:
        if run_in_terminal:
            full_cmd = [
                "uwsm-app", "--",
                "kitty",
                "--title", title,
                "--hold",
                "sh", "-c", expanded_cmd
            ]
        else:
            # Parse command string safely into arguments
            try:
                parsed_args = shlex.split(expanded_cmd)
            except ValueError:
                # Fallback: wrap in shell for complex commands (pipes, redirects)
                parsed_args = ["sh", "-c", expanded_cmd]

            full_cmd = ["uwsm-app", "--"] + parsed_args

        subprocess.Popen(
            full_cmd,
            start_new_session=True,  # Detach from parent (replaces & disown)
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True

    except FileNotFoundError:
        print(f"[ERROR] 'uwsm-app' or command not found. Is UWSM installed?")
        return False
    except OSError as e:
        print(f"[ERROR] Failed to execute: {e}")
        return False
    
# =============================================================================
# PRE-FLIGHT DEPENDENCY CHECK
# =============================================================================
def preflight_check() -> None:
    """Verify all dependencies are available before proceeding."""
    missing: list[str] = []

    try:
        import yaml  # noqa: F401
    except ImportError:
        missing.append("python-yaml")

    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Gtk, Adw  # noqa: F401
    except (ImportError, ValueError):
        if "python-gobject" not in missing:
            missing.append("python-gobject")
        missing.extend(["gtk4", "libadwaita"])

    if missing:
        unique_missing = list(dict.fromkeys(missing))
        print("\n╭───────────────────────────────────────────────────────────╮")
        print("│  ⚠  Missing Dependencies                                  │")
        print("╰───────────────────────────────────────────────────────────╯")
        print(f"\n  The following packages are required:\n")
        for pkg in unique_missing:
            print(f"    • {pkg}")
        print(f"\n  Install with:\n")
        print(f"    sudo pacman -S --needed {' '.join(unique_missing)}\n")
        sys.exit(1)