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
    """Verify all dependencies and other parts are available before proceeding."""
    # Verify required Python packages and system libraries
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

    # If imports succeeded, check for .config/dusky/settings directory
    config_dir = os.path.join(os.path.expanduser("~"), ".config", "dusky", "settings")
    if not os.path.isdir(config_dir):
        print(f"[INFO] Creating settings directory at: {config_dir}")
        os.makedirs(config_dir, exist_ok=True)

# =============================================================================
# SYSTEM VALUE RETRIEVAL (e.g. Memory, CPU)
# =============================================================================
def get_system_value(key: str) -> str:
    """Retrieve system information based on the provided key."""
    try:
        if key == "memory_total":
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return str(round(int(line.split()[1]) / 1024 ** 2, 1)) + " GB"
        elif key == "cpu_model":
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
        elif key == "gpu_model":
            lspci_output = subprocess.check_output(["lspci"], text=True)
            for line in lspci_output.splitlines():
                if "VGA compatible controller" in line or "3D controller" in line:
                    return line.split(":", 2)[2].strip()
        elif key == "kernel_version":
            return os.uname().release
        # Add more keys as needed
    except Exception as e:
        print(f"[ERROR] Could not retrieve system value for {key}: {e}")
    
    return "N/A"

# =============================================================================
# SAVING SETTINGS
# =============================================================================
KEY_LOCATION = os.path.join(os.path.expanduser("~"), ".config", "dusky", "settings")

# Key files don't have an extension and contain only the value as plain text.
# i.e. ~/.config/dusky/settings/some_setting_key
# Boolean, file stores "true" or "false"s
# Integer, file stores string representation of integer
# String, file stores the string directly
# etc.
def save_setting(key: str, value: Any, as_int: bool = False) -> None:
    """Save a setting value to a file in the settings directory."""
    key_path = os.path.join(KEY_LOCATION, key)
    try:
        with open(key_path, "w", encoding="utf-8") as f:
            if as_int and isinstance(value, bool):
                f.write(str(int(value)))
            else:
                f.write(str(value))
            print(f"[INFO] Saved setting {key} = {value}")
    except OSError as e:
        print(f"[ERROR] Could not save setting {key}: {e}")

def load_setting(key: str, default: Any = None, is_inversed: bool = False) -> Any:
    """Load a setting value from a file in the settings directory."""
    key_path = os.path.join(KEY_LOCATION, key)
    if not os.path.isfile(key_path):
        return default
    try:
        with open(key_path, "r", encoding="utf-8") as f:
            value = f.read().strip()
            if isinstance(default, bool):
                try: 
                    return (int(value) != 0) ^ is_inversed
                except ValueError:
                    return (value.lower() == "true") ^ is_inversed
            elif isinstance(default, int):
                return int(value)
            elif isinstance(default, float):
                return float(value)
            return value
    except OSError as e:
        print(f"[ERROR] Could not load setting {key}: {e}")
        return default
    
def toast(toast_overlay, message: str, timeout: int = 2) -> None:
        """Show a toast notification."""
        import gi
        gi.require_version("Adw", "1")
        from gi.repository import Adw  # noqa: F401
        if toast_overlay:
            toast = Adw.Toast(title=message, timeout=timeout)
            toast_overlay.add_toast(toast)