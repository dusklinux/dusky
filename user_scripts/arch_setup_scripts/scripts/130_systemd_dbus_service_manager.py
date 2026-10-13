#!/usr/bin/env python3
#d: Manage systemd and D-Bus services

import argparse
import os
import pwd
import re
import shlex
import shutil
import string
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# --- Rich UI Imports ---
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

# Initialize Rich Consoles
console = Console()
error_console = Console(stderr=True)

# ==============================================================================
# CONFIGURATION SECTION
# ==============================================================================

@dataclass(frozen=True, slots=True)
class ServiceConfig:
    """Configuration model for a Systemd service/timer file."""
    source_path: str
    default_action: Literal["enable", "disable"] = "disable"


@dataclass(frozen=True, slots=True)
class SymlinkConfig:
    """Configuration model for DBus or User Symlinks."""
    source_path: str
    target_path: str


# ------------------------------------------------------------------------------
# 1. Systemd User Services (Target: ~/.config/systemd/user/)
# ------------------------------------------------------------------------------
USER_SERVICES: list[ServiceConfig] = [
    # Dusky Polkit Authentication Agent (Default: Enable)
    # Replaces hyprpolkitagent (removed by 010_package_removal.sh).
    ServiceConfig("$HOME/user_scripts/dusky_system/dusky_polkit/dusky_polkit.service", "enable"),
    # Network Meter (Default: Enable)
    ServiceConfig("$HOME/user_scripts/waybar/network/rust/dusky_network.service", "enable"),
    # Dusky Control Center Daemon (Default: Disable)
    ServiceConfig("$HOME/user_scripts/dusky_system/control_center/service/dusky.service", "disable"),
    # Dusky Update Checker (Default: Disable)
    ServiceConfig("$HOME/user_scripts/update_dusky/update_checker/service/update_checker.service", "disable"),
    ServiceConfig("$HOME/user_scripts/update_dusky/update_checker/service/update_checker.timer", "disable"),
    # Dusky OSD Router (Default: Disable)
    ServiceConfig("$HOME/user_scripts/mako_osd/osd_router/osd_lock.service", "disable"),
    # Dusky RAM Monitor (Default: Disable)
    ServiceConfig("$HOME/user_scripts/performance/swap_and_ram_monitor_service/dusky_ram_monitor.service", "disable"),
    # Dusky Audio Visualizer (Default: Disable)
    ServiceConfig("$HOME/user_scripts/way_layers/visualizer/dusky_visualizer.service", "disable"),
    # Dusky Screentime Tracker (Default: Disable)
    ServiceConfig("$HOME/user_scripts/screentime/dusky_screentime.service", "disable"),

    # Dusky Notification Time Tracker Daemon (Default: Enable)
    ServiceConfig("$HOME/user_scripts/dusky_system/dusky_tray/service/notification_time_service/dusky_notif_time.service", "enable"),

    # Dusky Wayland Clipboard Manager (History & Persistence) (Default: Enable)
    ServiceConfig("$HOME/user_scripts/clipboard/service/dusky_clipboard.service", "enable"),
]

# ------------------------------------------------------------------------------
# 2. DBus & User Service Symlinks
# ------------------------------------------------------------------------------
DBUS_SYMLINKS: list[SymlinkConfig] = [
    # Dusky Control Center DBus Activation
    SymlinkConfig(
        "$HOME/user_scripts/dusky_system/control_center/service/com.github.dusky.controlcenter.service",
        "$XDG_DATA_HOME/dbus-1/services/com.github.dusky.controlcenter.service",
    ),
]

# ------------------------------------------------------------------------------
# 3. Systemd System Services (Target: /etc/systemd/system/ - Requires Root)
# ------------------------------------------------------------------------------
SYSTEM_SERVICES: list[ServiceConfig] = [
    # RAPL CPU Energy Permissions Setter (Default: Enable)
    ServiceConfig("$HOME/user_scripts/mako_osd/dusky_glance/services/glance_cpu_pkg_watt.service", "enable"),
    # Dusky CPU Core and Power Limiter Restorer (Default: Enable)
    ServiceConfig("$HOME/user_scripts/performance/cpu/service/dusky_cpu.service", "enable"),
    # Disable NumLock on TTYs on Boot (Default: Disable)
    ServiceConfig("$HOME/user_scripts/hypr/input/service/numlock_disable.service", "disable"),
    # Clean up modules from old kernels on boot (Default: Enable)
    ServiceConfig("/usr/lib/systemd/system/linux-modules-cleanup.service", "enable"),
    # Dusky Keylogger Keystroke Statistics Daemon (Default: Disable)
    ServiceConfig("$HOME/user_scripts/keylogger/systemd/dusky_keylogger.service", "disable"),
    # Dusky Powertop Auto-Tune one-shot + boot timer (Default: Disable)
    ServiceConfig("$HOME/user_scripts/battery/powertop/dusky_powertop_autotune.service", "disable"),
    ServiceConfig("$HOME/user_scripts/battery/powertop/dusky_powertop_autotune.timer", "disable"),
]

SYSTEMD_SYSTEM_DIR = Path("/etc/systemd/system")

# ==============================================================================
# HELPER & UTILITY FUNCTIONS
# ==============================================================================

@dataclass(frozen=True, slots=True)
class UserContext:
    username: str
    home: Path
    uid: int
    gid: int
    is_root: bool


def get_user_context() -> UserContext:
    """Resolves real non-root user details prioritizing active privilege escalation context (headless safe)."""
    is_root = os.geteuid() == 0
    real_uid = os.getuid()

    if is_root:
        # 1. Immediate Privilege Escalation Environment Variables MUST take precedence
        escalation_uid = os.environ.get("SUDO_UID") or os.environ.get("PKEXEC_UID")
        if escalation_uid and escalation_uid.isdigit():
            real_uid = int(escalation_uid)
        elif "DOAS_USER" in os.environ:
            try:
                real_uid = pwd.getpwnam(os.environ["DOAS_USER"]).pw_uid
            except KeyError:
                pass
        else:
            # 2. Fallback to Absolute Truth via PAM / logind
            try:
                loginuid_raw = Path("/proc/self/loginuid").read_text(encoding="utf-8").strip()
                loginuid = int(loginuid_raw)
                if loginuid != 4294967295:  # (unsigned -1) means unset
                    real_uid = loginuid
            except (FileNotFoundError, ValueError, OSError):
                pass

    try:
        pw = pwd.getpwuid(real_uid)
    except KeyError:
        error_console.print(f"[bold red][ERROR][/bold red] Fatal: Resolved UID {real_uid} does not map to a valid system user.")
        sys.exit(1)

    return UserContext(
        username=pw.pw_name,
        home=Path(pw.pw_dir),
        uid=pw.pw_uid,
        gid=pw.pw_gid,
        is_root=is_root,
    )


def get_xdg_dir(ctx: UserContext, variable: str, fallback: Path) -> Path:
    """Use absolute XDG paths from the user's environment, including sudo children."""
    value = os.environ.get(variable) if not ctx.is_root or ctx.uid != 0 else None
    return Path(value) if value and Path(value).is_absolute() else ctx.home / fallback


def get_user_config_dir(ctx: UserContext) -> Path:
    return get_xdg_dir(ctx, "XDG_CONFIG_HOME", Path(".config"))


def get_user_data_dir(ctx: UserContext) -> Path:
    return get_xdg_dir(ctx, "XDG_DATA_HOME", Path(".local/share"))


def expand_path(raw_path: str, ctx: UserContext) -> Path:
    """Expand configured paths using the resolved user, independent of root's HOME."""
    replacements = frozendict({
        "HOME": str(ctx.home),
        "XDG_CONFIG_HOME": str(get_user_config_dir(ctx)),
        "XDG_DATA_HOME": str(get_user_data_dir(ctx)),
    })
    path = string.Template(raw_path).safe_substitute(replacements)
    if path == "~" or path.startswith("~/"):
        path = str(ctx.home) + path[1:]
    if not Path(path).is_absolute():
        raise ValueError(f"Configured path must be absolute: {raw_path}")
    return Path(path)


def write_atomic(dest: Path, content: str, mode: int = 0o644) -> bool:
    """Replace a file atomically; clean up only this invocation's temporary file."""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", errors="surrogateescape",
            dir=dest.parent, prefix=f".dusky-{dest.name}.", delete_on_close=False,
        ) as stream:
            stream.write(content)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
            stream.close()
            Path(stream.name).replace(dest)
        dir_fd = os.open(dest.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        return True
    except (OSError, UnicodeError) as exc:
        log_error(f"Failed to write {dest}: {exc}")
        return False


def deploy_unit_file(src_path: Path, target_file: Path, ctx: UserContext) -> tuple[bool, bool]:
    """
    Substitute configured installer variables and atomically deploy unit files.
    Returns (ok, changed): changed is False when target already had identical content.
    """
    try:
        raw_content = src_path.read_text(encoding="utf-8", errors="surrogateescape")

        # 1. Build comprehensive template dictionary
        install_dir = src_path.parent.parent
        service_dir = src_path.parent
        local_venv = install_dir / ".venv" / "bin" / "python"
        uv_keylogger_venv = ctx.home / "contained_apps" / "uv" / "dusky_key_logger" / "bin" / "python"

        if local_venv.is_file():
            venv_py = str(local_venv)
        elif src_path.name == "dusky_keylogger.service" and uv_keylogger_venv.is_file():
            venv_py = str(uv_keylogger_venv)
        else:
            venv_py = sys.executable

        replacements = frozendict({
            "USER": ctx.username,
            "HOME": str(ctx.home),
            "UID": str(ctx.uid),
            "GID": str(ctx.gid),
            "INSTALL_DIR": str(install_dir),
            "SERVICE_DIR": str(service_dir),
            "VENV_PYTHON": venv_py,
            # The keylogger installer and daemon use this canonical location,
            # independent of XDG_CONFIG_HOME.
            "DATA_DIR": str(ctx.home / ".config" / "dusky" / "settings" / "keylogger" / "data"),
            "CONFIG_DIR": str(ctx.home / ".config" / "dusky" / "settings" / "keylogger"),
            "XDG_DATA_HOME": str(get_user_data_dir(ctx)),
            "XDG_CONFIG_HOME": str(get_user_config_dir(ctx)),
            "XDG_STATE_HOME": str(get_xdg_dir(ctx, "XDG_STATE_HOME", Path(".local/state"))),
            "XDG_RUNTIME_DIR": f"/run/user/{ctx.uid}",
        })

        # Substitute only known installer variables. Keep systemd's $$ and runtime
        # variables (e.g. $MAINPID), and leave literal paths/specifiers untouched.
        def substitute(match: re.Match[str]) -> str:
            name = match[1] or match[2]
            if name not in replacements:
                return match[0]
            # C escapes keep whitespace, quotes and backslashes inside one value;
            # doubled percent/dollar signs survive systemd's own expansion.
            value = replacements[name]
            value = "".join(
                f"\\x{ord(char):02x}" if char in (" ", "\\", '"', "'", "\t", "\n", "\r") else char
                for char in value
            )
            value = value.replace("%", "%%")
            line_start = raw_content.rfind("\n", 0, match.start()) + 1
            directive = raw_content[line_start:match.start()].lstrip()
            return value.replace("$", "$$") if directive.startswith("Exec") else value

        content = re.sub(
            r"\$\$|\$\{([A-Za-z_][A-Za-z_0-9]*)\}|\$([A-Za-z_][A-Za-z_0-9]*)",
            substitute, raw_content,
        )

        # Skip identical content to avoid writes and mtime churn.
        try:
            if target_file.is_file() and not target_file.is_symlink():
                existing = target_file.read_text(encoding="utf-8", errors="surrogateescape")
                if existing == content:
                    return (True, False)
        except OSError:
            pass

        # Atomic file write with 0644 permissions and fsync.
        ok = write_atomic(target_file, content)
        return (ok, ok)
    except (OSError, UnicodeError, ValueError) as e:
        log_error(f"Failed to process and deploy {src_path.name}: {e}")
        return (False, False)


def safe_mkdir(target_dir: Path) -> bool:
    """Create missing directories with 0755 before applying the umask. Returns success state."""
    try:
        target_dir.mkdir(parents=True, exist_ok=True, mode=0o755, parent_mode=0o755)
        return True
    except (OSError, UnicodeError, ValueError) as e:
        log_error(f"Failed to create directory {target_dir}: {e}")
        return False


def get_user_ipc_env(ctx: UserContext) -> dict[str, str]:
    """Build a consistent environment for the resolved user's IPC and XDG paths."""
    runtime_dir = Path(f"/run/user/{ctx.uid}")
    if not runtime_dir.exists():
        log_warn(f"Runtime dir {runtime_dir} missing! Is user logged in or linger enabled?")

    env = {
        "PATH": os.environ.get("PATH", "/usr/local/sbin:/usr/local/bin:/usr/bin"),
        "USER": ctx.username,
        "HOME": str(ctx.home),
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
        "XDG_RUNTIME_DIR": str(runtime_dir),
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime_dir}/bus",
    }

    env["XDG_CONFIG_HOME"] = str(get_user_config_dir(ctx))
    env["XDG_DATA_HOME"] = str(get_user_data_dir(ctx))
    env["XDG_STATE_HOME"] = str(get_xdg_dir(ctx, "XDG_STATE_HOME", Path(".local/state")))

    return env


def log_info(msg: str) -> None:
    console.print(f"[bold blue][INFO][/bold blue] {msg}")


def log_success(msg: str) -> None:
    console.print(f"[bold green][OK][/bold green]   {msg}")


def log_warn(msg: str) -> None:
    console.print(f"[bold yellow][WARN][/bold yellow] {msg}")


def log_error(msg: str) -> None:
    error_console.print(f"[bold red][ERROR][/bold red] {msg}")


def suggest_missing(source_path: Path) -> None:
    """Provide fuzzy suggestions if source unit file does not exist."""
    parent = source_path.parent
    if parent.exists() and parent.is_dir():
        log_warn("Did you name it something else? Found in directory:")
        matching = [f.name for f in parent.glob("*.service")] + [f.name for f in parent.glob("*.timer")]
        if matching:
            for item in matching:
                console.print(f"  - {item}")
        else:
            console.print("  (No .service or .timer files found)")


def get_caller_cgroup_units() -> set[str]:
    """Find hosting units without misparsing process names containing spaces or ')'."""
    caller_units: set[str] = set()
    visited: set[int] = set()
    pid = os.getpid()
    while pid > 1 and pid not in visited:
        visited.add(pid)
        try:
            cgroups = Path(f"/proc/{pid}/cgroup").read_text(encoding="utf-8")
            caller_units.update(re.findall(r"/([^/\s]+\.(?:service|scope|timer))(?=/|$)", cgroups, re.MULTILINE))
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            pid = int(stat.rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return caller_units


def run_systemctl(args: list[str], is_user: bool, dry_run: bool, ctx: UserContext) -> bool:
    """Execute systemctl, allowing normal service start/stop deadlines to complete."""
    base = ["systemctl", "--user"] if is_user else ["systemctl"]
    cmd = base + args
    cmd_str = shlex.join(cmd)

    if dry_run:
        log_info(f"[Dry-Run] Would execute: [cyan]{cmd_str}[/cyan]")
        return True

    env = get_user_ipc_env(ctx) if is_user else None
    try:
        res = subprocess.run(cmd, capture_output=True, env=env, text=True,
                             encoding="utf-8", errors="replace", timeout=120)
        if res.returncode != 0:
            out_msg = (res.stdout or "").strip()
            err_msg = (res.stderr or "").strip()
            combined = "\n".join(p for p in (err_msg, out_msg) if p)
            log_error(f"Systemctl failed ({cmd_str}):\n{combined}" if combined else f"Systemctl returned non-zero ({cmd_str})")
            units = [a for a in args if a.endswith((".service", ".timer", ".socket", ".target"))]
            scope_flag = "--user " if is_user else ""
            if units:
                log_info(f"Hint: systemctl {scope_flag}status {' '.join(units)} --no-pager -l; journalctl {scope_flag}-xeu {units[0]}")
            else:
                log_info(f"Hint: systemctl {scope_flag}status --no-pager -l; journalctl {scope_flag}-xe")
            return False
        return True
    except subprocess.TimeoutExpired:
        log_error(f"Systemctl timed out ({cmd_str}); the manager job may still be running.")
        return False
    except (OSError, UnicodeError, ValueError) as e:
        log_error(f"Execution error ({cmd_str}): {e}")
        return False


def heal_vendor_shadow(
    service_name: str,
    vendor_src: Path,
    target_file: Path,
    is_user: bool,
    dry_run: bool,
    ctx: UserContext,
) -> tuple[bool, bool]:
    """Removes a stale /etc (or ~/.config) shadow of a vendor unit when it is a proven artifact.

    Returns (proceed, changed): proceed False means skip the unit, changed True means
    installation may have changed (caller needs daemon-reload). Only deletes when the shadow
    is byte-identical to vendor content with $$ collapsed to $ (the old Template bug).
    Intentional overrides are preserved.
    """
    if target_file.is_symlink() or not target_file.exists():
        return (True, False)
    if target_file.is_dir():
        log_error(f"Target path {target_file} is a directory! Cannot replace. Skipping {service_name}.")
        return (False, False)
    try:
        vendor_content = vendor_src.read_bytes()
        shadow_content = target_file.read_bytes()
    except OSError as e:
        log_error(f"Failed to read vendor/shadow for {service_name}: {e}")
        return (False, False)
    if vendor_content != shadow_content and vendor_content.replace(b"$$", b"$") == shadow_content:
        log_warn(f"Stale shadow {target_file} matches the old $$->$ artifact. Removing to restore vendor unit.")
        if dry_run:
            log_info(f"[Dry-Run] Would disable and remove stale shadow {target_file}, then apply the selected action to {service_name}")
            return (True, True)
        # Remove wrong wants/ link first so re-enable recreates a link to vendor path.
        if not run_systemctl(["disable", "--no-reload", service_name], is_user=is_user, dry_run=False, ctx=ctx):
            return (False, True)
        try:
            target_file.unlink(missing_ok=True)
            log_success(f"Removed stale shadow {service_name}")
        except OSError as e:
            log_error(f"Failed to remove stale shadow {target_file}: {e}")
            return (False, True)
        return (True, True)
    log_warn(f"Preserving existing override {target_file}; applying the selected action as-is.")
    return (True, False)


def reload_dbus(dry_run: bool, ctx: UserContext) -> bool:
    """Modern DBus reload via busctl for dbus-broker compatibility."""
    log_info("Triggering DBus configuration reload via busctl...")
    cmd = [
        "busctl", "--user", "--timeout=10s", "call",
        "org.freedesktop.DBus",
        "/org/freedesktop/DBus",
        "org.freedesktop.DBus",
        "ReloadConfig"
    ]
    if dry_run:
        log_info(f"[Dry-Run] Would execute: {' '.join(cmd)}")
        return True

    env = get_user_ipc_env(ctx)
    try:
        res = subprocess.run(cmd, capture_output=True, env=env, text=True, encoding="utf-8", errors="replace", timeout=15)
        if res.returncode != 0:
            log_warn(f"DBus reload failed (is DBus running?): {res.stderr.strip()}")
            return False
        log_success("DBus reloaded successfully.")
        return True
    except (OSError, subprocess.TimeoutExpired) as e:
        log_warn(f"DBus reload error: {e}")
        return False


# ==============================================================================
# CORE WORKFLOW PHASES
# ==============================================================================

def process_service_batch(
    configs: list[ServiceConfig],
    target_dir: Path,
    is_user: bool,
    use_defaults: bool,
    dry_run: bool,
    ctx: UserContext,
) -> bool:
    """Installs and manages systemd service units. Returns True if all operations succeeded."""
    scope = "User" if is_user else "System"
    console.print(f"\n[bold magenta]--- Processing {scope} Services ---[/bold magenta]")

    if not dry_run:
        if not safe_mkdir(target_dir):
            log_error(f"Aborting batch due to directory creation failure: {target_dir}")
            return False
    installed_units: list[ServiceConfig] = []
    valid_extensions = {".service", ".timer", ".socket", ".target"}
    dirty = False
    overall_ok = True

    # Deploy all files before changing installation links.
    for cfg in configs:
        src_path = expand_path(cfg.source_path, ctx)
        service_name = src_path.name
        target_file = target_dir / service_name

        console.print("-" * 50)
        log_info(f"Processing: [bold cyan]{service_name}[/bold cyan]")

        if src_path.suffix not in valid_extensions:
            log_error(f"Invalid unit extension '{src_path.suffix}'. Skipping.")
            overall_ok = False
            continue

        if not src_path.is_file():
            overall_ok = False
            log_error(f"Source file not found: {src_path}")
            suggest_missing(src_path)
            continue

        if src_path.is_relative_to(Path("/usr/lib/systemd")):
            log_info(f"System-provided unit [bold]{service_name}[/bold] already in system search path.")
            proceed, changed = heal_vendor_shadow(service_name, src_path, target_file, is_user, dry_run, ctx)
            dirty |= changed
            if not proceed:
                overall_ok = False
                continue
            installed_units.append(cfg)
            continue

        log_info(f"Installing to {target_file}...")
        if dry_run:
            log_info(f"[Dry-Run] Deploy {src_path} -> {target_file} (mode=0644)")
        else:
            ok, changed = deploy_unit_file(src_path, target_file, ctx)
            if not ok:
                overall_ok = False
                dirty = True  # A failed write may have reached rename before fsync.
                continue
            dirty |= changed
            if changed:
                log_success(f"Installed {service_name}")
            else:
                log_success(f"Already up to date: {service_name}")

        installed_units.append(cfg)

    # Assess actions after successful deployment.
    enable_units: list[str] = []
    disable_units: list[str] = []

    for cfg in installed_units:
        service_name = Path(cfg.source_path).name
        action_str = cfg.default_action.strip().lower()
        if action_str not in ("enable", "disable"):
            action_str = "disable"

        default_is_enable = (action_str == "enable")

        if use_defaults or not sys.stdin.isatty():
            user_wants_enable = default_is_enable
            log_info(f"Auto-applying default ({action_str}) for {service_name}...")
        else:
            try:
                user_wants_enable = Confirm.ask(
                    f"Enable and Start {service_name}?",
                    default=default_is_enable,
                    console=console,
                )
            except (EOFError, OSError):
                user_wants_enable = default_is_enable

        if user_wants_enable:
            enable_units.append(service_name)
        else:
            source = expand_path(cfg.source_path, ctx)
            if source.is_relative_to(Path("/usr/lib/systemd")) and (target_dir / service_name).is_symlink():
                log_error(f"Cannot disable {service_name} without risking removal of its vendor override symlink. Preserving it.")
                overall_ok = False
                continue
            disable_units.append(service_name)

    # Change installation links without implicit per-unit daemon reloads.
    # Disable intentionally leaves active units running, as in the original installer.
    start_units: list[str] = []
    for action, units in (("enable", enable_units), ("disable", disable_units)):
        for unit in units:
            dirty = True  # A failed command can still have changed some links.
            if run_systemctl([action, "--no-reload", unit], is_user, dry_run, ctx):
                log_success(f"{action.capitalize()}d: {unit}")
                if action == "enable":
                    start_units.append(unit)
            else:
                overall_ok = False

    if dirty or (dry_run and installed_units):
        log_info(f"Reloading {scope.lower()} systemd daemon...")
        if not run_systemctl(["daemon-reload"], is_user, dry_run, ctx):
            return False  # Do not start units against stale manager configuration.

    # Start separately after the single reload, retaining per-unit failure reporting.
    for unit in start_units:
        if run_systemctl(["start", unit], is_user, dry_run, ctx):
            log_success(f"Started: {unit}")
        else:
            overall_ok = False
    return overall_ok


def process_symlinks(
    configs: list[SymlinkConfig],
    dry_run: bool,
    ctx: UserContext,
) -> bool:
    """Manages DBus and User service symlinks cleanly via direct symlink creation without backup files."""
    console.print("\n[bold magenta]--- Processing DBus & Service Symlinks ---[/bold magenta]")

    overall_ok = True
    need_user_daemon_reload = False
    need_dbus_reload = False

    for cfg in configs:
        src_path = expand_path(cfg.source_path, ctx)
        target_path = expand_path(cfg.target_path, ctx)
        target_dir = target_path.parent

        console.print("-" * 50)
        log_info(f"Target: [cyan]{target_path.name}[/cyan]")

        if not src_path.is_file():
            overall_ok = False
            log_error(f"Source missing: {src_path}")
            suggest_missing(src_path)
            continue

        if not dry_run:
            if not safe_mkdir(target_dir):
                overall_ok = False
                continue

        if target_path.exists() and target_path.is_dir() and not target_path.is_symlink():
            log_error(f"Target path {target_path} is a directory! Cannot replace.")
            overall_ok = False
            continue

        if target_path.is_symlink():
            try:
                if target_path.resolve() == src_path.resolve():
                    log_success("Symlink already exists and points to correct source.")
                    continue
                log_warn(f"Updating existing link (was pointing to {target_path.resolve()})")
            except (OSError, RuntimeError):
                log_warn("Updating broken or invalid existing symlink.")

        log_info(f"Linking {src_path} -> {target_path}")
        if dry_run:
            log_info(f"[Dry-Run] Would symlink {src_path} -> {target_path}")
        else:
            try:
                with tempfile.TemporaryDirectory(dir=target_dir, prefix=".dusky-link-") as temporary:
                    link = Path(temporary) / "link"
                    link.symlink_to(src_path)
                    link.replace(target_path)
                log_success("Link mapped successfully.")
            except (OSError, UnicodeError, ValueError) as e:
                log_error(f"Failed to create symlink {target_path}: {e}")
                overall_ok = False
                continue

        # Robust Trigger Detection
        if target_dir.name == "services" and target_dir.parent.name == "dbus-1":
            need_dbus_reload = True
        elif target_path.is_relative_to(get_user_config_dir(ctx) / "systemd/user"):
            need_user_daemon_reload = True

    console.print("-" * 50)
    if need_user_daemon_reload:
        log_info("Systemd user units changed. Reloading daemon...")
        if run_systemctl(["daemon-reload"], is_user=True, dry_run=dry_run, ctx=ctx):
            log_success("Systemd user daemon reloaded.")
        else:
            overall_ok = False

    if need_dbus_reload:
        overall_ok &= reload_dbus(dry_run, ctx=ctx)
    return overall_ok


def display_status(ctx: UserContext) -> bool:
    """Render batched systemctl show properties and report query failures."""
    table = Table(title="Systemd & DBus Services Status Overview", title_style="bold cyan")
    table.add_column("Unit / Service", style="bold white")
    table.add_column("Type", style="magenta")
    table.add_column("Source Status", style="yellow")
    table.add_column("Target Installed", style="blue")
    table.add_column("Active State", style="green")
    table.add_column("Enabled State", style="cyan")
    overall_ok = True

    def get_bulk_states(units: list[str], is_user: bool) -> dict[str, tuple[str, str]]:
        nonlocal overall_ok
        if not units:
            return {}
        cmd = ["systemctl", "--no-pager", "show", "--property=Id,ActiveState,UnitFileState,LoadState", *units]
        credentials = {}
        if is_user:
            cmd.insert(1, "--user")
            if ctx.is_root and ctx.uid != 0:
                credentials = {
                    "user": ctx.uid,
                    "group": ctx.gid,
                    "extra_groups": os.getgrouplist(ctx.username, ctx.gid),
                }
        env = get_user_ipc_env(ctx) if is_user else None
        try:
            res = subprocess.run(cmd, capture_output=True, env=env, text=True,
                                 encoding="utf-8", errors="replace", timeout=15, **credentials)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log_error(f"Cannot query {'user' if is_user else 'system'} status: {exc}")
            overall_ok = False
            return {}
        if res.returncode:
            log_error(f"Status query failed: {res.stderr.strip() or res.returncode}")
            overall_ok = False
        states: dict[str, tuple[str, str]] = {}
        for block in res.stdout.strip().split("\n\n"):
            item = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
            if unit_id := item.get("Id"):
                states[unit_id] = (
                    ("not-found", "not-found") if item.get("LoadState") == "not-found" else
                    (item.get("ActiveState") or "unknown", item.get("UnitFileState") or "static")
                )
        return states

    # Batch Query User Services
    user_units = [expand_path(c.source_path, ctx).name for c in USER_SERVICES]
    user_states = get_bulk_states(user_units, is_user=True)
    user_target_dir = get_user_config_dir(ctx) / "systemd" / "user"

    for cfg in USER_SERVICES:
        src = expand_path(cfg.source_path, ctx)
        target = user_target_dir / src.name
        active, enabled = user_states.get(src.name, ("unknown", "unknown"))
        table.add_row(src.name, "User Service", "Found" if src.exists() else "Missing", "Installed" if target.exists() else "Not Installed", active, enabled)

    # Batch Query DBus Symlinks
    for cfg in DBUS_SYMLINKS:
        src = expand_path(cfg.source_path, ctx)
        target = expand_path(cfg.target_path, ctx)
        if target.is_symlink():
            try:
                tgt_state = "Linked" if target.resolve(strict=True) == src.resolve(strict=True) else "Wrong Target"
            except (OSError, RuntimeError):
                tgt_state = "Broken Link"
        else:
            tgt_state = "File (Not Link)" if target.exists() else "Not Linked"
        table.add_row(target.name, "DBus Link", "Found" if src.exists() else "Missing", tgt_state, "N/A", "N/A")

    # Batch Query System Services
    sys_units = [expand_path(c.source_path, ctx).name for c in SYSTEM_SERVICES]
    sys_states = get_bulk_states(sys_units, is_user=False)

    for cfg in SYSTEM_SERVICES:
        src = expand_path(cfg.source_path, ctx)
        target = SYSTEMD_SYSTEM_DIR / src.name
        active, enabled = sys_states.get(src.name, ("unknown", "unknown"))
        if src.is_relative_to(Path("/usr/lib/systemd")):
            installed_label = "Override" if target.exists() or target.is_symlink() else ("Vendor" if src.is_file() else "Not Installed")
        else:
            installed_label = "Installed" if target.exists() else "Not Installed"
        table.add_row(src.name, "System Service", "Found" if src.exists() else "Missing", installed_label, active, enabled)

    console.print(table)
    return overall_ok


def uninstall_all(
    ctx: UserContext, dry_run: bool, *, user: bool, dbus: bool, system: bool,
) -> bool:
    """Stop and remove selected units; keep files when stop/disable fails."""
    console.print("\n[bold red]--- Reverting Configured Services ---[/bold red]")
    overall_ok = True
    caller_units = get_caller_cgroup_units()

    for selected, configs, target_dir, is_user in (
        (user, USER_SERVICES, get_user_config_dir(ctx) / "systemd/user", True),
        (system, SYSTEM_SERVICES, SYSTEMD_SYSTEM_DIR, False),
    ):
        if not selected:
            continue
        if not is_user and not ctx.is_root and not dry_run:
            log_error("Root privileges required for system uninstall.")
            overall_ok = False
            continue
        dirty = False
        # Stop timers/sockets before the services they can reactivate.
        for cfg in sorted(configs, key=lambda cfg: Path(cfg.source_path).suffix == ".service"):
            source = expand_path(cfg.source_path, ctx)
            unit = source.name
            target = target_dir / unit
            vendor = source.is_relative_to(Path("/usr/lib/systemd"))
            if not vendor and not (target.exists() or target.is_symlink()):
                continue
            if unit in caller_units:
                log_error(f"Cannot uninstall hosting unit {unit} while it runs this script; run from another terminal.")
                overall_ok = False
                continue
            if vendor and target.is_symlink():
                log_error(f"Cannot uninstall {unit} without risking removal of vendor override {target}. Preserving it.")
                overall_ok = False
                continue
            if not run_systemctl(["stop", unit], is_user, dry_run, ctx):
                overall_ok = False
                continue
            dirty = True
            if not run_systemctl(["disable", "--no-reload", unit], is_user, dry_run, ctx):
                overall_ok = False
                continue
            if not (target.exists() or target.is_symlink()):
                continue
            try:
                if target.is_dir() and not target.is_symlink():
                    raise IsADirectoryError(f"Cannot remove unit directory: {target}")
                if vendor:
                    # A read error must never turn preservation into deletion.
                    original = source.read_bytes()
                    shadow = target.read_bytes()
                    if original == shadow or original.replace(b"$$", b"$") != shadow:
                        log_warn(f"Preserving vendor override {target}")
                        continue
                log_info(f"{'[Dry-Run] Would remove' if dry_run else 'Removing'} {target}")
                if not dry_run:
                    target.unlink(missing_ok=True)
            except OSError as exc:
                log_error(f"Failed to remove {target}: {exc}")
                overall_ok = False
        if dirty:
            overall_ok &= run_systemctl(["daemon-reload"], is_user, dry_run, ctx)

    if dbus:
        changed = False
        for cfg in DBUS_SYMLINKS:
            target = expand_path(cfg.target_path, ctx)
            if not (target.exists() or target.is_symlink()):
                continue
            log_info(f"{'[Dry-Run] Would remove' if dry_run else 'Removing'} {target}")
            try:
                if not dry_run:
                    target.unlink(missing_ok=True)
                changed = True
            except OSError as exc:
                log_error(f"Failed to remove {target}: {exc}")
                overall_ok = False
        if changed:
            overall_ok &= reload_dbus(dry_run, ctx)
    return overall_ok


def run_system_phase(ctx: UserContext, uninstall: bool, use_defaults: bool, dry_run: bool) -> bool:
    """Run system work directly as root, or with the same interpreter via sudo."""
    if ctx.is_root or dry_run:
        if uninstall:
            return uninstall_all(ctx, dry_run, user=False, dbus=False, system=True)
        return process_service_batch(
            SYSTEM_SERVICES, SYSTEMD_SYSTEM_DIR, False, use_defaults, dry_run, ctx,
        )
    if not shutil.which("sudo"):
        log_error("sudo is required for system services.")
        return False
    # Preserve the actual interpreter's import path (Rich may be installed in the
    # user's site directory), and forward resolved XDG paths consistently.
    cmd = [
        "sudo",
        f"PYTHONPATH={os.pathsep.join(sys.path)}",
        f"XDG_CONFIG_HOME={get_user_config_dir(ctx)}",
        f"XDG_DATA_HOME={get_user_data_dir(ctx)}",
        f"XDG_STATE_HOME={get_xdg_dir(ctx, 'XDG_STATE_HOME', Path('.local/state'))}",
        sys.executable, str(Path(__file__).resolve()), "--system",
    ]
    if uninstall:
        cmd.append("--uninstall")
    if use_defaults:
        cmd.append("--default")
    log_info("Running system phase via sudo...")
    try:
        result = subprocess.run(cmd, check=False)
    except OSError as exc:
        log_error(f"Cannot run system phase: {exc}")
        return False
    if result.returncode:
        log_error(f"System phase failed (exit {result.returncode}).")
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Unified Systemd & DBus Service Manager for Arch Linux",
        allow_abbrev=False,
    )
    mode_group = parser.add_argument_group("Execution Modes")
    mode_group.add_argument("-a", "--all", action="store_true", help="Process all scopes (default)")
    mode_group.add_argument("-u", "--user", action="store_true", help="Process user units only")
    mode_group.add_argument("-d", "--dbus", action="store_true", help="Process D-Bus activation links only")
    mode_group.add_argument("-s", "--system", action="store_true", help="Process system units only")
    mode_group.add_argument("-st", "--status", action="store_true", help="Display live status of all configured units")
    mode_group.add_argument("--uninstall", action="store_true", help="Stop, disable, and remove selected units/links")
    opt_group = parser.add_argument_group("Options")
    opt_group.add_argument("-y", "--default", action="store_true", help="Apply configured enable/disable defaults")
    opt_group.add_argument("-n", "--dry-run", action="store_true", help="Show actions without changing system state")
    args = parser.parse_args()
    if args.status and args.uninstall:
        parser.error("--status and --uninstall cannot be combined")
    if not shutil.which("systemctl"):
        log_error("systemctl is required.")
        return 1
    ctx = get_user_context()
    console.print(Panel.fit(
        f"[bold cyan]Unified Systemd & DBus Service Manager[/bold cyan]\n"
        f"[dim]User: {ctx.username} | Home: {ctx.home} | Root: {ctx.is_root}[/dim]",
        border_style="cyan",
    ))
    if args.status:
        return 0 if display_status(ctx) else 1

    all_scopes = args.all or not (args.user or args.dbus or args.system)
    run_user = all_scopes or args.user
    run_dbus = all_scopes or args.dbus
    run_system = all_scopes or args.system
    if ctx.is_root and (run_user or run_dbus) and not args.dry_run:
        log_error("Run user and D-Bus work as the normal user; system work automatically uses sudo. As root, select --system.")
        return 1

    overall_ok = True
    # Keep the system phase first, matching the existing installation order.
    if run_system:
        overall_ok &= run_system_phase(ctx, args.uninstall, args.default, args.dry_run)
    if args.uninstall:
        if run_user or run_dbus:
            overall_ok &= uninstall_all(ctx, args.dry_run, user=run_user, dbus=run_dbus, system=False)
    else:
        if run_user:
            overall_ok &= process_service_batch(
                USER_SERVICES, get_user_config_dir(ctx) / "systemd/user",
                True, args.default, args.dry_run, ctx,
            )
        if run_dbus:
            overall_ok &= process_symlinks(DBUS_SYMLINKS, args.dry_run, ctx)
    if overall_ok:
        log_success("All assigned operations completed successfully.")
    else:
        log_error("Completed with errors. See the reported failures above.")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        error_console.print("\n[bold red][ABORTED][/bold red] Interrupted.")
        sys.exit(130)
