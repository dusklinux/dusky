#!/usr/bin/env python3
#d: Toggle dusky systemd services

import argparse
import asyncio
import difflib
import json
import os
import pwd
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Final, Literal, NamedTuple

# Rich Presentation Imports Guard
RICH_AVAILABLE = False
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Confirm
    from rich.table import Table
    from rich.text import Text
    RICH_AVAILABLE = True
    console = Console()
    error_console = Console(stderr=True)
except ImportError:
    Console = Panel = Confirm = Table = Text = None  # type: ignore
    console = error_console = None  # type: ignore


def check_ui_deps(is_json: bool) -> None:
    """Enforces Rich UI requirement only if not running in JSON mode."""
    if not RICH_AVAILABLE and not is_json:
        print("ERROR: The 'rich' python library is required for interactive/UI mode.", file=sys.stderr)
        print("Install it via: sudo pacman -S python-rich", file=sys.stderr)
        sys.exit(1)


# ==============================================================================
# 1. DOMAIN CONFIGURATION MODELS
# ==============================================================================

@dataclass(frozen=True, slots=True)
class ServiceConfig:
    """Declarative configuration model for a Systemd service/timer/socket unit."""
    name: str
    enabled_by_default: bool = True
    description: str = ""


# ==============================================================================
# 2. SERVICE CONFIGURATION SECTIONS (USER EDITABLE)
# ==============================================================================

# Core System Services (System Scope - Sudo Required)
SYSTEM_SERVICES: Final[list[ServiceConfig]] = [
    ServiceConfig("NetworkManager.service", True, "Network connection manager"),
    ServiceConfig("udisks2.service", True, "Disk management and auto-mounting daemon"),
    ServiceConfig("thermald.service", True, "Thermal daemon for CPU temperature management"),
    ServiceConfig("bluetooth.service", True, "Bluetooth protocol stack daemon"),
    ServiceConfig("ufw.service", True, "Uncomplicated Firewall daemon"),
    ServiceConfig("fstrim.timer", True, "Weekly SSD TRIM maintenance timer"),
    ServiceConfig("systemd-timesyncd.service", True, "Network time synchronization daemon"),
    ServiceConfig("acpid.service", True, "Advanced Configuration and Power Interface daemon"),
    ServiceConfig("systemd-resolved.service", True, "Network Name Resolution manager"),
    ServiceConfig("snapper-cleanup.timer", True, "Btrfs Snapper snapshot cleanup timer"),
    ServiceConfig("snapper-cleanup.service", True, "Btrfs Snapper snapshot cleanup service"),
    # Optional / Disabled by Default:
    ServiceConfig("tlp.service", False, "Power management daemon (disabled by default)"),
    ServiceConfig("vsftpd.service", False, "FTP server daemon (disabled by default)"),
    ServiceConfig("reflector.timer", False, "Pacman mirrorlist reflector timer (disabled by default)"),
]

# AUR System Services (System Scope - Sudo Required)
AUR_SYSTEM_SERVICES: Final[list[ServiceConfig]] = [
    ServiceConfig("fwupd.service", True, "Firmware update daemon"),
    ServiceConfig("warp-svc.service", True, "Cloudflare WARP VPN service daemon"),
    ServiceConfig("preload.service", True, "Adaptive readahead daemon"),
    ServiceConfig("asusd.service", True, "ASUS ROG/TUF Linux control daemon"),
]

# Core User Services (User Session Scope - Executed as User)
USER_SERVICES: Final[list[ServiceConfig]] = [
    ServiceConfig("pipewire.socket", True, "PipeWire multimedia socket"),
    ServiceConfig("pipewire-pulse.socket", True, "PipeWire PulseAudio emulation socket"),
    ServiceConfig("wireplumber.service", True, "PipeWire session manager daemon"),
    ServiceConfig("hypridle.service", True, "Hyprland idle management daemon"),
    ServiceConfig("dusky_polkit.service", True, "Dusky PolicyKit authentication agent"),
    ServiceConfig("fumon.service", True, "File/Folder monitoring service"),
    ServiceConfig("gnome-keyring-daemon.service", True, "GNOME Keyring secret storage daemon"),
    ServiceConfig("gnome-keyring-daemon.socket", True, "GNOME Keyring control socket"),
    ServiceConfig("mako.service", True, "Mako notification daemon"),
    ServiceConfig("dusky_clipboard.service", True, "Dusky Wayland Clipboard Manager (History & Persistence)"),
    # Optional / Disabled by Default:
    ServiceConfig("hyprsunset.service", False, "Hyprland blue-light temperature daemon"),
    ServiceConfig("dusky_notif_time.service", False, "Dusky Notification Timestamp Tracking Daemon"),
]

# AUR User Services (User Session Scope - Executed as User)
AUR_USER_SERVICES: Final[list[ServiceConfig]] = [
    ServiceConfig("hypridle.service", True, "Hyprland idle manager (AUR session)"),
]


# ==============================================================================
# 3. ENUMS & DATA STRUCTURES
# ==============================================================================

class Scope(Enum):
    SYSTEM = auto()
    USER = auto()


class Category(Enum):
    SYSTEM_CORE = "Core System Services"
    SYSTEM_AUR = "AUR System Services"
    USER_CORE = "Core User Services"
    USER_AUR = "AUR User Services"


class UnitStatus(Enum):
    ENABLED_ACTIVE = "Enabled & Active"
    ENABLED_INACTIVE = "Enabled"
    DISABLED_ACTIVE = "Active (Disabled)"
    DISABLED = "Disabled"
    STATIC = "Static"
    MASKED = "Masked"
    MISSING = "Not Installed"
    ACTIVATING = "Activating/Deactivating"
    FAILED = "Failed"
    BAD = "Bad Unit File"
    ERROR = "Error"


@dataclass(slots=True)
class UnitTarget:
    config: ServiceConfig
    scope: Scope
    category: Category


@dataclass(slots=True)
class UnitState:
    unit_name: str
    scope: Scope
    category: Category
    description: str = ""
    exists: bool = False
    load_state: str = "not-found"
    active_state: str = "inactive"
    unit_file_state: str = "disabled"

    @property
    def status_enum(self) -> UnitStatus:
        if not self.exists or self.load_state == "not-found":
            return UnitStatus.MISSING
        if self.unit_file_state in ("bad", "error"):
            return UnitStatus.BAD
        if self.unit_file_state == "masked":
            return UnitStatus.MASKED
        if self.active_state == "failed":
            return UnitStatus.FAILED
        if self.unit_file_state in ("static", "indirect", "generated", "transient"):
            return UnitStatus.STATIC
        if self.active_state in ("activating", "deactivating"):
            return UnitStatus.ACTIVATING

        is_enabled = self.unit_file_state in ("enabled", "enabled-runtime", "alias", "linked")
        is_active = self.active_state in ("active", "reloading")

        if is_enabled and is_active:
            return UnitStatus.ENABLED_ACTIVE
        if is_enabled:
            return UnitStatus.ENABLED_INACTIVE
        if is_active:
            return UnitStatus.DISABLED_ACTIVE
        return UnitStatus.DISABLED


class ProcessingResult(NamedTuple):
    unit_name: str
    category: Category
    status: UnitStatus
    message: str
    output: str = ""


@dataclass(frozen=True)
class UserContext:
    username: str
    home: Path
    uid: int
    gid: int
    is_root: bool


# ==============================================================================
# 4. CONTEXT & ENVIRONMENT RESOLUTION
# ==============================================================================

def resolve_user_context() -> UserContext:
    """Resolves real non-root user details prioritizing active privilege escalation context."""
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
        if error_console:
            error_console.print(f"[bold red][ERROR][/bold red] Fatal: Resolved UID {real_uid} does not map to a valid user.")
        else:
            print(f"ERROR: Fatal: Resolved UID {real_uid} does not map to a valid user.", file=sys.stderr)
        sys.exit(1)

    return UserContext(
        username=pw.pw_name,
        home=Path(pw.pw_dir),
        uid=pw.pw_uid,
        gid=pw.pw_gid,
        is_root=is_root,
    )


def get_escalator() -> str | None:
    """Resolves available privilege escalation tool (sudo, doas, or pkexec)."""
    return shutil.which("sudo") or shutil.which("doas") or shutil.which("pkexec")


def get_user_ipc_env(ctx: UserContext) -> dict[str, str]:
    """
    Constructs a sterile IPC environment to prevent DBus/systemctl --user failure
    when executed under root/sudo contexts.
    """
    runtime_dir = Path(f"/run/user/{ctx.uid}")
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/sbin:/usr/local/bin:/usr/bin"),
        "USER": ctx.username,
        "HOME": str(ctx.home),
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
        "XDG_RUNTIME_DIR": str(runtime_dir),
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime_dir}/bus",
    }
    for xdg_var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME"):
        if xdg_var in os.environ:
            env[xdg_var] = os.environ[xdg_var]

    return env


def build_systemctl_cmd(scope: Scope, args: list[str], ctx: UserContext, read_only: bool = False) -> tuple[list[str], dict[str, str] | None]:
    """Constructs systemctl command vectors, tunneling DBus environment via env to defeat sudo stripping."""
    if scope == Scope.SYSTEM:
        if read_only or ctx.is_root:
            return ["systemctl"] + args, None
        escalator = get_escalator()
        if not escalator:
            raise RuntimeError("Privilege escalation tool (sudo/doas/pkexec) not found.")
        return [escalator, "systemctl"] + args, None
    else:  # Scope.USER
        env = get_user_ipc_env(ctx)
        if ctx.is_root and ctx.username != "root":
            escalator = get_escalator() or "sudo"
            tunnel_args = [
                "env",
                f"XDG_RUNTIME_DIR={env['XDG_RUNTIME_DIR']}",
                f"DBUS_SESSION_BUS_ADDRESS={env['DBUS_SESSION_BUS_ADDRESS']}",
                "systemctl",
                "--user",
            ] + args
            return [escalator, "-u", ctx.username] + tunnel_args, env
        return ["systemctl", "--user"] + args, env


# ==============================================================================
# 5. DIAGNOSTICS & FUZZY MATCHING
# ==============================================================================

def normalize_unit_name(name: str) -> str:
    """Ensures service/socket/timer unit suffix is present."""
    if not any(name.endswith(ext) for ext in (".service", ".socket", ".timer", ".target", ".path", ".device", ".mount", ".automount", ".swap")):
        return f"{name}.service"
    return name


def suggest_missing_unit(unit_name: str, scope: Scope, ctx: UserContext) -> list[str]:
    """Finds fuzzy suggestions across transient, system, and user search paths."""
    search_dirs: list[Path] = []
    if scope == Scope.SYSTEM:
        search_dirs = [Path("/usr/lib/systemd/system"), Path("/etc/systemd/system"), Path("/run/systemd/system")]
    else:
        search_dirs = [
            Path("/usr/lib/systemd/user"),
            Path("/etc/systemd/user"),
            Path("/run/systemd/user"),
            ctx.home / ".config" / "systemd" / "user",
            ctx.home / ".local" / "share" / "systemd" / "user",
        ]

    available_units: list[str] = []
    for d in search_dirs:
        if d.exists() and d.is_dir():
            for p in d.iterdir():
                if p.is_file() and any(p.name.endswith(ext) for ext in (".service", ".timer", ".socket")):
                    available_units.append(p.name)

    return difflib.get_close_matches(unit_name, available_units, n=3, cutoff=0.5)


# ==============================================================================
# 6. TRUE O(1) BULK DICTIONARY SYSTEMD PARSER
# ==============================================================================

def query_bulk_unit_states(targets: list[UnitTarget], ctx: UserContext) -> list[UnitState]:
    """Queries unit metadata in bulk utilizing exact O(1) Dictionary Mapping to survive Alias Desyncs."""
    states: list[UnitState] = []
    sys_targets = [t for t in targets if t.scope == Scope.SYSTEM]
    usr_targets = [t for t in targets if t.scope == Scope.USER]

    def fetch_scope_states(target_group: list[UnitTarget], scope: Scope) -> dict[str, dict[str, str]]:
        if not target_group:
            return {}
        unit_names = [normalize_unit_name(t.config.name) for t in target_group]

        cmd, env = build_systemctl_cmd(
            scope,
            ["show", "--property=Id,Names,ActiveState,UnitFileState,LoadState"] + unit_names,
            ctx=ctx,
            read_only=True,
        )
        env = env.copy() if env else os.environ.copy()
        env["SYSTEMD_COLORS"] = "0"

        try:
            res = subprocess.run(cmd, capture_output=True, env=env, text=True, timeout=15)
            out = res.stdout.strip()
            if not out:
                return {}

            blocks = out.split("\n\n")
            unit_map: dict[str, dict[str, str]] = {}

            for block in blocks:
                current: dict[str, str] = {}
                for line in block.splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        current[k.strip()] = v.strip()

                if "Id" in current:
                    unit_map[current["Id"]] = current
                if "Names" in current:
                    for name in current["Names"].split():
                        unit_map[name] = current
            return unit_map
        except Exception:
            return {}

    sys_map = fetch_scope_states(sys_targets, Scope.SYSTEM)
    usr_map = fetch_scope_states(usr_targets, Scope.USER)

    for target in targets:
        norm_name = normalize_unit_name(target.config.name)
        data = sys_map.get(norm_name, {}) if target.scope == Scope.SYSTEM else usr_map.get(norm_name, {})

        if data and data.get("LoadState") != "not-found":
            st = UnitState(
                unit_name=norm_name,
                scope=target.scope,
                category=target.category,
                description=target.config.description,
                exists=True,
                load_state=data.get("LoadState", "loaded"),
                active_state=data.get("ActiveState", "inactive"),
                unit_file_state=data.get("UnitFileState", "disabled"),
            )
        else:
            st = UnitState(unit_name=norm_name, scope=target.scope, category=target.category, description=target.config.description, exists=False)
        states.append(st)

    return states


# ==============================================================================
# 7. ASYNCHRONOUS CONCURRENT EXECUTION ENGINE WITH SEMAPHORE THROTTLING
# ==============================================================================

async def process_unit_action_async(
    target: UnitTarget,
    st: UnitState,
    action: Literal["enable", "disable"],
    now: bool,
    dry_run: bool,
    ctx: UserContext,
    semaphore: asyncio.Semaphore,
) -> ProcessingResult:
    """Asynchronously modifies unit state securely, utilizing semaphores to prevent DBus flooding."""
    async with semaphore:
        norm_name = normalize_unit_name(target.config.name)
        category = target.category

        if not st.exists:
            # Offload blocking filesystem search to background thread
            suggestions = await asyncio.to_thread(suggest_missing_unit, norm_name, target.scope, ctx)
            msg = f"Unit not found (Package not installed) | Suggestions: {', '.join(suggestions)}" if suggestions else "Unit not found (Package not installed)"
            return ProcessingResult(unit_name=norm_name, category=category, status=UnitStatus.MISSING, message=msg)

        if st.status_enum == UnitStatus.MASKED:
            return ProcessingResult(unit_name=norm_name, category=category, status=UnitStatus.MASKED, message="Unit is masked (Skipped)")
        if st.status_enum == UnitStatus.BAD:
            return ProcessingResult(unit_name=norm_name, category=category, status=UnitStatus.BAD, message="Unit file is bad/invalid")

        if action == "enable":
            if st.status_enum == UnitStatus.ENABLED_ACTIVE or (st.status_enum == UnitStatus.ENABLED_INACTIVE and not now):
                return ProcessingResult(
                    unit_name=norm_name,
                    category=category,
                    status=st.status_enum,
                    message="Already enabled & active" if st.active_state == "active" else "Already enabled",
                )
            if st.status_enum == UnitStatus.STATIC:
                if st.active_state == "active":
                    return ProcessingResult(unit_name=norm_name, category=category, status=UnitStatus.STATIC, message="Static unit (Already active)")
                if not now:
                    return ProcessingResult(unit_name=norm_name, category=category, status=UnitStatus.STATIC, message="Static unit (Cannot enable without --now start)")
                cmd_flags = ["start", norm_name]
            else:
                cmd_flags = ["enable", "--now", norm_name] if now else ["enable", norm_name]
        else:  # disable
            if st.status_enum == UnitStatus.DISABLED and st.active_state == "inactive":
                return ProcessingResult(unit_name=norm_name, category=category, status=UnitStatus.DISABLED, message="Already disabled & inactive")
            if st.status_enum == UnitStatus.STATIC:
                if st.active_state == "inactive":
                    return ProcessingResult(unit_name=norm_name, category=category, status=UnitStatus.STATIC, message="Static unit (Already inactive)")
                cmd_flags = ["stop", norm_name]
            else:
                cmd_flags = ["disable", "--now", norm_name] if now else ["disable", norm_name]

        try:
            cmd, env = build_systemctl_cmd(target.scope, cmd_flags, ctx=ctx, read_only=False)
        except RuntimeError as e:
            return ProcessingResult(unit_name=norm_name, category=category, status=UnitStatus.ERROR, message=str(e))

        if dry_run:
            return ProcessingResult(
                unit_name=norm_name,
                category=category,
                status=UnitStatus.ENABLED_ACTIVE if action == "enable" else UnitStatus.DISABLED,
                message=f"[DRY-RUN] Would execute: {shlex.join(cmd)}",
            )

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=95)
            except TimeoutError:  # Python 3.11+ / 3.14 built-in TimeoutError
                proc.terminate()  # SIGTERM allows sudo to cleanly terminate children without orphaning systemctl
                await proc.wait()
                return ProcessingResult(unit_name=norm_name, category=category, status=UnitStatus.ERROR, message="Execution timed out (95s limit reached), process terminated.")

            output_text = (stderr_bytes.decode(errors="replace").strip() + "\n" + stdout_bytes.decode(errors="replace").strip()).strip()

            if proc.returncode == 0:
                if st.status_enum == UnitStatus.STATIC:
                    msg = f"Successfully {'started' if now and action == 'enable' else 'stopped'} (Static Unit)"
                else:
                    msg = f"Successfully {action}d" + (" & started" if now and action == "enable" else (" & stopped" if now else ""))

                return ProcessingResult(
                    unit_name=norm_name,
                    category=category,
                    status=UnitStatus.ENABLED_ACTIVE if action == "enable" and now else UnitStatus.DISABLED,
                    message=msg,
                    output=output_text,
                )
            else:
                return ProcessingResult(
                    unit_name=norm_name,
                    category=category,
                    status=UnitStatus.ERROR,
                    message=f"Failed to {action}",
                    output=output_text,
                )
        except Exception as e:
            return ProcessingResult(unit_name=norm_name, category=category, status=UnitStatus.ERROR, message=f"Execution error: {e}")


async def execute_operations(
    targets: list[UnitTarget],
    states: list[UnitState],
    action: Literal["enable", "disable"],
    now: bool,
    dry_run: bool,
    ctx: UserContext,
) -> list[ProcessingResult]:
    """Manages concurrent execution of systemd operations via Python 3.14 TaskGroup & DBus throttling."""
    semaphore = asyncio.Semaphore(5)
    async with asyncio.TaskGroup() as tg:
        tasks = [
            tg.create_task(process_unit_action_async(target, st, action, now, dry_run, ctx, semaphore))
            for target, st in zip(targets, states)
        ]
    return [task.result() for task in tasks]


def reload_dbus(scope: Scope, dry_run: bool, ctx: UserContext) -> None:
    """Reloads DBus configuration via busctl for both user and system contexts."""
    if scope == Scope.USER:
        cmd = ["busctl", "--user", "call", "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "ReloadConfig"]
        env = get_user_ipc_env(ctx)
    else:
        escalator = get_escalator() or "sudo"
        cmd = [escalator, "busctl", "call", "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "ReloadConfig"]
        if ctx.is_root:
            cmd = ["busctl", "call", "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "ReloadConfig"]
        env = None

    scope_str = "System" if scope == Scope.SYSTEM else "User"
    if dry_run:
        if console:
            console.print(f"[bold yellow][DRY-RUN][/bold yellow] Would execute {scope_str} DBus reload: {' '.join(cmd)}")
        return

    try:
        res = subprocess.run(cmd, capture_output=True, env=env, text=True, timeout=10)
        if res.returncode == 0:
            if console:
                console.print(f"[bold green][OK][/bold green] {scope_str} DBus configuration reloaded via busctl.")
        else:
            if console:
                console.print(f"[bold yellow][WARN][/bold yellow] {scope_str} DBus reload skipped or failed: {res.stderr.strip()}")
    except Exception as e:
        if console:
            console.print(f"[bold red][ERR][/bold red] {scope_str} DBus reload failed: {e}")


def daemon_reload(scope: Scope, dry_run: bool, ctx: UserContext) -> None:
    """Executes daemon-reload for system or user scope."""
    try:
        cmd, env = build_systemctl_cmd(scope, ["daemon-reload"], ctx=ctx, read_only=False)
    except RuntimeError as e:
        if console:
            console.print(f"[bold red][ERR][/bold red] Cannot reload daemon: {e}")
        return

    scope_str = "System" if scope == Scope.SYSTEM else "User"
    if dry_run:
        if console:
            console.print(f"[bold yellow][DRY-RUN][/bold yellow] Would run {scope_str} daemon-reload: {' '.join(cmd)}")
        return

    try:
        res = subprocess.run(cmd, capture_output=True, env=env, text=True, timeout=20)
        if res.returncode == 0:
            if console:
                console.print(f"[bold green][OK][/bold green] Executed {scope_str} daemon-reload.")
        else:
            if console:
                console.print(f"[bold red][ERR][/bold red] Failed {scope_str} daemon-reload: {res.stdout.strip()}")
    except Exception as e:
        if console:
            console.print(f"[bold red][ERR][/bold red] Failed {scope_str} daemon-reload: {e}")


# ==============================================================================
# 8. RICH PRESENTATION & RENDERING
# ==============================================================================

def render_header(ctx: UserContext) -> None:
    """Renders the main Dusky Service Deployer header panel."""
    if not console:
        return
    header_text = Text()
    header_text.append("⚡ DUSKY SERVICE TOGGLER (290_dusky_service_toggler.py) ⚡\n", style="bold cyan")
    header_text.append("Context: Hyprland / UWSM | Kernel: ", style="dim white")
    header_text.append(f"{os.uname().release} | User: ", style="bold yellow")
    header_text.append(ctx.username, style="bold green")
    header_text.append(f" (Root: {ctx.is_root})", style="dim cyan")
    console.print(Panel(header_text, expand=False, border_style="cyan"))


def render_status_table(states: list[UnitState]) -> None:
    """Renders a summary table of all service statuses."""
    if not console:
        return
    table = Table(title="Dusky Deployed Systemd Services Overview", show_header=True, header_style="bold magenta", expand=True)

    table.add_column("Scope", style="dim", width=8)
    table.add_column("Category", width=22)
    table.add_column("Unit Name", style="bold")
    table.add_column("Installed", justify="center", width=12)
    table.add_column("Enabled State", justify="center", width=14)
    table.add_column("Active State", justify="center", width=12)
    table.add_column("Overall Status", width=22)

    for st in states:
        scope_badge = "[blue]SYSTEM[/blue]" if st.scope == Scope.SYSTEM else "[purple]USER[/purple]"
        installed_badge = "[green]YES[/green]" if st.exists else "[red]NO[/red]"
        enabled_badge = f"[green]{st.unit_file_state.upper()}[/green]" if st.unit_file_state in ("enabled", "static") else f"[yellow]{st.unit_file_state.upper()}[/yellow]"
        active_badge = f"[green]{st.active_state.upper()}[/green]" if st.active_state == "active" else f"[dim]{st.active_state.upper()}[/dim]"

        match st.status_enum:
            case UnitStatus.ENABLED_ACTIVE:
                status_fmt = "[bold green]✔ Enabled & Active[/bold green]"
            case UnitStatus.ENABLED_INACTIVE:
                status_fmt = "[cyan]● Enabled[/cyan]"
            case UnitStatus.DISABLED_ACTIVE:
                status_fmt = "[yellow]▲ Active (Disabled)[/yellow]"
            case UnitStatus.DISABLED:
                status_fmt = "[yellow]○ Disabled[/yellow]"
            case UnitStatus.STATIC:
                status_fmt = "[blue]🔒 Static[/blue]"
            case UnitStatus.MASKED:
                status_fmt = "[magenta]🚫 Masked[/magenta]"
            case UnitStatus.ACTIVATING:
                status_fmt = "[yellow]⏳ Activating[/yellow]"
            case UnitStatus.FAILED:
                status_fmt = "[bold red]💥 Failed[/bold red]"
            case UnitStatus.BAD:
                status_fmt = "[bold red]✖ Bad Unit[/bold red]"
            case UnitStatus.MISSING:
                status_fmt = "[dim red]✖ Not Installed[/dim red]"
            case UnitStatus.ERROR:
                status_fmt = "[bold red]✖ Error[/bold red]"

        table.add_row(
            scope_badge,
            st.category.value,
            st.unit_name,
            installed_badge,
            enabled_badge,
            active_badge,
            status_fmt,
        )

    console.print(table)


def export_json_status(states: list[UnitState]) -> None:
    """Exports unit status data as pure formatted JSON."""
    data = [
        {
            "unit": st.unit_name,
            "scope": st.scope.name,
            "category": st.category.value,
            "description": st.description,
            "installed": st.exists,
            "load_state": st.load_state,
            "unit_file_state": st.unit_file_state,
            "active_state": st.active_state,
            "status": st.status_enum.value,
        }
        for st in states
    ]
    print(json.dumps(data, indent=2))


def render_results(results: list[ProcessingResult]) -> None:
    """Displays action execution results categorized neatly."""
    if not console:
        return

    success_count = 0
    skip_count = 0
    missing_count = 0
    error_count = 0

    current_category: Category | None = None

    for res in results:
        if res.category != current_category:
            current_category = res.category
            console.print(f"\n[bold yellow]=== {current_category.value} ===[/bold yellow]")

        match res.status:
            case UnitStatus.ENABLED_ACTIVE | UnitStatus.ENABLED_INACTIVE:
                console.print(f" [bold green][OK][/bold green]    {res.unit_name:<30} -> {res.message}")
                success_count += 1
            case UnitStatus.DISABLED | UnitStatus.STATIC | UnitStatus.DISABLED_ACTIVE | UnitStatus.MASKED | UnitStatus.BAD:
                console.print(f" [bold blue][SKIP][/bold blue]  {res.unit_name:<30} -> {res.message}")
                skip_count += 1
            case UnitStatus.MISSING:
                console.print(f" [bold yellow][MISSING][/bold yellow] {res.unit_name:<30} -> {res.message}")
                missing_count += 1
            case UnitStatus.ERROR | UnitStatus.FAILED:
                console.print(f" [bold red][FAIL][/bold red]   {res.unit_name:<30} -> {res.message}")
                if res.output:
                    console.print(f"         └─ [red]{res.output}[/red]")
                error_count += 1

    summary_text = (
        f"[bold green]Success: {success_count}[/bold green] | "
        f"[bold blue]Unchanged/Skipped: {skip_count}[/bold blue] | "
        f"[yellow]Missing: {missing_count}[/yellow] | "
        f"[bold red]Errors: {error_count}[/bold red]"
    )
    console.print(Panel(summary_text, title="Execution Summary", border_style="bright_blue"))


# ==============================================================================
# 9. CLI ORCHESTRATION & MAIN ENTRYPOINT
# ==============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dusky Service Toggler - Arch Linux Systemd & AUR Service Toggler",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("-a", "--all", action="store_true", help="Process ALL service categories (default)")
    parser.add_argument("-s", "--system", action="store_true", help="Process Core System services")
    parser.add_argument("--aur-system", action="store_true", help="Process AUR System services")
    parser.add_argument("-u", "--user", action="store_true", help="Process Core User services")
    parser.add_argument("--aur-user", action="store_true", help="Process AUR User services")
    parser.add_argument("-c", "--status", action="store_true", help="Inspect and display status matrix without making changes")
    parser.add_argument("-i", "--interactive", action="store_true", help="Interactively prompt before processing each service")
    parser.add_argument("-y", "--default", action="store_true", help="Non-interactive mode: Auto-apply default configured actions")
    parser.add_argument("--disable", action="store_true", help="Disable (and stop) targeted services")
    parser.add_argument("--dry-run", action="store_true", help="Simulate actions without executing systemctl commands")
    parser.add_argument("--no-now", action="store_true", help="Enable/disable services without immediate start/stop")
    parser.add_argument("--daemon-reload", action="store_true", help="Issue daemon-reload after processing services")
    parser.add_argument("--dbus-reload", action="store_true", help="Issue DBus configuration reload via busctl")
    parser.add_argument("--json", action="store_true", help="Output status matrix as JSON (used with --status)")
    parser.add_argument("--child-fork", action="store_true", help=argparse.SUPPRESS)  # Internal flag to prevent UI duplication

    args = parser.parse_args()
    check_ui_deps(args.json)

    # Pre-execution sanity check
    if not shutil.which("systemctl"):
        if not args.json:
            if error_console:
                error_console.print("[bold red][ERROR][/bold red] Systemd (systemctl) not found. This script requires systemd.")
            else:
                print("ERROR: Systemd (systemctl) not found.", file=sys.stderr)
        sys.exit(1)

    ctx = resolve_user_context()

    if not args.json and not args.child_fork:
        render_header(ctx)

    # Determine targeted categories
    targeted_categories: set[Category] = set()

    if args.system:
        targeted_categories.add(Category.SYSTEM_CORE)
    if args.aur_system:
        targeted_categories.add(Category.SYSTEM_AUR)
    if args.user:
        targeted_categories.add(Category.USER_CORE)
    if args.aur_user:
        targeted_categories.add(Category.USER_AUR)

    if args.all or not targeted_categories:
        targeted_categories = {
            Category.SYSTEM_CORE,
            Category.SYSTEM_AUR,
            Category.USER_CORE,
            Category.USER_AUR,
        }

    # Subprocess Escalation Fork for System Services
    requires_system_scope = any(cat in (Category.SYSTEM_CORE, Category.SYSTEM_AUR) for cat in targeted_categories)
    child_json_data: list[dict] = []

    if requires_system_scope and not ctx.is_root and not args.status and not args.dry_run:
        escalator = get_escalator()
        if escalator:
            if not args.json and console:
                console.print(f"[bold blue][INFO][/bold blue] System services require root privileges. Forking via {escalator}...")

            script_path = Path(__file__).resolve().as_posix()
            child_args = [sys.executable, script_path, "--child-fork"]

            if Category.SYSTEM_CORE in targeted_categories:
                child_args.append("--system")
            if Category.SYSTEM_AUR in targeted_categories:
                child_args.append("--aur-system")
            if args.disable:
                child_args.append("--disable")
            if args.no_now:
                child_args.append("--no-now")
            if args.interactive:
                child_args.append("--interactive")
            if args.default:
                child_args.append("--default")
            if args.daemon_reload:
                child_args.append("--daemon-reload")
            if args.dbus_reload:
                child_args.append("--dbus-reload")
            if args.json:
                child_args.append("--json")

            sudo_cmd = [escalator] + child_args

            if args.json:
                res = subprocess.run(sudo_cmd, capture_output=True, text=True)
                if res.returncode != 0:
                    sys.exit(res.returncode)
                try:
                    child_json_data = json.loads(res.stdout.strip())
                except json.JSONDecodeError:
                    pass
            else:
                res = subprocess.run(sudo_cmd, check=False)
                if res.returncode != 0:
                    if error_console:
                        error_console.print("[bold red][ERROR][/bold red] Privilege escalation failed or aborted.")
                    else:
                        print("ERROR: Privilege escalation failed or aborted.", file=sys.stderr)
                    sys.exit(res.returncode)

            # Strip system categories from the parent process so they aren't executed twice
            targeted_categories.discard(Category.SYSTEM_CORE)
            targeted_categories.discard(Category.SYSTEM_AUR)

            if not targeted_categories:
                if args.json and child_json_data:
                    print(json.dumps(child_json_data, indent=2))

                if args.daemon_reload:
                    daemon_reload(Scope.USER, dry_run=args.dry_run, ctx=ctx)
                if args.dbus_reload:
                    reload_dbus(Scope.USER, dry_run=args.dry_run, ctx=ctx)
                return
        elif not args.json:
            if error_console:
                error_console.print("[bold red][ERROR][/bold red] Privilege escalation tool missing. System services skipped.")
            else:
                print("ERROR: Privilege escalation tool missing.", file=sys.stderr)

    # Target Mapping
    unit_targets: list[UnitTarget] = []

    if Category.SYSTEM_CORE in targeted_categories:
        unit_targets.extend([UnitTarget(cfg, Scope.SYSTEM, Category.SYSTEM_CORE) for cfg in SYSTEM_SERVICES if args.status or args.all or cfg.enabled_by_default or args.interactive])
    if Category.SYSTEM_AUR in targeted_categories:
        unit_targets.extend([UnitTarget(cfg, Scope.SYSTEM, Category.SYSTEM_AUR) for cfg in AUR_SYSTEM_SERVICES if args.status or args.all or cfg.enabled_by_default or args.interactive])
    if Category.USER_CORE in targeted_categories:
        unit_targets.extend([UnitTarget(cfg, Scope.USER, Category.USER_CORE) for cfg in USER_SERVICES if args.status or args.all or cfg.enabled_by_default or args.interactive])
    if Category.USER_AUR in targeted_categories:
        unit_targets.extend([UnitTarget(cfg, Scope.USER, Category.USER_AUR) for cfg in AUR_USER_SERVICES if args.status or args.all or cfg.enabled_by_default or args.interactive])

    if not args.json and not args.status and console:
        console.print("\n[bold cyan]Querying systemd unit states in bulk...[/bold cyan]")

    states_list = query_bulk_unit_states(unit_targets, ctx=ctx)

    # Status Inspection Mode
    if args.status:
        states_list.sort(key=lambda s: (s.category.value, s.unit_name))
        if args.json:
            export_json_status(states_list)
        else:
            render_status_table(states_list)
        return

    action_type: Literal["enable", "disable"] = "disable" if args.disable else "enable"

    # Process Interactive Prompts Sequentially
    approved_targets: list[UnitTarget] = []
    approved_states: list[UnitState] = []
    interactive = args.interactive and not args.default and not args.json

    for target, st in zip(unit_targets, states_list):
        if interactive and sys.stdin.isatty() and Confirm:
            if st.exists and st.status_enum not in (UnitStatus.MASKED, UnitStatus.BAD):
                prompt = f"Execute [bold cyan]{action_type}[/bold cyan] for [bold yellow]{normalize_unit_name(target.config.name)}[/bold yellow]?"
                if not Confirm.ask(prompt, default=target.config.enabled_by_default if action_type == "enable" else False):
                    continue
        approved_targets.append(target)
        approved_states.append(st)

    if not args.json and console:
        console.print(f"\n[bold cyan]Executing '{action_type}' concurrently for {len(approved_targets)} services...[/bold cyan]")
        if args.dry_run:
            console.print("[bold yellow]*** DRY-RUN MODE ACTIVE - No changes will be made ***[/bold yellow]\n")

    # Dispatch Execution Asynchronously via asyncio
    results = asyncio.run(execute_operations(approved_targets, approved_states, action_type, not args.no_now, args.dry_run, ctx))

    if args.json:
        parent_results = [{"unit": r.unit_name, "status": r.status.value, "message": r.message} for r in results]
        print(json.dumps(child_json_data + parent_results, indent=2))
    else:
        render_results(results)

    # Perform daemon-reload or dbus-reload
    if args.daemon_reload:
        if not args.json and console:
            console.print("\n[bold cyan]Triggering daemon-reload...[/bold cyan]")
        if any(t.scope == Scope.SYSTEM for t in unit_targets):
            daemon_reload(Scope.SYSTEM, dry_run=args.dry_run, ctx=ctx)
        if any(t.scope == Scope.USER for t in unit_targets):
            daemon_reload(Scope.USER, dry_run=args.dry_run, ctx=ctx)

    if args.dbus_reload:
        if not args.json and console:
            console.print("\n[bold cyan]Triggering dbus-reload...[/bold cyan]")
        if any(t.scope == Scope.SYSTEM for t in unit_targets):
            reload_dbus(Scope.SYSTEM, dry_run=args.dry_run, ctx=ctx)
        if any(t.scope == Scope.USER for t in unit_targets):
            reload_dbus(Scope.USER, dry_run=args.dry_run, ctx=ctx)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        if console:
            console.print("\n[bold red][ABORTED][/bold red] Interrupted by user (SIGINT).")
        else:
            print("\n[ABORTED] Interrupted by user (SIGINT).", file=sys.stderr)
        sys.exit(130)
